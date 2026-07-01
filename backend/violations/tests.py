from decimal import Decimal
from django.test import TestCase

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
