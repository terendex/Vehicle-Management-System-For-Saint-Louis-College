"""Services and Cleaning staff pay nothing for a vehicle pass.

That is an exemption, not the 50% employee rate — so it must hold even if the
configured employee fee changes. The amount lives on the model rather than in
the React form, because the price a person is quoted and the price the system
believes were otherwise two implementations free to drift apart.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from vehicles.models import VehicleRegistration, SystemSettings, RegistrationPeriod

User = get_user_model()
DT = VehicleRegistration.DepartmentType


def make_reg(**kw):
    base = dict(
        full_name='DELA CRUZ, JUAN', email='dept-fee@slc.edu.ph',
        plate_number='DPT1234', registrant_type='employee',
    )
    base.update(kw)
    return VehicleRegistration(**base)


class DepartmentFeeTests(TestCase):
    def setUp(self):
        s = SystemSettings.get()
        s.vehicle_pass_fee = Decimal('300.00')
        s.vehicle_pass_fee_employee = Decimal('150.00')
        s.save()
        self.settings = s

    def test_services_staff_pay_nothing(self):
        r = make_reg(department_type=DT.SERVICES)
        self.assertEqual(r.pass_fee(self.settings), Decimal('0.00'))

    def test_cleaning_staff_pay_nothing(self):
        r = make_reg(department_type=DT.CLEANING)
        self.assertEqual(r.pass_fee(self.settings), Decimal('0.00'))

    def test_teaching_staff_pay_the_employee_rate(self):
        r = make_reg(department_type=DT.TEACHING)
        self.assertEqual(r.pass_fee(self.settings), Decimal('150.00'))

    def test_non_teaching_staff_pay_the_employee_rate(self):
        r = make_reg(department_type=DT.NON_TEACHING)
        self.assertEqual(r.pass_fee(self.settings), Decimal('150.00'))

    def test_students_pay_the_standard_rate(self):
        r = make_reg(registrant_type='student', department_type='')
        self.assertEqual(r.pass_fee(self.settings), Decimal('300.00'))

    def test_exemption_survives_a_change_to_the_employee_fee(self):
        """Exempt means zero, not "half of whatever is configured"."""
        self.settings.vehicle_pass_fee_employee = Decimal('999.00')
        self.settings.save()
        self.assertEqual(make_reg(department_type=DT.SERVICES).pass_fee(self.settings),
                         Decimal('0.00'))
        self.assertEqual(make_reg(department_type=DT.CLEANING).pass_fee(self.settings),
                         Decimal('0.00'))
        self.assertEqual(make_reg(department_type=DT.TEACHING).pass_fee(self.settings),
                         Decimal('999.00'))

    def test_a_student_in_a_free_department_still_pays(self):
        """Exemption is tied to being employee staff, not to the label alone."""
        r = make_reg(registrant_type='student', department_type=DT.SERVICES)
        self.assertEqual(r.pass_fee(self.settings), Decimal('300.00'))


class DepartmentSubmissionTests(APITestCase):
    """The public form posts department labels; each must map to a stored value."""

    def setUp(self):
        # The public endpoint refuses submissions outside the registration
        # window, so open one for the duration of the test.
        today = timezone.localdate()
        RegistrationPeriod.objects.create(
            label='Test window', is_active=True,
            start_date=today - timedelta(days=1),
            end_date=today + timedelta(days=1),
        )

    def _submit(self, label):
        # /register/open/ is the unauthenticated public form endpoint;
        # /register/ is the CDSO walk-in one and needs a token.
        return self.client.post('/api/vehicles/register/open/', {
            'full_name': 'DELA CRUZ, JUAN',
            'email': f'dept-{label.lower().replace("-", "")}@slc.edu.ph',
            'contact_number': '+639171234567',
            'plate_number': f'DP{abs(hash(label)) % 9000 + 1000}',
            'vehicle_type': 'car',
            'registrant_type': 'employee',
            'employee_id': f'{abs(hash(label)) % 90000000 + 10000000}',
            'department': label,
            'address': 'San Fernando, La Union',
            'privacy_consent': True,
        }, format='json')

    def test_every_department_label_maps_to_a_stored_value(self):
        for value, label in DT.choices:
            with self.subTest(department=label):
                r = self._submit(label)
                self.assertIn(r.status_code, (200, 201),
                              msg=f"{label} rejected: {getattr(r, 'data', None)}")
                reg = VehicleRegistration.objects.get(email__startswith=f'dept-{label.lower().replace("-", "")}')
                self.assertEqual(reg.department_type, value)

    def test_status_endpoint_publishes_the_exempt_list(self):
        r = self.client.get('/api/vehicles/register/status/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(sorted(r.data['fee_exempt_departments']), ['cleaning', 'services'])
        labels = [d['label'] for d in r.data['department_options']]
        self.assertEqual(labels, ['Teaching', 'Non-Teaching', 'Services', 'Cleaning'])
