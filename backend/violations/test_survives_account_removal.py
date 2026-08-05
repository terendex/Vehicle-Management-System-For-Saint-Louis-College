"""A violation must outlive the account it was issued against.

It is a disciplinary and financial record, and the 3rd-offense registration
hold is enforcement built on top of it. Under the old CASCADE, deleting an
owner deleted their vehicle and every violation with it — so the hold lifted
itself and a deleted-then-re-registered owner came back with a clean record.
Archiving was milder but still wrong: it cleared vehicle.user, and the screens
resolved the owner live through that FK, so valid violations lost their name.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import delete_user_with_owned_records
from vehicles.models import Vehicle
from vehicles.serializers import VehicleRegistrationSerializer
from violations.models import Violation
from violations.serializers import ViolationSerializer

User = get_user_model()


def _owner(email='viol-owner@slc.edu.ph', name='DELA CRUZ, JUAN'):
    return User.objects.create_user(
        email=email, full_name=name, password='x', role='vehicle_owner')


class SnapshotTests(TestCase):
    def setUp(self):
        self.owner = _owner()
        self.vehicle = Vehicle.objects.create(
            plate_number='SNAP001', vehicle_type=Vehicle.Type.CAR,
            is_authorized=True, user=self.owner)

    def test_identity_is_captured_at_issue_time(self):
        v = Violation.objects.create(vehicle=self.vehicle,
                                     violation_type=Violation.Type.UNAUTHORIZED_ENTRY)
        self.assertEqual(v.plate_number, 'SNAP001')
        self.assertEqual(v.owner_name, 'DELA CRUZ, JUAN')
        self.assertEqual(v.owner_email, 'viol-owner@slc.edu.ph')

    def test_conduction_only_vehicle_is_named_not_blank(self):
        conduction = Vehicle.objects.create(
            conduction_number='CS123456', vehicle_type=Vehicle.Type.CAR, user=self.owner)
        v = Violation.objects.create(vehicle=conduction,
                                     violation_type=Violation.Type.DOUBLE_PARKING)
        self.assertEqual(v.plate_number, '')
        self.assertEqual(v.conduction_number, 'CS123456')
        self.assertEqual(v.identifier, 'CS123456')

    def test_resaving_does_not_re_resolve_identity(self):
        """A snapshot that refreshes itself is not a snapshot. Reassigning the
        vehicle to someone else must not rewrite history."""
        v = Violation.objects.create(vehicle=self.vehicle,
                                     violation_type=Violation.Type.TIME_EXCEED)
        other = _owner('other@slc.edu.ph', 'REYES, MARIA')
        self.vehicle.user = other
        self.vehicle.save(update_fields=['user'])

        v.notes = 'edited'
        v.save()
        v.refresh_from_db()
        self.assertEqual(v.owner_name, 'DELA CRUZ, JUAN')


class SurvivesDeletionTests(TestCase):
    def setUp(self):
        self.owner = _owner()
        self.vehicle = Vehicle.objects.create(
            plate_number='GONE001', vehicle_type=Vehicle.Type.CAR,
            is_authorized=True, user=self.owner)
        self.violation = Violation.objects.create(
            vehicle=self.vehicle,
            violation_type=Violation.Type.UNAUTHORIZED_ENTRY,
            registration_blocked=True,
            offense_number=3,
        )

    def test_violation_survives_owner_deletion(self):
        delete_user_with_owned_records(self.owner)

        self.violation.refresh_from_db()
        self.assertIsNone(self.violation.vehicle_id)
        self.assertEqual(Violation.objects.count(), 1)

    def test_identity_is_still_readable_after_deletion(self):
        delete_user_with_owned_records(self.owner)

        self.violation.refresh_from_db()
        self.assertEqual(self.violation.plate_number, 'GONE001')
        self.assertEqual(self.violation.owner_name, 'DELA CRUZ, JUAN')
        self.assertIn('GONE001', str(self.violation))

    def test_registration_hold_survives_deletion(self):
        """The case that matters: delete the account, re-register the plate,
        and the 3rd-offense hold must still be found."""
        delete_user_with_owned_records(self.owner)

        blocked = Violation.registration_block_for_plate('GONE001')
        self.assertEqual(blocked.count(), 1)

    def test_block_counts_still_flag_the_plate_after_deletion(self):
        """The registration review screen builds its counts in one batch query —
        it has to match the snapshot too, or the hold shows on the detail view
        and not on the list."""
        delete_user_with_owned_records(self.owner)

        class _Row:
            plate_number = 'GONE001'

        counts = VehicleRegistrationSerializer.build_block_counts([_Row()])
        self.assertEqual(counts.get('GONE001'), 1)

    def test_serializer_reports_identity_after_deletion(self):
        delete_user_with_owned_records(self.owner)

        data = ViolationSerializer(Violation.objects.get(pk=self.violation.pk)).data
        self.assertEqual(data['plate_number'], 'GONE001')
        self.assertEqual(data['owner_name'], 'DELA CRUZ, JUAN')


class SurvivesArchiveTests(TestCase):
    """Archiving unlinks the vehicle from its owner (vehicles/tasks.py). The row
    was never deleted, but the owner column went blank because the screens
    resolved the name through that link."""

    def setUp(self):
        self.owner = _owner()
        self.vehicle = Vehicle.objects.create(
            plate_number='ARCH001', vehicle_type=Vehicle.Type.CAR,
            is_authorized=True, user=self.owner)
        self.violation = Violation.objects.create(
            vehicle=self.vehicle, violation_type=Violation.Type.TIME_EXCEED)

    def test_owner_name_survives_the_unlink(self):
        self.vehicle.user = None
        self.vehicle.save(update_fields=['user'])

        data = ViolationSerializer(Violation.objects.get(pk=self.violation.pk)).data
        self.assertEqual(data['owner_name'], 'DELA CRUZ, JUAN')
        self.assertEqual(data['plate_number'], 'ARCH001')
