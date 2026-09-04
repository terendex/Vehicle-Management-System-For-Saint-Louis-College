"""The whole registration flow, driven by the payload the trimmed form sends.

TEMPORARY — Data Privacy Office trial. Every other registration test builds its
payload by hand, and most of them still include an address, a contact number and
a student ID, because they were written before the trial and the view now simply
discards those. That leaves a gap nothing covered: the joined-up path — submit,
acknowledgement email, receipt number, CDSO approval, credentials email — walked
with the exact set of keys `RegisterPage.jsx` now produces, and nothing else.

The payload dicts below are the answer to "does registration still work". They
are transcribed from the form's own `handleSubmit`, after it deletes `who_drives`,
`details_confirmed` and `vehicle_color_choice` — so if the form and the backend
drift apart, this is what notices.
"""
from datetime import timedelta
from decimal import Decimal

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from vehicles.models import (RegistrationPeriod, SystemSettings, Vehicle,
                             VehicleRegistration)

LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'

# The withheld six. Kept as one list so the assertions below read as "none of
# these, anywhere" rather than six separate spellings of the same rule.
WITHHELD = ('address', 'contact_number', 'age',
            'student_id', 'employee_id', 'driver_contact')


def student_payload(**over):
    """What the form posts for a College student who drives themselves."""
    data = dict(
        # Name block
        last_name='DELA CRUZ', first_name='JUAN', middle_name='SANTOS',
        full_name='DELA CRUZ, JUAN, SANTOS',
        email='12345678@slc-sflu.edu.ph',
        # Student block — the four *_student fields are form-only and stripped
        # by the view; program_year is what the form composes from them.
        student_level='college',
        student_strand='', student_grade='',
        student_program='BSIT', student_year='3',
        program_year='BSIT - 3',
        department='',
        drivers_license='N01-20-800001',
        driver_name='', driver_relationship='',
        schedule='MWF', campus_days=['Monday', 'Wednesday', 'Friday'],
        # Vehicle block
        plate_number='ABC 1234', conduction_number='',
        vehicle_type='Sedan', vehicle_color='BLUE', body_number='',
        privacy_consent=True,
        registrant_type='student',
        fetcher_type='', fetcher_students=[],
    )
    data.update(over)
    return data


def employee_payload(**over):
    data = dict(
        last_name='REYES', first_name='MARIA', middle_name='',
        full_name='REYES, MARIA',
        email='maria.reyes@slc-sflu.edu.ph',
        student_level='',
        student_strand='', student_grade='',
        student_program='', student_year='',
        program_year='',
        department='Teaching',
        drivers_license='N01-20-800002',
        driver_name='', driver_relationship='',
        schedule='', campus_days=[],
        plate_number='EMP 4321', conduction_number='',
        vehicle_type='SUV', vehicle_color='WHITE', body_number='',
        privacy_consent=True,
        registrant_type='employee',
        fetcher_type='', fetcher_students=[],
    )
    data.update(over)
    return data


def fetcher_payload(**over):
    data = dict(
        last_name='SANTOS', first_name='PEDRO', middle_name='',
        full_name='SANTOS, PEDRO',
        email='pedro.santos@gmail.com',
        student_level='',
        student_strand='', student_grade='',
        student_program='', student_year='',
        program_year='',
        department='',
        drivers_license='N01-20-800003',
        driver_name='', driver_relationship='',
        schedule='', campus_days=[],
        plate_number='FET 7788', conduction_number='',
        vehicle_type='Van', vehicle_color='SILVER', body_number='',
        privacy_consent=True,
        registrant_type='fetcher',
        fetcher_type='drop_and_go',
        # Name and level only — no student_id, no assessment.
        fetcher_students=[
            {'full_name': 'SANTOS, ANA', 'student_level': 'jhs',
             'program_year': 'Grade 7'},
        ],
    )
    data.update(over)
    return data


@override_settings(EMAIL_BACKEND=LOCMEM, DEFAULT_FROM_EMAIL='slccdso@gmail.com',
                   PUBLIC_SITE_URL='https://slc.example.edu',
                   EMAIL_SEND_ASYNC=False)
class TrialFlowTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        today = timezone.localdate()
        RegistrationPeriod.objects.create(
            label='DPO trial flow', is_active=True,
            start_date=today - timedelta(days=1), end_date=today + timedelta(days=1))
        self.admin = User.objects.create_user(
            email='trialadmin@slc.edu.ph', full_name='Trial Admin',
            password='pw', role='admin', is_staff=True, is_superuser=True)

    def submit(self, payload):
        res = self.client.post('/api/vehicles/register/open/', payload, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        return VehicleRegistration.objects.get(pk=res.data['id'])

    def pay(self, reg, or_number='1380093'):
        return self.client.post('/api/vehicles/register/payment/', {
            'token': str(reg.payment_token), 'or_number': or_number,
        }, format='json')

    def accept(self, reg, **body):
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(f'/api/vehicles/registrations/{reg.pk}/accept/',
                               body, format='json')
        self.client.force_authenticate(user=None)
        return res


class TheFormsPayloadIsAcceptedTests(TrialFlowTestCase):
    """Each registrant type, posted exactly as the form composes it."""

    def test_a_student_application_is_accepted_and_stores_what_it_should(self):
        reg = self.submit(student_payload())

        self.assertEqual(reg.full_name, 'DELA CRUZ, JUAN, SANTOS')
        self.assertEqual(reg.email, '12345678@slc-sflu.edu.ph')
        self.assertEqual(reg.plate_number, 'ABC1234')
        self.assertEqual(reg.vehicle_color, 'BLUE')
        self.assertEqual(reg.drivers_license, 'N01-20-800001')
        self.assertEqual(reg.program_year, 'BSIT - 3')
        self.assertEqual(reg.student_level, 'college')
        self.assertEqual(reg.schedule, 'MWF')
        self.assertEqual(reg.campus_days, ['Monday', 'Wednesday', 'Friday'])
        self.assertEqual(reg.status, VehicleRegistration.Status.PENDING)

    def test_an_employee_application_keeps_its_department_and_fee(self):
        reg = self.submit(employee_payload())
        self.assertEqual(reg.department_type, 'teaching')
        self.assertEqual(reg.schedule, 'ANY')
        self.assertEqual(reg.payment_status, VehicleRegistration.PaymentStatus.UNPAID)
        self.assertEqual(reg.pass_fee(),
                         SystemSettings.get().vehicle_pass_fee_employee)

    def test_a_fee_exempt_employee_still_short_circuits_the_fee(self):
        reg = self.submit(employee_payload(department='Cleaning and Services',
                                           email='cleaner@slc-sflu.edu.ph',
                                           plate_number='EMP 4322',
                                           drivers_license='N01-20-800012'))
        self.assertEqual(reg.payment_status, VehicleRegistration.PaymentStatus.EXEMPT)
        self.assertEqual(reg.amount_paid, Decimal('0.00'))

    def test_a_fetcher_lists_its_students_by_name_and_level(self):
        reg = self.submit(fetcher_payload())
        self.assertEqual(reg.fetcher_type, 'drop_and_go')
        self.assertEqual(reg.fetcher_students, [
            {'full_name': 'SANTOS, ANA', 'student_level': 'jhs',
             'program_year': 'Grade 7'},
        ])

    def test_a_guardian_driven_student_still_names_its_driver(self):
        """JHS/Elementary/SpEd register an adult driver; only their number went."""
        reg = self.submit(student_payload(
            email='parent.driver@gmail.com', plate_number='GRD 1111',
            drivers_license='N01-20-800004',
            student_level='jhs', student_program='', student_year='',
            student_grade='7', program_year='JHS - Grade 7',
            driver_name='DELA CRUZ, PEDRO', driver_relationship='parent',
        ))
        self.assertEqual(reg.driver_name, 'DELA CRUZ, PEDRO')
        self.assertEqual(reg.driver_relationship, 'parent')
        self.assertEqual(reg.driver_contact, '')

    def test_a_brand_new_car_registers_on_its_conduction_number(self):
        reg = self.submit(student_payload(
            email='newcar@slc-sflu.edu.ph', drivers_license='N01-20-800005',
            plate_number='', conduction_number='CS12345A678',
        ))
        self.assertEqual(reg.plate_number, '')
        self.assertEqual(reg.conduction_number, 'CS12345A678')

    def test_nothing_withheld_is_stored_by_any_of_them(self):
        for name, payload in (('student', student_payload()),
                              ('employee', employee_payload()),
                              ('fetcher', fetcher_payload())):
            with self.subTest(registrant_type=name):
                reg = self.submit(payload)
                self.assertEqual(reg.address, '')
                self.assertEqual(reg.contact_number, '')
                self.assertIsNone(reg.age)
                self.assertEqual(reg.student_id, '')
                self.assertEqual(reg.employee_id, '')
                self.assertEqual(reg.driver_contact, '')


class TheWholePathStillRunsTests(TrialFlowTestCase):
    """Submit → acknowledgement → receipt number → approval → credentials."""

    def test_a_paying_student_goes_all_the_way_to_an_account(self):
        mail.outbox.clear()
        reg = self.submit(student_payload())

        # 1. Acknowledgement email, with the acknowledgement PDF attached.
        self.assertEqual(len(mail.outbox), 1)
        pending = mail.outbox[0]
        self.assertIn('REG-', pending.subject)
        self.assertEqual(pending.to, ['12345678@slc-sflu.edu.ph'])
        self.assertEqual(len(pending.attachments), 1)
        self.assertTrue(pending.attachments[0][0].endswith('.pdf'))
        self.assertTrue(pending.attachments[0][1].startswith(b'%PDF'))
        # It carries the link the applicant needs, and none of the withheld data.
        body = pending.alternatives[0][0]
        self.assertIn(str(reg.payment_token), body)
        for label in ('Address', 'Contact No.', 'Student ID'):
            self.assertNotIn(label, body)

        # 2. The applicant files the OR number. No file, no receipt image.
        self.assertEqual(self.pay(reg).status_code, 200)
        reg.refresh_from_db()
        self.assertEqual(reg.payment_status, VehicleRegistration.PaymentStatus.PAID)
        self.assertEqual(reg.or_number, '1380093')
        self.assertFalse(reg.or_receipt_image)
        # 3. …and is told the fee landed.
        self.assertIn('Official Receipt Received', mail.outbox[-1].subject)

        # 4. CDSO approves it.
        res = self.accept(reg)
        self.assertEqual(res.status_code, 200, res.data)

        reg.refresh_from_db()
        self.assertEqual(reg.status, VehicleRegistration.Status.ACCEPTED)
        self.assertTrue(reg.system_student_id)

        # The account, the vehicle and the link between them all exist.
        owner = User.objects.get(email='12345678@slc-sflu.edu.ph')
        self.assertEqual(owner.role, 'vehicle_owner')
        self.assertTrue(owner.must_change_password)
        self.assertEqual(owner.schedule, 'MWF')
        # Nothing withheld was carried onto the account either. NULL rather than
        # '': both columns are null=True, and the accept flow now omits them
        # instead of passing a blank — which is the truer record of a field that
        # was never collected. Nothing reads them as strings (UserSerializer just
        # emits null), so the distinction costs nothing.
        self.assertIsNone(owner.contact)
        self.assertIsNone(owner.address)

        vehicle = Vehicle.objects.get(plate_number='ABC1234')
        self.assertEqual(vehicle.user_id, owner.id)
        self.assertTrue(vehicle.is_authorized)
        self.assertEqual(reg.user_id, owner.id)
        self.assertEqual(reg.vehicle_id, vehicle.id)

        # 5. The credentials email, with the QR and the registration PDF.
        approval = mail.outbox[-1]
        self.assertIn('Approved', approval.subject)
        self.assertEqual(approval.to, ['12345678@slc-sflu.edu.ph'])
        names = [a[0] for a in approval.attachments]
        self.assertTrue(any(n.endswith('.png') for n in names), names)
        self.assertTrue(any(n.endswith('.pdf') for n in names), names)
        approval_body = approval.alternatives[0][0]
        self.assertIn(owner.user_code, approval_body)
        for label in ('Address', 'Student ID'):
            self.assertNotIn(label, approval_body)

    def test_a_fee_exempt_employee_reaches_an_account_without_paying(self):
        mail.outbox.clear()
        reg = self.submit(employee_payload(department='Cleaning and Services'))
        self.assertIn('No Payment Required', mail.outbox[0].alternatives[0][0])

        res = self.accept(reg)
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(User.objects.filter(
            email='maria.reyes@slc-sflu.edu.ph', role='vehicle_owner').exists())

    def test_an_unpaid_approval_still_demands_a_stated_reason(self):
        """The trial removed the receipt image, not the accountability."""
        reg = self.submit(student_payload())
        refused = self.accept(reg)
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(refused.data['error'], 'unpaid_acceptance_requires_reason')

        allowed = self.accept(reg, unpaid_accept_reason='Receipt shown at the counter.')
        self.assertEqual(allowed.status_code, 200, allowed.data)

    def test_a_rejected_application_still_tells_the_applicant_why(self):
        reg = self.submit(student_payload())
        mail.outbox.clear()
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(f'/api/vehicles/registrations/{reg.pk}/reject/',
                               {'reason': 'Plate does not match the licence.'},
                               format='json')
        self.client.force_authenticate(user=None)

        self.assertEqual(res.status_code, 200, res.data)
        reg.refresh_from_db()
        self.assertEqual(reg.status, VehicleRegistration.Status.REJECTED)
        self.assertIn('Plate does not match the licence.', mail.outbox[-1].body)


class TheGuardsThatSurvivedTests(TrialFlowTestCase):
    """Dropping the ID columns must not have dropped the 1:1 rules with them."""

    def test_a_duplicate_plate_is_still_refused(self):
        self.submit(student_payload())
        res = self.client.post('/api/vehicles/register/open/', student_payload(
            email='someone.else@slc-sflu.edu.ph',
            drivers_license='N01-20-800006'), format='json')
        self.assertEqual(res.status_code, 400)

    def test_a_duplicate_email_is_still_refused(self):
        self.submit(student_payload())
        res = self.client.post('/api/vehicles/register/open/', student_payload(
            plate_number='XYZ 9999', drivers_license='N01-20-800007'),
            format='json')
        self.assertEqual(res.status_code, 400)

    def test_a_duplicate_licence_is_still_refused(self):
        self.submit(student_payload())
        res = self.client.post('/api/vehicles/register/open/', student_payload(
            email='third.party@slc-sflu.edu.ph', plate_number='XYZ 8888'),
            format='json')
        self.assertEqual(res.status_code, 400)

    def test_a_fetcher_still_needs_a_name_and_a_level_per_student(self):
        for bad in ({'full_name': '', 'student_level': 'jhs'},
                    {'full_name': 'SANTOS, ANA', 'student_level': ''}):
            with self.subTest(student=bad):
                res = self.client.post('/api/vehicles/register/open/',
                                       fetcher_payload(fetcher_students=[bad]),
                                       format='json')
                self.assertEqual(res.status_code, 400)

    def test_the_review_payload_carries_nothing_withheld(self):
        """What CDSO's queue and the owner portal actually receive."""
        from vehicles.serializers import VehicleRegistrationSerializer

        reg = self.submit(student_payload())
        data = VehicleRegistrationSerializer(reg).data
        for name in WITHHELD:
            self.assertNotIn(name, data)
        for name in ('drivers_license_image', 'assessment_form', 'or_receipt_image'):
            self.assertIsNone(data[name])
        # …and what it must still carry, or the reviewer cannot review.
        self.assertEqual(data['full_name'], 'DELA CRUZ, JUAN, SANTOS')
        self.assertEqual(data['drivers_license'], 'N01-20-800001')
        self.assertEqual(data['plate_number'], 'ABC1234')
