from django.test import TestCase
from django.utils import timezone
from accounts.models import User
from vehicles.models import Owner, Vehicle
from scanning.entry_logic import check_owner_entry, check_entry


class OwnerEntryLogicTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="testowner@slc.edu.ph",
            full_name="Juan dela Cruz",
            password="SecurePassword123!",
            role='vehicle_owner'
        )
        self.user.user_code = "SLC-OWN-000001"
        self.user.save()
        
        self.owner = Owner.objects.create(
            full_name="Juan dela Cruz",
            owner_type=Owner.OwnerType.EMPLOYEE,
            schedule=Owner.Schedule.ANY,
            user_code="SLC-OWN-000001",
        )

    def test_active_owner_authorized(self):
        self.assertTrue(self.user.is_active)
        result = check_owner_entry(self.owner, "bicycle")
        self.assertEqual(result["status"], "authorized")
        self.assertTrue(result["allowed"])

    def test_disabled_owner_denied(self):
        self.user.is_active = False
        self.user.save()
        
        result = check_owner_entry(self.owner, "bicycle")
        self.assertEqual(result["status"], "denied")
        self.assertFalse(result["allowed"])
        self.assertEqual(result["message"], "Owner account is suspended/disabled.")


class OwnerUserCodeTests(TestCase):
    """Tests for Owner.user_code field behavior."""
    
    def test_user_code_unique_constraint(self):
        """user_code must be unique across Owner records."""
        user = User.objects.create_user(
            email="unique@slc.edu.ph",
            full_name="Unique User",
            password="Password123!",
            role='vehicle_owner'
        )
        user.user_code = "SLC-OWN-000010"
        user.save()
        
        owner1 = Owner.objects.create(
            full_name="Unique User",
            owner_type=Owner.OwnerType.STUDENT,
            schedule=Owner.Schedule.MWF,
            user_code="SLC-OWN-000010",
        )
        
        with self.assertRaises(Exception):
            Owner.objects.create(
                full_name="Another Unique User",
                owner_type=Owner.OwnerType.STUDENT,
                schedule=Owner.Schedule.MWF,
                user_code="SLC-OWN-000010",
            )


class OwnerNameCollisionTests(TestCase):
    """Tests for handling identical names (data model limitation)."""
    
    def test_identical_names_one_suspended_one_not(self):
        """
        Two Owners with identical full_name, only one linked to suspended User.
        
        NOTE: Due to data model limitation (no stable identifier link between Owner and User),
        the current implementation uses full_name matching. This test documents that
        BOTH owners will be matched to the same User via full_name, which is a known
        limitation. The test verifies that the suspension check works correctly once
        user_code is set, but the backfill cannot distinguish between same-name owners.
        """
        user1 = User.objects.create_user(
            email="real@slc.edu.ph",
            full_name="John Smith",
            password="Password123!",
            role='vehicle_owner'
        )
        user1.user_code = "SLC-OWN-000021"
        user1.is_active = True
        user1.save()
        
        user2 = User.objects.create_user(
            email="suspended@slc.edu.ph",
            full_name="John Smith",
            password="Password123!",
            role='vehicle_owner'
        )
        user2.user_code = "SLC-OWN-000022"
        user2.is_active = False
        user2.save()
        
        owner1 = Owner.objects.create(
            full_name="John Smith",
            owner_type=Owner.OwnerType.STUDENT,
            schedule=Owner.Schedule.ANY,
            user_code="SLC-OWN-000021",
        )
        owner2 = Owner.objects.create(
            full_name="John Smith",
            owner_type=Owner.OwnerType.STUDENT,
            schedule=Owner.Schedule.ANY,
            user_code="SLC-OWN-000022",
        )
        
        result1 = check_owner_entry(owner1, "bicycle")
        result2 = check_owner_entry(owner2, "bicycle")
        
        self.assertEqual(result1["status"], "authorized")
        self.assertEqual(result2["status"], "denied")
        self.assertEqual(result2["message"], "Owner account is suspended/disabled.")


class OwnerSuspensionTests(TestCase):
    """Tests for owner suspension check using user_code (not name matching)."""
    
    def test_two_owners_same_name_only_one_suspended(self):
        """Two owners with same name: only the suspended one is denied."""
        user1 = User.objects.create_user(
            email="owner1@slc.edu.ph",
            full_name="Juan dela Cruz",
            password="Password123!",
            role='vehicle_owner'
        )
        user1.user_code = "SLC-OWN-000001"
        user1.save()
        
        user2 = User.objects.create_user(
            email="owner2@slc.edu.ph",
            full_name="Juan dela Cruz",
            password="Password123!",
            role='vehicle_owner'
        )
        user2.user_code = "SLC-OWN-000002"
        user2.is_active = False
        user2.save()
        
        owner1 = Owner.objects.create(
            full_name="Juan dela Cruz",
            owner_type=Owner.OwnerType.STUDENT,
            schedule=Owner.Schedule.ANY,
            user_code="SLC-OWN-000001",
        )
        owner2 = Owner.objects.create(
            full_name="Juan dela Cruz",
            owner_type=Owner.OwnerType.STUDENT,
            schedule=Owner.Schedule.ANY,
            user_code="SLC-OWN-000002",
        )
        
        result1 = check_owner_entry(owner1, "bicycle")
        result2 = check_owner_entry(owner2, "bicycle")
        
        self.assertEqual(result1["status"], "authorized")
        self.assertTrue(result1["allowed"])
        
        self.assertEqual(result2["status"], "denied")
        self.assertFalse(result2["allowed"])
        self.assertEqual(result2["message"], "Owner account is suspended/disabled.")

    def test_owner_no_linked_user_record(self):
        """Owner with no linked User record: suspension check skipped, entry based on schedule."""
        owner = Owner.objects.create(
            full_name="No User Owner",
            owner_type=Owner.OwnerType.STUDENT,
            schedule=Owner.Schedule.ANY,
            user_code="",
        )
        
        result = check_owner_entry(owner, "bicycle")
        
        self.assertEqual(result["status"], "authorized")
        self.assertTrue(result["allowed"])

    def test_suspended_owner_denied_before_schedule_check(self):
        """Suspended owner is denied BEFORE schedule check runs."""
        user = User.objects.create_user(
            email="suspended@slc.edu.ph",
            full_name="Suspended Owner",
            password="Password123!",
            role='vehicle_owner'
        )
        user.user_code = "SLC-OWN-000003"
        user.is_active = False
        user.save()
        
        owner = Owner.objects.create(
            full_name="Suspended Owner",
            owner_type=Owner.OwnerType.STUDENT,
            schedule=Owner.Schedule.ANY,
            user_code="SLC-OWN-000003",
        )
        
        result = check_owner_entry(owner, "bicycle")
        
        self.assertEqual(result["status"], "denied")
        self.assertFalse(result["allowed"])
        self.assertEqual(result["message"], "Owner account is suspended/disabled.")


class PlatedVehicleSuspensionTests(TestCase):
    """Tests for plated vehicle owners going through check_entry()."""
    
    def test_plated_vehicle_suspended_owner_denied(self):
        """Plated vehicle with suspended owner is denied."""
        user = User.objects.create_user(
            email="plated@slc.edu.ph",
            full_name="Plated Owner",
            password="Password123!",
            role='vehicle_owner'
        )
        user.user_code = "SLC-OWN-000004"
        user.is_active = False
        user.save()
        
        owner = Owner.objects.create(
            full_name="Plated Owner",
            owner_type=Owner.OwnerType.STUDENT,
            schedule=Owner.Schedule.MWF,
            user_code="SLC-OWN-000004",
        )
        
        vehicle = Vehicle.objects.create(
            plate_number="ABC1234",
            vehicle_type=Vehicle.Type.CAR,
            is_authorized=True,
            owner=owner
        )
        
        result = check_entry(vehicle)
        
        self.assertEqual(result["status"], "denied")
        self.assertFalse(result["allowed"])
        self.assertEqual(result["message"], "Owner account is suspended/disabled.")