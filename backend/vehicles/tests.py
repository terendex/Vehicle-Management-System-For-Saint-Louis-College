"""Tests for the registration 1:1 uniqueness rules.

Covers, for VehicleRegistration:
  * field normalization on save (plate/email/license/IDs),
  * DB-level conditional unique constraints among ACTIVE (pending/accepted)
    registrations, with rejected rows and blank values exempt,
  * the public availability endpoint, and
  * the public submission endpoint returning a clean 400 on conflict.
"""
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from vehicles.models import VehicleRegistration, RegistrationPeriod


def make_reg(**overrides):
    data = dict(
        registrant_type='student',
        full_name='Test User',
        email='user@example.com',
        plate_number='ABC 1234',
        vehicle_type='car',
        student_id='',
        employee_id='',
        drivers_license='',
        status=VehicleRegistration.Status.PENDING,
    )
    data.update(overrides)
    return VehicleRegistration.objects.create(**data)


class RegistrationNormalizationTests(TestCase):
    def test_plate_normalized_on_save(self):
        self.assertEqual(make_reg(plate_number='  abc 12 34 ').plate_number, 'ABC1234')

    def test_email_lowercased_on_save(self):
        self.assertEqual(make_reg(email='  John.Doe@Example.COM ').email, 'john.doe@example.com')

    def test_license_uppercased_on_save(self):
        self.assertEqual(make_reg(drivers_license=' n01-20-123456 ').drivers_license, 'N01-20-123456')

    def test_ids_stripped_on_save(self):
        reg = make_reg(registrant_type='student', student_id=' 12345678 ')
        self.assertEqual(reg.student_id, '12345678')


class ActiveUniquenessConstraintTests(TestCase):
    """The DB constraints must block duplicates among active registrations."""

    def _assert_blocked(self, **overrides):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_reg(**overrides)

    def test_duplicate_active_plate_blocked(self):
        make_reg(plate_number='ABC 1234', email='a@x.com')
        self._assert_blocked(plate_number='abc1234', email='b@x.com')

    def test_duplicate_active_email_blocked_case_insensitive(self):
        make_reg(plate_number='AAA 111', email='dup@x.com')
        self._assert_blocked(plate_number='BBB 222', email='DUP@X.com')

    def test_duplicate_active_student_id_blocked(self):
        make_reg(registrant_type='student', student_id='12345678', plate_number='AAA 111', email='a@x.com')
        self._assert_blocked(registrant_type='student', student_id='12345678', plate_number='BBB 222', email='b@x.com')

    def test_duplicate_active_employee_id_blocked(self):
        make_reg(registrant_type='employee', employee_id='E-1', plate_number='AAA 111', email='a@x.com')
        self._assert_blocked(registrant_type='employee', employee_id='E-1', plate_number='BBB 222', email='b@x.com')

    def test_duplicate_active_license_blocked(self):
        make_reg(drivers_license='N01-20-123456', plate_number='AAA 111', email='a@x.com')
        self._assert_blocked(drivers_license='n01-20-123456', plate_number='BBB 222', email='b@x.com')

    def test_rejected_registration_is_exempt(self):
        make_reg(plate_number='AAA 111', email='a@x.com', drivers_license='N01-20-123456',
                 status=VehicleRegistration.Status.REJECTED)
        # A fresh active registration may reuse a rejected one's plate/email/license
        try:
            make_reg(plate_number='AAA 111', email='a@x.com', drivers_license='N01-20-123456')
        except IntegrityError:
            self.fail('A rejected registration must not block reuse of its values')

    def test_blank_optional_ids_allowed_multiple_times(self):
        # Two employees both leave student_id + license blank — must be allowed
        make_reg(registrant_type='employee', employee_id='E-1', plate_number='AAA 111', email='a@x.com')
        try:
            make_reg(registrant_type='employee', employee_id='E-2', plate_number='BBB 222', email='b@x.com')
        except IntegrityError:
            self.fail('Blank student_id/license must not be treated as duplicates')


class AvailabilityEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        make_reg(
            registrant_type='student', student_id='12345678',
            drivers_license='N01-20-123456', plate_number='ABC 1234', email='taken@x.com',
        )

    def _get(self, **params):
        return self.client.get('/api/vehicles/register/availability/', params)

    def test_plate_conflict_reported(self):
        self.assertIsNotNone(self._get(plate_number='abc1234').data['plate_number'])

    def test_email_conflict_reported(self):
        self.assertIsNotNone(self._get(email='TAKEN@x.com').data['email'])

    def test_license_conflict_reported(self):
        self.assertIsNotNone(self._get(drivers_license='n01-20-123456').data['drivers_license'])

    def test_student_id_conflict_reported(self):
        self.assertIsNotNone(self._get(student_id='12345678').data['student_id'])

    def test_no_conflict_returns_null(self):
        r = self._get(plate_number='ZZZ 9999', email='free@x.com')
        self.assertIsNone(r.data['plate_number'])
        self.assertIsNone(r.data['email'])


class PublicRegistrationConflictTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        today = timezone.localdate()
        RegistrationPeriod.objects.create(
            label='Test window', is_active=True,
            start_date=today - timedelta(days=1), end_date=today + timedelta(days=1),
        )
        make_reg(
            registrant_type='employee', employee_id='E-1',
            drivers_license='N01-20-123456', plate_number='ABC 1234', email='taken@x.com',
        )

    def _post(self, **overrides):
        payload = dict(
            registrant_type='employee', full_name='New User', email='new@x.com',
            plate_number='XYZ 5678', vehicle_type='car',
            drivers_license='N02-21-654321', employee_id='E-9',
            contact_number='+639171234567', address='Somewhere',
        )
        payload.update(overrides)
        return self.client.post('/api/vehicles/register/open/', payload, format='json')

    def test_duplicate_plate_rejected(self):
        self.assertEqual(self._post(plate_number='abc1234').status_code, 400)

    def test_duplicate_email_rejected(self):
        self.assertEqual(self._post(email='TAKEN@x.com').status_code, 400)

    def test_duplicate_license_rejected(self):
        self.assertEqual(self._post(drivers_license='n01-20-123456').status_code, 400)

    def test_clean_registration_succeeds(self):
        self.assertEqual(self._post().status_code, 201)
