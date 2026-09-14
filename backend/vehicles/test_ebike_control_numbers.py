"""E-bike control numbers (FM-001, FM-002, ...).

An e-bike registers with neither a plate nor a conduction number: the system
issues it the next control number, stores it in plate_number (so the gate QR
and every identity lookup work on it unchanged), and never lets it be edited.
"""
from datetime import timedelta

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from vehicles.control_numbers import is_control_number, peek_next_control_number
from vehicles.models import RegistrationPeriod, Vehicle, VehicleRegistration
from vehicles.registration_edits import clean_changes, editable_for

LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'
OPEN_URL = '/api/vehicles/register/open/'


def _number(control):
    return int(control.split('-')[1])


@override_settings(EMAIL_BACKEND=LOCMEM, EMAIL_SEND_ASYNC=False)
class EbikeRegistrationTests(TestCase):
    def setUp(self):
        today = timezone.localdate()
        RegistrationPeriod.objects.create(
            label='T', start_date=today - timedelta(days=1),
            end_date=today + timedelta(days=30), is_active=True)

    def _post(self, email, **overrides):
        payload = dict(registrant_type='employee', full_name='E BIKE', email=email,
                       vehicle_type='E-Bike', vehicle_color='BLACK', department='Teaching')
        payload.update(overrides)
        return APIClient().post(OPEN_URL, payload, format='json')

    def test_numbers_are_issued_in_sequence(self):
        expected = peek_next_control_number()
        first = self._post('ebike1@slc-sflu.edu.ph')
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(first.data['control_number'], expected)

        second = self._post('ebike2@slc-sflu.edu.ph')
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(_number(second.data['control_number']), _number(expected) + 1)

        reg = VehicleRegistration.objects.get(email='ebike2@slc-sflu.edu.ph')
        self.assertEqual(reg.plate_number, second.data['control_number'])
        self.assertEqual(reg.conduction_number, '')

    def test_first_number_is_fm_001_on_an_empty_table(self):
        if (VehicleRegistration.objects.filter(plate_number__startswith='FM-').exists()
                or Vehicle.objects.filter(plate_number__startswith='FM-').exists()):
            self.skipTest('test database already holds control numbers')
        self.assertEqual(self._post('first@slc-sflu.edu.ph').data['control_number'], 'FM-001')

    def test_typed_identifiers_are_ignored(self):
        resp = self._post('typed@slc-sflu.edu.ph', plate_number='ABC 1234',
                          conduction_number='CS12345')
        self.assertEqual(resp.status_code, 201, resp.data)
        reg = VehicleRegistration.objects.get(email='typed@slc-sflu.edu.ph')
        self.assertTrue(is_control_number(reg.plate_number))
        self.assertEqual(reg.conduction_number, '')

    def test_rejected_numbers_are_not_reissued(self):
        taken = self._post('rejected@slc-sflu.edu.ph').data['control_number']
        VehicleRegistration.objects.filter(plate_number=taken).update(status='rejected')
        after = self._post('after@slc-sflu.edu.ph').data['control_number']
        self.assertEqual(_number(after), _number(taken) + 1)

    def test_other_vehicle_types_still_need_a_plate(self):
        resp = self._post('car@slc-sflu.edu.ph', vehicle_type='Sedan')
        self.assertEqual(resp.status_code, 400)
        self.assertIsNone(
            self._post('car2@slc-sflu.edu.ph', vehicle_type='Sedan',
                       plate_number='ABC 1234').data.get('control_number'))

    def test_pending_email_calls_it_a_control_number(self):
        self._post('mail@slc-sflu.edu.ph')
        body = mail.outbox[-1].body
        self.assertIn('Control Number: FM-', body)
        self.assertNotIn('Plate Number', body)

    def test_preview_endpoint_ignores_a_stale_token(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer not-a-real-token')
        resp = client.get('/api/vehicles/register/ebike-control-number/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['control_number'], peek_next_control_number())


@override_settings(EMAIL_BACKEND=LOCMEM, EMAIL_SEND_ASYNC=False)
class EbikeWalkInTests(TestCase):
    def test_walk_in_gets_a_control_number_and_an_ebike_vehicle(self):
        from accounts.models import User
        admin = User.objects.create_user(email='cdso-ebike@slc.edu.ph', full_name='CDSO',
                                         password='x', role='admin')
        client = APIClient()
        client.force_authenticate(admin)
        resp = client.post('/api/vehicles/register/direct/', dict(
            registrant_type='employee', full_name='WALK IN', email='walkin-ebike@slc-sflu.edu.ph',
            vehicle_type='E-Bike', vehicle_color='RED', department='Teaching',
            or_number='1234567', plate_number='SHOULD BE IGNORED',
        ), format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        control = resp.data['account']['plate_number']
        self.assertTrue(is_control_number(control))
        vehicle = Vehicle.objects.get(plate_number=control)
        self.assertEqual(vehicle.vehicle_type, Vehicle.Type.EBIKE)
        # The gate QR is VEHICLE:{plate}|ID:{id}; the scanner hands the plate to
        # Vehicle.resolve, which must find it however the guard types it.
        self.assertEqual(Vehicle.resolve(control).pk, vehicle.pk)
        self.assertEqual(Vehicle.resolve(control.replace('-', '').lower()).pk, vehicle.pk)
        self.assertEqual(Vehicle.resolve(f'FM-{_number(control)}').pk, vehicle.pk)


class EbikeEditLockTests(TestCase):
    def _reg(self, **kwargs):
        fields = dict(registrant_type='employee', full_name='E BIKE', email='lock@x.com',
                      vehicle_type='E-Bike', vehicle_color='BLACK', plate_number='FM-042',
                      status='pending')
        fields.update(kwargs)
        return VehicleRegistration.objects.create(**fields)

    def test_control_number_and_type_are_not_editable(self):
        reg = self._reg()
        names = {f.name for f in editable_for(reg)}
        self.assertFalse(names & {'plate_number', 'conduction_number', 'vehicle_type'})
        self.assertIn('vehicle_color', names)

        changes, errors = clean_changes(reg, {'plate_number': 'ABC 1234'})
        self.assertEqual(changes, {})
        self.assertIn('cannot be changed', errors['plate_number'])
        _, errors = clean_changes(reg, {'vehicle_type': 'Motorcycle'})
        self.assertIn('cannot be changed', errors['vehicle_type'])

    def test_a_plated_vehicle_cannot_become_an_ebike(self):
        reg = self._reg(vehicle_type='Motorcycle', plate_number='AB1234')
        _, errors = clean_changes(reg, {'vehicle_type': 'E-Bike'})
        self.assertIn('vehicle_type', errors)

    def test_legacy_ebike_with_a_real_plate_stays_editable(self):
        reg = self._reg(plate_number='AB1234')
        names = {f.name for f in editable_for(reg)}
        self.assertIn('plate_number', names)
        changes, errors = clean_changes(reg, {'vehicle_type': 'E-Bike', 'vehicle_color': 'RED'})
        self.assertEqual(errors, {})
        self.assertEqual(changes, {'vehicle_color': 'RED'})
