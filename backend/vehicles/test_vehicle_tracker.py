"""Vehicle identity and stillness, and the dwell gate they put on occupancy.

The tracker exists to answer one question the per-bay signals never could: has
this vehicle actually stopped? These pin that answer, and then pin what the
parking loop does with it — a bay is claimed by a car that parked in it, not by
one driving past.
"""
from unittest.mock import patch

from django.test import TestCase, SimpleTestCase

from vehicles.models import ParkingZone, ParkingSpace
from vehicles import parking_camera as pc
from vehicles.vehicle_tracker import VehicleTracker, STILL_RADIUS, TRACK_LOST_SECONDS


def det(x, y, w, h):
    return {"bbox": {"x": x, "y": y, "width": w, "height": h},
            "class_name": "vehicle", "score": 0.9}


class TrackIdentityTests(SimpleTestCase):
    def setUp(self):
        self.tracker = VehicleTracker()

    def test_a_car_holding_still_keeps_its_identity(self):
        a = self.tracker.update([det(0.2, 0.4, 0.2, 0.2)], 100.0)[0]
        b = self.tracker.update([det(0.2, 0.4, 0.2, 0.2)], 101.0)[0]
        self.assertEqual(a.track_id, b.track_id)

    def test_a_car_that_drifts_slightly_keeps_its_identity(self):
        """Detection boxes jitter by a few pixels on a parked car; that must not
        read as one car leaving and another arriving."""
        a = self.tracker.update([det(0.20, 0.40, 0.20, 0.20)], 100.0)[0]
        b = self.tracker.update([det(0.205, 0.402, 0.20, 0.20)], 101.0)[0]
        self.assertEqual(a.track_id, b.track_id)

    def test_two_cars_get_two_tracks(self):
        seen = self.tracker.update(
            [det(0.10, 0.40, 0.15, 0.20), det(0.60, 0.40, 0.15, 0.20)], 100.0)
        self.assertEqual(len({t.track_id for t in seen}), 2)

    def test_a_car_that_leaves_and_a_new_one_arriving_are_not_confused(self):
        first = self.tracker.update([det(0.10, 0.40, 0.15, 0.20)], 100.0)[0]
        # Nothing seen for longer than the tracker's own lost window. (Not
        # parking_camera's constant of the same name — that one is what the
        # camera passes in, and the two being equal today is a coincidence.)
        self.tracker.update([], 100.0 + TRACK_LOST_SECONDS + 1)
        second = self.tracker.update([det(0.10, 0.40, 0.15, 0.20)], 200.0)[0]
        self.assertNotEqual(first.track_id, second.track_id)

    def test_a_missed_observation_does_not_lose_the_car(self):
        first = self.tracker.update([det(0.10, 0.40, 0.15, 0.20)], 100.0)[0]
        self.tracker.update([], 101.0)                    # detector blinked
        again = self.tracker.update([det(0.10, 0.40, 0.15, 0.20)], 102.0)[0]
        self.assertEqual(first.track_id, again.track_id)


class StillnessTests(SimpleTestCase):
    def setUp(self):
        self.tracker = VehicleTracker()

    def test_a_parked_car_accumulates_stationary_time(self):
        self.tracker.update([det(0.2, 0.4, 0.2, 0.2)], 100.0)
        t = self.tracker.update([det(0.2, 0.4, 0.2, 0.2)], 110.0)[0]
        self.assertAlmostEqual(t.stationary_for(110.0), 10.0)
        self.assertTrue(t.has_settled(110.0, 8.0))
        self.assertFalse(t.has_settled(110.0, 12.0))

    def test_a_moving_car_never_accumulates(self):
        now = 100.0
        for i in range(20):
            now += 1.0
            t = self.tracker.update([det(0.2 + i * 0.05, 0.4, 0.2, 0.2)], now)[0]
        self.assertLess(t.stationary_for(now), 2.0)

    def test_a_car_creeping_forward_is_not_mistaken_for_a_parked_one(self):
        """Displacement is measured from where the car settled, not from the
        previous frame — otherwise a car moving less than the jitter allowance
        each frame would read as parked the whole way in, however far it went."""
        now, x = 100.0, 0.20
        creep = STILL_RADIUS * 0.6            # under the threshold every step
        for _ in range(20):
            now += 1.0
            x   += creep
            t = self.tracker.update([det(x, 0.4, 0.2, 0.2)], now)[0]
        self.assertLess(t.stationary_for(now), 3.0)

    def test_the_clock_restarts_when_a_settled_car_moves_off(self):
        self.tracker.update([det(0.2, 0.4, 0.2, 0.2)], 100.0)
        self.tracker.update([det(0.2, 0.4, 0.2, 0.2)], 120.0)
        t = self.tracker.update([det(0.4, 0.4, 0.2, 0.2)], 121.0)[0]
        self.assertAlmostEqual(t.stationary_for(121.0), 0.0)


class OccupancyDwellTests(TestCase):
    """What the dwell gate means for bays."""

    def setUp(self):
        self.zone = ParkingZone.objects.create(name='Dwell', vehicle_category='car')
        self.bay = ParkingSpace.objects.create(zone=self.zone, space_number='A1',
                                               x1=0.10, y1=0.40, x2=0.30, y2=0.70)
        self.thread = pc.ParkingCameraThread(self.zone.id, 'rtsp://unused')
        self.inside = det(0.11, 0.42, 0.18, 0.26)

    def _hits_after(self, seconds):
        now = 100.0
        vehicles = self.thread._tracker.update([self.inside], now)
        now += seconds
        vehicles = self.thread._tracker.update([self.inside], now)
        return self.thread._detector_hits([self.bay], vehicles, now)

    def test_a_car_that_just_arrived_does_not_claim_the_bay(self):
        hits = self._hits_after(pc.PARKED_AFTER_SECONDS - 2)
        self.assertFalse(hits[self.bay.id])

    def test_a_car_that_has_settled_claims_the_bay(self):
        hits = self._hits_after(pc.PARKED_AFTER_SECONDS + 1)
        self.assertTrue(hits[self.bay.id])

    def test_a_car_driving_through_never_claims_the_bay(self):
        """It crosses the bay for several seconds on the way past — long enough
        for the old four-frame hysteresis to latch it as occupied."""
        now = 100.0
        for i in range(30):
            now += 1.0
            moving  = det(0.11 + i * 0.04, 0.42, 0.18, 0.26)
            vehicles = self.thread._tracker.update([moving], now)
            hits = self.thread._detector_hits([self.bay], vehicles, now)
            self.assertFalse(hits[self.bay.id])


class DwellSettingsTests(TestCase):
    """The thresholds are the admin's, not the code's."""

    def setUp(self):
        from accounts.models import User
        from rest_framework.test import APIClient

        # _load_zone_config() calls close_old_connections() — right for a daemon
        # that outlives its connections, fatal for a test running inside one
        # transaction. Same neutralisation as DoubleParkingReportingTests.
        p = patch('django.db.close_old_connections')
        p.start()
        self.addCleanup(p.stop)

        # The thresholds are cached process-wide, which outlives a test's
        # transaction — so a stale entry from a neighbouring test would answer
        # in place of the row this one just wrote.
        pc.invalidate_dwell_settings()
        self.addCleanup(pc.invalidate_dwell_settings)

        self.admin = User.objects.create_user(
            email='dwell-cdso@example.com', full_name='CDSO', password='Passw0rd!23',
            role='admin')
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def _put(self, **overrides):
        payload = {
            'retention_years': 5, 'scan_dedup_seconds': 60,
            'vehicle_pass_fee': 300, 'vehicle_pass_fee_employee': 150,
            'account_expiry_months': 12, 'account_expiry_days': 0,
            'parked_after_seconds': 8, 'double_park_after_seconds': 12,
        }
        payload.update(overrides)
        return self.client.put('/api/vehicles/system-settings/', payload, format='json')

    def test_the_thresholds_round_trip(self):
        resp = self._put(parked_after_seconds=20, double_park_after_seconds=45)
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['parked_after_seconds'], 20)
        self.assertEqual(resp.data['double_park_after_seconds'], 45)

    def test_double_park_shorter_than_parked_is_rejected(self):
        """A car cannot be badly parked before it counts as parked at all."""
        resp = self._put(parked_after_seconds=30, double_park_after_seconds=10)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('double_park_after_seconds', resp.data)

    def test_equal_thresholds_are_allowed(self):
        resp = self._put(parked_after_seconds=15, double_park_after_seconds=15)
        self.assertEqual(resp.status_code, 200, resp.data)

    def test_out_of_range_is_rejected(self):
        self.assertEqual(self._put(parked_after_seconds=0).status_code, 400)
        self.assertEqual(self._put(double_park_after_seconds=999).status_code, 400)

    def test_a_running_zone_picks_up_the_admin_value(self):
        from vehicles.models import SystemSettings
        cfg = SystemSettings.get()
        cfg.parked_after_seconds, cfg.double_park_after_seconds = 30, 60
        cfg.save()
        pc.invalidate_dwell_settings()   # written straight to the model, not via the API

        zone = ParkingZone.objects.create(name='Dwellcfg', vehicle_category='car')
        thread = pc.ParkingCameraThread(zone.id, 'rtsp://unused')
        # Defaults until the first config read, then the admin's values.
        self.assertEqual(thread._parked_after, pc.PARKED_AFTER_SECONDS)
        thread._load_zone_config()
        self.assertEqual(thread._parked_after, 30.0)
        self.assertEqual(thread._double_park_after, 60.0)

    def test_an_unreadable_settings_row_keeps_the_values_in_force(self):
        """A database blip must not silently drop a lot tuned for 60 seconds
        back to fining people at 12."""
        zone = ParkingZone.objects.create(name='Dwellfail', vehicle_category='car')
        thread = pc.ParkingCameraThread(zone.id, 'rtsp://unused')
        thread._parked_after, thread._double_park_after = 30.0, 60.0
        pc.invalidate_dwell_settings()   # force the read to actually hit the DB

        with patch('vehicles.models.SystemSettings.objects') as mgr:
            mgr.filter.side_effect = RuntimeError('db down')
            thread._refresh_dwell()

        self.assertEqual(thread._parked_after, 30.0)
        self.assertEqual(thread._double_park_after, 60.0)


class OccupancyHysteresisTests(TestCase):
    """The counter is evidence toward the next change of state, not a signed
    accumulator — OCCUPY_THR has to mean what it says."""

    def setUp(self):
        self.zone = ParkingZone.objects.create(name='Hyst', vehicle_category='car')
        self.bay = ParkingSpace.objects.create(zone=self.zone, space_number='A1',
                                               x1=0.10, y1=0.40, x2=0.30, y2=0.70)
        self.thread = pc.ParkingCameraThread(self.zone.id, 'rtsp://unused')
        p = patch('django.db.close_old_connections')
        p.start()
        self.addCleanup(p.stop)

    def _free_for(self, frames):
        for _ in range(frames):
            self.thread._apply_hits([self.bay], {self.bay.id: False})

    def _taken_until_occupied(self, limit=200):
        for n in range(1, limit + 1):
            self.thread._apply_hits([self.bay], {self.bay.id: True})
            self.bay.refresh_from_db()
            if self.bay.is_occupied:
                return n
        return None

    def test_claiming_a_long_free_bay_takes_the_documented_frames(self):
        """A bay free for a while sat at -FREE_THR, so claiming it took 24
        frames rather than OCCUPY_THR. Every parked car now passes through that
        floor while the dwell gate holds it, so the configured wait would not be
        the wait an admin gets."""
        self._free_for(40)
        self.assertEqual(self._taken_until_occupied(), pc.OCCUPY_THR)

    def test_a_flickering_detection_does_not_creep_toward_occupied(self):
        """Alternating evidence must not accumulate — that is what the reset is
        for, in both directions."""
        for _ in range(30):
            self.thread._apply_hits([self.bay], {self.bay.id: True})
            self.thread._apply_hits([self.bay], {self.bay.id: False})
        self.bay.refresh_from_db()
        self.assertFalse(self.bay.is_occupied)

    def test_releasing_a_bay_still_takes_the_full_free_threshold(self):
        """The slow side is deliberate: a car hidden for a moment must not free
        its bay."""
        self._free_for(40)
        self._taken_until_occupied()
        for _ in range(pc.FREE_THR - 1):
            self.thread._apply_hits([self.bay], {self.bay.id: False})
        self.bay.refresh_from_db()
        self.assertTrue(self.bay.is_occupied)

        self.thread._apply_hits([self.bay], {self.bay.id: False})
        self.bay.refresh_from_db()
        self.assertFalse(self.bay.is_occupied)


class DwellCacheTests(TestCase):
    """The thresholds are one row shared by every zone, so they are read once
    per process per TTL — not once per zone."""

    def setUp(self):
        p = patch('django.db.close_old_connections')
        p.start()
        self.addCleanup(p.stop)
        pc.invalidate_dwell_settings()
        self.addCleanup(pc.invalidate_dwell_settings)

    def test_many_zones_share_one_read(self):
        from vehicles.models import SystemSettings
        SystemSettings.get()

        threads = []
        for i in range(5):
            zone = ParkingZone.objects.create(name=f'Cache{i}', vehicle_category='car')
            threads.append(pc.ParkingCameraThread(zone.id, 'rtsp://unused'))

        with patch('vehicles.models.SystemSettings.objects') as mgr:
            mgr.filter.return_value.values.return_value.first.return_value = {
                'parked_after_seconds': 25, 'double_park_after_seconds': 40,
            }
            for t in threads:
                t._refresh_dwell()
            self.assertEqual(mgr.filter.call_count, 1)

        for t in threads:
            self.assertEqual(t._parked_after, 25.0)
            self.assertEqual(t._double_park_after, 40.0)

    def test_saving_settings_drops_the_cache(self):
        from vehicles.models import SystemSettings
        SystemSettings.get()
        self.assertIsNotNone(pc.dwell_settings())

        pc.invalidate_dwell_settings()
        with patch('vehicles.models.SystemSettings.objects') as mgr:
            mgr.filter.return_value.values.return_value.first.return_value = {
                'parked_after_seconds': 33, 'double_park_after_seconds': 44,
            }
            self.assertEqual(pc.dwell_settings(), (33.0, 44.0))

    def test_an_unreadable_row_returns_none_rather_than_defaults(self):
        pc.invalidate_dwell_settings()
        with patch('vehicles.models.SystemSettings.objects') as mgr:
            mgr.filter.side_effect = RuntimeError('db down')
            self.assertIsNone(pc.dwell_settings())


class TrackerReadoutTests(TestCase):
    def test_the_readout_reports_dwell_per_vehicle(self):
        zone = ParkingZone.objects.create(name='RO', vehicle_category='car')
        thread = pc.ParkingCameraThread(zone.id, 'rtsp://unused')

        with patch('time.monotonic', return_value=100.0):
            thread._tracker.update([det(0.2, 0.4, 0.2, 0.2)], 100.0)
        with patch('time.monotonic', return_value=100.0 + pc.PARKED_AFTER_SECONDS + 1):
            rows = thread.get_tracked_vehicles()

        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['parked'])
        self.assertGreaterEqual(rows[0]['stationary_seconds'], pc.PARKED_AFTER_SECONDS)
