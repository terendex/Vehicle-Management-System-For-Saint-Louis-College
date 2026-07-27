"""Smoke-test the report, backup and restore endpoints.

Nothing else in the suite calls these views, so a missing import in one of
them stays invisible until an operator clicks the button — which is exactly
how a dropped `reportlab` dependency, an unimported `settings` and an
unimported `Q` all survived a branch merge. Each test drives the view
through the real DRF stack so an import error fails here instead of in
production.
"""
import json

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

User = get_user_model()


class RestoredEndpointSmokeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            email='smoke-admin@slc.edu.ph', full_name='SMOKE ADMIN',
            password='Passw0rd!23', role='admin',
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def _get(self, url):
        response = self.client.get(url)
        self.assertIn(
            response.status_code, (200, 204),
            f'{url} -> {response.status_code} '
            f'{getattr(response, "data", b"")!r}',
        )
        return response

    # ── Reports (reportlab / openpyxl paths) ────────────────────────────
    def test_audit_log_excel_export(self):
        self._get('/api/accounts/audit-logs/export/')

    def test_audit_log_pdf_export(self):
        self._get('/api/accounts/audit-logs/export-pdf/')

    def test_violation_report_excel(self):
        self._get('/api/violations/report/excel/')

    def test_violation_report_pdf(self):
        self._get('/api/violations/report/pdf/')

    def test_registration_report_excel(self):
        self._get('/api/vehicles/registrations/report/excel/')

    def test_registration_report_pdf(self):
        self._get('/api/vehicles/registrations/report/pdf/')

    # ── Reports with the filters the UI actually sends ──────────────────
    def test_reports_accept_ui_filters(self):
        params = '?date_from=2026-01-01&date_to=2026-12-31&search=ABC'
        self._get(f'/api/accounts/audit-logs/export-pdf/{params}')
        self._get(f'/api/violations/report/pdf/{params}&status=warning')
        self._get(f'/api/vehicles/registrations/report/pdf/{params}&status=pending')

    # ── Backup (settings.BASE_DIR / dumpdata path) ──────────────────────
    def test_system_backup_download(self):
        response = self._get('/api/accounts/system/backup/')
        self.assertTrue(response['Content-Disposition'].startswith('attachment'))

    def test_system_restore_round_trip(self):
        """Restore writes its pre-restore snapshot to settings.BASE_DIR, so
        this is the path that breaks when the module-level import is lost."""
        fixture = self.client.get('/api/accounts/system/backup/').content
        upload = SimpleUploadedFile('backup.json', fixture, 'application/json')
        response = self.client.post(
            '/api/accounts/system/restore/', {'file': upload}, format='multipart',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn('restored', response.data)
        self.assertIn('safety_backup', response.data)

    def test_system_restore_rejects_non_fixture(self):
        upload = SimpleUploadedFile(
            'notes.json', json.dumps({'nope': True}).encode(), 'application/json',
        )
        response = self.client.post(
            '/api/accounts/system/restore/', {'file': upload}, format='multipart',
        )
        self.assertEqual(response.status_code, 400, response.data)

    # ── Joshua's new features ───────────────────────────────────────────
    def test_scheduled_visits_list(self):
        self._get('/api/vehicles/scheduled-visits/')

    def test_dashboard_stats(self):
        response = self._get('/api/accounts/dashboard/stats/')
        # vehicles_by_type was hand-merged back into the aggregate block
        self.assertIn('by_type', response.data['vehicles'])

    def test_audit_log_list_folds_exits(self):
        self._get('/api/accounts/audit-logs/')

    def test_access_log_list(self):
        self._get('/api/scan/logs/')

    def test_gates_list(self):
        self._get('/api/scan/gates/')

    def test_system_settings_exposes_pass_fees(self):
        response = self._get('/api/vehicles/system-settings/')
        self.assertIn('vehicle_pass_fee', response.data)
        self.assertIn('vehicle_pass_fee_employee', response.data)
