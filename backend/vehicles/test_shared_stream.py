"""One RTSP connection per camera, however many zones read it.

A camera may carry several zones — motorcycle bays and car bays in one frame
are two zones with two layouts and one lens. Each zone used to open its own
capture, so that camera got N simultaneous RTSP sessions and the units that cap
concurrent streams (the Yoosee Y20 among them) left the extra zones black.

These tests pin the sharing itself, not the scoring: that one URL means one
capture, that the capture outlives every zone but the last, and that zones
sharing a frame cannot corrupt each other's copy of it.
"""
import threading
import time
from unittest.mock import patch

import numpy as np
from django.test import SimpleTestCase

from vehicles import parking_camera as pc


class FakeCapture:
    """Stands in for a cv2.VideoCapture over a real camera.

    Counts opens, so a test can tell one shared connection from several.
    """
    opened   = 0
    released = 0

    def __init__(self, frame=None):
        FakeCapture.opened += 1
        self._frame = np.zeros((8, 8, 3), dtype=np.uint8) if frame is None else frame

    def read(self):
        time.sleep(0.005)
        # A fresh array per read, exactly as cv2 does.
        return True, self._frame.copy()

    def release(self):
        FakeCapture.released += 1


class SharedStreamTests(SimpleTestCase):
    """No database: these exercise the threading layer only."""

    def setUp(self):
        FakeCapture.opened = FakeCapture.released = 0

        # Scoring is neutralised — this is about the plumbing under it — but
        # which zone scored is recorded, which is how "both zones are being fed"
        # is told apart from "one zone is being fed twice as often".
        self.scored = []
        self._scored_lock = threading.Lock()

        def record(thread_self, frame):
            with self._scored_lock:
                self.scored.append(thread_self.zone_id)

        p = patch.object(pc.ParkingCameraThread, '_process_frame',
                         autospec=True, side_effect=record)
        p.start()
        self.addCleanup(p.stop)

        c = patch('vehicles.ffmpeg_capture.open_capture', side_effect=lambda url: FakeCapture())
        c.start()
        self.addCleanup(c.stop)

        self.addCleanup(self._stop_everything)

    def _stop_everything(self):
        for zone_id in list(pc.all_threads()):
            pc.stop(zone_id)
        for t in list(pc.all_threads().values()):
            t.join(timeout=3)
        # Readers close on their own once the last zone lets go; give them a beat.
        self._wait_until(lambda: not pc.stream_status())

    @staticmethod
    def _wait_until(pred, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if pred():
                return True
            time.sleep(0.01)
        return pred()

    def _zones_scored(self):
        with self._scored_lock:
            return set(self.scored)

    def _wait_for_frames(self, *zone_ids, timeout=3.0):
        """Block until every named zone has scored at least one frame."""
        want = set(zone_ids)
        return self._wait_until(lambda: want <= self._zones_scored(), timeout)

    # ── sharing ─────────────────────────────────────────────────────────────
    def test_two_zones_on_one_camera_open_one_capture(self):
        url = 'rtsp://cam-a/stream'
        pc.start(1, url)
        pc.start(2, url)
        self.assertTrue(self._wait_for_frames(1))

        self.assertEqual(FakeCapture.opened, 1)
        self.assertEqual(pc.stream_status(), {url: 2})

    def test_two_cameras_open_two_captures(self):
        pc.start(1, 'rtsp://cam-a/stream')
        pc.start(2, 'rtsp://cam-b/stream')
        self.assertTrue(self._wait_for_frames(1))

        self.assertEqual(FakeCapture.opened, 2)
        self.assertEqual(len(pc.stream_status()), 2)

    def test_both_zones_are_scored_from_the_shared_stream(self):
        """Sharing a capture must not mean one zone consuming the frames and
        the other starving — each gets every frame it asks for."""
        url = 'rtsp://cam-a/stream'
        pc.start(1, url)
        pc.start(2, url)

        self.assertTrue(self._wait_for_frames(1, 2))
        self.assertEqual(self._zones_scored(), {1, 2})

    # ── lifetime ────────────────────────────────────────────────────────────
    def test_stopping_one_zone_leaves_the_other_streaming(self):
        url = 'rtsp://cam-a/stream'
        pc.start(1, url)
        pc.start(2, url)
        self.assertTrue(self._wait_for_frames(1))

        pc.stop(1)
        self._wait_until(lambda: pc.stream_status().get(url) == 1)

        self.assertEqual(pc.stream_status(), {url: 1})
        self.assertEqual(FakeCapture.released, 0, 'the surviving zone still needs the capture')

    def test_the_capture_closes_when_the_last_zone_stops(self):
        url = 'rtsp://cam-a/stream'
        pc.start(1, url)
        pc.start(2, url)
        self.assertTrue(self._wait_for_frames(1))

        pc.stop(1)
        pc.stop(2)
        self._wait_until(lambda: not pc.stream_status())

        self.assertEqual(pc.stream_status(), {})
        self.assertTrue(self._wait_until(lambda: FakeCapture.released == 1))

    def test_restarting_a_zone_reopens_the_camera(self):
        url = 'rtsp://cam-a/stream'
        pc.start(1, url)
        self.assertTrue(self._wait_for_frames(1))
        pc.stop(1)
        self._wait_until(lambda: not pc.stream_status())

        pc.start(1, url)
        self.assertTrue(self._wait_until(lambda: pc.stream_status().get(url) == 1))
        self.assertEqual(FakeCapture.opened, 2, 'a stopped reader must not be handed out again')


class FrameHandoffTests(SimpleTestCase):
    """What a zone receives from wait_for_frame, and whether it is its own."""

    def _reader_with_frame(self, refs):
        reader = pc._StreamReader('rtsp://cam/stream')
        reader.refs = refs
        reader._latest = np.zeros((4, 4, 3), dtype=np.uint8)
        reader._seq = 1
        return reader

    def test_a_lone_zone_is_handed_the_published_frame(self):
        reader = self._reader_with_frame(refs=1)
        frame, seq = reader.wait_for_frame(0, timeout=0.1)
        self.assertIs(frame, reader._latest)
        self.assertEqual(seq, 1)

    def test_sharing_zones_each_get_their_own_copy(self):
        """Otherwise one zone drawing evidence boxes would corrupt what the
        other is scoring off the same array."""
        reader = self._reader_with_frame(refs=2)
        a, _ = reader.wait_for_frame(0, timeout=0.1)
        b, _ = reader.wait_for_frame(0, timeout=0.1)

        self.assertIsNot(a, reader._latest)
        self.assertIsNot(a, b)
        a[0, 0] = 255
        self.assertEqual(b[0, 0].tolist(), [0, 0, 0])
        self.assertEqual(reader._latest[0, 0].tolist(), [0, 0, 0])

    def test_no_new_frame_times_out_without_replaying_the_last_one(self):
        """A zone must score each frame once; a stalled camera means no work,
        not the same frame scored ten times a second."""
        reader = self._reader_with_frame(refs=1)
        frame, seq = reader.wait_for_frame(1, timeout=0.05)
        self.assertIsNone(frame)
        self.assertEqual(seq, 1)

    def test_a_stopped_reader_releases_a_waiting_zone(self):
        reader = pc._StreamReader('rtsp://cam/stream')
        reader.refs = 1
        done = threading.Event()

        def wait():
            reader.wait_for_frame(0, timeout=5)
            done.set()

        t = threading.Thread(target=wait, daemon=True)
        t.start()
        time.sleep(0.05)
        reader.stop()
        self.assertTrue(done.wait(1), 'stop() must wake zones blocked on a frame')


class DetachedFrameAccessTests(SimpleTestCase):
    """A zone that is not running has no stream, and must say so rather than
    hand back a stale frame or raise."""

    def test_frame_accessors_are_none_without_a_reader(self):
        t = pc.ParkingCameraThread(99, 'rtsp://unused')
        self.assertIsNone(t.get_frame())
        self.assertIsNone(t.get_jpeg())
