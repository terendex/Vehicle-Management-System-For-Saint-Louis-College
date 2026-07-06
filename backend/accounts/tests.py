"""Tests for user-account email uniqueness rules.

Every account-creation/update path must reject a duplicate email
case-insensitively and store it lowercased. Also covers the admin
direct-owner create guard against colliding with an active registration.
"""
from django.test import TestCase, override_settings

from accounts.models import User
from accounts.serializers import (
    GuardCreateSerializer,
    AdminReplaceSerializer,
    RegisterSerializer,
    UserUpdateSerializer,
    AdminOwnerCreateSerializer,
)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class UserEmailUniquenessTests(TestCase):
    def setUp(self):
        self.existing = User.objects.create_user(
            email='taken@example.com', full_name='Existing', password='Passw0rd!23', role='admin',
        )

    def test_guard_rejects_duplicate_email_case_insensitive(self):
        s = GuardCreateSerializer(data={'full_name': 'G', 'email': 'TAKEN@Example.com', 'agency': 'ACME'})
        self.assertFalse(s.is_valid())
        self.assertIn('email', s.errors)

    def test_guard_created_email_is_lowercased(self):
        s = GuardCreateSerializer(data={'full_name': 'G', 'email': 'NewGuard@Example.COM', 'agency': 'ACME'})
        self.assertTrue(s.is_valid(), s.errors)
        guard = s.save()
        self.assertEqual(guard.email, 'newguard@example.com')
        self.assertEqual(guard.role, 'security')

    def test_admin_replace_rejects_duplicate_email_case_insensitive(self):
        s = AdminReplaceSerializer(data={'full_name': 'A', 'email': 'Taken@example.COM'})
        self.assertFalse(s.is_valid())
        self.assertIn('email', s.errors)

    def test_register_rejects_duplicate_email_case_insensitive(self):
        s = RegisterSerializer(data={
            'full_name': 'S', 'email': 'TAKEN@example.com',
            'password': 'Passw0rd!23', 'confirm_password': 'Passw0rd!23', 'role': 'security',
        })
        self.assertFalse(s.is_valid())
        self.assertIn('email', s.errors)

    def test_user_update_rejects_another_users_email(self):
        other = User.objects.create_user(
            email='other@example.com', full_name='Other', password='Passw0rd!23', role='security',
        )
        s = UserUpdateSerializer(instance=other, data={
            'full_name': 'Other', 'email': 'TAKEN@example.com', 'role': 'security',
        })
        self.assertFalse(s.is_valid())
        self.assertIn('email', s.errors)

    def test_user_update_allows_same_users_own_email(self):
        s = UserUpdateSerializer(instance=self.existing, data={
            'full_name': 'Existing', 'email': 'TAKEN@example.com', 'role': 'admin',
        })
        self.assertTrue(s.is_valid(), s.errors)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class AdminOwnerCreateConflictTests(TestCase):
    def setUp(self):
        from vehicles.models import VehicleRegistration
        VehicleRegistration.objects.create(
            registrant_type='employee', full_name='X', email='occupied@example.com',
            plate_number='ABC 1234', vehicle_type='car', employee_id='E-1',
            drivers_license='N01-20-123456', status=VehicleRegistration.Status.ACCEPTED,
        )

    def _data(self, **overrides):
        d = dict(
            last_name='Doe', first_name='Jane', email='newowner@example.com',
            registrant_type='employee', employee_id='E-9',
            plate_number='ZZZ 9999', vehicle_type='car', drivers_license='N02-21-654321',
        )
        d.update(overrides)
        return d

    def test_rejects_plate_conflict_with_active_registration(self):
        s = AdminOwnerCreateSerializer(data=self._data(plate_number='abc1234'))
        self.assertFalse(s.is_valid())
        self.assertIn('plate_number', s.errors)

    def test_rejects_license_conflict_with_active_registration(self):
        s = AdminOwnerCreateSerializer(data=self._data(drivers_license='n01-20-123456'))
        self.assertFalse(s.is_valid())
        self.assertIn('drivers_license', s.errors)

    def test_no_orphan_user_created_on_conflict(self):
        before = User.objects.count()
        # Email belongs to a registration (not a User), so it passes the User check
        # but must trip the active-registration email conflict in validate().
        s = AdminOwnerCreateSerializer(data=self._data(email='occupied@example.com'))
        self.assertFalse(s.is_valid())
        self.assertIn('email', s.errors)
        self.assertEqual(User.objects.count(), before)

    def test_clean_owner_create_succeeds(self):
        s = AdminOwnerCreateSerializer(data=self._data())
        self.assertTrue(s.is_valid(), s.errors)
        user = s.save()
        self.assertEqual(user.role, 'vehicle_owner')
        self.assertEqual(user.email, 'newowner@example.com')
