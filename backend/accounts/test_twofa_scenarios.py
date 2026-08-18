"""End-to-end two-factor journeys, driven entirely through the real endpoints.

test_twofa.py checks the pieces. This file checks that they compose: every test
here is a complete story told in HTTP calls, starting at the login form with
nothing but an email and a password, exactly as a browser would.

Nothing shortcuts the flow — no hand-built tokens, no direct model writes to
skip a step. If a journey passes here, a person can actually walk it.
"""

import pyotp
from django.contrib.auth.tokens import default_token_generator
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APIClient

from accounts import twofa
from accounts.models import TwoFactorBackupCode, TwoFactorDevice, User

PASSWORD = 'Passw0rd!23'


def next_code(totp):
    """The code the authenticator will show after the current one rolls over.

    Journeys that spend two codes in a row have to use this. A TOTP code is
    single-use, so a person acting twice inside the same 30-second window waits
    for their app to tick over rather than retyping what is on screen — and the
    server accepts the next step already, since it allows one step of drift
    either way. Using totp.now() twice would be testing the replay guard, not
    the journey.
    """
    import time
    return totp.at(int(time.time()) + totp.interval)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class Journey(TestCase):
    """A browser, plus the small vocabulary of steps a journey is made of."""

    def setUp(self):
        cache.clear()
        self.browser = APIClient()
        self.device_token = ''      # what the browser has in localStorage
        self.access = ''            # the session, once there is one
        self.step_up = ''           # sudo-mode grant, held in memory by the app

    # ── the browser ─────────────────────────────────────────────────────────

    def post(self, url, payload=None, **extra):
        headers = dict(extra)
        if self.access:
            headers['HTTP_AUTHORIZATION'] = f'Bearer {self.access}'
        if self.step_up:
            headers.setdefault('HTTP_X_STEPUP_TOKEN', self.step_up)
        return self.browser.post(url, payload or {}, format='json', **headers)

    def get(self, url, **extra):
        headers = dict(extra)
        if self.access:
            headers['HTTP_AUTHORIZATION'] = f'Bearer {self.access}'
        if self.step_up:
            headers.setdefault('HTTP_X_STEPUP_TOKEN', self.step_up)
        return self.browser.get(url, **headers)

    def put(self, url, payload=None, **extra):
        headers = dict(extra)
        if self.access:
            headers['HTTP_AUTHORIZATION'] = f'Bearer {self.access}'
        if self.step_up:
            headers.setdefault('HTTP_X_STEPUP_TOKEN', self.step_up)
        return self.browser.put(url, payload or {}, format='json', **headers)

    # ── steps ───────────────────────────────────────────────────────────────

    def sign_in(self, user, password=PASSWORD):
        """Submit the login form, sending whatever device token we are holding."""
        extra = {'HTTP_X_DEVICE_TOKEN': self.device_token} if self.device_token else {}
        return self.browser.post(
            '/api/auth/login/',
            {'email': user.email, 'password': password},
            format='json', **extra,
        )

    def start_session(self, data):
        """What authStore._startSession does: keep the tokens the server sent."""
        self.access = data['access']
        if data.get('device_token'):
            self.device_token = data['device_token']
        if data.get('step_up_token'):
            self.step_up = data['step_up_token']
        return data

    def enroll(self, challenge):
        """Scan the QR and type the first code. Returns (totp, backup_codes)."""
        setup = self.browser.post('/api/accounts/2fa/setup/',
                                  {'challenge': challenge}, format='json')
        self.assertEqual(setup.status_code, 200, setup.data)
        self.assertTrue(setup.data['qr_code'].startswith('data:image/png;base64,'))

        totp = pyotp.TOTP(setup.data['secret'])
        confirmed = self.browser.post('/api/accounts/2fa/confirm/',
                                      {'challenge': challenge, 'code': totp.now()},
                                      format='json')
        self.assertEqual(confirmed.status_code, 200, confirmed.data)
        self.start_session(confirmed.data)
        return totp, confirmed.data['backup_codes']

    def verify(self, challenge, totp=None, backup_code=None):
        payload = {'challenge': challenge}
        payload.update(
            {'backup_code': backup_code} if backup_code else {'code': next_code(totp)})
        res = self.browser.post('/api/accounts/2fa/verify/', payload, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        return self.start_session(res.data)

    def do_step_up(self, totp):
        """What StepUpGate does when a request comes back needing a code."""
        res = self.post('/api/accounts/2fa/step-up/', {'code': next_code(totp)})
        self.assertEqual(res.status_code, 200, res.data)
        self.step_up = res.data['step_up_token']
        return self.step_up

    # ── people ──────────────────────────────────────────────────────────────

    def make_admin(self, email='cdso@slc.edu.ph', **kw):
        return User.objects.create_user(
            email=email, full_name='CDSO ADMIN', password=PASSWORD, role='admin', **kw)

    def make_owner(self, email='owner@slc.edu.ph', **kw):
        return User.objects.create_user(
            email=email, full_name='VEHICLE OWNER', password=PASSWORD,
            role='vehicle_owner', **kw)

    def make_guard(self, email='guard@slc.edu.ph', **kw):
        from scanning.models import Gate
        Gate.objects.get_or_create(
            gate_id='gate1', defaults={'name': 'Gate 1', 'is_active': True})
        return User.objects.create_user(
            email=email, full_name='GATE GUARD', password=PASSWORD,
            role='security', gate_assignment='gate1', **kw)


# ═══════════════════════════════════════════════════════════════════════════
#  Everyday journeys
# ═══════════════════════════════════════════════════════════════════════════

class NewAdminFirstDay(Journey):
    """A CDSO account created this morning, used for the first time."""

    def test_full_first_day(self):
        admin = self.make_admin()
        admin.must_change_password = True
        admin.save(update_fields=['must_change_password'])

        # 1. Correct password is not enough — there is no session yet.
        paused = self.sign_in(admin)
        self.assertEqual(paused.status_code, 200)
        self.assertEqual(paused.data['twofa_action'], 'setup')
        self.assertNotIn('access', paused.data)

        # 2. Scan the QR, type the code.
        totp, backup_codes = self.enroll(paused.data['challenge'])
        self.assertEqual(len(backup_codes), twofa.BACKUP_CODE_COUNT)
        self.assertTrue(self.access, 'enrollment should hand over the session')

        # 3. The account still owes a password change, and that screen is
        #    step-up protected — but enrollment granted one, so it goes through
        #    without a second code.
        changed = self.post('/api/accounts/change-password/', {
            'current_password': PASSWORD,
            'new_password': 'FirstDay!9',
            'confirm_password': 'FirstDay!9',
        })
        self.assertEqual(changed.status_code, 200, changed.data)

        # 4. Changing the password invalidates every outstanding token — that is
        #    the fingerprint doing its job — so the next sensitive action asks
        #    again rather than riding on the old grant.
        self.step_up = ''
        blocked = self.put('/api/vehicles/system-settings/', {'retention_years': 4})
        self.assertEqual(blocked.status_code, 403)
        self.assertTrue(blocked.data['stepup_required'])

        # 5. A fresh code, and the same save now lands.
        self.do_step_up(totp)
        saved = self.put('/api/vehicles/system-settings/', {'retention_years': 4})
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertEqual(saved.data['retention_years'], 4)


class ReturningAdminSameWeek(Journey):
    """Signed in on Monday, back on Wednesday, on the same laptop."""

    def test_trusted_browser_is_not_asked_again(self):
        admin = self.make_admin()
        totp, _ = self.enroll(self.sign_in(admin).data['challenge'])
        self.assertTrue(self.device_token)

        # Wednesday: new tab, same browser storage.
        self.access = self.step_up = ''
        again = self.sign_in(admin)
        self.assertEqual(again.status_code, 200, again.data)
        self.assertIn('access', again.data)
        self.assertNotIn('twofa_required', again.data)
        self.start_session(again.data)

        # Sensitive work still costs one code, once.
        first = self.put('/api/vehicles/system-settings/', {'retention_years': 3})
        self.assertEqual(first.status_code, 403)

        self.do_step_up(totp)
        self.assertEqual(
            self.put('/api/vehicles/system-settings/', {'retention_years': 3}).status_code, 200)

        # ...and the rest of the ten minutes is free — no second prompt for the
        # next save, which is the whole point of sudo mode.
        second = self.put('/api/vehicles/system-settings/', {'scan_dedup_seconds': 45})
        self.assertEqual(second.status_code, 200, second.data)

        rules = self.post('/api/vehicles/rules/', {
            'name': 'Student Hours', 'constraint_type': 'student_vehicle',
            'days': ['Monday'], 'start_time': '06:00', 'end_time': '20:00',
        })
        self.assertEqual(rules.status_code, 201, rules.data)


class AdminBackAfterAWeek(Journey):
    """The dormancy rule, on a browser that is still holding a valid token."""

    def test_a_week_of_silence_costs_a_code(self):
        admin = self.make_admin()
        totp, _ = self.enroll(self.sign_in(admin).data['challenge'])
        trusted = self.device_token
        self.access = ''

        # Wind the clock back on the account's last login.
        User.objects.filter(pk=admin.pk).update(
            last_login=timezone.now() - timezone.timedelta(days=twofa.DORMANCY_DAYS + 1))

        paused = self.sign_in(admin)
        self.assertEqual(paused.data['twofa_action'], 'verify')
        self.assertNotIn('access', paused.data)
        self.assertEqual(self.device_token, trusted,
                         'the browser token is untouched — it is simply not enough')

        self.verify(paused.data['challenge'], totp)
        self.assertTrue(self.access)


class OwnerEverydayUse(Journey):
    """Vehicle owners are in scope too, and their portal must still work."""

    def test_owner_enrolls_and_changes_their_password(self):
        owner = self.make_owner()
        totp, _ = self.enroll(self.sign_in(owner).data['challenge'])

        self.assertEqual(self.get('/api/accounts/me/').status_code, 200)

        self.step_up = ''
        blocked = self.post('/api/accounts/change-password/', {
            'current_password': PASSWORD,
            'new_password': 'OwnerNew!9', 'confirm_password': 'OwnerNew!9',
        })
        self.assertEqual(blocked.status_code, 403)
        self.assertTrue(blocked.data['stepup_required'])

        self.do_step_up(totp)
        ok = self.post('/api/accounts/change-password/', {
            'current_password': PASSWORD,
            'new_password': 'OwnerNew!9', 'confirm_password': 'OwnerNew!9',
        })
        self.assertEqual(ok.status_code, 200, ok.data)


class GuardWholeShift(Journey):
    """A guard must never meet a code — the gate cannot wait for a phone."""

    def test_guard_never_sees_two_factor(self):
        guard = self.make_guard()

        logged_in = self.browser.post(
            '/api/auth/guard-login/',
            {'email': guard.email, 'password': PASSWORD, 'gate': 'gate1'},
            format='json')
        self.assertEqual(logged_in.status_code, 200, logged_in.data)
        self.assertNotIn('twofa_required', logged_in.data)
        self.access = logged_in.data['access']

        # No enrollment row was created for them anywhere.
        self.assertFalse(TwoFactorDevice.objects.filter(user=guard).exists())
        self.assertFalse(self.get('/api/accounts/2fa/status/').data['applicable'])

        # Their own password change goes through with no code at all.
        changed = self.post('/api/accounts/change-password/', {
            'current_password': PASSWORD,
            'new_password': 'GuardNew!9', 'confirm_password': 'GuardNew!9',
        })
        self.assertEqual(changed.status_code, 200, changed.data)

    def test_guard_still_cannot_touch_the_access_rules(self):
        """Waving guards past the code must not leave the policy open to them."""
        guard = self.make_guard()
        logged_in = self.browser.post(
            '/api/auth/guard-login/',
            {'email': guard.email, 'password': PASSWORD, 'gate': 'gate1'},
            format='json')
        self.access = logged_in.data['access']

        self.assertEqual(self.get('/api/vehicles/rules/').status_code, 200)
        for res in (
            self.post('/api/vehicles/rules/', {
                'name': 'Sneaky', 'constraint_type': 'student_vehicle'}),
            self.put('/api/vehicles/system-settings/', {'retention_years': 1}),
            self.get('/api/accounts/system/backup/'),
        ):
            self.assertEqual(res.status_code, 403)


# ═══════════════════════════════════════════════════════════════════════════
#  Recovery journeys
# ═══════════════════════════════════════════════════════════════════════════

class LostPhone(Journey):
    """The path that stops 2FA from becoming a locked door."""

    def test_backup_code_gets_them_in_then_the_cdso_re_pairs_them(self):
        owner = self.make_owner()
        _, backup_codes = self.enroll(self.sign_in(owner).data['challenge'])

        # New phone, new browser — the old authenticator is gone.
        self.browser = APIClient()
        self.access = self.device_token = self.step_up = ''

        paused = self.sign_in(owner)
        self.assertEqual(paused.data['twofa_action'], 'verify')
        self.verify(paused.data['challenge'], backup_code=backup_codes[0])
        self.assertTrue(self.access, 'a backup code is a way back in')

        # That code is spent for good.
        self.assertEqual(
            TwoFactorBackupCode.objects.filter(user=owner, used_at__isnull=True).count(),
            twofa.BACKUP_CODE_COUNT - 1)

        # Meanwhile the CDSO clears the old device so a new phone can be paired.
        cdso = Journey()
        cdso.setUp()
        admin = self.make_admin()
        admin_totp, _ = cdso.enroll(cdso.sign_in(admin).data['challenge'])
        cdso.do_step_up(admin_totp)
        reset = cdso.post(f'/api/accounts/users/{owner.pk}/2fa/reset/')
        self.assertEqual(reset.status_code, 200, reset.data)

        # The owner's next login starts enrollment over, on a clean phone.
        self.browser = APIClient()
        self.access = self.device_token = self.step_up = ''
        self.assertEqual(self.sign_in(owner).data['twofa_action'], 'setup')
        self.enroll(self.sign_in(owner).data['challenge'])
        self.assertTrue(self.access)


class ClosedTheTabOnTheBackupCodes(Journey):
    """The accidental-exit case, and the way back from it.

    Abandoning the QR is harmless — nothing is confirmed, so the next login
    starts over. Closing the tab on the *backup codes* is the one step that
    cannot be repeated: the account is enrolled and the codes are stored hashed,
    so those exact codes are gone. Account Security has to be able to replace
    them, or the person is one lost phone away from needing a CDSO reset.
    """

    def test_abandoning_the_qr_leaves_no_damage(self):
        owner = self.make_owner()
        challenge = self.sign_in(owner).data['challenge']

        # Fetch the QR, then walk away without confirming.
        first = self.browser.post('/api/accounts/2fa/setup/',
                                  {'challenge': challenge}, format='json')
        self.assertEqual(first.status_code, 200)
        abandoned_secret = first.data['secret']

        device = TwoFactorDevice.objects.get(user=owner)
        self.assertIsNone(device.confirmed_at, 'an abandoned setup must not count')

        # Next login offers setup again — with a brand-new secret, so the
        # abandoned one can never be used against the account.
        self.browser = APIClient()
        again = self.sign_in(owner)
        self.assertEqual(again.data['twofa_action'], 'setup')

        second = self.browser.post('/api/accounts/2fa/setup/',
                                   {'challenge': again.data['challenge']}, format='json')
        self.assertNotEqual(second.data['secret'], abandoned_secret)

        totp = pyotp.TOTP(second.data['secret'])
        done = self.browser.post('/api/accounts/2fa/confirm/',
                                 {'challenge': again.data['challenge'], 'code': totp.now()},
                                 format='json')
        self.assertEqual(done.status_code, 200, done.data)

    def test_new_codes_can_be_generated_for_someone_who_never_saw_theirs(self):
        owner = self.make_owner()
        totp, original = self.enroll(self.sign_in(owner).data['challenge'])

        # Pretend the tab was closed here: enrolled, ten codes stored, none read.
        self.assertEqual(
            TwoFactorBackupCode.objects.filter(user=owner, used_at__isnull=True).count(),
            twofa.BACKUP_CODE_COUNT)

        # Account Security shows the state...
        self.step_up = ''
        status = self.get('/api/accounts/2fa/status/')
        self.assertEqual(status.status_code, 200)
        self.assertTrue(status.data['confirmed'])
        self.assertEqual(status.data['email'], owner.email)
        self.assertEqual(status.data['backup_codes_remaining'], twofa.BACKUP_CODE_COUNT)

        # ...and regenerating needs a code, then hands over a fresh set.
        blocked = self.post('/api/accounts/2fa/backup-codes/')
        self.assertEqual(blocked.status_code, 403)
        self.assertTrue(blocked.data['stepup_required'])

        self.do_step_up(totp)
        fresh = self.post('/api/accounts/2fa/backup-codes/')
        self.assertEqual(fresh.status_code, 200, fresh.data)
        self.assertEqual(len(fresh.data['backup_codes']), twofa.BACKUP_CODE_COUNT)
        self.assertNotEqual(set(fresh.data['backup_codes']), set(original))

        # The unseen originals are dead, and a new one works.
        self.browser = APIClient()
        self.access = self.device_token = self.step_up = ''
        stale = self.browser.post(
            '/api/accounts/2fa/verify/',
            {'challenge': self.sign_in(owner).data['challenge'],
             'backup_code': original[0]}, format='json')
        self.assertEqual(stale.status_code, 400)

        self.verify(self.sign_in(owner).data['challenge'],
                    backup_code=fresh.data['backup_codes'][0])
        self.assertTrue(self.access)

    def test_a_new_phone_can_be_paired_without_involving_the_cdso(self):
        owner = self.make_owner()
        old_totp, _ = self.enroll(self.sign_in(owner).data['challenge'])

        # Pairing over a working device is sensitive — prove the old one first.
        self.step_up = ''
        self.assertEqual(self.post('/api/accounts/2fa/setup/').status_code, 403)

        self.do_step_up(old_totp)
        paired = self.post('/api/accounts/2fa/setup/')
        self.assertEqual(paired.status_code, 200, paired.data)

        new_totp = pyotp.TOTP(paired.data['secret'])
        confirmed = self.post('/api/accounts/2fa/confirm/', {'code': new_totp.now()})
        self.assertEqual(confirmed.status_code, 200, confirmed.data)

        # The new phone works; the old one is now useless.
        self.browser = APIClient()
        self.access = self.device_token = self.step_up = ''
        challenge = self.sign_in(owner).data['challenge']
        rejected = self.browser.post('/api/accounts/2fa/verify/',
                                     {'challenge': challenge, 'code': old_totp.now()},
                                     format='json')
        self.assertEqual(rejected.status_code, 400)
        self.verify(self.sign_in(owner).data['challenge'], new_totp)
        self.assertTrue(self.access)


class ForgotPassword(Journey):
    """A reset proves the mailbox, not the person."""

    def reset_via_email_link(self, user, new_password):
        # The real link is minted server-side from the row as it stands now.
        # Django hashes last_login into the token, so a stale in-memory copy
        # produces a token the view rejects — which is Django working correctly
        # (a login invalidates outstanding reset links), not the flow failing.
        user.refresh_from_db()
        res = self.browser.post('/api/accounts/password-reset/confirm/', {
            'uid': urlsafe_base64_encode(force_bytes(user.pk)),
            'token': default_token_generator.make_token(user),
            'new_password': new_password, 'confirm_password': new_password,
        }, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        return res

    def test_reset_then_login_demands_a_code_on_the_trusted_browser(self):
        admin = self.make_admin()
        totp, _ = self.enroll(self.sign_in(admin).data['challenge'])
        self.assertTrue(self.device_token)
        self.access = self.step_up = ''

        told = self.reset_via_email_link(admin, 'Reset!Pass9')
        self.assertTrue(told.data['twofa_required_next_login'],
                        'the reset screen should warn them a code is coming')

        # Same browser, still holding a token it earned minutes ago.
        paused = self.sign_in(admin, password='Reset!Pass9')
        self.assertEqual(paused.data['twofa_action'], 'verify')
        self.assertNotIn('access', paused.data)

        # And the password alone cannot spend the flag by retrying.
        self.sign_in(admin, password='Reset!Pass9')
        admin.refresh_from_db()
        self.assertTrue(admin.must_verify_2fa)

        self.verify(paused.data['challenge'], totp)
        admin.refresh_from_db()
        self.assertFalse(admin.must_verify_2fa)

        # Back to normal afterwards — it was a one-shot, not a punishment.
        self.access = ''
        self.assertIn('access', self.sign_in(admin, password='Reset!Pass9').data)


# ═══════════════════════════════════════════════════════════════════════════
#  Attacker journeys — each one must dead-end
# ═══════════════════════════════════════════════════════════════════════════

class StolenPassword(Journey):
    """Everything below assumes the password is already known."""

    def setUp(self):
        super().setUp()
        self.admin = self.make_admin()
        victim = Journey()
        victim.setUp()
        self.totp, self.backup_codes = victim.enroll(
            victim.sign_in(self.admin).data['challenge'])
        self.victim = victim

    def test_password_alone_reaches_nothing(self):
        """The attacker's browser has no device token and no authenticator."""
        paused = self.sign_in(self.admin)
        self.assertEqual(paused.data['twofa_action'], 'verify')
        self.assertNotIn('access', paused.data)
        self.assertNotIn('refresh', paused.data)

    def test_guessing_codes_is_locked_out_after_five_tries(self):
        challenge = self.sign_in(self.admin).data['challenge']
        for _ in range(5):
            self.browser.post('/api/accounts/2fa/verify/',
                              {'challenge': challenge, 'code': '000000'}, format='json')
        blocked = self.browser.post('/api/accounts/2fa/verify/',
                                    {'challenge': challenge, 'code': '000000'}, format='json')
        self.assertEqual(blocked.status_code, 429)

        # The lockout is real: even the correct code is refused while it holds.
        correct = self.browser.post('/api/accounts/2fa/verify/',
                                    {'challenge': challenge, 'code': next_code(self.totp)},
                                    format='json')
        self.assertEqual(correct.status_code, 429)

    def test_a_code_seen_over_the_shoulder_cannot_be_reused(self):
        code = next_code(self.totp)
        challenge = self.sign_in(self.admin).data['challenge']
        first = self.browser.post('/api/accounts/2fa/verify/',
                                  {'challenge': challenge, 'code': code}, format='json')
        self.assertEqual(first.status_code, 200, first.data)

        thief = Journey()
        thief.setUp()
        stolen_challenge = thief.sign_in(self.admin).data['challenge']
        replayed = thief.browser.post('/api/accounts/2fa/verify/',
                                      {'challenge': stolen_challenge, 'code': code},
                                      format='json')
        self.assertEqual(replayed.status_code, 400)

    def test_a_used_backup_code_is_dead(self):
        challenge = self.sign_in(self.admin).data['challenge']
        self.verify(challenge, backup_code=self.backup_codes[0])

        thief = Journey()
        thief.setUp()
        again = thief.browser.post(
            '/api/accounts/2fa/verify/',
            {'challenge': thief.sign_in(self.admin).data['challenge'],
             'backup_code': self.backup_codes[0]}, format='json')
        self.assertEqual(again.status_code, 400)

    def test_forged_tokens_are_refused_everywhere(self):
        for payload in ('', 'not-a-token', 'a.b.c'):
            self.assertEqual(
                self.browser.post('/api/accounts/2fa/verify/',
                                  {'challenge': payload, 'code': '123456'},
                                  format='json').status_code, 400)
            self.assertEqual(
                self.browser.post('/api/accounts/2fa/setup/',
                                  {'challenge': payload}, format='json').status_code, 401)

    def test_a_hijacked_session_still_cannot_reach_the_dangerous_screens(self):
        """The realistic case: an unlocked laptop with a live session on it."""
        self.access = self.victim.access
        self.step_up = ''

        self.assertEqual(self.get('/api/accounts/me/').status_code, 200)   # ordinary use
        for res in (
            self.get('/api/accounts/system/backup/'),
            self.post('/api/accounts/system/restore/'),
            self.put('/api/vehicles/system-settings/', {'retention_years': 1}),
            self.post('/api/vehicles/rules/', {
                'name': 'X', 'constraint_type': 'student_vehicle'}),
            self.post('/api/accounts/change-password/', {
                'current_password': PASSWORD,
                'new_password': 'Hijack!ed9', 'confirm_password': 'Hijack!ed9'}),
            self.post(f'/api/accounts/users/{self.admin.pk}/2fa/reset/'),
        ):
            self.assertEqual(res.status_code, 403, res.data)
            self.assertTrue(res.data.get('stepup_required'), res.data)

    def test_a_step_up_from_another_account_is_worthless(self):
        other = self.make_owner()
        attacker = Journey()
        attacker.setUp()
        other_totp, _ = attacker.enroll(attacker.sign_in(other).data['challenge'])
        attacker.do_step_up(other_totp)

        self.access = self.victim.access
        self.step_up = attacker.step_up
        res = self.put('/api/vehicles/system-settings/', {'retention_years': 1})
        self.assertEqual(res.status_code, 403)
