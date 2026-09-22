"""A violation against an unregistered vehicle still names somebody.

`_snapshot_identity` used to fill owner_name only when the plate had a User
account behind it, so every violation raised against a visitor or a vehicle a
guard wrote up by hand landed with an empty owner — and the Violations table
drew a dash in the column that is supposed to say who did it.

There IS a name for those people. It just lives on the visitor pass or on the
unrecognized-entry row the gate wrote, rather than on a User.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from scanning.models import AccessLog, VisitorPass
from vehicles.models import Vehicle
from violations.models import Violation
from violations.serializers import ViolationSerializer

User = get_user_model()


class UnregisteredOwnerNameTests(TestCase):
    def _violation(self, vehicle):
        return Violation.objects.create(
            vehicle=vehicle, violation_type=Violation.Type.TIME_EXCEED,
            offense_number=1, status=Violation.Status.WARNING)

    def test_a_registered_owner_is_unaffected(self):
        owner = User.objects.create_user(
            email='un-owner@slc.edu.ph', full_name='SANTOS, MARIA',
            password='x', role='vehicle_owner')
        vehicle = Vehicle.objects.create(
            plate_number='REG0001', vehicle_type=Vehicle.Type.CAR, user=owner)
        v = self._violation(vehicle)
        self.assertEqual(v.owner_name, 'SANTOS, MARIA')
        self.assertEqual(v.owner_email, 'un-owner@slc.edu.ph')

    def test_a_visitor_is_named_from_their_pass(self):
        vehicle = Vehicle.objects.create(
            plate_number='VIS0001', vehicle_type=Vehicle.Type.CAR,
            is_authorized=False)
        VisitorPass.objects.create(
            vehicle=vehicle, plate_number='VIS0001',
            visitor_name='DELA CRUZ, JUAN', allowed_duration=15)
        v = self._violation(vehicle)
        self.assertEqual(v.owner_name, 'DELA CRUZ, JUAN')
        self.assertEqual(ViolationSerializer(v).data['owner_name'], 'DELA CRUZ, JUAN')

    def test_the_newest_pass_wins(self):
        """A plate that visited twice carries the name from the current visit."""
        vehicle = Vehicle.objects.create(
            plate_number='VIS0002', vehicle_type=Vehicle.Type.CAR)
        VisitorPass.objects.create(vehicle=vehicle, plate_number='VIS0002',
                                   visitor_name='OLD NAME', allowed_duration=15)
        VisitorPass.objects.create(vehicle=vehicle, plate_number='VIS0002',
                                   visitor_name='CURRENT DRIVER', allowed_duration=15)
        self.assertEqual(self._violation(vehicle).owner_name, 'CURRENT DRIVER')

    def test_a_hand_recorded_driver_is_named_from_their_entry(self):
        vehicle = Vehicle.objects.create(
            plate_number='UNK0001', vehicle_type=Vehicle.Type.CAR)
        AccessLog.objects.create(
            plate_number='UNK0001', vehicle=vehicle,
            status=AccessLog.Status.AUTHORIZED, is_unrecognized=True,
            driver_name='REYES, PEDRO')
        self.assertEqual(self._violation(vehicle).owner_name, 'REYES, PEDRO')

    def test_a_pass_with_no_name_on_it_does_not_win_over_a_driver_name(self):
        vehicle = Vehicle.objects.create(
            plate_number='UNK0002', vehicle_type=Vehicle.Type.CAR)
        VisitorPass.objects.create(vehicle=vehicle, plate_number='UNK0002',
                                   visitor_name='', allowed_duration=15)
        AccessLog.objects.create(
            plate_number='UNK0002', vehicle=vehicle,
            status=AccessLog.Status.AUTHORIZED, is_unrecognized=True,
            driver_name='GARCIA, ANA')
        self.assertEqual(self._violation(vehicle).owner_name, 'GARCIA, ANA')

    def test_with_nothing_on_record_the_column_still_is_not_blank(self):
        """A dash reads as data that failed to load. The truth is that nobody is
        registered against the plate, so the row says that instead."""
        vehicle = Vehicle.objects.create(
            plate_number='NONAME1', vehicle_type=Vehicle.Type.CAR)
        v = self._violation(vehicle)
        self.assertEqual(v.owner_name, '')
        self.assertEqual(ViolationSerializer(v).data['owner_name'],
                         'Unregistered vehicle')

    def test_the_snapshot_is_not_re_resolved_on_a_later_save(self):
        """Identity is taken once, at issue. A pass created afterwards must not
        rewrite who an already-issued violation was against."""
        vehicle = Vehicle.objects.create(
            plate_number='VIS0003', vehicle_type=Vehicle.Type.CAR)
        VisitorPass.objects.create(vehicle=vehicle, plate_number='VIS0003',
                                   visitor_name='FIRST DRIVER', allowed_duration=15)
        v = self._violation(vehicle)
        VisitorPass.objects.create(vehicle=vehicle, plate_number='VIS0003',
                                   visitor_name='SOMEONE ELSE', allowed_duration=15)
        v.notes = 'edited'
        v.save()
        v.refresh_from_db()
        self.assertEqual(v.owner_name, 'FIRST DRIVER')
