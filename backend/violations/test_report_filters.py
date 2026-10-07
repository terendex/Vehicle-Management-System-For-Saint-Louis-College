"""The violations report answers the same filters as the management screen.

Two bugs met here. The screen's status buttons are buckets, not model statuses
— "Confiscated (3rd)" used to filter on `fee_imposed`, which migration 0016
emptied and nothing has set since, so it could only ever return nothing — and
the report endpoint had no violation_type knob at all, so a table narrowed to
one type still exported every row.
"""
from django.test import TestCase
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from violations.models import Violation
from violations.views import _filter_violations_report


class ReportFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        def mk(plate, vtype='unauthorized_entry', **kw):
            return Violation.objects.create(
                violation_type=vtype, plate_number=plate, **kw)

        mk('AAA111', status='warning', offense_number=1)                    # standing
        mk('BBB222', status='warning', offense_number=3)                    # 3rd rung
        # Resolved by the plain PATCH, which leaves the status at 'warning'.
        mk('CCC333', status='warning', offense_number=1, is_resolved=True)
        mk('DDD444', status='cleared', offense_number=2)
        mk('EEE555', status='lifted',  offense_number=1, is_resolved=True)
        mk('FFF666', vtype='double_parking', status='warning', offense_number=1)

    def filtered(self, **params):
        """Plates the report would print, and the lines describing the filter."""
        qs, desc = _filter_violations_report(
            Request(APIRequestFactory().get('/', params)))
        return sorted(v.plate_number for v in qs), desc

    def test_no_filter_exports_everything(self):
        plates, desc = self.filtered()
        self.assertEqual(len(plates), 6)
        self.assertEqual(desc, [])

    def test_warnings_exclude_resolved_cleared_and_lifted(self):
        """CCC333 is the one that used to slip through: status 'warning' with
        is_resolved set. Testing the status alone counted it as still active."""
        plates, desc = self.filtered(status='warning')
        self.assertEqual(plates, ['AAA111', 'BBB222', 'FFF666'])
        self.assertIn('Status: Active warnings', desc)

    def test_confiscated_is_the_third_rung_not_the_legacy_fee_status(self):
        plates, desc = self.filtered(status='confiscated')
        self.assertEqual(plates, ['BBB222'])
        self.assertIn('Status: Confiscated (3rd offense)', desc)

    def test_resolved_spans_all_three_ways_a_violation_ends(self):
        plates, _ = self.filtered(status='resolved')
        self.assertEqual(plates, ['CCC333', 'DDD444', 'EEE555'])

    def test_type_filter(self):
        plates, desc = self.filtered(violation_type='double_parking')
        self.assertEqual(plates, ['FFF666'])
        self.assertIn('Type: Double Parking', desc)

    def test_status_and_type_narrow_together(self):
        plates, _ = self.filtered(status='warning', violation_type='double_parking')
        self.assertEqual(plates, ['FFF666'])

    def test_a_raw_model_status_still_resolves(self):
        """An older saved link carries 'cleared', not a bucket name."""
        plates, desc = self.filtered(status='cleared')
        self.assertEqual(plates, ['DDD444'])
        self.assertIn('Status: Cleared', desc)

    def test_search_reaches_the_fields_the_screen_searches(self):
        Violation.objects.create(violation_type='other', plate_number='GGG777',
                                 status='warning', owner_email='zed@slc.edu.ph',
                                 notes='straddled two bays')
        by_email, _ = self.filtered(search='zed@slc')
        by_notes, _ = self.filtered(search='straddled')
        self.assertEqual(by_email, ['GGG777'])
        self.assertEqual(by_notes, ['GGG777'])


class ReportLayoutTests(TestCase):
    """What the printed report looks like: its title, columns and sections."""

    @classmethod
    def setUpTestData(cls):
        Violation.objects.create(violation_type='unauthorized_entry', plate_number='WRN111',
                                 status='warning', offense_number=1)
        Violation.objects.create(violation_type='time_exceed', plate_number='OVR222',
                                 status='warning', offense_number=2, overstay_minutes=85)
        Violation.objects.create(violation_type='double_parking', plate_number='CNF333',
                                 status='warning', offense_number=3)
        Violation.objects.create(violation_type='unauthorized', plate_number='OLD444',
                                 status='cleared', offense_number=1)

    def request(self, **params):
        return Request(APIRequestFactory().get('/', params))

    def test_the_title_follows_the_status_filter(self):
        from violations.views import _violation_report_title
        self.assertEqual(_violation_report_title(self.request()), 'VIOLATIONS REPORT / WARNINGS')
        self.assertEqual(_violation_report_title(self.request(status='warning')), 'WARNINGS REPORT')
        self.assertEqual(_violation_report_title(self.request(status='confiscated')), 'VIOLATIONS REPORT')

    def test_no_status_fee_or_issuer_column(self):
        from violations.views import VIOLATION_REPORT_HEADERS, VIOLATION_REPORT_WIDTHS_MM
        self.assertEqual(VIOLATION_REPORT_HEADERS, ['#', 'Date & Time', 'Plate', 'Owner', 'Violation'])
        self.assertEqual(sum(VIOLATION_REPORT_WIDTHS_MM), 267)

    def test_unfiltered_is_grouped_by_status_with_no_date(self):
        from violations.views import (_filter_violations_report, _violation_report_period,
                                      _violation_report_rows, _violation_report_sections)
        request = self.request()
        qs, desc = _filter_violations_report(request)
        sections = _violation_report_sections(request, _violation_report_rows(qs))
        self.assertEqual([s['title'] for s in sections],
                         ['Warnings (2)', 'Confiscated (3rd offense) (1)', 'Cleared / Resolved (1)'])
        self.assertEqual(_violation_report_period(request), '')
        self.assertEqual(desc, [])
        overstay = next(r for r in sections[0]['rows'] if r[2] == 'OVR222')
        self.assertEqual(overstay[4], 'Overstaying (1 hr 25 min)')
        legacy = sections[2]['rows'][0]
        self.assertEqual(legacy[4], 'Unauthorized Entry')

    def test_a_status_filter_prints_its_own_section(self):
        from violations.views import (_filter_violations_report, _violation_report_rows,
                                      _violation_report_sections)
        request = self.request(status='warning')
        qs, _ = _filter_violations_report(request)
        sections = _violation_report_sections(request, _violation_report_rows(qs))
        self.assertEqual([s['title'] for s in sections], ['Active warnings (3)'])

    def test_dates_are_the_period_not_a_filter_line(self):
        from violations.views import _filter_violations_report, _violation_report_period
        request = self.request(date_from='2026-10-01', date_to='2026-10-05')
        _, desc = _filter_violations_report(request)
        self.assertEqual(desc, [])
        self.assertEqual(_violation_report_period(request),
                         'Period: October 1, 2026 to October 5, 2026')

    def test_unauthorized_entry_includes_the_legacy_type(self):
        from violations.views import _filter_violations_report
        qs, _ = _filter_violations_report(self.request(violation_type='unauthorized_entry'))
        self.assertEqual(sorted(v.plate_number for v in qs), ['OLD444', 'WRN111'])

    def test_the_pdf_builds(self):
        from accounts.models import User
        from rest_framework.test import APIClient
        admin = User.objects.create_user(email='vrep@slc.edu.ph', last_name='Rep', first_name='V',
                                         password='pw', role='admin', is_staff=True)
        client = APIClient()
        client.force_authenticate(admin)
        for params in ({}, {'status': 'warning'}, {'date_from': '2026-01-01'}):
            res = client.get('/api/violations/report/pdf/', params)
            self.assertEqual(res.status_code, 200, params)
            self.assertTrue(res.content.startswith(b'%PDF'))
        res = client.get('/api/violations/report/excel/')
        self.assertEqual(res.status_code, 200)

    def test_all_statuses_show_a_status_column(self):
        from violations.views import (_filter_violations_report, _violation_report_rows,
                                      _violation_report_sections)
        request = self.request()
        qs, _ = _filter_violations_report(request)
        sections = _violation_report_sections(request, _violation_report_rows(qs))
        for section in sections:
            self.assertEqual(section['headers'][-1], 'Status')
            self.assertEqual(sum(section['col_widths_mm']), 267)
        status = {row[2]: row[5] for section in sections for row in section['rows']}
        self.assertEqual(status, {'WRN111': 'Warning (1st offense)', 'OVR222': 'Warning (2nd offense)',
                                  'CNF333': 'Confiscated (3rd offense)', 'OLD444': 'Cleared'})

    def test_one_status_needs_no_status_column(self):
        from violations.views import (_filter_violations_report, _violation_report_rows,
                                      _violation_report_sections)
        request = self.request(status='warning')
        qs, _ = _filter_violations_report(request)
        [section] = _violation_report_sections(request, _violation_report_rows(qs))
        self.assertNotIn('Status', section['headers'])
