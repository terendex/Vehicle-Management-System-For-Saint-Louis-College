from django.test import TestCase
from accounts.models import User
from vehicles.models import Vehicle
from scanning.entry_logic import check_entry


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
