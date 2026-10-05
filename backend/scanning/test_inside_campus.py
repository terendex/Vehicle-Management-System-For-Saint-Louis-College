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

    def enter(self, plate, category, minutes_ago=1, owner=None, **extra):
        vehicle = None
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

    def test_admin_only(self):
        guard = User.objects.create_user(email='in-guard@slc.edu.ph', last_name='G', first_name='G',
                                         password='pw', role='security')
        self.client.force_authenticate(guard)
        self.assertEqual(self.client.get('/api/scan/inside/').status_code, 403)

    def test_a_plateless_vehicle_never_takes_a_walk_in_visitors_name(self):
        from scanning.models import VisitorPass
        walk_in = Vehicle.objects.create(plate_number='WALKIN 1', vehicle_type='car')
        VisitorPass.objects.create(vehicle=walk_in, plate_number='', visitor_name='JUAN CRUZ',
                                   status='active', valid_date=timezone.localdate())
        self.enter('', 'unknown', is_unrecognized=True, driver_name='PEDRO')
        [row] = self.client.get('/api/scan/inside/').data['results']
        self.assertEqual(row['name'], 'PEDRO')
