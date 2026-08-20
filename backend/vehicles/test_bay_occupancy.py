"""The classic (no-ML) bay scorer.

Everything here runs on synthetic frames: flat grey stands in for empty asphalt,
a textured patch for a parked vehicle. That is the point — the claims worth
pinning are about the *arithmetic*, and a claim like "a lighting change alone
must not flip a bay" is only meaningful if it is asserted rather than hoped for.
"""
import cv2
import numpy as np
from django.test import TestCase

from vehicles import bay_occupancy as bo
from vehicles.models import ParkingSpace, ParkingZone

H, W = 240, 320


def empty_lot(seed=7):
    """Flat grey with faint noise — asphalt has texture but almost no edges.

    The seed is a parameter so a live frame can carry *different* sensor noise
    from the baseline. With one fixed seed the two would be pixel-identical and
    every "empty reads free" assertion would pass for the wrong reason.
    """
    rng = np.random.RandomState(seed)
    frame = np.full((H, W, 3), 120, dtype=np.uint8)
    noise = rng.randint(-4, 5, (H, W, 3), dtype=np.int16)
    return np.clip(frame.astype(np.int16) + noise, 0, 255).astype(np.uint8)


def park_car(frame, x1, y1, x2, y2):
    """A dark body with panel lines and glass — edges plus a tonal shift."""
    out = frame.copy()
    cv2.rectangle(out, (x1, y1), (x2, y2), (38, 40, 44), -1)
    cv2.rectangle(out, (x1 + 4, y1 + 4), (x2 - 4, y2 - 4), (150, 155, 160), 2)
    mid = (y1 + y2) // 2
    cv2.line(out, (x1, mid), (x2, mid), (200, 200, 205), 2)
    cv2.line(out, (x1 + 8, y1), (x1 + 8, y2), (90, 90, 95), 2)
    return out


class ScorerTests(TestCase):
    def setUp(self):
        self.zone = ParkingZone.objects.create(name='Classic', vehicle_category='car')
        # Left half of the frame, roughly x 0.10-0.45, y 0.20-0.75.
        self.bay = ParkingSpace.objects.create(
            zone=self.zone, space_number='A1',
            x1=0.10, y1=0.20, x2=0.45, y2=0.75)
        self.baseline = empty_lot()
        self.prepared = bo.prepare_zone(self.baseline, [self.bay], (H, W), 'tok')

    def _score(self, frame):
        return bo.evaluate(self.prepared, frame)[self.bay.id]

    def test_empty_bay_reads_free(self):
        self.assertFalse(self._score(empty_lot(11))['occupied'])

    def test_parked_car_reads_occupied(self):
        frame = park_car(empty_lot(11), 40, 55, 135, 175)
        result = self._score(frame)
        self.assertTrue(result['occupied'])
        self.assertGreaterEqual(result['votes'], bo.VOTES_REQUIRED)

    def test_car_in_a_different_part_of_the_frame_does_not_occupy_this_bay(self):
        # Far right, well outside the bay's rectangle.
        frame = park_car(empty_lot(11), 230, 55, 310, 175)
        self.assertFalse(self._score(frame)['occupied'])

    def test_uniform_lighting_change_alone_does_not_flip_a_bay(self):
        """A cloud moving over shifts every pixel's brightness.

        This is the case that broke a plain two-of-three vote: the histogram and
        the mean both move, and together they outvoted the one signal that does
        not. Hence illumination compensation plus double-weighted edges.
        """
        brighter = np.clip(empty_lot(11).astype(np.int16) + 28, 0, 255).astype(np.uint8)
        self.assertFalse(self._score(brighter)['occupied'])

    def test_darkening_alone_does_not_flip_a_bay(self):
        darker = np.clip(empty_lot(11).astype(np.int16) - 26, 0, 255).astype(np.uint8)
        self.assertFalse(self._score(darker)['occupied'])

    def test_a_car_is_still_detected_under_a_lighting_change(self):
        """Compensation must not go so far that it hides a real vehicle."""
        frame = park_car(empty_lot(11), 40, 55, 135, 175)
        dim = np.clip(frame.astype(np.int16) - 22, 0, 255).astype(np.uint8)
        self.assertTrue(self._score(dim)['occupied'])

    def test_signals_are_reported_for_tuning(self):
        result = self._score(park_car(empty_lot(11), 40, 55, 135, 175))
        for key in ('edge_delta', 'hist_corr', 'mad', 'illum', 'votes'):
            self.assertIn(key, result)


class PolygonBayTests(TestCase):
    """The pen tool's polygons must not measure asphalt outside the drawn shape."""

    def setUp(self):
        self.zone = ParkingZone.objects.create(name='Poly', vehicle_category='car')
        self.bay = ParkingSpace.objects.create(
            zone=self.zone, space_number='P1',
            x1=0.10, y1=0.20, x2=0.45, y2=0.75,
            points=[[0.10, 0.20], [0.45, 0.24], [0.43, 0.75], [0.12, 0.71]])

    def test_polygon_mask_is_smaller_than_its_bounding_box(self):
        prepared = bo.prepare_zone(empty_lot(), [self.bay], (H, W), 'tok')
        bay = prepared.bays[0]
        x1, y1, x2, y2 = bay.rect
        self.assertLess(bay.mask_area, (x2 - x1) * (y2 - y1))

    def test_polygon_bay_still_detects_a_car(self):
        prepared = bo.prepare_zone(empty_lot(), [self.bay], (H, W), 'tok')
        frame = park_car(empty_lot(), 40, 60, 130, 170)
        self.assertTrue(bo.evaluate(prepared, frame)[self.bay.id]['occupied'])


class PreparationTests(TestCase):
    def setUp(self):
        self.zone = ParkingZone.objects.create(name='Prep', vehicle_category='car')

    def test_unplaced_bay_is_skipped_not_guessed(self):
        space = ParkingSpace.objects.create(zone=self.zone, space_number='X1')
        prepared = bo.prepare_zone(empty_lot(), [space], (H, W), 'tok')
        self.assertEqual(prepared.bays, [])

    def test_degenerate_bay_is_skipped(self):
        space = ParkingSpace.objects.create(
            zone=self.zone, space_number='X2',
            x1=0.500, y1=0.500, x2=0.502, y2=0.502)   # a couple of pixels
        prepared = bo.prepare_zone(empty_lot(), [space], (H, W), 'tok')
        self.assertEqual(prepared.bays, [])

    def test_baseline_of_a_different_resolution_is_resized_not_rejected(self):
        space = ParkingSpace.objects.create(
            zone=self.zone, space_number='X3',
            x1=0.10, y1=0.20, x2=0.45, y2=0.75)
        big = cv2.resize(empty_lot(), (W * 2, H * 2))
        prepared = bo.prepare_zone(big, [space], (H, W), 'tok')
        self.assertEqual(prepared.shape, (H, W))
        self.assertEqual(len(prepared.bays), 1)

    def test_signature_changes_when_the_baseline_is_recaptured(self):
        space = ParkingSpace.objects.create(
            zone=self.zone, space_number='X4',
            x1=0.10, y1=0.20, x2=0.45, y2=0.75)
        first  = bo.layout_signature([space], 'img|2026-01-01', (H, W))
        second = bo.layout_signature([space], 'img|2026-01-02', (H, W))
        self.assertNotEqual(first, second)

    def test_signature_changes_when_a_bay_moves(self):
        space = ParkingSpace.objects.create(
            zone=self.zone, space_number='X5',
            x1=0.10, y1=0.20, x2=0.45, y2=0.75)
        before = bo.layout_signature([space], 'tok', (H, W))
        space.x2 = 0.50
        space.save()
        space.refresh_from_db()
        self.assertNotEqual(before, bo.layout_signature([space], 'tok', (H, W)))
