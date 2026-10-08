"""Automatic backups: the calendar schedule, the rotation, and the saved-file endpoints.

Every test redirects BASE_DIR at a temp directory, so none of this touches the
real `backend/backups` folder — the one holding the pre-restore snapshots that
are the last resort after a bad restore.
"""
import datetime
import json
import os
import shutil
import socket
import tempfile
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone as tz
from rest_framework.test import APIClient

from accounts.models import Notification, User
from accounts import backup_utils
from scanning.models import Gate
from vehicles.models import ParkingSpace, SystemSettings


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


class RetiredIntervalBackupTests(BackupTempDirMixin, TestCase):
    """The old "every N since the last" schedule was folded into the calendar
    one. Its job must not come back, and its old files are still usable."""

    def test_the_interval_job_is_gone_from_the_scheduler(self):
        from vehicles import tasks
        from vehicles.scheduler import DAILY_JOBS

        self.assertNotIn('auto_backup', DAILY_JOBS)
        self.assertFalse(hasattr(tasks, 'auto_backup'))

    def test_its_old_files_are_still_listed_and_rotated_with_manual_copies(self):
        for day in range(1, 5):
            self.touch(f'auto-backup-2026010{day}-000000.json')
        self.assertEqual({i['kind'] for i in backup_utils.list_backups()}, {'auto'})
        backup_utils.prune_backups(2)
        self.assertEqual(len(backup_utils.list_backups()), 2)

    def test_the_migration_moves_both_old_schedules_onto_the_new_one(self):
        import importlib
        from django.apps import apps
        from django.db import connection
        combine = importlib.import_module('vehicles.migrations.0101_combine_automatic_backups').combine_schedules

        # 'daily' is no longer a valid choice, so its CHECK constraint has to
        # go for the test to plant one (rolled back with the test).
        SystemSettings.get()
        table = SystemSettings._meta.db_table
        with connection.cursor() as cur:
            cur.execute('SET CONSTRAINTS ALL IMMEDIATE')
            cur.execute(f'ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {table}_scheduled_backup_frequency_valid')

        for auto, scheduled, expected in (
            ('hourly',  'daily',   'weekly'),     # what the live server had
            ('hourly',  'off',     'weekly'),
            ('daily',   'off',     'weekly'),
            ('monthly', 'off',     'monthly'),
            ('off',     'off',     'off'),
            ('weekly',  'monthly', 'monthly'),    # the calendar schedule wins when both were on
        ):
            with self.subTest(auto=auto, scheduled=scheduled):
                SystemSettings.objects.update(auto_backup_frequency=auto,
                                              scheduled_backup_frequency=scheduled)
                combine(apps, None)
                cfg = SystemSettings.objects.get()
                self.assertEqual(cfg.scheduled_backup_frequency, expected)
                self.assertEqual(cfg.auto_backup_frequency, 'off')


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class SavedBackupEndpointTests(BackupTempDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user(
            email='auto-backup-admin@slc.edu.ph', last_name='ADMIN', first_name='AUTO', middle_initial='B',
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
        self.assertNotIn('auto_backup_frequency', resp.json())

    def test_listing_is_admin_only(self):
        guard = User.objects.create_user(
            email='auto-backup-guard@slc.edu.ph', last_name='GUARD', first_name='AUTO', middle_initial='B',
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
                'last_name': 'RESTORED', 'first_name': 'SAVED FILE', 'middle_initial': '',
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
        self.assertEqual(self.admin.full_name, 'RESTORED, SAVED FILE')

        # And the pre-restore snapshot of what was there before now exists.
        self.assertTrue(any(item['kind'] == 'safety' for item in backup_utils.list_backups()))

    def test_restore_will_not_read_a_file_outside_the_backups_folder(self):
        resp = self.client.post('/api/accounts/system/restore/', {'filename': '../manage.py'})
        self.assertEqual(resp.status_code, 404)

    def test_restore_still_needs_something_to_restore_from(self):
        resp = self.client.post('/api/accounts/system/restore/', {})
        self.assertEqual(resp.status_code, 400)


class RestoreLoaderTests(BackupTempDirMixin, TestCase):
    """The bulk loader that replaced `loaddata` on the restore endpoint.

    `loaddata` wrote a fixture one row at a time, which against the production
    database in Singapore meant one ~40 ms round trip per record and a restore
    that took minutes. `backup_utils.load_backup` upserts a model at a time
    instead. These cover the guarantees loaddata used to provide for free and
    the loader now has to carry itself.
    """

    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user(
            email='restore-admin@slc.edu.ph', last_name='ADMIN', first_name='RESTORE',
            password='SecurePassword123!', role='admin')
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def restore(self, fixture):
        self.touch('auto-backup-20260101-000000.json', json.dumps(fixture))
        return self.client.post('/api/accounts/system/restore/',
                                {'filename': 'auto-backup-20260101-000000.json'})

    @staticmethod
    def gate_row(pk, gate_id, label, created_at=None):
        """One row shaped the way `dumpdata` writes it — every concrete column
        present, `created_at` included. That matters: the load inserts raw, so
        an `auto_now_add` column is no longer quietly filled in with the time of
        the restore, and a fixture that omits it fails on the NOT NULL. That is
        the same thing `loaddata` did, and a real backup always carries it."""
        stamp = created_at or tz.make_aware(datetime.datetime(2026, 1, 1, 8, 0, 0))
        return {'model': 'scanning.gate', 'pk': pk,
                'fields': {'gate_id': gate_id, 'label': label, 'is_active': True,
                           'created_at': stamp.isoformat()}}

    def test_a_row_holding_a_needed_unique_value_is_archived_not_deleted(self):
        """The case a restore onto a fresh install hits every single time.

        A new install seeds cdso.slc.sflu@gmail.com at pk=1; a backup taken
        from a running system carries the same address at a different pk. The upsert
        says ON CONFLICT (pk), so it does not see a collision on the separate
        partial index over email - Postgres raised, the transaction rolled
        back, and the restore failed whole. Rebuilding onto a fresh install is
        the most important thing a restore is for, and it was the one case
        that could never work.

        The live row is displaced, not wrong, so it is archived: the index is
        partial (unique WHERE is_archived = false), so archiving lifts it out
        and frees the address while the row itself survives. A restore still
        deletes nothing.
        """
        squatter = User.objects.create_user(
            email='shared@slc.edu.ph', last_name='PLACEHOLDER', first_name='SEEDED',
            password='SecurePassword123!', role='admin')

        resp = self.restore([{
            'model': 'accounts.user', 'pk': squatter.pk + 500,
            'fields': {
                'password': '!', 'last_login': None, 'is_superuser': False,
                'first_name': '', 'last_name': '', 'is_staff': False,
                'is_active': True, 'date_joined': tz.now().isoformat(),
                'last_name': 'ACCOUNT', 'first_name': 'THE', 'middle_initial': 'R', 'email': 'shared@slc.edu.ph',
                'role': 'admin', 'is_archived': False,
            },
        }])
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['displaced'], 1)      # reported, never silent

        squatter.refresh_from_db()
        self.assertTrue(squatter.is_archived)            # moved aside...
        self.assertTrue(User.objects.filter(pk=squatter.pk).exists())   # ...and still here

        restored = User.objects.get(pk=squatter.pk + 500)
        self.assertEqual(restored.email, 'shared@slc.edu.ph')
        self.assertFalse(restored.is_archived)

    def test_a_model_with_no_archive_flag_has_the_value_moved_instead(self):
        """The other half of the same rule, for a model that cannot be archived.

        A fresh install seeds vehicles.Camera with cam_number=1 and a real
        backup carries its own camera 1 at a different pk. Camera has no
        is_archived flag, so the contested VALUE is moved instead: the row
        keeps everything else and simply stops holding the number the backup
        needs. Still nothing deleted.
        """
        from vehicles.models import Camera

        # 77, not 1: migration 0026 already seeds a camera on cam_number 1, so
        # creating a second one here would collide in the test's own setup
        # rather than in the restore this is about.
        squatter = Camera.objects.create(
            cam_number=77, name='SEEDED', ip='10.0.0.1', device_id='seed',
            rtsp_url='rtsp://seed', assignment='entry')

        # Every concrete column, the way dumpdata writes it - created_at and
        # updated_at included, because the load inserts raw and an auto_now
        # column is no longer filled in for it (see gate_row above).
        stamp = tz.make_aware(datetime.datetime(2026, 1, 1, 8, 0, 0)).isoformat()
        resp = self.restore([{
            'model': 'vehicles.camera', 'pk': squatter.pk + 500,
            'fields': {'cam_number': 77, 'name': 'THE REAL CAMERA',
                       'ip': '10.184.63.63', 'device_id': 'real', 'password': '',
                       'rtsp_url': 'rtsp://real', 'assignment': 'entry',
                       'gate_id': None, 'is_active': True,
                       'created_at': stamp, 'updated_at': stamp},
        }])
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['displaced'], 1)

        squatter.refresh_from_db()
        self.assertTrue(Camera.objects.filter(pk=squatter.pk).exists())   # not deleted
        self.assertNotEqual(squatter.cam_number, 77)                      # moved aside
        self.assertEqual(squatter.name, 'SEEDED')                         # nothing else touched

        restored = Camera.objects.get(pk=squatter.pk + 500)
        self.assertEqual(restored.cam_number, 77)
        self.assertEqual(restored.name, 'THE REAL CAMERA')

    def test_nothing_is_displaced_when_the_primary_keys_already_line_up(self):
        """The ordinary overwrite must not archive the row it is about to
        rewrite: same pk, same email, so ON CONFLICT (pk) handles it and there
        is nothing in the way."""
        user = User.objects.create_user(
            email='same@slc.edu.ph', last_name='BEFORE', first_name='BEFORE',
            password='SecurePassword123!', role='admin')

        resp = self.restore([{
            'model': 'accounts.user', 'pk': user.pk,
            'fields': {
                'password': '!', 'last_login': None, 'is_superuser': False,
                'first_name': '', 'last_name': '', 'is_staff': False,
                'is_active': True, 'date_joined': tz.now().isoformat(),
                'last_name': 'AFTER', 'first_name': 'AFTER', 'email': 'same@slc.edu.ph',
                'role': 'admin', 'is_archived': False,
            },
        }])
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['displaced'], 0)

        user.refresh_from_db()
        self.assertFalse(user.is_archived)
        self.assertEqual(user.full_name, 'AFTER, AFTER')

    def test_a_restore_overwrites_matching_rows_and_inserts_the_rest(self):
        """The merge semantics the endpoint promises: update by primary key,
        insert what is missing, delete nothing."""
        existing = Gate.objects.create(gate_id='gate9', label='OLD LABEL')
        untouched = Gate.objects.create(gate_id='gate8', label='NOT IN THE FILE')

        resp = self.restore([
            self.gate_row(existing.pk, 'gate9', 'NEW LABEL'),
            self.gate_row(existing.pk + 500, 'gate7', 'BRAND NEW'),
        ])

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()['restored'], 2)
        existing.refresh_from_db()
        self.assertEqual(existing.label, 'NEW LABEL')
        self.assertEqual(Gate.objects.get(gate_id='gate7').label, 'BRAND NEW')
        self.assertTrue(Gate.objects.filter(pk=untouched.pk).exists())

    def test_a_restore_leaves_sequences_past_the_ids_it_loaded(self):
        """Restored rows carry their own primary keys, which does not move the
        table's sequence. Without a reset the next locally-created row picks an
        id the restore already used and the insert dies on a duplicate key."""
        high = Gate.objects.create(gate_id='gate9', label='SEED').pk + 10_000

        resp = self.restore([self.gate_row(high, 'gate-high', 'RESTORED HIGH ID')])
        self.assertEqual(resp.status_code, 200, resp.content)

        fresh = Gate.objects.create(gate_id='gate-after', label='CREATED AFTER RESTORE')
        self.assertGreater(fresh.pk, high)

    def test_a_dangling_reference_rolls_the_whole_restore_back(self):
        """Foreign keys are deferred until commit, so a bad one surfaces at the
        end of the load rather than on the row that caused it. The whole restore
        must come back out — a half-applied backup is worse than none."""
        before = Gate.objects.count()

        resp = self.restore([
            self.gate_row(4242, 'gate-doomed', 'SHOULD NOT SURVIVE'),
            {'model': 'vehicles.parkingspace', 'pk': 4243,
             'fields': {'zone': 999999, 'space_number': 'A1'}},
        ])

        self.assertEqual(resp.status_code, 400)
        self.assertIn('rolled back', resp.json()['error'])
        self.assertFalse(Gate.objects.filter(pk=4242).exists())
        self.assertFalse(ParkingSpace.objects.filter(pk=4243).exists())
        self.assertEqual(Gate.objects.count(), before)

    def test_a_repeated_primary_key_keeps_the_last_one(self):
        """PostgreSQL refuses to touch the same row twice in one upsert, so
        duplicates have to be collapsed before the write. Last-one-wins is what
        applying them in order used to produce."""
        resp = self.restore([
            self.gate_row(4244, 'gate-dupe', 'FIRST'),
            self.gate_row(4244, 'gate-dupe', 'SECOND'),
            self.gate_row(4244, 'gate-dupe', 'LAST'),
        ])

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(Gate.objects.get(pk=4244).label, 'LAST')

    def test_a_restore_keeps_the_timestamps_that_are_in_the_file(self):
        """The timestamps are the data — when a vehicle passed the gate, when an
        account was made. `auto_now_add` would otherwise re-stamp every restored
        row with the moment of the restore and flatten the whole history into
        one instant, so the load has to insert raw the way `loaddata` does."""
        old = tz.make_aware(datetime.datetime(2019, 3, 14, 9, 26, 53))

        resp = self.restore([self.gate_row(4246, 'gate-old', 'FROM 2019', created_at=old)])

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(Gate.objects.get(pk=4246).created_at, old)

    def test_a_restore_does_not_mint_notifications_for_restored_rows(self):
        """Bulk writes fire no per-row save signals, which is the point. Under
        `loaddata` every restored registration raised a fresh "new registration"
        alert — the receivers never checked the raw flag that marks a fixture
        load — so restoring a month-old backup buried the admin bell."""
        before = Notification.objects.count()

        resp = self.restore([self.gate_row(4245, 'gate-quiet', 'QUIET')])

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(Notification.objects.count(), before)


def _local(*args):
    """An aware campus-local datetime, for pinning `now` in slot tests."""
    return tz.make_aware(datetime.datetime(*args), tz.get_current_timezone())


class ScheduledSlotTests(TestCase):
    """The calendar arithmetic behind weekly / monthly / quarterly / yearly."""

    def cfg(self, freq, hour=17, minute=0, weekday=4, day=1, month=1):
        cfg = SystemSettings.get()
        cfg.scheduled_backup_frequency = freq
        cfg.scheduled_backup_time = datetime.time(hour, minute)
        cfg.scheduled_backup_weekday = weekday
        cfg.scheduled_backup_day = day
        cfg.scheduled_backup_month = month
        return cfg

    def test_off_has_no_slots(self):
        self.assertEqual(backup_utils.scheduled_slots(self.cfg('off')), (None, None))

    def test_retired_daily_and_hourly_have_no_slots(self):
        """A row the migration has not reached yet must not crash the pass."""
        for freq in ('daily', 'hourly'):
            with self.subTest(freq=freq):
                self.assertEqual(backup_utils.scheduled_slots(self.cfg(freq)), (None, None))

    def test_weekly_lands_on_the_chosen_weekday(self):
        # 2026-10-01 is a Thursday; weekday 4 is Friday.
        prev, nxt = backup_utils.scheduled_slots(self.cfg('weekly', weekday=4), _local(2026, 10, 1, 12, 0))
        self.assertEqual(prev, _local(2026, 9, 25, 17, 0))
        self.assertEqual(nxt, _local(2026, 10, 2, 17, 0))
        # On the Friday itself, before the time: still last Friday.
        prev, _ = backup_utils.scheduled_slots(self.cfg('weekly', weekday=4), _local(2026, 10, 2, 16, 59))
        self.assertEqual(prev, _local(2026, 9, 25, 17, 0))

    def test_monthly_day_31_falls_on_the_last_day_of_a_short_month(self):
        prev, nxt = backup_utils.scheduled_slots(self.cfg('monthly', day=31), _local(2027, 3, 15, 12, 0))
        self.assertEqual(prev, _local(2027, 2, 28, 17, 0))
        self.assertEqual(nxt, _local(2027, 3, 31, 17, 0))

    def test_monthly_rolls_back_across_new_year(self):
        prev, nxt = backup_utils.scheduled_slots(self.cfg('monthly', day=5), _local(2027, 1, 2, 8, 0))
        self.assertEqual(prev, _local(2026, 12, 5, 17, 0))
        self.assertEqual(nxt, _local(2027, 1, 5, 17, 0))

    def test_quarterly_runs_every_third_month_from_the_chosen_one(self):
        # Month 1: January, April, July, October.
        prev, nxt = backup_utils.scheduled_slots(self.cfg('quarterly', month=1), _local(2026, 10, 8, 12, 0))
        self.assertEqual(prev, _local(2026, 10, 1, 17, 0))
        self.assertEqual(nxt, _local(2027, 1, 1, 17, 0))
        # Month 4 is the same cycle as month 1.
        self.assertEqual(
            backup_utils.scheduled_slots(self.cfg('quarterly', month=4), _local(2026, 10, 8, 12, 0)),
            (prev, nxt))

    def test_quarterly_before_the_day_points_at_the_previous_quarter(self):
        # Month 2: February, May, August, November. On August 1 the 15th has
        # not come yet, so the latest slot is May 15.
        prev, nxt = backup_utils.scheduled_slots(self.cfg('quarterly', month=2, day=15), _local(2026, 8, 1, 9, 0))
        self.assertEqual(prev, _local(2026, 5, 15, 17, 0))
        self.assertEqual(nxt, _local(2026, 8, 15, 17, 0))

    def test_quarterly_day_31_falls_on_the_last_day_of_a_short_month(self):
        # Month 3: March, June, September, December.
        prev, nxt = backup_utils.scheduled_slots(self.cfg('quarterly', month=3, day=31), _local(2026, 7, 4, 9, 0))
        self.assertEqual(prev, _local(2026, 6, 30, 17, 0))
        self.assertEqual(nxt, _local(2026, 9, 30, 17, 0))

    def test_yearly_lands_on_the_chosen_month_and_day(self):
        # July 31 is the last day of the school year.
        prev, nxt = backup_utils.scheduled_slots(self.cfg('yearly', month=7, day=31), _local(2026, 10, 8, 12, 0))
        self.assertEqual(prev, _local(2026, 7, 31, 17, 0))
        self.assertEqual(nxt, _local(2027, 7, 31, 17, 0))
        # On the day itself, after the time: this year's slot.
        prev, _ = backup_utils.scheduled_slots(self.cfg('yearly', month=7, day=31), _local(2027, 7, 31, 17, 0, 1))
        self.assertEqual(prev, _local(2027, 7, 31, 17, 0))

    def test_yearly_february_29_falls_on_the_28th_in_a_common_year(self):
        prev, nxt = backup_utils.scheduled_slots(self.cfg('yearly', month=2, day=29), _local(2027, 6, 1, 12, 0))
        self.assertEqual(prev, _local(2027, 2, 28, 17, 0))
        self.assertEqual(nxt, _local(2028, 2, 29, 17, 0))


class ScheduledBackupTaskTests(BackupTempDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.folder = tempfile.mkdtemp(prefix='slc-scheduled-test-')
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.cfg = SystemSettings.get()
        self.cfg.scheduled_backup_frequency = 'weekly'
        self.cfg.scheduled_backup_weekday = tz.localdate().weekday()   # today's slot...
        self.cfg.scheduled_backup_time = datetime.time(0, 0)            # ...always already passed
        self.cfg.scheduled_backup_folder = self.folder
        self.cfg.scheduled_backup_keep = 3
        self.cfg.save()

    def run_task(self):
        from vehicles.tasks import scheduled_backup
        return scheduled_backup()

    def put_file(self, when, folder=None):
        name = f'{backup_utils.SCHEDULED_PREFIX}{when.strftime("%Y%m%d-%H%M%S")}.json'
        with open(os.path.join(folder or self.folder, name), 'w', encoding='utf-8') as fh:
            fh.write('[]')
        return name

    def test_off_writes_nothing(self):
        self.cfg.scheduled_backup_frequency = 'off'
        self.cfg.save()
        self.assertIn('skipped', self.run_task())
        self.assertEqual(os.listdir(self.folder), [])

    def test_writes_a_loadable_fixture_into_the_chosen_folder(self):
        result = self.run_task()
        self.assertIn('created', result)
        self.assertEqual(os.listdir(self.folder), [result['created']])
        with open(os.path.join(self.folder, result['created']), encoding='utf-8') as fh:
            self.assertIsInstance(json.load(fh), list)
        # Not in the app's own folder: the point is a copy kept elsewhere.
        self.assertEqual(os.listdir(backup_utils.backup_dir()), [])

    def test_a_slot_already_covered_is_not_taken_twice(self):
        self.run_task()
        self.assertEqual(self.run_task().get('skipped'), 'not due')
        self.assertEqual(len(os.listdir(self.folder)), 1)

    def test_a_missed_slot_is_caught_up(self):
        """Last backup from fifteen days ago means two weekly slots were missed
        (PC switched off): one backup is taken now, not two."""
        self.put_file(tz.localtime() - tz.timedelta(days=15))
        self.assertIn('created', self.run_task())
        self.assertEqual(self.run_task().get('skipped'), 'not due')

    def test_blank_folder_uses_the_backups_directory(self):
        self.cfg.scheduled_backup_folder = ''
        self.cfg.save()
        result = self.run_task()
        self.assertTrue(os.path.isfile(os.path.join(backup_utils.backup_dir(), result['created'])))

    def test_rotation_keeps_the_newest_and_spares_other_files(self):
        for days in range(2, 7):
            self.put_file(tz.localtime() - tz.timedelta(days=days))
        with open(os.path.join(self.folder, 'my-notes.json'), 'w') as fh:
            fh.write('{}')

        self.run_task()

        names = sorted(os.listdir(self.folder))
        self.assertIn('my-notes.json', names)   # not ours, never touched
        self.assertEqual(len([n for n in names if n.startswith(backup_utils.SCHEDULED_PREFIX)]), 3)

    def test_a_windows_path_on_another_os_is_skipped_not_created(self):
        """Campus and cloud share one settings row. A folder that is not a
        path on this machine is not this machine's job — and must not become a
        folder literally named "D:\\Backups" in the working directory."""
        self.cfg.scheduled_backup_folder = 'relative\\not-a-real-path'
        self.cfg.save()
        self.assertEqual(self.run_task().get('skipped'), 'folder is not on this machine')
        self.assertFalse(os.path.exists('relative\\not-a-real-path'))

    def test_an_unusable_folder_fails_loudly(self):
        """So the scheduler releases the claim and retries, rather than
        recording the slot as done when no file was written."""
        blocker = os.path.join(self.folder, 'a-file-not-a-folder')
        with open(blocker, 'w') as fh:
            fh.write('x')
        self.cfg.scheduled_backup_folder = os.path.join(blocker, 'sub')
        self.cfg.save()
        with self.assertRaises(RuntimeError):
            self.run_task()

    def test_scheduled_files_in_the_folder_are_listed_served_and_restorable(self):
        name = self.run_task()['created']
        items = {i['name']: i for i in backup_utils.list_backups()}
        self.assertEqual(items[name]['kind'], 'scheduled')
        self.assertEqual(os.path.dirname(backup_utils.safe_path(name)),
                         os.path.realpath(self.folder))

    def test_safe_path_only_reaches_scheduled_names_in_the_folder(self):
        with open(os.path.join(self.folder, 'auto-backup-20260101-000000.json'), 'w') as fh:
            fh.write('[]')
        self.assertIsNone(backup_utils.safe_path('auto-backup-20260101-000000.json'))

    def test_claim_key_follows_the_slot(self):
        from vehicles.scheduler import _claim_key

        first = _claim_key('scheduled_backup')
        self.assertTrue(first.startswith(f'scheduled_backup@{socket.gethostname()[:30]}#'))
        self.assertLessEqual(len(first), 64)
        self.cfg.scheduled_backup_folder = os.path.join(self.folder, 'elsewhere')
        self.cfg.save()
        self.assertNotEqual(_claim_key('scheduled_backup'), first)

    def test_scheduler_runs_it_and_gives_back_a_slot_that_was_not_due(self):
        from vehicles.models import DailyJobRun
        from vehicles.scheduler import DAILY_JOBS, _claim_key, run_due_jobs

        self.assertLess(DAILY_JOBS.index('scheduled_backup'), DAILY_JOBS.index('purge_old_records'))
        self.put_file(tz.localtime())          # today's slot already covered
        with patch('vehicles.tasks.auto_archive_expired_accounts', return_value={}), \
             patch('vehicles.tasks.purge_old_records', return_value={}):
            outcomes = run_due_jobs()
        self.assertIn('not due', outcomes['scheduled_backup'])
        self.assertFalse(DailyJobRun.objects.filter(job=_claim_key('scheduled_backup')).exists())

    def test_a_morning_catch_up_does_not_cost_the_same_days_slot(self):
        """Found running the real server: switching a schedule on at 9 AM takes
        the last missed backup straight away — and a day-keyed claim then
        skipped that same day's noon slot. Each slot must claim on its own."""
        from vehicles.scheduler import run_due_jobs

        self.cfg.scheduled_backup_time = datetime.time(12, 0)
        self.cfg.scheduled_backup_weekday = 2               # 2026-06-10 is a Wednesday
        self.cfg.save()
        with patch('vehicles.scheduler.DAILY_JOBS', ('scheduled_backup',)):
            with patch('django.utils.timezone.now', return_value=_local(2026, 6, 10, 9, 0)):
                self.assertIn('created', run_due_jobs()['scheduled_backup'])     # last Wednesday's noon
            with patch('django.utils.timezone.now', return_value=_local(2026, 6, 10, 12, 0, 5)):
                self.assertIn('created', run_due_jobs()['scheduled_backup'])     # today's noon
            with patch('django.utils.timezone.now', return_value=_local(2026, 6, 10, 13, 0)):
                self.assertNotIn('created', run_due_jobs().get('scheduled_backup', ''))
        self.assertEqual(len(os.listdir(self.folder)), 2)

    def test_the_scheduler_wakes_for_a_slot_inside_the_hour(self):
        from vehicles import scheduler

        soon = tz.localtime() + tz.timedelta(minutes=10)
        self.cfg.scheduled_backup_weekday = soon.weekday()
        self.cfg.scheduled_backup_time = soon.time().replace(second=0, microsecond=0)
        self.cfg.save()
        wait = scheduler._seconds_until_next_pass()
        # Anywhere in the next ~10 minutes (minute rounding), never the full hour.
        self.assertLess(wait, 11 * 60)


class ScheduledBackupSettingsTests(BackupTempDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.folder = tempfile.mkdtemp(prefix='slc-scheduled-test-')
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.admin = User.objects.create_user(
            email='scheduled-backup-admin@test.local', last_name='ADMIN', first_name='SCHEDULED', middle_initial='B',
            password='SecurePassword123!', role='admin')
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def put(self, **data):
        return self.client.put('/api/vehicles/system-settings/', data, format='json')

    def test_quarterly_and_yearly_round_trip_with_their_month(self):
        for freq, month in (('quarterly', 2), ('yearly', 7)):
            with self.subTest(freq=freq):
                resp = self.put(scheduled_backup_frequency=freq, scheduled_backup_month=month,
                                scheduled_backup_day=31, scheduled_backup_time='17:00')
                self.assertEqual(resp.status_code, 200, resp.content)
                self.assertEqual(resp.json()['scheduled_backup_frequency'], freq)
                self.assertEqual(resp.json()['scheduled_backup_month'], month)
                self.assertNotIn('auto_backup_frequency', resp.json())

    def test_a_writable_folder_is_saved_and_round_trips(self):
        resp = self.put(scheduled_backup_frequency='weekly', scheduled_backup_time='07:45',
                        scheduled_backup_weekday=0, scheduled_backup_folder=self.folder,
                        scheduled_backup_keep=5)
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body['scheduled_backup_frequency'], 'weekly')
        self.assertEqual(body['scheduled_backup_time'], '07:45')
        self.assertEqual(body['scheduled_backup_weekday'], 0)
        self.assertEqual(body['scheduled_backup_folder'], self.folder)
        self.assertEqual(os.listdir(self.folder), [])    # the write test cleaned up after itself

    def test_a_folder_that_cannot_be_written_is_refused(self):
        blocker = os.path.join(self.folder, 'a-file')
        with open(blocker, 'w') as fh:
            fh.write('x')
        resp = self.put(scheduled_backup_frequency='weekly',
                        scheduled_backup_folder=os.path.join(blocker, 'sub'))
        self.assertEqual(resp.status_code, 400)
        self.assertIn('scheduled_backup_folder', resp.json())
        self.assertEqual(SystemSettings.get().scheduled_backup_frequency, 'off')

    def test_a_relative_folder_is_refused(self):
        resp = self.put(scheduled_backup_frequency='weekly', scheduled_backup_folder='backups-here')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('full folder path', resp.json()['scheduled_backup_folder'])

    def test_bad_parts_are_refused(self):
        for field, value in (('scheduled_backup_frequency', 'hourly'),
                             ('scheduled_backup_frequency', 'daily'),      # retired with the interval schedule
                             ('scheduled_backup_time', '25:00'),
                             ('scheduled_backup_month', 13),
                             ('scheduled_backup_weekday', 7),
                             ('scheduled_backup_day', 0),
                             ('scheduled_backup_keep', 91)):
            with self.subTest(field=field, value=value):
                resp = self.put(**{field: value})
                self.assertEqual(resp.status_code, 400)
                self.assertIn(field, resp.json())

    def test_listing_reports_the_schedule_status(self):
        self.put(scheduled_backup_frequency='weekly', scheduled_backup_weekday=tz.localdate().weekday(),
                 scheduled_backup_time='00:00', scheduled_backup_folder=self.folder)
        status = self.client.get('/api/accounts/system/backups/').json()['scheduled']
        self.assertTrue(status['folder_ok'])
        self.assertTrue(status['overdue'])           # today's 00:00 has no file yet
        self.assertIsNotNone(status['next_due'])

        from vehicles.tasks import scheduled_backup
        scheduled_backup()
        status = self.client.get('/api/accounts/system/backups/').json()['scheduled']
        self.assertFalse(status['overdue'])
        self.assertTrue(status['last']['name'].startswith(backup_utils.SCHEDULED_PREFIX))

    def test_listing_flags_a_folder_that_has_gone_missing(self):
        self.put(scheduled_backup_frequency='weekly', scheduled_backup_folder=self.folder)
        shutil.rmtree(self.folder)
        status = self.client.get('/api/accounts/system/backups/').json()['scheduled']
        self.assertFalse(status['folder_ok'])
        self.assertIn('does not exist', status['folder_error'])
