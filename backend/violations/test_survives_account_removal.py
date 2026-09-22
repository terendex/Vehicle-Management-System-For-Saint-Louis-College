"""What happens to a violation when the account behind it goes away.

Two different events, two deliberately different outcomes:

  * **Deleted account** — the violations go with it. Chosen policy: deleting an
    owner removes their violation history rather than leaving it unattributed.
    The trade is real and is asserted below — a 3rd-offense registration hold
    is enforced by a Violation row, so it does not survive the deletion either.

  * **Archived account** — everything is kept. Archiving only unlinks the
    vehicle from its owner, and the account still exists. The screens used to
    resolve the owner live through that FK, so an archived owner's perfectly
    valid violations went blank; the identity snapshot is what fixes that.

The snapshot still earns its place under this policy: archived owners, visitor
violations that never had an account, and conduction-only vehicles all keep
their identity, and the FK being SET_NULL is what stops a deletion elsewhere
from silently taking records with it.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APITestCase

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


class DeletedAccountTests(TestCase):
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

    def test_violations_go_with_the_account(self):
        delete_user_with_owned_records(self.owner)
        self.assertEqual(Violation.objects.count(), 0)

    def test_deleting_one_owner_leaves_another_owners_violations_alone(self):
        """The sweep runs off the vehicle-to-user link, so it has to select, not
        just delete everything ownerless."""
        other = _owner('keep@slc.edu.ph', 'SANTOS, ANA')
        kept_vehicle = Vehicle.objects.create(
            plate_number='KEEP001', vehicle_type=Vehicle.Type.CAR, user=other)
        kept = Violation.objects.create(
            vehicle=kept_vehicle, violation_type=Violation.Type.TIME_EXCEED)

        delete_user_with_owned_records(self.owner)

        self.assertTrue(Violation.objects.filter(pk=kept.pk).exists())
        self.assertEqual(Violation.objects.count(), 1)

    def test_visitor_violation_is_not_swept_up_by_an_unrelated_deletion(self):
        """A violation with no account behind it is not this owner's to delete.
        Removing those is a separate, explicit command."""
        visitor_vehicle = Vehicle.objects.create(
            plate_number='VIS777', vehicle_type=Vehicle.Type.CAR, user=None)
        visitor = Violation.objects.create(
            vehicle=visitor_vehicle, violation_type=Violation.Type.TIME_EXCEED)

        delete_user_with_owned_records(self.owner)

        self.assertTrue(Violation.objects.filter(pk=visitor.pk).exists())

    def test_registration_hold_does_not_outlive_the_account(self):
        """The accepted cost of tying violations to the account: the plate comes
        back clean. Asserted rather than left implicit, because it is the part
        that is easy to be surprised by later."""
        delete_user_with_owned_records(self.owner)

        self.assertEqual(Violation.registration_block_for_plate('GONE001').count(), 0)

        class _Row:
            plate_number = 'GONE001'

        self.assertEqual(
            VehicleRegistrationSerializer.build_block_counts([_Row()]).get('GONE001'), None)


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
