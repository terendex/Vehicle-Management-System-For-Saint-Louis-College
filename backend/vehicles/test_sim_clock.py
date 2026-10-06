"""The instructor demo's simulated clock stays out of the real system.

The clock itself is only ever installed by sim_settings.py, on a local demo
database. These tests pin what must hold everywhere else, and the helpers the
demo relies on, without installing it in the test process (installing patches
django.utils.timezone.now for the whole interpreter).
"""
import os
from unittest import mock

from django.core import mail
from django.core.mail import EmailMessage
from django.test import SimpleTestCase, TestCase, override_settings

import sim_clock
import sim_clock_actions


class InstallGuardTests(SimpleTestCase):

    def test_refuses_the_live_database(self):
        for host in ('ep-cool-name-123.ap-southeast-1.aws.neon.tech', '10.0.0.5', ''):
            with self.assertRaises(sim_clock.SimClockRefused, msg=host):
                sim_clock.install('unused.json', {'default': {'HOST': host, 'NAME': 'x'}})
        self.assertFalse(sim_clock.installed())

    def test_refuses_on_railway_even_with_a_local_host(self):
        with mock.patch.dict(os.environ, {'RAILWAY_ENVIRONMENT': 'production'}):
            with self.assertRaises(sim_clock.SimClockRefused):
                sim_clock.install('unused.json', {'default': {'HOST': '127.0.0.1', 'NAME': 'slc_sim_demo'}})
        self.assertFalse(sim_clock.installed())

    def test_the_real_system_has_no_test_clock(self):
        self.assertFalse(sim_clock_actions.available())
        with self.assertRaises(sim_clock.SimClockRefused):
            sim_clock_actions.status()


class RealSystemTests(TestCase):

    def test_no_test_clock_url(self):
        self.assertEqual(self.client.get('/api/system/test-clock/').status_code, 404)

    def test_deployment_reports_no_simulated_clock(self):
        self.assertNotIn('sim_clock', self.client.get('/api/deployment/').json())

    def test_two_factor_uses_the_real_time(self):
        from accounts.twofa import _wall_clock
        from django.utils import timezone
        self.assertLess(abs((_wall_clock() - timezone.now()).total_seconds()), 5)


class DormancyOnTheRealClockTests(SimpleTestCase):
    """Moving the demo's date must not make two-factor ask at every sign-in."""

    def _dormant(self, last_login, simulated_now, real_now):
        from types import SimpleNamespace
        from accounts import twofa
        with mock.patch('django.utils.timezone.now', return_value=simulated_now), \
             mock.patch.object(twofa, '_wall_clock', return_value=real_now):
            return twofa.is_dormant(SimpleNamespace(last_login=last_login))

    def test_a_date_jump_does_not_make_a_recent_login_dormant(self):
        from datetime import timedelta
        from django.utils import timezone
        real = timezone.now()
        self.assertFalse(self._dormant(real - timedelta(days=1), real + timedelta(days=30), real))

    def test_a_login_stamped_in_the_moved_past_is_not_dormant_there(self):
        from datetime import timedelta
        from django.utils import timezone
        real = timezone.now()
        past = real - timedelta(days=60)
        self.assertFalse(self._dormant(past - timedelta(hours=1), past, real))

    def test_a_week_of_real_silence_is_still_dormant(self):
        from datetime import timedelta
        from django.utils import timezone
        real = timezone.now()
        self.assertTrue(self._dormant(real - timedelta(days=8), real, real))


class DemoLoginWithoutCodeTests(SimpleTestCase):
    """The demo signs in on the password alone; the real system never does."""

    def test_the_demo_asks_no_code_at_login(self):
        from types import SimpleNamespace
        from accounts import twofa
        admin = SimpleNamespace(is_authenticated=True, is_archived=False, is_active=True, role='admin')
        with override_settings(SIM_SKIP_2FA_LOGIN=True), \
             mock.patch.object(sim_clock, 'installed', return_value=True):
            self.assertIsNone(twofa.login_challenge(admin))

    def test_the_setting_alone_does_not_skip_codes(self):
        from accounts import twofa
        with override_settings(SIM_SKIP_2FA_LOGIN=True):
            self.assertFalse(sim_clock.installed())
            self.assertFalse(twofa._demo_skips_login_codes())

    def test_the_real_system_has_no_such_setting(self):
        from django.conf import settings
        from accounts import twofa
        self.assertFalse(getattr(settings, 'SIM_SKIP_2FA_LOGIN', False))
        self.assertFalse(twofa._demo_skips_login_codes())


class HelperTests(SimpleTestCase):

    def test_offsets_read_plainly(self):
        self.assertEqual(sim_clock.describe_offset(0), '0')
        self.assertEqual(sim_clock.describe_offset(4 * 86400 + 2 * 3600), '+4 days 2 h')
        self.assertEqual(sim_clock.describe_offset(-3600), '-1 h')
        self.assertEqual(sim_clock.describe_offset(90 * 60), '+1 h 30 min')

    def test_steps_and_dates_parse(self):
        self.assertEqual(sim_clock_actions.parse_step('3d'), {'days': 3})
        self.assertEqual(sim_clock_actions.parse_step('2wd'), {'working_days': 2})
        self.assertEqual(sim_clock_actions.parse_step('-5h'), {'hours': -5})
        with self.assertRaises(ValueError):
            sim_clock_actions.parse_step('soon')
        self.assertEqual(sim_clock_actions.parse_when('2026-10-09 10:00').hour, 10)
        self.assertEqual(sim_clock_actions.parse_when('2026-10-09').day, 9)
        with self.assertRaises(ValueError):
            sim_clock_actions.parse_when('next friday')


class RedirectEmailTests(SimpleTestCase):

    @override_settings(SIM_EMAIL_TO='demo-inbox@example.com',
                       SIM_REAL_EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_every_message_goes_to_the_demo_inbox_only(self):
        mail.outbox = []
        backend = sim_clock.RedirectEmailBackend()
        msg = EmailMessage('Expired', 'body', 'cdso@example.com',
                           to=['student@slc-sflu.edu.ph'], cc=['parent@example.com'], bcc=['x@example.com'])
        backend.send_messages([msg])
        [sent] = mail.outbox
        self.assertEqual((sent.to, sent.cc, sent.bcc), (['demo-inbox@example.com'], [], []))
        self.assertIn('student@slc-sflu.edu.ph', sent.extra_headers['X-SLC-Demo-Original-To'])

    @override_settings(SIM_EMAIL_TO='', SIM_REAL_EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_without_a_demo_inbox_nothing_is_sent(self):
        mail.outbox = []
        backend = sim_clock.RedirectEmailBackend()
        self.assertIn('console', type(backend.inner).__module__)


class DemoSafeguardTests(TestCase):
    """A deployment can leave jobs out, and the demo opens no cameras."""

    @override_settings(SCHEDULER_SKIP_JOBS=('auto_backup', 'scheduled_backup'))
    def test_skipped_jobs_never_run(self):
        from vehicles import scheduler
        with mock.patch('vehicles.tasks.auto_backup') as backup, \
             mock.patch('vehicles.tasks.scheduled_backup') as scheduled, \
             mock.patch('vehicles.tasks.purge_old_records', return_value={}) as purge:
            outcomes = scheduler.run_due_jobs(force=True)
        backup.assert_not_called()
        scheduled.assert_not_called()
        purge.assert_called_once()
        self.assertNotIn('auto_backup', outcomes)

    @override_settings(SIM_CLOCK_ENABLED=True, SIM_CAMERAS=False)
    def test_the_demo_does_not_open_real_cameras(self):
        from vehicles import ffmpeg_capture
        with mock.patch.object(ffmpeg_capture, '_try_cv2') as cv2_open:
            cap = ffmpeg_capture.open_capture('rtsp://admin:x@10.243.40.80:554/onvif1')
        self.assertFalse(cap.isOpened())
        cv2_open.assert_not_called()


class DemoBackupTests(TestCase):
    """Demo backups follow the simulated date, save where the demo was told to,
    and never mix with real backups in the same folder."""

    def setUp(self):
        import shutil
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.own = os.path.join(self.tmp, 'sim_backups')
        self.chosen = os.path.join(self.tmp, 'picked folder')        # what Browse... filled in
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.demo = override_settings(BACKUP_DIR=self.own, SCHEDULED_BACKUP_PREFIX='demo-scheduled-backup-')

    def _schedule(self, folder):
        from datetime import time
        from vehicles.models import SystemSettings
        cfg = SystemSettings.get()
        cfg.auto_backup_frequency = 'daily'
        cfg.scheduled_backup_frequency = 'daily'
        cfg.scheduled_backup_time = time(0, 0)
        cfg.scheduled_backup_folder = folder
        cfg.scheduled_backup_keep = 1
        cfg.save()

    def _stamp(self, days):
        from datetime import timedelta
        from django.utils import timezone
        return timezone.localtime(timezone.now() + timedelta(days=days)).strftime('%Y%m%d-%H%M%S')

    def test_the_real_system_keeps_its_folder_and_names(self):
        from django.conf import settings
        from accounts.backup_utils import SCHEDULED_PREFIX, backup_dir, scheduled_prefix
        self.assertEqual(backup_dir(), os.path.join(settings.BASE_DIR, 'backups'))
        self.assertEqual(scheduled_prefix(), SCHEDULED_PREFIX)

    def test_scheduled_backups_save_to_the_browsed_folder(self):
        from vehicles import tasks
        self._schedule(self.chosen)
        with self.demo, mock.patch('accounts.backup_utils.dump_backup', return_value='[]'):
            auto = tasks.auto_backup()
            scheduled = tasks.scheduled_backup()
        self.assertTrue(os.path.isfile(os.path.join(self.own, auto['created'])))
        self.assertEqual(scheduled['folder'], self.chosen)
        self.assertTrue(scheduled['created'].startswith('demo-scheduled-backup-'))
        self.assertTrue(os.path.isfile(os.path.join(self.chosen, scheduled['created'])))

    def test_no_folder_picked_saves_beside_the_demos_own_backups(self):
        from vehicles import tasks
        self._schedule('')
        with self.demo, mock.patch('accounts.backup_utils.dump_backup', return_value='[]'):
            scheduled = tasks.scheduled_backup()
        self.assertEqual(scheduled['folder'], self.own)

    def test_real_backups_in_the_same_folder_are_left_alone(self):
        """keep=1 rotates the demo's own files only; the real system does not
        list the demo's, so it never counts one as its backup."""
        from accounts.backup_utils import SCHEDULED_PREFIX, latest_scheduled_backup
        from vehicles import tasks
        os.makedirs(self.chosen)
        real = os.path.join(self.chosen, f'{SCHEDULED_PREFIX}{self._stamp(-10)}.json')
        open(real, 'w').close()
        self._schedule(self.chosen)
        with self.demo, mock.patch('accounts.backup_utils.dump_backup', return_value='[]'):
            open(os.path.join(self.chosen, f'demo-scheduled-backup-{self._stamp(-5)}.json'), 'w').close()
            tasks.scheduled_backup()
            demo_files = [n for n in os.listdir(self.chosen) if n.startswith('demo-')]
        self.assertTrue(os.path.isfile(real))
        self.assertEqual(len(demo_files), 1)                          # rotated down to keep=1
        self.assertEqual(latest_scheduled_backup(self.chosen)['name'], os.path.basename(real))

    def test_moving_back_removes_only_demo_backups_dated_after_the_new_date(self):
        from accounts.backup_utils import AUTO_PREFIX, SCHEDULED_PREFIX
        os.makedirs(self.own)
        os.makedirs(self.chosen)
        self._schedule(self.chosen)
        files = {
            os.path.join(self.own, f'{AUTO_PREFIX}{self._stamp(-3)}.json'): True,       # past: kept
            os.path.join(self.own, f'{AUTO_PREFIX}{self._stamp(30)}.json'): False,      # future: removed
            os.path.join(self.chosen, f'demo-scheduled-backup-{self._stamp(365)}.json'): False,
            os.path.join(self.chosen, f'{SCHEDULED_PREFIX}{self._stamp(365)}.json'): True,   # a real one: untouched
            os.path.join(self.chosen, 'notes.txt'): True,
        }
        for path in files:
            open(path, 'w').close()
        with self.demo:
            sim_clock_actions._drop_future_backups()
        for path, kept in files.items():
            self.assertEqual(os.path.exists(path), kept, path)

    def test_run_jobs_now_is_the_schedulers_whole_pass(self):
        from vehicles.scheduler import DAILY_JOBS
        self.assertEqual(tuple(sim_clock_actions.DEMO_JOBS), tuple(DAILY_JOBS))
        self.assertEqual(set(sim_clock_actions.JOB_LABELS), set(DAILY_JOBS))
