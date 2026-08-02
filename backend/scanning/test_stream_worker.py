"""Lifecycle of the shared RTSP capture worker.

One capture thread per camera URL is shared by every viewer of that camera, so
the interesting failures are all lifecycle races: a viewer arriving as the last
one leaves, a thread that has given up but is still listed in the pool, a stop
signal from one generation reaching the next. Each of those presented the same
way on screen — a feed that showed nothing and then reported itself
disconnected — which is why they are pinned here individually.

Nothing below opens a socket: _run is stubbed except where a fake VideoCapture
stands in for the camera.
"""
import threading
import time
from unittest.mock import patch

from django.test import SimpleTestCase

from scanning import consumers
from scanning.consumers import _StreamWorker, _acquire_worker, _STREAM_POOL

URL = 'rtsp://cam.invalid/stream1'


def _idle_run(self, stop):
    """Stand-in for _run: stays alive until this generation's stop is set."""
    stop.wait(5)


class _Loop:
    """Minimal event-loop stand-in — _push only ever calls call_soon_threadsafe."""
    def __init__(self):
        self.calls = []

    def call_soon_threadsafe(self, fn):
        self.calls.append(fn)
        fn()


class WorkerLifecycleTests(SimpleTestCase):
    def setUp(self):
        _STREAM_POOL.clear()
        p = patch.object(_StreamWorker, '_run', _idle_run)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(_STREAM_POOL.clear)

    def _acquire(self, sid):
        return _acquire_worker(URL, sid, _Loop())

    def test_first_subscriber_starts_the_capture_thread(self):
        worker, _ = self._acquire('a')
        self.assertTrue(worker.is_running())
        worker.unsubscribe('a')

    def test_second_subscriber_shares_the_one_thread(self):
        """The camera must see a single connection no matter how many viewers."""
        w1, _ = self._acquire('a')
        thread = w1._thread
        w2, _ = self._acquire('b')
        self.assertIs(w2, w1)
        self.assertIs(w2._thread, thread)
        w1.unsubscribe('a'); w1.unsubscribe('b')

    def test_a_worker_whose_thread_died_restarts_for_the_next_subscriber(self):
        """The black-feed bug: the pool kept a worker whose thread had exited,
        and subscribe() started nothing because _subs was already non-empty."""
        worker, _ = self._acquire('a')
        worker._stop.set()                      # thread gives up, 'a' stays attached
        worker._thread.join(5)
        self.assertFalse(worker.is_running())

        worker2, q = self._acquire('b')
        self.assertIs(worker2, worker)
        self.assertTrue(worker2.is_running(), 'newcomer adopted a dead worker')
        worker.unsubscribe('a'); worker.unsubscribe('b')

    def test_one_leaving_viewer_does_not_stop_the_feed_for_the_others(self):
        worker, _ = self._acquire('a')
        self._acquire('b')
        worker.unsubscribe('a')
        self.assertTrue(worker.is_running())
        self.assertIs(_STREAM_POOL.get(URL), worker)
        worker.unsubscribe('b')

    def test_the_last_viewer_leaving_stops_the_thread_and_frees_the_pool(self):
        worker, _ = self._acquire('a')
        worker.unsubscribe('a')
        worker._thread and worker._thread.join(5)
        self.assertNotIn(URL, _STREAM_POOL)

    def test_unsubscribing_twice_does_not_evict_a_live_viewer(self):
        """_ref_count was a second source of truth and could disagree with the
        subscriber dict; a repeated unsubscribe then tore down a working feed."""
        worker, _ = self._acquire('a')
        self._acquire('b')
        worker.unsubscribe('a')
        worker.unsubscribe('a')          # repeat
        self.assertTrue(worker.is_running())
        self.assertIs(_STREAM_POOL.get(URL), worker)
        worker.unsubscribe('b')

    def test_a_stop_from_the_previous_generation_cannot_kill_the_new_thread(self):
        worker, _ = self._acquire('a')
        old_stop = worker._stop
        worker.unsubscribe('a')          # sets old_stop
        worker._thread and worker._thread.join(5)

        worker2, _ = self._acquire('b')  # fresh worker (old one left the pool)
        old_stop.set()
        time.sleep(0.15)
        self.assertTrue(worker2.is_running())
        worker2.unsubscribe('b')

    def test_a_subscribe_racing_the_last_unsubscribe_still_gets_a_live_feed(self):
        """The 'magically disconnects' bug: the arriving viewer's thread was
        started and then killed by the departing viewer's stop, and its worker
        pulled out of the pool with a subscriber still attached."""
        for i in range(60):
            worker, _ = self._acquire(f'old{i}')
            done = threading.Event()
            box = {}

            def joiner():
                box['worker'], _ = self._acquire(f'new{i}')
                done.set()

            t = threading.Thread(target=joiner)
            t.start()
            worker.unsubscribe(f'old{i}')     # races the subscribe above
            t.join(5)
            self.assertTrue(done.is_set(), 'subscribe deadlocked')

            joined = box['worker']
            self.assertTrue(joined.is_running(),
                            f'iteration {i}: newcomer left with a stopped worker')
            self.assertIs(_STREAM_POOL.get(URL), joined,
                          f'iteration {i}: live worker was evicted from the pool')
            joined.unsubscribe(f'new{i}')
            _STREAM_POOL.clear()


# ── With a stand-in camera ────────────────────────────────────────────────────

class _FakeCap:
    """VideoCapture stand-in: hands out a tiny black frame forever."""
    def __init__(self, opened=True):
        import numpy as np
        self._opened = opened
        self._frame = np.zeros((16, 16, 3), dtype='uint8')
        self.released = False

    def isOpened(self):  return self._opened
    def grab(self):      return self._opened
    def retrieve(self):  return (True, self._frame)
    def set(self, *a):   return True

    def release(self):
        self.released = True
        self._opened = False


class WorkerStreamingTests(SimpleTestCase):
    def setUp(self):
        _STREAM_POOL.clear()
        self.addCleanup(_STREAM_POOL.clear)

    def test_frames_reach_a_subscriber(self):
        cap = _FakeCap()
        with patch.object(consumers.RtspStreamConsumer, '_open_cap', staticmethod(lambda url: cap)):
            worker = _StreamWorker(URL)
            loop = _Loop()
            q = worker.subscribe('a', loop)
            thread = worker._thread          # unsubscribe clears the attribute
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and q.qsize() == 0:
                time.sleep(0.05)
            worker.unsubscribe('a')
            thread.join(5)

        kinds = set()
        while not q.empty():
            kinds.add(q.get_nowait().get('type'))
        self.assertIn('frame', kinds, f'no frames pushed (saw {kinds})')

    def test_a_camera_that_never_opens_reports_an_error_and_leaves_the_pool(self):
        with patch.object(consumers.RtspStreamConsumer, '_open_cap',
                          staticmethod(lambda url: _FakeCap(opened=False))), \
             patch.object(_StreamWorker, 'MAX_RETRIES', 1), \
             patch.object(_StreamWorker, 'RETRY_DELAY', 0.01):
            worker, q = _acquire_worker(URL, 'a', _Loop())
            worker._thread.join(10)

        msgs = []
        while not q.empty():
            msgs.append(q.get_nowait())
        self.assertTrue(any(m.get('type') == 'error' for m in msgs),
                        f'no error reported: {msgs}')
        # The corpse must not be left where the next viewer will adopt it.
        self.assertNotIn(URL, _STREAM_POOL)
