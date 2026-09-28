"""Flagged in the parking area, refused at the gate — end to end.

A double-parking violation confiscates the account (or the visitor) on the
spot, puts them on the Confiscated accounts card, and turns them away the next
time they come in. Each step is tested on its own elsewhere; this proves the
chain holds together for both kinds of person.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model

from scanning.models import VisitorPass
from scanning.test_visitor_confiscation import PASS_URL, _VisitorCase
from vehicles.models import ParkingZone, Vehicle
from violations.models import Violation

User = get_user_model()

REPORT_URL = '/api/vehicles/parking-zones/report-double-park/'
CARD_URL = '/api/violations/confiscated/'
ENTRY_URL = '/api/scan/manual-entry/'


class ParkingFlagToGateRefusalTests(_VisitorCase):

    def setUp(self):
        super().setUp()
        self.zone = ParkingZone.objects.create(name='North Lot', vehicle_category='car')

    def _flag(self, plate):
        res = self.client.post(REPORT_URL, {'plate_number': plate, 'zone_id': self.zone.pk},
                               format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(res.data['violation']['confiscated'])
        return res

    def _card(self):
        return self.client.get(CARD_URL).data

    def _enter(self, plate):
        return self.client.post(ENTRY_URL, {'plate_number': plate, 'gate_id': 'main'},
                                format='json').data

    def test_an_owner_flagged_in_parking_is_refused_at_the_gate(self):
        owner = User.objects.create_user(
            email='pf-owner@slc.edu.ph', full_name='PARKED OWNER', password='x',
            role='vehicle_owner', owner_type='student')
        Vehicle.objects.create(plate_number='OWN4321', vehicle_type='car',
                               is_authorized=True, user=owner)

        self._flag('OWN4321')

        owner.refresh_from_db()
        self.assertTrue(owner.is_confiscated)                 # confiscated on the spot
        row, = self._card()
        self.assertEqual((row['kind'], row['full_name']), ('account', 'PARKED OWNER'))

        self._set_clock(self.now + timedelta(hours=1))
        res = self._enter('OWN4321')
        self.assertEqual(res['status'], 'confiscated')
        self.assertFalse(res['allowed'])

    def test_a_visitor_flagged_in_parking_is_refused_at_the_gate(self):
        res = self._issue()                                   # ABC1234, JUAN DELA CRUZ
        self.assertEqual(res.status_code, 201, res.data)
        pk = res.data['id']
        self.client.post(f'{PASS_URL}{pk}/printed/', {}, format='json')   # on campus

        self._flag('ABC1234')

        row, = self._card()
        self.assertEqual((row['kind'], row['full_name']), ('visitor', 'JUAN DELA CRUZ'))

        # They may still leave on their slip…
        out = self.client.post(f'{PASS_URL}{pk}/exit/', {'gate_id': 'main'}, format='json')
        self.assertEqual(out.status_code, 200, out.data)
        self.assertEqual(VisitorPass.objects.get(pk=pk).status, VisitorPass.Status.EXITED)

        # …but not come back: no new pass, and the plate reads as confiscated.
        self._set_clock(self.now + timedelta(hours=1))
        self.assertEqual(self._issue().status_code, 403)
        res = self._enter('ABC1234')
        self.assertEqual(res['status'], 'confiscated')
        self.assertFalse(res['allowed'])
        # The flag and the refused return are one incident: still one strike.
        self.assertEqual(Violation.objects.filter(plate_number='ABC1234').count(), 1)
