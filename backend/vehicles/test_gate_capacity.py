"""Parking capacity is counted from the gate ledger, not from the bay cameras.

A vehicle takes a slot when a guard scans it in and gives it back when one
scans it out. These tests pin that rule, the guard-discipline backstop that
keeps a missed exit scan from holding a slot all day, and the query budget —
the whole point of deriving the number this way is that it costs the same
whether three vehicles are on campus or three hundred.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from scanning.models import AccessLog
from scanning.occupancy import STALE_ENTRY_HOURS, inside_counts
from vehicles.capacity import category_capacity, category_state
from vehicles.models import ParkingSpace, ParkingZone, Vehicle

User = get_user_model()

ZONES = '/api/vehicles/parking-zones/'
AVAIL = '/api/vehicles/parking-availability/'


def _vehicle(plate, vtype=Vehicle.Type.CAR):
    return Vehicle.objects.create(plate_number=plate, vehicle_type=vtype, is_authorized=True)


def _enter(vehicle, minutes_ago=5):
    """An authorized entry scan, backdated. `scanned_at` is auto_now_add, so it
    has to be rewritten after the insert."""
    log = AccessLog.objects.create(
        plate_number=vehicle.plate_number, vehicle=vehicle,
        status=AccessLog.Status.AUTHORIZED, gate_id='gate1',
    )
    AccessLog.objects.filter(pk=log.pk).update(
        scanned_at=timezone.now() - timedelta(minutes=minutes_ago))
    return AccessLog.objects.get(pk=log.pk)


def _exit(vehicle, entry):
    return AccessLog.objects.create(
        plate_number=vehicle.plate_number, vehicle=vehicle,
        status=AccessLog.Status.EXITED, gate_id='gate1', paired_entry=entry,
    )


class InsideCountTests(TestCase):
    def test_entry_occupies_a_slot(self):
        _enter(_vehicle('AAA1111'))
        self.assertEqual(inside_counts()['car'], 1)

    def test_paired_exit_frees_the_slot(self):
        car = _vehicle('BBB2222')
        _exit(car, _enter(car))
        self.assertEqual(inside_counts()['car'], 0)

    def test_types_map_onto_two_categories(self):
        _enter(_vehicle('CAR0001', Vehicle.Type.CAR))
        _enter(_vehicle('VAN0001', Vehicle.Type.VAN))
        _enter(_vehicle('BUS0001', Vehicle.Type.BUS))
        _enter(_vehicle('MOT0001', Vehicle.Type.MOTORCYCLE))
        _enter(_vehicle('EBK0001', Vehicle.Type.EBIKE))

        counts = inside_counts()
        self.assertEqual(counts['car'], 3)         # car + van + bus
        self.assertEqual(counts['motorcycle'], 2)  # motorcycle + ebike

    def test_entry_without_a_vehicle_record_is_uncategorised_not_dropped(self):
        """Visitors and open-campus admits are on campus and must show in the
        total, but nothing says which category of slot they took."""
        log = AccessLog.objects.create(
            plate_number='VIS0001', vehicle=None,
            status=AccessLog.Status.AUTHORIZED, gate_id='gate1',
        )
        AccessLog.objects.filter(pk=log.pk).update(scanned_at=timezone.now())

        counts = inside_counts()
        self.assertEqual(counts['car'], 0)
        self.assertEqual(counts['motorcycle'], 0)
        self.assertEqual(counts['unknown'], 1)
        self.assertEqual(counts['total'], 1)

    def test_re_entry_after_exit_counts_once(self):
        car = _vehicle('CCC3333')
        _exit(car, _enter(car, minutes_ago=120))
        _enter(car, minutes_ago=10)
        self.assertEqual(inside_counts()['car'], 1)

    def test_duplicate_unpaired_entries_still_occupy_one_slot(self):
        """Plates are counted DISTINCT — a double-logged entry must not read as
        two vehicles taking two slots."""
        car = _vehicle('DDD4444')
        _enter(car, minutes_ago=30)
        _enter(car, minutes_ago=10)
        self.assertEqual(inside_counts()['car'], 1)

    def test_denied_scan_never_occupies_a_slot(self):
        car = _vehicle('EEE5555')
        AccessLog.objects.create(
            plate_number=car.plate_number, vehicle=car,
            status=AccessLog.Status.DENIED, gate_id='gate1',
        )
        self.assertEqual(inside_counts()['car'], 0)

    def test_yesterdays_entry_does_not_count_today(self):
        car = _vehicle('FFF6666')
        _enter(car, minutes_ago=60 * 30)   # 30h ago — before today's midnight
        self.assertEqual(inside_counts()['car'], 0)

    # ── guard-discipline backstop ────────────────────────────────────────────

    def test_missed_exit_scan_stops_holding_a_slot(self):
        car = _vehicle('GGG7777')
        _enter(car, minutes_ago=60 * (STALE_ENTRY_HOURS + 1))

        counts = inside_counts()
        self.assertEqual(counts['car'], 0)
        # Reported, not hidden: this is the number that says a gate stopped
        # scanning exits.
        self.assertEqual(counts['stale_excluded'], 1)

    def test_entry_inside_the_window_still_counts(self):
        car = _vehicle('HHH8888')
        _enter(car, minutes_ago=60 * (STALE_ENTRY_HOURS - 1))

        counts = inside_counts()
        self.assertEqual(counts['car'], 1)
        self.assertEqual(counts['stale_excluded'], 0)


class CategoryStateTests(TestCase):
    def setUp(self):
        self.zone = ParkingZone.objects.create(name='Car A', vehicle_category='car')

    def test_capacity_falls_back_to_bays_drawn(self):
        for i in range(4):
            ParkingSpace.objects.create(zone=self.zone, space_number=f'C{i}',
                                        x1=0.1, y1=0.1, x2=0.2, y2=0.2)
        self.assertEqual(category_capacity()['car'], 4)

    def test_declared_capacity_wins_over_bay_count(self):
        ParkingSpace.objects.create(zone=self.zone, space_number='C0',
                                    x1=0.1, y1=0.1, x2=0.2, y2=0.2)
        self.zone.capacity_override = 50
        self.zone.save(update_fields=['capacity_override'])
        self.assertEqual(category_capacity()['car'], 50)

    def test_capacity_sums_across_zones_of_a_category(self):
        self.zone.capacity_override = 30
        self.zone.save(update_fields=['capacity_override'])
        ParkingZone.objects.create(name='Car B', vehicle_category='car',
                                   capacity_override=20)
        self.assertEqual(category_capacity()['car'], 50)

    def test_full_when_occupancy_reaches_capacity(self):
        self.zone.capacity_override = 2
        self.zone.save(update_fields=['capacity_override'])
        _enter(_vehicle('III9999'))
        _enter(_vehicle('JJJ0000'))

        state = category_state()['car']
        self.assertEqual(state['occupied'], 2)
        self.assertEqual(state['available'], 0)
        self.assertTrue(state['is_full'])

    def test_available_never_goes_negative(self):
        """An override lowered below the live count reads as full, not as a
        negative number of free slots."""
        self.zone.capacity_override = 1
        self.zone.save(update_fields=['capacity_override'])
        _enter(_vehicle('KKK1111'))
        _enter(_vehicle('LLL2222'))

        state = category_state()['car']
        self.assertEqual(state['available'], 0)
        self.assertEqual(state['fill_pct'], 100)
        self.assertTrue(state['is_full'])

    def test_unregistered_entry_still_consumes_a_slot(self):
        """Nothing can classify a visitor's vehicle — the detector is
        single-class and there is no registration row to read a type from. It
        must still take up room, or the lot reports free slots it does not
        have."""
        self.zone.capacity_override = 5
        self.zone.save(update_fields=['capacity_override'])
        log = AccessLog.objects.create(
            plate_number='VIS9001', vehicle=None,
            status=AccessLog.Status.AUTHORIZED, gate_id='gate1',
        )
        AccessLog.objects.filter(pk=log.pk).update(scanned_at=timezone.now())

        state = category_state()
        self.assertEqual(state['car']['occupied'], 1)
        self.assertEqual(state['car']['available'], 4)
        # Reported separately, so the assumption is visible rather than buried.
        self.assertEqual(state['unknown'], 1)

    def test_unregistered_entries_do_not_double_count(self):
        """Charged to one category only — never to both."""
        log = AccessLog.objects.create(
            plate_number='VIS9002', vehicle=None,
            status=AccessLog.Status.AUTHORIZED, gate_id='gate1',
        )
        AccessLog.objects.filter(pk=log.pk).update(scanned_at=timezone.now())

        state = category_state()
        self.assertEqual(state['motorcycle']['occupied'], 0)
        self.assertEqual(state['car']['occupied'], 1)

    def test_zero_capacity_is_not_full(self):
        """A category with nothing configured must not report FULL — that would
        block a lot nobody has set up yet."""
        self.assertFalse(category_state()['motorcycle']['is_full'])


class CapacityQueryBudgetTests(TestCase):
    """The reason for counting this way: cost independent of scale."""

    def test_inside_count_is_one_query(self):
        for i in range(6):
            _enter(_vehicle(f'QB{i:05d}'))
        with self.assertNumQueries(1):
            inside_counts()

    def test_state_is_two_queries_regardless_of_zone_count(self):
        for i in range(5):
            zone = ParkingZone.objects.create(name=f'Z{i}', vehicle_category='car')
            for j in range(4):
                ParkingSpace.objects.create(zone=zone, space_number=f'{i}-{j}',
                                            x1=0.1, y1=0.1, x2=0.2, y2=0.2)
        with self.assertNumQueries(2):   # capacity aggregate + ledger count
            category_state()


class ParkingApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.guard = User.objects.create_user(
            email='cap-guard@slc.edu.ph', full_name='GUARD', password='x', role='security')

    def setUp(self):
        self.zone = ParkingZone.objects.create(
            name='Car Zone', vehicle_category='car', capacity_override=10)
        for i in range(3):
            ParkingSpace.objects.create(zone=self.zone, space_number=f'B{i}',
                                        x1=0.1, y1=0.1, x2=0.2, y2=0.2)
        self.client.force_authenticate(self.guard)

    def test_zone_reports_both_granularities(self):
        _enter(_vehicle('MMM3333'))
        ParkingSpace.objects.filter(zone=self.zone, space_number='B0').update(
            is_occupied=True, occupied_by='CAMERA')

        row = self.client.get(f'{ZONES}{self.zone.id}/').data
        self.assertEqual(row['category_capacity'], 10)   # declared
        self.assertEqual(row['category_occupied'], 1)    # gate ledger
        self.assertEqual(row['category_available'], 9)
        self.assertEqual(row['bays_occupied'], 1)        # camera map
        self.assertEqual(row['space_count'], 3)
        self.assertFalse(row['is_full'])
        self.assertEqual(row['occupancy_source'], 'gate_ledger')

    def test_zone_list_does_not_scale_queries_with_zone_count(self):
        for i in range(4):
            ParkingZone.objects.create(name=f'Extra {i}', vehicle_category='car')
        # Four flat: ledger count, capacity aggregate, the zone page, and the
        # space prefetch. It does not grow per zone — the old serializer ran
        # three .count()/.filter() queries per zone on top of these, so five
        # zones cost nineteen.
        with self.assertNumQueries(4):
            self.client.get(ZONES)

    def test_availability_summary_comes_from_the_ledger(self):
        _enter(_vehicle('NNN4444'))
        _enter(_vehicle('OOO5555'))

        body = self.client.get(f'{AVAIL}?category=car').data
        self.assertEqual(body['summary']['car']['total'], 10)
        self.assertEqual(body['summary']['car']['occupied'], 2)
        self.assertEqual(body['summary']['car']['available'], 8)
        self.assertEqual(body['summary']['car']['source'], 'gate_ledger')

    def test_availability_still_reports_the_bay_map(self):
        ParkingSpace.objects.filter(zone=self.zone, space_number='B0').update(
            is_occupied=True, occupied_by='CAMERA')

        body = self.client.get(f'{AVAIL}?category=car').data
        self.assertEqual(len(body['spaces']), 3)
        zone_row = next(z for z in body['zones'] if z['zone_id'] == self.zone.id)
        self.assertEqual(zone_row['zone_name'], 'Car Zone')
        self.assertEqual(zone_row['occupied'], 1)
        self.assertEqual(zone_row['total'], 3)
        self.assertEqual(zone_row['source'], 'camera_bays')

    def test_availability_is_a_flat_query_count(self):
        for i in range(4):
            zone = ParkingZone.objects.create(name=f'More {i}', vehicle_category='car')
            ParkingSpace.objects.create(zone=zone, space_number=f'M{i}',
                                        x1=0.1, y1=0.1, x2=0.2, y2=0.2)
        # Three flat: the spaces page, the ledger count, the capacity
        # aggregate. The zone-name lookup used to sit inside the aggregation
        # loop, costing one extra SELECT per zone on top.
        with self.assertNumQueries(3):
            self.client.get(f'{AVAIL}?category=car')

    def test_status_code_and_shape_for_unfiltered_request(self):
        r = self.client.get(AVAIL)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn('car', r.data['summary'])
        self.assertIn('motorcycle', r.data['summary'])
        self.assertIn('stale_excluded', r.data)
