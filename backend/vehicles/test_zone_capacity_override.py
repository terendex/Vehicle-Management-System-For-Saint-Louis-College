"""A zone's capacity set by hand — the Events tab's "Zone Capacity Overrides".

The override replaces the zone's drawn bay count in the capacity figures
(free / full) until someone clears it. It is not tied to an event, and an
event's parking share is held back on top of it — both of which the Events
tab now says, so both are pinned here.
"""
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from vehicles.capacity import category_state
from vehicles.models import Event, ParkingSpace, ParkingZone


def _url(zone):
    return f'/api/vehicles/parking-zones/{zone.pk}/set-capacity/'


class CapacityOverrideTests(TestCase):
    def setUp(self):
        self.zone = ParkingZone.objects.create(name='Main Lot', vehicle_category='car')
        ParkingSpace.objects.bulk_create(
            ParkingSpace(zone=self.zone, space_number=f'C{i:02d}') for i in range(1, 11))   # 10 bays drawn
        admin = User.objects.create_user(
            email='cap-admin@test.local', last_name='ADMIN', first_name='CAP',
            password='SecurePassword123!', role='admin')
        self.client = APIClient()
        self.client.force_authenticate(user=admin)

    def _set(self, value, client=None):
        return (client or self.client).patch(_url(self.zone), {'capacity_override': value}, format='json')

    def test_an_override_replaces_the_drawn_bays_in_the_counts(self):
        r = self._set(6)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['capacity_override'], 6)
        self.assertEqual(r.data['total_capacity'], 6)
        self.assertEqual(r.data['space_count'], 10)            # the "Default" column is still the bays drawn
        car = category_state()['car']
        self.assertEqual((car['capacity'], car['available']), (6, 6))

    def test_zero_is_a_real_override_and_reads_full(self):
        self.assertEqual(self._set(0).status_code, 200)
        car = category_state()['car']
        self.assertEqual(car['capacity'], 0)
        self.assertEqual(car['available'], 0)

    def test_clearing_goes_back_to_the_drawn_bays(self):
        self._set(6)
        for blank in (None, ''):
            r = self._set(blank)
            self.assertEqual(r.status_code, 200, r.data)
            self.assertIsNone(ParkingZone.objects.get(pk=self.zone.pk).capacity_override)
            self.assertEqual(category_state()['car']['capacity'], 10)
            self._set(6)

    def test_it_stays_until_cleared(self):
        # Not tied to any event: nothing expires it.
        self._set(4)
        self.assertEqual(ParkingZone.objects.get(pk=self.zone.pk).capacity_override, 4)
        self.assertEqual(self.client.get('/api/vehicles/parking-zones/').data[0]['capacity_override'], 4)

    def test_bad_values_are_refused_and_change_nothing(self):
        self._set(7)
        for bad in (-1, 'abc', '2.5'):
            r = self._set(bad)
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn('error', r.data)
        self.assertEqual(ParkingZone.objects.get(pk=self.zone.pk).capacity_override, 7)

    def test_only_admin_can_set_it(self):
        guard = User.objects.create_user(
            email='cap-guard@test.local', last_name='GUARD', first_name='CAP',
            password='SecurePassword123!', role='security', gate_assignment='gate1')
        client = APIClient()
        client.force_authenticate(user=guard)
        self.assertEqual(self._set(3, client).status_code, 403)
        self.assertIsNone(ParkingZone.objects.get(pk=self.zone.pk).capacity_override)

    def test_an_event_share_is_held_back_on_top_of_the_override(self):
        # What the tab warns about: using both for one event counts it twice.
        self._set(8)
        Event.objects.create(name='FAIR', date=timezone.localdate(), parking_share='half')
        car = category_state()['car']
        self.assertEqual(car['capacity'], 8)
        self.assertEqual(car['reserved'], 4)
        self.assertEqual(car['available'], 4)
