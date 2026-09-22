"""Overstaying is visible while it is still happening, not only at exit.

`_check_stay_limit` runs at EXIT, once the duration is known. That left a car
sitting three hours past its rule invisible to the guard until it drove out, by
which time the only thing left to do is record it. `_overstaying_now` answers
the same question about vehicles that are still inside, and acknowledging one
issues the violation there and then.
"""
from datetime import datetime, time, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from scanning.models import AccessLog
from scanning.views import _overstaying_now
from vehicles.models import RuleConstraint, Vehicle, VehicleRegistration
from violations.models import Violation

User = get_user_model()

ALL_DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']


class OverstayCardTests(APITestCase):
    """Every case runs at a fixed 13:00 local.

    "Inside today" is bounded by the local day, so a test that backdates an
    entry by 400 minutes silently tests nothing when the suite happens to run
    at 01:00 — the entry lands on yesterday and drops out of the query. Pinning
    the clock removes that whole class of failure rather than choosing
    backdating windows that only work during office hours.
    """

    def setUp(self):
        frozen = timezone.make_aware(
            datetime.combine(timezone.localdate(), time(13, 0)),
            timezone.get_current_timezone())
        patcher = mock.patch('django.utils.timezone.now', return_value=frozen)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.now = frozen

        # 60-minute cap for students, none for employees — so the employee case
        # below proves the absence of a rule is respected, not just the presence.
        RuleConstraint.objects.create(
            name='Student stay cap', constraint_type='student_vehicle',
            days=ALL_DAYS, start_time='00:00', end_time='23:59',
            max_stay_minutes=60, enabled=True,
        )
        RuleConstraint.objects.create(
            name='Employee hours', constraint_type='employee',
            days=ALL_DAYS, start_time='00:00', end_time='23:59',
            max_stay_minutes=None, enabled=True,
        )
        RuleConstraint.objects.create(
            name='Fetcher drop & go', constraint_type='fetcher',
            days=ALL_DAYS, start_time='00:00', end_time='23:59',
            max_stay_minutes=15, enabled=True,
        )

        self.guard = User.objects.create_user(
            email='os-guard@slc.edu.ph', full_name='GUARD ONE', password='x',
            role='security', gate_assignment='main')

    def _owner(self, email, owner_type):
        return User.objects.create_user(
            email=email, full_name=email.split('@')[0].upper(), password='x',
            role='vehicle_owner', owner_type=owner_type)

    def _enter(self, plate, owner, minutes_ago, gate_id='main'):
        """Put a vehicle on campus, entered `minutes_ago` minutes back."""
        vehicle = Vehicle.objects.create(
            plate_number=plate, vehicle_type=Vehicle.Type.CAR,
            is_authorized=True, user=owner)
        log = AccessLog.objects.create(
            plate_number=plate, vehicle=vehicle,
            status=AccessLog.Status.AUTHORIZED, gate_id=gate_id,
            scanned_by=self.guard)
        # scanned_at is auto_now_add, so it is pushed back afterwards.
        AccessLog.objects.filter(pk=log.pk).update(
            scanned_at=self.now - timedelta(minutes=minutes_ago))
        return vehicle, AccessLog.objects.get(pk=log.pk)

    def _plates(self, **kw):
        return {r['plate_number'] for r in _overstaying_now(**kw)}

    # ── who is counted ───────────────────────────────────────────────────────

    def test_a_student_past_the_cap_is_listed(self):
        self._enter('OVER001', self._owner('os1@slc.edu.ph', 'student'), 95)
        rows = _overstaying_now()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['plate_number'], 'OVER001')
        self.assertEqual(rows[0]['max_minutes'], 60)
        self.assertEqual(rows[0]['over_minutes'], 35)
        self.assertEqual(rows[0]['rule_name'], 'Student stay cap')

    def test_a_student_inside_the_cap_is_not(self):
        self._enter('UNDER01', self._owner('os2@slc.edu.ph', 'student'), 30)
        self.assertEqual(_overstaying_now(), [])

    def test_exactly_at_the_cap_is_not_an_overstay(self):
        """The same boundary _check_stay_limit uses: over the limit, not at it."""
        self._enter('EXACT01', self._owner('os3@slc.edu.ph', 'student'), 60)
        self.assertEqual(_overstaying_now(), [])

    def test_an_entrant_whose_rule_has_no_cap_is_never_listed(self):
        self._enter('EMPL001', self._owner('os4@slc.edu.ph', 'employee'), 600)
        self.assertEqual(_overstaying_now(), [])

    def test_a_vehicle_that_has_exited_is_not_listed(self):
        owner = self._owner('os5@slc.edu.ph', 'student')
        vehicle, entry = self._enter('GONE001', owner, 200)
        AccessLog.objects.create(
            plate_number='GONE001', vehicle=vehicle, status=AccessLog.Status.EXITED,
            gate_id='main', scanned_by=self.guard, paired_entry=entry)
        self.assertEqual(_overstaying_now(), [])

    def test_a_standby_fetcher_is_allowed_to_wait(self):
        """Standby fetchers may sit on campus — that is what the type means."""
        owner = self._owner('os6@slc.edu.ph', 'fetcher')
        VehicleRegistration.objects.create(
            user=owner, full_name=owner.full_name, email=owner.email,
            status='accepted', registrant_type='fetcher', fetcher_type='standby')
        self._enter('STANDBY', owner, 300)
        self.assertEqual(_overstaying_now(), [])

    def test_a_drop_and_go_fetcher_is_not(self):
        owner = self._owner('os7@slc.edu.ph', 'fetcher')
        VehicleRegistration.objects.create(
            user=owner, full_name=owner.full_name, email=owner.email,
            status='accepted', registrant_type='fetcher', fetcher_type='drop_and_go')
        self._enter('DROPGO1', owner, 45)
        self.assertEqual(self._plates(), {'DROPGO1'})

    def test_worst_offender_is_first(self):
        self._enter('MILD001', self._owner('os8@slc.edu.ph', 'student'), 70)
        self._enter('BAD0001', self._owner('os9@slc.edu.ph', 'student'), 400)
        self.assertEqual([r['plate_number'] for r in _overstaying_now()],
                         ['BAD0001', 'MILD001'])

    def test_a_gate_filter_keeps_blank_gate_rows(self):
        """A row posted with no gate belongs to every gate, not to none."""
        self._enter('GATE1AA', self._owner('os10@slc.edu.ph', 'student'), 90, gate_id='gate1')
        self._enter('GATE2AA', self._owner('os11@slc.edu.ph', 'student'), 90, gate_id='gate2')
        self._enter('NOGATE1', self._owner('os12@slc.edu.ph', 'student'), 90, gate_id='')
        self.assertEqual(self._plates(gate_id='gate1'), {'GATE1AA', 'NOGATE1'})

    # ── acknowledging ────────────────────────────────────────────────────────

    def test_acknowledging_issues_the_violation_now(self):
        owner = self._owner('os13@slc.edu.ph', 'student')
        self._enter('ACK0001', owner, 130)
        self.client.force_authenticate(self.guard)
        res = self.client.post('/api/scan/overstaying/acknowledge/',
                               {'plate_number': 'ACK0001'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['over_minutes'], 70)

        violation = Violation.objects.get(owner=owner)
        self.assertEqual(violation.violation_type, Violation.Type.TIME_EXCEED)
        self.assertEqual(violation.offense_number, 1)
        self.assertIn('Overstay acknowledged at the gate', violation.notes)

        # The ladder ran: a first offence costs a week of campus access.
        owner.refresh_from_db()
        self.assertEqual(owner.confiscation_level, 1)
        self.assertTrue(owner.is_confiscated)

    def test_acknowledging_twice_does_not_strike_twice(self):
        """The per-day cap in _auto_log_violation is what keeps the card and the
        exit sweep from counting one overstay as two offences."""
        owner = self._owner('os14@slc.edu.ph', 'student')
        self._enter('ACK0002', owner, 130)
        self.client.force_authenticate(self.guard)
        for _ in range(2):
            self.client.post('/api/scan/overstaying/acknowledge/',
                             {'plate_number': 'ACK0002'}, format='json')
        self.assertEqual(Violation.objects.filter(owner=owner).count(), 1)

    def test_the_card_says_when_acknowledging_would_do_nothing(self):
        owner = self._owner('os15@slc.edu.ph', 'student')
        self._enter('ACK0003', owner, 130)
        self.assertFalse(_overstaying_now()[0]['already_issued'])
        self.client.force_authenticate(self.guard)
        self.client.post('/api/scan/overstaying/acknowledge/',
                         {'plate_number': 'ACK0003'}, format='json')
        self.assertTrue(_overstaying_now()[0]['already_issued'])

    def test_acknowledging_something_that_left_is_refused(self):
        self.client.force_authenticate(self.guard)
        res = self.client.post('/api/scan/overstaying/acknowledge/',
                               {'plate_number': 'NOTHERE'}, format='json')
        self.assertEqual(res.status_code, 409)

    def test_an_owner_cannot_acknowledge(self):
        owner = self._owner('os16@slc.edu.ph', 'student')
        self._enter('ACK0004', owner, 130)
        self.client.force_authenticate(owner)
        res = self.client.post('/api/scan/overstaying/acknowledge/',
                               {'plate_number': 'ACK0004'}, format='json')
        self.assertEqual(res.status_code, 403)
        self.assertFalse(Violation.objects.filter(owner=owner).exists())
