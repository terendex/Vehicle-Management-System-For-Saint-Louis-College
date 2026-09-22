"""The sanctions ladder, as the gate actually applies it.

Sanctions for Violations (the terms every pass holder signs):

    1st offence — confiscation of the vehicle pass for one (1) week
    2nd offence — confiscation for two (2) weeks
    3rd offence — confiscation, and prohibition from securing a vehicle pass
                  for the next school year

Two things about a confiscated account have to hold at the barrier, and they
pull in opposite directions:

  * they may still LEAVE. A penalty is not detention, and a gate that refuses
    to log an exit leaves the vehicle counted as on campus forever.
  * they may not COME BACK IN — whichever way the guard looks them up. Plate,
    conduction number or name all have to reach the same refusal, because a
    guard with a mud-covered plate searches by name and must not be handed a
    row that looks admissible.
"""
from datetime import datetime, time, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from scanning.entry_logic import check_entry
from scanning.models import AccessLog
from vehicles.models import Vehicle
from violations.models import Violation
from violations.penalty import apply_penalty

User = get_user_model()


class PenaltyEnforcementTests(APITestCase):
    def setUp(self):
        frozen = timezone.make_aware(
            datetime.combine(timezone.localdate(), time(13, 0)),
            timezone.get_current_timezone())
        patcher = mock.patch('django.utils.timezone.now', return_value=frozen)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.now = frozen

        self.guard = User.objects.create_user(
            email='pe-guard@slc.edu.ph', full_name='GUARD', password='x',
            role='security', gate_assignment='main')
        self.owner = User.objects.create_user(
            email='pe-owner@slc.edu.ph', full_name='TANGALIN, AXEL JONAS',
            password='x', role='vehicle_owner', owner_type='student')
        self.vehicle = Vehicle.objects.create(
            plate_number='PEN0001', conduction_number='CD-PEN-01',
            vehicle_type=Vehicle.Type.CAR, is_authorized=True, user=self.owner)

    def _strike(self, n=1):
        """Put the account on rung `n` of the ladder, the way the gate does."""
        for i in range(1, n + 1):
            v = Violation.objects.create(
                vehicle=self.vehicle, owner=self.owner,
                violation_type=Violation.Type.TIME_EXCEED, offense_number=i,
                status=Violation.Status.WARNING)
            apply_penalty(v)
        self.owner.refresh_from_db()

    # ── the ladder itself ────────────────────────────────────────────────────

    def test_first_offence_confiscates_for_one_week(self):
        self._strike(1)
        self.assertEqual(self.owner.confiscation_level, 1)
        self.assertEqual(self.owner.confiscated_until,
                         self.now.date() + timedelta(days=7))
        self.assertFalse(self.owner.registration_banned)

    def test_second_offence_confiscates_for_two_weeks(self):
        self._strike(2)
        self.assertEqual(self.owner.confiscation_level, 2)
        self.assertEqual(self.owner.confiscated_until,
                         self.now.date() + timedelta(days=14))
        self.assertFalse(self.owner.registration_banned)

    def test_third_offence_also_bars_securing_another_pass(self):
        self._strike(3)
        self.assertEqual(self.owner.confiscation_level, 3)
        self.assertTrue(self.owner.registration_banned)

    def test_the_penalty_ends_on_its_own(self):
        """Nothing runs on a timer — is_confiscated compares the stored end date
        to today, so a one-week penalty lifts itself."""
        self._strike(1)
        self.assertTrue(self.owner.is_confiscated)
        self.owner.confiscated_until = self.now.date() - timedelta(days=1)
        self.owner.save(update_fields=['confiscated_until'])
        self.assertFalse(self.owner.is_confiscated)

    # ── may not enter ────────────────────────────────────────────────────────

    def test_a_confiscated_account_is_refused_entry(self):
        self._strike(1)
        result = check_entry(Vehicle.objects.get(pk=self.vehicle.pk))
        self.assertFalse(result['allowed'])
        self.assertEqual(result['status'], 'confiscated')
        self.assertIn('Offence 1 of 3', result['message'])

    def test_the_refusal_outranks_every_other_rule(self):
        """Checked before the timetable ones: a guard reading "wrong day" would
        have no idea the account is serving a penalty."""
        self._strike(2)
        result = check_entry(Vehicle.objects.get(pk=self.vehicle.pk))
        self.assertEqual(result['status'], 'confiscated')

    def test_another_car_of_the_same_owner_is_refused_too(self):
        """The ladder is per ACCOUNT, not per vehicle."""
        self._strike(1)
        second = Vehicle.objects.create(
            plate_number='PEN0002', vehicle_type=Vehicle.Type.CAR,
            is_authorized=True, user=self.owner)
        self.assertFalse(check_entry(second)['allowed'])

    # ── may still leave ──────────────────────────────────────────────────────

    def test_a_confiscated_vehicle_inside_can_still_record_its_exit(self):
        """A penalty is not detention. Refusing the exit would also leave the
        vehicle counted as on campus indefinitely."""
        entry = AccessLog.objects.create(
            plate_number='PEN0001', vehicle=self.vehicle,
            status=AccessLog.Status.AUTHORIZED, gate_id='main',
            scanned_by=self.guard)
        AccessLog.objects.filter(pk=entry.pk).update(
            scanned_at=self.now - timedelta(minutes=90))
        self._strike(1)

        self.client.force_authenticate(self.guard)
        res = self.client.post('/api/scan/manual-entry/',
                               {'plate_number': 'PEN0001', 'gate_id': 'main'},
                               format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['status'], 'exited')
        self.assertTrue(AccessLog.objects.filter(
            plate_number='PEN0001', status=AccessLog.Status.EXITED).exists())

    def test_and_is_refused_when_it_tries_to_come_back(self):
        self._strike(1)
        self.client.force_authenticate(self.guard)
        res = self.client.post('/api/scan/manual-entry/',
                               {'plate_number': 'PEN0001', 'gate_id': 'main'},
                               format='json')
        self.assertFalse(res.data['allowed'])
        self.assertEqual(res.data['status'], 'confiscated')

    # ── every way of looking them up says so ─────────────────────────────────

    def _lookup(self, q):
        self.client.force_authenticate(self.guard)
        res = self.client.get('/api/scan/owner-lookup/', {'q': q})
        self.assertEqual(res.status_code, 200)
        return res.data['results']

    def test_lookup_by_plate_shows_the_penalty(self):
        self._strike(1)
        row = next(r for r in self._lookup('PEN0001') if r['plate_number'] == 'PEN0001')
        self.assertTrue(row['is_confiscated'])
        self.assertEqual(row['confiscation_level'], 1)
        self.assertIn('Entry denied', row['denied_reason'])

    def test_lookup_by_conduction_number_shows_the_penalty(self):
        self._strike(2)
        row = next(r for r in self._lookup('CD-PEN-01') if r['plate_number'] == 'PEN0001')
        self.assertTrue(row['is_confiscated'])
        self.assertEqual(row['confiscation_level'], 2)

    def test_lookup_by_name_shows_the_penalty(self):
        """The case the guard actually hits: a plate they cannot read, so they
        type the driver's name instead."""
        self._strike(1)
        row = next(r for r in self._lookup('TANGALIN') if r['plate_number'] == 'PEN0001')
        self.assertTrue(row['is_confiscated'])
        self.assertIn('Report to the CDSO office', row['denied_reason'])

    def test_a_clean_account_is_not_flagged(self):
        row = next(r for r in self._lookup('PEN0001') if r['plate_number'] == 'PEN0001')
        self.assertFalse(row['is_confiscated'])
        self.assertEqual(row['confiscation_level'], 0)
        self.assertEqual(row['denied_reason'], '')

    def test_the_lookup_reason_matches_what_the_gate_says(self):
        """Two sentences that must not drift: the row the guard picks and the
        refusal they get for picking it."""
        self._strike(1)
        row = next(r for r in self._lookup('PEN0001') if r['plate_number'] == 'PEN0001')
        gate = check_entry(Vehicle.objects.get(pk=self.vehicle.pk))
        self.assertEqual(row['denied_reason'], gate['message'])
