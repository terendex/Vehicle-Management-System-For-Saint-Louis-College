"""Bays belong to one view of a multi-lens camera.

A dual-lens unit stacks two unrelated scenes into one frame, so a camera has
two independent sets of bays. `lens_index` tags which set a bay is in. It is
NOT a coordinate space: geometry stays normalised against the whole frame,
because that is what the detector returns and what bay_occupancy reads.
"""
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from vehicles.models import Camera, ParkingSpace, ParkingZone

User = get_user_model()


class LensIndexTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email='lens-admin@slc.edu.ph', full_name='ADMIN', password='x', role='admin')
        self.client.force_authenticate(self.admin)
        self.cam = Camera.objects.create(
            cam_number=901, name='Cam L', ip='10.0.0.9', device_id='d9',
            rtsp_url='rtsp://10.0.0.9/onvif1', assignment='parking')
        self.zone = ParkingZone.objects.create(
            name='Z', vehicle_category='car', camera=self.cam)

    def _save(self, spaces):
        return self.client.post(
            f'/api/vehicles/parking-zones/{self.zone.id}/save-layout/',
            {'spaces': spaces}, format='json')

    def test_lens_index_round_trips(self):
        r = self._save([
            {'space_number': 'C1', 'x1': .1, 'y1': .1, 'x2': .2, 'y2': .2, 'lens_index': 0},
            {'space_number': 'C2', 'x1': .1, 'y1': .6, 'x2': .2, 'y2': .7, 'lens_index': 1},
        ])
        self.assertEqual(r.status_code, 200, r.data)
        by_num = {s['space_number']: s for s in r.data}
        self.assertEqual(by_num['C1']['lens_index'], 0)
        self.assertEqual(by_num['C2']['lens_index'], 1)

    def test_geometry_stays_full_frame(self):
        """The tag must not rewrite coordinates — the detector reads full-frame
        boxes, so a lens-local y here would move the bay to the wrong scene."""
        self._save([{'space_number': 'C2', 'x1': .1, 'y1': .6,
                     'x2': .2, 'y2': .7, 'lens_index': 1}])
        sp = ParkingSpace.objects.get(zone=self.zone, space_number='C2')
        self.assertAlmostEqual(sp.y1, .6)      # unchanged, not halved or shifted
        self.assertAlmostEqual(sp.y2, .7)

    def test_missing_lens_index_defaults_to_zero(self):
        """Every bay drawn before this existed, and every single-lens camera."""
        self._save([{'space_number': 'C1', 'x1': .1, 'y1': .1, 'x2': .2, 'y2': .2}])
        self.assertEqual(
            ParkingSpace.objects.get(zone=self.zone, space_number='C1').lens_index, 0)

    def test_a_nonsense_lens_index_is_clamped_not_stored(self):
        """It indexes a stacked frame; a negative would orphan the bay from
        every view the editor can show."""
        self._save([{'space_number': 'C1', 'x1': .1, 'y1': .1, 'x2': .2,
                     'y2': .2, 'lens_index': -3}])
        self.assertEqual(
            ParkingSpace.objects.get(zone=self.zone, space_number='C1').lens_index, 0)

    def test_saving_keeps_both_views(self):
        """The editor shows one view at a time but submits every draft. If it
        ever submitted only the visible lens, save would delete the other."""
        self._save([
            {'space_number': 'C1', 'x1': .1, 'y1': .1, 'x2': .2, 'y2': .2, 'lens_index': 0},
            {'space_number': 'C2', 'x1': .1, 'y1': .6, 'x2': .2, 'y2': .7, 'lens_index': 1},
        ])
        self.assertEqual(ParkingSpace.objects.filter(zone=self.zone).count(), 2)
        self.assertEqual(
            sorted(ParkingSpace.objects.filter(zone=self.zone)
                   .values_list('lens_index', flat=True)), [0, 1])
