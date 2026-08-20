"""Splitting a multi-lens frame for detection, and putting the boxes back.

Two things have to hold or this silently corrupts everything downstream:

  * the shape test must split a stacked dual-lens frame and leave a genuinely
    portrait-mounted camera alone — a wrong split drops half the picture from
    detection without any error;
  * a box found in the lower lens must come back in full-frame coordinates,
    because parking-space geometry and the browser overlay are both expressed
    against the whole frame.
"""
import numpy as np
from django.test import SimpleTestCase

from vehicles.lens_layout import (detect_across_lenses, lens_count, lenses)


def _frame(w, h):
    return np.zeros((h, w, 3), np.uint8)


class LensCountTests(SimpleTestCase):
    def test_a_stacked_dual_lens_frame_is_two(self):
        """The campus camera: two 1080p views in one 1920x2160 picture."""
        self.assertEqual(lens_count(_frame(1920, 2160)), 2)

    def test_the_same_camera_sub_stream_is_two(self):
        self.assertEqual(lens_count(_frame(864, 976)), 2)

    def test_an_ordinary_landscape_camera_is_one(self):
        self.assertEqual(lens_count(_frame(1920, 1080)), 1)
        self.assertEqual(lens_count(_frame(640, 480)), 1)

    def test_a_portrait_camera_is_not_cut_in_half(self):
        """1080x1920 is one upright picture. Splitting it would throw away half
        the scene, and nothing would report an error."""
        self.assertEqual(lens_count(_frame(1080, 1920)), 1)

    def test_an_ambiguous_shape_is_left_whole(self):
        """960x1280 could be a 4:3 portrait camera or two stacked 3:2 views.

        There is no way to tell from the shape, and the two mistakes are not
        symmetric: leaving a stacked camera whole is just the old behaviour,
        while splitting an upright one discards half its scene silently. So
        ambiguity resolves to "leave it alone".
        """
        self.assertEqual(lens_count(_frame(960, 1280)), 1)

    def test_junk_input_does_not_raise(self):
        self.assertEqual(lens_count(None), 1)


class LensSliceTests(SimpleTestCase):
    def test_the_halves_are_the_top_and_bottom_of_the_frame(self):
        frame = _frame(1920, 2160)
        frame[:1080] = 10          # top lens
        frame[1080:] = 200         # bottom lens

        out = list(lenses(frame))
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0][1].shape, (1080, 1920, 3))
        self.assertEqual(int(out[0][1].mean()), 10)
        self.assertEqual(int(out[1][1].mean()), 200)

    def test_slices_stay_contiguous_for_the_detector(self):
        _, sub = next(iter(lenses(_frame(1920, 2160))))
        self.assertTrue(sub.flags['C_CONTIGUOUS'])

    def test_a_single_lens_frame_yields_itself_untouched(self):
        frame = _frame(1920, 1080)
        out = list(lenses(frame))
        self.assertEqual(len(out), 1)
        self.assertIs(out[0][1], frame)


class DetectAcrossLensesTests(SimpleTestCase):
    def test_a_single_lens_frame_is_passed_straight_through(self):
        """An ordinary camera must not pay for this — one call, same object."""
        seen = []

        def detector(img, **kw):
            seen.append(img)
            return [{'bbox': {'x': 0.1, 'y': 0.2, 'width': 0.3, 'height': 0.4},
                     'score': 0.9}]

        frame = _frame(1920, 1080)
        out = detect_across_lenses(frame, detector)
        self.assertEqual(len(seen), 1)
        self.assertIs(seen[0], frame)
        # Untouched: no remapping happened.
        self.assertEqual(out[0]['bbox']['y'], 0.2)

    def test_each_lens_is_given_to_the_detector(self):
        seen = []

        def detector(img, **kw):
            seen.append(img.shape)
            return []

        detect_across_lenses(_frame(1920, 2160), detector)
        self.assertEqual(seen, [(1080, 1920, 3), (1080, 1920, 3)])

    def test_a_box_in_the_lower_lens_lands_in_the_lower_half(self):
        """The remapping that keeps parking geometry and overlays honest.

        A box halfway down the second lens is three-quarters of the way down
        the whole frame.
        """
        def detector(img, **kw):
            return [{'bbox': {'x': 0.25, 'y': 0.5, 'width': 0.5, 'height': 0.2},
                     'score': 0.9}]

        out = detect_across_lenses(_frame(1920, 2160), detector)
        self.assertEqual(len(out), 2)

        top, bottom = out[0], out[1]
        self.assertAlmostEqual(top['bbox']['y'], 0.25)      # halfway down lens 1
        self.assertAlmostEqual(bottom['bbox']['y'], 0.75)   # halfway down lens 2
        for det in out:
            # Horizontal coordinates are already whole-frame values.
            self.assertAlmostEqual(det['bbox']['x'], 0.25)
            self.assertAlmostEqual(det['bbox']['width'], 0.5)
            # Height is squeezed into its half.
            self.assertAlmostEqual(det['bbox']['height'], 0.1)

    def test_boxes_stay_inside_the_frame(self):
        """A detection filling its lens must not spill past the frame."""
        def detector(img, **kw):
            return [{'bbox': {'x': 0.0, 'y': 0.0, 'width': 1.0, 'height': 1.0},
                     'score': 0.5}]

        for det in detect_across_lenses(_frame(1920, 2160), detector):
            bb = det['bbox']
            self.assertGreaterEqual(bb['y'], 0.0)
            self.assertLessEqual(bb['y'] + bb['height'], 1.0)

    def test_the_lens_a_detection_came_from_is_recorded(self):
        def detector(img, **kw):
            return [{'bbox': {'x': 0, 'y': 0, 'width': 0.1, 'height': 0.1},
                     'score': 0.5}]

        self.assertEqual([d['lens'] for d in
                          detect_across_lenses(_frame(1920, 2160), detector)],
                         [0, 1])

    def test_detector_arguments_are_forwarded(self):
        """detect_plates is called with try_rotation; losing it would silently
        change what the scanner detects."""
        seen = []

        def detector(img, try_rotation=None, **kw):
            seen.append(try_rotation)
            return []

        detect_across_lenses(_frame(1920, 2160), detector, try_rotation=False)
        self.assertEqual(seen, [False, False])

    def test_one_failing_lens_does_not_lose_the_other(self):
        """A camera half-blind is better than a camera blind."""
        def detector(img, **kw):
            if not getattr(detector, 'called', False):
                detector.called = True
                raise RuntimeError('inference blew up')
            return [{'bbox': {'x': 0, 'y': 0, 'width': 0.1, 'height': 0.1},
                     'score': 0.7}]

        out = detect_across_lenses(_frame(1920, 2160), detector)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['lens'], 1)

    def test_results_come_back_strongest_first(self):
        scores = [0.2, 0.9]

        def detector(img, **kw):
            return [{'bbox': {'x': 0, 'y': 0, 'width': 0.1, 'height': 0.1},
                     'score': scores.pop(0)}]

        out = detect_across_lenses(_frame(1920, 2160), detector)
        self.assertEqual([d['score'] for d in out], [0.9, 0.2])
