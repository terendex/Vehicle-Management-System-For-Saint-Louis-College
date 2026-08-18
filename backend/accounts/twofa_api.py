"""Two-factor endpoints: enrollment, login verification, and step-up.

The policy these views enforce lives in `accounts.twofa`; this module is only
the HTTP surface. It also owns `build_login_response`, the single place that
mints a JWT pair for a human login — the normal login view, the 2FA verify call
and the enrollment-completing confirm call all go through it, so `last_login`
(which the weekly dormancy rule reads) is written on exactly one code path.
"""

import logging

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from rest_framework import exceptions, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from . import twofa
from .audit import audit
from .models import AuditLog, TwoFactorBackupCode, TwoFactorDevice, User

logger = logging.getLogger(__name__)

# Brute-force ceiling. A TOTP code is six digits, so an attacker who already
# has the password needs roughly 10^6/2 guesses on average — trivial at machine
# speed and impossible at five per fifteen minutes. Keyed per account, not per
# IP, because the account is what is under attack and IPs are cheap.
MAX_CODE_ATTEMPTS = 5
ATTEMPT_WINDOW_SECONDS = 15 * 60


def _attempt_key(user) -> str:
    return f'twofa:attempts:{user.pk}'


def _too_many_attempts(user) -> bool:
    return (cache.get(_attempt_key(user)) or 0) >= MAX_CODE_ATTEMPTS


def _record_failure(user) -> None:
    key = _attempt_key(user)
    try:
        cache.add(key, 0, ATTEMPT_WINDOW_SECONDS)
        cache.incr(key)
    except ValueError:
        # The key expired between add() and incr() — the next attempt re-seeds it.
        cache.set(key, 1, ATTEMPT_WINDOW_SECONDS)


def _clear_failures(user) -> None:
    cache.delete(_attempt_key(user))


def _lockout_response():
    return Response(
        {'error': 'Too many incorrect codes. Please wait 15 minutes and try again.'},
        status=status.HTTP_429_TOO_MANY_REQUESTS,
    )


# ── Shared login-response builder ───────────────────────────────────────────

def build_user_payload(user, request=None):
    """The `user` object every login endpoint returns. One definition so the
    normal login, the 2FA verify and the guard endpoints cannot drift apart."""
    photo_url = (
        request.build_absolute_uri(user.photo.url)
        if request is not None and user.photo else None
    )
    return {
        'id': user.id,
        'user_code': user.user_code,
        'full_name': user.full_name,
        'email': user.email,
        'role': user.role,
        'must_change_password': user.must_change_password,
        'photo_url': photo_url,
        'gate_assignment': user.gate_assignment,
    }


def build_login_response(user, request=None, trust_device=True):
    """Mint the JWT pair, stamp `last_login`, and retire `must_verify_2fa`.

    Stamping matters beyond bookkeeping: `twofa.is_dormant` reads `last_login`
    to decide whether a returning user is challenged, and SimpleJWT does not
    write it by default. Before 2FA nothing read the column, so nothing noticed
    it was always NULL.

    This function is reached only after a code has actually been entered — the
    ordinary password login mints its own tokens through the serializer and
    never comes here. That is precisely why the post-reset flag is cleared at
    this point and nowhere else: clearing it on any path a password alone can
    reach would hand the protection straight back to whoever did the reset.
    """
    refresh = RefreshToken.for_user(user)
    refresh['role'] = user.role
    refresh['full_name'] = user.full_name
    refresh['email'] = user.email
    refresh['must_change_password'] = user.must_change_password

    User.objects.filter(pk=user.pk).update(
        last_login=timezone.now(), must_verify_2fa=False,
    )
    user.must_verify_2fa = False

    data = {
        'access': str(refresh.access_token),
        'refresh': str(refresh),
        'role': user.role,
        'user_code': user.user_code,
        'must_change_password': user.must_change_password,
        'user': build_user_payload(user, request),
    }
    if trust_device and twofa.requires_2fa(user):
        data['device_token'] = twofa.issue_device_token(user)
        data['device_trust_days'] = twofa.DEVICE_TRUST_DAYS
    return data


# ── Step-up permission, used by the sensitive endpoints ─────────────────────

class StepUpRequired(exceptions.PermissionDenied):
    """403 that tells the client to ask for a code and retry.

    The body carries `stepup_required` rather than DRF's bare `detail`, because
    the frontend has to tell "you may not do this at all" (show an error) apart
    from "you may, once you prove it's you" (open the code prompt). A plain
    PermissionDenied cannot express the difference.
    """

    def __init__(self, detail=None):
        super().__init__({
            'error': detail or 'This action needs verification with your authenticator app.',
            'stepup_required': True,
        })


def step_up_denied(detail=None):
    """Same payload, for views that check inline rather than via the permission."""
    return Response(StepUpRequired(detail).detail, status=status.HTTP_403_FORBIDDEN)


class HasRecentTwoFactor(permissions.BasePermission):
    """Requires a fresh `X-StepUp-Token` header for accounts that carry 2FA.

    Accounts outside `TWO_FACTOR_ROLES` — guards, above all — pass straight
    through, so the gate flow is untouched. An in-scope account that has not
    finished enrolling also passes: the login flow already forces enrollment
    before a session exists, and failing closed here too would strand a
    half-enrolled admin on the very screen that completes it.

    Safe methods are exempt unless the view sets `step_up_on_read = True`,
    which the backup download does — that GET hands over the whole database.
    """

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS and not getattr(
            view, 'step_up_on_read', False
        ):
            return True

        user = request.user
        if not (user and user.is_authenticated):
            return False
        if not twofa.requires_2fa(user):
            return True

        device = TwoFactorDevice.objects.filter(user=user, confirmed_at__isnull=False).first()
        if device is None:
            return True

        try:
            twofa.check_step_up(user, request.headers.get('X-StepUp-Token', ''))
        except twofa.TwoFactorError as exc:
            raise StepUpRequired(str(exc))
        return True


# ── Code checking shared by verify / confirm / step-up ──────────────────────

def _consume_backup_code(user, code):
    """Spend one unused backup code. Returns True when `code` matched."""
    digest = twofa.hash_backup_code(code)
    updated = TwoFactorBackupCode.objects.filter(
        user=user, code_hash=digest, used_at__isnull=True,
    ).update(used_at=timezone.now())
    return updated > 0


# What to say when a code does not go through. A replayed code is the one a
# person meets by accident — act twice inside the same 30-second window and the
# app is still showing the code you just spent — so it gets an instruction
# ("wait for the next one") rather than a flat contradiction of what they see.
WRONG_CODE_MESSAGE = 'That code is not correct. Check the app and try again.'
REPLAYED_CODE_MESSAGE = (
    'You have already used that code. Wait for your authenticator app to show '
    'the next one, then enter it.'
)


def _code_error(request):
    """The message matching why the last code check failed."""
    reason = getattr(request, 'twofa_code_reason', twofa.CODE_INVALID)
    return REPLAYED_CODE_MESSAGE if reason == twofa.CODE_REPLAYED else WRONG_CODE_MESSAGE


def _check_code(user, device, code, backup_code, request):
    """Validate a TOTP code or a backup code.

    Returns (ok, used_backup) where `ok` is True, False, or None when the
    account is locked out. On a failure the reason is left on
    `request.twofa_code_reason` so the view can pick the right wording without
    every caller having to re-derive it.

    Rate limiting, replay burning and audit logging all happen here so no
    caller can accidentally skip one of them.
    """
    request.twofa_code_reason = twofa.CODE_INVALID
    if _too_many_attempts(user):
        return None, None

    if backup_code:
        if _consume_backup_code(user, backup_code):
            _clear_failures(user)
            remaining = TwoFactorBackupCode.objects.filter(
                user=user, used_at__isnull=True,
            ).count()
            audit(
                request, AuditLog.Action.TWOFA_BACKUP_USED,
                f'Backup code used by {user.full_name} ({user.user_code}) — '
                f'{remaining} remaining',
                target_user=user,
            )
            return True, True
        _record_failure(user)
        return False, False

    if device is not None:
        result = twofa.verify_code(device, code)
        request.twofa_code_reason = result
        if result == twofa.CODE_OK:
            device.last_verified_at = timezone.now()
            device.save(update_fields=['last_used_step', 'last_verified_at'])
            _clear_failures(user)
            return True, False

    # A replay is not counted towards the lockout. It is overwhelmingly the
    # honest case — the same code entered twice in one 30-second window — and
    # spending an attempt on it would let a user lock themselves out by being
    # quick. It buys an attacker nothing either: the code is already dead.
    if request.twofa_code_reason != twofa.CODE_REPLAYED:
        _record_failure(user)
        audit(
            request, AuditLog.Action.TWOFA_FAILED,
            f'Incorrect verification code for {user.full_name} ({user.user_code})',
            target_user=user,
        )
    return False, False


def _issue_backup_codes(user):
    """Replace this user's backup codes. Returns the plain-text list, which is
    the only moment it exists — only hashes are stored."""
    TwoFactorBackupCode.objects.filter(user=user).delete()
    codes = twofa.generate_backup_codes()
    TwoFactorBackupCode.objects.bulk_create([
        TwoFactorBackupCode(user=user, code_hash=twofa.hash_backup_code(c))
        for c in codes
    ])
    return codes


def _replenish_backup_codes(user):
    """Issue a fresh set if spending a code left the account with none.

    Deliberately "top up when empty" rather than "replace on every use": with
    BACKUP_CODE_COUNT at 1 that fires each time, which is the point — a single
    code that is spent leaves nothing behind. Were the count raised to ten,
    replacing on every use would destroy the nine unused ones, so the emptiness
    check is what keeps this correct at any count.

    Returns the new plain-text codes, or None when the account still has some.
    The caller must SHOW whatever comes back: a code issued and never displayed
    is the exact failure this whole path exists to avoid.
    """
    if TwoFactorBackupCode.objects.filter(user=user, used_at__isnull=True).exists():
        return None
    return _issue_backup_codes(user)


# ── Enrollment ──────────────────────────────────────────────────────────────

class TwoFactorSetupView(APIView):
    """Begin (or restart) enrollment and hand back the QR code to scan.

    Reachable two ways: with a `challenge` token from a paused login (the
    first-login case, where no session exists yet), or by an already signed-in
    user re-enrolling a new phone.
    """

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        user, via_challenge = _resolve_actor(request)
        if user is None:
            return Response({'error': 'Authentication required.'},
                            status=status.HTTP_401_UNAUTHORIZED)

        if not twofa.requires_2fa(user):
            return Response(
                {'error': 'Two-factor authentication does not apply to this account.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        device = TwoFactorDevice.objects.filter(user=user).first()

        # Re-enrolling over a *confirmed* device is a security-relevant change,
        # so a signed-in user must prove the old device still works first. A
        # challenge-token holder has only just proved their password and has no
        # confirmed device by definition, so the branch cannot bite them.
        if device is not None and device.is_confirmed and not via_challenge:
            try:
                twofa.check_step_up(user, request.headers.get('X-StepUp-Token', ''))
            except twofa.TwoFactorError:
                return step_up_denied(
                    'Enter a code from your current authenticator before pairing a new device.'
                )

        secret = twofa.new_secret()
        if device is None:
            device = TwoFactorDevice(user=user)
        device.secret = secret
        device.confirmed_at = None
        device.last_used_step = 0
        device.save()

        uri = twofa.provisioning_uri(user, secret)
        return Response({
            'secret': secret,
            'otpauth_uri': uri,
            'qr_code': twofa.qr_data_uri(uri),
            'issuer': 'SLC Vehicle Management',
            'account': user.email,
        })


class TwoFactorConfirmView(APIView):
    """Finish enrollment: prove the app produces the right code.

    Returns the backup codes, and — when this completes a paused first login —
    the session tokens too, so the user lands on their dashboard instead of
    being bounced back to the login form.
    """

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        user, via_challenge = _resolve_actor(request)
        if user is None:
            return Response({'error': 'Authentication required.'},
                            status=status.HTTP_401_UNAUTHORIZED)

        device = TwoFactorDevice.objects.filter(user=user).first()
        if device is None:
            return Response(
                {'error': 'Start the setup again — no pending authenticator was found.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        code = (request.data.get('code') or '').strip()
        ok, _ = _check_code(user, device, code, None, request)
        if ok is None:
            return _lockout_response()
        if not ok:
            return Response({'error': _code_error(request)},
                            status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            device.confirmed_at = timezone.now()
            device.save(update_fields=['confirmed_at'])
            codes = _issue_backup_codes(user)

        audit(
            request, AuditLog.Action.TWOFA_ENABLED,
            f'Two-factor enabled for {user.full_name} ({user.user_code})',
            target_user=user,
        )

        payload = {
            'enabled': True,
            'backup_codes': codes,
            'message': 'Two-factor authentication is on. Save these backup codes now.',
            # A code was entered a second ago, so enrollment carries its own
            # step-up. This is what stops a brand-new account — which lands on a
            # forced password change the moment it signs in — from being asked
            # for a second code before it has finished the first screen.
            'step_up_token': twofa.issue_step_up(user),
            'step_up_expires_in': twofa.STEP_UP_MINUTES * 60,
        }
        if via_challenge:
            payload.update(build_login_response(user, request))
        return Response(payload)


# ── Login verification ──────────────────────────────────────────────────────

class TwoFactorVerifyView(APIView):
    """Second half of a paused login: challenge token + code, out come tokens."""

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        try:
            user, purpose = twofa.read_challenge(request.data.get('challenge') or '')
        except twofa.TwoFactorError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        if purpose != 'verify':
            return Response({'error': 'Finish setting up your authenticator first.'},
                            status=status.HTTP_400_BAD_REQUEST)

        device = TwoFactorDevice.objects.filter(user=user, confirmed_at__isnull=False).first()
        if device is None:
            return Response({'error': 'Finish setting up your authenticator first.'},
                            status=status.HTTP_400_BAD_REQUEST)

        code = (request.data.get('code') or '').strip()
        backup = (request.data.get('backup_code') or '').strip()
        ok, used_backup = _check_code(user, device, code, backup, request)
        if ok is None:
            return _lockout_response()
        if not ok:
            return Response(
                {'error': _code_error(request)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = build_login_response(user, request)

        # A code was just entered, so the next few minutes of sensitive work
        # need no second one. This is what makes re-pairing reachable after a
        # lost phone: the QR is step-up protected, and without this the only way
        # to it is a backup code — the very thing someone in that position is
        # short of. They would spend one to get in and another to open the QR.
        #
        # Note this is granted only on THIS endpoint, where a code was actually
        # typed. A trusted-device login enters nothing and gets nothing, so a
        # sensitive action there is still challenged — which is the common case
        # and the one the rule was written for.
        data['step_up_token'] = twofa.issue_step_up(user)
        data['step_up_expires_in'] = twofa.STEP_UP_MINUTES * 60

        if used_backup:
            data['used_backup_code'] = True
            # Signing in with the last backup code hands back a replacement in
            # the same response, so nobody is left with no way in but the CDSO.
            # The client shows it before letting them onto their dashboard.
            replacement = _replenish_backup_codes(user)
            if replacement:
                data['backup_codes'] = replacement
                data['backup_codes_replaced'] = True
            data['backup_codes_remaining'] = TwoFactorBackupCode.objects.filter(
                user=user, used_at__isnull=True,
            ).count()
        return Response(data)


# ── Step-up ("sudo mode") ───────────────────────────────────────────────────

class TwoFactorStepUpView(APIView):
    """Exchange a current code for a short-lived grant to do sensitive things.

    Without this, every save on the System Settings screen would demand its own
    code. With it, one code covers ten minutes of admin work — the same shape
    as the "sudo mode" GitHub uses for repository settings.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        user = request.user
        if not twofa.requires_2fa(user):
            return Response(
                {'error': 'Two-factor authentication does not apply to this account.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        device = TwoFactorDevice.objects.filter(user=user, confirmed_at__isnull=False).first()
        if device is None:
            return Response(
                {'error': 'Set up your authenticator app first.', 'setup_required': True},
                status=status.HTTP_400_BAD_REQUEST,
            )

        code = (request.data.get('code') or '').strip()
        backup = (request.data.get('backup_code') or '').strip()
        ok, used_backup = _check_code(user, device, code, backup, request)
        if ok is None:
            return _lockout_response()
        if not ok:
            return Response({'error': _code_error(request)},
                            status=status.HTTP_400_BAD_REQUEST)

        payload = {
            'step_up_token': twofa.issue_step_up(user),
            'expires_in': twofa.STEP_UP_MINUTES * 60,
            'used_backup_code': used_backup,
        }
        # Same rule as the login path. A backup code spent here would otherwise
        # leave the account empty just as surely, only without the login screen
        # to notice it.
        if used_backup:
            replacement = _replenish_backup_codes(user)
            if replacement:
                payload['backup_codes'] = replacement
                payload['backup_codes_replaced'] = True
        return Response(payload)


# ── Status / management ─────────────────────────────────────────────────────

class TwoFactorStatusView(APIView):
    """What the signed-in user's 2FA looks like — drives the security screen."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        device = TwoFactorDevice.objects.filter(user=user).first()
        return Response({
            # Named so a downloaded backup-code file says whose account it is —
            # people end up with files from more than one system.
            'email': user.email,
            'applicable': twofa.requires_2fa(user),
            'enrolled': device is not None,
            'confirmed': bool(device and device.is_confirmed),
            'confirmed_at': device.confirmed_at if device else None,
            'last_verified_at': device.last_verified_at if device else None,
            'backup_codes_remaining': TwoFactorBackupCode.objects.filter(
                user=user, used_at__isnull=True,
            ).count(),
            # How many a fresh set contains. Sent so the screen can word itself
            # ("code" vs "codes", and whether "running low" even means anything)
            # instead of hardcoding a number the backend owns.
            'backup_code_total': twofa.BACKUP_CODE_COUNT,
            'step_up_minutes': twofa.STEP_UP_MINUTES,
            'device_trust_days': twofa.DEVICE_TRUST_DAYS,
        })


class TwoFactorBackupCodesView(APIView):
    """Reissue backup codes. Needs a fresh step-up — the codes are login
    credentials, and handing out a new set is as sensitive as changing one."""

    permission_classes = [permissions.IsAuthenticated, HasRecentTwoFactor]

    def post(self, request):
        user = request.user
        if not TwoFactorDevice.objects.filter(user=user, confirmed_at__isnull=False).exists():
            return Response({'error': 'Set up your authenticator app first.'},
                            status=status.HTTP_400_BAD_REQUEST)
        codes = _issue_backup_codes(user)
        audit(
            request, AuditLog.Action.TWOFA_ENABLED,
            f'Backup codes regenerated for {user.full_name} ({user.user_code})',
            target_user=user,
        )
        return Response({'backup_codes': codes})


class TwoFactorResetView(APIView):
    """Admin clears another account's authenticator — the lost-phone path.

    Deliberately not a self-service "turn 2FA off": the account is put back to
    unenrolled, so the next login walks the person through pairing a new device
    rather than dropping them to password-only. The admin doing it must pass
    their own step-up, so a hijacked admin session cannot quietly strip 2FA
    from every account it can see.
    """

    permission_classes = [permissions.IsAuthenticated, HasRecentTwoFactor]

    def post(self, request, pk):
        if request.user.role != 'admin':
            return Response({'error': 'Only the CDSO may reset two-factor authentication.'},
                            status=status.HTTP_403_FORBIDDEN)

        try:
            target = User.objects.get(pk=pk, is_archived=False)
        except User.DoesNotExist:
            return Response({'error': 'User not found.'}, status=status.HTTP_404_NOT_FOUND)

        TwoFactorDevice.objects.filter(user=target).delete()
        TwoFactorBackupCode.objects.filter(user=target).delete()
        _clear_failures(target)

        audit(
            request, AuditLog.Action.TWOFA_RESET,
            f'Two-factor reset for {target.full_name} ({target.user_code}) '
            f'by {request.user.full_name}',
            target_user=target,
        )
        return Response({
            'reset': True,
            'message': f'{target.full_name} will set up a new authenticator at their next login.',
        })


# ── Helpers ─────────────────────────────────────────────────────────────────

def _resolve_actor(request):
    """Identify the account behind an enrollment call.

    Returns (user, via_challenge). A challenge token wins over a session: it is
    the first-login path, where the bearer token in the header may belong to
    nobody yet.
    """
    challenge = request.data.get('challenge') or ''
    if challenge:
        try:
            user, _purpose = twofa.read_challenge(challenge)
            return user, True
        except twofa.TwoFactorError:
            return None, False

    user = getattr(request, 'user', None)
    if user is not None and user.is_authenticated:
        return user, False
    return None, False
