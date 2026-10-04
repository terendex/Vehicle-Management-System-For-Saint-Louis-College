"""Guards record who is parked in an occupied bay.

The camera knows a bay is taken but not by whom. A guard fills that in — plate
or conduction number, optionally the driver — and every parking screen shows
it. The record goes when the bay goes free, so the next car never inherits it,
and writing it must not disturb the bay's `updated_at`, which is part of the
zone's baseline signature.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import AuditLog
from vehicles import parking_camera as pc
from vehicles.models import ParkingZone, ParkingSpace

User = get_user_model()

ZONES  = '/api/vehicles/parking-zones/'
SPACES = '/api/vehicles/parking/'
AVAIL  = '/api/vehicles/parking-availability/'


class ParkingOccupantTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            email='occ-admin@slc.edu.ph', last_name='ADMIN', first_name='ADMIN', password='x', role='admin')
        cls.guard = User.objects.create_user(
            email='occ-guard@slc.edu.ph', last_name='CRUZ', first_name='PEDRO', password='x', role='security')
        cls.owner = User.objects.create_user(
            email='occ-owner@slc.edu.ph', last_name='OWNER', first_name='OWNER', password='x', role='vehicle_owner')

    def setUp(self):
        self.zone = ParkingZone.objects.create(name='Occ Zone', vehicle_category='car')
        self.space = ParkingSpace.objects.create(
            zone=self.zone, space_number='C01', x1=0.1, y1=0.1, x2=0.2, y2=0.3,
            is_occupied=True, occupied_by='CAMERA')
        self.url = f'{SPACES}{self.space.id}/occupant/'
        # The camera worker closes stale connections before it writes; under
        # the test runner that would close the test's own connection.
        p = patch('django.db.close_old_connections')
        p.start()
        self.addCleanup(p.stop)

    def _record(self, user=None, plate='abc 1234', name='  juan   dela cruz '):
        self.client.force_authenticate(user or self.guard)
        return self.client.post(self.url, {'plate_number': plate, 'name': name}, format='json')

    # ── recording ───────────────────────────────────────────────────────────
    def test_guard_records_an_occupied_bay(self):
        r = self._record()
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)
        self.space.refresh_from_db()
        self.assertEqual(self.space.occupant_plate, 'ABC1234')
        self.assertEqual(self.space.occupant_name, 'JUAN DELA CRUZ')
        self.assertEqual(self.space.occupant_noted_by, self.guard)
        self.assertIsNotNone(self.space.occupant_noted_at)
        self.assertEqual(r.data['occupant_plate'], 'ABC1234')
        self.assertEqual(r.data['occupant_noted_by_name'], self.guard.full_name)

    def test_name_is_optional(self):
        r = self._record(name='')
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)
        self.space.refresh_from_db()
        self.assertEqual(self.space.occupant_name, '')

    def test_plate_is_required(self):
        r = self._record(plate='  ')
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('plate_number', r.data)

    def test_junk_in_the_plate_is_refused(self):
        r = self._record(plate='ABC<1234>')
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('plate_number', r.data)

    def test_a_list_body_is_a_400_not_a_500(self):
        self.client.force_authenticate(self.guard)
        r = self.client.post(self.url, ['ABC1234'], format='json')
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_control_number_is_canonicalised(self):
        self._record(plate='fm1')
        self.space.refresh_from_db()
        self.assertEqual(self.space.occupant_plate, 'FM-001')

    def test_bay_freed_mid_request_is_not_named(self):
        # The camera frees the bay after the view has read it as occupied but
        # before the write: the note must not land on the now-free bay.
        from vehicles import models as vm
        real = vm.canonical_identifier

        def free_then_normalise(value):
            ParkingSpace.objects.filter(pk=self.space.pk).update(is_occupied=False, occupied_by='')
            return real(value)

        with patch.object(vm, 'canonical_identifier', free_then_normalise):
            r = self._record()
        self.assertEqual(r.status_code, status.HTTP_409_CONFLICT)
        self.space.refresh_from_db()
        self.assertEqual(self.space.occupant_plate, '')
        self.assertFalse(AuditLog.objects.filter(details__startswith='Parking occupant recorded').exists())

    def test_every_open_screen_is_told(self):
        with patch('realtime.broadcast.broadcast_change') as told:
            self._record()
        told.assert_called_with('parkingspace', 'updated', id=self.space.pk)

    def test_admin_marking_occupied_keeps_the_note(self):
        self._record()
        self.client.force_authenticate(self.admin)
        r = self.client.patch(f'{SPACES}{self.space.id}/',
                              {'is_occupied': True, 'occupied_by': 'XYZ999'}, format='json')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.space.refresh_from_db()
        self.assertEqual(self.space.occupied_by, 'XYZ999')
        self.assertEqual(self.space.occupant_plate, 'ABC1234')

    def test_admin_patch_writes_only_what_it_sent(self):
        # The camera writes noise_stats, and a guard the occupant, after this
        # request has read the row: its stale copy must not be written back.
        from vehicles.serializers import ParkingSpaceSerializer
        real_update = ParkingSpaceSerializer.update

        def others_write_first(ser, instance, validated_data):
            ParkingSpace.objects.filter(pk=instance.pk).update(
                noise_stats={'samples': 5}, occupant_plate='LATE123')
            return real_update(ser, instance, validated_data)

        self.client.force_authenticate(self.admin)
        with patch.object(ParkingSpaceSerializer, 'update', others_write_first):
            r = self.client.patch(f'{SPACES}{self.space.id}/', {'occupied_by': 'XYZ999'}, format='json')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.space.refresh_from_db()
        self.assertEqual(self.space.occupied_by, 'XYZ999')
        self.assertEqual(self.space.noise_stats, {'samples': 5})
        self.assertEqual(self.space.occupant_plate, 'LATE123')

    def test_admin_may_record_too(self):
        self.assertEqual(self._record(user=self.admin).status_code, status.HTTP_200_OK)

    def test_free_bay_cannot_be_recorded(self):
        ParkingSpace.objects.filter(pk=self.space.pk).update(is_occupied=False, occupied_by='')
        r = self._record()
        self.assertEqual(r.status_code, status.HTTP_409_CONFLICT)
        self.space.refresh_from_db()
        self.assertEqual(self.space.occupant_plate, '')

    def test_owner_cannot_record(self):
        self.assertEqual(self._record(user=self.owner).status_code, status.HTTP_403_FORBIDDEN)

    def test_guard_still_cannot_patch_a_space(self):
        self.client.force_authenticate(self.guard)
        r = self.client.patch(f'{SPACES}{self.space.id}/', {'is_occupied': False}, format='json')
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    def test_recording_is_audited(self):
        self._record()
        entry = AuditLog.objects.filter(actor=self.guard).latest('id')
        self.assertIn('ABC1234', entry.details)
        self.assertIn('C01', entry.details)

    def test_recording_leaves_updated_at_alone(self):
        # updated_at is part of bay_occupancy.layout_signature: bumping it
        # would throw away the zone's prepared baseline over a note.
        before = ParkingSpace.objects.get(pk=self.space.pk).updated_at
        self._record()
        self.client.delete(self.url)
        self.assertEqual(ParkingSpace.objects.get(pk=self.space.pk).updated_at, before)

    # ── who sees it ─────────────────────────────────────────────────────────
    def test_other_guards_and_admin_see_it_on_the_zone(self):
        self._record()
        for user in (self.guard, self.admin):
            self.client.force_authenticate(user)
            bay = self.client.get(f'{ZONES}{self.zone.id}/').data['spaces'][0]
            self.assertEqual(bay['occupant_plate'], 'ABC1234')
            self.assertEqual(bay['occupant_name'], 'JUAN DELA CRUZ')

    def test_owners_never_see_the_occupant(self):
        self._record()
        self.client.force_authenticate(self.owner)
        bay = self.client.get(f'{ZONES}{self.zone.id}/').data['spaces'][0]
        self.assertNotIn('occupant_name', bay)
        self.assertNotIn('occupant_plate', bay)
        r = self.client.get(AVAIL)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertNotIn('occupant_name', r.data['spaces'][0])

    # ── clearing ────────────────────────────────────────────────────────────
    def test_guard_can_clear_it(self):
        self._record()
        r = self.client.delete(self.url)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.space.refresh_from_db()
        self.assertEqual(self.space.occupant_plate, '')
        self.assertIsNone(self.space.occupant_noted_by)

    def test_admin_mark_free_clears_it(self):
        self._record()
        self.client.force_authenticate(self.admin)
        r = self.client.patch(f'{SPACES}{self.space.id}/',
                              {'is_occupied': False, 'occupied_by': ''}, format='json')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.space.refresh_from_db()
        self.assertEqual(self.space.occupant_plate, '')
        self.assertEqual(self.space.occupant_name, '')

    def test_camera_seeing_it_free_clears_it(self):
        thread = pc.ParkingCameraThread(self.zone.id, 'rtsp://unused')
        sp = ParkingSpace.objects.get(pk=self.space.pk)   # the worker's copy, from before the note
        self._record()
        thread._set_occupied(sp, False)
        self.space.refresh_from_db()
        self.assertFalse(self.space.is_occupied)
        self.assertEqual(self.space.occupant_plate, '')
        self.assertIsNone(self.space.occupant_noted_at)

    # ── the layout editor ───────────────────────────────────────────────────
    def _save_layout(self, spaces):
        self.client.force_authenticate(self.admin)
        return self.client.post(f'{ZONES}{self.zone.id}/save-layout/', {'spaces': spaces}, format='json')

    def test_standard_shape_saves_as_its_outline(self):
        # A 45° bay from the Shapes tool: a slanted four-corner outline.
        outline = [[0.30, 0.40], [0.42, 0.40], [0.36, 0.55], [0.24, 0.55]]
        r = self._save_layout([
            {'space_number': 'C01', 'x1': 0.1, 'y1': 0.1, 'x2': 0.2, 'y2': 0.3},
            {'space_number': 'C02', 'points': outline},
        ])
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)
        bay = ParkingSpace.objects.get(zone=self.zone, space_number='C02')
        self.assertEqual(bay.points, outline)
        self.assertEqual((bay.x1, bay.y1, bay.x2, bay.y2), (0.24, 0.40, 0.42, 0.55))

    def test_saving_the_layout_keeps_the_note(self):
        self._record()
        r = self._save_layout([{'space_number': 'C01', 'x1': 0.1, 'y1': 0.1, 'x2': 0.25, 'y2': 0.3}])
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)
        self.space.refresh_from_db()
        self.assertEqual(self.space.occupant_plate, 'ABC1234')
        self.assertEqual(r.data[0]['occupant_plate'], 'ABC1234')   # the editor gets it back too

    def test_camera_taking_a_bay_keeps_a_fresh_note(self):
        ParkingSpace.objects.filter(pk=self.space.pk).update(is_occupied=False, occupied_by='')
        thread = pc.ParkingCameraThread(self.zone.id, 'rtsp://unused')
        sp = ParkingSpace.objects.get(pk=self.space.pk)   # loaded while the bay was free and unnoted
        ParkingSpace.objects.filter(pk=self.space.pk).update(is_occupied=True)
        self._record()
        thread._set_occupied(sp, True)
        self.space.refresh_from_db()
        self.assertEqual(self.space.occupant_plate, 'ABC1234')
