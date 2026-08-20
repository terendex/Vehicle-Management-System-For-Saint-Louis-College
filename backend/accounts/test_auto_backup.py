"""Automatic backups: the schedule, the rotation, and the saved-file endpoints.

Every test redirects BASE_DIR at a temp directory, so none of this touches the
real `backend/backups` folder — the one holding the pre-restore snapshots that
are the last resort after a bad restore.
"""
import json
import os
import shutil
import tempfile

from django.test import TestCase, override_settings
from django.utils import timezone as tz
from rest_framework.test import APIClient

from accounts.models import User
from accounts import backup_utils
from vehicles.models import SystemSettings


class BackupTempDirMixin:
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix='slc-backup-test-')
        patcher = override_settings(BASE_DIR=self.tmp)
        patcher.enable()
        self.addCleanup(patcher.disable)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def touch(self, name, body='[]'):
        path = os.path.join(backup_utils.backup_dir(), name)
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(body)
        return path


class BackupUtilsTests(BackupTempDirMixin, TestCase):
    def test_kind_comes_from_the_filename_prefix(self):
        self.assertEqual(backup_utils.kind_of('auto-backup-20260101-000000.json'), 'auto')
        self.assertEqual(backup_utils.kind_of('manual-backup-20260101-000000.json'), 'manual')
        self.assertEqual(backup_utils.kind_of('pre-restore-20260101-000000.json'), 'safety')
        self.assertEqual(backup_utils.kind_of('whatever.json'), 'other')

    def test_taken_at_reads_the_stamp_in_the_name(self):
        """Not the mtime — copying a file must not change when its data is from."""
        path = self.touch('auto-backup-20260214-093000.json')
        taken = backup_utils.taken_at(os.path.basename(path), path)
        self.assertEqual((taken.year, taken.month, taken.day), (2026, 2, 14))
        self.assertEqual((taken.hour, taken.minute), (9, 30))

    def test_listing_is_newest_first(self):
        self.touch('auto-backup-20260101-000000.json')
        self.touch('auto-backup-20260301-000000.json')
        self.touch('auto-backup-20260201-000000.json')
        names = [item['name'] for item in backup_utils.list_backups()]
        self.assertEqual(names, [
            'auto-backup-20260301-000000.json',
            'auto-backup-20260201-000000.json',
            'auto-backup-20260101-000000.json',
        ])

    def test_pruning_keeps_the_newest_and_spares_safety_snapshots(self):
        for day in range(1, 6):
            self.touch(f'auto-backup-2026010{day}-000000.json')
        for day in range(1, 4):
            self.touch(f'pre-restore-2026010{day}-000000.json')
        self.touch('manual-backup-20260101-000000.json')
        self.touch('manual-backup-20260102-000000.json')

        removed = backup_utils.prune_backups(2)

        kinds = {}
        for item in backup_utils.list_backups():
            kinds.setdefault(item['kind'], []).append(item['name'])
        self.assertEqual(len(kinds['auto']), 2)
        self.assertEqual(len(kinds['manual']), 2)      # already within the limit
        self.assertEqual(len(kinds['safety']), 3)      # never rotated away
        self.assertEqual(sorted(kinds['auto']), [
            'auto-backup-20260104-000000.json',
            'auto-backup-20260105-000000.json',
        ])
        self.assertEqual(len(removed), 3)

    def test_a_keep_count_of_zero_still_leaves_one_backup(self):
        """A bad value must not be read as "delete everything"."""
        self.touch('auto-backup-20260101-000000.json')
        self.touch('auto-backup-20260102-000000.json')
        backup_utils.prune_backups(0)
        self.assertEqual(len(backup_utils.list_backups()), 1)

    def test_safe_path_refuses_anything_outside_the_backups_folder(self):
        for name in ('../manage.py', '..\\manage.py', '/etc/passwd', 'sub/dir.json',
                     'missing.json', '', 'notjson.txt'):
            with self.subTest(name=name):
                self.assertIsNone(backup_utils.safe_path(name))

    def test_safe_path_accepts_a_real_file(self):
        self.touch('auto-backup-20260101-000000.json')
        self.assertIsNotNone(backup_utils.safe_path('auto-backup-20260101-000000.json'))


class AutoBackupTaskTests(BackupTempDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.cfg = SystemSettings.get()

    def run_task(self):
        from vehicles.tasks import auto_backup
        return auto_backup()

    def test_off_writes_nothing(self):
        self.cfg.auto_backup_frequency = 'off'
        self.cfg.save()
        result = self.run_task()
        self.assertIn('skipped', result)
        self.assertEqual(backup_utils.list_backups(), [])

    def test_daily_writes_a_loadable_fixture(self):
        self.cfg.auto_backup_frequency = 'daily'
        self.cfg.save()
        result = self.run_task()

        self.assertIn('created', result)
        items = backup_utils.list_backups()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['kind'], 'auto')
        with open(os.path.join(backup_utils.backup_dir(), items[0]['name']), encoding='utf-8') as fh:
            self.assertIsInstance(json.load(fh), list)

    def test_a_second_run_the_same_day_is_skipped(self):
        """The scheduler calls this daily; the frequency is applied here."""
        self.cfg.auto_backup_frequency = 'daily'
        self.cfg.save()
        self.run_task()
        result = self.run_task()
        self.assertEqual(result.get('skipped'), 'not due')
        self.assertEqual(len(backup_utils.list_backups()), 1)

    def test_weekly_waits_a_week(self):
        self.cfg.auto_backup_frequency = 'weekly'
        self.cfg.save()
        yesterday = tz.localtime() - tz.timedelta(days=1)
        self.touch(f'auto-backup-{yesterday.strftime("%Y%m%d-%H%M%S")}.json')

        self.assertEqual(self.run_task().get('skipped'), 'not due')

        # Eight days back is past the window, so the next call writes one.
        os.remove(os.path.join(backup_utils.backup_dir(),
                               backup_utils.list_backups()[0]['name']))
        old = tz.localtime() - tz.timedelta(days=8)
        self.touch(f'auto-backup-{old.strftime("%Y%m%d-%H%M%S")}.json')
        self.assertIn('created', self.run_task())

    def test_old_backups_are_rotated_away(self):
        self.cfg.auto_backup_frequency = 'daily'
        self.cfg.auto_backup_keep = 2
        self.cfg.save()
        for day in range(1, 5):
            self.touch(f'auto-backup-2026010{day}-000000.json')

        self.run_task()

        autos = [i for i in backup_utils.list_backups() if i['kind'] == 'auto']
        self.assertEqual(len(autos), 2)

    def test_hourly_writes_again_an_hour_later(self):
        self.cfg.auto_backup_frequency = 'hourly'
        self.cfg.save()
        recent = tz.localtime() - tz.timedelta(minutes=20)
        self.touch(f'auto-backup-{recent.strftime("%Y%m%d-%H%M%S")}.json')

        self.assertEqual(self.run_task().get('skipped'), 'not due')

        # `latest` is the newest file, so the recent one has to go before an
        # older one can stand in for "the last backup was an hour ago".
        os.remove(os.path.join(backup_utils.backup_dir(),
                               backup_utils.list_backups()[0]['name']))
        an_hour_ago = tz.localtime() - tz.timedelta(hours=1)
        self.touch(f'auto-backup-{an_hour_ago.strftime("%Y%m%d-%H%M%S")}.json')
        self.assertIn('created', self.run_task())

    def test_hourly_tolerates_a_scheduler_wake_that_lands_early(self):
        """The scheduler wakes about every hour, not exactly. A pass at 59
        minutes must still count as the next hour, or an hourly schedule quietly
        becomes a two-hourly one."""
        self.cfg.auto_backup_frequency = 'hourly'
        self.cfg.save()
        almost = tz.localtime() - tz.timedelta(minutes=59)
        self.touch(f'auto-backup-{almost.strftime("%Y%m%d-%H%M%S")}.json')

        self.assertIn('created', self.run_task())

    def test_hourly_claims_the_hour_not_the_day(self):
        """The daily ledger would otherwise cap hourly at one backup a day."""
        from vehicles.scheduler import _claim_key

        self.cfg.auto_backup_frequency = 'hourly'
        self.cfg.save()
        key = _claim_key('auto_backup')
        self.assertNotEqual(key, 'auto_backup')
        self.assertTrue(key.startswith('auto_backup:h'))

        # Every other frequency keeps the plain daily key, so the ledger only
        # grows when hourly is actually in use.
        for freq in ('off', 'daily', 'weekly', 'monthly'):
            with self.subTest(freq=freq):
                self.cfg.auto_backup_frequency = freq
                self.cfg.save()
                self.assertEqual(_claim_key('auto_backup'), 'auto_backup')

    def test_other_jobs_are_never_hour_keyed(self):
        from vehicles.scheduler import _claim_key

        self.cfg.auto_backup_frequency = 'hourly'
        self.cfg.save()
        self.assertEqual(_claim_key('purge_old_records'), 'purge_old_records')
        self.assertEqual(_claim_key('auto_archive_expired_accounts'),
                         'auto_archive_expired_accounts')

    def test_the_scheduler_runs_it_before_anything_that_deletes(self):
        """A snapshot taken after the purge would already be missing the rows
        someone might need to get back."""
        from vehicles.scheduler import DAILY_JOBS

        self.assertIn('auto_backup', DAILY_JOBS)
        self.assertLess(DAILY_JOBS.index('auto_backup'), DAILY_JOBS.index('purge_old_records'))


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class SavedBackupEndpointTests(BackupTempDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user(
            email='auto-backup-admin@slc.edu.ph', full_name='AUTO BACKUP ADMIN',
            password='SecurePassword123!', role='admin')
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def test_listing_reports_each_file_with_its_origin(self):
        self.touch('auto-backup-20260101-000000.json')
        self.touch('pre-restore-20260102-000000.json')

        resp = self.client.get('/api/accounts/system/backups/')
        self.assertEqual(resp.status_code, 200)
        kinds = {item['name']: item['kind'] for item in resp.json()['backups']}
        self.assertEqual(kinds['auto-backup-20260101-000000.json'], 'auto')
        self.assertEqual(kinds['pre-restore-20260102-000000.json'], 'safety')
        self.assertEqual(resp.json()['auto_backup_frequency'], SystemSettings.get().auto_backup_frequency)

    def test_listing_is_admin_only(self):
        guard = User.objects.create_user(
            email='auto-backup-guard@slc.edu.ph', full_name='AUTO BACKUP GUARD',
            password='SecurePassword123!', role='security')
        client = APIClient()
        client.force_authenticate(guard)
        self.assertEqual(client.get('/api/accounts/system/backups/').status_code, 403)

    def test_a_saved_backup_can_be_downloaded(self):
        self.touch('auto-backup-20260101-000000.json', '[{"model": "x.y", "pk": 1, "fields": {}}]')
        resp = self.client.get('/api/accounts/system/backups/auto-backup-20260101-000000.json/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('attachment;', resp['Content-Disposition'])
        self.assertEqual(b''.join(resp.streaming_content).decode(),
                         '[{"model": "x.y", "pk": 1, "fields": {}}]')

    def test_a_saved_backup_can_be_deleted(self):
        self.touch('auto-backup-20260101-000000.json')
        resp = self.client.delete('/api/accounts/system/backups/auto-backup-20260101-000000.json/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(backup_utils.list_backups(), [])

    def test_a_missing_file_is_a_404_not_a_crash(self):
        resp = self.client.get('/api/accounts/system/backups/nope.json/')
        self.assertEqual(resp.status_code, 404)

    def test_restore_accepts_a_saved_filename(self):
        """The same endpoint as an upload, so the same safety snapshot is taken."""
        fixture = json.dumps([{
            'model': 'accounts.user',
            'pk': self.admin.pk,
            'fields': {
                'email': self.admin.email,
                'full_name': 'RESTORED FROM SAVED FILE',
                'password': self.admin.password,
                'role': 'admin',
                'is_active': True,
                'is_staff': self.admin.is_staff,
                'is_superuser': self.admin.is_superuser,
                'date_joined': self.admin.date_joined.isoformat(),
            },
        }])
        self.touch('auto-backup-20260101-000000.json', fixture)

        resp = self.client.post('/api/accounts/system/restore/',
                                {'filename': 'auto-backup-20260101-000000.json'})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()['restored'], 1)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.full_name, 'RESTORED FROM SAVED FILE')

        # And the pre-restore snapshot of what was there before now exists.
        self.assertTrue(any(item['kind'] == 'safety' for item in backup_utils.list_backups()))

    def test_restore_will_not_read_a_file_outside_the_backups_folder(self):
        resp = self.client.post('/api/accounts/system/restore/', {'filename': '../manage.py'})
        self.assertEqual(resp.status_code, 404)

    def test_restore_still_needs_something_to_restore_from(self):
        resp = self.client.post('/api/accounts/system/restore/', {})
        self.assertEqual(resp.status_code, 400)
