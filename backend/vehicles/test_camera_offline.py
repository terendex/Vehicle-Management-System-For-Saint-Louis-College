"""A parking camera that stops sending frames.

The zone keeps its bays exactly as last seen — clearing them would show a full
lot as empty for as long as the camera is down — and reports itself offline so
the screens can say those bays are not live. When frames come back, the clocks
behind each bay start over rather than resuming from before the outage.
"""
from unittest.mock import patch

from django.test import TestCase

from vehicles import parking_camera as pc
from vehicles.test_adaptive_occupancy import CLAIM_PAST, _ZoneCase


class StreamStateTests(TestCase):
    """connecting → online → offline, read off the thread's own clocks."""

    def setUp(self):
        self.thread = pc.ParkingCameraThread(99, 'rtsp://unused')
        self.t0 = self.thread._started_at

    def test_a_fresh_zone_is_connecting_not_offline(self):
        self.assertEqual(self.thread.stream_state(self.t0 + 1), ('connecting', None))

    def test_no_first_frame_for_the_threshold_is_offline(self):
        later = self.t0 + pc.STREAM_OFFLINE_SECONDS + 5
        state, secs = self.thread.stream_state(later)
        self.assertEqual(state, 'offline')
        self.assertAlmostEqual(secs, pc.STREAM_OFFLINE_SECONDS + 5)

    def test_a_recent_frame_is_online(self):
        self.thread._note_frame(self.t0 + 2)
        self.assertEqual(self.thread.stream_state(self.t0 + 3), ('online', None))

    def test_offline_counts_from_the_last_frame(self):
        self.thread._note_frame(self.t0 + 2)
        state, secs = self.thread.stream_state(self.t0 + 2 + 30)
        self.assertEqual(state, 'offline')
        self.assertAlmostEqual(secs, 30)

    def test_status_dict_reports_running_and_stream_separately(self):
        """The thread is alive for the whole outage — which is exactly why
        `running` alone used to read an unplugged camera as monitoring."""
        self.thread._last_frame_at = self.t0 - 60
        self.thread.is_alive = lambda: True
        with patch.dict(pc._cameras, {99: self.thread}, clear=True):
            status = pc.status_dict()
        self.assertEqual(status[99]['running'], True)
        self.assertEqual(status[99]['stream'], 'offline')
        self.assertGreaterEqual(status[99]['offline_seconds'], 60)


class OutageTests(_ZoneCase):
    """What a gap in frames does, and does not do, to the bays."""

    GAP = pc.STREAM_OFFLINE_SECONDS + 50

    def _claim_at(self, t):
        for i in range(pc.OCCUPY_THR):
            self.thread._note_frame(t + i * 0.1)
            self._frame(t + i * 0.1)
        self.thread._note_frame(t + CLAIM_PAST)
        self._frame(t + CLAIM_PAST)
        self.assertTrue(self._occupied())
        return t + CLAIM_PAST

    def test_an_occupied_bay_stays_occupied_through_the_outage(self):
        """Nothing is scored while offline, so nothing is cleared."""
        last = self._claim_at(1000.0)
        self.assertEqual(self.thread.stream_state(last + self.GAP)[0], 'offline')
        self.assertTrue(self._occupied())

    def test_first_empty_frame_after_the_outage_does_not_release(self):
        """The release clock stopped with the stream. Without the resume, the
        first frame back is already a full grace past the last sighting and the
        bay frees on a single reading."""
        last = self._claim_at(1000.0)
        back = last + self.GAP
        self.thread._note_frame(back)
        self._frame(back, hit=False)
        self.assertTrue(self._occupied())

    def test_releases_a_full_grace_after_frames_return(self):
        last = self._claim_at(1000.0)
        back = last + self.GAP
        self.thread._note_frame(back)
        self._frame(back, hit=False)
        self._frame(back + pc.OCCUPIED_GRACE_SECONDS - 0.5, hit=False)
        self.assertTrue(self._occupied())
        self._frame(back + pc.OCCUPIED_GRACE_SECONDS, hit=False)
        self.assertFalse(self._occupied())

    def test_a_claim_does_not_straddle_the_outage(self):
        """Two hits before the gap and one after are not three in a row."""
        for i in range(pc.OCCUPY_THR - 1):
            self.thread._note_frame(1000.0 + i * 0.1)
            self._frame(1000.0 + i * 0.1)
        back = 1000.0 + self.GAP
        self.thread._note_frame(back)
        self._frame(back)
        self.assertFalse(self._occupied())

    def test_tracks_from_before_the_outage_are_dropped(self):
        self.thread._note_frame(1000.0)
        self._frame(1000.0, hit=False, boxes=[self.OVER])
        self.assertTrue(self.thread._tracker.tracks)
        self.thread._note_frame(1000.0 + self.GAP)
        self.assertFalse(self.thread._tracker.tracks)

    def test_a_short_stutter_is_not_an_outage(self):
        """Below the threshold nothing is reset — a camera that drops a few
        frames must not lose a claim that is half formed."""
        for i in range(pc.OCCUPY_THR - 1):
            self.thread._note_frame(1000.0 + i * 0.1)
            self._frame(1000.0 + i * 0.1)
        self.thread._note_frame(1000.0 + CLAIM_PAST)
        self._frame(1000.0 + CLAIM_PAST)
        self.assertTrue(self._occupied())
