from django.test import TestCase
from django.utils import timezone
from accounts.models import User
from vehicles.models import Vehicle
from scanning.entry_logic import check_owner_entry, check_entry


def _make_owner_user(email, full_name, owner_type, schedule, is_active=True, user_code=None):
    user = User.objects.create_user(
        email=email,
        full_name=full_name,
        password="SecurePassword123!",
        role='vehicle_owner',
        owner_type=owner_type,
        schedule=schedule,
    )
    if user_code:
        user.user_code = user_code
        user.save(update_fields=['user_code'])
    if not is_active:
        user.is_active = False
        user.save(update_fields=['is_active'])
    return user


class OwnerEntryLogicTests(TestCase):
    def setUp(self):
        self.user = _make_owner_user(
            "testowner@slc.edu.ph", "Juan dela Cruz",
            User.OwnerType.EMPLOYEE, User.Schedule.ANY,
            user_code="SLC-OWN-000001",
        )

    def test_active_owner_authorized(self):
        self.assertTrue(self.user.is_active)
        result = check_owner_entry(self.user, "bicycle")
        self.assertEqual(result["status"], "authorized")
        self.assertTrue(result["allowed"])

    def test_disabled_owner_denied(self):
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        result = check_owner_entry(self.user, "bicycle")
        self.assertEqual(result["status"], "denied")
        self.assertFalse(result["allowed"])
        self.assertEqual(result["message"], "Owner account is suspended/disabled.")


class OwnerSuspensionTests(TestCase):
    def test_two_owners_same_name_only_one_suspended(self):
        user1 = _make_owner_user("owner1@slc.edu.ph", "Juan dela Cruz", User.OwnerType.STUDENT, User.Schedule.ANY, user_code="SLC-OWN-000001")
        user2 = _make_owner_user("owner2@slc.edu.ph", "Juan dela Cruz", User.OwnerType.STUDENT, User.Schedule.ANY, is_active=False, user_code="SLC-OWN-000002")

        result1 = check_owner_entry(user1, "bicycle")
        result2 = check_owner_entry(user2, "bicycle")

        self.assertEqual(result1["status"], "authorized")
        self.assertTrue(result1["allowed"])
        self.assertEqual(result2["status"], "denied")
        self.assertFalse(result2["allowed"])
        self.assertEqual(result2["message"], "Owner account is suspended/disabled.")

    def test_suspended_owner_denied_before_schedule_check(self):
        user = _make_owner_user("suspended@slc.edu.ph", "Suspended Owner", User.OwnerType.STUDENT, User.Schedule.ANY, is_active=False, user_code="SLC-OWN-000003")
        result = check_owner_entry(user, "bicycle")
        self.assertEqual(result["status"], "denied")
        self.assertFalse(result["allowed"])
        self.assertEqual(result["message"], "Owner account is suspended/disabled.")


class PlatedVehicleSuspensionTests(TestCase):
    def test_plated_vehicle_suspended_owner_denied(self):
        user = _make_owner_user("plated@slc.edu.ph", "Plated Owner", User.OwnerType.STUDENT, User.Schedule.MWF, is_active=False, user_code="SLC-OWN-000004")
        vehicle = Vehicle.objects.create(
            plate_number="ABC1234",
            vehicle_type=Vehicle.Type.CAR,
            is_authorized=True,
            user=user,
        )
        result = check_entry(vehicle)
        self.assertEqual(result["status"], "denied")
        self.assertFalse(result["allowed"])
        self.assertEqual(result["message"], "Owner account is suspended/disabled.")
