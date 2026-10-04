"""The parking trigger, end to end: a car parks, the bay turns red; it leaves,
the bay turns green — and every screen hears about it.

Everything below the camera is real: the worker's own per-frame step
(`_process_frame`), the baseline scorer, the claim/release hysteresis, the
database write and the realtime signal that tells open pages to refetch. Only
the video feed (frames are handed in) and the YOLO model (which has nothing to
say about a bay that is simply occupied) are stood in for, and the clock is
stepped by hand so the timings can be checked exactly.

The other test files each pin one link of this chain; this one checks that the
links still meet.
"""
import shutil
import tempfile
import time as _real_time
from types import SimpleNamespace
from unittest.mock import patch

import cv2
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from vehicles import parking_camera as pc
from vehicles.models import ParkingSpace, ParkingZone
from vehicles.test_bay_occupancy import empty_lot, park_car

User = get_user_model()

FRAME = 0.1   # the worker scores about ten frames a second
# Copied once, at import: a test that read these live would quietly shorten
# its own waits if a change made them 0, and pass for the wrong reason.
GRACE = pc.OCCUPIED_GRACE_SECONDS
TTL   = pc.LAYOUT_TTL_SECONDS


class OccupancyTriggerTests(APITestCase):
    def setUp(self):
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        storage = override_settings(
            MEDIA_ROOT=media,
            STORAGES={
                'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
                'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
            })
        storage.enable()
        self.addCleanup(storage.disable)

        self.zone = ParkingZone.objects.create(name='Trigger Lot', vehicle_category='car')
        ok, buf = cv2.imencode('.png', empty_lot())          # lossless: the baseline is exact
        self.zone.baseline_image.save('baseline.png', ContentFile(buf.tobytes()))
        self.zone.baseline_captured_at = timezone.now()
        self.zone.save()

        # A drawn box on the left, and on the right a slanted outline of the
        # kind the Shapes tool places — both kinds must trigger.
        self.box = ParkingSpace.objects.create(
            zone=self.zone, space_number='C01', x1=0.10, y1=0.20, x2=0.45, y2=0.75)
        shape = [[0.60, 0.20], [0.88, 0.20], [0.82, 0.75], [0.54, 0.75]]
        self.shape = ParkingSpace.objects.create(
            zone=self.zone, space_number='C02', points=shape,
            x1=0.54, y1=0.20, x2=0.88, y2=0.75)

        self.guard = User.objects.create_user(
            email='trigger-guard@slc.edu.ph', last_name='GUARD', first_name='T',
            password='x', role='security')

        # The worker, without its camera or its model.
        self.clock = 1000.0
        fake_time = SimpleNamespace(monotonic=lambda: self.clock,
                                    time=_real_time.time, sleep=_real_time.sleep)
        for p in (patch.object(pc, 'time', fake_time),
                  patch('django.db.close_old_connections')):    # would close the test's connection
            p.start()
            self.addCleanup(p.stop)
        self.thread = pc.ParkingCameraThread(self.zone.id, 'rtsp://unused')
        self.thread._detect = lambda frame: []
        self.thread._detect_people = lambda frame: []

        # Every broadcast the realtime layer sends, in order.
        self.told = []
        p = patch('realtime.signals.broadcast_change',
                  side_effect=lambda resource, action, **kw: self.told.append(resource))
        p.start()
        self.addCleanup(p.stop)

    # ── helpers ─────────────────────────────────────────────────────────────
    def feed(self, frame, seconds):
        """Run the worker's own per-frame step for `seconds` of video."""
        for _ in range(round(seconds / FRAME)):
            self.clock += FRAME
            with self.captureOnCommitCallbacks(execute=True):
                self.thread._process_frame(frame)

    def occupied(self, bay):
        bay.refresh_from_db()
        return bay.is_occupied

    @staticmethod
    def car_in_box():
        return park_car(empty_lot(11), 40, 55, 135, 175)

    @staticmethod
    def car_in_shape():
        return park_car(empty_lot(11), 190, 60, 250, 170)

    # ── the trigger ─────────────────────────────────────────────────────────
    def test_an_empty_lot_stays_free(self):
        self.feed(empty_lot(11), 5)
        self.assertFalse(self.occupied(self.box))
        self.assertFalse(self.occupied(self.shape))
        self.assertNotIn('parkingspace', self.told)

    def test_a_parked_car_turns_its_bay_occupied(self):
        self.feed(empty_lot(11), 1)
        self.feed(self.car_in_box(), 0.5)                     # still settling
        self.assertFalse(self.occupied(self.box), 'claimed before the claim time')
        self.feed(self.car_in_box(), 1.0)
        self.assertTrue(self.occupied(self.box), 'a parked car never claimed its bay')
        self.assertEqual(self.box.occupied_by, 'CAMERA')
        self.assertFalse(self.occupied(self.shape), 'the car claimed the neighbouring bay too')
        self.assertIn('parkingspace', self.told, 'the screens were never told')

    def test_a_standard_shape_bay_triggers_too(self):
        self.feed(empty_lot(11), 1)
        self.feed(self.car_in_shape(), 1.5)
        self.assertTrue(self.occupied(self.shape), 'a Shapes-tool bay never claimed')
        self.assertFalse(self.occupied(self.box))

    def test_a_car_passing_through_does_not_claim(self):
        """Under the claim time, with gaps — a car driving across the bay."""
        self.feed(empty_lot(11), 1)
        for _ in range(4):
            self.feed(self.car_in_box(), 0.3)
            self.feed(empty_lot(11), 0.3)
        self.assertFalse(self.occupied(self.box))

    def test_the_bay_frees_after_the_car_leaves_and_the_grace_runs_out(self):
        self.feed(self.car_in_box(), 2)
        self.assertTrue(self.occupied(self.box))
        self.told.clear()

        self.feed(empty_lot(11), GRACE - 2)
        self.assertTrue(self.occupied(self.box), 'freed before the grace ran out')
        self.feed(empty_lot(11), 3)
        self.assertFalse(self.occupied(self.box), 'never freed after the car left')
        self.assertEqual(self.box.occupied_by, '')
        self.assertIn('parkingspace', self.told)

    def test_a_brief_occlusion_does_not_free_the_bay(self):
        """Someone walking past the camera blanks the bay for a moment."""
        self.feed(self.car_in_box(), 2)
        self.told.clear()
        self.feed(empty_lot(11), 2)
        self.assertTrue(self.occupied(self.box), 'a two-second blank freed the bay')
        self.feed(self.car_in_box(), 2)
        self.feed(empty_lot(11), GRACE - 1)
        self.assertTrue(self.occupied(self.box))
        self.assertNotIn('parkingspace', self.told, 'the bay flickered free and back')

    # ── with the guard's record ─────────────────────────────────────────────
    def record(self, bay, plate='NBC1234', name='JUAN DELA CRUZ'):
        self.client.force_authenticate(self.guard)
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(f'/api/vehicles/parking/{bay.id}/occupant/',
                                    {'plate_number': plate, 'name': name}, format='json')

    def test_full_cycle_with_the_guards_record(self):
        # 1. The car parks; the camera claims the bay.
        self.feed(self.car_in_box(), 2)
        self.assertTrue(self.occupied(self.box))
        prepared = self.thread._prepared

        # 2. A guard says who it is.
        r = self.record(self.box)
        self.assertEqual(r.status_code, 200, r.data)
        self.box.refresh_from_db()
        self.assertEqual(self.box.occupant_plate, 'NBC1234')

        # 3. The camera keeps watching, and the note neither disturbs it nor is
        #    disturbed by it: same prepared baseline, record still there.
        self.feed(self.car_in_box(), TTL + 1)   # past a layout re-read
        self.assertIs(self.thread._prepared, prepared, 'the note reset the zone baseline')
        self.box.refresh_from_db()
        self.assertTrue(self.box.is_occupied)
        self.assertEqual(self.box.occupant_plate, 'NBC1234')

        # 4. The car leaves: the bay frees and forgets the driver.
        self.feed(empty_lot(11), GRACE + 1)
        self.box.refresh_from_db()
        self.assertFalse(self.box.is_occupied)
        self.assertEqual(self.box.occupant_plate, '')
        self.assertEqual(self.box.occupant_name, '')
        self.assertIsNone(self.box.occupant_noted_by)

        # 5. The next car is a new arrival with no name attached.
        self.feed(self.car_in_box(), 2)
        self.box.refresh_from_db()
        self.assertTrue(self.box.is_occupied)
        self.assertEqual(self.box.occupant_plate, '')

    def test_a_note_cannot_be_left_on_a_bay_the_camera_freed(self):
        self.feed(self.car_in_box(), 2)
        self.feed(empty_lot(11), GRACE + 1)
        self.assertFalse(self.occupied(self.box))
        r = self.record(self.box)
        self.assertEqual(r.status_code, 409)
        self.box.refresh_from_db()
        self.assertEqual(self.box.occupant_plate, '')
