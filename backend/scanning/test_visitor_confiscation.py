"""A visitor who overstays may leave once, and may not come back in while the
penalty runs.

A visitor has no account, so the ladder used to have nothing to confiscate: the
overstay was recorded and the same person was issued a fresh pass the next
morning. Their penalty is now derived from the violations against their plate,
conduction number or name (violations.penalty.visitor_confiscation).
"""
from datetime import datetime, time, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from scanning.models import AccessLog, VisitorPass
from scanning.views import _overstaying_now
from vehicles.models import RuleConstraint
from violations.models import Violation
from violations.penalty import visitor_confiscation

User = get_user_model()

PASS_URL = '/api/scan/visitor-pass/'


class _VisitorCase(APITestCase):
    """Every case starts at a fixed 13:00 local, so a backdated pass cannot
    slip onto yesterday and quietly test nothing."""

    def setUp(self):
        self._clock = None
        self.addCleanup(lambda: self._clock.stop())
        self._set_clock(timezone.make_aware(
            datetime.combine(timezone.localdate(), time(13, 0)),
            timezone.get_current_timezone()))
        self.guard = User.objects.create_user(
            email='vc-guard@slc.edu.ph', full_name='GUARD ONE', password='x',
            role='security', gate_assignment='main')
        self.client.force_authenticate(self.guard)

    def _set_clock(self, when):
        if self._clock is not None:
            self._clock.stop()
        self._clock = mock.patch('django.utils.timezone.now', return_value=when)
        self._clock.start()
        self.now = when

    def _issue(self, plate='ABC1234', name='JUAN DELA CRUZ', conduction='', minutes=15):
        return self.client.post(PASS_URL, {
            'plate_number': plate, 'visitor_name': name,
            'conduction_number': conduction, 'purpose': 'Visit',
            'allowed_duration': minutes,
        }, format='json')

    def _visit_and_overstay(self, **kw):
        """Issue a pass, print it (which logs the entry), run it past its
        allowance, and leave on the slip — the overstay is recorded at exit."""
        res = self._issue(**kw)
        self.assertEqual(res.status_code, 201, res.data)
        pk = res.data['id']
        self.client.post(f'{PASS_URL}{pk}/printed/', {}, format='json')
        VisitorPass.objects.filter(pk=pk).update(
            expires_at=self.now - timedelta(minutes=20))
        out = self.client.post(f'{PASS_URL}{pk}/exit/', {'gate_id': 'main'}, format='json')
        self.assertEqual(out.status_code, 200, out.data)   # they may always leave
        return VisitorPass.objects.get(pk=pk)


class OverstayConfiscatesTheVisitorTests(_VisitorCase):

    def test_the_overstay_violation_carries_the_visitors_details(self):
        self._visit_and_overstay(conduction='CS12345')
        v = Violation.objects.get(violation_type='time_exceed')
        self.assertEqual(v.plate_number, 'ABC1234')
        self.assertEqual(v.conduction_number, 'CS12345')
        self.assertEqual(v.owner_name, 'JUAN DELA CRUZ')
        self.assertEqual(v.offense_number, 1)

    def test_no_new_pass_for_the_same_plate(self):
        self._visit_and_overstay()
        res = self._issue()
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.data['error'], 'visitor_confiscated')
        self.assertEqual(res.data['confiscation']['level'], 1)

    def test_a_refusal_creates_nothing(self):
        self._visit_and_overstay()
        before = VisitorPass.objects.count()
        self._issue()
        self.assertEqual(VisitorPass.objects.count(), before)

    def test_the_same_person_in_another_car_is_refused(self):
        self._visit_and_overstay()
        res = self._issue(plate='XYZ9876')
        self.assertEqual(res.status_code, 403)
        self.assertIn('name', res.data['confiscation']['matched_on'])

    def test_the_same_conduction_number_is_refused(self):
        self._visit_and_overstay(conduction='CS12345')
        res = self._issue(plate='NEW0001', name='SOMEONE ELSE', conduction='CS 12345')
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.data['confiscation']['matched_on'], ['plate / conduction number'])

    def test_an_unrelated_visitor_is_not_refused(self):
        self._visit_and_overstay()
        res = self._issue(plate='XYZ9876', name='MARIA SANTOS')
        self.assertEqual(res.status_code, 201)

    def test_the_gate_reads_the_plate_as_confiscated(self):
        """Typed or scanned, the plate says why — not "not registered", which
        would send the guard straight to issuing a new pass."""
        self._visit_and_overstay()
        self._set_clock(self.now + timedelta(hours=1))   # past the exit cooldown
        res = self.client.post('/api/scan/manual-entry/',
                               {'plate_number': 'ABC1234', 'gate_id': 'main'}, format='json')
        self.assertEqual(res.data['status'], 'confiscated')
        self.assertFalse(res.data['allowed'])

    def test_one_strike_per_day(self):
        """The overstay at exit and the confiscated sighting minutes later are
        one incident, not two strikes."""
        self._visit_and_overstay()
        self._set_clock(self.now + timedelta(hours=1))   # past the exit cooldown
        res = self.client.post('/api/scan/manual-entry/',
                         {'plate_number': 'ABC1234', 'gate_id': 'main'}, format='json')
        self.assertEqual(res.data['status'], 'confiscated')   # the sighting really was checked
        self.assertEqual(Violation.objects.filter(plate_number='ABC1234').count(), 1)


class PenaltyTermTests(_VisitorCase):

    def test_a_first_offence_ends_after_a_week(self):
        self._visit_and_overstay()
        self._set_clock(self.now + timedelta(days=7))
        self.assertIsNotNone(visitor_confiscation('ABC1234'))   # the last day still counts
        self._set_clock(self.now + timedelta(days=1))
        self.assertIsNone(visitor_confiscation('ABC1234'))
        self.assertEqual(self._issue().status_code, 201)

    def test_a_second_offence_is_two_weeks(self):
        self._visit_and_overstay()
        self._set_clock(self.now + timedelta(days=8))
        self._visit_and_overstay()
        v = Violation.objects.filter(plate_number='ABC1234').order_by('-issued_at').first()
        self.assertEqual(v.offense_number, 2)
        self.assertEqual(visitor_confiscation('ABC1234')['until'],
                         timezone.localdate() + timedelta(days=14))

    def test_lifting_the_violation_lifts_the_penalty(self):
        self._visit_and_overstay()
        Violation.objects.update(status=Violation.Status.LIFTED)
        self.assertEqual(self._issue().status_code, 201)

    def test_a_registered_owners_violation_does_not_follow_the_plate(self):
        """An owner's offences are counted on their account. A row carrying
        their email snapshot must not also confiscate the plate as a visitor."""
        Violation.objects.create(violation_type='time_exceed', plate_number='OWN1234',
                                 owner_name='AN OWNER', owner_email='owner@slc.edu.ph')
        self.assertIsNone(visitor_confiscation('OWN1234', '', 'AN OWNER'))


class VisitorOverstayCardTests(_VisitorCase):

    def setUp(self):
        super().setUp()
        # A supplier cap that would fire, to prove visitors are no longer
        # judged by it.
        RuleConstraint.objects.create(
            name='Supplier cap', constraint_type='supplier',
            days=['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'],
            start_time='00:00', end_time='23:59', max_stay_minutes=5, enabled=True)

    def _inside(self, minutes_ago, allowed):
        res = self._issue(conduction='CS12345', minutes=allowed)
        pk = res.data['id']
        self.client.post(f'{PASS_URL}{pk}/printed/', {}, format='json')
        started = self.now - timedelta(minutes=minutes_ago)
        VisitorPass.objects.filter(pk=pk).update(
            entered_at=started, expires_at=started + timedelta(minutes=allowed))
        AccessLog.objects.filter(plate_number='ABC1234').update(scanned_at=started)
        return pk

    def test_an_overstaying_visitor_is_listed_with_their_details(self):
        self._inside(minutes_ago=40, allowed=15)
        row, = _overstaying_now()
        self.assertEqual(row['owner_type'], 'visitor')
        self.assertEqual(row['owner_name'], 'JUAN DELA CRUZ')
        self.assertEqual(row['conduction_number'], 'CS12345')
        self.assertEqual(row['rule_name'], 'Visitor pass')
        self.assertEqual(row['max_minutes'], 15)
        self.assertEqual(row['over_minutes'], 25)

    def test_a_visitor_inside_their_pass_is_not_listed(self):
        """Ten minutes in on a fifteen-minute pass: past the supplier cap, but
        that cap is not theirs."""
        self._inside(minutes_ago=10, allowed=15)
        self.assertEqual(_overstaying_now(), [])

    def test_a_visitor_who_left_on_their_slip_is_not_listed(self):
        pk = self._inside(minutes_ago=40, allowed=15)
        self.client.post(f'{PASS_URL}{pk}/exit/', {'gate_id': 'main'}, format='json')
        self.assertEqual(_overstaying_now(), [])
