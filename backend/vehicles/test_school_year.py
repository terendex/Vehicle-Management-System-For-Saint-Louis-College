"""The school year (August 1 to July 31) and what is derived from it.

  * the admin picks a school year ("2026-2027") and the registration period is
    August 1 of its first year to July 31 of its second; no other dates can
    be set, on the form or through the API;
  * a school year has at most one period, and one that is over gets none;
  * a period saved with its own dates before this rule ("legacy") stays as
    history but cannot be made active;
  * an owner account accepted for a school year expires on its July 31;
  * once a registration period closes, no unpaid application filed in it is
    left pending: its deadline is the close, and it expires at the close
    (plus the unadvertised grace hour) with its own reason. Paid ones wait
    for the CDSO as before.

The clock is pinned (timezone.now patched), so none of this depends on the
day the suite runs. The same patch is how localhost testing works: every
check reads school_year.today().
"""
from datetime import date, datetime, timedelta, timezone as dt_tz
from unittest import mock

from django.core import mail
from django.test import SimpleTestCase
from django.utils import timezone

from accounts.models import User
from vehicles import school_year as sy
from vehicles.models import RegistrationPeriod, VehicleRegistration
from vehicles.registration_deadline import expire_overdue
from vehicles.school_year import label, pass_expiry_date, school_year_of, valid_until
from vehicles.test_registration_payment import PaymentTestCase

R = VehicleRegistration
MANILA = dt_tz(timedelta(hours=8))


def at(*args):
    return datetime(*args, tzinfo=MANILA)


class SchoolYearRuleTests(SimpleTestCase):

    def test_a_school_year_is_august_to_july(self):
        self.assertEqual(sy.period_dates(2026), (date(2026, 8, 1), date(2027, 7, 31)))
        self.assertEqual(valid_until(2026), date(2027, 7, 31))
        self.assertEqual(label(2026), 'S.Y. 2026–2027')
        self.assertEqual(sy.as_text(2026), '2026-2027')

    def test_which_school_year_a_date_is_in(self):
        self.assertEqual(school_year_of(date(2026, 8, 1)), 2026)
        self.assertEqual(school_year_of(date(2026, 12, 31)), 2026)
        self.assertEqual(school_year_of(date(2027, 7, 31)), 2026)
        self.assertEqual(school_year_of(date(2027, 8, 1)), 2027)

    def test_only_consecutive_years_parse(self):
        self.assertEqual(sy.parse('2026-2027'), 2026)
        self.assertEqual(sy.parse(' 2026 – 2027 '), 2026)
        for bad in ('2026-2028', '2026', '2027-2026', 'next year', None):
            with self.assertRaises(ValueError, msg=bad):
                sy.parse(bad)

    def test_legacy_periods_are_recognised(self):
        aug, jul = sy.period_dates(2026)
        self.assertEqual(sy.year_of_period(aug, jul), 2026)
        self.assertIsNone(sy.year_of_period(date(2026, 9, 29), date(2026, 10, 13)))

    def test_the_form_offers_this_school_year_and_the_next_two(self):
        self.assertEqual(sy.selectable_years(date(2026, 10, 5)), [2026, 2027, 2028])
        self.assertEqual(sy.selectable_years(date(2027, 7, 31)), [2026, 2027, 2028])


class PeriodEndpointTests(PaymentTestCase):
    """The same rules through the API, so the form cannot be bypassed."""

    def setUp(self):
        clock = mock.patch('django.utils.timezone.now', return_value=at(2026, 10, 5, 12, 0))
        clock.start()
        self.addCleanup(clock.stop)
        super().setUp()
        self.client.force_authenticate(self.admin)

    def post(self, **data):
        return self.client.post('/api/vehicles/registration-periods/', data, format='json')

    def test_selecting_a_school_year_fills_in_august_to_july(self):
        res = self.post(school_year='2026-2027')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual((res.data['start_date'], res.data['end_date']), ('2026-08-01', '2027-07-31'))
        self.assertEqual(res.data['label'], 'S.Y. 2026–2027')
        self.assertEqual(res.data['valid_until'], '2027-07-31')
        self.assertTrue(res.data['is_active'])
        self.assertFalse(res.data['legacy'])

    def test_the_form_options_carry_the_fixed_dates(self):
        res = self.client.get('/api/vehicles/registration-periods/', {'with_options': 1})
        first = res.data['school_years'][0]
        self.assertEqual((first['value'], first['start_date'], first['end_date']),
                         ('2026-2027', '2026-08-01', '2027-07-31'))

    def test_dates_outside_august_to_july_are_refused(self):
        for dates in ({'start_date': '2026-06-01'}, {'end_date': '2027-05-31'},
                      {'start_date': '2026-08-01', 'end_date': '2027-08-15'}):
            res = self.post(school_year='2026-2027', **dates)
            self.assertEqual(res.status_code, 400, dates)
            self.assertIn('No other dates can be set', str(res.data), dates)
        self.assertFalse(RegistrationPeriod.objects.filter(start_date=date(2026, 6, 1)).exists())

    def test_matching_dates_may_be_sent_along(self):
        res = self.post(school_year='2026-2027', start_date='2026-08-01', end_date='2027-07-31')
        self.assertEqual(res.status_code, 201, res.data)

    def test_the_same_school_year_cannot_be_created_twice(self):
        self.assertEqual(self.post(school_year='2026-2027').status_code, 201)
        res = self.post(school_year='2026-2027')
        self.assertEqual(res.status_code, 400)
        self.assertIn('already has a registration period', res.data['school_year'])
        self.assertEqual(RegistrationPeriod.objects.filter(start_date=date(2026, 8, 1)).count(), 1)
        options = self.client.get('/api/vehicles/registration-periods/', {'with_options': 1}).data
        self.assertTrue(options['school_years'][0]['taken'])

    def test_a_school_year_is_required_and_must_be_consecutive(self):
        self.assertIn('school_year', self.post().data)
        self.assertIn('school_year', self.post(school_year='2026-2028').data)

    def test_a_school_year_that_is_over_is_refused(self):
        res = self.post(school_year='2025-2026')
        self.assertEqual(res.status_code, 400)
        self.assertIn('already ended', res.data['school_year'])

    def test_a_period_cannot_be_edited(self):
        period = self.post(school_year='2026-2027').data
        res = self.client.patch(f'/api/vehicles/registration-periods/{period["id"]}/',
                                {'end_date': '2027-08-31'}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(RegistrationPeriod.objects.get(pk=period['id']).end_date, date(2027, 7, 31))

    def test_a_legacy_period_stays_but_cannot_be_activated(self):
        legacy = RegistrationPeriod.objects.create(label='S.Y. August 2026 – May 2027', is_active=False,
                                                   start_date=date(2026, 9, 29), end_date=date(2026, 10, 13))
        rows = self.client.get('/api/vehicles/registration-periods/').data
        self.assertTrue(next(r for r in rows if r['id'] == legacy.pk)['legacy'])
        res = self.client.post(f'/api/vehicles/registration-periods/{legacy.pk}/activate/')
        self.assertEqual(res.status_code, 400)
        # It does not take the school year from the real period either.
        self.assertEqual(self.post(school_year='2026-2027').status_code, 201)


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
        self.assertEqual(pass_expiry_date(date(2027, 8, 15)), date(2028, 7, 31))

    def test_a_stale_active_period_never_gives_a_date_already_past(self):
        RegistrationPeriod.objects.update(start_date=date(2025, 8, 1), end_date=date(2026, 7, 31))
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
