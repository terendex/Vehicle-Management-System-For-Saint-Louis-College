"""Guards read parking, admins change it.

The guard UI never showed an edit control, but both parking viewsets were
plain IsAuthenticated — a guard's own token could create, edit or DELETE any
zone straight against the API. These tests pin the rule at the endpoint,
where it is actually enforced.
"""
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from vehicles.models import ParkingZone, ParkingSpace

User = get_user_model()

ZONES  = '/api/vehicles/parking-zones/'
SPACES = '/api/vehicles/parking/'


class ParkingPermissionTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            email='perm-admin@slc.edu.ph', full_name='ADMIN', password='x', role='admin')
        cls.guard = User.objects.create_user(
            email='perm-guard@slc.edu.ph', full_name='GUARD', password='x', role='security')
        cls.owner = User.objects.create_user(
            email='perm-owner@slc.edu.ph', full_name='OWNER', password='x', role='vehicle_owner')

    def setUp(self):
        self.zone = ParkingZone.objects.create(name='Perm Zone', vehicle_category='motorcycle')
        self.space = ParkingSpace.objects.create(
            zone=self.zone, space_number='P01', x1=0.1, y1=0.1, x2=0.2, y2=0.2)

    # ── reads: open to every signed-in role ─────────────────────────────────
    def test_guard_can_list_zones(self):
        self.client.force_authenticate(self.guard)
        self.assertEqual(self.client.get(ZONES).status_code, status.HTTP_200_OK)

    def test_guard_can_retrieve_zone(self):
        self.client.force_authenticate(self.guard)
        self.assertEqual(self.client.get(f'{ZONES}{self.zone.id}/').status_code,
                         status.HTTP_200_OK)

    def test_guard_can_read_camera_status(self):
        self.client.force_authenticate(self.guard)
        self.assertEqual(self.client.get(f'{ZONES}camera-status/').status_code,
                         status.HTTP_200_OK)

    def test_anonymous_cannot_read(self):
        self.assertIn(self.client.get(ZONES).status_code,
                      (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    # ── writes: admin only ──────────────────────────────────────────────────
    def test_guard_cannot_create_zone(self):
        self.client.force_authenticate(self.guard)
        r = self.client.post(ZONES, {'name': 'Guard Zone', 'vehicle_category': 'car'},
                             format='json')
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    def test_guard_cannot_rename_zone(self):
        self.client.force_authenticate(self.guard)
        r = self.client.patch(f'{ZONES}{self.zone.id}/', {'name': 'Renamed'}, format='json')
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)
        self.zone.refresh_from_db()
        self.assertEqual(self.zone.name, 'Perm Zone')

    def test_guard_cannot_delete_zone(self):
        self.client.force_authenticate(self.guard)
        r = self.client.delete(f'{ZONES}{self.zone.id}/')
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(ParkingZone.objects.filter(id=self.zone.id).exists())

    def test_guard_cannot_toggle_a_space(self):
        self.client.force_authenticate(self.guard)
        r = self.client.patch(f'{SPACES}{self.space.id}/', {'is_occupied': True}, format='json')
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)
        self.space.refresh_from_db()
        self.assertFalse(self.space.is_occupied)

    def test_guard_cannot_start_camera_detection(self):
        self.client.force_authenticate(self.guard)
        r = self.client.post(f'{ZONES}{self.zone.id}/start-camera/')
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    def test_vehicle_owner_cannot_write_either(self):
        self.client.force_authenticate(self.owner)
        r = self.client.delete(f'{ZONES}{self.zone.id}/')
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    # ── admin keeps the full function ───────────────────────────────────────
    def test_admin_can_create_zone(self):
        self.client.force_authenticate(self.admin)
        r = self.client.post(ZONES, {'name': 'Admin Zone', 'vehicle_category': 'car'},
                             format='json')
        self.assertIn(r.status_code, (status.HTTP_200_OK, status.HTTP_201_CREATED))

    def test_admin_can_rename_zone(self):
        self.client.force_authenticate(self.admin)
        r = self.client.patch(f'{ZONES}{self.zone.id}/', {'name': 'Renamed'}, format='json')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.zone.refresh_from_db()
        self.assertEqual(self.zone.name, 'Renamed')

    def test_admin_can_toggle_a_space(self):
        self.client.force_authenticate(self.admin)
        r = self.client.patch(f'{SPACES}{self.space.id}/', {'is_occupied': True}, format='json')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.space.refresh_from_db()
        self.assertTrue(self.space.is_occupied)

    def test_admin_can_delete_zone(self):
        self.client.force_authenticate(self.admin)
        r = self.client.delete(f'{ZONES}{self.zone.id}/')
        self.assertEqual(r.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(ParkingZone.objects.filter(id=self.zone.id).exists())
