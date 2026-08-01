from decimal import Decimal
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from vehicles.models import Vehicle
from violations.models import Violation, FINE_STANDARD, FINE_REPEAT, REPEAT_THRESHOLD


def _make_vehicle(plate, email=None):
    user = User.objects.create_user(
        email=email or f'{plate.lower()}@slc.edu.ph',
        full_name='Test Owner',
        password='SecurePassword123!',
        role='vehicle_owner',
        owner_type=User.OwnerType.STUDENT,
    )
    return Vehicle.objects.create(
        plate_number=plate,
        vehicle_type=Vehicle.Type.CAR,
        is_authorized=True,
        user=user,
    )


class OffenseNumberTests(TestCase):
    """Violation.compute_offense_number() counts active new-style violations."""

    def setUp(self):
        self.vehicle = _make_vehicle('OFF001')

    def test_first_offense_is_1(self):
        n = Violation.compute_offense_number(self.vehicle, 'unauthorized_entry')
        self.assertEqual(n, 1)

    def test_second_offense_is_2(self):
        Violation.objects.create(
            vehicle=self.vehicle, violation_type='unauthorized_entry',
            offense_number=1, status=Violation.Status.WARNING,
        )
        n = Violation.compute_offense_number(self.vehicle, 'unauthorized_entry')
        self.assertEqual(n, 2)

    def test_third_offense_caps_at_3(self):
        for i in range(1, 4):
            Violation.objects.create(
                vehicle=self.vehicle, violation_type='unauthorized_entry',
                offense_number=i, status=Violation.Status.WARNING,
            )
        n = Violation.compute_offense_number(self.vehicle, 'unauthorized_entry')
        self.assertEqual(n, 3)

    def test_cleared_violation_not_counted(self):
        Violation.objects.create(
            vehicle=self.vehicle, violation_type='unauthorized_entry',
            offense_number=1, status=Violation.Status.CLEARED,
        )
        # Cleared → should not count; next offense is still 1
        n = Violation.compute_offense_number(self.vehicle, 'unauthorized_entry')
        self.assertEqual(n, 1)

    def test_different_type_not_counted(self):
        Violation.objects.create(
            vehicle=self.vehicle, violation_type='double_parking',
            offense_number=1, status=Violation.Status.WARNING,
        )
        n = Violation.compute_offense_number(self.vehicle, 'unauthorized_entry')
        self.assertEqual(n, 1)

    def test_fee_imposed_violation_counts(self):
        # FEE_IMPOSED is not CLEARED, so two of them push the next offense to 3
        for i in range(1, 3):
            Violation.objects.create(
                vehicle=self.vehicle, violation_type='unauthorized_entry',
                offense_number=i, status=Violation.Status.FEE_IMPOSED,
            )
        n = Violation.compute_offense_number(self.vehicle, 'unauthorized_entry')
        self.assertEqual(n, 3)


class LegacyFineTests(TestCase):
    """Violation.compute_fine() applies legacy fee logic."""

    def setUp(self):
        self.vehicle = _make_vehicle('FIN001')

    def test_standard_fine_with_no_prior_violations(self):
        fine = Violation.compute_fine(self.vehicle)
        self.assertEqual(fine, FINE_STANDARD)

    def test_repeat_fine_once_threshold_is_reached(self):
        for _ in range(REPEAT_THRESHOLD):
            Violation.objects.create(
                vehicle=self.vehicle,
                violation_type='unauthorized',
            )
        fine = Violation.compute_fine(self.vehicle)
        self.assertEqual(fine, FINE_REPEAT)


class MyViolationsViewTests(TestCase):
    """GET /api/violations/my/ — owner sees violations across ALL their vehicles,
    resolved/cleared history included."""

    def setUp(self):
        self.owner = User.objects.create_user(
            email='multi@slc.edu.ph', full_name='Multi Vehicle Owner',
            password='SecurePassword123!', role='vehicle_owner',
            owner_type=User.OwnerType.STUDENT,
        )
        self.car = Vehicle.objects.create(
            plate_number='MULTI1', vehicle_type=Vehicle.Type.CAR,
            is_authorized=True, user=self.owner,
        )
        self.moto = Vehicle.objects.create(
            plate_number='MULTI2', vehicle_type=Vehicle.Type.MOTORCYCLE,
            is_authorized=True, user=self.owner,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    def _get(self):
        resp = self.client.get('/api/violations/my/')
        self.assertEqual(resp.status_code, 200)
        return resp.data

    def test_sees_violations_from_all_owned_vehicles(self):
        Violation.objects.create(vehicle=self.car,  violation_type='double_parking',
                                 offense_number=1, is_released=True)
        Violation.objects.create(vehicle=self.moto, violation_type='unauthorized_entry',
                                 offense_number=1, is_released=True)
        plates = {v['plate_number'] for v in self._get()}
        self.assertEqual(plates, {'MULTI1', 'MULTI2'})

    def test_resolved_violation_still_visible_as_history(self):
        Violation.objects.create(
            vehicle=self.car, violation_type='unauthorized_entry',
            offense_number=3, status=Violation.Status.CLEARED,
            is_released=True, is_resolved=True, official_receipt='OR-123',
        )
        data = self._get()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['status'], 'cleared')
        self.assertTrue(data[0]['is_resolved'])

    def test_resolved_but_never_released_legacy_violation_visible(self):
        Violation.objects.create(vehicle=self.car, violation_type='unauthorized',
                                 is_released=False, is_resolved=True)
        self.assertEqual(len(self._get()), 1)

    def test_unreleased_unresolved_legacy_violation_hidden(self):
        Violation.objects.create(vehicle=self.car, violation_type='unauthorized',
                                 is_released=False, is_resolved=False)
        self.assertEqual(len(self._get()), 0)

    def test_other_owners_violations_not_visible(self):
        other_vehicle = _make_vehicle('OTHER1')
        Violation.objects.create(vehicle=other_vehicle, violation_type='double_parking',
                                 offense_number=1, is_released=True)
        self.assertEqual(len(self._get()), 0)


class ViolationIssuePermissionTests(TestCase):
    """Issuing violations is guard-only; admin (CDSO) manages but does not issue."""

    def setUp(self):
        self.guard = User.objects.create_user(email='vguard@slc.edu.ph', full_name='Guard',
                                               password='x', role='security')
        self.admin = User.objects.create_user(email='vadmin@slc.edu.ph', full_name='Admin',
                                               password='x', role='admin')
        _make_vehicle('ISS1234')

    def _post(self, user):
        c = APIClient(); c.force_authenticate(user)
        return c.post('/api/violations/', {'plate_number': 'ISS1234',
                      'violation_type': 'double_parking', 'notes': 'x'}, format='json')

    def test_guard_can_issue(self):
        self.assertEqual(self._post(self.guard).status_code, 201)

    def test_admin_cannot_issue(self):
        self.assertEqual(self._post(self.admin).status_code, 403)
