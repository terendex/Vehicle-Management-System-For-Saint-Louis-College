"""Double parking: geometry, attribution, and what happens when the plate is unreadable.

The camera can only fine a vehicle it can actually name. A plate it cannot read
must raise an alert for a guard, never a Violation against a guess — these tests
pin both halves of that rule.
"""
from unittest.mock import patch

from django.test import TestCase

from vehicles.models import ParkingZone, ParkingSpace, Vehicle
from vehicles import parking_camera as pc
from violations.models import Violation


def box(x, y, w, h):
    return {"x": x, "y": y, "width": w, "height": h}


def det(x, y, w, h):
    return {"bbox": box(x, y, w, h), "class_name": "vehicle", "score": 0.9}


class CoverageGeometryTests(TestCase):
    """The centroid test could not express a car across two bays at all."""

    def setUp(self):
        self.zone = ParkingZone.objects.create(name='Geo', vehicle_category='car')
        self.a = ParkingSpace.objects.create(zone=self.zone, space_number='A1',
                                             x1=0.10, y1=0.40, x2=0.30, y2=0.70)
        self.b = ParkingSpace.objects.create(zone=self.zone, space_number='A2',
                                             x1=0.30, y1=0.40, x2=0.50, y2=0.70)

    def test_empty_bay_reads_zero(self):
        self.assertEqual(pc._space_coverage(self.a, box(0.8, 0.1, 0.05, 0.05)), 0.0)

    def test_car_in_its_own_bay_does_not_touch_the_neighbour(self):
        b = box(0.11, 0.42, 0.18, 0.26)
        self.assertGreaterEqual(pc._space_coverage(self.a, b), pc.OCCUPY_COVERAGE)
        self.assertLess(pc._space_coverage(self.b, b), pc.DOUBLE_PARK_COVERAGE)

    def test_car_clipping_the_neighbour_is_not_double_parking(self):
        b = box(0.16, 0.42, 0.18, 0.26)
        self.assertLess(pc._space_coverage(self.b, b), pc.DOUBLE_PARK_COVERAGE)

    def test_straddling_car_covers_both_bays(self):
        b = box(0.22, 0.42, 0.20, 0.26)
        self.assertGreaterEqual(pc._space_coverage(self.a, b), pc.DOUBLE_PARK_COVERAGE)
        self.assertGreaterEqual(pc._space_coverage(self.b, b), pc.DOUBLE_PARK_COVERAGE)

    def test_vehicle_share_is_measured_against_the_vehicle(self):
        """The mirror of coverage: how much of the car is in this bay."""
        b = box(0.11, 0.42, 0.18, 0.26)          # sits inside A1
        self.assertGreater(pc._vehicle_share(self.a, b), 0.9)
        self.assertEqual(pc._vehicle_share(self.b, b), 0.0)

    def test_a_straddling_car_has_a_real_share_in_both_bays(self):
        b = box(0.22, 0.42, 0.20, 0.26)
        self.assertGreaterEqual(pc._vehicle_share(self.a, b), pc.DOUBLE_PARK_VEHICLE_SHARE)
        self.assertGreaterEqual(pc._vehicle_share(self.b, b), pc.DOUBLE_PARK_VEHICLE_SHARE)

    def test_an_overhang_into_a_narrow_bay_is_not_a_straddle(self):
        """The case bay coverage alone gets wrong.

        A car parked correctly in its own bay overhangs a narrow neighbour. That
        sliver can be a large fraction of the narrow bay while being a small
        fraction of the car, so coverage says straddle and vehicle share says
        overhang. Vehicle share is right.
        """
        narrow = ParkingSpace.objects.create(
            zone=self.zone, space_number='N1',
            x1=0.30, y1=0.40, x2=0.335, y2=0.70)     # a sliver of a bay
        car = box(0.11, 0.42, 0.20, 0.26)            # own bay, slight overhang

        self.assertGreaterEqual(pc._space_coverage(narrow, car), pc.DOUBLE_PARK_COVERAGE)
        self.assertLess(pc._vehicle_share(narrow, car), pc.DOUBLE_PARK_VEHICLE_SHARE)

    def test_polygon_bay_from_the_pen_tool(self):
        poly = ParkingSpace.objects.create(
            zone=self.zone, space_number='P1', x1=0.58, y1=0.40, x2=0.80, y2=0.70,
            points=[[0.60, 0.40], [0.80, 0.42], [0.78, 0.70], [0.58, 0.68]])
        self.assertGreater(pc._space_coverage(poly, box(0.60, 0.42, 0.18, 0.26)), 0.5)
        self.assertEqual(pc._space_coverage(poly, box(0.10, 0.10, 0.10, 0.10)), 0.0)


class DoubleParkingReportingTests(TestCase):
    """Reporting is now gated on the vehicle having stopped, not on how many
    frames the bays looked straddled — so these drive a clock, not a counter."""

    def setUp(self):
        # The camera thread calls close_old_connections() before each write —
        # right for a daemon that outlives its connections, fatal for a test
        # running inside one transaction. Neutralise it so these tests exercise
        # the detection logic rather than Django's connection handling.
        p = patch('django.db.close_old_connections')
        p.start()
        self.addCleanup(p.stop)

        self.zone = ParkingZone.objects.create(name='DP', vehicle_category='car')
        self.a = ParkingSpace.objects.create(zone=self.zone, space_number='A1',
                                             x1=0.10, y1=0.40, x2=0.30, y2=0.70)
        self.b = ParkingSpace.objects.create(zone=self.zone, space_number='A2',
                                             x1=0.30, y1=0.40, x2=0.50, y2=0.70)
        self.spaces = [self.a, self.b]
        self.thread = pc.ParkingCameraThread(self.zone.id, 'rtsp://unused')

        # Straddles both bays from either position, and the two are 0.03 apart —
        # above vehicle_tracker.STILL_RADIUS, so alternating between them reads
        # as a car that has not settled.
        self.straddle       = det(0.20, 0.42, 0.24, 0.26)
        self.straddle_moved = det(0.20, 0.45, 0.24, 0.26)

        self.now = 1000.0

    # ── driving the loop ────────────────────────────────────────────────────
    def _tick(self, dets, step=1.0):
        """One observation, `step` seconds after the last."""
        self.now += step
        vehicles = self.thread._tracker.update(dets, self.now)
        self.thread._check_double_parking(self.spaces, vehicles,
                                          frame=None, now=self.now)

    def _hold(self, dets, seconds, step=1.0):
        """Keep the same boxes still for `seconds` of observed time."""
        self._tick(dets, step)                       # the vehicle arrives
        for _ in range(int(seconds / step) + 1):
            self._tick(dets, step)

    def _settled_straddle(self):
        self._hold([self.straddle], pc.DOUBLE_PARK_AFTER_SECONDS)

    # ── timing ──────────────────────────────────────────────────────────────
    def test_a_car_still_manoeuvring_is_never_reported(self):
        """The case the frame counter could not express. A driver reversing into
        a slot is across the line the whole way in; if the shuffling itself
        counted toward the threshold, every normal park would be a fine."""
        with patch.object(pc.ParkingCameraThread, '_report_double_parking') as rep:
            for _ in range(int(pc.DOUBLE_PARK_AFTER_SECONDS * 3)):
                self._tick([self.straddle])
                self._tick([self.straddle_moved])
            rep.assert_not_called()

    def test_a_brief_straddle_is_not_reported(self):
        with patch.object(pc.ParkingCameraThread, '_report_double_parking') as rep:
            self._hold([self.straddle], pc.DOUBLE_PARK_AFTER_SECONDS - 3)
            rep.assert_not_called()

    def test_a_settled_straddle_is_reported_once(self):
        with patch.object(pc.ParkingCameraThread, '_report_double_parking') as rep:
            self._hold([self.straddle], pc.DOUBLE_PARK_AFTER_SECONDS * 2)
            self.assertEqual(rep.call_count, 1)

    def test_the_clock_restarts_when_the_car_straightens_up(self):
        """Nearly there, then the driver corrects into their own bay: the dwell
        starts over, and the corrected position no longer straddles anyway."""
        with patch.object(pc.ParkingCameraThread, '_report_double_parking') as rep:
            self._hold([self.straddle], pc.DOUBLE_PARK_AFTER_SECONDS - 3)
            straightened = det(0.11, 0.42, 0.18, 0.26)       # inside A1
            self._hold([straightened], pc.DOUBLE_PARK_AFTER_SECONDS * 2)
            rep.assert_not_called()

    def test_a_detector_blink_does_not_restart_the_clock(self):
        """A frame the detector misses is not the car moving. Losing the track
        on every miss would mean a busy lot never accumulates any dwell."""
        with patch.object(pc.ParkingCameraThread, '_report_double_parking') as rep:
            self._hold([self.straddle], pc.DOUBLE_PARK_AFTER_SECONDS - 4)
            self._tick([])                                   # one missed frame
            self._hold([self.straddle], 3)
            self.assertEqual(rep.call_count, 1)

    def test_an_overhanging_car_is_never_reported(self):
        """End to end: the narrow-bay overhang must not raise an alert however
        long it sits there."""
        narrow = ParkingSpace.objects.create(
            zone=self.zone, space_number='N1',
            x1=0.30, y1=0.40, x2=0.335, y2=0.70)
        parked = det(0.11, 0.42, 0.20, 0.26)

        with patch.object(pc.ParkingCameraThread, '_report_double_parking') as rep:
            spaces = self.spaces + [narrow]
            self.now += 1
            for _ in range(int(pc.DOUBLE_PARK_AFTER_SECONDS) + 5):
                self.now += 1
                vehicles = self.thread._tracker.update([parked], self.now)
                self.thread._check_double_parking(spaces, vehicles,
                                                  frame=None, now=self.now)
            rep.assert_not_called()

    # ── attribution ─────────────────────────────────────────────────────────
    def test_readable_plate_of_a_registered_vehicle_creates_a_violation(self):
        v = Vehicle.objects.create(plate_number='ABC1234', vehicle_type='car')
        with patch.object(pc.ParkingCameraThread, '_read_plate', return_value='ABC1234'):
            self._settled_straddle()

        vio = Violation.objects.filter(vehicle=v,
                                       violation_type=Violation.Type.DOUBLE_PARKING)
        self.assertEqual(vio.count(), 1)
        alert = self.thread.get_alerts()[0]
        self.assertTrue(alert['attributed'])
        self.assertEqual(alert['plate'], 'ABC1234')
        self.assertEqual(sorted(alert['spaces']), ['A1', 'A2'])

    def test_the_alert_records_how_long_the_car_had_been_still(self):
        """An alert has to be defensible as being about a stopped car."""
        with patch.object(pc.ParkingCameraThread, '_read_plate', return_value=''):
            self._settled_straddle()

        alert = self.thread.get_alerts()[0]
        self.assertGreaterEqual(alert['stationary_seconds'],
                                pc.DOUBLE_PARK_AFTER_SECONDS)

    def test_unreadable_plate_alerts_but_never_fines_anyone(self):
        Vehicle.objects.create(plate_number='XYZ9999', vehicle_type='car')
        with patch.object(pc.ParkingCameraThread, '_read_plate', return_value=''):
            self._settled_straddle()

        self.assertEqual(Violation.objects.count(), 0)
        alert = self.thread.get_alerts()[0]
        self.assertFalse(alert['attributed'])
        self.assertEqual(alert['plate'], '')

    def test_plate_not_in_the_system_alerts_but_does_not_fine(self):
        with patch.object(pc.ParkingCameraThread, '_read_plate', return_value='NOTREG1'):
            self._settled_straddle()

        self.assertEqual(Violation.objects.count(), 0)
        alert = self.thread.get_alerts()[0]
        self.assertFalse(alert['attributed'])
        self.assertEqual(alert['plate'], 'NOTREG1')

    # ── alert lifecycle ─────────────────────────────────────────────────────
    def test_alert_clears_once_the_vehicle_moves_off_the_line(self):
        with patch.object(pc.ParkingCameraThread, '_read_plate', return_value=''):
            self._settled_straddle()
        self.assertEqual(len(self.thread.get_alerts()), 1)

        self._tick([])
        self.assertEqual(self.thread.get_alerts(), [])

    def test_both_straddled_bays_are_marked_occupied(self):
        # A1 is only partly covered — below OCCUPY_COVERAGE — but nobody can use
        # it while a car sits across the line, so it must not read as free.
        with patch.object(pc.ParkingCameraThread, '_read_plate', return_value=''):
            self._settled_straddle()
        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.assertTrue(self.a.is_occupied)
        self.assertTrue(self.b.is_occupied)

    def test_bays_are_not_claimed_while_the_car_is_still_moving(self):
        """A bay a car is merely passing through is still free for someone else."""
        with patch.object(pc.ParkingCameraThread, '_read_plate', return_value=''):
            for _ in range(int(pc.DOUBLE_PARK_AFTER_SECONDS)):
                self._tick([self.straddle])
                self._tick([self.straddle_moved])
        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.assertFalse(self.a.is_occupied)
        self.assertFalse(self.b.is_occupied)
