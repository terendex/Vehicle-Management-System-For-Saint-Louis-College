"""The violations table's Confiscation column (penalty.penalty_state).

Each row carries the penalty the person behind it is serving — running with
the instant it ends, served, or lifted early — so the screen can count down
and flip to "ended" by itself. A penalty that has expired is never stored as
such (User.is_confiscated reads the end date), so "ended" has to be derived.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from vehicles.models import Vehicle
from violations.models import Violation
from violations.penalty import penalty_state

User = get_user_model()
UE = Violation.Type.UNAUTHORIZED_ENTRY


class OwnerPenaltyStateTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            email='cc-admin@slc.edu.ph', last_name='ADMIN', first_name='ADMIN', password='x', role='admin')
        cls.owner = User.objects.create_user(
            email='cc-owner@slc.edu.ph', last_name='OWNER', first_name='OWNER', password='x', role='vehicle_owner')

    def setUp(self):
        self.vehicle = Vehicle.objects.create(plate_number='CCO1234', vehicle_type='car', user=self.owner)
        self.today = timezone.localdate()

    def _issue(self, n=1, **extra):
        return Violation.objects.create(
            vehicle=self.vehicle, owner=self.owner, violation_type=UE, offense_number=n,
            status=Violation.Status.WARNING, is_released=True, issued_at=timezone.now(), **extra)

    def _confiscate(self, level, until):
        self.owner.confiscation_level = level
        self.owner.confiscated_at = timezone.now()
        self.owner.confiscated_until = until
        self.owner.save()

    def test_running_penalty_ends_at_the_midnight_after_its_last_day(self):
        until = self.today + timedelta(days=7)
        self._confiscate(1, until)
        state = penalty_state(self._issue())
        self.assertEqual(state['state'], 'active')
        self.assertEqual(state['until'], until.isoformat())
        ends_at = timezone.localtime(timezone.datetime.fromisoformat(state['ends_at']))
        self.assertEqual(ends_at.date(), until + timedelta(days=1))
        self.assertEqual((ends_at.hour, ends_at.minute), (0, 0))

    def test_the_last_day_itself_still_counts_as_running(self):
        self._confiscate(1, self.today)
        self.assertEqual(penalty_state(self._issue())['state'], 'active')

    def test_a_served_penalty_reads_as_ended(self):
        self._confiscate(1, self.today - timedelta(days=1))
        state = penalty_state(self._issue())
        self.assertEqual(state['state'], 'ended')
        self.assertIsNotNone(state['ends_at'])

    def test_a_penalty_the_cdso_lifted_reads_as_lifted(self):
        self._confiscate(0, None)
        self.assertEqual(penalty_state(self._issue())['state'], 'lifted')

    def test_a_3rd_strike_with_no_period_is_indefinite(self):
        self._confiscate(3, None)
        state = penalty_state(self._issue(3))
        self.assertEqual(state['state'], 'active')
        self.assertTrue(state['indefinite'])
        self.assertIsNone(state['ends_at'])

    def test_settled_rows_carry_nothing(self):
        self._confiscate(1, self.today + timedelta(days=3))
        lifted = self._issue()
        lifted.status = Violation.Status.LIFTED
        self.assertIsNone(penalty_state(lifted))
        resolved = self._issue()
        resolved.is_resolved = True
        self.assertIsNone(penalty_state(resolved))

    def test_list_endpoint_carries_the_column(self):
        self._confiscate(1, self.today + timedelta(days=7))
        self._issue(1)
        self._issue(2)
        self.client.force_authenticate(self.admin)
        rows = self.client.get('/api/violations/').data
        rows = rows['results'] if isinstance(rows, dict) else rows
        states = {r['confiscation']['state'] for r in rows if r['plate_number'] == 'CCO1234'}
        self.assertEqual(states, {'active'})


class VisitorPenaltyStateTests(APITestCase):
    def setUp(self):
        # A gate-created vehicle with no account behind it: a visitor.
        self.vehicle = Vehicle.objects.create(plate_number='VIS4321', vehicle_type='car', is_authorized=False)

    def _issue(self, days_ago):
        v = Violation.objects.create(
            vehicle=self.vehicle, violation_type=UE, offense_number=1,
            status=Violation.Status.WARNING, is_released=True)
        # issued_at is stamped on create and ignores a passed value, so it is
        # back-dated with an UPDATE afterwards.
        Violation.objects.filter(pk=v.pk).update(issued_at=timezone.now() - timedelta(days=days_ago))
        v.refresh_from_db()
        return v

    def test_a_recent_offence_is_running(self):
        state = penalty_state(self._issue(days_ago=2))
        self.assertEqual(state['state'], 'active')
        self.assertEqual(state['until'], (timezone.localdate() + timedelta(days=5)).isoformat())

    def test_an_offence_past_its_week_is_ended(self):
        self.assertEqual(penalty_state(self._issue(days_ago=10))['state'], 'ended')
