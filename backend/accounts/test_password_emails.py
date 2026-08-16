"""Which email a password change produces, and what must never stop it.

The first change replaces the temporary password the CDSO issued, so it is a
welcome; every later change is a security notice, which is the only warning an
owner gets that somebody else is in their account. Picking the wrong one is
invisible in production — mail still sends — so the split is pinned here.
"""
from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.email_utils import notify_password_set
from accounts.models import User

LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'
NEW_PASSWORD = 'BrandNew1!'
OLD_PASSWORD = 'TempPass1!'


def html_of(msg):
    return next((body for body, mimetype in msg.alternatives
                 if mimetype == 'text/html'), '')


@override_settings(EMAIL_BACKEND=LOCMEM, DEFAULT_FROM_EMAIL='slccdso@gmail.com',
                   FRONTEND_URL='https://spvvs.example.test')
class PasswordChangeEmailTests(TestCase):
    def _user(self, must_change=True, **kw):
        kw.setdefault('role', User.Role.VEHICLE_OWNER)
        user = User.objects.create_user(
            email=kw.pop('email', 'owner@slc.edu.ph'),
            password=OLD_PASSWORD,
            full_name=kw.pop('full_name', 'DELA CRUZ, JUAN'),
            **kw)
        user.must_change_password = must_change
        user.save(update_fields=['must_change_password'])
        return user

    def _change(self, user, new=NEW_PASSWORD, current=OLD_PASSWORD):
        client = APIClient()
        client.force_authenticate(user=user)
        return client.post('/api/accounts/change-password/', {
            'current_password': current,
            'new_password': new,
            'confirm_password': new,
        }, format='json')

    # ── which message ────────────────────────────────────────────────────
    def test_first_change_sends_the_welcome(self):
        user = self._user(must_change=True)
        mail.outbox.clear()
        self.assertEqual(self._change(user).status_code, 200)

        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[-1]
        self.assertIn('Welcome', msg.subject)
        self.assertEqual(msg.to, [user.email])
        self.assertIn('DELA CRUZ, JUAN', html_of(msg))

    def test_later_change_sends_the_security_notice(self):
        user = self._user(must_change=False)
        mail.outbox.clear()
        self.assertEqual(self._change(user).status_code, 200)

        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[-1]
        self.assertIn('password was changed', msg.subject)
        self.assertIn('If this was not you', html_of(msg))
        self.assertNotIn('Welcome', msg.subject)

    def test_the_welcome_is_sent_once_not_on_every_change(self):
        """must_change_password is cleared by the first change, so the second
        must fall through to the security notice."""
        user = self._user(must_change=True)
        mail.outbox.clear()
        self._change(user)
        user.refresh_from_db()
        self._change(user, new='ThirdOne2!', current=NEW_PASSWORD)

        self.assertEqual(len(mail.outbox), 2)
        self.assertIn('Welcome', mail.outbox[0].subject)
        self.assertIn('password was changed', mail.outbox[1].subject)

    def test_the_welcome_describes_the_users_own_role(self):
        guard = self._user(must_change=True, role=User.Role.SECURITY,
                           email='guard@slc.edu.ph')
        mail.outbox.clear()
        self._change(guard)
        self.assertIn('scan vehicles at the gate', html_of(mail.outbox[-1]).lower())

    # ── never at the password's expense ──────────────────────────────────
    def test_a_mail_failure_does_not_fail_the_password_change(self):
        """The password is already saved when the email is sent. Letting the
        error propagate would report failure for a change that succeeded, and
        the user would retry with what is now their current password."""
        user = self._user(must_change=False)
        with mock.patch('accounts.email_utils.send_mail',
                        side_effect=RuntimeError('smtp is down')):
            with self.assertLogs('accounts.email_utils', level='ERROR'):
                response = self._change(user)

        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.check_password(NEW_PASSWORD),
                        'the new password was not saved')

    def test_a_rejected_change_sends_nothing(self):
        user = self._user(must_change=False)
        mail.outbox.clear()
        response = self._change(user, current='WrongCurrent1!')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(mail.outbox, [])
        user.refresh_from_db()
        self.assertTrue(user.check_password(OLD_PASSWORD))

    def test_a_user_without_an_email_is_skipped(self):
        """create_user requires an email, so this guards the helper directly —
        it exists so a blanked address cannot turn into a send to nobody."""
        user = self._user(must_change=False)
        user.email = ''
        mail.outbox.clear()

        notify_password_set(user, was_first_change=False)
        notify_password_set(user, was_first_change=True)

        self.assertEqual(mail.outbox, [])
