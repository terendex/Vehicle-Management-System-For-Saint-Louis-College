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


class ConductionPlateTests(TestCase):
    """Registration accepts a plate OR a conduction number (never both), and the
    gate/parking resolver finds a vehicle by either."""

    def setUp(self):
        from vehicles.models import RegistrationPeriod
        today = timezone.localdate()
        RegistrationPeriod.objects.create(
            label='T', start_date=today - timedelta(days=1),
            end_date=today + timedelta(days=30), is_active=True)

    def _post(self, **overrides):
        payload = dict(registrant_type='student', full_name='New Car', email='newcar@x.com',
                       vehicle_type='car', student_id='30000001', campus_days=['Monday'],
                       contact_number='+639171234567', address='X')
        payload.update(overrides)
        return APIClient().post('/api/vehicles/register/open/', payload, format='json')

    def test_conduction_only_registration_ok(self):
        r = self._post(conduction_number='CS12345A678')
        self.assertEqual(r.status_code, 201, r.data)
        reg = VehicleRegistration.objects.get(email='newcar@x.com')
        self.assertEqual(reg.conduction_number, 'CS12345A678')
        self.assertEqual(reg.plate_number, '')

    def test_both_identifiers_rejected(self):
        self.assertEqual(self._post(plate_number='ABC 1234', conduction_number='CS12345A678').status_code, 400)

    def test_neither_identifier_rejected(self):
        self.assertEqual(self._post().status_code, 400)

    def test_resolve_by_conduction_or_plate(self):
        from vehicles.models import Vehicle
        v = Vehicle.objects.create(conduction_number='CS999X', vehicle_type='car')
        self.assertEqual(Vehicle.resolve('cs 999 x').pk, v.pk)   # normalized match
        p = Vehicle.objects.create(plate_number='ABC1234', vehicle_type='car')
        self.assertEqual(Vehicle.resolve('ABC 1234').pk, p.pk)
        self.assertIsNone(Vehicle.resolve('NOTHING'))

    def test_two_conduction_only_vehicles_allowed(self):
        # Blank plate must not collide under the partial unique constraint.
        from vehicles.models import Vehicle
        Vehicle.objects.create(conduction_number='C1', vehicle_type='car')
        Vehicle.objects.create(conduction_number='C2', vehicle_type='car')  # no IntegrityError


class PlateSwapTests(TestCase):
    """Owner replaces a conduction number with the real plate — once, self-service."""

    def _make_conduction_owner(self, email='swap@x.com', conduction='CS111'):
        from accounts.models import User
        from vehicles.models import Vehicle
        owner = User.objects.create_user(email=email, full_name='Swap', password='x',
                                         role='vehicle_owner', owner_type='student')
        veh = Vehicle.objects.create(conduction_number=conduction, vehicle_type='car',
                                     is_authorized=True, user=owner)
        reg = make_reg(registrant_type='student', full_name='Swap', email=email,
                       plate_number='', conduction_number=conduction, vehicle=veh, user=owner,
                       status=VehicleRegistration.Status.ACCEPTED)
        return owner, veh, reg

    def test_swap_replaces_conduction_and_is_one_time(self):
        owner, veh, reg = self._make_conduction_owner()
        c = APIClient(); c.force_authenticate(owner)
        resp = c.post('/api/accounts/me/plate-swap/', {'plate_number': 'XYZ 5678'}, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)
        veh.refresh_from_db(); reg.refresh_from_db()
        self.assertEqual(veh.plate_number, 'XYZ5678')
        self.assertEqual(veh.conduction_number, '')
        self.assertEqual(reg.plate_number, 'XYZ5678')
        self.assertEqual(reg.conduction_number, '')
        # Second attempt now that a plate exists → rejected (one-time)
        resp2 = c.post('/api/accounts/me/plate-swap/', {'plate_number': 'AAA 1111'}, format='json')
        self.assertEqual(resp2.status_code, 400)

    def test_swap_rejects_duplicate_plate(self):
        from vehicles.models import Vehicle
        from accounts.models import User
        other = User.objects.create_user(email='other@x.com', full_name='Other', password='x',
                                         role='vehicle_owner', owner_type='student')
        Vehicle.objects.create(plate_number='XYZ5678', vehicle_type='car', is_authorized=True, user=other)
        owner, veh, reg = self._make_conduction_owner()
        c = APIClient(); c.force_authenticate(owner)
        resp = c.post('/api/accounts/me/plate-swap/', {'plate_number': 'XYZ 5678'}, format='json')
        self.assertEqual(resp.status_code, 400)
        veh.refresh_from_db()
        self.assertEqual(veh.conduction_number, 'CS111')  # unchanged


class DoubleParkAttributionTests(TestCase):
    """A guard names the vehicle behind a double-parking alert, issuing the
    violation; admin cannot, and an unknown identifier is refused."""

    def setUp(self):
        from accounts.models import User
        from vehicles.models import Vehicle
        self.guard = User.objects.create_user(email='dpg@slc.edu.ph', full_name='Guard',
                                              password='x', role='security')
        self.admin = User.objects.create_user(email='dpa@slc.edu.ph', full_name='Admin',
                                              password='x', role='admin')
        self.vehicle = Vehicle.objects.create(plate_number='DPK1234', vehicle_type='car', is_authorized=True)

    def _post(self, user, **data):
        c = APIClient(); c.force_authenticate(user)
        payload = dict(zone_id=1, space_ids=[1, 2], plate_number='DPK 1234')
        payload.update(data)
        return c.post('/api/vehicles/parking-zones/attribute-double-park/', payload, format='json')

    def test_guard_attribution_creates_violation(self):
        from violations.models import Violation
        resp = self._post(self.guard)
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(Violation.objects.filter(
            vehicle=self.vehicle, violation_type='double_parking').exists())

    def test_admin_cannot_attribute(self):
        self.assertEqual(self._post(self.admin).status_code, 403)

    def test_unknown_identifier_404(self):
        self.assertEqual(self._post(self.guard, plate_number='NOPE999').status_code, 404)

    def test_attribution_by_conduction(self):
        from vehicles.models import Vehicle
        from violations.models import Violation
        v = Vehicle.objects.create(conduction_number='CDN777', vehicle_type='car', is_authorized=True)
        resp = self._post(self.guard, plate_number='CDN 777')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(Violation.objects.filter(vehicle=v, violation_type='double_parking').exists())


class DailySchedulerTests(TestCase):
    """The in-process scheduler archives expired owners and applies the retention
    window once a day, on its own."""

    def setUp(self):
        from vehicles.models import SystemSettings
        cfg = SystemSettings.get()
        cfg.account_expiry_enabled = True
        cfg.account_expiry_months = 1
        cfg.account_expiry_days = 0
        cfg.save()

    def _expired_owner(self, email='sched-owner@example.com'):
        from accounts.models import User
        owner = User.objects.create_user(
            email=email, full_name='Sched Owner', password='Passw0rd!23',
            role='vehicle_owner', owner_type='student',
        )
        owner.expires_at = timezone.localdate() - timedelta(days=1)
        owner.save(update_fields=['expires_at'])
        return owner

    def test_run_due_jobs_archives_expired_owner(self):
        from vehicles.scheduler import run_due_jobs
        owner = self._expired_owner()

        run_due_jobs()

        owner.refresh_from_db()
        self.assertTrue(owner.is_archived)
        self.assertFalse(owner.is_active)

    def test_ledger_row_records_the_run(self):
        from vehicles.models import DailyJobRun
        from vehicles.scheduler import run_due_jobs

        self._expired_owner()
        run_due_jobs()

        row = DailyJobRun.objects.get(job='auto_archive_expired_accounts',
                                      run_date=timezone.localdate())
        self.assertIsNotNone(row.finished_at)
        self.assertIn('archived', row.result)

    def test_second_pass_same_day_is_skipped(self):
        from vehicles.models import DailyJobRun
        from vehicles.scheduler import DAILY_JOBS, run_due_jobs

        self._expired_owner()
        first = run_due_jobs()
        self.assertIn('auto_archive_expired_accounts', first)

        # A second owner expires after the day's run was already claimed.
        late = self._expired_owner('late-owner@example.com')
        second = run_due_jobs()

        self.assertEqual(second, {})                      # nothing ran
        self.assertEqual(DailyJobRun.objects.count(), len(DAILY_JOBS))  # no duplicate rows
        late.refresh_from_db()
        self.assertFalse(late.is_archived)                # picked up tomorrow

    def test_new_day_runs_again(self):
        from vehicles.models import DailyJobRun
        from vehicles.scheduler import DAILY_JOBS, run_due_jobs

        self._expired_owner()
        run_due_jobs()

        # Simulate yesterday's run so today's pass is due again.
        DailyJobRun.objects.update(run_date=timezone.localdate() - timedelta(days=1))
        late = self._expired_owner('tomorrow-owner@example.com')

        run_due_jobs()

        late.refresh_from_db()
        self.assertTrue(late.is_archived)
        self.assertEqual(DailyJobRun.objects.count(), len(DAILY_JOBS) * 2)

    def test_force_reruns_despite_ledger(self):
        from vehicles.scheduler import run_due_jobs
        self._expired_owner()
        run_due_jobs()

        late = self._expired_owner('forced-owner@example.com')
        out = run_due_jobs(force=True)

        self.assertIn('auto_archive_expired_accounts', out)
        late.refresh_from_db()
        self.assertTrue(late.is_archived)

    def test_job_failure_is_reported_not_raised(self):
        from unittest.mock import patch
        from vehicles.scheduler import run_due_jobs

        with patch('vehicles.tasks.auto_archive_expired_accounts', side_effect=RuntimeError('boom')):
            out = run_due_jobs()

        self.assertIn('failed: boom', out['auto_archive_expired_accounts'])

    def test_failed_job_releases_its_claim_and_retries(self):
        from unittest.mock import patch
        from vehicles.models import DailyJobRun
        from vehicles.scheduler import run_due_jobs

        owner = self._expired_owner()
        with patch('vehicles.tasks.auto_archive_expired_accounts', side_effect=RuntimeError('boom')):
            run_due_jobs()

        # The failed job released its claim, so the day is not written off as
        # done for it. The jobs that succeeded keep theirs.
        self.assertFalse(DailyJobRun.objects.filter(
            job='auto_archive_expired_accounts').exists())

        run_due_jobs()   # next hourly pass
        owner.refresh_from_db()
        self.assertTrue(owner.is_archived)
        self.assertTrue(DailyJobRun.objects.filter(
            job='auto_archive_expired_accounts').exists())

    def test_disabled_expiry_archives_nobody(self):
        from vehicles.models import SystemSettings
        from vehicles.scheduler import run_due_jobs

        cfg = SystemSettings.get()
        cfg.account_expiry_enabled = False
        cfg.save()
        owner = self._expired_owner()

        run_due_jobs()

        owner.refresh_from_db()
        self.assertFalse(owner.is_archived)

    def test_start_does_not_launch_thread_outside_server(self):
        import threading
        from vehicles import scheduler

        before = {t.name for t in threading.enumerate()}
        scheduler.start()   # test runner argv, not a server
        after = {t.name for t in threading.enumerate()}
        self.assertNotIn('daily-scheduler', after - before)

    def test_purge_runs_on_the_same_pass(self):
        """Retention is on the scheduler too — it must not need the Windows task."""
        from vehicles.models import DailyJobRun
        from vehicles.scheduler import run_due_jobs

        run_due_jobs()

        row = DailyJobRun.objects.get(job='purge_old_records',
                                      run_date=timezone.localdate())
        self.assertIsNotNone(row.finished_at)

    def test_archiving_and_purge_do_not_collide_on_one_pass(self):
        """An account archived by this pass has just started its retention window,
        so the purge in the same pass must not delete it."""
        from accounts.models import User
        from vehicles.scheduler import run_due_jobs

        owner = self._expired_owner()

        run_due_jobs()

        owner.refresh_from_db()
        self.assertTrue(owner.is_archived)
        self.assertTrue(User.objects.filter(pk=owner.pk).exists())


class RetentionPurgeTests(TestCase):
    """Retention deletes archived accounts once the window passes — and only
    archived ones."""

    def setUp(self):
        from vehicles.models import SystemSettings
        cfg = SystemSettings.get()
        cfg.retention_years = 5
        cfg.save()

    def _archived_owner(self, email, archived_days_ago):
        from accounts.models import User
        owner = User.objects.create_user(
            email=email, full_name='Old Owner', password='Passw0rd!23',
            role='vehicle_owner', owner_type='student',
        )
        owner.is_archived = True
        owner.is_active = False
        owner.archived_at = timezone.now() - timedelta(days=archived_days_ago)
        owner.save(update_fields=['is_archived', 'is_active', 'archived_at'])
        return owner

    def test_account_past_the_window_is_deleted(self):
        from accounts.models import User
        from vehicles.tasks import purge_old_records

        old = self._archived_owner('ancient@example.com', archived_days_ago=6 * 365)

        result = purge_old_records()

        self.assertEqual(result['deleted_accounts'], 1)
        self.assertFalse(User.objects.filter(pk=old.pk).exists())

    def test_account_inside_the_window_survives(self):
        from accounts.models import User
        from vehicles.tasks import purge_old_records

        recent = self._archived_owner('recent@example.com', archived_days_ago=30)

        self.assertEqual(purge_old_records()['deleted_accounts'], 0)
        self.assertTrue(User.objects.filter(pk=recent.pk).exists())

    def test_live_account_is_never_deleted(self):
        """Age alone is not grounds for deletion — only archived accounts go."""
        from accounts.models import User
        from vehicles.tasks import purge_old_records

        live = User.objects.create_user(
            email='live@example.com', full_name='Live Owner', password='Passw0rd!23',
            role='vehicle_owner', owner_type='student',
        )
        User.objects.filter(pk=live.pk).update(
            date_joined=timezone.now() - timedelta(days=10 * 365))

        self.assertEqual(purge_old_records()['deleted_accounts'], 0)
        self.assertTrue(User.objects.filter(pk=live.pk).exists())

    def test_archived_without_a_timestamp_is_left_alone(self):
        """No archived_at means no clock to measure — do not delete on a guess."""
        from accounts.models import User
        from vehicles.tasks import purge_old_records

        owner = self._archived_owner('noclock@example.com', archived_days_ago=6 * 365)
        User.objects.filter(pk=owner.pk).update(archived_at=None)

        self.assertEqual(purge_old_records()['deleted_accounts'], 0)
        self.assertTrue(User.objects.filter(pk=owner.pk).exists())

    def test_admin_is_never_purged(self):
        from accounts.models import User
        from vehicles.tasks import purge_old_records

        admin = User.objects.create_user(
            email='oldadmin@example.com', full_name='Old Admin',
            password='Passw0rd!23', role='admin')
        User.objects.filter(pk=admin.pk).update(
            is_archived=True, archived_at=timezone.now() - timedelta(days=9 * 365))

        self.assertEqual(purge_old_records()['deleted_accounts'], 0)
        self.assertTrue(User.objects.filter(pk=admin.pk).exists())

    def test_owned_records_go_with_the_account(self):
        from vehicles.models import Vehicle
        from vehicles.tasks import purge_old_records

        old = self._archived_owner('withcar@example.com', archived_days_ago=6 * 365)
        veh = Vehicle.objects.create(plate_number='OLD111', vehicle_type='car', user=old)
        reg = make_reg(registrant_type='student', full_name='Old Owner',
                       email='withcar@example.com', plate_number='OLD 111',
                       student_id='20200001', user=old,
                       status=VehicleRegistration.Status.EXPIRED)

        purge_old_records()

        self.assertFalse(Vehicle.objects.filter(pk=veh.pk).exists())
        self.assertFalse(VehicleRegistration.objects.filter(pk=reg.pk).exists())

    def test_audit_history_survives_the_purge(self):
        from accounts.models import AuditLog
        from vehicles.tasks import purge_old_records

        old = self._archived_owner('audited@example.com', archived_days_ago=6 * 365)
        entry = AuditLog.objects.create(
            actor=None, action=AuditLog.Action.USER_ARCHIVED, target_user=old,
            details='Account auto-archived on expiry',
        )

        purge_old_records()

        entry.refresh_from_db()
        self.assertIsNone(entry.target_user_id)          # SET_NULL, not cascade
        self.assertEqual(entry.details, 'Account auto-archived on expiry')


class DailyJobQueryCountTests(TestCase):
    """The daily jobs must cost a fixed number of statements, not a walk.

    Both run unattended against a remote Postgres, where a per-account round trip
    is the whole cost. These assert the count does not move between a small batch
    and a larger one — if someone reintroduces a query inside a loop, the numbers
    diverge and these fail.

    Measured flat at 1 and at 12 accounts: archiving 10 statements, the purge 30,
    a full scheduler pass 48. The purge's 30 is set by how many models hold a FK
    to User that Django must null out, not by how many accounts are being
    deleted — adding such a model moves that number, which is expected.
    """

    def setUp(self):
        from vehicles.models import SystemSettings
        cfg = SystemSettings.get()
        cfg.account_expiry_enabled = True
        cfg.account_expiry_months = 1
        cfg.retention_years = 5
        cfg.save()

    def _expired_owners(self, n, prefix):
        from accounts.models import User
        for i in range(n):
            owner = User.objects.create_user(
                email=f'{prefix}{i}@example.com', full_name=f'Owner {i}',
                password='Passw0rd!23', role='vehicle_owner', owner_type='student',
            )
            User.objects.filter(pk=owner.pk).update(
                expires_at=timezone.localdate() - timedelta(days=1))

    def _archived_owners(self, n, prefix):
        from accounts.models import User
        from vehicles.models import Vehicle
        for i in range(n):
            owner = User.objects.create_user(
                email=f'{prefix}{i}@example.com', full_name=f'Old {i}',
                password='Passw0rd!23', role='vehicle_owner', owner_type='student',
            )
            Vehicle.objects.create(plate_number=f'{prefix.upper()}{i:03d}',
                                   vehicle_type='car', user=owner)
            User.objects.filter(pk=owner.pk).update(
                is_archived=True, is_active=False,
                archived_at=timezone.now() - timedelta(days=6 * 365))

    def _count(self, fn):
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        with CaptureQueriesContext(connection) as ctx:
            fn()
        return len(ctx)

    def test_archiving_cost_does_not_grow_with_the_batch(self):
        from vehicles.tasks import auto_archive_expired_accounts

        self._expired_owners(1, 'solo')
        one = self._count(auto_archive_expired_accounts)

        self._expired_owners(12, 'many')
        twelve = self._count(auto_archive_expired_accounts)

        self.assertEqual(one, twelve,
                         f"archiving cost grew with batch size: {one} -> {twelve} queries")

    def test_purge_cost_does_not_grow_with_the_batch(self):
        from vehicles.tasks import purge_old_records

        self._archived_owners(1, 'solo')
        one = self._count(purge_old_records)

        self._archived_owners(12, 'many')
        twelve = self._count(purge_old_records)

        self.assertEqual(one, twelve,
                         f"purge cost grew with batch size: {one} -> {twelve} queries")

    def test_a_full_scheduler_pass_is_a_fixed_cost(self):
        from vehicles.models import DailyJobRun
        from vehicles.scheduler import run_due_jobs

        self._expired_owners(1, 'pass1')
        self._archived_owners(1, 'purge1')
        one = self._count(run_due_jobs)

        DailyJobRun.objects.all().delete()
        self._expired_owners(12, 'pass2')
        self._archived_owners(12, 'purge2')
        twelve = self._count(run_due_jobs)

        self.assertEqual(one, twelve,
                         f"scheduler pass cost grew with batch size: {one} -> {twelve} queries")


class ExpiryCannotBeDisabledTests(TestCase):
    """Account expiration has no off switch — only a period."""

    def setUp(self):
        from accounts.models import User
        from rest_framework.test import APIClient
        self.admin = User.objects.create_user(
            email='cdso@example.com', full_name='CDSO', password='Passw0rd!23',
            role='admin')
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def _put(self, **overrides):
        payload = {
            'retention_years': 5, 'scan_dedup_seconds': 60,
            'vehicle_pass_fee': 300, 'vehicle_pass_fee_employee': 150,
            'account_expiry_months': 12, 'account_expiry_days': 0,
        }
        payload.update(overrides)
        return self.client.put('/api/vehicles/system-settings/', payload, format='json')

    def test_zero_period_is_rejected(self):
        resp = self._put(account_expiry_months=0, account_expiry_days=0)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('account_expiry_months', resp.data)

    def test_flag_cannot_be_cleared_by_the_client(self):
        from vehicles.models import SystemSettings
        resp = self._put(account_expiry_enabled=False)
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(SystemSettings.get().account_expiry_enabled)
        self.assertTrue(resp.data['account_expiry_enabled'])

    def test_days_only_period_is_accepted(self):
        resp = self._put(account_expiry_months=0, account_expiry_days=30)
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['account_expiry_days'], 30)

    def test_owner_without_an_expiry_is_backfilled_on_save(self):
        from accounts.models import User
        owner = User.objects.create_user(
            email='noexpiry@example.com', full_name='No Expiry',
            password='Passw0rd!23', role='vehicle_owner', owner_type='student')
        User.objects.filter(pk=owner.pk).update(expires_at=None)

        self.assertEqual(self._put(account_expiry_months=6).status_code, 200)

        owner.refresh_from_db()
        self.assertIsNotNone(owner.expires_at)

    def test_existing_expiry_is_not_moved_by_a_period_change(self):
        from accounts.models import User
        owner = User.objects.create_user(
            email='fixed@example.com', full_name='Fixed', password='Passw0rd!23',
            role='vehicle_owner', owner_type='student')
        original = owner.expires_at
        self.assertIsNotNone(original)

        self.assertEqual(self._put(account_expiry_months=1).status_code, 200)

        owner.refresh_from_db()
        self.assertEqual(owner.expires_at, original)
