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


class AccountExpiryArchiveTests(TestCase):
    """Owner accounts auto-archive on expiry, and archiving frees the owner's
    email / ID / plate so they can register again."""

    def setUp(self):
        from vehicles.models import SystemSettings
        cfg = SystemSettings.get()
        cfg.account_expiry_enabled = True
        cfg.account_expiry_months = 1
        cfg.account_expiry_days = 0
        cfg.save()

    def _make_owner_with_reg(self):
        from accounts.models import User
        owner = User.objects.create_user(
            email='owner@example.com', full_name='Owner One',
            password='Passw0rd!23', role='vehicle_owner',
            owner_type='student',
        )
        reg = make_reg(
            registrant_type='student', full_name='Owner One',
            email='owner@example.com', plate_number='OWN 111',
            student_id='20250001', user=owner,
            status=VehicleRegistration.Status.ACCEPTED,
        )
        return owner, reg

    def test_create_user_sets_expiry(self):
        owner, _ = self._make_owner_with_reg()
        self.assertIsNotNone(owner.expires_at)
        # ~1 month out (28-31 days depending on month length)
        delta = (owner.expires_at - timezone.localdate()).days
        self.assertGreaterEqual(delta, 27)
        self.assertLessEqual(delta, 32)

    def test_admin_account_never_expires(self):
        from accounts.models import User
        admin = User.objects.create_user(
            email='admin2@example.com', full_name='Admin', password='x', role='admin')
        self.assertIsNone(admin.expires_at)

    def test_archive_and_reregistration(self):
        from django.core import mail
        from accounts.models import User, AuditLog
        from vehicles.tasks import auto_archive_expired_accounts
        from vehicles.views import _registration_conflict, _email_conflict

        owner, reg = self._make_owner_with_reg()
        # Force expiry into the past
        owner.expires_at = timezone.localdate() - timedelta(days=1)
        owner.save(update_fields=['expires_at'])

        result = auto_archive_expired_accounts()
        self.assertEqual(result['archived'], 1)

        owner.refresh_from_db()
        reg.refresh_from_db()
        self.assertTrue(owner.is_archived)
        self.assertFalse(owner.is_active)
        self.assertIsNotNone(owner.archived_at)
        self.assertEqual(reg.status, VehicleRegistration.Status.EXPIRED)
        self.assertTrue(AuditLog.objects.filter(
            action=AuditLog.Action.USER_ARCHIVED, target_user=owner).exists())
        self.assertGreaterEqual(len(mail.outbox), 1)

        # Identity is now free: no conflict for the same email / plate / ID
        active = VehicleRegistration.objects.filter(
            status__in=[VehicleRegistration.Status.PENDING, VehicleRegistration.Status.ACCEPTED])
        self.assertIsNone(_email_conflict('owner@example.com', active))
        self.assertIsNone(_registration_conflict(
            'student', 'OWN 111', 'owner@example.com', '20250001', ''))

        # And a brand-new account can be created with the reused email (the DB
        # partial-unique excludes the archived row).
        new_owner = User.objects.create_user(
            email='owner@example.com', full_name='Owner One Again',
            password='Passw0rd!23', role='vehicle_owner', owner_type='student')
        self.assertFalse(new_owner.is_archived)
        # Auth resolves to the live account, not the archived one
        self.assertEqual(User.objects.get_by_natural_key('owner@example.com').pk, new_owner.pk)

    def test_idempotent(self):
        from vehicles.tasks import auto_archive_expired_accounts
        owner, _ = self._make_owner_with_reg()
        owner.expires_at = timezone.localdate() - timedelta(days=1)
        owner.save(update_fields=['expires_at'])
        self.assertEqual(auto_archive_expired_accounts()['archived'], 1)
        self.assertEqual(auto_archive_expired_accounts()['archived'], 0)

    def _expire_now(self, owner):
        owner.expires_at = timezone.localdate() - timedelta(days=1)
        owner.save(update_fields=['expires_at'])

    def test_plate_freed_via_vehicle_unlink(self):
        """A non-banned owner's Vehicle plate is released (user unlinked) so the
        plate-level conflict check no longer blocks re-registration."""
        from vehicles.models import Vehicle
        from vehicles.tasks import auto_archive_expired_accounts
        from vehicles.views import _plate_conflict

        owner, _ = self._make_owner_with_reg()
        veh = Vehicle.objects.create(plate_number='OWN111', vehicle_type='car',
                                     is_authorized=True, user=owner)
        active = VehicleRegistration.objects.filter(status='accepted')
        # Before archive: the plate is tied to a live account → blocked
        self.assertIsNotNone(_plate_conflict('OWN 111', active.none()))

        self._expire_now(owner)
        auto_archive_expired_accounts()

        veh.refresh_from_db()
        self.assertIsNone(veh.user_id)           # unlinked → plate freed
        self.assertFalse(veh.is_authorized)
        # No active registrations and unowned vehicle → plate is clear
        self.assertIsNone(_plate_conflict('OWN 111',
            VehicleRegistration.objects.filter(status__in=['pending', 'accepted'])))

    def test_max_violation_owner_is_banned(self):
        """An owner who reached the max violations is banned on archive, keeps the
        Vehicle link, and cannot re-register by email / plate / ID."""
        from vehicles.models import Vehicle
        from violations.models import Violation
        from vehicles.tasks import auto_archive_expired_accounts
        from vehicles.views import _registration_ban

        owner, _ = self._make_owner_with_reg()
        veh = Vehicle.objects.create(plate_number='OWN111', vehicle_type='car',
                                     is_authorized=True, user=owner)
        Violation.objects.create(vehicle=veh, violation_type='unauthorized_entry',
                                 offense_number=3, status='fee_imposed',
                                 registration_blocked=True)

        self._expire_now(owner)
        result = auto_archive_expired_accounts()
        self.assertEqual(result['banned'], 1)

        owner.refresh_from_db()
        veh.refresh_from_db()
        self.assertTrue(owner.registration_banned)
        self.assertEqual(veh.user_id, owner.pk)  # kept linked (still blocked/traceable)

        # Any of the banned identity's fields is refused
        self.assertIsNotNone(_registration_ban('OWN 111', 'owner@example.com', '20250001', ''))
        self.assertIsNotNone(_registration_ban('', 'owner@example.com', '', ''))
        self.assertIsNotNone(_registration_ban('OWN 111', '', '', ''))
        # An unrelated applicant is not banned
        self.assertIsNone(_registration_ban('ZZZ 999', 'clean@example.com', '99999999', ''))

    def test_banned_applicant_blocked_at_submission(self):
        from vehicles.models import Vehicle, RegistrationPeriod
        from violations.models import Violation
        from vehicles.tasks import auto_archive_expired_accounts
        from rest_framework.test import APIClient

        owner, _ = self._make_owner_with_reg()
        veh = Vehicle.objects.create(plate_number='OWN111', vehicle_type='car',
                                     is_authorized=True, user=owner)
        Violation.objects.create(vehicle=veh, violation_type='unauthorized_entry',
                                 offense_number=3, status='fee_imposed',
                                 registration_blocked=True)
        self._expire_now(owner)
        auto_archive_expired_accounts()

        # Open a registration window so submission reaches the ban check
        today = timezone.localdate()
        RegistrationPeriod.objects.create(
            label='Test', start_date=today - timedelta(days=1),
            end_date=today + timedelta(days=30), is_active=True)

        resp = APIClient().post('/api/vehicles/register/open/', {
            'registrant_type': 'student', 'full_name': 'Owner One',
            'email': 'owner@example.com', 'plate_number': 'OWN 111',
            'vehicle_type': 'car', 'student_id': '20250001',
            'contact_number': '+639171234567', 'address': 'Somewhere',
        }, format='json')
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(resp.data.get('registration_banned'))
