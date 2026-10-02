"""One Official Receipt, one registration.

An OR number is proof that one Vehicle Pass fee was paid, so no two
registrations may carry the same one — whichever path files it (the applicant's
payment link, CDSO's approval, or a counter walk-in), and whatever the other
row's status. The race test at the bottom is the slow-connection case: two
submissions of the same receipt that both pass the first check before either
has saved.
"""
import threading
from unittest import mock

from django.db import connection
from django.test import TransactionTestCase, override_settings

from vehicles import views
from vehicles.models import VehicleRegistration
from vehicles.test_registration_payment import LOCMEM, PaymentTestCase, WalkInPaymentTests

PS = VehicleRegistration.PaymentStatus


class PaymentLinkTests(PaymentTestCase):

    def test_a_receipt_on_another_application_is_refused(self):
        first, second = self.submit(), self.submit()
        self.assertEqual(self.pay(first, or_number='1380093').status_code, 200)

        res = self.pay(second, or_number='1380093')
        self.assertEqual(res.status_code, 400)
        self.assertIn('already been used', res.data['error'])
        second.refresh_from_db()
        self.assertEqual(second.or_number, '')
        self.assertEqual(second.payment_status, PS.UNPAID)

    def test_the_refusal_does_not_name_the_other_applicant(self):
        """The link is public; a guessed receipt number must not leak a name."""
        first, second = self.submit(last_name='SECRETNAME'), self.submit()
        self.pay(first, or_number='1380093')
        res = self.pay(second, or_number='1380093')
        self.assertNotIn('SECRETNAME', res.data['error'])
        self.assertNotIn(f'{first.pk:06d}', res.data['error'])

    def test_refiling_your_own_number_is_not_a_duplicate(self):
        """A retry after a dropped connection resends the same number."""
        reg = self.submit()
        self.assertEqual(self.pay(reg, or_number='1380093').status_code, 200)
        self.assertEqual(self.pay(reg, or_number='1380093', receipt=None).status_code, 200)

    def test_leading_zeros_do_not_make_a_new_receipt(self):
        first, second = self.submit(), self.submit()
        self.pay(first, or_number='0123456')
        self.assertEqual(self.pay(second, or_number='123456').status_code, 400)

    def test_a_receipt_on_a_rejected_application_still_counts(self):
        first, second = self.submit(), self.submit()
        self.pay(first, or_number='1380093')
        VehicleRegistration.objects.filter(pk=first.pk).update(
            status=VehicleRegistration.Status.REJECTED)
        self.assertEqual(self.pay(second, or_number='1380093').status_code, 400)


class ApprovalTests(PaymentTestCase):

    def test_approving_with_a_used_receipt_is_refused(self):
        first, second = self.submit(), self.submit()
        self.pay(first, or_number='1380093')

        res = self.accept(second, or_number='1380093')
        self.assertEqual(res.status_code, 400)
        # CDSO is told which registration holds it, so they can go and look.
        self.assertIn(f'REG-{first.pk:06d}', res.data['error'])
        second.refresh_from_db()
        self.assertEqual(second.status, VehicleRegistration.Status.PENDING)

    def test_approving_with_the_applicants_own_receipt_goes_through(self):
        reg = self.submit()
        self.pay(reg, or_number='1380093')
        self.assertEqual(self.accept(reg, or_number='1380093').status_code, 200)

    def test_a_second_approval_is_refused_plainly(self):
        """The slow-connection retry: the first click went through."""
        reg = self.submit()
        self.pay(reg, or_number='1380093')
        self.assertEqual(self.accept(reg).status_code, 200)
        res = self.accept(reg)
        self.assertEqual(res.status_code, 400)


class WalkInTests(PaymentTestCase):
    walk_in = WalkInPaymentTests.walk_in

    def test_a_walk_in_with_a_used_receipt_is_refused(self):
        self.assertEqual(self.walk_in(department='Teaching', or_number='1380093').status_code, 201)
        res = self.walk_in(department='Teaching', or_number='1380093')
        self.assertEqual(res.status_code, 400)
        self.assertIn('only be used once', res.data['error'])
        self.assertEqual(VehicleRegistration.objects.filter(or_number='1380093').count(), 1)


@override_settings(EMAIL_BACKEND=LOCMEM, DEFAULT_FROM_EMAIL='slccdso@gmail.com',
                   PUBLIC_SITE_URL='https://slc.example.edu')
class SimultaneousFilingTests(TransactionTestCase):
    """Two applicants file the same receipt at the same moment.

    The first duplicate check is held until BOTH requests have passed it, which
    is exactly what a slow upload does in real life. Only the lock taken inside
    the transaction can then stop the second write.
    """

    def setUp(self):
        self.helper = PaymentTestCase(methodName='setUp')
        self.helper.setUp()

    def test_only_one_of_two_simultaneous_filings_lands(self):
        results = self.race()
        self.assertEqual(sorted(results), [200, 400])
        self.assertEqual(VehicleRegistration.objects.filter(or_number='1380093').count(), 1)

    def test_without_the_lock_both_would_land(self):
        """The control: proves the race above is real, and only the lock stops it.
        If this ever fails, the test above has stopped testing anything."""
        with mock.patch.object(views, '_lock_or_number', lambda or_number: None):
            results = self.race()
        self.assertEqual(sorted(results), [200, 200])
        self.assertEqual(VehicleRegistration.objects.filter(or_number='1380093').count(), 2)

    def race(self):
        """Two applications file OR 1380093 at once; returns both status codes."""
        if connection.vendor != 'postgresql':
            self.skipTest('the receipt lock is a Postgres advisory lock')
        h = self.helper
        regs = [h.submit(), h.submit()]

        real_owner = views._or_number_owner
        barrier = threading.Barrier(2, timeout=30)
        calls = threading.local()

        def held_first_check(*args, **kwargs):
            result = real_owner(*args, **kwargs)
            if not getattr(calls, 'seen', False):   # only the pre-check, never the locked recheck
                calls.seen = True
                barrier.wait()
            return result

        results = []

        def file(reg):
            from rest_framework.test import APIClient
            from vehicles.test_registration_payment import receipt_file
            try:
                res = APIClient().post('/api/vehicles/register/payment/', {
                    'token': str(reg.payment_token), 'or_number': '1380093',
                    'or_receipt_image': receipt_file(),
                }, format='multipart')
                results.append(res.status_code)
            finally:
                connection.close()

        with mock.patch.object(views, '_or_number_owner', held_first_check):
            threads = [threading.Thread(target=file, args=(r,)) for r in regs]
            for t in threads:
                t.start()
            for t in threads:
                t.join(60)
        return results
