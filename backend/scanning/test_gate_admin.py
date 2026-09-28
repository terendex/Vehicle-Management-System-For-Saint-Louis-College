"""The Campus Gates panel in System Settings.

Two rules the endpoints enforce so the panel cannot leave the gates in a
state that reads one way and behaves another: the last active gate cannot be
deactivated (login would silently fall back to gate1/gate4), and a new gate's
label cannot name a different number than its slug.
"""
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from scanning.models import Gate
from vehicles.models import Camera


class GateAdminTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        admin = User.objects.create_user(
            email='gates@test.local', full_name='GATE ADMIN',
            password='AdminPw!2026', role='admin')
        self.client.force_authenticate(admin)
        # Start from a known table rather than whatever the seed left.
        Gate.objects.all().delete()
        self.g1 = Gate.objects.create(gate_id='gate1', label='Gate 1 — Main Entrance')
        self.g4 = Gate.objects.create(gate_id='gate4', label='Gate 4 — Side Entrance')

    def _toggle(self, gate, active):
        return self.client.patch(f'/api/scan/gates/{gate.pk}/', {'is_active': active}, format='json')

    def test_can_deactivate_while_another_gate_stays_active(self):
        resp = self._toggle(self.g4, False)
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertFalse(resp.data['is_active'])

    def test_last_active_gate_cannot_be_deactivated(self):
        self.assertEqual(self._toggle(self.g4, False).status_code, 200)
        resp = self._toggle(self.g1, False)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('only active gate', resp.data['error'])
        self.g1.refresh_from_db()
        self.assertTrue(self.g1.is_active)

    def test_inactive_gate_can_be_reactivated(self):
        self._toggle(self.g4, False)
        resp = self._toggle(self.g4, True)
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(resp.data['is_active'])

    def test_label_naming_another_number_is_refused(self):
        resp = self.client.post('/api/scan/gates/',
                                {'gate_id': 'gate5', 'label': 'Gate 2 — North Entrance'}, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Gate.objects.filter(gate_id='gate5').exists())

    def test_matching_or_unnumbered_label_is_accepted(self):
        for slug, label in (('gate2', 'Gate 2 — North Entrance'), ('gate3', 'Back Entrance')):
            resp = self.client.post('/api/scan/gates/', {'gate_id': slug, 'label': label}, format='json')
            self.assertEqual(resp.status_code, 201, resp.data)

    def test_camera_on_an_added_gate_reads_its_label(self):
        Gate.objects.create(gate_id='gate2', label='Gate 2 — North Entrance')
        cam = Camera(name='NORTH CAM', gate_id='gate2')
        self.assertEqual(cam.gate_label, 'Gate 2 — North Entrance')
        self.assertEqual(Camera(name='X', gate_id='gate9').gate_label, 'gate9')
        self.assertEqual(Camera(name='Y', gate_id='gate1').gate_label, 'Gate 1')
