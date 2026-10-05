"""GET /api/accounts/users/<pk>/activity/ — one owner's schedule, violations
and gate visits, as User Management's View Profile and Activity window show
them."""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from scanning.models import AccessLog
from vehicles.models import RuleConstraint, Vehicle
from violations.models import Violation


class UserActivityTests(TestCase):

    def setUp(self):
        self.admin = User.objects.create_user(
            email='act-admin@slc.edu.ph', last_name='Admin', first_name='Act',
            password='pw', role='admin', is_staff=True)
        self.owner = User.objects.create_user(
            email='act-owner@slc.edu.ph', last_name='Owner', first_name='Act', password='pw',
            role='vehicle_owner', owner_type='student', campus_days=['Monday', 'Wednesday'])
        self.vehicle = Vehicle.objects.create(plate_number='ACT 1234', vehicle_type='car',
                                              user=self.owner, is_authorized=True)
        RuleConstraint.objects.filter(constraint_type='student_vehicle').delete()
        RuleConstraint.objects.create(name='Students', constraint_type='student_vehicle',
                                      days=['mon', 'tue', 'wed', 'thu', 'fri'],
                                      start_time='06:00', end_time='19:00', max_stay_minutes=600)
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def get(self, **params):
        return self.client.get(f'/api/accounts/users/{self.owner.pk}/activity/', params)

    def test_schedule_is_the_owners_days_within_the_rule(self):
        res = self.get()
        self.assertEqual(res.status_code, 200, res.data)
        schedule = res.data['schedule']
        self.assertEqual(schedule['days'], ['Monday', 'Wednesday'])
        self.assertEqual((schedule['start_time'], schedule['end_time']), ('06:00', '19:00'))
        self.assertEqual(schedule['max_stay_minutes'], 600)
        self.assertEqual([v['plate'] for v in res.data['vehicles']], ['ACT 1234'])

    def test_violations_with_their_overstay_and_state(self):
        Violation.objects.create(vehicle=self.vehicle, owner=self.owner, violation_type='time_exceed',
                                 plate_number='ACT 1234', status='warning', offense_number=1,
                                 overstay_minutes=85)
        Violation.objects.create(vehicle=self.vehicle, owner=self.owner, violation_type='double_parking',
                                 plate_number='ACT 1234', status='cleared', offense_number=1)
        data = self.get().data
        self.assertEqual(data['counts']['violations'], 2)
        self.assertEqual(data['counts']['violations_active'], 1)
        labels = {v['label'] for v in data['violations']}
        self.assertIn('Overstaying (1 hr 25 min)', labels)

    def test_visits_are_entry_and_exit_merged(self):
        now = timezone.now()
        entry = AccessLog.objects.create(vehicle=self.vehicle, plate_number='ACT 1234',
                                         status=AccessLog.Status.AUTHORIZED, gate_id='main')
        AccessLog.objects.filter(pk=entry.pk).update(scanned_at=now - timedelta(hours=2))
        exit_log = AccessLog.objects.create(vehicle=self.vehicle, plate_number='ACT 1234',
                                            status=AccessLog.Status.EXITED, gate_id='main',
                                            paired_entry=entry)
        AccessLog.objects.filter(pk=exit_log.pk).update(scanned_at=now - timedelta(minutes=30))
        visits = self.get().data['visits']
        self.assertEqual(len(visits), 1)
        self.assertEqual(visits[0]['duration_minutes'], 90)
        self.assertFalse(visits[0]['still_inside'])

    def test_an_old_entry_without_an_exit_is_not_still_inside(self):
        entry = AccessLog.objects.create(vehicle=self.vehicle, plate_number='ACT 1234',
                                         status=AccessLog.Status.AUTHORIZED, gate_id='main')
        AccessLog.objects.filter(pk=entry.pk).update(scanned_at=timezone.now() - timedelta(days=3))
        [visit] = self.get().data['visits']
        self.assertFalse(visit['still_inside'])
        self.assertTrue(visit['no_exit'])

    def test_a_date_range_narrows_the_visits(self):
        entry = AccessLog.objects.create(vehicle=self.vehicle, plate_number='ACT 1234',
                                         status=AccessLog.Status.AUTHORIZED, gate_id='main')
        AccessLog.objects.filter(pk=entry.pk).update(scanned_at=timezone.now() - timedelta(days=10))
        today = timezone.localdate().isoformat()
        self.assertEqual(self.get(date_from=today).data['visits'], [])
        self.assertEqual(len(self.get().data['visits']), 1)

    def test_admin_only(self):
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.get().status_code, 403)
