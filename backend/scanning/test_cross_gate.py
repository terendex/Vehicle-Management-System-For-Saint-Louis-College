"""Cross-gate records: a vehicle in at one gate and out at another is listed in
the Operations Center's Gate Records, as a paged table across every day."""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from scanning.models import AccessLog


class CrossGateRecordTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(email='xg-cdso@slc.edu.ph', full_name='CDSO ONE',
                                              password='x', role='admin')
        self.guard = User.objects.create_user(email='xg-guard@slc.edu.ph', full_name='GUARD ONE',
                                              password='x', role='security', gate_assignment='gate4')
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def _visit(self, plate, entry_gate, exit_gate, days_ago=0):
        entry = AccessLog.objects.create(plate_number=plate, status='authorized', gate_id=entry_gate)
        ex = AccessLog.objects.create(plate_number=plate, status='exited', gate_id=exit_gate,
                                      paired_entry=entry)
        when = timezone.now() - timedelta(days=days_ago)
        AccessLog.objects.filter(pk=entry.pk).update(scanned_at=when - timedelta(minutes=30))
        AccessLog.objects.filter(pk=ex.pk).update(scanned_at=when)
        return ex

    def _list(self, **params):
        return self.client.get('/api/scan/cross-gate/', {'page': 1, 'page_size': 10, **params}).data

    def test_only_cross_gate_visits_are_listed_across_days(self):
        self._visit('TYT123', 'gate4', 'gate1')
        self._visit('ASB123', 'gate4', 'gate1', days_ago=3)     # earlier days stay listed
        self._visit('SAME111', 'gate1', 'gate1')                 # same gate: not cross-gate
        data = self._list()
        self.assertEqual(data['count'], 2)
        self.assertEqual([r['plate_number'] for r in data['results']], ['TYT123', 'ASB123'])   # newest first
        row = data['results'][0]
        self.assertEqual((row['entry_gate'], row['exit_gate'], row['duration_minutes']), ('gate4', 'gate1', 30))
        self.assertNotIn('reviewed_at', row)                     # a record, nothing to review

    def test_the_stat_counts_today_only(self):
        self._visit('TYT123', 'gate4', 'gate1')
        self._visit('ASB123', 'gate4', 'gate1', days_ago=3)
        monitor = self.client.get('/api/scan/guard-monitor/').data
        self.assertEqual(monitor['cross_gate_today'], 1)
        self.assertNotIn('cross_gate_open', monitor)

    def test_pages(self):
        for i in range(12):
            self._visit(f'PG{i:04d}', 'gate4', 'gate1')
        first = self._list()
        self.assertEqual((first['count'], len(first['results'])), (12, 10))
        self.assertEqual(len(self._list(page=2)['results']), 2)

    def test_no_review_endpoints(self):
        ex = self._visit('TYT123', 'gate4', 'gate1')
        self.assertEqual(self.client.post(f'/api/scan/cross-gate/{ex.pk}/review/').status_code, 404)
        self.assertEqual(self.client.post('/api/scan/cross-gate/review-all/').status_code, 404)

    def test_cdso_only(self):
        c = APIClient(); c.force_authenticate(self.guard)
        self.assertEqual(c.get('/api/scan/cross-gate/').status_code, 403)
