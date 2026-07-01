from datetime import date
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

    def test_vehicle_with_no_owner_denied(self):
        vehicle = Vehicle.objects.create(
            plate_number='NOOWN1', vehicle_type=Vehicle.Type.CAR, is_authorized=True,
        )
        result = check_entry(vehicle)
        self.assertFalse(result['allowed'])
        self.assertIn('no registered owner', result['message'].lower())

    def test_fee_imposed_violation_blocks_entry(self):
        _, vehicle = _make_owner('fee@slc.edu.ph', 'FEE001', User.OwnerType.STUDENT, schedule='ANY')
        Violation.objects.create(
            vehicle=vehicle,
            violation_type=Violation.Type.UNAUTHORIZED_ENTRY,
            offense_number=3,
            status=Violation.Status.FEE_IMPOSED,
        )
        result = check_entry(vehicle)
        self.assertFalse(result['allowed'])
        self.assertIn('₱150', result['message'])

    def test_open_campus_mode_bypasses_all_rules(self):
        # Unauthorized vehicle + inactive owner — still allowed in open campus mode
        _, vehicle = _make_owner('open@slc.edu.ph', 'OPEN01', User.OwnerType.STUDENT,
                                  is_authorized=False, is_active=False)
        settings = SystemSettings.get()
        settings.open_campus_mode = True
        settings.save()
        result = check_entry(vehicle)
        self.assertTrue(result['allowed'])
        self.assertEqual(result['status'], 'authorized')

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
        _, vehicle = _make_owner('fetch@slc.edu.ph', 'FTECH1', User.OwnerType.FETCHER, schedule='ANY')
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
        """Two denied scans in quick succession must produce only one auto-violation."""
        _, vehicle = _make_owner('dup@slc.edu.ph', 'DUP001', User.OwnerType.STUDENT,
                                  is_authorized=False)
        self.client.post('/api/scan/manual-entry/', {'plate_number': 'DUP001'}, format='json')
        self.client.post('/api/scan/manual-entry/', {'plate_number': 'DUP001'}, format='json')
        count = Violation.objects.filter(vehicle=vehicle).count()
        self.assertEqual(count, 1)

    def test_missing_plate_number_returns_400(self):
        resp = self.client.post('/api/scan/manual-entry/', {}, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_unauthenticated_request_rejected(self):
        client = APIClient()
        resp = client.post('/api/scan/manual-entry/', {'plate_number': 'AUTH01'}, format='json')
        self.assertEqual(resp.status_code, 401)


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

    def test_create_pass_auto_logs_entry(self):
        self._create_pass('VIS002')
        self.assertTrue(AccessLog.objects.filter(plate_number='VIS002').exists())

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
