"""Forgot-password flow, end to end.

Each test pins one way the flow used to go wrong while still answering 200 or
showing a success screen — the kind of fault nobody reports because nothing
looks broken:

  * the link lived three days while the email and the page said one hour,
  * a stale session in the browser turned both public endpoints into a 401,
  * a password with a space at either end was saved without it, so the
    password the user had just typed no longer signed them in,
  * a non-text email crashed the request endpoint with a 500,
  * two live accounts whose addresses differ only in case crashed it too,
  * a link kept working for an account disabled after it was sent,
  * every session open before the reset stayed open after it,
  * nothing limited how many reset emails one address could be sent.
"""

from datetime import datetime, timedelta
from unittest import mock

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User

REQUEST = '/api/accounts/password-reset/request/'
CONFIRM = '/api/accounts/password-reset/confirm/'
OLD_PASSWORD = 'Old!Pass123'
NEW_PASSWORD = 'Reset!Pass9'


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   DEFAULT_FROM_EMAIL='slccdso@gmail.com', EMAIL_SEND_ASYNC=False)
class PasswordResetFlowTests(TestCase):

    def setUp(self):
        cache.clear()   # the request limits are cache-backed
        self.client = APIClient()
        self.owner = User.objects.create_user(
            email='reset-owner@test.local', full_name='RESET OWNER',
            password=OLD_PASSWORD, role='vehicle_owner',
        )

    # ── helpers ──────────────────────────────────────────────────────────────

    def link_for(self, user):
        return {
            'uid': urlsafe_base64_encode(force_bytes(user.pk)),
            'token': default_token_generator.make_token(user),
        }

    def confirm(self, link, password=NEW_PASSWORD, **extra):
        return self.client.post(CONFIRM, {
            **link, 'new_password': password, 'confirm_password': password,
        }, format='json', **extra)

    def login(self, email, password):
        return APIClient().post('/api/auth/login/',
                                {'email': email, 'password': password}, format='json')

    # ── the happy path still works ───────────────────────────────────────────

    def test_request_then_confirm_then_login(self):
        res = self.client.post(REQUEST, {'email': self.owner.email}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('/reset-password?uid=', mail.outbox[0].body)

        res = self.confirm(self.link_for(self.owner))
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(self.login(self.owner.email, NEW_PASSWORD).status_code, 200)

    def test_link_is_single_use(self):
        link = self.link_for(self.owner)
        self.assertEqual(self.confirm(link).status_code, 200)
        self.assertEqual(self.confirm(link, password='Another!Pass7').status_code, 400)

    # ── token lifetime ───────────────────────────────────────────────────────

    def test_link_expires_after_the_hour_the_email_promises(self):
        self.assertEqual(settings.PASSWORD_RESET_TIMEOUT, 3600)
        two_hours_ago = datetime.now() - timedelta(hours=2)
        with mock.patch.object(default_token_generator, '_now', return_value=two_hours_ago):
            link = self.link_for(self.owner)
        res = self.confirm(link)
        self.assertEqual(res.status_code, 400)
        self.assertIn('expired', res.data['error'])

    # ── a stale session in the browser ───────────────────────────────────────

    def test_request_ignores_a_stale_bearer_token(self):
        res = self.client.post(REQUEST, {'email': self.owner.email}, format='json',
                               HTTP_AUTHORIZATION='Bearer not-a-real-token')
        self.assertEqual(res.status_code, 200)

    def test_confirm_ignores_a_stale_bearer_token(self):
        res = self.confirm(self.link_for(self.owner),
                           HTTP_AUTHORIZATION='Bearer not-a-real-token')
        self.assertEqual(res.status_code, 200, getattr(res, 'data', None))

    # ── the password is saved exactly as typed ───────────────────────────────

    def test_password_with_surrounding_spaces_signs_in_as_typed(self):
        typed = ' Spaced!Pass9 '
        self.assertEqual(self.confirm(self.link_for(self.owner), password=typed).status_code, 200)
        self.assertEqual(self.login(self.owner.email, typed).status_code, 200)

    # ── malformed input is a 400, never a 500 ────────────────────────────────

    def test_non_text_email_is_rejected_cleanly(self):
        for bad in (None, 123, ['x@test.local'], {'a': 1}):
            res = self.client.post(REQUEST, {'email': bad}, format='json')
            self.assertEqual(res.status_code, 400, bad)

    def test_non_text_confirm_fields_are_rejected_cleanly(self):
        res = self.client.post(CONFIRM, {'uid': None, 'token': 5,
                                         'new_password': [], 'confirm_password': {}},
                               format='json')
        self.assertEqual(res.status_code, 400)

    def test_case_variant_live_accounts_do_not_crash_the_request(self):
        # The uniqueness constraint is case-sensitive; only the serializers
        # compare case-insensitively, so accounts made another way can collide.
        User.objects.create_user(email='Dup@test.local', full_name='DUP ONE',
                                 password=OLD_PASSWORD, role='vehicle_owner')
        User.objects.create_user(email='dup@test.local', full_name='DUP TWO',
                                 password=OLD_PASSWORD, role='vehicle_owner')
        res = self.client.post(REQUEST, {'email': 'dup@test.local'}, format='json')
        self.assertEqual(res.status_code, 200)

    # ── accounts that may no longer be used ──────────────────────────────────

    def test_link_stops_working_once_the_account_is_disabled(self):
        link = self.link_for(self.owner)
        User.objects.filter(pk=self.owner.pk).update(is_active=False)
        self.assertEqual(self.confirm(link).status_code, 400)

    def test_link_stops_working_once_the_account_is_archived(self):
        link = self.link_for(self.owner)
        User.objects.filter(pk=self.owner.pk).update(is_archived=True)
        self.assertEqual(self.confirm(link).status_code, 400)

    # ── a reset ends the sessions that were open before it ───────────────────

    def test_reset_signs_out_existing_sessions(self):
        refresh = str(RefreshToken.for_user(self.owner))
        self.assertEqual(self.confirm(self.link_for(self.owner)).status_code, 200)
        res = APIClient().post('/api/auth/refresh/', {'refresh': refresh}, format='json')
        self.assertEqual(res.status_code, 401)

    # ── how often a reset can be requested ───────────────────────────────────

    def test_one_address_cannot_be_flooded_with_reset_emails(self):
        for _ in range(6):
            res = self.client.post(REQUEST, {'email': self.owner.email}, format='json')
            # Always the neutral answer: the limit must not reveal that the
            # address has an account.
            self.assertEqual(res.status_code, 200)
        self.assertEqual(len(mail.outbox), 3)

    def test_one_client_cannot_spray_requests(self):
        codes = [
            self.client.post(REQUEST, {'email': f'nobody{i}@test.local'},
                             format='json', REMOTE_ADDR='203.0.113.9').status_code
            for i in range(12)
        ]
        self.assertEqual(codes[:10], [200] * 10)
        self.assertEqual(codes[10:], [429, 429])
