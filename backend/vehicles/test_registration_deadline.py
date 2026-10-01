"""The 3-day payment deadline on online registrations.

An applicant has three days from submitting to pay at the Accounting Office and
file their Official Receipt. Past that (plus an unadvertised grace hour for a
slow upload), the application expires and releases what it was holding. These
tests pin:

  * the deadline is told to the applicant at every step (submit response,
    acknowledgement email, payment page, edit page);
  * only the applicant's own step is on the clock — a paid or fee-exempt
    application never expires, however long the CDSO takes;
  * expiry frees the plate for a fresh application, mails the applicant, and
    the dead link says why instead of a generic "invalid link";
  * an upload that arrives inside the grace hour is still accepted;
  * applications filed before the rollout get three days from the rollout.

Time is moved by backdating created_at, not by patching the clock, so the
tests do not depend on what time of day the suite runs.
"""
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.utils import timezone

from vehicles import scheduler
from vehicles.models import DailyJobRun, VehicleRegistration
from vehicles.registration_deadline import expire_overdue
from vehicles.test_registration_payment import PaymentTestCase

R = VehicleRegistration
PS = R.PaymentStatus
LONG_AGO = timezone.now() - timedelta(days=365)


def age(reg, delta):
    """Make `reg` look as if it was submitted `delta` ago."""
    R.objects.filter(pk=reg.pk).update(created_at=timezone.now() - delta)
    reg.refresh_from_db()
    return reg


# A rollout a year back, so only the 3-day rule itself is in play.
@mock.patch.object(R, 'PAYMENT_DEADLINE_ROLLOUT', LONG_AGO)
class DeadlineIsStatedTests(PaymentTestCase):

    def test_submit_response_carries_the_deadline(self):
        res = self.client.post('/api/vehicles/register/open/', dict(
            registrant_type='student', last_name='DUE', first_name='DATE', email='due@slc.edu.ph',
            plate_number='DUE 0001', vehicle_type='car', drivers_license='N01-20-990001',
            program_year='BSIT 1', campus_days=['Monday'], student_level='college',
        ), format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['payment_window_days'], 3)
        self.assertTrue(res.data['payment_deadline_display'])
        left = res.data['payment_seconds_left']
        self.assertAlmostEqual(left, 3 * 86400, delta=120)

    def test_acknowledgement_email_states_the_deadline(self):
        reg = self.submit()
        body = mail.outbox[-1].body
        self.assertIn('DEADLINE:', body)
        self.assertIn('expires automatically', body)
        from vehicles.registration_deadline import format_deadline
        self.assertIn(format_deadline(reg.payment_deadline()), body)

    def test_payment_and_edit_pages_carry_the_deadline(self):
        reg = self.submit()
        for url in ('/api/vehicles/register/payment/', '/api/vehicles/register/details/'):
            res = self.client.get(url, {'token': str(reg.payment_token)})
            self.assertEqual(res.status_code, 200, (url, res.data))
            self.assertIsNotNone(res.data['payment_seconds_left'], url)
            self.assertTrue(res.data['payment_deadline_display'], url)

    def test_exempt_applicant_is_never_shown_a_deadline(self):
        reg = self.submit_employee('Cleaning and Services')
        self.assertIsNone(reg.payment_deadline())
        res = self.client.get('/api/vehicles/register/payment/', {'token': str(reg.payment_token)})
        self.assertIsNone(res.data['payment_seconds_left'])
        self.assertNotIn('DEADLINE:', mail.outbox[-1].body)


@mock.patch.object(R, 'PAYMENT_DEADLINE_ROLLOUT', LONG_AGO)
class ExpiryTests(PaymentTestCase):

    def test_unpaid_application_expires_after_three_days(self):
        reg = age(self.submit(), timedelta(days=3, hours=2))
        mail.outbox.clear()
        expired = expire_overdue()
        self.assertEqual([r.pk for r in expired], [reg.pk])
        reg.refresh_from_db()
        self.assertEqual(reg.status, R.Status.EXPIRED)
        self.assertEqual(reg.rejection_reason, R.EXPIRED_UNPAID_REASON)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Expired', mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].to, [reg.email])

    def test_not_yet_due_is_left_alone(self):
        reg = age(self.submit(), timedelta(days=2, hours=23))
        self.assertEqual(expire_overdue(), [])
        reg.refresh_from_db()
        self.assertEqual(reg.status, R.Status.PENDING)

    def test_grace_hour_is_not_expired_by_the_sweep(self):
        reg = age(self.submit(), timedelta(days=3, minutes=30))
        self.assertEqual(expire_overdue(), [])
        self.assertEqual(R.objects.get(pk=reg.pk).status, R.Status.PENDING)

    def test_upload_inside_the_grace_hour_is_accepted(self):
        """A slow connection that started before the deadline must not lose."""
        reg = age(self.submit(), timedelta(days=3, minutes=30))
        res = self.pay(reg)
        self.assertEqual(res.status_code, 200, res.data)
        reg.refresh_from_db()
        self.assertEqual(reg.payment_status, PS.PAID)
        # Paid now, so the clock is off for good.
        age(reg, timedelta(days=30))
        self.assertEqual(expire_overdue(), [])

    def test_paid_application_never_expires(self):
        reg = self.submit()
        self.assertEqual(self.pay(reg).status_code, 200)
        age(reg, timedelta(days=30))
        self.assertEqual(expire_overdue(), [])
        self.assertEqual(R.objects.get(pk=reg.pk).status, R.Status.PENDING)

    def test_exempt_application_never_expires(self):
        reg = age(self.submit_employee('Cleaning and Services'), timedelta(days=30))
        self.assertEqual(expire_overdue(), [])
        self.assertEqual(R.objects.get(pk=reg.pk).status, R.Status.PENDING)

    def test_walk_in_is_not_on_the_clock(self):
        reg = age(self.submit(), timedelta(days=30))
        R.objects.filter(pk=reg.pk).update(source=R.Source.DIRECT)
        self.assertEqual(expire_overdue(), [])

    def test_overdue_link_expires_on_open_and_says_why(self):
        reg = age(self.submit(), timedelta(days=4))
        for url in ('/api/vehicles/register/payment/', '/api/vehicles/register/details/'):
            res = self.client.get(url, {'token': str(reg.payment_token)})
            self.assertEqual(res.status_code, 410, (url, res.data))
            self.assertTrue(res.data['expired'])
            self.assertIn('3 days', res.data['error'])
        self.assertEqual(R.objects.get(pk=reg.pk).status, R.Status.EXPIRED)

    def test_late_upload_is_refused_as_expired(self):
        reg = age(self.submit(), timedelta(days=3, hours=2))
        res = self.pay(reg)
        self.assertEqual(res.status_code, 410, res.data)
        self.assertEqual(R.objects.get(pk=reg.pk).payment_status, PS.UNPAID)

    def test_unknown_token_still_gets_the_generic_answer(self):
        import uuid
        res = self.client.get('/api/vehicles/register/payment/', {'token': str(uuid.uuid4())})
        self.assertEqual(res.status_code, 404)
        self.assertNotIn('expired', res.data)

    def test_reviewed_application_link_is_not_called_expired(self):
        reg = self.submit()
        R.objects.filter(pk=reg.pk).update(status=R.Status.REJECTED)
        res = self.client.get('/api/vehicles/register/payment/', {'token': str(reg.payment_token)})
        self.assertEqual(res.status_code, 404)

    def test_expiry_frees_the_plate_for_a_new_application(self):
        """The submit sweeps first, so nobody waits for the hourly job."""
        old = age(self.submit(plate_number='FREE 001', email='first@slc.edu.ph'),
                  timedelta(days=4))
        avail = self.client.get('/api/vehicles/register/availability/',
                                {'plate_number': 'FREE 001'})
        self.assertIsNone(avail.data['plate_number'], 'overdue row must not read as taken')
        new = self.submit(plate_number='FREE 001', email='second@slc.edu.ph')
        self.assertEqual(R.objects.get(pk=old.pk).status, R.Status.EXPIRED)
        self.assertEqual(new.status, R.Status.PENDING)

    def test_a_second_sweep_does_nothing(self):
        age(self.submit(), timedelta(days=4))
        self.assertEqual(len(expire_overdue()), 1)
        mail.outbox.clear()
        self.assertEqual(expire_overdue(), [])
        self.assertEqual(mail.outbox, [])

    def test_cdso_sees_the_deadline_in_the_queue(self):
        reg = self.submit()
        self.client.force_authenticate(user=self.admin)
        res = self.client.get('/api/vehicles/registrations/pending/?status=pending')
        row = next(r for r in res.data if r['id'] == reg.pk)
        self.assertTrue(row['payment_deadline'])
        self.assertTrue(row['payment_deadline_display'])


class RolloutTests(PaymentTestCase):
    """Applications filed before the rule existed get three days from rollout."""

    def test_old_application_gets_three_days_from_rollout(self):
        reg = self.submit()
        R.objects.filter(pk=reg.pk).update(created_at=R.PAYMENT_DEADLINE_ROLLOUT - timedelta(days=20))
        reg.refresh_from_db()
        self.assertEqual(reg.payment_deadline(), R.PAYMENT_DEADLINE_ROLLOUT + R.PAYMENT_WINDOW)
        # Just past the rollout itself, nothing is overdue yet.
        with mock.patch('django.utils.timezone.now',
                        return_value=R.PAYMENT_DEADLINE_ROLLOUT + timedelta(days=1)):
            self.assertEqual(expire_overdue(), [])
        with mock.patch('django.utils.timezone.now',
                        return_value=R.PAYMENT_DEADLINE_ROLLOUT + timedelta(days=3, hours=2)):
            self.assertEqual([r.pk for r in expire_overdue()], [reg.pk])


class SchedulerTests(PaymentTestCase):

    def test_sweep_is_claimed_hourly(self):
        key = scheduler._claim_key('expire_unpaid_registrations')
        self.assertRegex(key, r'^expire_unpaid_registrations:h\d\d$')

    def test_task_prunes_its_old_ledger_rows(self):
        from vehicles.tasks import expire_unpaid_registrations
        old_day = timezone.localdate() - timedelta(days=10)
        DailyJobRun.objects.create(job='expire_unpaid_registrations:h03', run_date=old_day)
        DailyJobRun.objects.create(job='auto_backup', run_date=old_day)
        expire_unpaid_registrations()
        self.assertFalse(DailyJobRun.objects.filter(job__startswith='expire_unpaid').exists())
        self.assertTrue(DailyJobRun.objects.filter(job='auto_backup').exists())
