"""Scheduled visits at the gate: the guard's Expected Today feed, check-in
through a visitor pass, the arrival tick, and the slip block."""
import os
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from scanning.models import AccessLog, VisitorPass
from vehicles.models import ScheduledVisit, Supplier, SupplierPlate


def _user(email, role, **extra):
    return User.objects.create_user(email=email, full_name=email.split('@')[0].upper(),
                                    password='SecurePassword123!', role=role, **extra)


class ScheduledVisitGateTests(TestCase):
    def setUp(self):
        self.admin = _user('cdso@slc.edu.ph', 'admin')
        self.guard = _user('guard@slc.edu.ph', 'security', gate_assignment='gate1')
        self.client = APIClient()
        self.client.force_authenticate(user=self.guard)
        self.today = timezone.localdate()

    def _visit(self, **kw):
        fields = dict(visitor_name='DR. HELEN OCAMPO', category='guest', expected_date=self.today,
                      purpose='Board meeting', created_by=self.admin)
        fields.update(kw)
        return ScheduledVisit.objects.create(**fields)

    def _check_in(self, plate, **extra):
        resp = self.client.post('/api/scan/visitor-pass/', {
            'plate_number': plate, 'visitor_name': 'Helen Ocampo', 'purpose': 'Board meeting',
            'allowed_duration': 60, **extra}, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        return resp

    # ── Expected Today feed ────────────────────────────────────────────────
    def test_guard_sees_only_today(self):
        self._visit(visitor_name='TODAY')
        self._visit(visitor_name='TOMORROW', expected_date=self.today + timedelta(days=1))
        resp = self.client.get('/api/vehicles/scheduled-visits/today/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([v['visitor_name'] for v in resp.data], ['TODAY'])
        self.assertEqual(resp.data[0]['created_by_name'], 'CDSO')

    def test_owner_cannot_read_expected_list(self):
        owner = _user('owner@slc.edu.ph', 'vehicle_owner', owner_type='student')
        self.client.force_authenticate(user=owner)
        self.assertEqual(self.client.get('/api/vehicles/scheduled-visits/today/').status_code, 403)

    def test_guard_still_cannot_manage_visits(self):
        visit = self._visit()
        self.assertEqual(self.client.get('/api/vehicles/scheduled-visits/').status_code, 403)
        self.assertEqual(self.client.patch(f'/api/vehicles/scheduled-visits/{visit.pk}/',
                                           {'is_arrived': True}, format='json').status_code, 403)

    # ── Check-in ───────────────────────────────────────────────────────────
    def test_check_in_links_pass_and_arrives_only_when_slip_prints(self):
        visit = self._visit()   # no plate on file: the guard types it at the gate
        resp = self._check_in('HOC2020', scheduled_visit=visit.pk)
        pass_ = VisitorPass.objects.get(pk=resp.data['id'])
        self.assertEqual(pass_.scheduled_visit_id, visit.pk)
        visit.refresh_from_db()
        self.assertFalse(visit.is_arrived, 'no entry is logged until the slip prints')

        self.client.post(f"/api/scan/visitor-pass/{pass_.pk}/printed/")
        visit.refresh_from_db()
        self.assertTrue(visit.is_arrived)
        self.assertIsNotNone(visit.arrived_at)

    def test_plain_pass_on_a_scheduled_plate_links_by_plate(self):
        visit = self._visit(plate_number='HOC2020')
        resp = self._check_in('HOC 2020')   # guard came from the scan result, not the panel
        self.assertEqual(VisitorPass.objects.get(pk=resp.data['id']).scheduled_visit_id, visit.pk)

    def test_another_days_visit_is_not_linked(self):
        visit = self._visit(expected_date=self.today + timedelta(days=1), plate_number='HOC2020')
        resp = self._check_in('HOC2020', scheduled_visit=visit.pk)
        self.assertIsNone(VisitorPass.objects.get(pk=resp.data['id']).scheduled_visit_id)

    def test_slip_carries_the_scheduled_block(self):
        visit = self._visit()
        slip = self._check_in('HOC2020', scheduled_visit=visit.pk).data['slip']
        rows = [r for section in slip['sections'] for r in section]
        self.assertIn(['Scheduled Visit', f'SV-{visit.pk}', True], rows)
        self.assertIn(['Arranged by', 'CDSO'], rows)
        # The pass says "Board meeting" already; the booking's line would repeat it.
        self.assertNotIn('Booked for', [r[0] for r in rows])

    def test_slip_keeps_the_booked_purpose_when_it_differs(self):
        visit = self._visit(purpose='Accreditation visit')
        slip = self._check_in('HOC2020', scheduled_visit=visit.pk).data['slip']
        rows = [r for section in slip['sections'] for r in section]
        self.assertIn(['Booked for', 'Accreditation visit'], rows)

    def test_plateless_booking_records_the_car_they_came_in(self):
        visit = self._visit()
        booked = self._visit(visitor_name='BOOKED CAR', plate_number='BKD1234')
        for v, plate in ((visit, 'HOC 2020'), (booked, 'OTH5678')):
            resp = self._check_in(plate, scheduled_visit=v.pk)
            self.client.post(f"/api/scan/visitor-pass/{resp.data['id']}/printed/")
        visit.refresh_from_db(); booked.refresh_from_db()
        self.assertEqual(visit.plate_number, 'HOC2020')
        self.assertEqual(booked.plate_number, 'BKD1234')   # a booked plate is left as booked
        self.assertTrue(booked.is_arrived)

    def test_walk_in_slip_has_no_scheduled_block(self):
        slip = self._check_in('WLK1234').data['slip']
        labels = [r[0] for section in slip['sections'] for r in section]
        self.assertNotIn('Scheduled Visit', labels)

    # ── Arrival by plate ───────────────────────────────────────────────────
    def test_supplier_scan_ticks_off_its_visit_and_prints_it(self):
        supplier = Supplier.objects.create(company_name='ILOCOS FRESH', category='delivery', is_active=True)
        SupplierPlate.objects.create(supplier=supplier, plate_number='WTX4521')
        visit = self._visit(visitor_name='ILOCOS FRESH', category='delivery', supplier=supplier,
                            plate_number='WTX4521', purpose='Cafeteria delivery')
        feed = self.client.get('/api/vehicles/scheduled-visits/today/').data
        self.assertTrue(feed[0]['auto_admit'])

        with patch('scanning.views._supplier_rule_denial', return_value=None):
            resp = self.client.post('/api/scan/manual-entry/', {'plate_number': 'WTX4521'}, format='json')
        self.assertTrue(resp.data['allowed'])
        visit.refresh_from_db()
        self.assertTrue(visit.is_arrived)
        rows = [r for section in resp.data['supplier_slip']['sections'] for r in section]
        self.assertIn(['Booked for', 'Cafeteria delivery'], rows)

    def test_refused_scan_is_not_an_arrival(self):
        visit = self._visit(plate_number='ZZZ9999')
        self.client.post('/api/scan/manual-entry/', {'plate_number': 'ZZZ9999'}, format='json')
        self.assertTrue(AccessLog.objects.filter(plate_number='ZZZ9999').exclude(status='authorized').exists())
        visit.refresh_from_db()
        self.assertFalse(visit.is_arrived)

    # ── CDSO side ──────────────────────────────────────────────────────────
    def test_bad_or_past_date_is_400_not_500(self):
        self.client.force_authenticate(user=self.admin)
        for bad in ('21/09/2026', 'tomorrow', str(self.today - timedelta(days=1))):
            resp = self.client.post('/api/vehicles/scheduled-visits/',
                                    {'visitor_name': 'X', 'expected_date': bad}, format='json')
            self.assertEqual(resp.status_code, 400, bad)

    def test_create_records_who_scheduled_it(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post('/api/vehicles/scheduled-visits/',
                                {'visitor_name': 'X', 'expected_date': str(self.today)}, format='json')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['created_by_name'], 'CDSO')

    def test_hand_tick_is_audited_and_timestamped(self):
        from accounts.models import AuditLog
        visit = self._visit()
        self.client.force_authenticate(user=self.admin)
        resp = self.client.patch(f'/api/vehicles/scheduled-visits/{visit.pk}/', {'is_arrived': True}, format='json')
        self.assertTrue(resp.data['is_arrived'])
        self.assertIsNotNone(resp.data['arrived_at'])
        self.assertTrue(AuditLog.objects.filter(details__contains='Scheduled visit marked arrived').exists())
        resp = self.client.patch(f'/api/vehicles/scheduled-visits/{visit.pk}/', {'is_arrived': False}, format='json')
        self.assertIsNone(resp.data['arrived_at'])

    # ── Reschedule ─────────────────────────────────────────────────────────
    def _reschedule(self, visit, date):
        self.client.force_authenticate(user=self.admin)
        return self.client.patch(f'/api/vehicles/scheduled-visits/{visit.pk}/',
                                 {'expected_date': str(date)}, format='json')

    def test_no_show_is_rescheduled_in_place_and_reaches_the_gate(self):
        from accounts.models import AuditLog
        visit = self._visit()
        ScheduledVisit.objects.filter(pk=visit.pk).update(expected_date=self.today - timedelta(days=1))
        resp = self._reschedule(visit, self.today)
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['id'], visit.pk)   # same booking, same SV number
        self.assertTrue(AuditLog.objects.filter(details__contains=f'rescheduled | DR. HELEN OCAMPO (SV-{visit.pk})').exists())

        self.client.force_authenticate(user=self.guard)
        feed = self.client.get('/api/vehicles/scheduled-visits/today/').data
        self.assertEqual([v['id'] for v in feed], [visit.pk])

    def test_upcoming_visit_can_be_moved_before_its_day(self):
        visit = self._visit(expected_date=self.today + timedelta(days=2))
        resp = self._reschedule(visit, self.today + timedelta(days=5))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['expected_date'], str(self.today + timedelta(days=5)))

    def test_cannot_reschedule_into_the_past_or_after_arrival(self):
        visit = self._visit()
        self.assertEqual(self._reschedule(visit, self.today - timedelta(days=1)).status_code, 400)
        self.assertEqual(self._reschedule(visit, 'next week').status_code, 400)
        ScheduledVisit.objects.filter(pk=visit.pk).update(is_arrived=True)
        self.assertEqual(self._reschedule(visit, self.today + timedelta(days=1)).status_code, 400)
        visit.refresh_from_db()
        self.assertEqual(visit.expected_date, self.today)

    def test_guard_cannot_reschedule(self):
        visit = self._visit()
        resp = self.client.patch(f'/api/vehicles/scheduled-visits/{visit.pk}/',
                                 {'expected_date': str(self.today + timedelta(days=1))}, format='json')
        self.assertEqual(resp.status_code, 403)

    # ── Archive ────────────────────────────────────────────────────────────
    def _archive(self, visit, archived=True, reason=''):
        self.client.force_authenticate(user=self.admin)
        return self.client.patch(f'/api/vehicles/scheduled-visits/{visit.pk}/',
                                 {'archived': archived, 'archive_reason': reason}, format='json')

    def test_archive_keeps_the_record_with_who_when_and_why(self):
        from accounts.models import AuditLog
        visit = self._visit()
        resp = self._archive(visit, reason='Visitor cancelled')
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(resp.data['archived_at'])
        self.assertEqual(resp.data['archived_by_name'], 'CDSO')
        self.assertEqual(resp.data['archive_reason'], 'Visitor cancelled')
        self.assertTrue(ScheduledVisit.objects.filter(pk=visit.pk).exists())
        listed = self.client.get('/api/vehicles/scheduled-visits/', {'status': 'archived'}).data
        self.assertEqual([v['id'] for v in listed], [visit.pk])   # still on record, under Archived
        self.assertEqual(self.client.get('/api/vehicles/scheduled-visits/').data, [])   # off the active list
        self.assertTrue(AuditLog.objects.filter(details__contains='Scheduled visit archived').exists())

    def test_archived_visit_is_invisible_to_the_gate(self):
        visit = self._visit(plate_number='HOC2020')
        self._archive(visit)
        self.client.force_authenticate(user=self.guard)
        self.assertEqual(self.client.get('/api/vehicles/scheduled-visits/today/').data, [])
        # Neither the named link nor the plate match picks it up...
        resp = self._check_in('HOC2020', scheduled_visit=visit.pk)
        self.assertIsNone(VisitorPass.objects.get(pk=resp.data['id']).scheduled_visit_id)
        # ...and the entry does not tick it off.
        self.client.post(f"/api/scan/visitor-pass/{resp.data['id']}/printed/")
        visit.refresh_from_db()
        self.assertFalse(visit.is_arrived)

    def test_restore_brings_it_back_and_it_can_then_be_rescheduled(self):
        visit = self._visit()
        self._archive(visit)
        self.assertEqual(self._reschedule(visit, self.today + timedelta(days=1)).status_code, 400)
        resp = self._archive(visit, archived=False)
        self.assertIsNone(resp.data['archived_at'])
        self.assertEqual(resp.data['archive_reason'], '')
        self.assertEqual(self._reschedule(visit, self.today + timedelta(days=1)).status_code, 200)


class ScheduledVisitTableTests(TestCase):
    """The CDSO table's filters, and the PDF report taken from the same filter."""

    def setUp(self):
        self.admin = _user('cdso@slc.edu.ph', 'admin')
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        today = timezone.localdate()
        mk = lambda name, days, **kw: ScheduledVisit.objects.create(
            visitor_name=name, expected_date=today + timedelta(days=days), created_by=self.admin, **kw)
        self.today    = mk('TODAY GUEST', 0, category='guest', plate_number='TDY1234')
        self.later    = mk('LATER CONTRACTOR', 3, category='contractor', purpose='Roof inspection')
        self.arrived  = mk('ARRIVED GUEST', 0, category='guest', is_arrived=True, arrived_at=timezone.now())
        self.noshow   = mk('NOSHOW GUEST', -2, category='guest')
        self.archived = mk('ARCHIVED JOB', 1, category='maintenance', archived_at=timezone.now(),
                           archived_by=self.admin, archive_reason='Postponed')

    def names(self, **params):
        resp = self.client.get('/api/vehicles/scheduled-visits/', params)
        self.assertEqual(resp.status_code, 200)
        return sorted(v['visitor_name'] for v in resp.data)

    def test_status_tabs(self):
        self.assertEqual(self.names(), ['ARRIVED GUEST', 'LATER CONTRACTOR', 'NOSHOW GUEST', 'TODAY GUEST'])
        self.assertEqual(self.names(status='today'), ['TODAY GUEST'])
        self.assertEqual(self.names(status='upcoming'), ['LATER CONTRACTOR'])
        self.assertEqual(self.names(status='arrived'), ['ARRIVED GUEST'])
        self.assertEqual(self.names(status='no_show'), ['NOSHOW GUEST'])
        self.assertEqual(self.names(status='archived'), ['ARCHIVED JOB'])
        self.assertEqual(len(self.names(all=1)), 5)

    def test_search_category_and_dates(self):
        self.assertEqual(self.names(q='roof'), ['LATER CONTRACTOR'])
        self.assertEqual(self.names(q='tdy 1234'), ['TODAY GUEST'])
        self.assertEqual(self.names(q=f'SV-{self.noshow.pk}'), ['NOSHOW GUEST'])
        self.assertEqual(self.names(category='contractor'), ['LATER CONTRACTOR'])
        today = timezone.localdate()
        self.assertEqual(self.names(date_from=str(today), date_to=str(today)), ['ARRIVED GUEST', 'TODAY GUEST'])
        self.assertEqual(len(self.names(date_from='not-a-date')), 4)   # half-typed filter narrows nothing

    def test_pdf_report_follows_the_filter(self):
        resp = self.client.get('/api/vehicles/scheduled-visits/report/pdf/', {'status': 'no_show'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/pdf')
        body = b''.join(resp.streaming_content) if resp.streaming else resp.content
        self.assertTrue(body.startswith(b'%PDF'))

    def test_pdf_rows_match_the_table(self):
        with patch('report_utils.branded_pdf_response') as build:
            from django.http import HttpResponse
            build.return_value = HttpResponse(b'%PDF')
            self.client.get('/api/vehicles/scheduled-visits/report/pdf/', {'status': 'archived'})
        kw = build.call_args.kwargs
        self.assertEqual(len(kw['rows']), 1)
        self.assertEqual(kw['rows'][0][1], f'SV-{self.archived.pk}')
        self.assertEqual(kw['rows'][0][7], 'Archived — Postponed')
        self.assertIn('Status: Archived', kw['subtitle'])
        self.assertEqual(sum(kw['col_widths_mm']), 267)
        self.assertEqual(len(kw['col_widths_mm']), len(kw['headers']))

    def test_excel_report_follows_the_filter(self):
        resp = self.client.get('/api/vehicles/scheduled-visits/report/excel/', {'status': 'upcoming'})
        self.assertEqual(resp.status_code, 200)
        self.assertIn('spreadsheetml', resp['Content-Type'])

    def test_guard_cannot_download_report(self):
        guard = _user('guard@slc.edu.ph', 'security')
        self.client.force_authenticate(user=guard)
        self.assertEqual(self.client.get('/api/vehicles/scheduled-visits/report/pdf/').status_code, 403)



class ExpectedVisitCardTests(TestCase):
    """The Expected Visit thermal card printed from the CDSO table."""

    def setUp(self):
        self.admin = _user('cdso@slc.edu.ph', 'admin')
        self.guard = _user('guard@slc.edu.ph', 'security', gate_assignment='gate1')
        self.client = APIClient()
        self.visit = ScheduledVisit.objects.create(
            visitor_name='DR. HELEN OCAMPO', category='guest', purpose='Board meeting',
            expected_date=timezone.localdate() + timedelta(days=2), created_by=self.admin)
        self.code = f'SLC-SCHEDULED:{self.visit.pk}'

    def test_card_reads_as_an_expected_visit(self):
        self.client.force_authenticate(user=self.admin)
        slip = self.client.get('/api/scan/slip/', {'code': self.code}).data
        self.assertEqual(slip['title'], 'EXPECTED VISIT')
        self.assertEqual(slip['headline'], f'SV-{self.visit.pk}')   # no plate yet
        self.assertEqual(slip['state'], 'upcoming')
        rows = [r for section in slip['sections'] for r in section]
        self.assertIn(['Visitor', 'DR. HELEN OCAMPO', True], rows)
        self.assertIn(['Plate', 'Recorded at the gate'], rows)
        self.assertIn(['Arranged by', 'CDSO'], rows)
        out = os.environ.get('SCENARIO_OUT')
        if out:
            from scanning.slip_printer import render_slip
            render_slip(slip).save(os.path.join(out, 'slip_expected_visit.png'))

    def test_guard_can_scan_it_but_an_owner_cannot(self):
        self.client.force_authenticate(user=self.guard)
        self.assertEqual(self.client.get('/api/scan/slip/', {'code': self.code}).status_code, 200)
        owner = _user('owner@slc.edu.ph', 'vehicle_owner', owner_type='student')
        self.client.force_authenticate(user=owner)
        self.assertEqual(self.client.get('/api/scan/slip/', {'code': self.code}).status_code, 403)

    def test_prints_on_the_thermal_printer(self):
        self.client.force_authenticate(user=self.admin)
        with patch('scanning.slip_printer.find_printer', return_value='POS58 Printer'),              patch('scanning.slip_printer.send_raw') as send:
            resp = self.client.post('/api/scan/slip/print/', {'code': self.code}, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)
        send.assert_called_once()

    def test_card_is_not_an_exit(self):
        self.client.force_authenticate(user=self.guard)
        resp = self.client.post('/api/scan/slip/exit/', {'code': self.code}, format='json')
        self.assertEqual(resp.status_code, 400)
