"""Two-factor authentication policy — who needs it, when, and how it is proved.

The system uses TOTP (RFC 6238), the scheme Google Authenticator implements.
That choice matters for a campus deployment: codes are generated on the phone
from a shared secret and the clock, so verification needs no network round-trip
to Google, no OAuth client, and no Google account. Authy and Microsoft
Authenticator read the same `otpauth://` URI, so nobody is forced onto one app.

Everything about *policy* lives here rather than in the views, because the same
questions ("does this account need 2FA?", "is this step-up still fresh?") are
asked from the login view, five sensitive endpoints, and the enrollment flow.
A rule duplicated across those is a rule that drifts.

Three short-lived signed tokens carry state between requests, none of them
stored in the database:

  challenge  — issued when a correct password is not enough (login is paused
               mid-flight). Spent by the verify call. 10 minutes.
  step-up    — proof that a code was entered recently. Sent by the client on
               sensitive writes so a code is not demanded on every save of a
               settings form. 10 minutes.
  device     — "remember this browser". Lets a returning user skip the code
               until it lapses, which is also what implements the weekly rule.
               7 days.

All three are `TimestampSigner` values bound to a fingerprint of the account's
password hash, so changing or resetting a password silently invalidates every
outstanding token — including a trusted-device token sitting in the browser of
whoever the password was changed to lock out.
"""

import base64
import hashlib
import secrets

from django.conf import settings
from django.core import signing
from django.utils import timezone

# ── Policy constants ────────────────────────────────────────────────────────

# Guards are deliberately absent. They sign in at a shared gate kiosk, often
# mid-shift with a queue of vehicles behind them, and a phone-based code at
# that terminal would either stall the gate or end up as a photo of a QR taped
# under the desk. Their compensating control is the QR badge plus the fact that
# a shift is tied to a physical gate.
TWO_FACTOR_ROLES = frozenset({'admin', 'vehicle_owner'})

# How long a browser stays trusted after a successful code. Re-issued on every
# verified login, so an account in regular use is never asked twice in a week;
# an account left alone for longer lapses and is challenged again. This is the
# "haven't logged in for a week" rule.
DEVICE_TRUST_DAYS = 7

# An account dormant this long is challenged even on a browser still inside its
# trust window — the device could have changed hands while nobody was watching.
DORMANCY_DAYS = 7

# "Sudo mode": how long one code authorises sensitive writes.
STEP_UP_MINUTES = 10

# Codes are accepted one timestep either side of now, so a phone clock drifting
# by up to 30s still works. Wider than this and a stolen code stays live longer.
TOTP_VALID_WINDOW = 1

# One recovery code, not a sheet of them. It is the single thing standing
# between a wiped phone and an account only the CDSO can reopen, so it is worth
# knowing the trade: using it leaves nothing in reserve. The mitigation is that
# Account Security can mint a replacement the moment they are back in, and the
# UI says so — a set of ten mostly ends up as ten codes nobody wrote down.
BACKUP_CODE_COUNT = 1

_CHALLENGE_SALT = 'accounts.twofa.challenge'
_STEP_UP_SALT = 'accounts.twofa.stepup'
_DEVICE_SALT = 'accounts.twofa.device'


class TwoFactorError(Exception):
    """Raised when a token or code is missing, malformed, expired or replayed."""


# ── Policy questions ────────────────────────────────────────────────────────

def requires_2fa(user) -> bool:
    """Whether this account is in scope for two-factor at all.

    Archived and inactive accounts are excluded so a disabled login fails on
    the credential check with its own message, rather than first walking the
    person through an enrollment they can never complete.
    """
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_archived', False) or not user.is_active:
        return False
    return user.role in TWO_FACTOR_ROLES


def is_dormant(user) -> bool:
    """True when the account has not completed a login in DORMANCY_DAYS.

    A never-used account counts as dormant, which is what makes a first login
    always pass through enrollment.
    """
    last = getattr(user, 'last_login', None)
    if last is None:
        return True
    return (timezone.now() - last).days >= DORMANCY_DAYS


# ── Password-hash fingerprint ───────────────────────────────────────────────

def _fingerprint(user) -> str:
    """Short digest of the stored password hash.

    Embedded in every token so a password change — or an admin-forced reset —
    invalidates trusted devices and pending challenges without needing a
    revocation table to sweep.
    """
    return hashlib.sha256(
        (user.password or '').encode('utf-8') + settings.SECRET_KEY.encode('utf-8')
    ).hexdigest()[:16]


def _sign(user, salt, **extra) -> str:
    payload = {'uid': user.pk, 'fp': _fingerprint(user)}
    payload.update(extra)
    return signing.dumps(payload, salt=salt)


def _unsign(token, salt, max_age_seconds) -> dict:
    if not token:
        raise TwoFactorError('Missing verification token.')
    try:
        return signing.loads(token, salt=salt, max_age=max_age_seconds)
    except signing.SignatureExpired:
        raise TwoFactorError('This verification has expired. Please start again.')
    except signing.BadSignature:
        raise TwoFactorError('Invalid verification token.')


def _load_for_user(token, salt, max_age_seconds, user):
    """Unsign and confirm the token was minted for `user` at their current password."""
    data = _unsign(token, salt, max_age_seconds)
    if data.get('uid') != user.pk or data.get('fp') != _fingerprint(user):
        raise TwoFactorError('Invalid verification token.')
    return data


# ── Challenge token (login paused, awaiting a code) ─────────────────────────

def issue_challenge(user, purpose) -> str:
    """`purpose` is 'setup' (must enroll first) or 'verify' (already enrolled)."""
    return _sign(user, _CHALLENGE_SALT, purpose=purpose)


def read_challenge(token):
    """Resolve a challenge token back to (user, purpose). Raises TwoFactorError."""
    from .models import User

    data = _unsign(token, _CHALLENGE_SALT, STEP_UP_MINUTES * 60)
    try:
        user = User.objects.get(pk=data.get('uid'), is_archived=False)
    except User.DoesNotExist:
        raise TwoFactorError('Invalid verification token.')
    if data.get('fp') != _fingerprint(user):
        # The password changed between the password check and the code entry.
        raise TwoFactorError('Your password changed. Please sign in again.')
    if not user.is_active:
        raise TwoFactorError(
            'Your account has been disabled. Please contact the administrator.'
        )
    return user, data.get('purpose')


def login_challenge(user, request=None):
    """Decide whether a correct password is enough for `user` this time.

    Returns the challenge payload the login endpoint should hand back instead
    of tokens, or None to let the login complete normally.

    Four ways a code gets demanded, which together cover the cases the system
    was asked to protect:

      * no confirmed authenticator yet  -> 'setup'  (this is the first login)
      * the password was just reset     -> 'verify' (the forgot-password rule)
      * the account has been dormant    -> 'verify' (the weekly rule)
      * this browser is not trusted     -> 'verify' (a new or cleared device)

    `must_verify_2fa` outranks device trust on purpose: a reset link proves
    somebody can read the mailbox, and the browser they are sitting at may well
    be one this account trusted months ago.

    The device-trust token is re-issued on every verified login, so an account
    in regular use lapses only after DEVICE_TRUST_DAYS of silence. That makes
    the trust window and the dormancy window the same rule seen from two sides,
    and both are checked because they fail differently: trust alone would let a
    long-dormant account back in from a browser that never cleared its storage,
    and dormancy alone would ask nothing of a brand-new device.
    """
    from .models import TwoFactorDevice

    if not requires_2fa(user):
        return None

    device = (TwoFactorDevice.objects
              .filter(user=user, confirmed_at__isnull=False)
              .first())

    if device is None:
        return {
            'twofa_required': True,
            'twofa_action': 'setup',
            'challenge': issue_challenge(user, 'setup'),
            'email': user.email,
            'full_name': user.full_name,
            'detail': 'Set up two-factor authentication to finish signing in.',
        }

    device_token = ''
    if request is not None:
        device_token = request.headers.get('X-Device-Token', '')

    if (getattr(user, 'must_verify_2fa', False)
            or is_dormant(user)
            or not device_is_trusted(user, device_token)):
        return {
            'twofa_required': True,
            'twofa_action': 'verify',
            'challenge': issue_challenge(user, 'verify'),
            'email': user.email,
            'full_name': user.full_name,
            'detail': 'Enter the 6-digit code from your authenticator app.',
        }

    return None


# ── Step-up token ("sudo mode") ─────────────────────────────────────────────

def issue_step_up(user) -> str:
    return _sign(user, _STEP_UP_SALT)


def check_step_up(user, token) -> None:
    """Raise TwoFactorError unless `token` is a live step-up grant for `user`."""
    _load_for_user(token, _STEP_UP_SALT, STEP_UP_MINUTES * 60, user)


# ── Trusted-device token ────────────────────────────────────────────────────

def issue_device_token(user) -> str:
    return _sign(user, _DEVICE_SALT)


def device_is_trusted(user, token) -> bool:
    """Non-raising: an absent or stale token simply means "challenge them"."""
    if not token:
        return False
    try:
        _load_for_user(token, _DEVICE_SALT, DEVICE_TRUST_DAYS * 24 * 3600, user)
        return True
    except TwoFactorError:
        return False


# ── TOTP ────────────────────────────────────────────────────────────────────

def new_secret() -> str:
    """A fresh base32 TOTP secret (160 bits, per the RFC 4226 recommendation)."""
    return base64.b32encode(secrets.token_bytes(20)).decode('ascii').rstrip('=')


def provisioning_uri(user, secret) -> str:
    """The `otpauth://` URI encoded into the enrollment QR code."""
    import pyotp

    issuer = getattr(settings, 'TWO_FACTOR_ISSUER', 'SLC Vehicle Management')
    return pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=issuer)


def qr_data_uri(uri) -> str:
    """Render `uri` as a PNG data: URI so the client needs no QR library."""
    import io

    import qrcode

    buf = io.BytesIO()
    qrcode.make(uri).save(buf, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode('ascii')


# What verify_code concluded. Kept apart from a plain bool because "wrong code"
# and "right code, already used" need different words in front of the user: the
# second one is what a person sees when they act twice inside the same 30-second
# window, and telling them it is incorrect sends them hunting for a problem that
# is not there.
CODE_OK = 'ok'
CODE_INVALID = 'invalid'
CODE_REPLAYED = 'replayed'


def verify_code(device, code):
    """Check a 6-digit TOTP against `device`, rejecting replays.

    Returns CODE_OK, CODE_INVALID or CODE_REPLAYED.

    "Close enough" is not good enough for a one-time password: without the
    counter check below, a code read over a shoulder stays usable for its whole
    30-second step plus the drift window. Recording the timestep that was spent
    makes each code single-use, which is the "one-time" half of the name.

    The caller persists the device; the burned counter is written to the
    instance here so it can be saved in the same transaction as whatever the
    code authorised.
    """
    import pyotp

    code = (code or '').strip().replace(' ', '')
    if not code.isdigit() or len(code) != 6:
        return CODE_INVALID

    totp = pyotp.TOTP(device.secret)
    now = int(timezone.now().timestamp())
    step = now // totp.interval

    matched = None
    for offset in range(-TOTP_VALID_WINDOW, TOTP_VALID_WINDOW + 1):
        candidate = step + offset
        if secrets.compare_digest(totp.at(candidate * totp.interval), code):
            matched = candidate
            break
    if matched is None:
        return CODE_INVALID
    if device.last_used_step and matched <= device.last_used_step:
        return CODE_REPLAYED

    device.last_used_step = matched
    return CODE_OK


# ── Backup codes ────────────────────────────────────────────────────────────

def generate_backup_codes(count=BACKUP_CODE_COUNT):
    """Plain-text recovery code(s). Shown once, stored only as hashes.

    Without these, a lost or wiped phone means an admin locked out of the
    system that holds the only admin account — the classic 2FA dead end.

    Still written as a list rather than a single value: the count is a policy
    number that may well move again, and every caller, serializer and test
    already speaks in terms of a set. Changing BACKUP_CODE_COUNT is the only
    edit needed to issue more.
    """
    return [
        '{:05d}-{:05d}'.format(secrets.randbelow(10 ** 5), secrets.randbelow(10 ** 5))
        for _ in range(count)
    ]


def hash_backup_code(code) -> str:
    """Hash for storage. Backup codes are high-entropy random strings, so a
    fast digest is adequate — there is no human-chosen secret to protect."""
    normalised = (code or '').strip().replace(' ', '').replace('-', '')
    return hashlib.sha256(
        normalised.encode('utf-8') + settings.SECRET_KEY.encode('utf-8')
    ).hexdigest()
