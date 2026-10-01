"""An event's status is its date, on the campus clock.

Pending before its day, Active on it, Archived after — nobody switches it. The
stored is_active/archived flags follow the date (save() writes them, the daily
auto_manage_events job rolls them at midnight), and nothing that decides
anything reads them: the gate and the parking reserve ask the date directly.

The Manila-clock cases pin `timezone.now` to 17:30 UTC on 4 October, which is
01:30 on 5 October in Manila. A server whose own clock is UTC (the cloud
container) reads "4 October" off date.today() at that moment, so each of these
fails if anything goes back to the OS clock.
"""
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from scanning.entry_logic import organizer_event_for
from vehicles.capacity import reserving_event
from vehicles.models import Event
from vehicles.tasks import auto_manage_events

EVENTS = '/api/vehicles/events/'

# 01:30 on 5 Oct in Manila, still 4 Oct in UTC.
UTC_EVENING = datetime(2026, 10, 4, 17, 30, tzinfo=dt_timezone.utc)
MANILA_DAY = UTC_EVENING.date() + timedelta(days=1)       # 2026-10-05
manila_small_hours = patch('django.utils.timezone.now', return_value=UTC_EVENING)


def _event(days=0, **kw):
    kw.setdefault('name', 'FOUNDATION DAY')
    kw.setdefault('date', timezone.localdate() + timedelta(days=days))
    return Event.objects.create(**kw)


def _flags(ev):
    ev.refresh_from_db()
    return ev.is_active, ev.archived


class StatusFollowsTheDateTests(TestCase):
    def test_today_is_active_past_is_archived_future_is_pending(self):
        self.assertEqual(_event(0).status, 'active')
        self.assertEqual(_event(-1).status, 'archived')
        self.assertEqual(_event(1).status, 'pending')

    def test_save_writes_the_flags_from_the_date_whatever_it_was_told(self):
        # (is_active, archived) — the pair the list screen and older code read.
        self.assertEqual(_flags(_event(0, is_active=False, archived=True)), (True, False))
        self.assertEqual(_flags(_event(-1, is_active=True)), (False, True))
        self.assertEqual(_flags(_event(1, is_active=True, archived=True)), (False, False))

    def test_a_partial_save_still_carries_the_flags(self):
        ev = _event(1)
        ev.date = timezone.localdate()
        ev.save(update_fields=['date'])
        self.assertEqual(_flags(ev), (True, False))

    def test_stale_flags_do_not_decide_at_the_gate_or_in_parking(self):
        # Between midnight and the daily pass the flags still describe
        # yesterday. Today's event must already act; a finished one must not.
        today = _event(0, organizer_plates=['ORG1234'], parking_share='half')
        Event.objects.filter(pk=today.pk).update(is_active=False, archived=True)
        self.assertEqual(organizer_event_for('ORG1234').pk, today.pk)
        self.assertEqual(reserving_event().pk, today.pk)

        Event.objects.filter(pk=today.pk).update(date=timezone.localdate() - timedelta(days=1),
                                                 is_active=True, archived=False)
        self.assertIsNone(organizer_event_for('ORG1234'))
        self.assertIsNone(reserving_event())


class ManilaClockTests(TestCase):
    def test_status_goes_by_the_manila_date(self):
        with manila_small_hours:
            self.assertEqual(timezone.localdate(), MANILA_DAY)   # the premise
            ev = _event(date=MANILA_DAY)
            self.assertEqual(ev.status, 'active')
            self.assertEqual(_flags(ev), (True, False))
            self.assertEqual(_event(date=UTC_EVENING.date()).status, 'archived')

    def test_the_daily_job_rolls_over_at_manila_midnight(self):
        with manila_small_hours:
            yesterday = _event(date=UTC_EVENING.date())
            today = _event(date=MANILA_DAY)
            # As they stood before midnight: yesterday live, today pending.
            Event.objects.filter(pk=yesterday.pk).update(is_active=True, archived=False)
            Event.objects.filter(pk=today.pk).update(is_active=False, archived=False)

            result = auto_manage_events()

        self.assertEqual(result, {'activated': 1, 'archived': 1, 'pending': 0})
        self.assertEqual(_flags(today), (True, False))
        self.assertEqual(_flags(yesterday), (False, True))

    def test_the_api_takes_today_on_the_manila_date(self):
        admin = User.objects.create_user(
            email='status-admin@test.local', last_name='ADMIN', first_name='STATUS',
            password='SecurePassword123!', role='admin')
        client = APIClient()
        client.force_authenticate(user=admin)
        with manila_small_hours:
            ok = client.post(EVENTS, {'name': 'DAWN MASS', 'date': MANILA_DAY.isoformat()},
                             format='json')
            past = client.post(EVENTS, {'name': 'TOO LATE', 'date': UTC_EVENING.date().isoformat()},
                               format='json')
        self.assertEqual(ok.status_code, 201, ok.data)
        self.assertEqual(ok.data['status'], 'active')
        self.assertEqual(past.status_code, 400)
        self.assertIn('past', past.data['date'])


class DailyJobTests(TestCase):
    def test_repairs_every_kind_of_stale_row(self):
        today, past, coming = _event(0), _event(-2), _event(4)
        Event.objects.filter(pk=today.pk).update(is_active=False, archived=True)
        Event.objects.filter(pk=past.pk).update(is_active=True, archived=False)
        Event.objects.filter(pk=coming.pk).update(is_active=True, archived=False)

        self.assertEqual(auto_manage_events(), {'activated': 1, 'archived': 1, 'pending': 1})
        self.assertEqual(_flags(today), (True, False))
        self.assertEqual(_flags(past), (False, True))
        self.assertEqual(_flags(coming), (False, False))

    def test_a_second_run_changes_nothing(self):
        _event(0), _event(-1), _event(1)
        self.assertEqual(auto_manage_events(), {'activated': 0, 'archived': 0, 'pending': 0})

    def test_open_pages_are_told_only_when_something_rolled(self):
        # .update() fires no post_save, so the realtime signal never sees it.
        ev = _event(0)
        with patch('realtime.broadcast.broadcast_change') as told:
            auto_manage_events()
        told.assert_not_called()

        Event.objects.filter(pk=ev.pk).update(is_active=False)
        with patch('realtime.broadcast.broadcast_change') as told:
            auto_manage_events()
        told.assert_called_once_with('event', 'changed')

    def test_the_server_runs_it_on_its_daily_pass(self):
        from vehicles.models import DailyJobRun
        from vehicles.scheduler import DAILY_JOBS, run_due_jobs

        self.assertIn('auto_manage_events', DAILY_JOBS)
        stale = _event(-3)
        Event.objects.filter(pk=stale.pk).update(is_active=True, archived=False)

        run_due_jobs()

        self.assertEqual(_flags(stale), (False, True))
        self.assertTrue(DailyJobRun.objects.filter(
            job='auto_manage_events', run_date=timezone.localdate(),
            finished_at__isnull=False).exists())


class EventApiStatusTests(TestCase):
    def setUp(self):
        admin = User.objects.create_user(
            email='status-api@test.local', last_name='API', first_name='STATUS',
            password='SecurePassword123!', role='admin')
        self.client = APIClient()
        self.client.force_authenticate(user=admin)

    def _create(self, days):
        day = (timezone.localdate() + timedelta(days=days)).isoformat()
        return self.client.post(EVENTS, {'name': 'SPORTS FEST', 'date': day}, format='json')

    def _patch(self, ev, body):
        return self.client.patch(f'{EVENTS}{ev.pk}/', body, format='json')

    def test_created_for_today_is_active_at_once(self):
        r = self._create(0)
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual((r.data['status'], r.data['is_active'], r.data['archived']),
                         ('active', True, False))

    def test_created_for_a_later_day_is_pending(self):
        r = self._create(5)
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual((r.data['status'], r.data['is_active'], r.data['archived']),
                         ('pending', False, False))

    def test_a_past_date_is_refused(self):
        r = self._create(-1)
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Event.objects.exists())

    def test_the_status_cannot_be_switched_by_hand(self):
        ev = _event(3)
        for body in ({'is_active': True}, {'archived': True}):
            r = self._patch(ev, body)
            self.assertEqual(r.status_code, 400)
            self.assertIn('follows its date', r.data['is_active'])
        self.assertEqual(_flags(ev), (False, False))

    def test_rescheduling_moves_the_status_with_the_date(self):
        ev = _event(-4)                                  # archived
        r = self._patch(ev, {'date': timezone.localdate().isoformat()})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['status'], 'active')
        self.assertEqual(_flags(ev), (True, False))

        later = (timezone.localdate() + timedelta(days=7)).isoformat()
        self.assertEqual(self._patch(ev, {'date': later}).data['status'], 'pending')
        self.assertEqual(_flags(ev), (False, False))

    def test_rescheduling_into_the_past_is_refused(self):
        ev = _event(2)
        past = (timezone.localdate() - timedelta(days=1)).isoformat()
        r = self._patch(ev, {'date': past})
        self.assertEqual(r.status_code, 400)
        self.assertIn('past', r.data['date'])
        self.assertEqual(Event.objects.get(pk=ev.pk).status, 'pending')

    def test_editing_an_archived_event_keeps_it_archived(self):
        ev = _event(-2)
        r = self._patch(ev, {'name': 'RENAMED', 'organizer_plates': ['ABC1234']})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['status'], 'archived')

    def test_the_list_reports_the_date_not_stale_flags(self):
        ev = _event(0)
        Event.objects.filter(pk=ev.pk).update(is_active=False, archived=True)
        row = next(e for e in self.client.get(EVENTS).data if e['id'] == ev.pk)
        self.assertEqual((row['status'], row['is_active'], row['archived']),
                         ('active', True, False))
