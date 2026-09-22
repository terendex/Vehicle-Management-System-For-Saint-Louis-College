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
        self.assertIn('Status: Confiscated (3rd offence)', desc)

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
