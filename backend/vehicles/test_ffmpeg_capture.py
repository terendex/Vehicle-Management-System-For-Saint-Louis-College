"""The capture-backend fallback, including a real ffmpeg subprocess.

The bug this module exists to prevent is specific and was live on campus: a
camera whose stream is perfectly good, which `cv2.VideoCapture` reports as
*opened* and then never yields a frame from. Anything that only checks
`isOpened()` declares that camera working and shows a black rectangle, so the
tests below care about the difference between opening and decoding.

The `FFmpegCapture` tests drive the real binary against a generated file rather
than a camera. That exercises the parts most likely to break — banner parsing,
the frame-sized reads off the pipe, the reshape, teardown — with no hardware and
no network.
"""
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from django.test import SimpleTestCase

from vehicles import ffmpeg_capture


def _make_clip(path, size='64x48', rate=10, seconds=2):
    """A tiny synthetic video, so the tests need no camera."""
    subprocess.run(
        [ffmpeg_capture.ffmpeg_binary(), '-y', '-loglevel', 'error',
         '-f', 'lavfi', '-i', f'testsrc=size={size}:rate={rate}',
         '-t', str(seconds), '-pix_fmt', 'yuv420p', path],
        check=True, capture_output=True, timeout=60)


class _FakeCv2Cap:
    """cv2.VideoCapture stand-in with configurable open/decode behaviour."""

    def __init__(self, opens=True, frames=True):
        self._opens, self._frames = opens, frames
        self.released = False
        self.reads = 0

    def isOpened(self):
        return self._opens

    def read(self):
        self.reads += 1
        if not self._frames:
            return False, None
        import numpy as np
        return True, np.zeros((4, 4, 3), np.uint8)

    def set(self, *a):
        return True

    def get(self, prop):
        return 0.0

    def release(self):
        self.released = True


class BackendSelectionTests(SimpleTestCase):
    """Which backend `open_capture` picks, and why."""

    URL = 'rtsp://admin:secret@10.0.0.5:554/onvif1'

    def test_opencv_is_used_when_it_can_actually_decode(self):
        """The fast path must stay the fast path — no subprocess for a camera
        OpenCV handles, or every working camera pays for the fallback."""
        cap = _FakeCv2Cap(opens=True, frames=True)
        with patch.object(ffmpeg_capture, '_try_cv2', return_value=cap) as tried, \
             patch.object(ffmpeg_capture, 'FFmpegCapture') as ffm:
            got = ffmpeg_capture.open_capture(self.URL)
        self.assertIs(got, cap)
        tried.assert_called_once()
        ffm.assert_not_called()

    def test_a_camera_that_opens_but_never_decodes_falls_through_to_ffmpeg(self):
        """The Yoosee failure exactly: OpenCV says opened, then yields nothing.

        Selecting on isOpened() alone is what left that camera black, so
        _try_cv2 has to reject it and hand over to the subprocess backend.
        """
        cap = _FakeCv2Cap(opens=True, frames=False)
        with patch('cv2.VideoCapture', return_value=cap), \
             patch.object(ffmpeg_capture, 'is_available', return_value=True), \
             patch.object(ffmpeg_capture, 'SLOT_RELEASE_SECONDS', 0), \
             patch.object(ffmpeg_capture, 'FFmpegCapture') as ffm:
            ffmpeg_capture.open_capture(self.URL)
        ffm.assert_called_once()
        self.assertTrue(cap.released)

    def test_ffmpeg_is_used_when_opencv_cannot_open_at_all(self):
        cap = _FakeCv2Cap(opens=False)
        with patch('cv2.VideoCapture', return_value=cap), \
             patch.object(ffmpeg_capture, 'is_available', return_value=True), \
             patch.object(ffmpeg_capture, 'SLOT_RELEASE_SECONDS', 0), \
             patch.object(ffmpeg_capture, 'FFmpegCapture') as ffm:
            ffmpeg_capture.open_capture(self.URL)
        ffm.assert_called_once()

    def test_both_transports_are_tried_before_giving_up_on_opencv(self):
        """TCP-only was the old behaviour and it blacked out every camera that
        answers a TCP SETUP with a UDP transport."""
        seen = []

        def record(url, api, params=None):
            seen.append(os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'])
            return _FakeCv2Cap(opens=False)

        with patch('cv2.VideoCapture', side_effect=record):
            ffmpeg_capture._try_cv2(self.URL, 1000)
        self.assertEqual(len(seen), 2)
        self.assertIn('rtsp_transport;tcp', seen[0])
        self.assertIn('rtsp_transport;udp', seen[1])

    def test_the_open_timeout_is_passed_to_the_constructor_not_set_afterwards(self):
        """cap.set() after construction is too late — the open already happened,
        which is why an unopenable camera used to cost 30 s per transport."""
        captured = {}

        def record(url, api, params=None):
            captured['params'] = params
            return _FakeCv2Cap(opens=False)

        with patch('cv2.VideoCapture', side_effect=record):
            ffmpeg_capture._try_cv2(self.URL, 4321)
        import cv2
        self.assertIn(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, captured['params'])
        self.assertIn(4321, captured['params'])

    def test_a_closed_capture_is_returned_when_nothing_can_decode(self):
        """Callers check isOpened(); they must never get None to crash on."""
        with patch.object(ffmpeg_capture, '_try_cv2', return_value=None), \
             patch.object(ffmpeg_capture, 'is_available', return_value=False):
            got = ffmpeg_capture.open_capture(self.URL)
        self.assertFalse(got.isOpened())


class PrimedCaptureTests(SimpleTestCase):
    def test_the_proving_frame_is_replayed_rather_than_thrown_away(self):
        inner = _FakeCv2Cap()
        primed = ffmpeg_capture._PrimedCapture(inner, 'first')

        self.assertEqual(primed.read(), (True, 'first'))
        self.assertEqual(inner.reads, 0)         # served from the cache

        ok, _ = primed.read()                    # now it delegates
        self.assertTrue(ok)
        self.assertEqual(inner.reads, 1)

    def test_unknown_attributes_reach_the_wrapped_capture(self):
        inner = _FakeCv2Cap()
        ffmpeg_capture._PrimedCapture(inner, 'f').release()
        self.assertTrue(inner.released)


@unittest.skipUnless(ffmpeg_capture.is_available(), 'no ffmpeg binary available')
class FFmpegCaptureTests(SimpleTestCase):
    """The subprocess backend against the real binary — no camera involved."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # ignore_cleanup_errors: on Windows a terminated ffmpeg keeps its handle
        # on the clip for a moment after exiting, and losing a temp file to that
        # race should not fail the suite.
        cls._dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.clip = os.path.join(cls._dir.name, 'clip.mp4')
        _make_clip(cls.clip)

    @classmethod
    def tearDownClass(cls):
        cls._dir.cleanup()
        super().tearDownClass()

    def test_frames_come_back_at_the_size_ffmpeg_reported(self):
        cap = ffmpeg_capture.FFmpegCapture(self.clip, open_timeout=30)
        self.addCleanup(cap.release)

        self.assertTrue(cap.isOpened(), msg=cap.last_error())
        self.assertEqual((cap.width, cap.height), (64, 48))

        ok, frame = cap.read()
        self.assertTrue(ok)
        # (height, width, channels) — the layout OpenCV callers index into.
        self.assertEqual(frame.shape, (48, 64, 3))

    def test_the_capture_property_ids_match_opencv(self):
        cap = ffmpeg_capture.FFmpegCapture(self.clip, open_timeout=30)
        self.addCleanup(cap.release)
        import cv2
        self.assertTrue(cap.isOpened(), msg=cap.last_error())
        self.assertEqual(cap.get(cv2.CAP_PROP_FRAME_WIDTH), 64.0)
        self.assertEqual(cap.get(cv2.CAP_PROP_FRAME_HEIGHT), 48.0)

    def test_every_frame_in_the_source_can_be_read(self):
        """A regression guard with teeth. `-fflags nobuffer -flags low_delay`
        looked harmless and made FFmpeg emit zero frames from an H.264 source —
        dimensions still parsed, so anything checking only the banner passed.
        """
        cap = ffmpeg_capture.FFmpegCapture(self.clip, open_timeout=30,
                                           read_timeout=10)
        self.addCleanup(cap.release)
        self.assertTrue(cap.isOpened(), msg=cap.last_error())

        seen = sum(1 for _ in range(10) if cap.read()[0])
        self.assertGreaterEqual(seen, 5, msg=f'only {seen} frames: {cap.last_error()}')

    def test_a_source_that_does_not_exist_is_reported_closed_not_hung(self):
        missing = os.path.join(self._dir.name, 'nope.mp4')
        cap = ffmpeg_capture.FFmpegCapture(missing, open_timeout=20)
        self.addCleanup(cap.release)
        self.assertFalse(cap.isOpened())
        self.assertFalse(cap.read()[0])

    def test_release_is_safe_to_call_twice(self):
        cap = ffmpeg_capture.FFmpegCapture(self.clip, open_timeout=30)
        cap.release()
        cap.release()
        self.assertFalse(cap.isOpened())

    def test_no_binary_means_closed_rather_than_an_exception(self):
        with patch.object(ffmpeg_capture, 'ffmpeg_binary', return_value=None):
            cap = ffmpeg_capture.FFmpegCapture(self.clip)
        self.assertFalse(cap.isOpened())
        self.assertFalse(cap.read()[0])
