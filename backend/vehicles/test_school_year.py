"""The school year (August 1 to July 31) and what is derived from it.

  * a registration period's school year is read off its open date, and both
    dates must fall in it (June 1 of Y to July 31 of Y+1);
  * a school year that is over cannot be given a new window;
  * an owner account accepted for a school year expires on its July 31;
  * once a registration period closes, no unpaid application filed in it is
    left pending: its deadline is the close, and it expires at the close
    (plus the unadvertised grace hour) with its own reason. Paid ones wait
    for the CDSO as before.

The clock is pinned (timezone.now patched), so none of this depends on the
day the suite runs.
"""
from datetime import date, datetime, timedelta, timezone as dt_tz
from unittest import mock

from django.core import mail
from django.test import SimpleTestCase
from django.utils import timezone

from accounts.models import User
from vehicles.models import RegistrationPeriod, VehicleRegistration
from vehicles.registration_deadline import expire_overdue
from vehicles.school_year import (label, pass_expiry_date, school_year_of,
                                  validate_period, valid_until)
from vehicles.test_registration_payment import PaymentTestCase

R = VehicleRegistration
MANILA = dt_tz(timedelta(hours=8))


def at(*args):
    return datetime(*args, tzinfo=MANILA)


class SchoolYearRuleTests(SimpleTestCase):

    def test_school_year_comes_from_the_open_date(self):
        self.assertEqual(school_year_of(date(2026, 6, 1)), 2026)     # early registration
        self.assertEqual(school_year_of(date(2026, 8, 1)), 2026)     # first semester
        self.assertEqual(school_year_of(date(2026, 12, 31)), 2026)
        self.assertEqual(school_year_of(date(2027, 1, 15)), 2026)    # second semester
        self.assertEqual(school_year_of(date(2027, 5, 31)), 2026)
        self.assertEqual(school_year_of(date(2027, 6, 1)), 2027)     # next year's early window
        self.assertEqual(label(2026), 'S.Y. 2026–2027')
        self.assertEqual(valid_until(2026), date(2027, 7, 31))

    def test_dates_inside_the_school_year_pass(self):
        today = date(2026, 5, 20)
        for start, end in ((date(2026, 6, 1), date(2026, 9, 30)),
                           (date(2026, 6, 1), date(2027, 7, 31)),     # both boundary days
                           (date(2026, 8, 3), date(2026, 8, 3))):     # a one-day window
            self.assertEqual(validate_period(start, end, today), {}, (start, end))

    def test_close_outside_the_school_year_is_refused(self):
        errors = validate_period(date(2026, 6, 1), date(2027, 8, 15), date(2026, 5, 20))
        self.assertEqual(errors, {'end_date': 'Closes on must be within S.Y. 2026–2027 '
                                              '(June 1, 2026 to July 31, 2027).'})

    def test_may_belongs_to_the_previous_school_year(self):
        # Opening on May 31 is the tail of S.Y. 2025-2026, so closing in
        # August is past that year's July 31.
        errors = validate_period(date(2026, 5, 31), date(2026, 8, 31), date(2026, 5, 1))
        self.assertIn('S.Y. 2025–2026', errors['end_date'])

    def test_close_before_open_is_refused(self):
        errors = validate_period(date(2026, 9, 1), date(2026, 8, 31), date(2026, 5, 20))
        self.assertIn('end_date', errors)

    def test_a_finished_school_year_is_refused(self):
        errors = validate_period(date(2025, 6, 1), date(2025, 9, 30), date(2026, 10, 5))
        self.assertEqual(errors, {'start_date': 'S.Y. 2025–2026 has already ended. '
                                                'Choose dates in the current or coming school year.'})


class PeriodEndpointTests(PaymentTestCase):
    """The same rules, through the API the admin's form uses."""

    def setUp(self):
        clock = mock.patch('django.utils.timezone.now', return_value=at(2026, 10, 5, 12, 0))
        clock.start()
        self.addCleanup(clock.stop)
        super().setUp()
        self.client.force_authenticate(self.admin)

    def post(self, start, end):
        return self.client.post('/api/vehicles/registration-periods/',
                                {'start_date': start, 'end_date': end}, format='json')

    def test_a_date_outside_the_school_year_is_refused(self):
        res = self.post('2026-10-05', '2027-08-15')
        self.assertEqual(res.status_code, 400)
        self.assertIn('S.Y. 2026–2027', res.data['end_date'])

    def test_a_past_school_year_is_refused(self):
        res = self.post('2025-06-01', '2025-09-30')
        self.assertEqual(res.status_code, 400)
        self.assertIn('already ended', res.data['start_date'])

    def test_an_edit_cannot_push_the_close_past_july(self):
        period = RegistrationPeriod.get_active()
        res = self.client.patch(f'/api/vehicles/registration-periods/{period.pk}/',
                                {'end_date': '2027-08-01'}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_accepted_dates_are_named_for_their_school_year(self):
        res = self.post('2026-10-05', '2026-12-31')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['label'], 'S.Y. 2026–2027')
        self.assertEqual(res.data['valid_until'], '2027-07-31')


class PassValidityTests(PaymentTestCase):
    """Every owner account expires on July 31 at the end of its school year."""

    def setUp(self):
        self.clock = mock.patch('django.utils.timezone.now', return_value=at(2026, 10, 5, 12, 0))
        self.clock.start()
        self.addCleanup(self.clock.stop)
        super().setUp()

    def owner(self, email):
        return User.objects.create_user(email=email, last_name='Owner', first_name='Pass',
                                        password='pw', role='vehicle_owner', owner_type='student')

    def test_new_account_expires_on_the_last_day_of_the_school_year(self):
        self.assertEqual(self.owner('jul31@slc.edu.ph').expires_at, date(2027, 7, 31))

    def test_without_an_active_period_the_date_decides(self):
        RegistrationPeriod.objects.update(is_active=False)
        self.assertEqual(pass_expiry_date(date(2027, 3, 1)), date(2027, 7, 31))
        self.assertEqual(pass_expiry_date(date(2027, 6, 15)), date(2028, 7, 31))

    def test_a_stale_active_period_never_gives_a_date_already_past(self):
        RegistrationPeriod.objects.update(start_date=date(2025, 6, 1), end_date=date(2025, 9, 30))
        self.assertEqual(pass_expiry_date(date(2026, 10, 5)), date(2027, 7, 31))

    def test_the_settings_page_states_the_date(self):
        self.client.force_authenticate(self.admin)
        res = self.client.get('/api/vehicles/system-settings/')
        self.assertEqual(res.data['pass_valid_until'], '2027-07-31')


@mock.patch.object(R, 'PAYMENT_DEADLINE_ROLLOUT', at(2025, 1, 1))
class RegistrationCloseTests(PaymentTestCase):
    """Nothing filed in a closed period stays pending on the applicant's step."""

    # Monday Oct 5, 2026; the period (from setUp) runs Oct 4 to Nov 4.
    NOW = at(2026, 10, 5, 10, 0)

    def setUp(self):
        clock = mock.patch('django.utils.timezone.now', return_value=self.NOW)
        clock.start()
        self.addCleanup(clock.stop)
        super().setUp()
        self.period = RegistrationPeriod.get_active()
        # Registration closes this Wednesday, Oct 7.
        RegistrationPeriod.objects.filter(pk=self.period.pk).update(end_date=date(2026, 10, 7))

    def test_the_deadline_is_the_close_when_that_comes_first(self):
        reg = self.submit()
        # 3 working days would be Thursday 10 AM; registration closes first.
        self.assertEqual(reg.working_day_deadline(), at(2026, 10, 8, 10, 0))
        self.assertEqual(reg.payment_deadline(), at(2026, 10, 8, 0, 0))   # end of Wednesday
        res = self.client.get('/api/vehicles/register/payment/', {'token': str(reg.payment_token)})
        self.assertEqual(res.data['payment_seconds_left'],
                         int((at(2026, 10, 8) - self.NOW).total_seconds()))

    def test_unpaid_expires_at_the_close_and_paid_waits(self):
        unpaid = self.submit()
        paid = self.submit()
        self.assertEqual(self.pay(paid).status_code, 200)
        # Inside the grace hour after the close: still pending.
        with mock.patch('django.utils.timezone.now', return_value=at(2026, 10, 8, 0, 30)):
            self.assertEqual(expire_overdue(), [])
        mail.outbox.clear()
        with mock.patch('django.utils.timezone.now', return_value=at(2026, 10, 8, 1, 0)):
            expired = expire_overdue()
        self.assertEqual([r.pk for r in expired], [unpaid.pk])
        unpaid.refresh_from_db()
        paid.refresh_from_db()
        self.assertEqual(unpaid.status, R.Status.EXPIRED)
        self.assertEqual(unpaid.rejection_reason, R.EXPIRED_CLOSED_REASON)
        self.assertEqual(paid.status, R.Status.PENDING)
        [msg] = [m for m in mail.outbox if 'Expired' in m.subject]
        self.assertIn('registration closed before', msg.body)

    def test_the_dead_link_says_registration_closed(self):
        reg = self.submit()
        with mock.patch('django.utils.timezone.now', return_value=at(2026, 10, 9, 9, 0)):
            res = self.client.get('/api/vehicles/register/payment/', {'token': str(reg.payment_token)})
        self.assertEqual(res.status_code, 410, res.data)
        self.assertIn('registration closed', res.data['error'])

    def test_working_days_still_win_when_they_end_first(self):
        RegistrationPeriod.objects.filter(pk=self.period.pk).update(end_date=date(2026, 10, 30))
        reg = self.submit()
        with mock.patch('django.utils.timezone.now', return_value=at(2026, 10, 8, 11, 0)):
            expire_overdue()
        reg.refresh_from_db()
        self.assertEqual(reg.rejection_reason, R.EXPIRED_UNPAID_REASON)

    def test_an_extended_period_moves_the_deadline_back(self):
        reg = self.submit()
        RegistrationPeriod.objects.filter(pk=self.period.pk).update(end_date=date(2026, 12, 1))
        self.assertEqual(reg.payment_deadline(), reg.working_day_deadline())

    def test_filter_and_row_check_agree_with_overlapping_periods(self):
        """overdue_q() stays exact: plate and slot holds are released by it."""
        RegistrationPeriod.objects.create(label='Overlap', is_active=False,
                                          start_date=date(2026, 10, 6), end_date=date(2026, 10, 9))
        regs = [self.submit() for _ in range(16)]
        for i, reg in enumerate(regs):
            R.objects.filter(pk=reg.pk).update(created_at=at(2026, 10, 4, 0, 0) + timedelta(hours=7 * i))
            reg.refresh_from_db()
        pks = [r.pk for r in regs]
        for h in range(0, 24 * 10, 3):
            moment = at(2026, 10, 4, 0, 0) + timedelta(hours=h)
            with mock.patch('django.utils.timezone.now', return_value=moment):
                by_filter = set(R.objects.filter(R.overdue_q(), pk__in=pks).values_list('pk', flat=True))
                by_row = {r.pk for r in regs if r.payment_overdue()}
                self.assertEqual(by_filter, by_row, moment)
