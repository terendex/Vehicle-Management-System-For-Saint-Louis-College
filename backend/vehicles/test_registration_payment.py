"""Proof of payment, and the two axes it made necessary.

Acceptance has always required an Official Receipt number, which meant the fee
was recorded at the moment of approval and nowhere before it — a pending row
could not say whether the applicant had paid and was waiting, or had not paid at
all. `payment_status` splits that out, and these tests pin the boundaries:

  * an applicant records their own payment through the token in their pending
    email, and cannot record anything else about their application;
  * the token is unguessable, single-purpose, and dies with the pending status;
  * a fee-exempt applicant is never asked for a receipt they were never issued;
  * a pass can still be granted against an unsettled fee, but only with a stated
    reason, and the row keeps saying `unpaid` afterwards so the debt survives.
"""
from datetime import timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from vehicles.models import RegistrationPeriod, SystemSettings, VehicleRegistration

LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'

PS = VehicleRegistration.PaymentStatus


def receipt_file(name='receipt.jpg', size=64):
    return SimpleUploadedFile(name, b'x' * size, content_type='image/jpeg')


@override_settings(EMAIL_BACKEND=LOCMEM, DEFAULT_FROM_EMAIL='slccdso@gmail.com',
                   PUBLIC_SITE_URL='https://slc.example.edu')
class PaymentTestCase(TestCase):
    """Shared fixture: an open window, an admin, and unique-payload helpers."""

    def setUp(self):
        self.client = APIClient()
        self._n = 0
        today = timezone.localdate()
        RegistrationPeriod.objects.create(
            label='Payment tests', is_active=True,
            start_date=today - timedelta(days=1), end_date=today + timedelta(days=1))
        self.admin = User.objects.create_user(
            email='payadmin@slc.edu.ph', full_name='Pay Admin',
            password='pw', role='admin', is_staff=True, is_superuser=True)

    def submit(self, **over):
        self._n += 1
        i = self._n
        data = dict(
            registrant_type='student', full_name='PAYER, TESTER',
            email=f'payer{i}@slc.edu.ph', plate_number=f'PAY {i:04d}',
            vehicle_type='car', contact_number='+639171234567',
            address='San Fernando, La Union',
            drivers_license=f'N01-20-90{i:04d}', student_id=f'2028{i:04d}',
            program_year='BSIT 1', campus_days=['Monday'], student_level='college',
        )
        data.update(over)
        res = self.client.post('/api/vehicles/register/open/', data, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        return VehicleRegistration.objects.get(pk=res.data['id'])

    def submit_employee(self, department, **over):
        self._n += 1
        i = self._n
        data = dict(
            registrant_type='employee', full_name='STAFFER, TESTER',
            email=f'staff{i}@slc.edu.ph', plate_number=f'EMP {i:04d}',
            vehicle_type='car', contact_number='+639171234567',
            address='San Fernando, La Union',
            drivers_license=f'N01-20-95{i:04d}', employee_id=f'3028{i:04d}',
            department=department,
        )
        data.update(over)
        res = self.client.post('/api/vehicles/register/open/', data, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        return VehicleRegistration.objects.get(pk=res.data['id'])

    def pay(self, reg, or_number='1380093', **over):
        # TEMPORARY (DPO trial): the OR number alone, sent as JSON — the receipt
        # image is not collected any more. See pay_with_file below for what a
        # browser still running the previous bundle does.
        payload = {'token': str(reg.payment_token), 'or_number': or_number}
        payload.update(over)
        return self.client.post('/api/vehicles/register/payment/', payload,
                                format='json')

    def pay_with_file(self, reg, or_number='1380093', receipt=None):
        """What a stale bundle still posts: multipart, with a receipt attached."""
        return self.client.post('/api/vehicles/register/payment/', {
            'token': str(reg.payment_token), 'or_number': or_number,
            'receipt': receipt if receipt is not None else receipt_file(),
        }, format='multipart')

    def accept(self, reg, **body):
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(f'/api/vehicles/registrations/{reg.pk}/accept/',
                               body, format='json')
        self.client.force_authenticate(user=None)
        return res


class SubmissionStartsUnpaidTests(PaymentTestCase):

    def test_a_new_application_owes_the_fee(self):
        reg = self.submit()
        self.assertEqual(reg.payment_status, PS.UNPAID)
        self.assertIsNone(reg.paid_at)
        self.assertIsNone(reg.amount_paid)

    def test_every_application_gets_an_upload_token(self):
        a, b = self.submit(), self.submit()
        self.assertIsNotNone(a.payment_token)
        self.assertNotEqual(a.payment_token, b.payment_token,
                            'tokens must be unguessable per application')

    def test_a_submission_cannot_declare_itself_paid(self):
        """The whole point of the Accounting Office step."""
        reg = self.submit(payment_status=PS.PAID, amount_paid='300.00',
                          or_number='9999999')
        self.assertEqual(reg.payment_status, PS.UNPAID)
        self.assertIsNone(reg.amount_paid)
        self.assertEqual(reg.or_number, '')

    def test_a_fee_exempt_department_is_flagged_on_submission(self):
        reg = self.submit_employee('Cleaning and Services')
        self.assertEqual(reg.payment_status, PS.EXEMPT)
        self.assertEqual(reg.amount_paid, Decimal('0.00'))

    def test_a_paying_department_is_not_flagged_exempt(self):
        reg = self.submit_employee('Teaching')
        self.assertEqual(reg.payment_status, PS.UNPAID)


class ReceiptUploadTests(PaymentTestCase):

    def test_the_link_describes_what_is_owed(self):
        reg = self.submit()
        res = self.client.get('/api/vehicles/register/payment/',
                              {'token': str(reg.payment_token)})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['full_name'], reg.full_name)
        self.assertEqual(res.data['payment_status'], PS.UNPAID)
        self.assertEqual(Decimal(res.data['amount_due']),
                         SystemSettings.get().vehicle_pass_fee)

    def test_filing_the_or_number_settles_the_application(self):
        reg = self.submit()
        res = self.pay(reg, or_number='1380093')
        self.assertEqual(res.status_code, 200, res.data)

        reg.refresh_from_db()
        self.assertEqual(reg.payment_status, PS.PAID)
        self.assertEqual(reg.or_number, '1380093')
        self.assertIsNotNone(reg.paid_at)
        self.assertEqual(reg.amount_paid, SystemSettings.get().vehicle_pass_fee)
        # TEMPORARY (DPO trial): no image is kept — the CDSO checks the paper
        # receipt at the counter instead.
        self.assertFalse(reg.or_receipt_image)

    def test_the_amount_is_snapshotted_not_looked_up(self):
        """A later fee change must not rewrite what this applicant paid."""
        reg = self.submit()
        original = SystemSettings.get().vehicle_pass_fee
        self.assertEqual(self.pay(reg).status_code, 200)

        settings_obj = SystemSettings.get()
        settings_obj.vehicle_pass_fee = original + Decimal('200.00')
        settings_obj.save(update_fields=['vehicle_pass_fee'])

        reg.refresh_from_db()
        self.assertEqual(reg.amount_paid, original,
                         'raising the fee retroactively rewrote a past payment')

    def test_a_receipt_photo_is_no_longer_required(self):
        """TEMPORARY — Data Privacy Office trial. The number is the whole step."""
        reg = self.submit()
        res = self.client.post('/api/vehicles/register/payment/',
                               {'token': str(reg.payment_token), 'or_number': '1380093'},
                               format='multipart')
        self.assertEqual(res.status_code, 200, res.data)
        reg.refresh_from_db()
        self.assertEqual(reg.payment_status, PS.PAID)
        self.assertFalse(reg.or_receipt_image)

    def test_an_or_number_is_required(self):
        reg = self.submit()
        res = self.pay(reg, or_number='')
        self.assertEqual(res.status_code, 400)
        reg.refresh_from_db()
        self.assertEqual(reg.payment_status, PS.UNPAID)

    def test_the_or_number_keeps_its_shape(self):
        for bad in ('ABC1234', '12345678', '138-0093'):
            with self.subTest(or_number=bad):
                reg = self.submit()
                self.assertEqual(self.pay(reg, or_number=bad).status_code, 400)

    def test_an_attached_file_is_ignored_rather_than_stored(self):
        """TEMPORARY — Data Privacy Office trial.

        A browser still running the previous bundle posts multipart with a file.
        The payment must still go through — the applicant did pay — but nothing
        of the file may reach storage, whatever it turns out to be.
        """
        for upload in (receipt_file(),
                       SimpleUploadedFile('receipt.exe', b'MZ',
                                          content_type='application/octet-stream')):
            with self.subTest(name=upload.name):
                reg = self.submit()
                res = self.pay_with_file(reg, receipt=upload)
                self.assertEqual(res.status_code, 200, res.data)
                reg.refresh_from_db()
                self.assertEqual(reg.payment_status, PS.PAID)
                self.assertFalse(reg.or_receipt_image)

    def test_a_receipt_can_be_replaced_while_pending(self):
        """A mistyped first number must not lock the applicant out."""
        reg = self.submit()
        self.assertEqual(self.pay(reg, or_number='1111111').status_code, 200)
        self.assertEqual(self.pay(reg, or_number='2222222').status_code, 200)
        reg.refresh_from_db()
        self.assertEqual(reg.or_number, '2222222')

    def test_the_link_reports_exemption_so_the_page_can_say_so(self):
        """The upload page branches on this rather than showing a zero form."""
        reg = self.submit_employee('Cleaning and Services')
        res = self.client.get('/api/vehicles/register/payment/',
                              {'token': str(reg.payment_token)})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['payment_status'], PS.EXEMPT)
        self.assertEqual(Decimal(res.data['amount_due']), Decimal('0.00'))

    def test_an_exempt_applicant_is_told_not_to_pay(self):
        reg = self.submit_employee('Cleaning and Services')
        res = self.pay(reg)
        self.assertEqual(res.status_code, 400)
        self.assertIn('exempt', res.data['error'].lower())


class TokenIsTheOnlyKeyTests(PaymentTestCase):

    def test_a_made_up_token_is_rejected(self):
        self.submit()
        for bogus in ('', 'not-a-uuid', '00000000-0000-0000-0000-000000000000'):
            with self.subTest(token=bogus):
                res = self.client.get('/api/vehicles/register/payment/', {'token': bogus})
                self.assertEqual(res.status_code, 404)

    def test_the_token_dies_with_the_pending_status(self):
        """Once reviewed, the receipt on file is part of the decision."""
        reg = self.submit()
        self.assertEqual(self.pay(reg).status_code, 200)
        self.assertEqual(self.accept(reg, or_number='1380093').status_code, 200)

        self.assertEqual(self.pay(reg, or_number='7777777').status_code, 404)
        reg.refresh_from_db()
        self.assertEqual(reg.or_number, '1380093')

    def test_the_token_never_leaves_through_the_registration_payload(self):
        """Anyone who could read it could file a receipt against a stranger."""
        reg = self.submit()
        self.client.force_authenticate(user=self.admin)
        res = self.client.get('/api/vehicles/registrations/pending/?status=pending')
        self.client.force_authenticate(user=None)

        self.assertEqual(res.status_code, 200)
        row = next(r for r in res.data if r['id'] == reg.pk)
        self.assertNotIn('payment_token', row)


class AcceptancePaymentGateTests(PaymentTestCase):

    def test_an_uploaded_receipt_carries_the_acceptance(self):
        """CDSO verifies the image; there is no number left to re-key."""
        reg = self.submit()
        self.assertEqual(self.pay(reg, or_number='1380093').status_code, 200)

        res = self.accept(reg)
        self.assertEqual(res.status_code, 200, res.data)
        reg.refresh_from_db()
        self.assertEqual(reg.status, VehicleRegistration.Status.ACCEPTED)
        self.assertEqual(reg.payment_status, PS.PAID)
        self.assertEqual(reg.or_number, '1380093')

    def test_a_reviewer_can_correct_a_mistyped_or_number(self):
        reg = self.submit()
        self.assertEqual(self.pay(reg, or_number='1380093').status_code, 200)

        self.assertEqual(self.accept(reg, or_number='1380094').status_code, 200)
        reg.refresh_from_db()
        self.assertEqual(reg.or_number, '1380094')

    def test_a_counter_receipt_still_counts_as_payment(self):
        """Somebody who brought the paper instead of uploading it."""
        reg = self.submit()
        self.assertEqual(self.accept(reg, or_number='1380093').status_code, 200)

        reg.refresh_from_db()
        self.assertEqual(reg.payment_status, PS.PAID)
        self.assertIsNotNone(reg.paid_at)
        self.assertEqual(reg.amount_paid, SystemSettings.get().vehicle_pass_fee)

    def test_approving_with_no_receipt_needs_a_reason(self):
        reg = self.submit()
        res = self.accept(reg)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['error'], 'unpaid_acceptance_requires_reason')

        reg.refresh_from_db()
        self.assertEqual(reg.status, VehicleRegistration.Status.PENDING)

    def test_an_unpaid_approval_keeps_saying_unpaid(self):
        """The debt has to outlive the approval, or nobody chases it."""
        reg = self.submit()
        res = self.accept(reg, unpaid_accept_reason='Accounting system down; OR to follow.')
        self.assertEqual(res.status_code, 200, res.data)

        reg.refresh_from_db()
        self.assertEqual(reg.status, VehicleRegistration.Status.ACCEPTED)
        self.assertEqual(reg.payment_status, PS.UNPAID)
        self.assertEqual(reg.unpaid_accept_reason,
                         'Accounting system down; OR to follow.')
        self.assertIsNone(reg.paid_at)

    def test_an_exempt_applicant_is_never_asked_for_a_receipt(self):
        """Requiring one forced CDSO to invent an OR number to approve them."""
        reg = self.submit_employee('Cleaning and Services')
        res = self.accept(reg)
        self.assertEqual(res.status_code, 200, res.data)

        reg.refresh_from_db()
        self.assertEqual(reg.status, VehicleRegistration.Status.ACCEPTED)
        self.assertEqual(reg.payment_status, PS.EXEMPT)
        self.assertEqual(reg.or_number, '')


class PendingEmailTests(PaymentTestCase):

    def test_the_pending_email_carries_the_upload_link(self):
        from django.core import mail
        reg = self.submit()
        body = mail.outbox[-1].alternatives[0][0]
        self.assertIn(f'/registration/payment?token={reg.payment_token}', body)

    def test_an_exempt_applicant_is_sent_no_payment_link(self):
        from django.core import mail
        self.submit_employee('Cleaning and Services')
        body = mail.outbox[-1].alternatives[0][0]
        self.assertNotIn('/registration/payment?token=', body)
        self.assertIn('No Payment Required', body)


class WalkInPaymentTests(PaymentTestCase):
    """The CDSO counter path, which never went through the Accounting Office link.

    It demanded an Official Receipt number before it had even looked at the
    department, so registering an exempt walk-in meant inventing one.
    """

    def walk_in(self, **over):
        self._n += 1
        i = self._n
        data = dict(
            registrant_type='employee', full_name='WALKIN, TESTER',
            email=f'walkin{i}@slc.edu.ph', plate_number=f'WLK {i:04d}',
            vehicle_type='car', contact_number='+639171234567',
            address='San Fernando, La Union',
            drivers_license=f'N01-20-97{i:04d}', employee_id=f'5028{i:04d}',
        )
        data.update(over)
        self.client.force_authenticate(user=self.admin)
        res = self.client.post('/api/vehicles/register/direct/', data, format='json')
        self.client.force_authenticate(user=None)
        return res

    def test_an_exempt_walk_in_needs_no_receipt_number(self):
        res = self.walk_in(department='Cleaning and Services')
        self.assertEqual(res.status_code, 201, res.data)

        reg = VehicleRegistration.objects.get(email__startswith='walkin')
        self.assertEqual(reg.payment_status, PS.EXEMPT)
        self.assertEqual(reg.or_number, '')
        self.assertEqual(reg.amount_paid, Decimal('0.00'))
        self.assertIsNone(reg.paid_at)

    def test_a_paying_walk_in_still_needs_one(self):
        res = self.walk_in(department='Teaching')
        self.assertEqual(res.status_code, 400)
        self.assertIn('Official Receipt', res.data['error'])

    def test_a_paying_walk_in_is_recorded_as_paid(self):
        res = self.walk_in(department='Teaching', or_number='1380093')
        self.assertEqual(res.status_code, 201, res.data)

        reg = VehicleRegistration.objects.get(or_number='1380093')
        self.assertEqual(reg.payment_status, PS.PAID)
        self.assertIsNotNone(reg.paid_at)
        self.assertEqual(reg.amount_paid, SystemSettings.get().vehicle_pass_fee_employee)

    def test_the_walk_in_path_records_the_department(self):
        """It never mapped the label, so the exemption could never have fired."""
        self.assertEqual(self.walk_in(department='Cleaning and Services').status_code, 201)
        reg = VehicleRegistration.objects.get(email__startswith='walkin')
        self.assertEqual(reg.department_type, 'cleaning_services')


class FeeRuleTests(PaymentTestCase):
    """fee_for/is_fee_exempt answer without a row, and agree with pass_fee."""

    def test_exemption_is_employee_only(self):
        VR = VehicleRegistration
        self.assertTrue(VR.is_fee_exempt('employee', 'cleaning_services'))
        self.assertFalse(VR.is_fee_exempt('employee', 'teaching'))
        self.assertFalse(VR.is_fee_exempt('employee', ''))
        # A student or fetcher has no department, and never an exemption.
        self.assertFalse(VR.is_fee_exempt('student', 'cleaning_services'))
        self.assertFalse(VR.is_fee_exempt('fetcher', 'cleaning_services'))

    def test_the_row_free_figure_matches_the_row_one(self):
        settings_obj = SystemSettings.get()
        cases = [
            ('student',  '',                  settings_obj.vehicle_pass_fee),
            ('fetcher',  '',                  settings_obj.vehicle_pass_fee),
            ('employee', 'teaching',          settings_obj.vehicle_pass_fee_employee),
            ('employee', 'cleaning_services', Decimal('0.00')),
        ]
        for registrant_type, dept, expected in cases:
            with self.subTest(registrant_type=registrant_type, department=dept):
                self.assertEqual(
                    VehicleRegistration.fee_for(registrant_type, dept), expected)
                row = VehicleRegistration(registrant_type=registrant_type,
                                          department_type=dept)
                self.assertEqual(row.pass_fee(), expected)
