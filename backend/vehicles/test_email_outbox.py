"""The retry outbox: a send that fails is kept and retried, not lost."""
import smtplib
from datetime import timedelta
from unittest import mock

import requests
from django.core.mail import EmailMultiAlternatives, send_mail
from django.core.mail.backends.base import BaseEmailBackend
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import Notification
from config.email_backends import BrevoEmailBackend, PermanentEmailError
from vehicles import email_outbox
from vehicles.email_utils import send_in_background
from vehicles.models import EmailOutbox


class FlakyTransport(BaseEmailBackend):
    """Raises `error` for the next `failures` sends, then delivers into `sent`."""
    failures = 0
    error = ConnectionError('connection reset by peer')
    sent = []

    def send_messages(self, messages):
        if FlakyTransport.failures > 0:
            FlakyTransport.failures -= 1
            raise FlakyTransport.error
        FlakyTransport.sent.extend(messages)
        return len(messages)


OUTBOX = dict(EMAIL_BACKEND='config.email_backends.OutboxEmailBackend',
              EMAIL_TRANSPORT_BACKEND='vehicles.test_email_outbox.FlakyTransport',
              EMAIL_SEND_ASYNC=False, DEFAULT_FROM_EMAIL='cdso@test.local')


@override_settings(**OUTBOX)
class OutboxTests(TestCase):
    def setUp(self):
        FlakyTransport.failures = 0
        FlakyTransport.error = ConnectionError('connection reset by peer')
        FlakyTransport.sent = []

    def _make_due(self):
        EmailOutbox.objects.update(next_attempt_at=timezone.now() - timedelta(seconds=1))

    def _message(self):
        msg = EmailMultiAlternatives('Approved', 'plain body', None, ['owner@test.local'],
                                     bcc=['audit@test.local'])
        msg.attach_alternative('<b>html body</b>', 'text/html')
        msg.attach('registration.pdf', b'%PDF-1.4 \x00\xff binary', 'application/pdf')
        return msg

    def test_a_healthy_send_goes_straight_through(self):
        self.assertEqual(self._message().send(), 1)
        self.assertEqual(len(FlakyTransport.sent), 1)
        self.assertFalse(EmailOutbox.objects.exists())

    def test_a_failed_send_is_queued_then_delivered_intact_on_retry(self):
        FlakyTransport.failures = 1
        self.assertEqual(self._message().send(), 1)   # the caller sees success
        row = EmailOutbox.objects.get()
        self.assertEqual(row.status, EmailOutbox.Status.PENDING)
        self.assertIn('connection reset', row.last_error)
        self.assertGreater(row.next_attempt_at, timezone.now())

        self.assertEqual(email_outbox.run_due()['sent'], 0)   # not due yet
        self._make_due()
        self.assertEqual(email_outbox.run_due()['sent'], 1)

        self.assertFalse(EmailOutbox.objects.exists())   # deleted once delivered
        out = FlakyTransport.sent[0]
        self.assertEqual((out.subject, out.body, out.to, out.bcc),
                         ('Approved', 'plain body', ['owner@test.local'], ['audit@test.local']))
        self.assertEqual(out.from_email, 'cdso@test.local')
        self.assertEqual(out.alternatives[0][0], '<b>html body</b>')
        self.assertEqual(out.attachments[0], ('registration.pdf', b'%PDF-1.4 \x00\xff binary',
                                              'application/pdf'))

    def test_it_keeps_retrying_with_growing_gaps(self):
        FlakyTransport.failures = 4
        send_mail('Hi', 'body', None, ['owner@test.local'])
        gaps = []
        for _ in range(3):
            self._make_due()
            before = timezone.now()
            self.assertEqual(email_outbox.run_due()['retrying'], 1)
            gaps.append(EmailOutbox.objects.get().next_attempt_at - before)
        self.assertEqual(EmailOutbox.objects.get().attempts, 4)
        self.assertTrue(gaps[0] < gaps[1] < gaps[2])
        self._make_due()
        self.assertEqual(email_outbox.run_due()['sent'], 1)

    def test_background_sends_no_longer_raise_the_failure_notice(self):
        FlakyTransport.failures = 1
        called = []
        send_in_background(lambda: send_mail('Hi', 'b', None, ['o@test.local']),
                           on_failure=lambda: called.append(True))
        self.assertEqual(called, [])
        self.assertEqual(EmailOutbox.objects.count(), 1)

    def test_a_refused_recipient_is_not_retried(self):
        FlakyTransport.failures = 1
        FlakyTransport.error = smtplib.SMTPRecipientsRefused({'x@bad': (550, b'no such user')})
        with self.assertRaises(smtplib.SMTPRecipientsRefused):
            send_mail('Hi', 'body', None, ['x@bad'])
        self.assertFalse(EmailOutbox.objects.exists())

    def test_fail_silently_still_queues(self):
        FlakyTransport.failures = 1
        send_mail('Hi', 'body', None, ['owner@test.local'], fail_silently=True)
        self.assertEqual(EmailOutbox.objects.count(), 1)

    def test_when_the_queue_itself_fails_the_original_error_surfaces(self):
        FlakyTransport.failures = 1
        with mock.patch.object(EmailOutbox.objects, 'create', side_effect=RuntimeError('db down')):
            with self.assertRaises(ConnectionError):
                send_mail('Hi', 'body', None, ['owner@test.local'])

    def test_a_deadline_caps_the_retry_window(self):
        FlakyTransport.failures = 1
        with email_outbox.expires_in(3600):
            send_mail('Reset', 'link', None, ['owner@test.local'])
        row = EmailOutbox.objects.get()
        self.assertTrue(row.message['hard_deadline'])
        self.assertLess(row.expires_at, timezone.now() + timedelta(hours=1, seconds=5))

    def test_a_deadline_shorter_than_the_first_retry_is_not_queued(self):
        FlakyTransport.failures = 1
        with email_outbox.expires_in(30):
            with self.assertRaises(ConnectionError):
                send_mail('Reset', 'link', None, ['owner@test.local'])
        self.assertFalse(EmailOutbox.objects.exists())

    def test_an_expired_message_is_given_up_and_reported(self):
        FlakyTransport.failures = 1
        send_mail('Approved', 'body', None, ['owner@test.local'])
        EmailOutbox.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self._make_due()
        self.assertEqual(email_outbox.run_due()['failed'], 1)
        self.assertEqual(EmailOutbox.objects.get().status, EmailOutbox.Status.FAILED)
        notice = Notification.objects.get(event='email_failed')
        self.assertEqual(notice.severity, 'critical')
        self.assertIn('owner@test.local', notice.message)

    def test_the_cdso_is_told_once_when_a_message_stays_stuck(self):
        FlakyTransport.failures = 3
        send_mail('Approved', 'body', None, ['owner@test.local'])
        EmailOutbox.objects.update(created_at=timezone.now() - timedelta(minutes=31))
        for _ in range(2):
            self._make_due()
            email_outbox.run_due()
        self.assertEqual(Notification.objects.filter(event='email_delayed').count(), 1)
        self._make_due()
        email_outbox.run_due()   # finally delivered
        self.assertEqual(Notification.objects.filter(event='email_delivered_late').count(), 1)

    def test_a_lapsed_claim_is_picked_up_again(self):
        FlakyTransport.failures = 1
        send_mail('Hi', 'body', None, ['owner@test.local'])
        # A server that claimed the row and died leaves next_attempt_at a lease
        # ahead; once that passes, anyone may take it.
        EmailOutbox.objects.update(next_attempt_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(email_outbox.run_due()['sent'], 1)


@override_settings(**dict(OUTBOX, EMAIL_SEND_ASYNC=True))
class QueueFirstTests(TestCase):
    """In a server process the caller only stores the message; the worker sends
    it. A slow mail provider then costs nobody a frozen screen."""

    def setUp(self):
        FlakyTransport.failures = 0
        FlakyTransport.sent = []
        patcher = mock.patch.object(email_outbox, '_started', True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_caller_only_stores_and_the_worker_sends(self):
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            self.assertEqual(send_mail('Approved', 'body', None, ['owner@test.local']), 1)
        self.assertEqual(FlakyTransport.sent, [])                 # nothing sent on the caller's time
        row = EmailOutbox.objects.get()
        self.assertEqual((row.attempts, row.last_error), (0, ''))
        self.assertLessEqual(row.next_attempt_at, timezone.now())  # due at once
        self.assertIn(email_outbox.wake, callbacks)                # the worker is woken after commit

        self.assertEqual(email_outbox.run_due()['sent'], 1)
        self.assertEqual(FlakyTransport.sent[0].subject, 'Approved')
        self.assertFalse(EmailOutbox.objects.exists())

    def test_a_first_send_that_fails_waits_a_minute_like_any_retry(self):
        send_mail('Approved', 'body', None, ['owner@test.local'])
        FlakyTransport.failures = 1
        self.assertEqual(email_outbox.run_due()['retrying'], 1)
        row = EmailOutbox.objects.get()
        self.assertEqual(row.attempts, 1)
        self.assertGreater(row.next_attempt_at, timezone.now() + timedelta(seconds=50))

    def test_if_storing_fails_it_is_sent_directly(self):
        with mock.patch.object(EmailOutbox.objects, 'create', side_effect=RuntimeError('db down')):
            self.assertEqual(send_mail('Approved', 'body', None, ['owner@test.local']), 1)
        self.assertEqual(len(FlakyTransport.sent), 1)

    def test_not_in_a_server_process_it_sends_directly(self):
        with mock.patch.object(email_outbox, '_started', False):
            send_mail('Approved', 'body', None, ['owner@test.local'])
        self.assertEqual(len(FlakyTransport.sent), 1)
        self.assertFalse(EmailOutbox.objects.exists())


@override_settings(BREVO_API_KEY='xkeysib-test', EMAIL_TIMEOUT=1)
class SlowNetworkTests(TestCase):
    def _send(self, side_effect):
        backend = BrevoEmailBackend()
        msg = EmailMultiAlternatives('s', 'b', 'cdso@test.local', ['o@test.local'])
        with mock.patch.object(requests.Session, 'post', side_effect=side_effect) as post, \
                mock.patch('config.email_backends.time.sleep'):
            try:
                backend.send_messages([msg])
            finally:
                self.calls = post.call_count
                self.timeout = post.call_args.kwargs['timeout']

    def test_a_lost_answer_is_not_resent_on_the_spot(self):
        """The request reached Brevo, which usually means the mail went out.
        Resending at once delivered up to four copies of one email."""
        with self.assertRaises(requests.ReadTimeout):
            self._send(requests.ReadTimeout('read timed out'))
        self.assertEqual(self.calls, 1)

    def test_a_connection_that_never_opened_is_still_retried(self):
        with self.assertRaises(requests.ConnectionError):
            self._send(requests.ConnectionError('connection refused'))
        self.assertEqual(self.calls, 4)

    def test_the_answer_gets_longer_to_arrive_than_the_connection(self):
        with self.assertRaises(requests.ConnectionError):
            self._send(requests.ConnectionError('x'))
        self.assertEqual(self.timeout, (1, 30))


@override_settings(BREVO_API_KEY='xkeysib-test', EMAIL_TIMEOUT=1)
class BrevoErrorClassTests(TestCase):
    def _send_with_status(self, status):
        resp = mock.Mock(status_code=status, headers={})
        resp.json.return_value = {'message': 'nope'}
        backend = BrevoEmailBackend()
        msg = EmailMultiAlternatives('s', 'b', 'cdso@test.local', ['o@test.local'])
        with mock.patch.object(requests.Session, 'post', return_value=resp):
            backend.send_messages([msg])

    def test_a_bad_request_is_permanent(self):
        with self.assertRaises(PermanentEmailError):
            self._send_with_status(400)

    def test_an_auth_failure_is_retryable(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._send_with_status(401)
        self.assertFalse(email_outbox.is_permanent(ctx.exception))
