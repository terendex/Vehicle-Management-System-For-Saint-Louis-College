"""Every PDF report answers the filters its screen shows.

The failure this guards against is silent and looks like the report being
wrong: a screen narrowed to one status exports every row, and nobody notices
until an operator checks a total. It happened on the Violations screen, where
ReportExportBar supported a `filters` prop that the page simply never passed.

Four report surfaces, checked here against the helper each PDF view actually
calls — not against the rendered bytes, whose text sits inside a compressed
stream:

    Audit Log         accounts._filter_audit_logs
    Vehicle Log       scanning._filter_access_logs (+ _apply_status_group)
    Registrations     vehicles._filter_registrations_report
    Violations        violations._filter_violations_report  (own file:
                      violations/test_report_filters.py)

Each case also asserts the filter is NAMED in filters_desc, because that line
is what the printed page uses to state what was left out of it.
"""
from datetime import datetime, time, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, APITestCase

from accounts.models import AuditLog
from accounts.views import _filter_audit_logs
from scanning.models import AccessLog
from scanning.views import (_apply_status_group, _filter_access_logs,
                            _merge_access_log_visits)
from vehicles.models import Vehicle, VehicleRegistration
from vehicles.views import _filter_registrations_report

User = get_user_model()


def _req(path, params):
    return Request(APIRequestFactory().get(path, params))


class AuditLogReportFilterTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(
            email='rf-alice@slc.edu.ph', full_name='ALICE ADMIN',
            password='x', role='admin')
        cls.bob = User.objects.create_user(
            email='rf-bob@slc.edu.ph', full_name='BOB GUARD',
            password='x', role='security')
        AuditLog.objects.create(actor=cls.alice, action=AuditLog.Action.GUARD_LOGIN,
                                details='alice signed in')
        AuditLog.objects.create(actor=cls.bob, action=AuditLog.Action.GUARD_LOGIN,
                                details='bob signed in')
        AuditLog.objects.create(actor=cls.alice, action=AuditLog.Action.RECORD_UPDATED,
                                details='alice edited a gate')

    def filtered(self, **params):
        qs, desc = _filter_audit_logs(_req('/api/accounts/audit-logs/export-pdf/', params))
        return list(qs), desc

    def test_no_filter_exports_everything(self):
        rows, desc = self.filtered()
        self.assertEqual(len(rows), 3)
        self.assertEqual(desc, [])

    def test_the_action_filter_narrows_and_is_named(self):
        rows, desc = self.filtered(action=AuditLog.Action.RECORD_UPDATED)
        self.assertEqual(len(rows), 1)
        self.assertTrue(any(d.startswith('Action:') for d in desc))

    def test_the_search_filter_narrows_and_is_named(self):
        rows, desc = self.filtered(search='BOB')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].actor, self.bob)
        self.assertIn("Search: 'BOB'", desc)

    def test_filters_combine(self):
        rows, _ = self.filtered(action=AuditLog.Action.GUARD_LOGIN, search='ALICE')
        self.assertEqual(len(rows), 1)


class VehicleLogReportFilterTests(APITestCase):
    """Status is the interesting one: it is deliberately NOT applied in SQL.

    Narrowing before the entry/exit merge would drop exit rows before they
    could be folded into their entries, so the report views merge first and
    call _apply_status_group afterwards. A test that only exercised
    _filter_access_logs would therefore prove nothing about the status filter.
    """

    def setUp(self):
        frozen = timezone.make_aware(
            datetime.combine(timezone.localdate(), time(13, 0)),
            timezone.get_current_timezone())
        patcher = mock.patch('django.utils.timezone.now', return_value=frozen)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.guard = User.objects.create_user(
            email='rf-vl-guard@slc.edu.ph', full_name='VL GUARD',
            password='x', role='security')
        owner = User.objects.create_user(
            email='rf-vl-owner@slc.edu.ph', full_name='CRUZ, MARIA',
            password='x', role='vehicle_owner')
        veh = Vehicle.objects.create(plate_number='VLR0001',
                                     vehicle_type=Vehicle.Type.CAR, user=owner)
        AccessLog.objects.create(plate_number='VLR0001', vehicle=veh,
                                 status=AccessLog.Status.AUTHORIZED,
                                 gate_id='gate1', entrant_category='student',
                                 scanned_by=self.guard)
        AccessLog.objects.create(plate_number='VLR0002',
                                 status=AccessLog.Status.DENIED,
                                 gate_id='gate2', entrant_category='visitor',
                                 scanned_by=self.guard)
        AccessLog.objects.create(plate_number='VLR0003',
                                 status=AccessLog.Status.UNKNOWN,
                                 gate_id='gate1', entrant_category='visitor',
                                 scanned_by=self.guard)

    def filtered(self, **params):
        qs, desc = _filter_access_logs(_req('/api/scan/logs/export-pdf/', params))
        visible, _ = _merge_access_log_visits(list(qs))
        visible = _apply_status_group(visible, params.get('status', ''))
        return {log.plate_number for log in visible}, desc

    def test_no_filter_exports_everything(self):
        plates, desc = self.filtered()
        self.assertEqual(plates, {'VLR0001', 'VLR0002', 'VLR0003'})
        self.assertEqual(desc, [])

    def test_the_gate_filter_narrows_and_is_named(self):
        plates, desc = self.filtered(gate_id='gate1')
        self.assertEqual(plates, {'VLR0001', 'VLR0003'})
        self.assertTrue(any(d.startswith('Gate:') for d in desc))

    def test_the_category_filter_narrows_and_is_named(self):
        plates, desc = self.filtered(category='visitor')
        self.assertEqual(plates, {'VLR0002', 'VLR0003'})
        self.assertTrue(any(d.startswith('Category:') for d in desc))

    def test_the_search_filter_reaches_the_owner_name(self):
        plates, desc = self.filtered(search='CRUZ')
        self.assertEqual(plates, {'VLR0001'})
        self.assertIn("Search: 'CRUZ'", desc)

    def test_the_status_filter_narrows_after_the_merge(self):
        plates, desc = self.filtered(status='authorized')
        self.assertEqual(plates, {'VLR0001'})
        self.assertTrue(any(d.startswith('Status:') for d in desc))

    def test_filters_combine(self):
        plates, _ = self.filtered(gate_id='gate1', category='visitor')
        self.assertEqual(plates, {'VLR0003'})


class RegistrationReportFilterTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        def mk(plate, name, status, rtype, payment):
            return VehicleRegistration.objects.create(
                plate_number=plate, full_name=name, email=f'{plate}@slc.edu.ph',
                status=status, registrant_type=rtype, payment_status=payment)

        mk('RG00001', 'SANTOS, JOSE',  'pending',  'student',  'unpaid')
        mk('RG00002', 'REYES, ANA',    'accepted', 'student',  'paid')
        mk('RG00003', 'LIM, CARLOS',   'accepted', 'employee', 'paid')
        mk('RG00004', 'TAN, LISA',     'rejected', 'employee', 'unpaid')

    def filtered(self, **params):
        qs, desc = _filter_registrations_report(
            _req('/api/vehicles/registrations/report/pdf/', params))
        return {r.plate_number for r in qs}, desc

    def test_no_filter_exports_everything(self):
        plates, desc = self.filtered()
        self.assertEqual(len(plates), 4)
        self.assertEqual(desc, [])

    def test_the_status_filter_narrows_and_is_named(self):
        plates, desc = self.filtered(status='accepted')
        self.assertEqual(plates, {'RG00002', 'RG00003'})
        self.assertTrue(any(d.startswith('Status:') for d in desc))

    def test_the_registrant_type_filter_narrows_and_is_named(self):
        plates, desc = self.filtered(registrant_type='employee')
        self.assertEqual(plates, {'RG00003', 'RG00004'})
        self.assertTrue(any(d.startswith('Registrant type:') for d in desc))

    def test_the_payment_filter_narrows_and_is_named(self):
        plates, desc = self.filtered(payment_status='unpaid')
        self.assertEqual(plates, {'RG00001', 'RG00004'})
        self.assertTrue(any(d.startswith('Payment:') for d in desc))

    def test_the_search_filter_reaches_the_name(self):
        plates, desc = self.filtered(search='REYES')
        self.assertEqual(plates, {'RG00002'})
        self.assertIn("Search: 'REYES'", desc)

    def test_all_three_narrow_together(self):
        """The combination the screen actually produces when an operator has
        clicked through the toolbar."""
        plates, desc = self.filtered(status='accepted', registrant_type='employee',
                                     payment_status='paid')
        self.assertEqual(plates, {'RG00003'})
        self.assertEqual(len(desc), 3)
