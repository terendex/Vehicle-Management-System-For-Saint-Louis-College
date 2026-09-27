"""Cross-gate records: a vehicle in at one gate and out at another is flagged in
the Operations Center, listed in a paged table, and cleared by review."""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import AuditLog, User
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

    def test_only_cross_gate_visits_are_flagged_across_days(self):
        self._visit('TYT123', 'gate4', 'gate1')
        self._visit('ASB123', 'gate4', 'gate1', days_ago=3)     # earlier days stay on record
        self._visit('SAME111', 'gate1', 'gate1')                 # same gate: not a flag
        data = self._list(status='all')
        self.assertEqual([r['plate_number'] for r in data['results']], ['TYT123', 'ASB123'])   # newest first
        row = data['results'][0]
        self.assertEqual((row['entry_gate'], row['exit_gate'], row['duration_minutes']), ('gate4', 'gate1', 30))
        self.assertEqual(data['counts'], {'all': 2, 'open': 2, 'reviewed': 0})

    def test_reviewing_clears_the_open_count_and_keeps_who_and_when(self):
        ex = self._visit('TYT123', 'gate4', 'gate1')
        self._visit('ASB123', 'gate4', 'gate1')
        monitor = self.client.get('/api/scan/guard-monitor/').data
        self.assertEqual(monitor['cross_gate_open'], 2)

        resp = self.client.post(f'/api/scan/cross-gate/{ex.pk}/review/', {}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['reviewed_by_name'], 'CDSO ONE')
        self.assertIsNotNone(resp.data['reviewed_at'])
        self.assertEqual(self._list()['counts'], {'all': 2, 'open': 1, 'reviewed': 1})
        self.assertEqual([r['plate_number'] for r in self._list(status='reviewed')['results']], ['TYT123'])
        self.assertTrue(AuditLog.objects.filter(details__contains='Cross-gate flag reviewed | Plate: TYT123').exists())

        # Reopen, then clear everything at once.
        self.client.post(f'/api/scan/cross-gate/{ex.pk}/review/', {'reviewed': False}, format='json')
        self.assertEqual(self._list()['counts']['open'], 2)
        self.assertEqual(self.client.post('/api/scan/cross-gate/review-all/', {}, format='json').data,
                         {'reviewed': 2})
        self.assertEqual(self.client.get('/api/scan/guard-monitor/').data['cross_gate_open'], 0)

    def test_pages(self):
        for i in range(12):
            self._visit(f'PG{i:04d}', 'gate4', 'gate1')
        first = self._list(status='all')
        self.assertEqual((first['count'], len(first['results'])), (12, 10))
        self.assertEqual(len(self._list(status='all', page=2)['results']), 2)

    def test_review_all_respects_the_date_filter(self):
        self._visit('OLD1111', 'gate4', 'gate1', days_ago=5)
        self._visit('NEW1111', 'gate4', 'gate1')
        today = timezone.localdate().isoformat()
        n = self.client.post('/api/scan/cross-gate/review-all/',
                             {'date_from': today, 'date_to': today}, format='json').data['reviewed']
        self.assertEqual(n, 1)
        self.assertEqual([r['plate_number'] for r in self._list()['results']], ['OLD1111'])

    def test_cdso_only(self):
        ex = self._visit('TYT123', 'gate4', 'gate1')
        c = APIClient(); c.force_authenticate(self.guard)
        self.assertEqual(c.get('/api/scan/cross-gate/').status_code, 403)
        self.assertEqual(c.post(f'/api/scan/cross-gate/{ex.pk}/review/').status_code, 403)
        self.assertEqual(c.post('/api/scan/cross-gate/review-all/').status_code, 403)
