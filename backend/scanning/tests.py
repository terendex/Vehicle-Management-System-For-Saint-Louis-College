from datetime import date, datetime, timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from vehicles.models import Vehicle, SystemSettings
from violations.models import Violation
from scanning.entry_logic import check_entry
from scanning.models import AccessLog
from time_utils import day_start


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


class RuleConstraintDayCeilingTests(TestCase):
    """The rule's Allowed Days is a campus-wide ceiling: an owner needs the day
    on both the rule and their own campus_days. These used to pass no matter
    what the rule said, because only campus_days was consulted."""

    MONDAY = date(2026, 7, 6)

    def _check_on_monday(self, vehicle):
        monday_10am = timezone.make_aware(datetime(2026, 7, 6, 10, 0))
        with patch('scanning.entry_logic.timezone') as mock_tz:
            mock_tz.localdate.return_value = self.MONDAY
            mock_tz.localtime.return_value = monday_10am
            return check_entry(vehicle)

    def _set_rule_days(self, constraint_type, days):
        from vehicles.models import RuleConstraint
        rule = RuleConstraint.objects.filter(
            constraint_type=constraint_type, enabled=True).first()
        self.assertIsNotNone(rule, f'no seeded {constraint_type} rule to edit')
        rule.days = days
        rule.save(update_fields=['days'])
        return rule

    def test_student_denied_when_rule_excludes_today(self):
        """Student registered for Monday, but the campus rule is closed Monday."""
        _, vehicle = _make_owner('ceil1@slc.edu.ph', 'CEIL01', User.OwnerType.STUDENT,
                                 campus_days=['Monday'])
        rule = self._set_rule_days('student_vehicle', ['tue', 'wed', 'thu', 'fri'])
        result = self._check_on_monday(vehicle)
        self.assertFalse(result['allowed'])
        self.assertEqual(result['status'], 'wrong_day')
        self.assertIn(rule.name, result['message'])

    def test_student_allowed_when_rule_and_campus_days_agree(self):
        _, vehicle = _make_owner('ceil2@slc.edu.ph', 'CEIL02', User.OwnerType.STUDENT,
                                 campus_days=['Monday'])
        self._set_rule_days('student_vehicle', ['mon', 'tue', 'wed', 'thu', 'fri'])
        self.assertTrue(self._check_on_monday(vehicle)['allowed'])

    def test_student_still_denied_on_unregistered_day_the_rule_allows(self):
        """The owner's own days keep restricting inside the ceiling."""
        _, vehicle = _make_owner('ceil3@slc.edu.ph', 'CEIL03', User.OwnerType.STUDENT,
                                 campus_days=['Tuesday'])
        self._set_rule_days('student_vehicle', ['mon', 'tue'])
        result = self._check_on_monday(vehicle)
        self.assertEqual(result['status'], 'wrong_day')
        self.assertIn('Registered days', result['message'])

    def test_fetcher_denied_when_rule_excludes_today(self):
        _, vehicle = _make_owner('ceil4@slc.edu.ph', 'CEIL04', User.OwnerType.FETCHER,
                                 schedule='ANY')
        rule = self._set_rule_days('fetcher', ['sat'])
        result = self._check_on_monday(vehicle)
        self.assertFalse(result['allowed'])
        self.assertEqual(result['status'], 'wrong_day')
        self.assertIn(rule.name, result['message'])


class OvernightWindowTests(TestCase):
    """A window whose end is before its start wraps past midnight. It used to
    compare as start <= now <= end and deny entry every minute of the day."""

    class _Rule:
        def __init__(self, start, end):
            self.start_time, self.end_time = start, end

    def _at(self, hour, minute=0):
        return timezone.make_aware(datetime(2026, 7, 6, hour, minute))

    def test_overnight_window_covers_both_sides_of_midnight(self):
        from scanning.entry_logic import _is_within_window
        rule = self._Rule('20:00', '06:00')
        self.assertTrue(_is_within_window(rule, self._at(21)))   # before midnight
        self.assertTrue(_is_within_window(rule, self._at(2)))    # after midnight
        self.assertTrue(_is_within_window(rule, self._at(20)))   # on the boundary
        self.assertTrue(_is_within_window(rule, self._at(6)))
        self.assertFalse(_is_within_window(rule, self._at(12)))  # midday gap

    def test_normal_window_is_unchanged(self):
        from scanning.entry_logic import _is_within_window
        rule = self._Rule('06:00', '19:00')
        self.assertTrue(_is_within_window(rule, self._at(10)))
        self.assertFalse(_is_within_window(rule, self._at(21)))
        self.assertFalse(_is_within_window(rule, self._at(3)))


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


class ConductionNumberLookupTests(TestCase):
    """A brand-new car carries a conduction sticker, not a plate.

    ManualEntryView has always resolved one — it looks the identifier up before
    it validates the format — but the guard's field refused to send it, so the
    only way past a missed detection was blocked for exactly the vehicles whose
    plates cannot be read: the ones that do not have plates yet. These pin the
    server half of that path, including the space the input auto-inserts while
    the guard types.
    """

    def setUp(self):
        self.guard = _make_guard('conduction-guard@slc.edu.ph')
        self.client = APIClient()
        self.client.force_authenticate(user=self.guard)
        owner = User.objects.create_user(
            email='newcar@slc.edu.ph', full_name='New Car Owner',
            password='SecurePassword123!', role='vehicle_owner',
            owner_type=User.OwnerType.EMPLOYEE, schedule='ANY',
        )
        self.vehicle = Vehicle.objects.create(
            conduction_number='CS12345A678', vehicle_type=Vehicle.Type.CAR,
            is_authorized=True, user=owner,
        )

    def _lookup(self, identifier):
        return self.client.post('/api/scan/manual-entry/',
                                {'plate_number': identifier}, format='json')

    def test_conduction_number_identifies_the_vehicle(self):
        res = self._lookup('CS12345A678')
        self.assertEqual(res.status_code, 200)
        # Identified, whatever the schedule rules then decide about entry.
        self.assertIsNotNone(res.data.get('vehicle'))
        self.assertEqual(res.data['vehicle']['conduction_number'], 'CS12345A678')

    def test_the_space_the_field_inserts_is_ignored(self):
        """formatPlateNumber turns "CS12345A678" into "CS 12345A678" as it is
        typed; the server normalises it back out."""
        res = self._lookup('CS 12345A678')
        self.assertEqual(res.status_code, 200)
        self.assertIsNotNone(res.data.get('vehicle'))

    def test_lowercase_is_accepted(self):
        res = self._lookup('cs12345a678')
        self.assertEqual(res.status_code, 200)
        self.assertIsNotNone(res.data.get('vehicle'))

    def test_free_text_is_still_rejected(self):
        res = self._lookup('NOT A PLATE')
        self.assertEqual(res.status_code, 400)
        self.assertIn('Invalid plate format', res.data['error'])


# ── Access log list (admin Vehicle Log + guard Vehicle Log) ────────────────────

class AccessLogFilterAPITests(TestCase):
    """/scan/logs/ backs both Vehicle Log screens: the guard's (one gate, one
    day) and the CDSO's (all gates, a date range, searchable)."""

    def setUp(self):
        self.guard = _make_guard()
        self.admin = User.objects.create_user(
            email='cdso-logs@slc.edu.ph', full_name='Test CDSO',
            password='SecurePassword123!', role='admin',
        )
        self.owner, self.vehicle = _make_owner(
            'logowner@slc.edu.ph', 'ABC 1234', 'employee')
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

        self.today = timezone.localdate()
        self.old_day = self.today - timedelta(days=5)

    def _log(self, plate, status, gate_id='gate1', day=None, vehicle=None,
             paired_entry=None, minutes_past_midnight=8 * 60):
        log = AccessLog.objects.create(
            plate_number=plate, status=status, gate_id=gate_id,
            vehicle=vehicle, paired_entry=paired_entry,
            on_duty_guard=self.guard, scanned_by=self.guard,
        )
        # scanned_at is auto_now_add, so it can only be backdated by an UPDATE.
        # A fixed offset from local midnight keeps the row inside `day` however
        # close to midnight the suite happens to run.
        when = day_start(day or self.today) + timedelta(minutes=minutes_past_midnight)
        AccessLog.objects.filter(pk=log.pk).update(scanned_at=when)
        log.refresh_from_db()
        return log

    def _get(self, **params):
        res = self.client.get('/api/scan/logs/', params)
        self.assertEqual(res.status_code, 200)
        return res.data

    def test_date_range_excludes_rows_outside_it(self):
        self._log('ABC 1234', AccessLog.Status.AUTHORIZED, day=self.today)
        self._log('OLD 1111', AccessLog.Status.AUTHORIZED, day=self.old_day)

        plates = [row['plate_number'] for row in self._get(
            date_from=self.today.isoformat(), date_to=self.today.isoformat())]
        self.assertEqual(plates, ['ABC 1234'])

    def test_date_range_includes_both_bounds(self):
        self._log('ABC 1234', AccessLog.Status.AUTHORIZED, day=self.today)
        self._log('OLD 1111', AccessLog.Status.AUTHORIZED, day=self.old_day)

        plates = {row['plate_number'] for row in self._get(
            date_from=self.old_day.isoformat(), date_to=self.today.isoformat())}
        self.assertEqual(plates, {'ABC 1234', 'OLD 1111'})

    def test_malformed_date_range_is_ignored_not_a_500(self):
        self._log('ABC 1234', AccessLog.Status.AUTHORIZED)
        rows = self._get(date_from='not-a-date', date_to='13/45/2026')
        self.assertEqual(len(rows), 1)

    def test_search_matches_plate_case_insensitively(self):
        self._log('ABC 1234', AccessLog.Status.AUTHORIZED)
        self._log('XYZ 9999', AccessLog.Status.AUTHORIZED)

        plates = [row['plate_number'] for row in self._get(search='abc')]
        self.assertEqual(plates, ['ABC 1234'])

    def test_search_matches_the_vehicle_owner(self):
        self._log('ABC 1234', AccessLog.Status.AUTHORIZED, vehicle=self.vehicle)
        self._log('XYZ 9999', AccessLog.Status.AUTHORIZED)

        plates = [row['plate_number'] for row in self._get(search='Test Owner')]
        self.assertEqual(plates, ['ABC 1234'])

    def test_search_matches_the_guard_on_duty(self):
        self._log('ABC 1234', AccessLog.Status.AUTHORIZED)
        rows = self._get(search='Test Guard')
        self.assertEqual(len(rows), 1)

    def test_gate_filter_still_scopes_to_one_gate(self):
        self._log('ABC 1234', AccessLog.Status.AUTHORIZED, gate_id='gate1')
        self._log('XYZ 9999', AccessLog.Status.AUTHORIZED, gate_id='gate4')

        plates = [row['plate_number'] for row in self._get(gate_id='gate4')]
        self.assertEqual(plates, ['XYZ 9999'])

    def test_exit_folds_into_its_entry_with_a_duration(self):
        entry = self._log('ABC 1234', AccessLog.Status.AUTHORIZED,
                          vehicle=self.vehicle, minutes_past_midnight=8 * 60)
        self._log('ABC 1234', AccessLog.Status.EXITED, vehicle=self.vehicle,
                  paired_entry=entry, minutes_past_midnight=9 * 60 + 30)

        rows = self._get()
        self.assertEqual(len(rows), 1)          # one visit, one row
        self.assertEqual(rows[0]['id'], entry.id)
        self.assertEqual(rows[0]['duration_minutes'], 90)

    def test_search_keeps_the_exit_paired_to_its_entry(self):
        """Search filters entry and exit rows alike, so a matching visit still
        collapses into a single row rather than showing an orphan entry."""
        entry = self._log('ABC 1234', AccessLog.Status.AUTHORIZED,
                          vehicle=self.vehicle, minutes_past_midnight=8 * 60)
        self._log('ABC 1234', AccessLog.Status.EXITED, vehicle=self.vehicle,
                  paired_entry=entry, minutes_past_midnight=9 * 60)

        rows = self._get(search='ABC')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['duration_minutes'], 60)

    def test_limit_is_clamped_and_bad_values_fall_back(self):
        for i in range(3):
            self._log(f'AAA 000{i}', AccessLog.Status.AUTHORIZED)

        self.assertEqual(len(self._get(limit=1)), 1)
        self.assertEqual(len(self._get(limit=99999)), 3)   # clamped to 1000
        self.assertEqual(len(self._get(limit='banana')), 3)  # falls back to 200

    def test_anonymous_callers_are_rejected(self):
        res = APIClient().get('/api/scan/logs/')
        self.assertIn(res.status_code, (401, 403))


class VehicleLogReportAPITests(TestCase):
    """The two Vehicle Log reports. They re-run the screen's filters server-side,
    so what matters is that they agree with the table and stay CDSO-only."""

    def setUp(self):
        self.guard = _make_guard()
        self.admin = User.objects.create_user(
            email='cdso-reports@slc.edu.ph', full_name='Test CDSO',
            password='SecurePassword123!', role='admin',
        )
        self.owner, self.vehicle = _make_owner(
            'reportowner@slc.edu.ph', 'ABC 1234', 'employee')
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.today = timezone.localdate()

    def _log(self, plate, status, gate_id='gate1', day=None, vehicle=None,
             paired_entry=None, minutes_past_midnight=8 * 60, **extra):
        log = AccessLog.objects.create(
            plate_number=plate, status=status, gate_id=gate_id,
            vehicle=vehicle, paired_entry=paired_entry,
            on_duty_guard=self.guard, scanned_by=self.guard, **extra,
        )
        when = day_start(day or self.today) + timedelta(minutes=minutes_past_midnight)
        AccessLog.objects.filter(pk=log.pk).update(scanned_at=when)
        log.refresh_from_db()
        return log

    # ── Access ────────────────────────────────────────────────────────────

    def test_reports_are_cdso_only(self):
        """A guard sees their own gate's log on screen but cannot export it."""
        client = APIClient()
        client.force_authenticate(user=self.guard)
        self.assertEqual(client.get('/api/scan/logs/export/').status_code, 403)
        self.assertEqual(client.get('/api/scan/logs/export-pdf/').status_code, 403)

    def test_reports_reject_anonymous_callers(self):
        client = APIClient()
        self.assertIn(client.get('/api/scan/logs/export/').status_code, (401, 403))
        self.assertIn(client.get('/api/scan/logs/export-pdf/').status_code, (401, 403))

    # ── File shape ────────────────────────────────────────────────────────

    def test_excel_report_downloads_as_a_workbook(self):
        self._log('ABC 1234', AccessLog.Status.AUTHORIZED, vehicle=self.vehicle)
        res = self.client.get('/api/scan/logs/export/')
        self.assertEqual(res.status_code, 200)
        self.assertIn('spreadsheetml', res['Content-Type'])
        self.assertIn('Vehicle Log Report', res['Content-Disposition'])
        self.assertTrue(res.content.startswith(b'PK'))   # xlsx is a zip

    def test_pdf_report_downloads_as_a_pdf(self):
        self._log('ABC 1234', AccessLog.Status.AUTHORIZED, vehicle=self.vehicle)
        res = self.client.get('/api/scan/logs/export-pdf/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'application/pdf')
        self.assertIn('Vehicle Log Report', res['Content-Disposition'])
        self.assertTrue(res.content.startswith(b'%PDF'))

    def test_an_empty_result_still_produces_a_report(self):
        """No rows is a valid answer to a filter — not an error page."""
        res = self.client.get('/api/scan/logs/export-pdf/', {'search': 'NOTHING MATCHES'})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.content.startswith(b'%PDF'))

    # ── Rows match the screen ─────────────────────────────────────────────

    def _rows(self, **params):
        """Build the report rows the same way the views do, so the assertions can
        read the content instead of parsing a PDF."""
        from django.test import RequestFactory
        from scanning.views import _vehicle_log_report_data
        request = RequestFactory().get('/api/scan/logs/export/', params)
        request.user = self.admin
        # DRF's query_params is just request.GET on a plain HttpRequest.
        request.query_params = request.GET
        return _vehicle_log_report_data(request)

    def test_rows_carry_the_visit_with_its_duration(self):
        entry = self._log('ABC 1234', AccessLog.Status.AUTHORIZED, vehicle=self.vehicle,
                          minutes_past_midnight=8 * 60)
        self._log('ABC 1234', AccessLog.Status.EXITED, vehicle=self.vehicle,
                  paired_entry=entry, minutes_past_midnight=10 * 60)

        rows, _ = self._rows()
        self.assertEqual(len(rows), 1)          # one visit, one row
        self.assertEqual(rows[0][2], 'ABC 1234')
        self.assertEqual(rows[0][3], 'Test Owner')
        self.assertEqual(rows[0][9], '2h')      # duration column

    def test_a_vehicle_with_no_exit_is_marked_still_inside(self):
        self._log('ABC 1234', AccessLog.Status.AUTHORIZED, vehicle=self.vehicle)
        rows, _ = self._rows()
        self.assertEqual(rows[0][8], '')                  # no exit time
        self.assertIn('Still inside', rows[0][10])        # remarks

    def test_override_and_denied_reason_reach_the_remarks_column(self):
        self._log('XYZ 9999', AccessLog.Status.DENIED,
                  denied_reason='Vehicle not registered',
                  is_override=True, override_reason='Cleared by CDSO')
        rows, _ = self._rows()
        self.assertIn('Cleared by CDSO', rows[0][10])
        self.assertIn('Vehicle not registered', rows[0][10])

    def test_status_filter_is_applied_after_the_visit_merge(self):
        """Filtering to Authorized must not resurrect the exit row that was
        folded into its entry."""
        entry = self._log('ABC 1234', AccessLog.Status.AUTHORIZED, vehicle=self.vehicle,
                          minutes_past_midnight=8 * 60)
        self._log('ABC 1234', AccessLog.Status.EXITED, vehicle=self.vehicle,
                  paired_entry=entry, minutes_past_midnight=9 * 60)
        self._log('XYZ 9999', AccessLog.Status.DENIED)

        rows, _ = self._rows(status='authorized')
        self.assertEqual([r[2] for r in rows], ['ABC 1234'])
        self.assertEqual(rows[0][9], '1h')     # still paired to its exit

        rows, _ = self._rows(status='denied')
        self.assertEqual([r[2] for r in rows], ['XYZ 9999'])

    def test_denied_group_covers_wrong_day_too(self):
        self._log('AAA 1111', AccessLog.Status.DENIED)
        self._log('BBB 2222', AccessLog.Status.WRONG_DAY)
        self._log('CCC 3333', AccessLog.Status.AUTHORIZED)

        rows, _ = self._rows(status='denied')
        self.assertEqual({r[2] for r in rows}, {'AAA 1111', 'BBB 2222'})

    def test_screen_filters_narrow_the_report_the_same_way(self):
        self._log('ABC 1234', AccessLog.Status.AUTHORIZED, gate_id='gate1', vehicle=self.vehicle)
        self._log('XYZ 9999', AccessLog.Status.AUTHORIZED, gate_id='gate4')
        self._log('OLD 1111', AccessLog.Status.AUTHORIZED, gate_id='gate1',
                  day=self.today - timedelta(days=5))

        rows, _ = self._rows(gate_id='gate1', date_from=self.today.isoformat(),
                             date_to=self.today.isoformat())
        self.assertEqual([r[2] for r in rows], ['ABC 1234'])

        rows, _ = self._rows(search='Test Owner')
        self.assertEqual([r[2] for r in rows], ['ABC 1234'])

    def test_the_subtitle_spells_out_the_active_filters(self):
        """The reader of a filtered report has to be able to see it was filtered."""
        self._log('ABC 1234', AccessLog.Status.AUTHORIZED, vehicle=self.vehicle)
        _, desc = self._rows(gate_id='gate1', search='ABC', status='authorized',
                             date_from=self.today.isoformat())
        joined = '; '.join(desc)
        self.assertIn('Gate:', joined)
        self.assertIn("Search: 'ABC'", joined)
        self.assertIn('Status: Authorized', joined)
        self.assertIn('Period:', joined)

    def test_unfiltered_reports_say_so(self):
        _, desc = self._rows()
        self.assertEqual(desc, [])
