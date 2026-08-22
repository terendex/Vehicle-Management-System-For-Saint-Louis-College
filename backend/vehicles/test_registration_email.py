"""What the registration emails must contain, and what must never reach them.

The templates in vehicles/email_utils.py are f-strings, not Django templates,
so nothing auto-escapes and nothing warns when a field silently renders blank.
Both failures are invisible in production — the mail still sends — so they are
pinned here instead:

  * applicant-supplied text is HTML-escaped in the HTML part,
  * the plain-text part keeps the *raw* temporary password (escaping it would
    hand the owner a password they cannot log in with),
  * an employee's department appears whichever way it was recorded, and
  * a mail failure is logged and reported rather than swallowed.
"""
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from vehicles.email_utils import (department_label, esc, esc_or_dash,
                                  send_acceptance_email, send_pending_email,
                                  send_rejection_email)
from vehicles.models import (ReferenceItem, RegistrationPeriod,
                             VehicleRegistration)

LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'
# Nothing listens on port 1; the connect fails fast instead of reaching Gmail.
DEAD_SMTP = dict(EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
                 EMAIL_HOST='127.0.0.1', EMAIL_PORT=1, EMAIL_TIMEOUT=2)

BASE = dict(
    registrant_type='student',
    full_name='DELA CRUZ, JUAN',
    email='juan@slc.edu.ph',
    plate_number='EML 1001',
    vehicle_type='car',
    vehicle_color='Red',
    contact_number='+639171234567',
    address='San Fernando, La Union',
    drivers_license='N01-20-123456',
    student_id='20250001',
    program_year='BSIT 3',
    campus_days=['Monday', 'Wednesday'],
    schedule='MWF',
)


def make_reg(**overrides):
    data = dict(BASE)
    data.update(overrides)
    return VehicleRegistration.objects.create(**data)


def html_of(message):
    """The text/html alternative attached to a sent message."""
    return message.alternatives[0][0]


@override_settings(EMAIL_BACKEND=LOCMEM, DEFAULT_FROM_EMAIL='slccdso@gmail.com')
class EmailEscapingTests(TestCase):
    """Applicant-controlled text must not reach the HTML body as live markup."""

    HOSTILE = 'O<script>alert(1)</script>Brien & Sons'

    def test_esc_escapes_the_dangerous_characters(self):
        self.assertEqual(esc('<b>&"'), '&lt;b&gt;&amp;&quot;')

    def test_esc_or_dash_renders_a_dash_for_blanks(self):
        self.assertEqual(esc_or_dash(''), '—')
        self.assertEqual(esc_or_dash(None), '—')
        self.assertEqual(esc_or_dash('x'), 'x')

    def test_pending_email_escapes_the_applicant_name(self):
        send_pending_email(make_reg(full_name=self.HOSTILE))
        body = html_of(mail.outbox[-1])
        self.assertNotIn('<script>', body)
        self.assertIn('&lt;script&gt;', body)

    def test_acceptance_email_escapes_the_applicant_name(self):
        send_acceptance_email(make_reg(full_name=self.HOSTILE), 'TempPass1!', 'SLC-VO-000001')
        body = html_of(mail.outbox[-1])
        self.assertNotIn('<script>', body)
        self.assertIn('&lt;script&gt;', body)

    def test_rejection_email_escapes_the_cdso_reason(self):
        send_rejection_email(make_reg(), '<img src=x onerror=alert(1)>')
        body = html_of(mail.outbox[-1])
        self.assertNotIn('<img src=x', body)
        self.assertIn('&lt;img src=x', body)

    def test_authorized_driver_row_is_escaped(self):
        send_pending_email(make_reg(student_level='jhs', driver_name=self.HOSTILE,
                                    driver_relationship='parent',
                                    driver_contact='<b>0917</b>'))
        body = html_of(mail.outbox[-1])
        self.assertNotIn('<script>', body)
        self.assertNotIn('<b>0917</b>', body)


@override_settings(EMAIL_BACKEND=LOCMEM, DEFAULT_FROM_EMAIL='slccdso@gmail.com')
class TemporaryPasswordTests(TestCase):
    """The plain-text part must carry the password byte-for-byte."""

    # _generate_temp_password draws from '!@#$%^&*()_+-=', so '&' is reachable.
    PASSWORD = 'Ab1&x<y>"z!'

    def test_plain_text_body_keeps_the_password_unescaped(self):
        send_acceptance_email(make_reg(), self.PASSWORD, 'SLC-VO-000001')
        self.assertIn(f'Temporary Password: {self.PASSWORD}', mail.outbox[-1].body)

    def test_html_body_escapes_the_password(self):
        send_acceptance_email(make_reg(), self.PASSWORD, 'SLC-VO-000001')
        body = html_of(mail.outbox[-1])
        self.assertIn('Ab1&amp;x&lt;y&gt;&quot;z!', body)

    def test_plain_text_ids_are_not_escaped(self):
        send_acceptance_email(make_reg(system_student_id='SLC-STU-000042'),
                              'TempPass1!', 'SLC-VO-000001')
        text = mail.outbox[-1].body
        self.assertIn('Portal Account ID: SLC-VO-000001', text)
        self.assertIn('System Registration ID: SLC-STU-000042', text)


@override_settings(EMAIL_BACKEND=LOCMEM, DEFAULT_FROM_EMAIL='slccdso@gmail.com')
class DepartmentLabelTests(TestCase):
    """An employee's department must appear however it was recorded.

    The public form stores department_type and leaves the FK null, so reading
    only the FK mailed every online employee registrant "Department: —".
    """

    def _employee(self, **kw):
        return make_reg(registrant_type='employee', student_id='', employee_id='E-1001',
                        program_year='', campus_days=[], schedule='ANY', **kw)

    def test_label_prefers_the_reference_fk(self):
        dept = ReferenceItem.objects.create(category='department', name='Registrar')
        self.assertEqual(department_label(self._employee(department=dept)), 'Registrar')

    def test_label_falls_back_to_department_type(self):
        reg = self._employee(department_type='teaching')
        self.assertEqual(department_label(reg), 'Teaching')

    def test_label_is_blank_when_neither_is_set(self):
        self.assertEqual(department_label(self._employee()), '')

    def test_pending_email_shows_the_public_form_department(self):
        send_pending_email(self._employee(department_type='cleaning_services'))
        self.assertIn('Cleaning and Services', html_of(mail.outbox[-1]))

    def test_acceptance_email_shows_the_public_form_department(self):
        send_acceptance_email(self._employee(department_type='non_teaching'),
                              'TempPass1!', 'SLC-VO-000001')
        self.assertIn('Non-Teaching', html_of(mail.outbox[-1]))


@override_settings(EMAIL_BACKEND=LOCMEM, DEFAULT_FROM_EMAIL='slccdso@gmail.com')
class AcceptanceEmailContentTests(TestCase):
    # MEDIA_URL is overridden in both directions below rather than left to the
    # ambient .env: USE_R2 decides it, so without pinning it these tests assert
    # opposite things depending on whose machine runs them — and the remote case
    # would upload a test QR into the real bucket.

    @override_settings(MEDIA_URL='/media/')
    def test_carries_the_qr_code_and_the_registration_pdf(self):
        # Paid: the confirmation PDF rides with the settled fee, not with the
        # approval — test_receipt_email pins the unpaid half of that rule.
        send_acceptance_email(make_reg(payment_status='paid', or_number='1234567'),
                              'TempPass1!', 'SLC-VO-000001')
        msg = mail.outbox[-1]
        # Local storage has no absolute URL to link, so the inline copy falls
        # back to a data URI — see the remote-storage test below for production.
        self.assertIn('data:image/png;base64,', html_of(msg))
        self.assertTrue(
            any(str(name).endswith('.pdf') for name, _content, _type in msg.attachments),
            f"the approval email lost its registration PDF: {msg.attachments!r}")

    @override_settings(MEDIA_URL='/media/')
    def test_the_qr_is_always_attached_as_a_file(self):
        """The inline copy cannot be relied on — Gmail strips data: URIs and
        every client blocks remote images by default — so the owner's gate pass
        must also arrive as an attachment they can open and save."""
        send_acceptance_email(make_reg(), 'TempPass1!', 'SLC-VO-000001')
        msg = mail.outbox[-1]
        png = [(n, c) for n, c, t in msg.attachments if str(n).endswith('.png')]
        self.assertEqual(len(png), 1, f"expected one QR attachment: {msg.attachments!r}")
        self.assertTrue(png[0][1].startswith(b'\x89PNG'),
                        "the QR attachment is not a PNG")

    @override_settings(MEDIA_URL='https://cdn.example.test/')
    def test_qr_is_linked_by_url_when_media_storage_is_remote(self):
        """With USE_R2 on — which production requires — the QR must be linked
        rather than embedded. A data: URI does not render in Gmail at all, and
        cid: cannot work on the Railway half because Brevo's API carries no
        Content-ID. Storage is faked so the test never writes to the bucket."""
        storage = mock.MagicMock()
        storage.exists.return_value = False
        storage.save.side_effect = lambda name, _content: name
        storage.url.side_effect = lambda name: f'https://cdn.example.test/{name}'
        with mock.patch('django.core.files.storage.default_storage', storage):
            send_acceptance_email(make_reg(), 'TempPass1!', 'SLC-VO-000001')

        html = html_of(mail.outbox[-1])
        self.assertIn('https://cdn.example.test/qr_codes/registration-', html)
        self.assertNotIn('data:image/png;base64,', html)
        # Still attached, so a client that blocks remote images has a copy.
        self.assertTrue(any(str(n).endswith('.png') for n, _c, _t
                            in mail.outbox[-1].attachments))

    @override_settings(MEDIA_URL='https://cdn.example.test/')
    def test_a_storage_failure_still_sends_the_email_with_the_qr(self):
        """Uploading the QR is best-effort. If the bucket is unreachable the
        owner must still get their credentials, with the QR inline and attached."""
        storage = mock.MagicMock()
        storage.exists.side_effect = OSError('bucket unreachable')
        with mock.patch('django.core.files.storage.default_storage', storage):
            with self.assertLogs('vehicles.email_utils', level='ERROR'):
                send_acceptance_email(make_reg(), 'TempPass1!', 'SLC-VO-000001')

        msg = mail.outbox[-1]
        self.assertIn('data:image/png;base64,', html_of(msg))
        self.assertTrue(any(str(n).endswith('.png') for n, _c, _t in msg.attachments))

    @override_settings(MEDIA_URL='/media/')
    def test_a_pdf_failure_still_sends_the_email(self):
        """The PDF is best-effort — losing it must not cost the owner their
        credentials (nor, upstream, roll back the approval)."""
        import registration_pdf
        broken = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('no reportlab'))
        original = registration_pdf.registration_confirmation_pdf
        registration_pdf.registration_confirmation_pdf = broken
        try:
            with self.assertLogs('vehicles.email_utils', level='ERROR'):
                # Paid, so a PDF is actually attempted — an unpaid
                # application has none to lose.
                send_acceptance_email(make_reg(payment_status='paid', or_number='1234567'),
                                      'TempPass1!', 'SLC-VO-000001')
        finally:
            registration_pdf.registration_confirmation_pdf = original
        msg = mail.outbox[-1]
        # The PDF is the part that must be absent; the QR still rides along,
        # since it is generated here and does not depend on reportlab.
        self.assertFalse(any(str(n).endswith('.pdf') for n, _c, _t in msg.attachments),
                         f"the broken PDF was attached anyway: {msg.attachments!r}")
        self.assertTrue(any(str(n).endswith('.png') for n, _c, _t in msg.attachments),
                        "a PDF failure should not cost the owner their QR too")
        self.assertIn('TempPass1!', msg.body)


@override_settings(EMAIL_BACKEND=LOCMEM, DEFAULT_FROM_EMAIL='slccdso@gmail.com')
class MailFailureIsReportedTests(TestCase):
    """A dead mail server must never look like a healthy one."""

    def setUp(self):
        self.client = APIClient()
        today = timezone.localdate()
        RegistrationPeriod.objects.create(
            label='Email tests', is_active=True,
            start_date=today - timedelta(days=1), end_date=today + timedelta(days=1))
        self.admin = User.objects.create_user(
            email='mailadmin@slc.edu.ph', full_name='Mail Admin',
            password='pw', role='admin', is_staff=True, is_superuser=True)

    def _submit(self, **over):
        payload = dict(BASE, student_level='college')
        payload.update(over)
        return self.client.post('/api/vehicles/register/open/', payload, format='json')

    def test_submission_reports_a_sent_email(self):
        res = self._submit()
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['email_status'], 'sent')

    def test_submission_survives_a_dead_mail_server_but_says_so(self):
        with override_settings(**DEAD_SMTP):
            with self.assertLogs('vehicles.views', level='ERROR') as logs:
                res = self._submit()
        self.assertEqual(res.status_code, 201, 'the submission itself must still be saved')
        self.assertEqual(res.data['email_status'], 'failed')
        self.assertTrue(VehicleRegistration.objects.filter(pk=res.data['id']).exists())
        self.assertIn('pending-registration email', '\n'.join(logs.output))

    def test_rejection_reports_a_dead_mail_server(self):
        reg_id = self._submit().data['id']
        self.client.force_authenticate(user=self.admin)
        with override_settings(**DEAD_SMTP):
            with self.assertLogs('vehicles.views', level='ERROR'):
                res = self.client.post(f'/api/vehicles/registrations/{reg_id}/reject/',
                                       {'reason': 'Incomplete'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['email_status'], 'failed')
        self.assertEqual(VehicleRegistration.objects.get(pk=reg_id).status, 'rejected')

    def test_acceptance_reports_a_dead_mail_server_and_keeps_the_account(self):
        """The acceptance mail moved off the request path, so a dead server no
        longer reaches the reviewer through the response. It has to reach them
        somewhere, or an owner silently never gets their credentials — the
        admin bell is that somewhere."""
        from accounts.models import Notification

        reg_id = self._submit().data['id']
        self.client.force_authenticate(user=self.admin)
        with override_settings(**DEAD_SMTP):
            with self.assertLogs('vehicles.email_utils', level='ERROR'):
                res = self.client.post(f'/api/vehicles/registrations/{reg_id}/accept/',
                                       {'or_number': '1234567'}, format='json')
        self.assertEqual(res.status_code, 200)
        # The send is queued, not awaited — the reviewer is not made to wait on
        # a mail server for an outcome already committed.
        self.assertEqual(res.data['email_status'], 'queued')

        reg = VehicleRegistration.objects.get(pk=reg_id)
        self.assertEqual(reg.status, 'accepted')
        self.assertIsNotNone(reg.user, 'the account must survive a mail failure')

        notice = Notification.objects.filter(event='acceptance_email_failed').first()
        self.assertIsNotNone(
            notice, 'a failed credentials email must still reach the CDSO somehow')
        self.assertIn(reg.email, notice.message)
        self.assertEqual(notice.severity, 'warning')

    def test_a_successful_acceptance_raises_no_failure_notice(self):
        from accounts.models import Notification

        reg_id = self._submit().data['id']
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(f'/api/vehicles/registrations/{reg_id}/accept/',
                               {'or_number': '1234567'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['email_status'], 'queued')
        self.assertFalse(
            Notification.objects.filter(event='acceptance_email_failed').exists(),
            'a delivered email must not raise a failure notice')

    def test_the_background_sender_really_runs_on_a_thread(self):
        """The inline mode the rest of the suite runs in must not be the only
        path that works — this exercises the threaded one and joins it."""
        from django.core import mail
        from vehicles.email_utils import send_in_background

        seen = {}

        def _send(marker):
            seen['marker'] = marker
            mail.send_mail('subject', 'body', 'from@slc.edu.ph', ['to@slc.edu.ph'])

        with override_settings(EMAIL_SEND_ASYNC=True):
            thread = send_in_background(_send, 'ran')
        self.assertIsNotNone(thread, 'async mode must hand back a thread')
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive(), 'the send thread did not finish')
        self.assertEqual(seen.get('marker'), 'ran')

    def test_a_threaded_failure_still_reaches_on_failure(self):
        from vehicles.email_utils import send_in_background

        called = {}

        def _boom():
            raise RuntimeError('mail server down')

        with override_settings(EMAIL_SEND_ASYNC=True):
            with self.assertLogs('vehicles.email_utils', level='ERROR'):
                thread = send_in_background(
                    _boom, on_failure=lambda: called.setdefault('notified', True))
                thread.join(timeout=10)
        self.assertTrue(called.get('notified'),
                        'a failure on the thread must still raise its notice')

    def test_password_reset_logs_instead_of_failing_silently(self):
        User.objects.create_user(email='owner@slc.edu.ph', full_name='Owner',
                                 password='pw', role='vehicle_owner')
        with override_settings(**DEAD_SMTP):
            with self.assertLogs('accounts.views', level='ERROR') as logs:
                res = APIClient().post('/api/accounts/password-reset/request/',
                                       {'email': 'owner@slc.edu.ph'}, format='json')
        # The caller still gets the neutral message — it must not leak whether
        # the address exists — but the failure is now on the record.
        self.assertEqual(res.status_code, 200)
        self.assertIn('password reset link has been sent', res.data['message'])
        self.assertIn('password-reset email', '\n'.join(logs.output))
