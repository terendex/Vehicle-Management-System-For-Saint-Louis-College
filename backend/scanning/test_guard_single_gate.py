"""A guard stands at one gate at a time.

All three sign-in paths used to close only the shift they were *relieving* —
whoever else was still clocked in at the gate being logged into. None of them
closed the arriving guard's own open shift somewhere else, so a guard who
walked away from Gate 1 without signing out and then signed in at Gate 4 was
on duty at both. The Operations Center showed it faithfully (one shift running
for weeks), and every scan the abandoned gate attributed through
`gate_assignment` was credited to somebody who was not standing there.

`scanning.models.open_shift_for` is the one place that now decides what a
sign-in displaces, and these tests drive it through each of the three views
that call it rather than calling it directly — the point is that no path is
left behind.
"""
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from scanning.models import Gate, GuardShift, active_guard_for_gate, open_shift_for


class GuardShiftTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        # Gate.active_ids() is what every login path validates against.
        self.gates = list(Gate.active_ids())
        self.assertGreaterEqual(len(self.gates), 2,
                                'this suite needs at least two active gates')
        self.gate_a, self.gate_b = self.gates[0], self.gates[1]

        self.guard = User.objects.create_user(
            email='oneplace@slc.edu.ph', full_name='ONE PLACE',
            password='GuardPw!2026', role='security')
        self.guard.must_change_password = False
        self.guard.save(update_fields=['must_change_password'])

        self.other = User.objects.create_user(
            email='relieved@slc.edu.ph', full_name='RELIEVED GUARD',
            password='GuardPw!2026', role='security')
        self.other.must_change_password = False
        self.other.save(update_fields=['must_change_password'])

    def open_shifts(self, guard=None):
        qs = GuardShift.objects.filter(clocked_out_at__isnull=True)
        if guard is not None:
            qs = qs.filter(guard=guard)
        return list(qs.values_list('gate', flat=True))

    def credential_login(self, guard, gate, password='GuardPw!2026'):
        return self.client.post('/api/auth/guard-login/', {
            'email': guard.email, 'password': password, 'gate': gate,
        }, format='json')


class TheHelperTests(GuardShiftTestCase):
    """The rule itself, stated once."""

    def test_a_guard_ends_up_with_exactly_one_open_shift(self):
        open_shift_for(self.guard, self.gate_a)
        open_shift_for(self.guard, self.gate_b)
        self.assertEqual(self.open_shifts(self.guard), [self.gate_b])

    def test_it_reports_the_gate_it_signed_them_out_of(self):
        open_shift_for(self.guard, self.gate_a)
        _shift, displaced = open_shift_for(self.guard, self.gate_b)
        self.assertEqual(displaced, [self.gate_a])

    def test_an_ordinary_first_sign_in_displaces_nothing(self):
        _shift, displaced = open_shift_for(self.guard, self.gate_a)
        self.assertEqual(displaced, [])

    def test_signing_in_again_at_the_same_gate_displaces_nothing(self):
        """Re-authenticating at the gate you are already standing at is not a
        move, so it must not report one — but it still leaves one open shift."""
        open_shift_for(self.guard, self.gate_a)
        _shift, displaced = open_shift_for(self.guard, self.gate_a)
        self.assertEqual(displaced, [])
        self.assertEqual(self.open_shifts(self.guard), [self.gate_a])

    def test_it_still_relieves_the_guard_who_was_there(self):
        open_shift_for(self.other, self.gate_a)
        open_shift_for(self.guard, self.gate_a)
        self.assertEqual(self.open_shifts(self.other), [])
        self.assertEqual(self.open_shifts(self.guard), [self.gate_a])

    def test_relieving_somebody_is_not_reported_as_being_moved(self):
        """`displaced` is about the arriving guard's own sessions. The guard
        they relieved is a different person and a different message."""
        open_shift_for(self.other, self.gate_a)
        _shift, displaced = open_shift_for(self.guard, self.gate_a)
        self.assertEqual(displaced, [])

    def test_the_closed_shift_records_who_ended_it(self):
        first, _ = open_shift_for(self.guard, self.gate_a)
        open_shift_for(self.guard, self.gate_b)
        first.refresh_from_db()
        self.assertIsNotNone(first.clocked_out_at)
        # Themselves: their own sign-in is what ended it, and naming anybody
        # else would read as having been signed out by a colleague.
        self.assertEqual(first.clocked_out_by, self.guard)

    def test_the_gate_lookup_agrees_with_the_move(self):
        """active_guard_for_gate is what the gate screens and scan attribution
        read, so it is the thing that must stop naming the absent guard."""
        open_shift_for(self.guard, self.gate_a)
        self.assertEqual(active_guard_for_gate(self.gate_a), self.guard)
        open_shift_for(self.guard, self.gate_b)
        self.assertIsNone(active_guard_for_gate(self.gate_a))
        self.assertEqual(active_guard_for_gate(self.gate_b), self.guard)

    def test_two_different_guards_may_hold_two_gates(self):
        """The rule is one gate per guard, not one guard per campus."""
        open_shift_for(self.guard, self.gate_a)
        open_shift_for(self.other, self.gate_b)
        self.assertEqual(self.open_shifts(self.guard), [self.gate_a])
        self.assertEqual(self.open_shifts(self.other), [self.gate_b])

    def test_a_guard_abandoned_at_several_gates_is_cleared_from_all(self):
        """Rows predating this rule can already hold more than one open shift,
        so the fix has to clean up after itself rather than assume at most one."""
        GuardShift.objects.create(guard=self.guard, gate=self.gate_a)
        GuardShift.objects.create(guard=self.guard, gate=self.gate_b)
        self.assertEqual(len(self.open_shifts(self.guard)), 2)

        _shift, displaced = open_shift_for(self.guard, self.gate_a)
        self.assertEqual(self.open_shifts(self.guard), [self.gate_a])
        self.assertEqual(displaced, [self.gate_b])

    def test_a_closed_shift_is_left_alone(self):
        """History is not rewritten — only open shifts are touched."""
        old = GuardShift.objects.create(guard=self.guard, gate=self.gate_a)
        stamp = timezone.now()
        GuardShift.objects.filter(pk=old.pk).update(clocked_out_at=stamp)

        open_shift_for(self.guard, self.gate_b)
        old.refresh_from_db()
        self.assertEqual(old.clocked_out_at, stamp)
        self.assertIsNone(old.clocked_out_by)


class CredentialLoginTests(GuardShiftTestCase):
    """The path a guard uses at the gate station."""

    def test_logging_in_at_a_second_gate_releases_the_first(self):
        self.assertEqual(self.credential_login(self.guard, self.gate_a).status_code, 200)
        res = self.credential_login(self.guard, self.gate_b)
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(self.open_shifts(self.guard), [self.gate_b])

    def test_the_response_names_the_gate_they_were_signed_out_of(self):
        """The guard has to be told: a colleague's terminal at the other gate
        has just gone dead, and nothing else on screen would say why."""
        self.credential_login(self.guard, self.gate_a)
        res = self.credential_login(self.guard, self.gate_b)
        self.assertEqual(res.data.get('signed_out_of'), [self.gate_a])

    def test_an_ordinary_login_reports_nothing_displaced(self):
        res = self.credential_login(self.guard, self.gate_a)
        self.assertEqual(res.data.get('signed_out_of'), [])

    def test_the_profile_gate_follows_the_move(self):
        """gate_assignment is what server-side scan attribution reads, so it
        has to name the gate the guard is actually standing at."""
        self.credential_login(self.guard, self.gate_a)
        self.credential_login(self.guard, self.gate_b)
        self.guard.refresh_from_db()
        self.assertEqual(self.guard.gate_assignment, self.gate_b)

    def test_a_failed_login_moves_nobody(self):
        self.credential_login(self.guard, self.gate_a)
        bad = self.credential_login(self.guard, self.gate_b, password='wrong')
        self.assertNotEqual(bad.status_code, 200)
        self.assertEqual(self.open_shifts(self.guard), [self.gate_a])

    def test_an_unknown_gate_falls_back_instead_of_moving_them(self):
        """An unrecognised gate is not an error: the view falls back to the
        guard's persisted gate_assignment. So the guard stays where they are,
        rather than being signed out of it and left nowhere."""
        self.credential_login(self.guard, self.gate_a)
        res = self.credential_login(self.guard, 'gate_nowhere')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['user']['gate_assignment'], self.gate_a)
        self.assertEqual(self.open_shifts(self.guard), [self.gate_a])
        self.assertEqual(res.data.get('signed_out_of'), [])

    def test_no_gate_at_all_on_a_first_login_is_refused(self):
        """With nothing persisted to fall back to there is no gate to attribute
        scans to, so the login is refused rather than guessed at."""
        fresh = User.objects.create_user(
            email='nogate@slc.edu.ph', full_name='NO GATE',
            password='GuardPw!2026', role='security')
        fresh.must_change_password = False
        fresh.save(update_fields=['must_change_password'])
        res = self.client.post('/api/auth/guard-login/', {
            'email': fresh.email, 'password': 'GuardPw!2026',
        }, format='json')
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(self.open_shifts(fresh), [])


class QrBadgeLoginTests(GuardShiftTestCase):
    """The badge path — same rule, different door."""

    def qr_login(self, guard, gate):
        guard.refresh_from_db()
        return self.client.post('/api/auth/qr-login/', {
            'qr_token': str(guard.qr_token), 'gate': gate,
        }, format='json')

    def test_a_badge_scan_at_a_second_gate_releases_the_first(self):
        first = self.qr_login(self.guard, self.gate_a)
        self.assertEqual(first.status_code, 200, first.data)
        second = self.qr_login(self.guard, self.gate_b)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(self.open_shifts(self.guard), [self.gate_b])
        self.assertEqual(second.data.get('signed_out_of'), [self.gate_a])

    def test_a_badge_scan_still_relieves_the_guard_on_duty(self):
        open_shift_for(self.other, self.gate_a)
        res = self.qr_login(self.guard, self.gate_a)
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(self.open_shifts(self.other), [])
        self.assertEqual(res.data.get('signed_out_of'), [])

    def test_mixing_the_two_paths_still_leaves_one_shift(self):
        """A guard may badge in at one gate and type their password at the
        other; the rule cannot depend on which door they came through."""
        self.assertEqual(self.qr_login(self.guard, self.gate_a).status_code, 200)
        res = self.credential_login(self.guard, self.gate_b)
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(self.open_shifts(self.guard), [self.gate_b])
        self.assertEqual(res.data.get('signed_out_of'), [self.gate_a])
