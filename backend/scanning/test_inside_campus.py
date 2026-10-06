"""GET /api/scan/inside/ — the Operations Center's "who is on campus now"."""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from scanning.models import AccessLog
from vehicles.models import Vehicle


class InsideCampusTests(TestCase):

    def setUp(self):
        self.admin = User.objects.create_user(
            email='inside-admin@slc.edu.ph', last_name='Admin', first_name='In',
            password='pw', role='admin', is_staff=True)
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def enter(self, plate, category, minutes_ago=1, owner=None, vehicle=None, **extra):
        if owner:
            vehicle = Vehicle.objects.create(plate_number=plate, vehicle_type='car', user=owner,
                                             is_authorized=True)
        log = AccessLog.objects.create(plate_number=plate, status=AccessLog.Status.AUTHORIZED,
                                       gate_id='main', vehicle=vehicle, entrant_category=category, **extra)
        AccessLog.objects.filter(pk=log.pk).update(
            scanned_at=timezone.now() - timedelta(minutes=minutes_ago), entrant_category=category)
        return log

    def test_one_row_per_vehicle_inside_with_chip_counts(self):
        student = User.objects.create_user(email='in-st@slc.edu.ph', last_name='Cruz', first_name='Ana',
                                           password='pw', role='vehicle_owner', owner_type='student')
        self.enter('STU 1001', 'student', owner=student)
        self.enter('EMP 2002', 'employee')
        left = self.enter('LFT 3003', 'visitor', minutes_ago=30)
        AccessLog.objects.create(plate_number='LFT 3003', status=AccessLog.Status.EXITED,
                                 gate_id='main', paired_entry=left)
        self.enter('', 'unknown', is_unrecognized=True, driver_name='JUAN')

        data = self.client.get('/api/scan/inside/').data
        self.assertEqual(data['counts']['all'], 3)
        self.assertEqual(data['counts']['student'], 1)
        self.assertEqual(data['counts']['visitor'], 0)          # exited
        self.assertEqual(data['counts']['unregistered'], 1)
        student_row = next(r for r in data['results'] if r['plate'] == 'STU 1001')
        self.assertEqual(student_row['name'], student.full_name)
        plateless = next(r for r in data['results'] if r['group'] == 'unregistered')
        self.assertTrue(plateless['plate'].startswith('NP-'))

    def test_a_category_narrows_the_rows_not_the_counts(self):
        self.enter('EMP 2002', 'employee')
        self.enter('FET 4004', 'fetcher')
        data = self.client.get('/api/scan/inside/', {'category': 'fetcher'}).data
        self.assertEqual([r['plate'] for r in data['results']], ['FET 4004'])
        self.assertEqual(data['counts']['all'], 2)

    def test_guards_and_admin_only(self):
        guard = User.objects.create_user(email='in-guard@slc.edu.ph', last_name='G', first_name='G',
                                         password='pw', role='security')
        self.client.force_authenticate(guard)
        self.assertEqual(self.client.get('/api/scan/inside/').status_code, 200)
        owner = User.objects.create_user(email='in-owner@slc.edu.ph', last_name='O', first_name='O',
                                         password='pw', role='vehicle_owner', owner_type='student')
        self.client.force_authenticate(owner)
        self.assertEqual(self.client.get('/api/scan/inside/').status_code, 403)

    def test_a_plateless_vehicle_never_takes_a_walk_in_visitors_name(self):
        from scanning.models import VisitorPass
        walk_in = Vehicle.objects.create(plate_number='WALKIN 1', vehicle_type='car')
        VisitorPass.objects.create(vehicle=walk_in, plate_number='', visitor_name='JUAN CRUZ',
                                   status='active', valid_date=timezone.localdate())
        self.enter('', 'unknown', is_unrecognized=True, driver_name='PEDRO')
        [row] = self.client.get('/api/scan/inside/').data['results']
        self.assertEqual(row['name'], 'PEDRO')

    def test_a_visitor_row_carries_their_pass_matched_by_vehicle(self):
        from scanning.models import VisitorPass
        car = Vehicle.objects.create(plate_number='VIS5005', vehicle_type='car')
        pass_ = VisitorPass.objects.create(vehicle=car, plate_number='VIS5005', visitor_name='MARIA SANTOS',
                                           status='active', valid_date=timezone.localdate(),
                                           expires_at=timezone.now() + timedelta(minutes=40))
        # Spelt with a space on the entry: the plate alone would not match.
        log = self.enter('VIS 5005', 'visitor', vehicle=car)
        data = self.client.get('/api/scan/inside/').data
        [row] = data['results']
        self.assertEqual(row['id'], log.pk)
        self.assertEqual(row['name'], 'MARIA SANTOS')
        self.assertEqual(row['pass']['id'], pass_.pk)
        self.assertEqual(row['slip_code'], pass_.qr_payload)
        self.assertEqual(data['passes_not_inside'], [])

    def test_a_walk_in_with_no_plate_is_listed_once(self):
        from scanning.models import VisitorPass
        walk_in = Vehicle.objects.create(plate_number='WALKIN 2', vehicle_type='car')
        pass_ = VisitorPass.objects.create(vehicle=walk_in, plate_number='', visitor_name='JUAN CRUZ',
                                           status='active', valid_date=timezone.localdate())
        self.enter('', 'visitor', vehicle=walk_in)
        data = self.client.get('/api/scan/inside/').data
        [row] = data['results']
        self.assertEqual(row['pass']['id'], pass_.pk)
        self.assertEqual(data['passes_not_inside'], [])
        self.assertEqual(data['counts']['all'], 1)

    def test_a_pass_with_no_entry_is_listed_but_not_counted(self):
        from scanning.models import VisitorPass
        car = Vehicle.objects.create(plate_number='NOPRINT1', vehicle_type='car')
        pass_ = VisitorPass.objects.create(vehicle=car, plate_number='NOPRINT1', status='active',
                                           valid_date=timezone.localdate())   # slip never printed
        VisitorPass.objects.create(vehicle=car, plate_number='NOPRINT1', status='exited',
                                   valid_date=timezone.localdate())
        data = self.client.get('/api/scan/inside/').data
        self.assertEqual(data['counts']['all'], 0)
        self.assertEqual([p['id'] for p in data['passes_not_inside']], [pass_.pk])

    def test_an_owner_row_has_no_pass_and_a_no_plate_row_has_its_slip(self):
        student = User.objects.create_user(email='in-st2@slc.edu.ph', last_name='Reyes', first_name='Ben',
                                           password='pw', role='vehicle_owner', owner_type='student')
        self.enter('STU 1002', 'student', owner=student)
        noplate = self.enter('', 'unknown', is_unrecognized=True, driver_name='PEDRO')
        rows = {r['plate']: r for r in self.client.get('/api/scan/inside/').data['results']}
        self.assertIsNone(rows['STU 1002']['pass'])
        self.assertEqual(rows['STU 1002']['slip_code'], '')
        self.assertEqual(rows[f'NP-{noplate.pk}']['slip_code'], f'SLC-NOPLATE:{noplate.pk}')
