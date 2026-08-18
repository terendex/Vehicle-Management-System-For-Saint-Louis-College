"""Tests for two-factor authentication (TOTP / Google Authenticator).

Covers the four things that have to hold for the feature to be worth having:

  * the right accounts are challenged, and guards never are,
  * each of the five sensitive endpoints refuses to act without a fresh code,
  * a code cannot be replayed, reused, or brute-forced,
  * nobody can lock themselves out permanently (backup codes, admin reset).
"""

import time

import pyotp
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts import twofa
from accounts.models import AuditLog, TwoFactorBackupCode, TwoFactorDevice, User

PASSWORD = 'Passw0rd!23'


def make_confirmed_device(user):
    """Enroll `user` with a confirmed authenticator and return (device, totp)."""
    device = TwoFactorDevice.objects.create(
        user=user, secret=twofa.new_secret(), confirmed_at=timezone.now(),
    )
    return device, pyotp.TOTP(device.secret)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class TwoFactorTestCase(TestCase):
    """Shared fixtures. Every subclass gets an admin, an owner and a guard."""

    def setUp(self):
        cache.clear()          # the attempt counter is cache-backed
        self.client = APIClient()
        self.admin = User.objects.create_user(
            email='cdso@slc.edu.ph', full_name='CDSO ADMIN',
            password=PASSWORD, role='admin',
        )
        self.owner = User.objects.create_user(
            email='owner@slc.edu.ph', full_name='VEHICLE OWNER',
            password=PASSWORD, role='vehicle_owner',
        )
        self.guard = User.objects.create_user(
            email='guard@slc.edu.ph', full_name='GATE GUARD',
            password=PASSWORD, role='security', gate_assignment='gate1',
        )

    # ── helpers ──────────────────────────────────────────────────────────────

    def login(self, user, device_token=None):
        """POST the credentials. Returns the DRF response untouched."""
        headers = {'HTTP_X_DEVICE_TOKEN': device_token} if device_token else {}
        return self.client.post(
            '/api/auth/login/',
            {'email': user.email, 'password': PASSWORD},
            format='json', **headers,
        )

    def authed_client(self, user):
        """A client holding a live session, bypassing the 2FA login dance."""
        from accounts.twofa_api import build_login_response
        client = APIClient()
        data = build_login_response(user)
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + data['access'])
        return client

    def step_up(self, user, totp):
        """Exchange a code for a step-up token via the real endpoint."""
        client = self.authed_client(user)
        res = client.post('/api/accounts/2fa/step-up/',
                          {'code': totp.now()}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        return client, res.data['step_up_token']


# ── Who gets challenged ──────────────────────────────────────────────────────

class PolicyScopeTests(TwoFactorTestCase):

    def test_guards_are_never_in_scope(self):
        self.assertFalse(twofa.requires_2fa(self.guard))

    def test_admin_and_owner_are_in_scope(self):
        self.assertTrue(twofa.requires_2fa(self.admin))
        self.assertTrue(twofa.requires_2fa(self.owner))

    def test_disabled_account_is_out_of_scope(self):
        self.admin.is_active = False
        self.assertFalse(twofa.requires_2fa(self.admin))

    def test_guard_gate_login_issues_tokens_without_any_code(self):
        from scanning.models import Gate
        Gate.objects.get_or_create(
            gate_id='gate1', defaults={'name': 'Gate 1', 'is_active': True},
        )
        res = self.client.post(
            '/api/auth/guard-login/',
            {'email': self.guard.email, 'password': PASSWORD, 'gate': 'gate1'},
            format='json',
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIn('access', res.data)
        self.assertNotIn('twofa_required', res.data)


# ── Login flow ───────────────────────────────────────────────────────────────

class LoginChallengeTests(TwoFactorTestCase):

    def test_first_login_demands_setup_and_issues_no_tokens(self):
        res = self.login(self.admin)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data['twofa_required'])
        self.assertEqual(res.data['twofa_action'], 'setup')
        self.assertIn('challenge', res.data)
        self.assertNotIn('access', res.data)
        self.assertNotIn('refresh', res.data)

    def test_owner_first_login_also_demands_setup(self):
        res = self.login(self.owner)
        self.assertEqual(res.data['twofa_action'], 'setup')

    def test_enrolled_user_on_new_device_demands_a_code(self):
        make_confirmed_device(self.admin)
        User.objects.filter(pk=self.admin.pk).update(last_login=timezone.now())
        res = self.login(self.admin)
        self.assertEqual(res.data['twofa_action'], 'verify')
        self.assertNotIn('access', res.data)

    def test_trusted_device_skips_the_code(self):
        make_confirmed_device(self.admin)
        User.objects.filter(pk=self.admin.pk).update(last_login=timezone.now())
        self.admin.refresh_from_db()
        token = twofa.issue_device_token(self.admin)

        res = self.login(self.admin, device_token=token)
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIn('access', res.data)
        self.assertNotIn('twofa_required', res.data)

    def test_dormant_account_is_challenged_even_on_a_trusted_device(self):
        """The 'haven't logged in for a week' rule."""
        make_confirmed_device(self.admin)
        stale = timezone.now() - timezone.timedelta(days=twofa.DORMANCY_DAYS + 1)
        User.objects.filter(pk=self.admin.pk).update(last_login=stale)
        self.admin.refresh_from_db()
        token = twofa.issue_device_token(self.admin)

        res = self.login(self.admin, device_token=token)
        self.assertEqual(res.data['twofa_action'], 'verify')
        self.assertNotIn('access', res.data)

    def test_six_days_dormant_is_still_trusted(self):
        """The boundary the other way — the rule must not fire early."""
        make_confirmed_device(self.admin)
        recent = timezone.now() - timezone.timedelta(days=twofa.DORMANCY_DAYS - 1)
        User.objects.filter(pk=self.admin.pk).update(last_login=recent)
        self.admin.refresh_from_db()
        token = twofa.issue_device_token(self.admin)

        res = self.login(self.admin, device_token=token)
        self.assertIn('access', res.data)

    def test_wrong_password_still_fails_before_any_challenge(self):
        res = self.client.post(
            '/api/auth/login/',
            {'email': self.admin.email, 'password': 'wrong-password'},
            format='json',
        )
        self.assertEqual(res.status_code, 401)
        self.assertNotIn('challenge', res.data)

    def test_password_change_invalidates_a_trusted_device(self):
        make_confirmed_device(self.admin)
        User.objects.filter(pk=self.admin.pk).update(last_login=timezone.now())
        self.admin.refresh_from_db()
        token = twofa.issue_device_token(self.admin)

        self.admin.set_password('Different!45')
        self.admin.save(update_fields=['password'])
        self.admin.refresh_from_db()

        self.assertFalse(twofa.device_is_trusted(self.admin, token))


# ── Forgot-password reset ────────────────────────────────────────────────────

class PasswordResetChallengeTests(TwoFactorTestCase):
    """A reset proves control of the mailbox, not of the account.

    The next login must ask for a code even on a browser this account trusted
    yesterday — that browser is exactly where an attacker who read the reset
    email would be sitting.
    """

    def reset_password(self, user, new_password='Reset!Pass9'):
        """Drive the real forgot-password confirm endpoint end to end."""
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        res = self.client.post('/api/accounts/password-reset/confirm/', {
            'uid': urlsafe_base64_encode(force_bytes(user.pk)),
            'token': default_token_generator.make_token(user),
            'new_password': new_password,
            'confirm_password': new_password,
        }, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        user.refresh_from_db()
        return res

    def login_with(self, user, password, device_token=None):
        headers = {'HTTP_X_DEVICE_TOKEN': device_token} if device_token else {}
        return self.client.post(
            '/api/auth/login/',
            {'email': user.email, 'password': password},
            format='json', **headers,
        )

    def test_reset_flags_the_account_for_verification(self):
        make_confirmed_device(self.admin)
        res = self.reset_password(self.admin)
        self.assertTrue(res.data['twofa_required_next_login'])
        self.assertTrue(self.admin.must_verify_2fa)

    def test_login_after_reset_demands_a_code_on_a_trusted_device(self):
        """The rule that matters: device trust must not get them past this."""
        make_confirmed_device(self.admin)
        User.objects.filter(pk=self.admin.pk).update(last_login=timezone.now())
        self.admin.refresh_from_db()
        trusted = twofa.issue_device_token(self.admin)

        self.reset_password(self.admin)

        res = self.login_with(self.admin, 'Reset!Pass9', device_token=trusted)
        self.assertEqual(res.data.get('twofa_action'), 'verify')
        self.assertNotIn('access', res.data)

    def test_flag_survives_a_password_login_and_is_cleared_only_by_a_code(self):
        """The protection must not be spendable by the password alone —
        otherwise whoever performed the reset simply logs in twice."""
        device, totp = make_confirmed_device(self.admin)
        self.reset_password(self.admin)

        # First attempt: challenged, and the flag must still be standing.
        self.login_with(self.admin, 'Reset!Pass9')
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.must_verify_2fa)

        # A second password login is challenged just the same.
        challenge = self.login_with(self.admin, 'Reset!Pass9').data['challenge']
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.must_verify_2fa)

        res = self.client.post('/api/accounts/2fa/verify/',
                               {'challenge': challenge, 'code': totp.now()}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.admin.refresh_from_db()
        self.assertFalse(self.admin.must_verify_2fa)

    def test_next_login_after_verifying_is_quiet_again(self):
        """The flag is a one-shot, not a permanent penalty on the account."""
        device, totp = make_confirmed_device(self.admin)
        self.reset_password(self.admin)

        challenge = self.login_with(self.admin, 'Reset!Pass9').data['challenge']
        verified = self.client.post('/api/accounts/2fa/verify/',
                                    {'challenge': challenge, 'code': totp.now()}, format='json')
        fresh_device = verified.data['device_token']

        res = self.login_with(self.admin, 'Reset!Pass9', device_token=fresh_device)
        self.assertIn('access', res.data)
        self.assertNotIn('twofa_required', res.data)

    def test_unenrolled_user_is_sent_to_setup_after_a_reset(self):
        self.reset_password(self.owner)
        res = self.login_with(self.owner, 'Reset!Pass9')
        self.assertEqual(res.data['twofa_action'], 'setup')

    def test_guard_reset_does_not_raise_a_flag_nobody_reads(self):
        """Guards carry no second factor, so the flag would never be cleared."""
        res = self.reset_password(self.guard)
        self.assertFalse(res.data['twofa_required_next_login'])
        self.assertFalse(self.guard.must_verify_2fa)


# ── Enrollment ───────────────────────────────────────────────────────────────

class EnrollmentTests(TwoFactorTestCase):

    def test_setup_then_confirm_completes_the_paused_login(self):
        challenge = self.login(self.admin).data['challenge']

        setup = self.client.post('/api/accounts/2fa/setup/',
                                 {'challenge': challenge}, format='json')
        self.assertEqual(setup.status_code, 200, setup.data)
        self.assertTrue(setup.data['qr_code'].startswith('data:image/png;base64,'))
        self.assertIn('otpauth://totp/', setup.data['otpauth_uri'])

        totp = pyotp.TOTP(setup.data['secret'])
        confirm = self.client.post(
            '/api/accounts/2fa/confirm/',
            {'challenge': challenge, 'code': totp.now()}, format='json',
        )
        self.assertEqual(confirm.status_code, 200, confirm.data)
        # Tokens come back, so the user lands on their dashboard.
        self.assertIn('access', confirm.data)
        self.assertIn('device_token', confirm.data)
        self.assertEqual(len(confirm.data['backup_codes']), twofa.BACKUP_CODE_COUNT)

        device = TwoFactorDevice.objects.get(user=self.admin)
        self.assertTrue(device.is_confirmed)
        self.assertTrue(
            AuditLog.objects.filter(action=AuditLog.Action.TWOFA_ENABLED).exists()
        )

    def test_enrollment_grants_a_step_up_for_the_forced_password_change(self):
        """A new account lands on a forced password change the instant it signs
        in, and that screen is itself step-up protected. Enrollment must carry
        its own grant or the user is asked for two codes back to back."""
        self.admin.must_change_password = True
        self.admin.save(update_fields=['must_change_password'])

        challenge = self.login(self.admin).data['challenge']
        setup = self.client.post('/api/accounts/2fa/setup/',
                                 {'challenge': challenge}, format='json')
        totp = pyotp.TOTP(setup.data['secret'])
        confirm = self.client.post('/api/accounts/2fa/confirm/',
                                   {'challenge': challenge, 'code': totp.now()}, format='json')

        self.assertIn('step_up_token', confirm.data)

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + confirm.data['access'])
        res = client.post(
            '/api/accounts/change-password/',
            {'current_password': PASSWORD, 'new_password': 'Fresh!Start9',
             'confirm_password': 'Fresh!Start9'},
            format='json', HTTP_X_STEPUP_TOKEN=confirm.data['step_up_token'],
        )
        self.assertEqual(res.status_code, 200, res.data)

    def test_confirm_with_a_wrong_code_does_not_enroll(self):
        challenge = self.login(self.admin).data['challenge']
        self.client.post('/api/accounts/2fa/setup/', {'challenge': challenge}, format='json')

        res = self.client.post('/api/accounts/2fa/confirm/',
                               {'challenge': challenge, 'code': '000000'}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIsNone(TwoFactorDevice.objects.get(user=self.admin).confirmed_at)

    def test_setup_rejects_a_forged_challenge(self):
        res = self.client.post('/api/accounts/2fa/setup/',
                               {'challenge': 'not-a-real-token'}, format='json')
        self.assertEqual(res.status_code, 401)

    def test_re_enrolling_a_confirmed_device_needs_a_step_up(self):
        _, totp = make_confirmed_device(self.admin)
        client = self.authed_client(self.admin)

        denied = client.post('/api/accounts/2fa/setup/', {}, format='json')
        self.assertEqual(denied.status_code, 403)
        self.assertTrue(denied.data['stepup_required'])

        client, token = self.step_up(self.admin, totp)
        client.credentials(
            HTTP_AUTHORIZATION=client._credentials['HTTP_AUTHORIZATION'],
            HTTP_X_STEPUP_TOKEN=token,
        )
        allowed = client.post('/api/accounts/2fa/setup/', {}, format='json')
        self.assertEqual(allowed.status_code, 200, allowed.data)


# ── Code verification ────────────────────────────────────────────────────────

class VerificationTests(TwoFactorTestCase):

    def test_correct_code_completes_the_login(self):
        _, totp = make_confirmed_device(self.admin)
        challenge = self.login(self.admin).data['challenge']

        res = self.client.post('/api/accounts/2fa/verify/',
                               {'challenge': challenge, 'code': totp.now()}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIn('access', res.data)
        self.assertIn('device_token', res.data)

    def test_a_code_cannot_be_replayed(self):
        """The 'one-time' half of one-time password."""
        device, totp = make_confirmed_device(self.admin)
        code = totp.now()

        self.assertEqual(twofa.verify_code(device, code), twofa.CODE_OK)
        device.save(update_fields=['last_used_step'])
        # Distinguished from a wrong code: the user is told to wait for the next
        # one rather than that the code they can plainly see is incorrect.
        self.assertEqual(twofa.verify_code(device, code), twofa.CODE_REPLAYED)
        self.assertEqual(twofa.verify_code(device, '000000'), twofa.CODE_INVALID)

    def test_replaying_a_code_does_not_burn_a_login_attempt(self):
        """Being quick must not lock you out — the honest replay is the common
        one, and the code is already dead so an attacker gains nothing."""
        _, totp = make_confirmed_device(self.admin)
        code = totp.now()

        challenge = self.login(self.admin).data['challenge']
        first = self.client.post('/api/accounts/2fa/verify/',
                                 {'challenge': challenge, 'code': code}, format='json')
        self.assertEqual(first.status_code, 200, first.data)

        from accounts.twofa_api import MAX_CODE_ATTEMPTS, _attempt_key
        for _ in range(MAX_CODE_ATTEMPTS + 2):
            challenge = self.login(self.admin).data['challenge']
            replay = self.client.post('/api/accounts/2fa/verify/',
                                      {'challenge': challenge, 'code': code}, format='json')
            self.assertEqual(replay.status_code, 400)
            self.assertIn('already used', replay.data['error'])

        self.assertIsNone(cache.get(_attempt_key(self.admin)))

    def test_wrong_code_is_rejected_and_audited(self):
        make_confirmed_device(self.admin)
        challenge = self.login(self.admin).data['challenge']

        res = self.client.post('/api/accounts/2fa/verify/',
                               {'challenge': challenge, 'code': '000000'}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertTrue(
            AuditLog.objects.filter(action=AuditLog.Action.TWOFA_FAILED).exists()
        )

    def test_brute_force_is_locked_out(self):
        make_confirmed_device(self.admin)
        challenge = self.login(self.admin).data['challenge']

        from accounts.twofa_api import MAX_CODE_ATTEMPTS
        for _ in range(MAX_CODE_ATTEMPTS):
            self.client.post('/api/accounts/2fa/verify/',
                             {'challenge': challenge, 'code': '000000'}, format='json')

        res = self.client.post('/api/accounts/2fa/verify/',
                               {'challenge': challenge, 'code': '000000'}, format='json')
        self.assertEqual(res.status_code, 429)

    def test_expired_challenge_is_refused(self):
        make_confirmed_device(self.admin)
        challenge = twofa.issue_challenge(self.admin, 'verify')
        with override_settings():
            # Rewind past the challenge lifetime by asking for a zero max_age.
            original = twofa.STEP_UP_MINUTES
            twofa.STEP_UP_MINUTES = 0
            try:
                time.sleep(1)
                res = self.client.post(
                    '/api/accounts/2fa/verify/',
                    {'challenge': challenge, 'code': '000000'}, format='json',
                )
            finally:
                twofa.STEP_UP_MINUTES = original
        self.assertEqual(res.status_code, 400)
        self.assertIn('expired', res.data['error'].lower())


# ── Backup codes ─────────────────────────────────────────────────────────────

class BackupCodeTests(TwoFactorTestCase):

    def enroll_with_backup_codes(self, user):
        _, totp = make_confirmed_device(user)
        from accounts.twofa_api import _issue_backup_codes
        return totp, _issue_backup_codes(user)

    def test_backup_code_logs_in_and_is_single_use(self):
        totp, codes = self.enroll_with_backup_codes(self.admin)

        challenge = self.login(self.admin).data['challenge']
        res = self.client.post('/api/accounts/2fa/verify/',
                               {'challenge': challenge, 'backup_code': codes[0]}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(res.data['used_backup_code'])
        # Spending the last one is topped up in the same response, so the count
        # comes back full rather than dropping to zero. The code just used is
        # still dead — replacement is not forgiveness.
        self.assertEqual(res.data['backup_codes_remaining'], twofa.BACKUP_CODE_COUNT)

        # The same code a second time is dead.
        challenge = self.login(self.admin).data['challenge']
        again = self.client.post('/api/accounts/2fa/verify/',
                                 {'challenge': challenge, 'backup_code': codes[0]}, format='json')
        self.assertEqual(again.status_code, 400)

    def test_spending_the_last_code_hands_back_a_replacement(self):
        """Otherwise a single-code account is left with no way in but the CDSO."""
        totp, codes = self.enroll_with_backup_codes(self.admin)

        challenge = self.login(self.admin).data['challenge']
        res = self.client.post('/api/accounts/2fa/verify/',
                               {'challenge': challenge, 'backup_code': codes[-1]},
                               format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(res.data['backup_codes_replaced'])
        self.assertEqual(len(res.data['backup_codes']), twofa.BACKUP_CODE_COUNT)
        # Never left empty, and the replacement is genuinely new.
        self.assertEqual(res.data['backup_codes_remaining'], twofa.BACKUP_CODE_COUNT)
        self.assertNotEqual(set(res.data['backup_codes']), set(codes))

    def test_the_replacement_code_actually_works(self):
        totp, codes = self.enroll_with_backup_codes(self.admin)
        first = self.client.post(
            '/api/accounts/2fa/verify/',
            {'challenge': self.login(self.admin).data['challenge'],
             'backup_code': codes[-1]}, format='json')
        replacement = first.data['backup_codes'][0]

        res = self.client.post(
            '/api/accounts/2fa/verify/',
            {'challenge': self.login(self.admin).data['challenge'],
             'backup_code': replacement}, format='json')
        self.assertEqual(res.status_code, 200, res.data)

    def test_a_step_up_with_the_last_code_also_replaces_it(self):
        _, totp = make_confirmed_device(self.admin)
        from accounts.twofa_api import _issue_backup_codes
        codes = _issue_backup_codes(self.admin)

        client = self.authed_client(self.admin)
        res = client.post('/api/accounts/2fa/step-up/',
                          {'backup_code': codes[-1]}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(res.data['backup_codes_replaced'])
        self.assertEqual(len(res.data['backup_codes']), twofa.BACKUP_CODE_COUNT)

    def test_codes_still_held_are_not_replaced_or_destroyed(self):
        """Top-up-when-empty, not replace-on-every-use. With the count at 1 the
        distinction is invisible; raise it and replacing would silently throw
        away every code the person had not spent yet."""
        _, totp = make_confirmed_device(self.admin)
        from accounts.twofa_api import _issue_backup_codes
        codes = twofa.generate_backup_codes(3)
        TwoFactorBackupCode.objects.filter(user=self.admin).delete()
        TwoFactorBackupCode.objects.bulk_create([
            TwoFactorBackupCode(user=self.admin, code_hash=twofa.hash_backup_code(c))
            for c in codes
        ])

        res = self.client.post(
            '/api/accounts/2fa/verify/',
            {'challenge': self.login(self.admin).data['challenge'],
             'backup_code': codes[0]}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertNotIn('backup_codes', res.data)      # nothing reissued
        self.assertEqual(res.data['backup_codes_remaining'], 2)

        # And the two survivors still work.
        later = self.client.post(
            '/api/accounts/2fa/verify/',
            {'challenge': self.login(self.admin).data['challenge'],
             'backup_code': codes[1]}, format='json')
        self.assertEqual(later.status_code, 200, later.data)

    def test_backup_code_matches_regardless_of_dashes_and_spacing(self):
        totp, codes = self.enroll_with_backup_codes(self.admin)
        # Indexed from the end so this keeps working whatever BACKUP_CODE_COUNT
        # is — with a single code that is the same one, with ten it is a
        # different one from the test above.
        messy = ' ' + codes[-1].replace('-', ' ') + ' '

        challenge = self.login(self.admin).data['challenge']
        res = self.client.post('/api/accounts/2fa/verify/',
                               {'challenge': challenge, 'backup_code': messy}, format='json')
        self.assertEqual(res.status_code, 200, res.data)

    def test_admin_reset_returns_a_locked_out_user_to_enrollment(self):
        make_confirmed_device(self.owner)
        _, admin_totp = make_confirmed_device(self.admin)
        client, token = self.step_up(self.admin, admin_totp)

        res = client.post(
            f'/api/accounts/users/{self.owner.pk}/2fa/reset/', {}, format='json',
            HTTP_X_STEPUP_TOKEN=token,
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertFalse(TwoFactorDevice.objects.filter(user=self.owner).exists())
        self.assertEqual(self.login(self.owner).data['twofa_action'], 'setup')

    def test_non_admin_cannot_reset_someone_elses_two_factor(self):
        make_confirmed_device(self.admin)
        _, owner_totp = make_confirmed_device(self.owner)
        client, token = self.step_up(self.owner, owner_totp)

        res = client.post(
            f'/api/accounts/users/{self.admin.pk}/2fa/reset/', {}, format='json',
            HTTP_X_STEPUP_TOKEN=token,
        )
        self.assertEqual(res.status_code, 403)
        self.assertTrue(TwoFactorDevice.objects.filter(user=self.admin).exists())


# ── The five protected actions ───────────────────────────────────────────────

class StepUpEnforcementTests(TwoFactorTestCase):
    """Each sensitive endpoint must refuse without a fresh code, and act with one."""

    def setUp(self):
        super().setUp()
        _, self.totp = make_confirmed_device(self.admin)

    def assert_needs_step_up(self, method, url, **kwargs):
        client = self.authed_client(self.admin)
        denied = getattr(client, method)(url, **kwargs)
        self.assertEqual(denied.status_code, 403, f'{url} -> {denied.status_code}')
        self.assertTrue(denied.data.get('stepup_required'), denied.data)

    def test_change_password_needs_step_up(self):
        self.assert_needs_step_up(
            'post', '/api/accounts/change-password/',
            data={'current_password': PASSWORD, 'new_password': 'Brand!New9',
                  'confirm_password': 'Brand!New9'},
            format='json',
        )

    def test_change_password_succeeds_with_step_up(self):
        client, token = self.step_up(self.admin, self.totp)
        res = client.post(
            '/api/accounts/change-password/',
            {'current_password': PASSWORD, 'new_password': 'Brand!New9',
             'confirm_password': 'Brand!New9'},
            format='json', HTTP_X_STEPUP_TOKEN=token,
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password('Brand!New9'))

    def test_system_settings_write_needs_step_up(self):
        self.assert_needs_step_up(
            'put', '/api/vehicles/system-settings/',
            data={'retention_years': 5}, format='json',
        )

    def test_system_settings_read_does_not_need_step_up(self):
        client = self.authed_client(self.admin)
        self.assertEqual(client.get('/api/vehicles/system-settings/').status_code, 200)

    def test_rule_constraint_write_needs_step_up(self):
        self.assert_needs_step_up(
            'post', '/api/vehicles/rules/',
            data={'name': 'Test Rule', 'constraint_type': 'schedule'}, format='json',
        )

    def test_backup_download_needs_step_up(self):
        self.assert_needs_step_up('get', '/api/accounts/system/backup/')

    def test_backup_download_succeeds_with_step_up(self):
        client, token = self.step_up(self.admin, self.totp)
        res = client.get('/api/accounts/system/backup/', HTTP_X_STEPUP_TOKEN=token)
        self.assertEqual(res.status_code, 200)

    def test_restore_needs_step_up(self):
        self.assert_needs_step_up('post', '/api/accounts/system/restore/', data={})

    def test_guard_is_never_asked_for_a_step_up(self):
        """Guards carry no second factor, so the check must wave them through."""
        client = self.authed_client(self.guard)
        res = client.post(
            '/api/accounts/change-password/',
            {'current_password': PASSWORD, 'new_password': 'Guard!New9',
             'confirm_password': 'Guard!New9'},
            format='json',
        )
        self.assertEqual(res.status_code, 200, res.data)

    def test_guard_cannot_write_rules_at_all(self):
        """Waving guards past the step-up must not leave the policy writable —
        the admin check is what closes that door."""
        client = self.authed_client(self.guard)
        res = client.post('/api/vehicles/rules/',
                          {'name': 'Sneaky', 'constraint_type': 'schedule'}, format='json')
        self.assertEqual(res.status_code, 403)

    def test_step_up_expires(self):
        client, token = self.step_up(self.admin, self.totp)
        original = twofa.STEP_UP_MINUTES
        twofa.STEP_UP_MINUTES = 0
        try:
            time.sleep(1)
            res = client.put('/api/vehicles/system-settings/',
                             {'retention_years': 5}, format='json',
                             HTTP_X_STEPUP_TOKEN=token)
        finally:
            twofa.STEP_UP_MINUTES = original
        self.assertEqual(res.status_code, 403)

    def test_step_up_from_one_account_is_useless_on_another(self):
        _, owner_totp = make_confirmed_device(self.owner)
        _, owner_token = self.step_up(self.owner, owner_totp)

        client = self.authed_client(self.admin)
        res = client.put('/api/vehicles/system-settings/',
                         {'retention_years': 5}, format='json',
                         HTTP_X_STEPUP_TOKEN=owner_token)
        self.assertEqual(res.status_code, 403)


# ── Status endpoint ──────────────────────────────────────────────────────────

class StatusTests(TwoFactorTestCase):

    def test_status_reports_enrollment_and_remaining_codes(self):
        _, totp = make_confirmed_device(self.admin)
        from accounts.twofa_api import _issue_backup_codes
        _issue_backup_codes(self.admin)

        res = self.authed_client(self.admin).get('/api/accounts/2fa/status/')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data['applicable'])
        self.assertTrue(res.data['confirmed'])
        self.assertEqual(res.data['backup_codes_remaining'], twofa.BACKUP_CODE_COUNT)

    def test_status_marks_a_guard_as_out_of_scope(self):
        res = self.authed_client(self.guard).get('/api/accounts/2fa/status/')
        self.assertFalse(res.data['applicable'])
        self.assertFalse(res.data['enrolled'])
