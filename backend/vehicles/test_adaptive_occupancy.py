"""Fast, sensitive bay occupancy: the claim/release timings, the tracker's
holds and suppressions, and the live baseline that learns while a bay is empty.

The timings are asserted against the constants rather than against literals, so
a zone retuned for a slow camera does not silently invalidate the tests. What is
asserted as a literal is the *property* — that a claim lands inside a second,
that a suppression cannot be bypassed, that nothing can blend a parked car into
a baseline.
"""
import cv2
import numpy as np
from unittest.mock import patch

from django.test import TestCase

from vehicles import bay_occupancy as bo
from vehicles import parking_camera as pc
from vehicles.models import ParkingSpace, ParkingZone
from vehicles.test_bay_occupancy import H, W, empty_lot, park_car


def box(x, y, w, h) -> dict:
    return {'x': x, 'y': y, 'width': w, 'height': h}


# A hair past the claim gate, for the frame that is meant to tip a bay over.
#
# Stepping exactly onto it is not safe arithmetic here: `1000.0 + 0.8` minus
# 1000.0 is 0.79999... in binary floating point, which reads as short of the
# gate. That is a property of the clock values a test picks, not of the gate —
# the real loop compares monotonic readings and never lands on it exactly — and
# the exact boundary is pinned separately in test_vehicle_tracker, where the
# times start from zero and the subtraction is clean.
CLAIM_PAST = pc.BASELINE_CLAIM_SECONDS + 0.05


class _ZoneCase(TestCase):
    """One zone, one bay, and a thread that is never started.

    `_frame` drives exactly what the real loop does per frame — tracker effects,
    then the hysteresis — without a camera, so the timings can be stepped by
    hand.
    """

    # The bay, and a detection box that sits squarely inside it.
    BAY  = dict(x1=0.10, y1=0.40, x2=0.30, y2=0.70)
    OVER = box(0.10, 0.40, 0.20, 0.30)

    def setUp(self):
        self.zone = ParkingZone.objects.create(name='Adaptive', vehicle_category='car')
        self.bay  = ParkingSpace.objects.create(zone=self.zone, space_number='A1',
                                                **self.BAY)
        self.thread = pc.ParkingCameraThread(self.zone.id, 'rtsp://unused')
        p = patch('django.db.close_old_connections')
        p.start()
        self.addCleanup(p.stop)

    # ── one frame of the real pipeline ────────────────────────────────────────
    def _frame(self, now, hit=True, boxes=None, persons=None):
        """Advance one frame. `boxes` are vehicle detections for this frame;
        None means the detector did not run, so the tracker keeps what it has."""
        if boxes is not None:
            self.thread._tracker.update([{'bbox': b} for b in boxes], now)
        if persons is not None:
            self.thread._persons = persons

        holds, suppress, blocked = self.thread._tracker_effects([self.bay], now)
        self.thread._apply_hits([self.bay], {self.bay.id: hit}, now,
                                pc.BASELINE_CLAIM_SECONDS,
                                holds=holds, suppress=suppress)
        self.bay.refresh_from_db()
        return holds, suppress, blocked

    def _occupied(self) -> bool:
        self.bay.refresh_from_db()
        return self.bay.is_occupied

    def _set_occupied(self, value=True):
        self.bay.is_occupied = value
        self.bay.save(update_fields=['is_occupied'])


class ClaimTimingTests(_ZoneCase):
    """A claim needs frames AND seconds, and both together still fit in one
    second at the rate the loop actually runs."""

    def test_frames_alone_do_not_claim(self):
        """OCCUPY_THR frames inside the claim window is not a claim: the whole
        point of the seconds gate is that a fast camera cannot outrun it."""
        for i in range(pc.OCCUPY_THR):
            self._frame(1000.0 + i * 0.1)
        self.assertFalse(self._occupied())

    def test_seconds_alone_do_not_claim(self):
        """Two frames either side of the window clear the seconds gate and must
        still fail — a single flicker an instant before and after is not a car."""
        self._frame(1000.0)
        self._frame(1000.0 + pc.BASELINE_CLAIM_SECONDS)
        self.assertEqual(pc.OCCUPY_THR, 3, 'this case assumes 2 < OCCUPY_THR')
        self.assertFalse(self._occupied())

    def test_both_gates_met_claims(self):
        for i in range(pc.OCCUPY_THR):
            self._frame(1000.0 + i * 0.1)
        self._frame(1000.0 + CLAIM_PAST)
        self.assertTrue(self._occupied())

    def test_claim_lands_within_one_second_at_the_measured_rate(self):
        """The requirement this retuning exists for. Stepped at 9fps, the low
        end of what the loop measures on real frames (see OCCUPY_THR)."""
        start, step = 1000.0, 1.0 / 9.0
        for i in range(40):
            now = start + i * step
            self._frame(now)
            if self._occupied():
                self.assertLessEqual(now - start, 1.0)
                return
        self.fail('bay never claimed')

    def test_a_break_restarts_both_gates(self):
        """Someone crossing the bay twice is two short changes, not one long
        one — the frame streak and the clock both go back to zero."""
        for i in range(pc.OCCUPY_THR):
            self._frame(1000.0 + i * 0.1)
        self._frame(1000.5, hit=False)
        # A single hit a long time later satisfies neither gate on its own.
        self._frame(1000.5 + pc.BASELINE_CLAIM_SECONDS + 5)
        self.assertFalse(self._occupied())


class ReleaseTimingTests(_ZoneCase):
    """Release counts continuous seconds, and any evidence restarts it."""

    def _claim_at(self, t):
        for i in range(pc.OCCUPY_THR):
            self._frame(t + i * 0.1)
        self._frame(t + CLAIM_PAST)
        self.assertTrue(self._occupied())
        return t + CLAIM_PAST

    def test_releases_after_the_full_grace_of_emptiness(self):
        filled = self._claim_at(1000.0)
        self._frame(filled + pc.OCCUPIED_GRACE_SECONDS - 0.5, hit=False)
        self.assertTrue(self._occupied())
        self._frame(filled + pc.OCCUPIED_GRACE_SECONDS, hit=False)
        self.assertFalse(self._occupied())

    def test_a_hit_midway_through_the_grace_restarts_it(self):
        filled = self._claim_at(1000.0)
        mid = filled + pc.OCCUPIED_GRACE_SECONDS / 2
        self._frame(mid, hit=True)
        # The original deadline passes with the bay still taken...
        self._frame(filled + pc.OCCUPIED_GRACE_SECONDS + 0.1, hit=False)
        self.assertTrue(self._occupied())
        # ...and it frees a full grace after the mid-grace sighting instead.
        self._frame(mid + pc.OCCUPIED_GRACE_SECONDS, hit=False)
        self.assertFalse(self._occupied())


class PassingVehicleTests(_ZoneCase):
    """A vehicle crossing a bay must not claim it; the same vehicle must claim
    it once it stops."""

    def test_a_moving_vehicle_does_not_claim_the_bay_it_crosses(self):
        now = 1000.0
        x = 0.10
        # Steps of 0.03 move the centre well past STILL_RADIUS (0.02) every
        # frame, so the track never accumulates stillness, while keeping the box
        # over the bay throughout — a vehicle that has driven clear of the bay
        # is a different case, and correctly has nothing left to suppress.
        for _ in range(6):
            _, suppress, _ = self._frame(now, boxes=[box(x, 0.40, 0.20, 0.30)])
            self.assertIn(self.bay.id, suppress)
            self.assertFalse(self._occupied())
            x += 0.03
            now += 0.1
        self.assertFalse(self._occupied())

    def test_the_same_vehicle_claims_once_it_stops(self):
        now = 1000.0
        for x in (0.10, 0.15, 0.20):
            self._frame(now, boxes=[box(x, 0.40, 0.20, 0.30)])
            now += 0.1
        self.assertFalse(self._occupied())

        stopped_at = now
        parked = box(0.20, 0.40, 0.20, 0.30)
        for _ in range(40):
            self._frame(now, boxes=[parked])
            if self._occupied():
                break
            now += 0.1
        self.assertTrue(self._occupied())
        # Suppression lifts PASSING_MOVE_WINDOW after it settles and the claim
        # gate then runs its course, so the claim lands about their sum later.
        self.assertLessEqual(
            now - stopped_at,
            pc.PASSING_MOVE_WINDOW + pc.BASELINE_CLAIM_SECONDS + 0.3)

    def test_no_track_means_no_suppression(self):
        """A detector that simply missed the car must leave the baseline to
        claim it exactly as it did before any of this existed."""
        now = 1000.0
        for i in range(pc.OCCUPY_THR):
            _, suppress, _ = self._frame(now + i * 0.1, boxes=[])
            self.assertEqual(suppress, {})
        self._frame(now + CLAIM_PAST, boxes=[])
        self.assertTrue(self._occupied())


class ReleaseHoldTests(_ZoneCase):
    """A settled vehicle holds a bay open — and can never claim one."""

    def _settle(self, now, frames=6, step=1.0):
        """Park a box over the bay and let it accumulate stillness."""
        for i in range(frames):
            self.thread._tracker.update([{'bbox': self.OVER}], now + i * step)
        return now + (frames - 1) * step

    def test_a_stationary_vehicle_holds_an_occupied_bay_open(self):
        self._set_occupied(True)
        now = self._settle(1000.0)
        # Well past the grace, every frame scoring free, and the bay stays
        # taken because the tracker keeps saying something is sitting in it.
        for i in range(int(pc.OCCUPIED_GRACE_SECONDS * 2)):
            holds, _, _ = self._frame(now + i, hit=False, boxes=[self.OVER])
            self.assertIn(self.bay.id, holds)
        self.assertTrue(self._occupied())

    def test_the_hold_ends_with_the_vehicle_and_the_bay_then_frees(self):
        self._set_occupied(True)
        now = self._settle(1000.0)
        self._frame(now, hit=False, boxes=[self.OVER])
        # Vehicle gone: no hold, and a full grace of emptiness frees the bay.
        gone = now + 1
        self._frame(gone, hit=False, boxes=[])
        self._frame(gone + pc.OCCUPIED_GRACE_SECONDS, hit=False, boxes=[])
        self.assertFalse(self._occupied())

    def test_a_stationary_vehicle_never_claims_an_empty_bay(self):
        """The rule the whole design rests on: the detector holds and
        suppresses, it does not claim. A bay the scorer calls free stays free
        however long a box sits on it."""
        now = self._settle(1000.0)
        for i in range(60):
            holds, _, _ = self._frame(now + i, hit=False, boxes=[self.OVER])
            self.assertIn(self.bay.id, holds)
            self.assertFalse(self._occupied())


class PersonSuppressionTests(_ZoneCase):
    """Someone standing in an empty bay reads like a car arriving; a rider on a
    motorbike does not."""

    PERSON = [{'bbox': box(0.10, 0.40, 0.20, 0.30), 'score': 0.9}]

    def test_a_person_over_an_empty_bay_suppresses_the_claim(self):
        now = 1000.0
        for i in range(20):
            _, suppress, _ = self._frame(now + i * 0.1, persons=self.PERSON, boxes=[])
            self.assertIn(self.bay.id, suppress)
        self.assertFalse(self._occupied())

    def test_a_person_with_a_vehicle_does_not_suppress(self):
        """A rider astride a motorbike, or a tricycle driver: a person over a
        bay that genuinely is taken. Suppressing these would make every
        two-wheeler slow to report."""
        now = self._settle_vehicle(1000.0)
        for i in range(pc.OCCUPY_THR):
            _, suppress, _ = self._frame(now + i * 0.1,
                                         persons=self.PERSON, boxes=[self.OVER])
            self.assertEqual(suppress, {})
        self._frame(now + CLAIM_PAST,
                    persons=self.PERSON, boxes=[self.OVER])
        self.assertTrue(self._occupied())

    def _settle_vehicle(self, now):
        for i in range(6):
            self.thread._tracker.update([{'bbox': self.OVER}], now + i)
        return now + 5

    def test_person_detection_is_off_without_a_configured_model(self):
        """The campus detector has no person class, so the rule is inert rather
        than absent — and inert must mean "behaves as it did before"."""
        self.assertEqual(self.thread._detect_people(np.zeros((64, 64, 3), np.uint8)), [])


class AdaptiveThresholdTests(TestCase):
    """Each bay's thresholds come from its own noise, inside a band whose
    ceiling is the old global value."""

    def test_no_stats_falls_back(self):
        mad, edge, adaptive = bo.thresholds_for(None)
        self.assertFalse(adaptive)
        self.assertEqual((mad, edge), (bo.MAD_FALLBACK, bo.EDGE_FALLBACK))

    def test_too_few_samples_falls_back(self):
        stats = {'samples': bo.NOISE_MIN_SAMPLES - 1,
                 'mad_mean': 1.0, 'mad_std': 0.1,
                 'edge_mean': 0.001, 'edge_std': 0.0001}
        self.assertFalse(bo.thresholds_for(stats)[2])

    def test_a_quiet_bay_clamps_to_the_floor(self):
        stats = {'samples': 100, 'mad_mean': 0.5, 'mad_std': 0.05,
                 'edge_mean': 0.0001, 'edge_std': 0.00001}
        mad, edge, adaptive = bo.thresholds_for(stats)
        self.assertTrue(adaptive)
        self.assertEqual(mad, bo.MAD_FLOOR)
        self.assertEqual(edge, bo.EDGE_FLOOR)

    def test_a_noisy_bay_clamps_to_the_ceiling(self):
        """And the ceiling is the old global value, which is what makes this
        change unable to leave any bay less sensitive than it was."""
        stats = {'samples': 100, 'mad_mean': 35.0, 'mad_std': 10.0,
                 'edge_mean': 0.03, 'edge_std': 0.02}
        mad, edge, _ = bo.thresholds_for(stats)
        self.assertEqual(mad, bo.MAD_CEILING)
        self.assertEqual(edge, bo.EDGE_CEILING)
        self.assertEqual(bo.MAD_CEILING, bo.MAD_THR)
        self.assertEqual(bo.EDGE_CEILING, bo.EDGE_DELTA_THR)

    def test_a_middling_bay_lands_between(self):
        stats = {'samples': 100, 'mad_mean': 10.0, 'mad_std': 3.0,
                 'edge_mean': 0.010, 'edge_std': 0.002}
        mad, edge, _ = bo.thresholds_for(stats)
        self.assertAlmostEqual(mad, 10.0 + bo.MAD_NOISE_K * 3.0)
        self.assertAlmostEqual(edge, 0.010 + bo.EDGE_NOISE_K * 0.002)
        # ...and that is inside the band, not against either end of it.
        self.assertTrue(bo.MAD_FLOOR < mad < bo.MAD_CEILING)
        self.assertTrue(bo.EDGE_FLOOR < edge < bo.EDGE_CEILING)

    def test_the_vote_still_needs_three_points(self):
        """Adaptive thresholds must not change the arithmetic: the two
        corroborating signals together still lose."""
        tight = dict(mad_thr=bo.MAD_FLOOR, edge_thr=bo.EDGE_FLOOR)
        self.assertLess(bo.score_votes(0.0, 0.5, 0.0, **tight), bo.VOTES_REQUIRED)
        self.assertLess(bo.score_votes(0.99, 1.0, 0.0, **tight), bo.VOTES_REQUIRED)
        self.assertGreaterEqual(bo.score_votes(0.0, 0.5, 999.0, **tight),
                                bo.VOTES_REQUIRED)

    def test_noise_stats_use_edge_magnitude(self):
        """A bay that drifts both ways would otherwise average to nearly zero
        and get a threshold far tighter than its actual noise."""
        stats = bo.NoiseStats()
        for v in (0.02, -0.02) * 20:
            stats.add(10.0, v)
        self.assertAlmostEqual(stats.summary()['edge_mean'], 0.02, places=4)


class LiveBaselineTests(TestCase):
    """The captured baseline is never written to, and nothing a bay is not
    certain is empty may teach it anything."""

    def setUp(self):
        self.zone = ParkingZone.objects.create(name='Drift', vehicle_category='car')
        self.bay = ParkingSpace.objects.create(zone=self.zone, space_number='A1',
                                               x1=0.10, y1=0.20, x2=0.45, y2=0.75)
        self.baseline = empty_lot()
        self.prepared = bo.prepare_zone(self.baseline, [self.bay], (H, W), 'tok')
        self.prep_bay = self.prepared.bays[0]

    def _clear_run(self, seconds, start=1000.0, frame=None, blocked=()):
        """Score an empty frame repeatedly across `seconds` of clock."""
        frame = empty_lot(seed=11) if frame is None else frame
        bo.evaluate(self.prepared, frame, start, blocked)
        bo.evaluate(self.prepared, frame, start + seconds, blocked)
        return start + seconds

    def test_a_long_empty_run_becomes_due_and_blends(self):
        now = self._clear_run(bo.REFRESH_CLEAR_SECONDS + 1)
        due = bo.refresh_due(self.prepared, now)
        self.assertEqual([b.space_id for b in due], [self.bay.id])

        before = self.prep_bay.live_gray.copy()
        bo.refresh_live(self.prepared, empty_lot(seed=12), due, now)
        self.assertEqual(self.prep_bay.blends, 1)
        self.assertFalse(np.array_equal(before, self.prep_bay.live_gray))
        # The captured baseline is untouched, which is what a reset returns to.
        self.assertTrue(np.array_equal(
            self.prep_bay.base_gray,
            bo.prepare_zone(self.baseline, [self.bay], (H, W), 'tok').bays[0].base_gray))

    def test_a_single_hit_restarts_the_five_minutes(self):
        """The condition a parked car has to defeat to be absorbed, and it
        cannot: one frame that scores taken is enough to start the wait over."""
        now = self._clear_run(bo.REFRESH_CLEAR_SECONDS - 5)
        taken = park_car(empty_lot(seed=11), 40, 60, 140, 190)
        bo.evaluate(self.prepared, taken, now)
        self.assertIsNone(self.prep_bay.clear_since)
        self.assertEqual(bo.refresh_due(self.prepared, now + 10), [])

    def test_a_parked_car_is_never_due_however_long_it_stays(self):
        taken = park_car(empty_lot(seed=11), 40, 60, 140, 190)
        now = 1000.0
        for i in range(12):
            bo.evaluate(self.prepared, taken, now + i * 60)
        self.assertEqual(bo.refresh_due(self.prepared, now + 12 * 60), [])

    def test_an_overlapping_box_blocks_the_refresh(self):
        now = self._clear_run(bo.REFRESH_CLEAR_SECONDS + 1, blocked={self.bay.id})
        self.assertIsNone(self.prep_bay.clear_since)
        self.assertEqual(bo.refresh_due(self.prepared, now), [])

    def test_low_light_blocks_the_refresh(self):
        """Compensation keeps a uniformly dark frame scoring free, so without
        the brightness floor a night of sensor noise would blend straight into
        a daytime baseline."""
        dark = np.full((H, W, 3), 15, np.uint8)
        result = bo.evaluate(self.prepared, dark, 1000.0)
        self.assertFalse(result[self.bay.id]['occupied'])
        self.assertLess(result[self.bay.id]['brightness'], bo.REFRESH_MIN_BRIGHTNESS)
        self.assertIsNone(self.prep_bay.clear_since)

    def test_blends_are_rate_limited(self):
        now = self._clear_run(bo.REFRESH_CLEAR_SECONDS + 1)
        bo.refresh_live(self.prepared, empty_lot(seed=12),
                        bo.refresh_due(self.prepared, now), now)
        self.assertEqual(bo.refresh_due(self.prepared, now + 1), [])
        self.assertEqual(
            [b.space_id for b in
             bo.refresh_due(self.prepared, now + bo.REFRESH_INTERVAL_SECONDS)],
            [self.bay.id])

    def test_reset_returns_the_live_baseline_to_the_captured_one(self):
        now = self._clear_run(bo.REFRESH_CLEAR_SECONDS + 1)
        bo.refresh_live(self.prepared, empty_lot(seed=12),
                        bo.refresh_due(self.prepared, now), now)
        self.assertFalse(np.array_equal(self.prep_bay.base_gray,
                                        self.prep_bay.live_gray))
        self.assertEqual(bo.reset_live_baseline(self.prepared, self.bay.id), 1)
        self.assertTrue(np.array_equal(self.prep_bay.base_gray,
                                       self.prep_bay.live_gray))
        self.assertEqual(self.prep_bay.blends, 0)

    def test_noise_is_only_learned_from_confidently_empty_frames(self):
        taken = park_car(empty_lot(seed=11), 40, 60, 140, 190)
        results = bo.evaluate(self.prepared, taken, 1000.0)
        bo.sample_noise(self.prepared, results)
        self.assertEqual(self.prep_bay.noise.samples, 0)

        results = bo.evaluate(self.prepared, empty_lot(seed=11), 1001.0)
        bo.sample_noise(self.prepared, results)
        self.assertEqual(self.prep_bay.noise.samples, 1)

    def test_stats_from_an_empty_bay_derive_a_usable_threshold(self):
        """The end-to-end of section 1: score an empty bay for a while and it
        ends up judging itself by its own measured noise."""
        for i in range(bo.NOISE_MIN_SAMPLES + 5):
            results = bo.evaluate(self.prepared, empty_lot(seed=20 + i), 1000.0 + i)
            bo.sample_noise(self.prepared, results)
        summary = self.prep_bay.noise.summary()
        self.assertGreaterEqual(summary['samples'], bo.NOISE_MIN_SAMPLES)

        self.prep_bay.apply_stats(summary)
        self.assertTrue(self.prep_bay.adaptive)
        self.assertLessEqual(self.prep_bay.mad_thr, bo.MAD_CEILING)
        self.assertGreaterEqual(self.prep_bay.mad_thr, bo.MAD_FLOOR)
        # And the bay still reports a car parked in it.
        taken = park_car(empty_lot(seed=11), 40, 60, 140, 190)
        self.assertTrue(bo.evaluate(self.prepared, taken, 2000.0)[self.bay.id]['occupied'])


class NoiseStatsPersistenceTests(_ZoneCase):
    """Stats are written without touching updated_at, because updated_at is
    what invalidates the prepared baseline."""

    def test_stats_are_written_without_bumping_updated_at(self):
        prepared = bo.prepare_zone(empty_lot(), [self.bay], (H, W), 'tok')
        prep_bay = prepared.bays[0]
        for i in range(bo.NOISE_MIN_SAMPLES + 2):
            prep_bay.noise.add(3.0 + (i % 3), 0.002)
        self.thread._prepared = prepared

        before = ParkingSpace.objects.get(pk=self.bay.id).updated_at
        self.thread._stats_saved_at = 0.0
        self.thread._persist_noise_stats(prepared, pc.STATS_SAVE_SECONDS + 1)

        row = ParkingSpace.objects.get(pk=self.bay.id)
        self.assertEqual(row.updated_at, before,
                         'saving stats must not invalidate the prepared baseline')
        self.assertGreaterEqual(row.noise_stats['samples'], bo.NOISE_MIN_SAMPLES)
        self.assertTrue(prep_bay.adaptive)

    def test_stored_stats_are_picked_up_when_a_zone_is_prepared(self):
        ParkingSpace.objects.filter(pk=self.bay.id).update(noise_stats={
            'samples': 100, 'mad_mean': 10.0, 'mad_std': 3.0,
            'edge_mean': 0.010, 'edge_std': 0.002})
        space = ParkingSpace.objects.get(pk=self.bay.id)
        prepared = bo.prepare_zone(empty_lot(), [space], (H, W), 'tok')
        self.assertTrue(prepared.bays[0].adaptive)
        self.assertAlmostEqual(prepared.bays[0].mad_thr, 10.0 + bo.MAD_NOISE_K * 3.0)


class DiagnosticsTests(_ZoneCase):
    """A bay that will not claim, or will not free, has to be able to say why."""

    def test_signals_carry_thresholds_drift_and_the_last_reason(self):
        prepared = bo.prepare_zone(empty_lot(), [self.bay], (H, W), 'tok')
        self.thread._prepared = prepared
        self.thread._signals = bo.evaluate(prepared, empty_lot(seed=9), 1000.0)

        # A person over the bay, so there is a suppression to report.
        self._frame(1000.0, persons=[{'bbox': self.OVER, 'score': 0.91}], boxes=[])

        row = self.thread.get_signals()[self.bay.id]
        self.assertIn('mad_thr', row)
        self.assertIn('edge_thr', row)
        self.assertEqual(row['threshold_source'], 'fallback')
        self.assertFalse(row['live_baseline']['drifted'])
        self.assertEqual(row['live_baseline']['blends'], 0)
        self.assertEqual(row['last_event']['kind'], 'suppressed')
        self.assertIn('person', row['last_event']['reason'])
        # Reported as an age, not as the monotonic stamp it is stored as.
        self.assertIsInstance(row['last_event']['ago'], float)
        self.assertNotIn('at', row['last_event'])

    def test_a_hold_is_reported_as_the_reason_a_bay_stays_taken(self):
        self._set_occupied(True)
        now = 1000.0
        for i in range(6):
            self.thread._tracker.update([{'bbox': self.OVER}], now + i)
        self.thread._prepared = bo.prepare_zone(empty_lot(), [self.bay], (H, W), 'tok')
        self.thread._signals = {self.bay.id: {'occupied': False}}

        self._frame(now + 5, hit=False, boxes=[self.OVER])
        row = self.thread.get_signals()[self.bay.id]
        self.assertEqual(row['last_event']['kind'], 'held')

    def test_measured_fps_reflects_the_frames_actually_scored(self):
        self.assertIsNone(self.thread.measured_fps())
        for i in range(10):
            self.thread._frame_times.append(1000.0 + i * 0.1)
        self.assertAlmostEqual(self.thread.measured_fps(), 10.0, places=1)


class RefreshSafetyTests(_ZoneCase):
    """The property the whole refresh design exists to guarantee: a parked car
    can never be blended into the baseline that decides the bay is free."""

    def test_a_claimed_bay_is_never_refreshable_even_if_the_scorer_loses_it(self):
        """The dangerous case, and the one the per-frame verdict alone misses.

        A pale car in flat light can read free to the scorer for a long stretch
        while plainly sitting in the bay. If "confidently empty" meant only
        "this frame scored free", that stretch would run the five-minute clock
        out and blend the car in — freeing an occupied bay and sending the next
        driver into it. The bay's claimed state has to block it too.
        """
        prepared = bo.prepare_zone(empty_lot(), [self.bay], (H, W), 'tok')
        self.thread._prepared = prepared
        self._set_occupied(True)

        frame = empty_lot(seed=11)
        now = 1000.0
        # An hour of the scorer reading the bay free, with no detector box
        # either — every excuse the refresh could have to run.
        for _ in range(120):
            _, _, blocked = self.thread._tracker_effects([self.bay], now)
            self.assertIn(self.bay.id, blocked)
            bo.evaluate(prepared, frame, now, blocked)
            self.assertIsNone(prepared.bays[0].clear_since)
            self.assertEqual(bo.refresh_due(prepared, now), [])
            now += 30.0
        self.assertEqual(prepared.bays[0].blends, 0)

    def test_an_unclaimed_bay_with_a_box_on_it_is_never_refreshable(self):
        """The other half: a bay nobody has claimed yet, with a vehicle sitting
        in it that the scorer has not caught up with."""
        prepared = bo.prepare_zone(empty_lot(), [self.bay], (H, W), 'tok')
        self.thread._prepared = prepared
        now = 1000.0
        for i in range(8):
            self.thread._tracker.update([{'bbox': self.OVER}], now + i)
        for _ in range(40):
            _, _, blocked = self.thread._tracker_effects([self.bay], now)
            self.assertIn(self.bay.id, blocked)
            bo.evaluate(prepared, empty_lot(seed=11), now, blocked)
            self.assertEqual(bo.refresh_due(prepared, now), [])
            now += 30.0

    def test_a_genuinely_empty_unclaimed_bay_does_refresh(self):
        """The control. Without this the two cases above would also pass with
        the refresh simply broken."""
        prepared = bo.prepare_zone(empty_lot(), [self.bay], (H, W), 'tok')
        self.thread._prepared = prepared
        now, frame = 1000.0, empty_lot(seed=11)
        for _ in range(40):
            _, _, blocked = self.thread._tracker_effects([self.bay], now)
            self.assertNotIn(self.bay.id, blocked)
            bo.evaluate(prepared, frame, now, blocked)
            now += 30.0
        self.assertEqual([b.space_id for b in bo.refresh_due(prepared, now)],
                         [self.bay.id])


class LearnBaselineWiringTests(_ZoneCase):
    """The two production wrappers around the scorer's learning functions.

    The functions themselves are covered above; these run them the way the
    camera loop does, which is where a wrong argument or a swallowed exception
    would otherwise hide — `_learn_baseline` catches everything by design, so a
    typo in it would look exactly like a bay that simply never learns.
    """

    def _prepare(self):
        prepared = bo.prepare_zone(empty_lot(), [self.bay], (H, W), 'tok')
        self.thread._prepared = prepared
        return prepared

    def test_learn_baseline_collects_noise_and_persists_it(self):
        prepared = self._prepare()
        frame, now = empty_lot(seed=11), 1000.0
        for _ in range(bo.NOISE_MIN_SAMPLES + 4):
            self.thread._signals = bo.evaluate(prepared, frame, now, ())
            self.thread._learn_baseline(frame, now)
            now += 0.1

        self.assertGreaterEqual(prepared.bays[0].noise.samples, bo.NOISE_MIN_SAMPLES)
        row = ParkingSpace.objects.get(pk=self.bay.id)
        self.assertIsNotNone(row.noise_stats)
        self.assertTrue(prepared.bays[0].adaptive)

    def test_learn_baseline_blends_once_the_bay_has_been_clear_long_enough(self):
        prepared = self._prepare()
        frame, now = empty_lot(seed=11), 1000.0
        self.thread._signals = bo.evaluate(prepared, frame, now, ())
        self.thread._learn_baseline(frame, now)

        now += bo.REFRESH_CLEAR_SECONDS + 1
        self.thread._signals = bo.evaluate(prepared, frame, now, ())
        self.thread._learn_baseline(frame, now)
        self.assertEqual(prepared.bays[0].blends, 1)

    def test_learn_baseline_survives_a_broken_scorer(self):
        """It is called on every frame of a live camera; a failure here must
        cost the bay its learning, not the zone its occupancy."""
        prepared = self._prepare()
        self.thread._signals = {'not': 'a valid signals map'}
        self.thread._learn_baseline(empty_lot(), 1000.0)   # must not raise
        self.assertEqual(prepared.bays[0].blends, 0)

    def test_thread_reset_returns_the_bay_to_the_captured_baseline(self):
        prepared = self._prepare()
        frame, now = empty_lot(seed=11), 1000.0
        self.thread._signals = bo.evaluate(prepared, frame, now, ())
        self.thread._learn_baseline(frame, now)
        now += bo.REFRESH_CLEAR_SECONDS + 1
        self.thread._signals = bo.evaluate(prepared, frame, now, ())
        self.thread._learn_baseline(frame, now)
        self.assertEqual(prepared.bays[0].blends, 1)

        self.assertEqual(self.thread.reset_live_baseline(self.bay.id), 1)
        self.assertEqual(prepared.bays[0].blends, 0)
        self.assertTrue(np.array_equal(prepared.bays[0].base_gray,
                                       prepared.bays[0].live_gray))

    def test_reset_on_a_zone_with_no_prepared_baseline_is_a_no_op(self):
        self.thread._prepared = None
        self.assertEqual(self.thread.reset_live_baseline(), 0)


class EndToEndFrameTests(_ZoneCase):
    """The whole assembled pipeline on real pixels.

    Everything above tests a piece: the scorer on frames, the hysteresis on
    booleans, the tracker effects on boxes. This drives `_process_frame` itself
    — scoring, tracker effects, hysteresis and learning in the order the camera
    loop runs them — because the wiring between those pieces is exactly what
    unit tests cannot see, and it is where a claim that works in isolation
    quietly stops happening.
    """

    def setUp(self):
        super().setUp()
        self.clock = 1000.0
        # A bay filling most of the frame's left half, so a parked car moves the
        # scorer decisively rather than marginally.
        ParkingSpace.objects.filter(pk=self.bay.id).update(
            x1=0.10, y1=0.20, x2=0.45, y2=0.75)
        self.bay.refresh_from_db()

        self.boxes = []
        t = self.thread
        t._load_spaces      = lambda: [self.bay]
        t._load_zone_config = lambda: {'baseline': 'b.jpg', 'token': 'tok'}
        t._read_baseline    = lambda name: empty_lot()
        t._detect           = lambda frame: list(self.boxes)
        t._detect_people    = lambda frame: []

        p = patch('vehicles.parking_camera.time.monotonic', lambda: self.clock)
        p.start()
        self.addCleanup(p.stop)

    def _run(self, frame, seconds, step=1 / 9.0, boxes=None):
        """Feed frames for `seconds` of clock at the measured loop rate."""
        if boxes is not None:
            self.boxes = boxes
        for _ in range(max(1, int(seconds / step))):
            self.thread._process_frame(frame)
            self.clock += step
        self.bay.refresh_from_db()

    def test_a_car_arrives_is_claimed_within_a_second_and_released_after_it_goes(self):
        empty  = empty_lot(seed=11)
        parked = park_car(empty_lot(seed=11), 40, 60, 140, 190)

        self._run(empty, 2.0)
        self.assertFalse(self.bay.is_occupied, 'empty bay must not claim')

        # The car appears. With no detector box behind it there is nothing to
        # suppress, so the baseline claims it on the ordinary gates.
        arrived = self.clock
        for _ in range(40):
            self.thread._process_frame(parked)
            self.clock += 1 / 9.0
            self.bay.refresh_from_db()
            if self.bay.is_occupied:
                break
        self.assertTrue(self.bay.is_occupied, 'car never claimed')
        self.assertLessEqual(self.clock - arrived, 1.0,
                             'claim must land inside one second')

        # It stays claimed while it sits there.
        self._run(parked, 30.0)
        self.assertTrue(self.bay.is_occupied)

        # It leaves: a full grace of continuous emptiness frees the bay, and
        # not a moment before.
        left = self.clock
        self._run(empty, pc.OCCUPIED_GRACE_SECONDS - 1.0)
        self.assertTrue(self.bay.is_occupied, 'freed before the grace elapsed')
        self._run(empty, 2.0)
        self.assertFalse(self.bay.is_occupied, 'never freed after the grace')
        self.assertLessEqual(self.clock - left, pc.OCCUPIED_GRACE_SECONDS + 2.0)

    def test_a_car_driving_across_the_bay_never_claims_it(self):
        """The false positive this whole suppression path exists to stop, run
        on real pixels: the bay genuinely scores changed every frame."""
        self._run(empty_lot(seed=11), 1.0)
        x = 0.02
        for _ in range(45):
            frame = park_car(empty_lot(seed=11),
                             int(x * W), 60, int(x * W) + 100, 190)
            self.boxes = [{'bbox': box(x, 0.25, 100 / W, 130 / H),
                           'score': 0.8, 'class_name': 'vehicle'}]
            self.thread._process_frame(frame)
            self.clock += 1 / 9.0
            x += 0.03                      # always past STILL_RADIUS
            self.bay.refresh_from_db()
            self.assertFalse(self.bay.is_occupied,
                             'a vehicle crossing the bay claimed it')

    def test_an_empty_zone_learns_its_own_thresholds_from_real_frames(self):
        """Section 1 end to end: run an empty bay and it stops using the
        fallback pair and starts judging itself by its own measured noise."""
        for i in range(40):
            self.thread._process_frame(empty_lot(seed=200 + i))
            self.clock += 1 / 9.0

        prepared = self.thread._prepared
        self.assertIsNotNone(prepared)
        self.assertGreaterEqual(prepared.bays[0].noise.samples, bo.NOISE_MIN_SAMPLES)
        self.assertFalse(self.bay.is_occupied)

        # And a car parked in it is still reported under those tighter numbers.
        self._run(park_car(empty_lot(seed=11), 40, 60, 140, 190), 2.0)
        self.assertTrue(self.bay.is_occupied)

    def test_a_zone_with_no_baseline_scores_nothing(self):
        """The 'not set up' rule, which this change must not have weakened."""
        self.thread._read_baseline = lambda name: None
        self._run(park_car(empty_lot(seed=11), 40, 60, 140, 190), 5.0)
        self.assertFalse(self.bay.is_occupied)
        self.assertIsNone(self.thread._prepared)


# ── Shadow veto ───────────────────────────────────────────────────────────────

def textured_ground(seed=7, grain=1.0):
    """One fixed patch of asphalt, with grain.

    A FIXED ground, unlike empty_lot(), because that is the whole point: a
    bolted-down camera sees the same stones in the same places every frame, and
    correlation against the baseline is only meaningful against that. A ground
    regenerated per frame correlates with nothing, and would make every one of
    these tests pass for the wrong reason.
    """
    rng = np.random.RandomState(seed)
    f = np.full((H, W, 3), 120, np.uint8)
    for _ in range(int(900 * grain)):
        x, y = rng.randint(0, W), rng.randint(0, H)
        cv2.circle(f, (x, y), rng.randint(1, 3), (int(rng.randint(95, 150)),) * 3, -1)
    return f


def captured(ground, seed, noise=4):
    """One frame off that ground: the same stones, fresh sensor noise."""
    rng = np.random.RandomState(seed)
    return np.clip(ground.astype(np.int16)
                   + rng.randint(-noise, noise + 1, (H, W, 3), dtype=np.int16),
                   0, 255).astype(np.uint8)


def cast_shadow(frame, bay_px, frac=1.0, drop=45, penumbra=0):
    """Darken part of a bay by scaling its illumination.

    Multiplicative and applied through a mask, because that is what a shadow
    physically is. Darkening by subtraction, or blurring the whole image to fake
    a soft edge, would also destroy the ground's texture — and then these tests
    would be asserting against something that does not happen outdoors.
    """
    x1, y1, x2, y2 = bay_px
    m = np.zeros((H, W), np.float32)
    m[y1:y2, x1:int(x1 + (x2 - x1) * frac)] = 1.0
    if penumbra:
        m = cv2.GaussianBlur(m, (penumbra * 2 + 1,) * 2, 0)
    return np.clip(frame.astype(np.float32) * (1.0 - (drop / 120.0) * m)[..., None],
                   0, 255).astype(np.uint8)


def put_vehicle(frame, bay_px, grey=60):
    x1, y1, x2, y2 = bay_px
    out = frame.copy()
    cv2.rectangle(out, (x1 + 6, y1 + 10), (x2 - 6, y2 - 6), (grey,) * 3, -1)
    return out


class ShadowVetoTests(TestCase):
    """A shadow scales the light and leaves the ground's grain untouched; a
    vehicle covers the grain. That is the difference the tonal vote cannot see,
    and the one the veto is measured on."""

    BAY_PX = (32, 48, 144, 180)          # the bay's normalised box, in pixels

    def setUp(self):
        self.zone = ParkingZone.objects.create(name='Shadow', vehicle_category='car')
        self.bay = ParkingSpace.objects.create(zone=self.zone, space_number='A1',
                                               x1=0.10, y1=0.20, x2=0.45, y2=0.75)
        self.ground = textured_ground()
        self.prepared = bo.prepare_zone(captured(self.ground, 1), [self.bay],
                                        (H, W), 'tok')
        self.prep_bay = self.prepared.bays[0]

    def _learn(self, frames=200):
        """Let the bay measure what it correlates at while empty."""
        t = 1000.0
        for i in range(frames):
            bo.evaluate(self.prepared, captured(self.ground, 50 + i), t)
            t += 0.1
        return t

    def _score(self, frame, now=9000.0):
        return bo.evaluate(self.prepared, frame, now)[self.bay.id]

    # ── the reference the veto needs ─────────────────────────────────────────
    def test_the_veto_is_off_until_the_bay_has_measured_itself(self):
        """Before it knows what it looks like empty, a bay cannot tell a shadow
        from a car — so it behaves exactly as it did before the veto existed,
        rather than guessing."""
        self.assertEqual(self.prep_bay.veto_thr, 0.0)
        r = self._score(cast_shadow(captured(self.ground, 2), self.BAY_PX, 1.0, 45))
        self.assertFalse(r['shadow_veto'])

    def test_an_empty_bay_learns_a_high_structure_correlation(self):
        self._learn()
        self.assertGreater(self.prep_bay.ncc_base, bo.NCC_MIN_BASE)
        self.assertAlmostEqual(self.prep_bay.veto_thr,
                               bo.NCC_VETO_FRACTION * self.prep_bay.ncc_base)

    # ── shadows ──────────────────────────────────────────────────────────────
    def test_shadows_do_not_claim_an_empty_bay(self):
        """The regression this exists for. Several of these reached the
        three-point vote on tone alone and claimed a bay with nothing in it."""
        self._learn()
        for frac, drop, pen in ((1.0, 30, 0), (1.0, 45, 0), (1.0, 60, 0),
                                (1.0, 75, 0), (0.5, 45, 0), (0.5, 45, 6),
                                (0.7, 50, 10), (0.3, 55, 0)):
            frame = cast_shadow(captured(self.ground, 300), self.BAY_PX,
                                frac, drop, pen)
            r = self._score(frame)
            self.assertFalse(
                r['occupied'],
                "shadow %d%% -%d pen=%d claimed the bay (mad=%s ncc=%s thr=%s)"
                % (int(frac * 100), drop, pen, r['mad'], r['ncc'], r['veto_thr']))

    def test_a_deep_shadow_is_vetoed_rather_than_merely_unscored(self):
        """Distinguishes the veto doing its job from the vote happening to fall
        short — without this the case above could pass with the veto broken."""
        self._learn()
        r = self._score(cast_shadow(captured(self.ground, 301), self.BAY_PX, 1.0, 75))
        self.assertTrue(r['shadow_veto'])
        self.assertGreaterEqual(r['mad'], self.prep_bay.mad_thr,
                                'only meaningful if tone did claim the bay')
        self.assertGreaterEqual(r['ncc'], r['veto_thr'])

    # ── vehicles must be unaffected ──────────────────────────────────────────
    def test_vehicles_are_never_vetoed(self):
        self._learn()
        for grey in (80, 60, 45, 30):
            r = self._score(put_vehicle(captured(self.ground, 400), self.BAY_PX, grey))
            self.assertFalse(r['shadow_veto'], "grey %d vehicle was vetoed" % grey)
            self.assertTrue(r['occupied'], "grey %d vehicle not reported" % grey)

    def test_a_vehicle_under_a_shadow_is_still_reported(self):
        """Both at once, which is an ordinary afternoon: the car still covers
        the grain, so the structure is gone and the veto must not fire."""
        self._learn()
        frame = cast_shadow(put_vehicle(captured(self.ground, 401), self.BAY_PX, 60),
                            self.BAY_PX, 1.0, 45)
        r = self._score(frame)
        self.assertFalse(r['shadow_veto'])
        self.assertTrue(r['occupied'])

    # ── the reference must not be poisoned ───────────────────────────────────
    def test_shadowed_frames_do_not_lower_the_learned_reference(self):
        """If a vetoed frame fed the reference, every shadow would drag it down
        and the veto would quietly disarm itself over an afternoon."""
        self._learn()
        before = self.prep_bay.ncc_base
        t = 9000.0
        for i in range(200):
            bo.evaluate(self.prepared,
                        cast_shadow(captured(self.ground, 600 + i), self.BAY_PX,
                                    1.0, 60), t)
            t += 0.1
        self.assertAlmostEqual(self.prep_bay.ncc_base, before, places=6)

    def test_featureless_ground_disables_the_veto(self):
        """Nothing to correlate means nothing to conclude. A bay like this falls
        back to the tonal vote alone rather than acting on noise."""
        flat = bo.prepare_zone(empty_lot(), [self.bay], (H, W), 'tok')
        t = 1000.0
        for i in range(200):
            bo.evaluate(flat, empty_lot(seed=50 + i), t)
            t += 0.1
        self.assertLess(flat.bays[0].ncc_base, bo.NCC_MIN_BASE)
        self.assertEqual(flat.bays[0].veto_thr, 0.0)

    def test_stored_reference_survives_a_restart(self):
        """A zone restarting at noon must not spend the afternoon relearning
        this with the veto switched off."""
        self._learn()
        summary = self.prep_bay.noise.summary()
        self.assertIsNotNone(summary['ncc_base'])

        ParkingSpace.objects.filter(pk=self.bay.id).update(noise_stats=summary)
        space = ParkingSpace.objects.get(pk=self.bay.id)
        fresh = bo.prepare_zone(captured(self.ground, 1), [space], (H, W), 'tok')
        self.assertGreater(fresh.bays[0].veto_thr, 0.0)
        r = bo.evaluate(fresh, cast_shadow(captured(self.ground, 700),
                                           self.BAY_PX, 1.0, 75), 1.0)[self.bay.id]
        self.assertTrue(r['shadow_veto'])


class BlockCorrelationTests(TestCase):
    """The measurement the veto rests on, on its own.

    Asserted as properties rather than against numbers, because the numbers are
    a property of whatever ground the test happens to draw: what must hold is
    that scaling the light leaves correlation alone and replacing the surface
    destroys it.
    """

    def _ground(self, seed=3, h=160, w=160, grain=1.0):
        rng = np.random.RandomState(seed)
        g = np.full((h, w), 120, np.uint8)
        for _ in range(int(600 * grain)):
            x, y = rng.randint(0, w), rng.randint(0, h)
            cv2.circle(g, (x, y), rng.randint(1, 3), int(rng.randint(90, 160)), -1)
        return g

    def _mask(self, shape):
        return np.full(shape, 255, np.uint8)

    def test_scaling_the_light_leaves_correlation_intact(self):
        """What a shadow does. Every depth, because a deeper shadow is still
        only a scale factor."""
        base = self._ground()
        m = self._mask(base.shape)
        for factor in (0.8, 0.6, 0.4, 0.27):
            live = np.clip(base.astype(np.float32) * factor, 0, 255).astype(np.uint8)
            self.assertGreater(bo.block_ncc(live, base, m), 0.8,
                               'shadow at x%.2f broke correlation' % factor)

    def test_an_offset_leaves_correlation_intact(self):
        """Correlation is invariant to a*I+b, so an additive shift must not
        move it either — that is an overcast sky over one bay."""
        base = self._ground()
        live = np.clip(base.astype(np.int16) - 40, 0, 255).astype(np.uint8)
        self.assertGreater(bo.block_ncc(live, base, self._mask(base.shape)), 0.8)

    def test_replacing_the_surface_destroys_correlation(self):
        """What a vehicle does."""
        base = self._ground()
        live = base.copy()
        live[10:150, 10:150] = 60          # smooth bodywork over the grain
        self.assertLess(bo.block_ncc(live, base, self._mask(base.shape)), 0.2)

    def test_a_flat_live_crop_never_vetoes(self):
        """A bay covered by something featureless is the vehicle case, and must
        read as no-structure rather than as perfect agreement."""
        base = self._ground()
        live = np.full_like(base, 90)
        self.assertLess(bo.block_ncc(live, base, self._mask(base.shape)), 0.2)

    def test_a_bay_too_small_for_the_grid_coarsens(self):
        """A motorcycle bay cannot carry 8 blocks a side; the measurement has to
        coarsen rather than silently return nothing."""
        base = self._ground(h=40, w=40)
        live = np.clip(base.astype(np.float32) * 0.5, 0, 255).astype(np.uint8)
        self.assertGreater(bo.block_ncc(live, base, self._mask(base.shape), 8), 0.5)

    def test_a_bay_too_small_for_any_grid_returns_zero(self):
        """Zero never vetoes, so an unmeasurable bay falls back to the tonal
        vote instead of being held free on nothing."""
        base = self._ground(h=8, w=8)
        self.assertEqual(bo.block_ncc(base, base, self._mask(base.shape), 8), 0.0)

    def test_a_polygon_mask_is_respected(self):
        """Only the bay the admin drew counts; asphalt outside it must not."""
        base = self._ground()
        mask = np.zeros(base.shape, np.uint8)
        cv2.circle(mask, (80, 80), 70, 255, -1)
        live = base.copy()
        live[:, :] = 60                     # everything covered
        self.assertLess(bo.block_ncc(live, base, mask), 0.2)


class GridSelectionTests(TestCase):
    """Which grid a bay is judged on is a property of its ground, measured
    rather than configured."""

    def setUp(self):
        self.zone = ParkingZone.objects.create(name='Grid', vehicle_category='car')
        self.bay = ParkingSpace.objects.create(zone=self.zone, space_number='A1',
                                               x1=0.10, y1=0.20, x2=0.45, y2=0.75)

    def _settle(self, ground, frames=300):
        prep = bo.prepare_zone(captured(ground, 1), [self.bay], (H, W), 'tok')
        t = 1000.0
        for i in range(frames):
            bo.evaluate(prep, captured(ground, 50 + i), t)
            t += 0.1
        return prep.bays[0]

    def test_textured_ground_earns_the_finest_grid(self):
        """Fine blocks are the ones a real shadow edge cannot defeat, so a bay
        with the texture to support them should get them."""
        bay = self._settle(textured_ground(grain=1.0))
        self.assertEqual(bay.ncc_blocks, 8)
        self.assertGreater(bay.veto_thr, 0.0)

    def test_plain_ground_falls_back_to_a_coarser_grid(self):
        """Rather than losing the veto altogether: coarse blocks still catch a
        shadow that covers the whole bay, which is most of them."""
        bay = self._settle(textured_ground(grain=0.10))
        self.assertIn(bay.ncc_blocks, (0, 4, 2))
        if bay.ncc_blocks:
            self.assertGreaterEqual(bay.ncc_base, bo.NCC_MIN_BASE)

    def test_featureless_ground_gets_no_grid_and_no_veto(self):
        bay = self._settle(np.full((H, W, 3), 120, np.uint8))
        self.assertEqual(bay.ncc_blocks, 0)
        self.assertEqual(bay.veto_thr, 0.0)

    def test_a_settled_bay_stops_measuring_the_other_grids(self):
        """The steady-state cost is one correlation, not one per candidate —
        which grid the ground supports does not change by the second."""
        bay = self._settle(textured_ground(grain=1.0))
        self.assertEqual(bay.ncc_blocks, 8)
        before = {n: len(d) for n, d in bay.noise.ncc.items()}
        prep = bo.prepare_zone(captured(textured_ground(), 1), [self.bay],
                               (H, W), 'tok')
        prep.bays[0].ncc_blocks = 8
        prep.bays[0].ncc_base = bay.ncc_base
        t = 5000.0
        for i in range(200):
            bo.evaluate(prep, captured(textured_ground(), 700 + i), t)
            t += 0.1
        grew = {n: len(d) for n, d in prep.bays[0].noise.ncc.items()}
        self.assertGreater(grew[8], 0)
        self.assertEqual(grew[4], 0, 'a settled bay kept measuring a grid it had rejected')
        self.assertEqual(grew[2], 0)

    def test_a_restored_reference_survives_the_first_samples_after_a_restart(self):
        """The bug the plain restart case could not see.

        `ncc_choice` reads the in-memory window, which is empty when a zone has
        just come back, so it answers "no grid" for the first dozen samples. If
        that answer were simply assigned, the reference restored from the stored
        stats would be gone within a second of startup and the bay would spend
        the rest of the day relearning a number it already knew. The earlier
        restart case scores a single frame and never reaches a sample, so it
        passes either way.
        """
        ground = textured_ground()
        settled = self._settle(ground)
        self.assertEqual(settled.ncc_blocks, 8)
        summary = settled.noise.summary()
        ParkingSpace.objects.filter(pk=self.bay.id).update(noise_stats=summary)

        # ...the zone restarts.
        space = ParkingSpace.objects.get(pk=self.bay.id)
        prep = bo.prepare_zone(captured(ground, 1), [space], (H, W), 'tok')
        bay = prep.bays[0]
        self.assertEqual(bay.ncc_blocks, 8)
        self.assertGreater(bay.veto_thr, 0.0)

        # The reference has to hold from the FIRST sample onward, not merely be
        # rediscovered later: checking only the end state would pass even if the
        # bay dropped its grid and spent the next dozen samples relearning it,
        # which is exactly the failure this guards.
        t = 5000.0
        for i in range(40):
            bo.evaluate(prep, captured(ground, 800 + i), t)
            t += 0.1
            self.assertEqual(bay.ncc_blocks, 8,
                             'stored grid was dropped %d frames after restart' % (i + 1))
            self.assertGreater(bay.veto_thr, 0.0,
                               'veto was disarmed %d frames after restart' % (i + 1))

    def test_the_search_gives_up_on_ground_that_will_never_correlate(self):
        """Otherwise a featureless bay pays for every candidate grid forever."""
        bay = self._settle(np.full((H, W, 3), 120, np.uint8), frames=3000)
        self.assertTrue(bay.noise.ncc_exhausted)
        self.assertEqual(bay.ncc_blocks, 0)
