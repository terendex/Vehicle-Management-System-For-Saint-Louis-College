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
from vehicles.models import RuleConstraint, ScheduledVisit, Supplier, SupplierPlate, Vehicle
from accounts.names import name_kwargs


def _user(email, role, **extra):
    return User.objects.create_user(email=email, **name_kwargs(email.split('@')[0].upper()),
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
                                {'visitor_name': 'X', 'category': 'guest',
                                 'expected_date': str(self.today)}, format='json')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['created_by_name'], 'CDSO')

    # ── "Other" must say what it means ─────────────────────────────────────
    def _schedule(self, **body):
        self.client.force_authenticate(user=self.admin)
        return self.client.post('/api/vehicles/scheduled-visits/', {
            'visitor_name': 'X', 'expected_date': str(self.today), **body}, format='json')

    def test_other_without_its_text_is_refused(self):
        for body in ({'category': 'other'}, {'category': 'other', 'category_other': '   '}, {}):
            resp = self._schedule(**body)
            self.assertEqual(resp.status_code, 400, body)
            self.assertIn('category_other', resp.data)
        self.assertFalse(ScheduledVisit.objects.exists())

    def test_other_is_stored_with_its_text_and_read_back_as_one_label(self):
        resp = self._schedule(category='other', category_other='  Catering   service ')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['category_other'], 'Catering service')
        self.assertEqual(resp.data['category_label'], 'Other: Catering service')
        # The CDSO's search finds a visit by what "Other" was specified as.
        found = self.client.get('/api/vehicles/scheduled-visits/', {'q': 'catering'})
        self.assertEqual([v['id'] for v in found.data], [resp.data['id']])

    def test_a_listed_category_drops_any_stray_other_text(self):
        resp = self._schedule(category='delivery', category_other='Catering')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['category_other'], '')
        self.assertEqual(resp.data['category_label'], 'Delivery')

    def test_expected_visit_card_prints_the_specified_category(self):
        from scanning.slips import expected_visit_slip
        visit = self._visit(category='other', category_other='Catering')
        slip = expected_visit_slip(visit)
        rows = [row for section in slip['sections'] for row in section]
        self.assertIn(['Category', 'Other: Catering'], [row[:2] for row in rows])

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

    def test_restore_brings_it_back(self):
        visit = self._visit()
        self._archive(visit)
        resp = self._archive(visit, archived=False)
        self.assertIsNone(resp.data['archived_at'])
        self.assertEqual(resp.data['archive_reason'], '')

    def test_rescheduling_an_archived_no_show_brings_it_back(self):
        # Visits archive themselves a day after their date, so a no-show is
        # rebooked straight from the Archived tab.
        from accounts.models import AuditLog
        visit = self._visit(expected_date=self.today - timedelta(days=3))
        self._archive(visit, reason='Auto-archived after the visit day')
        resp = self._reschedule(visit, self.today + timedelta(days=1))
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertIsNone(resp.data['archived_at'])
        self.assertEqual(resp.data['outcome'], 'upcoming')
        self.assertTrue(AuditLog.objects.filter(details__contains='restored from archive').exists())
        # An arrived one is history: never rescheduled, archived or not.
        came = self._visit(expected_date=self.today - timedelta(days=3), is_arrived=True,
                           arrived_at=timezone.now())
        self._archive(came)
        self.assertEqual(self._reschedule(came, self.today + timedelta(days=1)).status_code, 400)


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

    def test_paged_response_carries_tab_counts_and_totals(self):
        data = self.client.get('/api/vehicles/scheduled-visits/', {'page': 1, 'page_size': 2}).data
        self.assertEqual(data['count'], 4)                 # every visit not archived
        self.assertEqual(len(data['results']), 2)
        self.assertIsNotNone(data['next'])
        self.assertEqual(data['counts'], {'all': 4, 'today': 1, 'upcoming': 1, 'arrived': 1,
                                          'no_show': 1, 'archived': 1})
        # Tab counts follow the search; the stat totals do not.
        data = self.client.get('/api/vehicles/scheduled-visits/', {'page': 1, 'q': 'guest'}).data
        self.assertEqual(data['counts']['all'], 3)
        self.assertEqual(data['counts']['upcoming'], 0)
        self.assertEqual(data['totals']['upcoming'], 1)

    def test_last_in_first_out(self):
        newest = ScheduledVisit.objects.create(visitor_name='JUST BOOKED', created_by=self.admin,
                                               expected_date=timezone.localdate() + timedelta(days=30))
        rows = self.client.get('/api/vehicles/scheduled-visits/', {'page': 1}).data['results']
        self.assertEqual(rows[0]['id'], newest.pk)          # booked last, listed first
        ids = [r['id'] for r in rows]
        self.assertEqual(ids, sorted(ids, reverse=True))
        page2 = self.client.get('/api/vehicles/scheduled-visits/', {'page': 2, 'page_size': 3}).data
        self.assertEqual([r['id'] for r in page2['results']], ids[3:])

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
        # Archived before its day: Cancelled is the outcome, archiving said after it.
        self.assertEqual(kw['rows'][0][7], 'Cancelled — Archived: Postponed')
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


class ExpectedVisitWaivesRulesTests(TestCase):
    """A plate the CDSO booked for today gets past the day/hour rules. The
    rules here allow no day at all, so the refusal does not depend on the
    clock the suite happens to run at."""
    def setUp(self):
        self.admin = _user('cdso@slc.edu.ph', 'admin')
        self.guard = _user('guard@slc.edu.ph', 'security', gate_assignment='gate1')
        self.client = APIClient()
        self.client.force_authenticate(user=self.guard)
        self.today = timezone.localdate()
        RuleConstraint.objects.update(enabled=False)   # the seeded rules, whatever their days
        for kind in ('supplier', 'student_vehicle'):
            RuleConstraint.objects.create(name=f'Closed {kind}', constraint_type=kind, days=[],
                                          start_time='00:00', end_time='23:59', enabled=True)
        supplier = Supplier.objects.create(company_name='ILOCOS FRESH', category='delivery', is_active=True)
        SupplierPlate.objects.create(supplier=supplier, plate_number='ASD123')

    def _visit(self, plate, **kw):
        return ScheduledVisit.objects.create(visitor_name='JACK BLAK', category='delivery',
                                             expected_date=self.today, plate_number=plate,
                                             created_by=self.admin, **kw)

    def _check(self, plate):
        return self.client.post('/api/scan/manual-entry/', {'plate_number': plate}, format='json').data

    def test_supplier_without_a_visit_is_refused(self):
        resp = self._check('ASD 123')
        self.assertEqual(resp['status'], 'denied')

    def test_supplier_expected_today_is_admitted_and_arrives(self):
        visit = self._visit('ASD123')
        resp = self._check('ASD 123')
        self.assertTrue(resp['allowed'], resp)
        self.assertIn(f'SV-{visit.pk}', resp['message'])
        self.assertIn('waived', resp['message'])
        visit.refresh_from_db()
        self.assertTrue(visit.is_arrived)

    def test_another_days_or_archived_visit_waives_nothing(self):
        ScheduledVisit.objects.create(visitor_name='JACK BLAK', plate_number='ASD123',
                                      expected_date=self.today - timedelta(days=1))
        self._visit('ASD123', archived_at=timezone.now())
        self.assertEqual(self._check('ASD123')['status'], 'denied')

    def test_student_on_a_closed_day_is_admitted_when_expected(self):
        owner = _user('stu@slc.edu.ph', 'vehicle_owner', owner_type='student')
        vehicle = Vehicle.objects.create(plate_number='STU1234', vehicle_type='car',
                                         is_authorized=True, user=owner)
        # Asked of check_entry directly: a refused gate check would also issue
        # a violation, and the confiscation it brings is (rightly) not waived.
        from scanning.entry_logic import check_entry
        self.assertEqual(check_entry(vehicle)['status'], 'wrong_day')
        self._visit('STU1234')
        resp = self._check('STU1234')
        self.assertEqual(resp['status'], 'scheduled_entry', resp)
        self.assertIn('waived', resp['message'])

    def test_a_confiscated_account_is_not_waived(self):
        owner = _user('conf@slc.edu.ph', 'vehicle_owner', owner_type='student')
        Vehicle.objects.create(plate_number='CNF1234', vehicle_type='car', is_authorized=True, user=owner)
        self._visit('CNF1234')
        with patch.object(User, 'is_confiscated', new=True):
            self.assertEqual(self._check('CNF1234')['status'], 'confiscated')


class ScheduledEntryRecordTests(TestCase):
    """A booked vehicle's entry is a Scheduled Entry on the card AND on the
    record: the log row names the booking, the log and the report say so, and
    Expected Today keeps the visit (as inside) until the vehicle leaves."""
    def setUp(self):
        self.admin = _user('cdso@slc.edu.ph', 'admin')
        self.guard = _user('guard@slc.edu.ph', 'security', gate_assignment='gate1')
        self.client = APIClient()
        self.client.force_authenticate(user=self.guard)
        self.today = timezone.localdate()
        supplier = Supplier.objects.create(company_name='JOLIBEE', category='delivery', is_active=True)
        SupplierPlate.objects.create(supplier=supplier, plate_number='ASD123')
        self.visit = ScheduledVisit.objects.create(
            visitor_name='JACK BLAK', category='delivery', supplier=supplier,
            plate_number='ASD123', expected_date=self.today, created_by=self.admin)

    def _check(self, plate='ASD 123'):
        with patch('scanning.views._supplier_rule_denial', return_value=None):
            return self.client.post('/api/scan/manual-entry/', {'plate_number': plate}, format='json').data

    def _feed(self):
        return self.client.get('/api/vehicles/scheduled-visits/today/').data

    def test_card_and_record_say_scheduled_entry(self):
        resp = self._check()
        self.assertEqual(resp['status'], 'scheduled_entry')
        self.assertIn(f'SV-{self.visit.pk}', resp['message'])
        self.assertNotIn('waived', resp['message'])        # within hours: nothing to waive
        entry = AccessLog.objects.get(plate_number='ASD123', status='authorized')
        self.assertEqual(entry.scheduled_visit_id, self.visit.pk)

        row = self.client.get('/api/scan/logs/').data[0]
        self.assertEqual(row['scheduled_visit_ref'], f'SV-{self.visit.pk}')
        self.assertEqual(row['scheduled_visit_name'], 'JACK BLAK')

        from django.test import RequestFactory
        from scanning.views import VEHICLE_LOG_REPORT_HEADERS, _vehicle_log_report_data
        request = RequestFactory().get('/x'); request.user = self.admin
        request.query_params = request.GET
        report, _ = _vehicle_log_report_data(request)
        col = lambda h: report[0][VEHICLE_LOG_REPORT_HEADERS.index(h)]
        self.assertEqual(col('Status'), 'Scheduled Entry')
        self.assertIn(f'SV-{self.visit.pk}', col('Remarks'))

    def test_expected_today_keeps_the_visit_until_it_exits(self):
        self.assertEqual([(v['id'], v['is_arrived']) for v in self._feed()], [(self.visit.pk, False)])
        self._check()
        feed = self._feed()
        self.assertEqual(len(feed), 1)
        self.assertTrue(feed[0]['is_arrived'])
        self.assertTrue(feed[0]['is_inside'])
        entry = AccessLog.objects.get(plate_number='ASD123', status='authorized')
        self.assertEqual(feed[0]['inside_since'], entry.scanned_at.isoformat())   # this stay's entry
        # Drive out: backdate the entry past the re-check window, check again.
        AccessLog.objects.filter(plate_number='ASD123').update(
            scanned_at=timezone.now() - timedelta(minutes=10))
        self.assertEqual(self._check()['status'], 'exited')
        self.assertEqual(self._feed(), [])

    def test_an_ordinary_entry_is_not_scheduled(self):
        resp = self.client.post('/api/scan/manual-entry/', {'plate_number': 'ZZZ9999'}, format='json').data
        self.assertNotEqual(resp.get('status'), 'scheduled_entry')
        self.assertFalse(AccessLog.objects.filter(scheduled_visit__isnull=False).exists())

    def test_visitor_slip_entry_names_its_booking_even_in_another_car(self):
        guest = ScheduledVisit.objects.create(visitor_name='DR. HELEN OCAMPO', category='guest',
                                              expected_date=self.today, created_by=self.admin)
        resp = self.client.post('/api/scan/visitor-pass/', {
            'plate_number': 'HOC2020', 'visitor_name': 'Helen Ocampo', 'allowed_duration': 60,
            'scheduled_visit': guest.pk}, format='json')
        self.client.post(f"/api/scan/visitor-pass/{resp.data['id']}/printed/")
        entry = AccessLog.objects.get(plate_number='HOC2020', status='authorized')
        self.assertEqual(entry.scheduled_visit_id, guest.pk)
        inside = {v['id']: v['is_inside'] for v in self._feed()}
        self.assertTrue(inside[guest.pk])

    def test_old_records_are_linked_by_the_backfill(self):
        import importlib
        from django.apps import apps
        self._check()
        AccessLog.objects.update(scheduled_visit=None)              # as it was before the field
        backfill = importlib.import_module('scanning.migrations.0027_accesslog_scheduled_visit')
        backfill.link_past_entries(apps, None)
        entry = AccessLog.objects.get(plate_number='ASD123', status='authorized')
        self.assertEqual(entry.scheduled_visit_id, self.visit.pk)
        # An archived booking is not linked — the gate never matches one.
        self.visit.archived_at = timezone.now(); self.visit.save()
        AccessLog.objects.update(scheduled_visit=None)
        backfill.link_past_entries(apps, None)
        self.assertIsNone(AccessLog.objects.get(pk=entry.pk).scheduled_visit_id)


class AutoArchiveTests(TestCase):
    """Visits archive themselves the day after their date and keep their outcome."""
    def setUp(self):
        self.admin = _user('cdso@slc.edu.ph', 'admin')
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.today = timezone.localdate()

    def _visit(self, name, days, **kw):
        return ScheduledVisit.objects.create(visitor_name=name, created_by=self.admin,
                                             expected_date=self.today + timedelta(days=days), **kw)

    def test_the_day_after_its_date_a_visit_archives_itself_keeping_its_outcome(self):
        from vehicles.tasks import auto_archive_past_visits
        came    = self._visit('CAME', -2, is_arrived=True, arrived_at=timezone.now() - timedelta(days=2))
        noshow  = self._visit('NOSHOW', -2)
        yesterday = self._visit('YESTERDAY', -1)        # the day after: archived
        today   = self._visit('TODAY', 0)               # its own day: not yet
        self.assertEqual(auto_archive_past_visits(), {'archived': 3})
        for v in (came, noshow, yesterday, today):
            v.refresh_from_db()
        self.assertIsNotNone(came.archived_at)
        self.assertTrue(came.is_arrived)                  # the arrival is kept
        self.assertIsNotNone(came.arrived_at)
        self.assertEqual(came.archive_reason, 'Auto-archived after the visit day')
        self.assertIsNone(came.archived_by)               # nobody did it
        self.assertIsNotNone(noshow.archived_at)
        self.assertIsNotNone(yesterday.archived_at)
        self.assertIsNone(today.archived_at)
        self.assertEqual(auto_archive_past_visits(), {'archived': 0})    # idempotent

        rows = {r['visitor_name']: r for r in
                self.client.get('/api/vehicles/scheduled-visits/', {'status': 'archived'}).data}
        self.assertEqual(rows['CAME']['outcome'], 'arrived')
        self.assertEqual(rows['NOSHOW']['outcome'], 'no_show')
        self.assertEqual(rows['YESTERDAY']['outcome'], 'no_show')
        self.assertEqual(self.client.get('/api/vehicles/scheduled-visits/', {'status': 'no_show'}).data, [])

    def test_the_daily_scheduler_runs_it(self):
        from vehicles.scheduler import DAILY_JOBS
        self.assertIn('auto_archive_past_visits', DAILY_JOBS)

    def test_a_card_for_an_archived_visit_still_says_what_happened(self):
        from vehicles.tasks import auto_archive_past_visits
        came = self._visit('CAME', -2, is_arrived=True, arrived_at=timezone.now())
        noshow = self._visit('NOSHOW', -2)
        cancelled = self._visit('CANCELLED', 3, archived_at=timezone.now(), archive_reason='Called off')
        auto_archive_past_visits()
        guard = _user('guard@slc.edu.ph', 'security', gate_assignment='gate1')
        self.client.force_authenticate(user=guard)
        state = lambda v: self.client.get('/api/scan/slip/', {'code': f'SLC-SCHEDULED:{v.pk}'}).data['state']
        self.assertEqual(state(came), 'arrived')
        self.assertEqual(state(noshow), 'no_show')
        self.assertEqual(state(cancelled), 'archived')

    def test_report_keeps_the_outcome(self):
        from vehicles.tasks import auto_archive_past_visits
        self._visit('CAME', -2, is_arrived=True, arrived_at=timezone.now())
        auto_archive_past_visits()
        with patch('report_utils.branded_pdf_response') as build:
            from django.http import HttpResponse
            build.return_value = HttpResponse(b'%PDF')
            self.client.get('/api/vehicles/scheduled-visits/report/pdf/', {'status': 'archived'})
        self.assertEqual(build.call_args.kwargs['rows'][0][7],
                         'Arrived — Archived: Auto-archived after the visit day')
