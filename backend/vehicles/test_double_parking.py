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

    def test_polygon_bay_from_the_pen_tool(self):
        poly = ParkingSpace.objects.create(
            zone=self.zone, space_number='P1', x1=0.58, y1=0.40, x2=0.80, y2=0.70,
            points=[[0.60, 0.40], [0.80, 0.42], [0.78, 0.70], [0.58, 0.68]])
        self.assertGreater(pc._space_coverage(poly, box(0.60, 0.42, 0.18, 0.26)), 0.5)
        self.assertEqual(pc._space_coverage(poly, box(0.10, 0.10, 0.10, 0.10)), 0.0)


class DoubleParkingReportingTests(TestCase):
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
        self.straddle = det(0.22, 0.42, 0.20, 0.26)

    def _run(self, frames):
        for _ in range(frames):
            self.thread._check_double_parking(self.spaces, [self.straddle], frame=None)

    # ── timing ──────────────────────────────────────────────────────────────
    def test_a_brief_straddle_while_manoeuvring_is_not_reported(self):
        with patch.object(pc.ParkingCameraThread, '_report_double_parking') as rep:
            self._run(pc.DOUBLE_PARK_FRAMES - 1)
            rep.assert_not_called()

    def test_a_sustained_straddle_is_reported_once(self):
        with patch.object(pc.ParkingCameraThread, '_report_double_parking') as rep:
            self._run(pc.DOUBLE_PARK_FRAMES + 30)   # keep going well past it
            self.assertEqual(rep.call_count, 1)

    def test_streak_resets_when_the_car_straightens_up(self):
        with patch.object(pc.ParkingCameraThread, '_report_double_parking') as rep:
            self._run(pc.DOUBLE_PARK_FRAMES - 5)
            # one clean frame — car no longer across the line
            self.thread._check_double_parking(self.spaces, [], frame=None)
            self._run(10)
            rep.assert_not_called()

    # ── attribution ─────────────────────────────────────────────────────────
    def test_readable_plate_of_a_registered_vehicle_creates_a_violation(self):
        v = Vehicle.objects.create(plate_number='ABC1234', vehicle_type='car')
        with patch.object(pc.ParkingCameraThread, '_read_plate', return_value='ABC1234'):
            self._run(pc.DOUBLE_PARK_FRAMES)

        vio = Violation.objects.filter(vehicle=v,
                                       violation_type=Violation.Type.DOUBLE_PARKING)
        self.assertEqual(vio.count(), 1)
        alert = self.thread.get_alerts()[0]
        self.assertTrue(alert['attributed'])
        self.assertEqual(alert['plate'], 'ABC1234')
        self.assertEqual(sorted(alert['spaces']), ['A1', 'A2'])

    def test_unreadable_plate_alerts_but_never_fines_anyone(self):
        Vehicle.objects.create(plate_number='XYZ9999', vehicle_type='car')
        with patch.object(pc.ParkingCameraThread, '_read_plate', return_value=''):
            self._run(pc.DOUBLE_PARK_FRAMES)

        self.assertEqual(Violation.objects.count(), 0)
        alert = self.thread.get_alerts()[0]
        self.assertFalse(alert['attributed'])
        self.assertEqual(alert['plate'], '')

    def test_plate_not_in_the_system_alerts_but_does_not_fine(self):
        with patch.object(pc.ParkingCameraThread, '_read_plate', return_value='NOTREG1'):
            self._run(pc.DOUBLE_PARK_FRAMES)

        self.assertEqual(Violation.objects.count(), 0)
        alert = self.thread.get_alerts()[0]
        self.assertFalse(alert['attributed'])
        self.assertEqual(alert['plate'], 'NOTREG1')

    # ── alert lifecycle ─────────────────────────────────────────────────────
    def test_alert_clears_once_the_vehicle_moves_off_the_line(self):
        with patch.object(pc.ParkingCameraThread, '_read_plate', return_value=''):
            self._run(pc.DOUBLE_PARK_FRAMES)
        self.assertEqual(len(self.thread.get_alerts()), 1)

        self.thread._check_double_parking(self.spaces, [], frame=None)
        self.assertEqual(self.thread.get_alerts(), [])

    def test_both_straddled_bays_are_marked_occupied(self):
        # A1 is only ~1/3 covered — below OCCUPY_COVERAGE — but nobody can use
        # it while a car sits across the line, so it must not read as free.
        with patch.object(pc.ParkingCameraThread, '_read_plate', return_value=''):
            self._run(1)
        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.assertTrue(self.a.is_occupied)
        self.assertTrue(self.b.is_occupied)
