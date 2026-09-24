"""A refusal at the gate says what it cost.

A wrong-day or out-of-hours refusal issues an Unauthorized Entry violation, and
that violation confiscates the account. The guard's card used to show only
"Wrong Schedule Day", so neither the guard nor the owner learned at the gate
that the same scan had just cost a week of access. The response now carries
the violation and its penalty.
"""
from datetime import datetime, time, timedelta
from unittest import mock

from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from scanning.tests import _make_guard, _make_owner

WRONG_DAY = {'status': 'wrong_day', 'allowed': False, 'constraint': 'Student rule',
             'message': 'Student access restricted. Today is not one of your campus days.'}
URL = '/api/scan/manual-entry/'


class RefusalReportsViolationTests(APITestCase):

    def setUp(self):
        self._set_clock(timezone.make_aware(
            datetime.combine(timezone.localdate(), time(13, 0)),
            timezone.get_current_timezone()))
        self.owner, self.vehicle = _make_owner('rv@slc.edu.ph', 'RVW001', User.OwnerType.STUDENT)
        self.client.force_authenticate(_make_guard())

    def _set_clock(self, when):
        p = mock.patch('django.utils.timezone.now', return_value=when)
        p.start()
        self.addCleanup(p.stop)
        self.now = when

    def _scan(self):
        # entry_logic's own wrong-day verdict is covered in scanning.tests;
        # this is about what the gate does with it.
        with mock.patch('scanning.views.check_entry', return_value=WRONG_DAY):
            return self.client.post(URL, {'plate_number': 'RVW001', 'gate_id': 'gate1'}, format='json')

    def test_a_wrong_day_refusal_reports_the_violation_and_penalty(self):
        v = self._scan().data['violation']
        self.assertEqual(v['type'], 'unauthorized_entry')
        self.assertEqual(v['offense_number'], 1)
        self.assertTrue(v['confiscated'])
        self.assertEqual(v['confiscated_until'],
                         (timezone.localdate() + timedelta(days=7)).isoformat())
        self.assertIn('1 week', v['penalty'])

    def test_a_second_refusal_the_same_day_says_nothing_new_was_issued(self):
        self._scan()
        self._set_clock(self.now + timedelta(minutes=10))   # clear of the duplicate-scan window
        self.assertEqual(self._scan().data['violation'], {'already_recorded': True})

    def test_an_admitted_vehicle_carries_no_violation(self):
        allowed = {'status': 'authorized', 'allowed': True, 'message': 'ok', 'constraint': None}
        with mock.patch('scanning.views.check_entry', return_value=allowed):
            res = self.client.post(URL, {'plate_number': 'RVW001', 'gate_id': 'gate1'}, format='json')
        self.assertIsNone(res.data['violation'])
