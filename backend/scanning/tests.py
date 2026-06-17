from django.test import TestCase
from django.utils import timezone
from accounts.models import User
from vehicles.models import Owner
from scanning.entry_logic import check_owner_entry

class OwnerEntryLogicTests(TestCase):
    def setUp(self):
        # Create a test user (must change password)
        self.user = User.objects.create_user(
            email="testowner@slc.edu.ph",
            full_name="Juan dela Cruz",
            password="SecurePassword123!",
            role='vehicle_owner'
        )
        
        # Create matching Owner (Employee schedule is ANY by default, student is MWF)
        self.owner = Owner.objects.create(
            full_name="Juan dela Cruz",
            owner_type=Owner.OwnerType.EMPLOYEE,
            schedule=Owner.Schedule.ANY
        )

    def test_active_owner_authorized(self):
        # When User.is_active is True, check_owner_entry should allow entry (subject to schedule)
        self.assertTrue(self.user.is_active)
        result = check_owner_entry(self.owner, "bicycle")
        self.assertEqual(result["status"], "authorized")
        self.assertTrue(result["allowed"])

    def test_disabled_owner_denied(self):
        # Disable the User account
        self.user.is_active = False
        self.user.save()
        
        # Check owner entry
        result = check_owner_entry(self.owner, "bicycle")
        self.assertEqual(result["status"], "denied")
        self.assertFalse(result["allowed"])
        self.assertEqual(result["message"], "Owner account is suspended/disabled.")
