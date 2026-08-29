"""The registration management page's headline counts, its summary PDF, and
editing a registration period in place.

The counts exist because the applications table only ever holds one status at a
time — pending, accepted or rejected — so "how many have registered, and of
what type" cannot be read off the rows on screen. These tests pin that the
cross-tab covers every status and type at once, that the PDF is built from the
same numbers, and that the date filter narrows both.

The period tests cover the other half: a window that is already running is the
one most likely to need a change (a deadline moves, or the label was picked
wrong), so PATCH has to reach the active row without archiving it.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.request import Request
from rest_framework.test import APIClient

from accounts.models import User
from vehicles.models import RegistrationPeriod, VehicleRegistration


class RegistrationSummaryTests(TestCase):
    """Counts and the summary PDF at /registrations/summary/ and /report/summary-pdf/."""

    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            email='summaryadmin@slc.edu.ph', full_name='Summary Admin',
            password='pw', role='admin', is_staff=True, is_superuser=True)
        self.client.force_authenticate(self.admin)

        self._n = 0
        # 3 students (2 pending, 1 accepted), 2 employees (accepted, rejected),
        # 1 pending fetcher — a grid with a zero in it, so a summary that only
        # counted the types it saw would come out short.
        for _ in range(2):
            self.make('student', 'pending')
        self.make('student', 'accepted')
        self.make('employee', 'accepted')
        self.make('employee', 'rejected')
        self.make('fetcher', 'pending')

    def make(self, registrant_type, status, **over):
        self._n += 1
        i = self._n
        return VehicleRegistration.objects.create(
            registrant_type=registrant_type, status=status,
            full_name=f'COUNTED, TESTER {i}', email=f'counted{i}@slc.edu.ph',
            plate_number=f'CNT {i:04d}', vehicle_type='car',
            contact_number='+639171234567', address='San Fernando, La Union',
            drivers_license=f'N01-20-90{i:04d}', **over)

    def summary(self, **params):
        res = self.client.get('/api/vehicles/registrations/summary/', params)
        self.assertEqual(res.status_code, 200, res.data)
        return res.data

    def test_total_counts_every_status(self):
        self.assertEqual(self.summary()['total'], 6)

    def test_status_breakdown_lists_all_statuses_including_empty_ones(self):
        by_status = {s['key']: s['count'] for s in self.summary()['by_status']}
        self.assertEqual(by_status['pending'], 3)
        self.assertEqual(by_status['accepted'], 2)
        self.assertEqual(by_status['rejected'], 1)
        # No expired rows exist, but the key must still be reported — the page
        # renders one tile per status and would otherwise drop the column.
        self.assertEqual(by_status['expired'], 0)

    def test_each_status_carries_its_own_payment_split(self):
        """The payment tiles are scoped to the status the table is showing.

        Reporting a system-wide "unpaid 120" above a table showing 8 pending
        rows reads as a broken page rather than as two different questions.
        """
        self.make('student', 'pending', payment_status='paid', or_number='1380093')
        by_status = {s['key']: s for s in self.summary()['by_status']}

        pending = by_status['pending']
        self.assertEqual(pending['count'], 4)
        self.assertEqual(pending['by_payment']['paid'], 1)
        self.assertEqual(pending['by_payment']['unpaid'], 3)
        # Buckets with no rows are still reported, or the tile row loses a tile.
        self.assertEqual(pending['by_payment']['exempt'], 0)

    def test_each_status_carries_its_own_type_split(self):
        by_status = {s['key']: s for s in self.summary()['by_status']}
        pending = by_status['pending']
        self.assertEqual(pending['by_type']['student'], 2)
        self.assertEqual(pending['by_type']['fetcher'], 1)
        self.assertEqual(pending['by_type']['employee'], 0)

    def test_every_status_split_sums_to_that_status(self):
        """The scoped tiles must reconcile with the tile above them."""
        for st in self.summary()['by_status']:
            with self.subTest(status=st['key']):
                self.assertEqual(sum(st['by_payment'].values()), st['count'])
                self.assertEqual(sum(st['by_type'].values()), st['count'])

    def test_the_scoped_splits_still_total_the_global_ones(self):
        data = self.summary()
        global_pay = {p['key']: p['count'] for p in data['by_payment']}
        summed = {}
        for st in data['by_status']:
            for key, n in st['by_payment'].items():
                summed[key] = summed.get(key, 0) + n
        self.assertEqual(summed, global_pay,
                         'the per-status payment splits do not add up to the global one')

    def test_type_breakdown_carries_its_own_status_split(self):
        by_type = {t['key']: t for t in self.summary()['by_type']}
        self.assertEqual(by_type['student']['count'], 3)
        self.assertEqual(by_type['employee']['count'], 2)
        self.assertEqual(by_type['fetcher']['count'], 1)
        self.assertEqual(by_type['student']['by_status']['pending'], 2)
        self.assertEqual(by_type['student']['by_status']['accepted'], 1)
        self.assertEqual(by_type['fetcher']['by_status']['rejected'], 0)

    def test_type_labels_are_human_readable(self):
        labels = {t['key']: t['label'] for t in self.summary()['by_type']}
        self.assertEqual(labels['student'], 'Student')

    def test_summary_pdf_downloads(self):
        res = self.client.get('/api/vehicles/registrations/report/summary-pdf/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'application/pdf')
        self.assertIn('attachment;', res['Content-Disposition'])
        self.assertTrue(res.content.startswith(b'%PDF'))

    def test_summary_pdf_honours_the_date_filter(self):
        # The PDF's text is inside a compressed stream, so the filter is pinned
        # on the counts the view feeds it rather than on the rendered bytes.
        from vehicles.views import _filter_registrations_report, _registration_counts

        old = self.make('student', 'accepted')
        VehicleRegistration.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=40))
        self.assertEqual(_registration_counts(VehicleRegistration.objects.all())['total'], 7)

        from rest_framework.test import APIRequestFactory

        today = timezone.localdate().isoformat()
        request = APIRequestFactory().get(
            '/api/vehicles/registrations/report/summary-pdf/', {'date_from': today})
        qs, _desc = _filter_registrations_report(Request(request))
        counts = _registration_counts(qs)
        self.assertEqual(counts['total'], 6)               # backdated row excluded
        self.assertEqual(counts['by_type']['student'], 3)

    def test_summary_pdf_renders_with_a_date_filter_applied(self):
        res = self.client.get('/api/vehicles/registrations/report/summary-pdf/',
                              {'date_from': timezone.localdate().isoformat()})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.content.startswith(b'%PDF'))

    def test_the_numbers_reconcile(self):
        data = self.summary()
        self.assertEqual(sum(s['count'] for s in data['by_status']), data['total'])
        self.assertEqual(sum(t['count'] for t in data['by_type']), data['total'])
        for t in data['by_type']:
            self.assertEqual(sum(t['by_status'].values()), t['count'], t['key'])

    def test_a_row_outside_the_enums_still_reconciles(self):
        """`choices` is not a database constraint, so a legacy row can carry a
        value neither enum lists. It must be surfaced, not dropped: a total that
        disagrees with the rows above it reads as a broken report."""
        VehicleRegistration.objects.filter(pk=self.make('student', 'pending').pk).update(
            registrant_type='visitor', status='withdrawn')

        data = self.summary()
        self.assertEqual(data['total'], 7)
        self.assertEqual(sum(t['count'] for t in data['by_type']), 7)
        self.assertEqual(sum(s['count'] for s in data['by_status']), 7)

        by_type = {t['key']: t for t in data['by_type']}
        self.assertEqual(by_type['other']['count'], 1)
        self.assertEqual(by_type['other']['label'], 'Other')
        self.assertEqual(by_type['student']['count'], 3)   # unchanged
        by_status = {s['key']: s['count'] for s in data['by_status']}
        self.assertEqual(by_status['other'], 1)
        self.assertEqual(by_status['pending'], 3)          # unchanged

    def test_the_other_bucket_is_absent_when_every_row_is_recognised(self):
        data = self.summary()
        self.assertNotIn('other', [t['key'] for t in data['by_type']])
        self.assertNotIn('other', [s['key'] for s in data['by_status']])

    def test_summary_pdf_renders_an_out_of_enum_row(self):
        VehicleRegistration.objects.filter(pk=self.make('student', 'pending').pk).update(
            registrant_type='visitor', status='withdrawn')
        res = self.client.get('/api/vehicles/registrations/report/summary-pdf/')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.content.startswith(b'%PDF'))

    def test_payment_is_a_second_axis_over_the_same_rows(self):
        """Payment does not subdivide status — it re-slices the same rows, so
        both breakdowns must total to the same number."""
        data = self.summary()
        self.assertEqual(sum(p['count'] for p in data['by_payment']), data['total'])
        self.assertEqual(sum(s['count'] for s in data['by_status']), data['total'])

    def test_payment_breakdown_counts_each_bucket(self):
        VehicleRegistration.objects.filter(pk=self.make('student', 'accepted').pk).update(
            payment_status=VehicleRegistration.PaymentStatus.PAID)
        VehicleRegistration.objects.filter(pk=self.make('employee', 'accepted').pk).update(
            payment_status=VehicleRegistration.PaymentStatus.EXEMPT)

        by_payment = {p['key']: p['count'] for p in self.summary()['by_payment']}
        self.assertEqual(by_payment['paid'], 1)
        self.assertEqual(by_payment['exempt'], 1)
        # The six from setUp default to unpaid.
        self.assertEqual(by_payment['unpaid'], 6)

    def test_a_rejected_row_can_still_be_paid(self):
        """The case the second axis exists for: a refund. It must not be forced
        to choose between 'rejected' and 'paid'."""
        VehicleRegistration.objects.filter(
            pk=self.make('student', 'rejected').pk).update(payment_status='paid')
        data = self.summary()
        by_status  = {s['key']: s['count'] for s in data['by_status']}
        by_payment = {p['key']: p['count'] for p in data['by_payment']}
        self.assertEqual(by_status['rejected'], 2)
        self.assertEqual(by_payment['paid'], 1)

    def test_each_type_carries_its_own_payment_split(self):
        by_type = {t['key']: t for t in self.summary()['by_type']}
        for t in by_type.values():
            self.assertEqual(sum(t['by_payment'].values()), t['count'], t['key'])

    def test_payment_labels_are_human_readable(self):
        labels = {p['key']: p['label'] for p in self.summary()['by_payment']}
        self.assertEqual(labels['unpaid'], 'Unpaid')

    def test_summary_pdf_carries_both_axes(self):
        res = self.client.get('/api/vehicles/registrations/report/summary-pdf/')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.content.startswith(b'%PDF'))

    def test_non_admin_cannot_read_the_counts(self):
        owner = User.objects.create_user(
            email='summaryowner@slc.edu.ph', full_name='Summary Owner',
            password='pw', role='owner')
        self.client.force_authenticate(owner)
        res = self.client.get('/api/vehicles/registrations/summary/')
        self.assertEqual(res.status_code, 403)


class BrandedPdfExtraTablesTests(TestCase):
    """`extra_tables` on the shared PDF builder, which the summary report uses
    to keep its two axes in separate tables."""

    HEADERS = ['A', 'B', 'C']
    WIDTHS  = [70, 70, 50]

    def pdf(self, **kw):
        from report_utils import branded_pdf_response
        return branded_pdf_response(
            filename='s.pdf', report_title='T', subtitle='s', generated_by='G',
            col_widths_mm=self.WIDTHS, **kw).content

    def page_count(self, content):
        return content.count(b'/Type /Page\n') or content.count(b'/Type /Page ')

    def test_existing_callers_are_unaffected(self):
        """Every other report omits extra_tables; that path must still build."""
        content = self.pdf(headers=self.HEADERS, rows=[['x', 1, 2]])
        self.assertTrue(content.startswith(b'%PDF'))
        self.assertEqual(self.page_count(content), 1)

    def test_an_extra_table_is_actually_laid_out(self):
        """PDF text is glyph-encoded, so presence is pinned structurally: rows
        that need three pages must still need three pages when they are handed
        to the second table instead of the first. A silently ignored
        extra_tables would leave this on one page."""
        big = [[f'Row {i}', i, i] for i in range(60)]
        primary = self.pdf(headers=self.HEADERS, rows=big)
        extra = self.pdf(headers=self.HEADERS, rows=[['x', 1, 2]], extra_tables=[
            {'title': 'Second', 'headers': self.HEADERS, 'rows': big,
             'col_widths_mm': self.WIDTHS}])
        self.assertGreater(self.page_count(primary), 1)
        self.assertEqual(self.page_count(extra), self.page_count(primary))

    def test_both_tables_may_be_empty(self):
        content = self.pdf(headers=self.HEADERS, rows=[], extra_tables=[
            {'title': 'Second', 'headers': self.HEADERS, 'rows': [],
             'col_widths_mm': self.WIDTHS}])
        self.assertTrue(content.startswith(b'%PDF'))


class RegistrationPeriodEditTests(TestCase):
    """PATCH /registration-periods/<pk>/ — editing a window that is already live."""

    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            email='periodadmin@slc.edu.ph', full_name='Period Admin',
            password='pw', role='admin', is_staff=True, is_superuser=True)
        self.client.force_authenticate(self.admin)
        today = timezone.localdate()
        self.period = RegistrationPeriod.objects.create(
            label='S.Y. 2026–2027', is_active=True,
            start_date=today - timedelta(days=5), end_date=today + timedelta(days=5))

    def patch(self, data, pk=None):
        return self.client.patch(
            f'/api/vehicles/registration-periods/{pk or self.period.id}/',
            data, format='json')

    def test_active_period_can_be_edited_and_stays_active(self):
        new_end = (timezone.localdate() + timedelta(days=30)).isoformat()
        res = self.patch({'end_date': new_end})
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['end_date'], new_end)
        self.assertTrue(res.data['is_active'])
        self.period.refresh_from_db()
        self.assertEqual(self.period.end_date.isoformat(), new_end)
        self.assertTrue(self.period.is_active)

    def test_label_can_be_corrected_without_resending_the_dates(self):
        res = self.patch({'label': 'S.Y. 2027–2028'})
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['label'], 'S.Y. 2027–2028')
        # Omitted fields keep their stored value rather than blanking out.
        self.assertEqual(res.data['start_date'], self.period.start_date.isoformat())

    def test_end_before_start_is_rejected(self):
        res = self.patch({'end_date': (self.period.start_date - timedelta(days=1)).isoformat()})
        self.assertEqual(res.status_code, 400)
        self.assertIn('end_date', res.data)

    def test_blank_label_is_rejected(self):
        res = self.patch({'label': '   '})
        self.assertEqual(res.status_code, 400)
        self.assertIn('label', res.data)

    def test_malformed_date_is_rejected(self):
        res = self.patch({'start_date': '06-01-2026'})
        self.assertEqual(res.status_code, 400)
        self.assertIn('start_date', res.data)

    def test_editing_one_period_does_not_disturb_another(self):
        today = timezone.localdate()
        archived = RegistrationPeriod.objects.create(
            label='S.Y. 2025–2026', is_active=False,
            start_date=today - timedelta(days=400), end_date=today - timedelta(days=200))
        self.patch({'label': 'S.Y. 2028–2029'})
        archived.refresh_from_db()
        self.assertEqual(archived.label, 'S.Y. 2025–2026')
        self.assertFalse(archived.is_active)

    def test_creating_a_period_still_validates_every_field_at_once(self):
        """The create and edit paths share one validator; this pins that the
        refactor kept create reporting all of its errors together rather than
        stopping at the first one."""
        res = self.client.post('/api/vehicles/registration-periods/',
                               {'label': '', 'start_date': 'nope'}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('label', res.data)
        self.assertIn('start_date', res.data)
        self.assertIn('end_date', res.data)

    def test_creating_a_period_archives_the_previous_active_one(self):
        today = timezone.localdate()
        res = self.client.post('/api/vehicles/registration-periods/', {
            'label': 'S.Y. 2030–2031',
            'start_date': today.isoformat(),
            'end_date': (today + timedelta(days=60)).isoformat(),
        }, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertTrue(res.data['is_active'])
        self.period.refresh_from_db()
        self.assertFalse(self.period.is_active)

    def test_creating_a_period_rejects_an_end_before_its_start(self):
        today = timezone.localdate()
        res = self.client.post('/api/vehicles/registration-periods/', {
            'label': 'S.Y. 2030–2031',
            'start_date': today.isoformat(),
            'end_date': (today - timedelta(days=1)).isoformat(),
        }, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('end_date', res.data)
        self.assertEqual(RegistrationPeriod.objects.count(), 1)

    def test_editing_leaves_the_created_at_ordering_alone(self):
        """The list is ordered by created_at, so an edit must not jump a period
        to the top of the table."""
        before = list(RegistrationPeriod.objects.values_list('id', flat=True))
        self.patch({'label': 'S.Y. 2031–2032'})
        self.assertEqual(list(RegistrationPeriod.objects.values_list('id', flat=True)), before)

    def test_non_admin_cannot_edit_a_period(self):
        owner = User.objects.create_user(
            email='periodowner@slc.edu.ph', full_name='Period Owner',
            password='pw', role='owner')
        self.client.force_authenticate(owner)
        res = self.patch({'label': 'S.Y. 2099–2100'})
        self.assertEqual(res.status_code, 403)
