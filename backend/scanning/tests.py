from datetime import date, datetime
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from vehicles.models import Vehicle, SystemSettings
from violations.models import Violation
from scanning.entry_logic import check_entry
from scanning.models import AccessLog


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _make_owner(email, plate, owner_type, campus_days=None, schedule='ANY',
                is_active=True, is_authorized=True):
    user = User.objects.create_user(
        email=email,
        full_name='Test Owner',
        password='SecurePassword123!',
        role='vehicle_owner',
        owner_type=owner_type,
        schedule=schedule,
        campus_days=campus_days or [],
    )
    if not is_active:
        user.is_active = False
        user.save(update_fields=['is_active'])
    vehicle = Vehicle.objects.create(
        plate_number=plate,
        vehicle_type=Vehicle.Type.CAR,
        is_authorized=is_authorized,
        user=user,
    )
    return user, vehicle


def _make_guard(email='guard@slc.edu.ph'):
    return User.objects.create_user(
        email=email,
        full_name='Test Guard',
        password='SecurePassword123!',
        role='security',
        gate_assignment='gate1',
    )


# ── Entry logic (unit) ─────────────────────────────────────────────────────────

class PlatedVehicleSuspensionTests(TestCase):
    def test_plated_vehicle_suspended_owner_denied(self):
        user = User.objects.create_user(
            email='plated@slc.edu.ph', full_name='Plated Owner',
            password='SecurePassword123!', role='vehicle_owner',
            owner_type=User.OwnerType.STUDENT, schedule=User.Schedule.MWF,
        )
        user.user_code = 'SLC-OWN-000004'
        user.save(update_fields=['user_code'])
        user.is_active = False
        user.save(update_fields=['is_active'])
        vehicle = Vehicle.objects.create(
            plate_number='ABC1234', vehicle_type=Vehicle.Type.CAR,
            is_authorized=True, user=user,
        )
        result = check_entry(vehicle)
        self.assertEqual(result['status'], 'denied')
        self.assertFalse(result['allowed'])
        self.assertEqual(result['message'], 'Owner account is suspended/disabled.')


class EntryLogicTests(TestCase):

    def test_authorized_employee_allowed(self):
        _, vehicle = _make_owner('emp@slc.edu.ph', 'EMP001', User.OwnerType.EMPLOYEE)
        result = check_entry(vehicle)
        self.assertTrue(result['allowed'])
        self.assertIn('Employee', result['message'])

    def test_authorized_student_any_schedule_allowed(self):
        _, vehicle = _make_owner('stu@slc.edu.ph', 'STU001', User.OwnerType.STUDENT, schedule='ANY')
        result = check_entry(vehicle)
        self.assertTrue(result['allowed'])
        self.assertEqual(result['status'], 'authorized')

    def test_unauthorized_vehicle_denied(self):
        _, vehicle = _make_owner('unauth@slc.edu.ph', 'UNAUTH1', User.OwnerType.STUDENT,
                                  is_authorized=False)
        result = check_entry(vehicle)
        self.assertFalse(result['allowed'])
        self.assertEqual(result['status'], 'denied')

    def test_vehicle_with_no_owner_reads_as_unregistered(self):
        """Ownerless (gate-created) vehicles with no active pass read as
        unregistered — same status the plate had before any pass was issued."""
        vehicle = Vehicle.objects.create(
            plate_number='NOOWN1', vehicle_type=Vehicle.Type.CAR, is_authorized=True,
        )
        result = check_entry(vehicle)
        self.assertFalse(result['allowed'])
        self.assertEqual(result['status'], 'unknown')
        self.assertIn('not registered', result['message'].lower())

    def test_visitor_owner_account_still_gets_no_pass(self):
        """Registered visitor-type accounts (with a User) keep the 'no_pass' status."""
        _, vehicle = _make_owner('visacct@slc.edu.ph', 'VISACC1', User.OwnerType.VISITOR)
        result = check_entry(vehicle)
        self.assertFalse(result['allowed'])
        self.assertEqual(result['status'], 'no_pass')
        self.assertIn('visitor pass', result['message'].lower())

    def test_confiscated_account_is_denied_entry(self):
        """The fine that used to block entry is gone; the penalty is the
        account being confiscated, and that is what the gate checks."""
        from violations.penalty import apply_penalty
        owner, vehicle = _make_owner('conf@slc.edu.ph', 'CONF01',
                                     User.OwnerType.STUDENT, schedule='ANY')
        apply_penalty(Violation.objects.create(
            vehicle=vehicle, owner=owner,
            violation_type=Violation.Type.UNAUTHORIZED_ENTRY,
            offense_number=1,
        ))
        owner.refresh_from_db()
        self.assertTrue(owner.is_confiscated)

        result = check_entry(vehicle)
        self.assertFalse(result['allowed'])
        self.assertEqual(result['status'], 'confiscated')
        self.assertIn('confiscated', result['message'].lower())

    def test_expired_confiscation_lets_the_owner_back_in(self):
        """The penalty ends on its own — nothing has to run to release it."""
        from datetime import timedelta
        from django.utils import timezone as tz
        owner, vehicle = _make_owner('expired@slc.edu.ph', 'CONF02',
                                     User.OwnerType.STUDENT, schedule='ANY')
        owner.confiscation_level = 1
        owner.confiscated_until = tz.localdate() - timedelta(days=1)
        owner.save(update_fields=['confiscation_level', 'confiscated_until'])

        self.assertFalse(owner.is_confiscated)
        self.assertTrue(check_entry(vehicle)['allowed'])

    def test_open_campus_mode_bypasses_all_rules(self):
        # Unauthorized vehicle + inactive owner — still allowed in open campus mode,
        # with the client-facing status 'open_entry' (displayed as "Open Entry")
        _, vehicle = _make_owner('open@slc.edu.ph', 'OPEN01', User.OwnerType.STUDENT,
                                  is_authorized=False, is_active=False)
        settings = SystemSettings.get()
        settings.open_campus_mode = True
        settings.save()
        result = check_entry(vehicle)
        self.assertTrue(result['allowed'])
        self.assertEqual(result['status'], 'open_entry')

    def test_student_wrong_campus_day_denied(self):
        """Student registered only on Monday is denied on a Tuesday (2026-07-07)."""
        _, vehicle = _make_owner('mwf@slc.edu.ph', 'MWF001', User.OwnerType.STUDENT,
                                  campus_days=['Monday'])
        tuesday = date(2026, 7, 7)
        with patch('scanning.entry_logic.timezone') as mock_tz:
            mock_tz.localdate.return_value = tuesday
            mock_tz.localtime.return_value = timezone.localtime()
            result = check_entry(vehicle)
        self.assertFalse(result['allowed'])
        self.assertEqual(result['status'], 'wrong_day')

    def test_student_correct_campus_day_allowed(self):
        """Same Monday-only student is allowed on a Monday (2026-07-06)."""
        _, vehicle = _make_owner('mwf2@slc.edu.ph', 'MWF002', User.OwnerType.STUDENT,
                                  campus_days=['Monday'])
        monday = date(2026, 7, 6)
        with patch('scanning.entry_logic.timezone') as mock_tz:
            mock_tz.localdate.return_value = monday
            mock_tz.localtime.return_value = timezone.localtime()
            result = check_entry(vehicle)
        self.assertTrue(result['allowed'])

    def test_authorized_fetcher_allowed(self):
        """Fetcher inside the seeded rule window (Mon–Sat 06:00–19:00) is allowed.
        Time is frozen to Monday 10:00 because migration 0015 seeds a fetcher rule."""
        _, vehicle = _make_owner('fetch@slc.edu.ph', 'FTECH1', User.OwnerType.FETCHER, schedule='ANY')
        monday_10am = timezone.make_aware(datetime(2026, 7, 6, 10, 0))
        with patch('scanning.entry_logic.timezone') as mock_tz:
            mock_tz.localdate.return_value = date(2026, 7, 6)
            mock_tz.localtime.return_value = monday_10am
            result = check_entry(vehicle)
        self.assertTrue(result['allowed'])
        self.assertIn('Fetcher', result['message'])


# ── Manual entry API (integration) ────────────────────────────────────────────

class ManualEntryAPITests(TestCase):
    """POST /api/scan/manual-entry/ — guard types a plate, system evaluates it."""

    def setUp(self):
        self.guard = _make_guard()
        self.client = APIClient()
        self.client.force_authenticate(user=self.guard)

    def test_known_authorized_plate_returns_authorized(self):
        _make_owner('api@slc.edu.ph', 'API001', User.OwnerType.EMPLOYEE)
        resp = self.client.post('/api/scan/manual-entry/', {'plate_number': 'API001'}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data['allowed'])
        self.assertEqual(resp.data['status'], 'authorized')

    def test_unknown_plate_returns_unknown_status(self):
        resp = self.client.post('/api/scan/manual-entry/', {'plate_number': 'ZZZ9999'}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data['allowed'])
        self.assertEqual(resp.data['status'], 'unknown')

    def test_authorized_scan_creates_access_log(self):
        _make_owner('log@slc.edu.ph', 'LOG001', User.OwnerType.EMPLOYEE)
        self.client.post('/api/scan/manual-entry/', {'plate_number': 'LOG001'}, format='json')
        self.assertTrue(AccessLog.objects.filter(plate_number='LOG001').exists())

    def test_denied_entry_auto_logs_violation(self):
        _, vehicle = _make_owner('den@slc.edu.ph', 'DEN001', User.OwnerType.STUDENT,
                                  is_authorized=False)
        self.client.post('/api/scan/manual-entry/', {'plate_number': 'DEN001'}, format='json')
        self.assertTrue(Violation.objects.filter(vehicle=vehicle).exists())

    def test_auto_violation_deduplicated_within_5_minutes(self):
        """Two denied scans in quick succession must produce only one auto-violation.

        The cap is per ACCOUNT per day, not per type. The first denied scan
        confiscates the owner, so a per-type cap would let the second scan
        through as "activity while confiscated" and spend two rungs of the
        ladder on one incident.
        """
        owner, vehicle = _make_owner('dup@slc.edu.ph', 'DUP001', User.OwnerType.STUDENT,
                                     is_authorized=False)
        self.client.post('/api/scan/manual-entry/', {'plate_number': 'DUP001'}, format='json')
        self.client.post('/api/scan/manual-entry/', {'plate_number': 'DUP001'}, format='json')
        self.assertEqual(Violation.objects.filter(vehicle=vehicle).count(), 1)

        owner.refresh_from_db()
        self.assertEqual(owner.confiscation_level, 1)   # one incident, one strike

    def test_missing_plate_number_returns_400(self):
        resp = self.client.post('/api/scan/manual-entry/', {}, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_unauthenticated_request_rejected(self):
        client = APIClient()
        resp = client.post('/api/scan/manual-entry/', {'plate_number': 'AUTH01'}, format='json')
        self.assertEqual(resp.status_code, 401)


# ── Open Campus Mode at the gate (integration) ────────────────────────────────

class OpenCampusModeAPITests(TestCase):
    """In Open Campus Mode plates are still read and logged, but the lookup
    status shown at the gate is 'open_entry' ("Open Entry")."""

    def setUp(self):
        self.guard = _make_guard()
        self.client = APIClient()
        self.client.force_authenticate(user=self.guard)
        settings = SystemSettings.get()
        settings.open_campus_mode = True
        settings.save()

    def test_registered_vehicle_gets_open_entry_status(self):
        _make_owner('oc@slc.edu.ph', 'OCA001', User.OwnerType.STUDENT)
        resp = self.client.post('/api/scan/manual-entry/', {'plate_number': 'OCA001'}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data['allowed'])
        self.assertEqual(resp.data['status'], 'open_entry')

    def test_open_entry_logged_as_authorized_for_pairing(self):
        # The AccessLog row must stay 'authorized' so exit pairing / stats work
        _make_owner('oc2@slc.edu.ph', 'OCA002', User.OwnerType.STUDENT)
        self.client.post('/api/scan/manual-entry/', {'plate_number': 'OCA002'}, format='json')
        log = AccessLog.objects.get(plate_number='OCA002')
        self.assertEqual(log.status, AccessLog.Status.AUTHORIZED)
        self.assertEqual(log.gate_id, 'gate1')  # guard's assigned gate

    def test_unregistered_plate_admitted_with_open_entry(self):
        resp = self.client.post('/api/scan/manual-entry/', {'plate_number': 'ZZZ9999'}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data['allowed'])
        self.assertEqual(resp.data['status'], 'open_entry')
        log = AccessLog.objects.get(plate_number='ZZZ9999')
        self.assertEqual(log.status, AccessLog.Status.AUTHORIZED)

    def test_no_violation_issued_in_open_campus_mode(self):
        _, vehicle = _make_owner('oc3@slc.edu.ph', 'OCA003', User.OwnerType.STUDENT,
                                  is_authorized=False)
        self.client.post('/api/scan/manual-entry/', {'plate_number': 'OCA003'}, format='json')
        self.assertFalse(Violation.objects.filter(vehicle=vehicle).exists())

    def test_regular_day_unregistered_plate_still_unknown(self):
        settings = SystemSettings.get()
        settings.open_campus_mode = False
        settings.save()
        resp = self.client.post('/api/scan/manual-entry/', {'plate_number': 'ZZZ8888'}, format='json')
        self.assertFalse(resp.data['allowed'])
        self.assertEqual(resp.data['status'], 'unknown')

    def test_gate4_guard_scans_land_in_gate4_log(self):
        guard4 = _make_guard('guard4@slc.edu.ph')
        guard4.gate_assignment = 'gate4'
        guard4.save(update_fields=['gate_assignment'])
        client4 = APIClient()
        client4.force_authenticate(user=guard4)
        _make_owner('oc4@slc.edu.ph', 'OCA004', User.OwnerType.STUDENT)
        resp = client4.post('/api/scan/manual-entry/', {'plate_number': 'OCA004'}, format='json')
        self.assertEqual(resp.data['status'], 'open_entry')
        self.assertEqual(resp.data['gate_id'], 'gate4')
        self.assertEqual(AccessLog.objects.get(plate_number='OCA004').gate_id, 'gate4')


# ── Visitor pass API (integration) ────────────────────────────────────────────

class VisitorPassAPITests(TestCase):
    """POST /api/scan/visitor-pass/ and the exit endpoint."""

    def setUp(self):
        self.guard = _make_guard()
        self.client = APIClient()
        self.client.force_authenticate(user=self.guard)

    def _create_pass(self, plate='VIS001'):
        return self.client.post(
            '/api/scan/visitor-pass/',
            {'plate_number': plate, 'purpose': 'Delivery', 'allowed_duration': 30},
            format='json',
        )

    def test_create_visitor_pass_returns_201(self):
        resp = self._create_pass('VIS001')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['plate_number'], 'VIS001')

    def test_create_pass_does_not_log_entry_until_printed(self):
        """The visitor's entry is only logged once the guard confirms the slip
        printed — creating the pass alone must not create an AccessLog."""
        resp = self._create_pass('VIS002')
        self.assertFalse(AccessLog.objects.filter(plate_number='VIS002').exists())

        printed = self.client.post(f"/api/scan/visitor-pass/{resp.data['id']}/printed/")
        self.assertEqual(printed.status_code, 200)
        self.assertTrue(AccessLog.objects.filter(plate_number='VIS002', status='authorized').exists())

    def test_visitor_exit_by_slip_qr(self):
        """Scanning the slip QR (SLC-VISITOR:{id}) records the exit."""
        resp = self._create_pass('VIS010')
        pass_id = resp.data['id']
        self.client.post(f'/api/scan/visitor-pass/{pass_id}/printed/')
        exit_resp = self.client.post(
            '/api/scan/visitor-pass/exit-scan/',
            {'qr_data': f'SLC-VISITOR:{pass_id}'},
            format='json',
        )
        self.assertEqual(exit_resp.status_code, 200)
        self.assertEqual(exit_resp.data['status'], 'exited')
        self.assertTrue(AccessLog.objects.filter(plate_number='VIS010', status='exited').exists())

    def test_exit_visitor_pass_marks_exited(self):
        create_resp = self._create_pass('VIS003')
        pass_id = create_resp.data['id']
        exit_resp = self.client.post(f'/api/scan/visitor-pass/{pass_id}/exit/')
        self.assertEqual(exit_resp.status_code, 200)
        self.assertEqual(exit_resp.data['status'], 'exited')

    def test_double_exit_returns_400(self):
        create_resp = self._create_pass('VIS004')
        pass_id = create_resp.data['id']
        self.client.post(f'/api/scan/visitor-pass/{pass_id}/exit/')
        resp = self.client.post(f'/api/scan/visitor-pass/{pass_id}/exit/')
        self.assertEqual(resp.status_code, 400)

    def test_missing_plate_number_returns_400(self):
        resp = self.client.post('/api/scan/visitor-pass/', {'purpose': 'Visit'}, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_exited_visitor_plate_reverts_to_unregistered(self):
        """After the pass is used and the visitor exits, the same plate reads
        as unregistered again — ready for a fresh visitor-pass cycle."""
        create_resp = self._create_pass('VIS009')
        pass_id = create_resp.data['id']

        vehicle = Vehicle.objects.get(plate_number='VIS009')
        self.assertEqual(check_entry(vehicle)['status'], 'authorized')  # pass active

        self.client.post(f'/api/scan/visitor-pass/{pass_id}/exit/')
        result = check_entry(vehicle)
        self.assertFalse(result['allowed'])
        self.assertEqual(result['status'], 'unknown')

    def test_unknown_visitor_result_never_issues_violation(self):
        """An ownerless vehicle scanned without a pass must not be auto-fined."""
        vehicle = Vehicle.objects.create(
            plate_number='VIS010', vehicle_type=Vehicle.Type.CAR, is_authorized=False,
        )
        self.client.post('/api/scan/manual-entry/', {'plate_number': 'VIS010'}, format='json')
        self.assertFalse(Violation.objects.filter(vehicle=vehicle).exists())


# ── Deny entry API (integration) ──────────────────────────────────────────────

class DenyEntryAPITests(TestCase):
    """POST /api/scan/deny/ — guard refuses a visitor/unregistered plate."""

    def setUp(self):
        self.guard = _make_guard()
        self.client = APIClient()
        self.client.force_authenticate(user=self.guard)

    def test_deny_creates_denied_log_at_guard_gate(self):
        resp = self.client.post('/api/scan/deny/',
                                {'plate_number': 'DNY0001', 'reason': 'No valid purpose'},
                                format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['status'], 'denied')
        log = AccessLog.objects.get(plate_number='DNY0001')
        self.assertEqual(log.status, AccessLog.Status.DENIED)
        self.assertIn('No valid purpose', log.denied_reason)
        self.assertEqual(log.gate_id, 'gate1')

    def test_deny_without_reason_uses_default(self):
        resp = self.client.post('/api/scan/deny/', {'plate_number': 'DNY0002'}, format='json')
        self.assertEqual(resp.status_code, 200)
        log = AccessLog.objects.get(plate_number='DNY0002')
        self.assertIn('denied at gate', log.denied_reason.lower())

    def test_deny_missing_plate_returns_400(self):
        resp = self.client.post('/api/scan/deny/', {}, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_deny_does_not_issue_violation(self):
        vehicle = Vehicle.objects.create(
            plate_number='DNY0003', vehicle_type=Vehicle.Type.CAR, is_authorized=False,
        )
        self.client.post('/api/scan/deny/', {'plate_number': 'DNY0003'}, format='json')
        self.assertFalse(Violation.objects.filter(vehicle=vehicle).exists())

    def test_deny_unauthenticated_rejected(self):
        client = APIClient()
        resp = client.post('/api/scan/deny/', {'plate_number': 'DNY0004'}, format='json')
        self.assertEqual(resp.status_code, 401)
