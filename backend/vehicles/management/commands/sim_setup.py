"""Create (or re-create) the instructor demo's database, slc_sim_demo.

Run by `.\\dev.ps1 -SimClock -SimSetup` (add -Reset to start over), under
sim_settings. The demo database is a COPY of the local screenshot database,
slc_manual_demo, which already holds a whole fictional campus (seed_demo.py):
accounts, vehicles, registrations, logs and violations, all invented. Copying
it leaves the screenshot database exactly as it was. Without it, an empty
database is created and migrated instead.

Then: migrations are applied, a registration period for the current school
year is made the active one, and the simulated clock is switched on at the
real time (offset 0). A copied campus also gets a copy of the screenshot
demo's uploads (parking reference images, receipts), which its rows point at.

--from-live copies the LIVE system instead, as close as the demo can get:
the live database (pg_dump over DATABASE_URL from backend/.env, read-only)
restored into a fresh slc_sim_demo, then this code's pending migrations, then
every uploaded file from the R2 bucket (read-only) into backend/sim_media.
Nothing on the live side is written. The copy is left exactly as live has it
(no period is activated). It holds real people's data: it stays on this PC,
emails from it only ever reach SIM_EMAIL_TO, and cameras stay closed unless
SIM_CAMERAS=1 (sim_settings.py). Drop it with -Reset when done.
"""
import os
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

TEMPLATE_DB = 'slc_manual_demo'
TEMPLATE_MEDIA = os.path.join(settings.BASE_DIR, '..', 'docs', 'user-manual', 'capture', 'demo_media')
PG_BIN = r'C:\Program Files\PostgreSQL\18\bin'


class Command(BaseCommand):
    help = "Create the instructor demo database (slc_sim_demo) from the screenshot demo's."

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true',
                            help='drop the demo database first and start over')
        parser.add_argument('--from-live', action='store_true',
                            help='copy the live database and uploads (read-only) instead of the fictional campus')

    def handle(self, reset=False, from_live=False, **options):
        import sim_clock_actions as actions
        if not getattr(settings, 'SIM_CLOCK_ENABLED', False):
            raise CommandError('Run this under sim_settings (.\\dev.ps1 -SimClock -SimSetup).')
        db = settings.DATABASES['default']
        name = db['NAME']
        if not name.startswith('slc_sim') or db['HOST'] not in ('127.0.0.1', 'localhost'):
            raise CommandError(f'Refusing to set up {name!r} on {db["HOST"]!r}.')

        if from_live:
            self._copy_live(db, name)
            self._clear_backups()
            self.stdout.write('Applying this code\'s migrations on the copy...')
            call_command('migrate', interactive=False, verbosity=0)
            self._copy_live_media()
            actions.reset()
            self._summary('live')
            return

        created = self._create(db, name, reset)
        if created == 'copied':
            self._clear_backups()
        if created == 'copied' and os.path.isdir(TEMPLATE_MEDIA):
            if reset and os.path.isdir(settings.MEDIA_ROOT):
                shutil.rmtree(settings.MEDIA_ROOT)
            shutil.copytree(TEMPLATE_MEDIA, settings.MEDIA_ROOT, dirs_exist_ok=True)
            self.stdout.write(f'Copied the demo uploads to {settings.MEDIA_ROOT}.')
        self.stdout.write('Applying migrations...')
        call_command('migrate', interactive=False, verbosity=0)
        if created == 'kept' and self._is_live_copy():
            # An earlier --from-live copy: keep it exactly as live has it.
            actions.reset()
            self._summary('live')
            return
        self._active_period()
        actions.reset()
        self._summary(created)

    @staticmethod
    def _is_live_copy():
        """The fictional campus always has its CDSO demo account; a live copy never does."""
        from accounts.models import User
        return not User.objects.filter(email='cdso.demo@slc-sflu.edu.ph').exists()

    # ── The database ────────────────────────────────────────────────────────
    def _clear_backups(self):
        """A fresh copy starts with no backups: the old ones hold the data it replaced."""
        folder = getattr(settings, 'BACKUP_DIR', None)
        if folder and os.path.isdir(folder):
            shutil.rmtree(folder)
            self.stdout.write(f'Cleared the demo backups in {folder}.')

    def _create(self, db, name, reset):
        import psycopg2
        from psycopg2 import sql
        conn = psycopg2.connect(dbname='postgres', user=db['USER'], password=db['PASSWORD'],
                                host=db['HOST'], port=db['PORT'])
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                if reset:
                    cur.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity '
                                'WHERE datname = %s AND pid <> pg_backend_pid()', [name])
                    cur.execute(sql.SQL('DROP DATABASE IF EXISTS {}').format(sql.Identifier(name)))
                    self.stdout.write(f'Dropped {name}.')
                cur.execute('SELECT 1 FROM pg_database WHERE datname = %s', [name])
                if cur.fetchone():
                    self.stdout.write(f'{name} already exists; keeping it (use -Reset to start over).')
                    return 'kept'
                cur.execute('SELECT 1 FROM pg_database WHERE datname = %s', [TEMPLATE_DB])
                if cur.fetchone():
                    # A template copy needs the template idle: the screenshot
                    # stack must not be running against it.
                    cur.execute('SELECT count(*) FROM pg_stat_activity WHERE datname = %s', [TEMPLATE_DB])
                    if cur.fetchone()[0]:
                        raise CommandError(f'{TEMPLATE_DB} is in use (is the screenshot stack running?). '
                                           'Close it and run setup again.')
                    cur.execute(sql.SQL('CREATE DATABASE {} TEMPLATE {}').format(
                        sql.Identifier(name), sql.Identifier(TEMPLATE_DB)))
                    self.stdout.write(f'Created {name} as a copy of {TEMPLATE_DB} (the fictional demo campus).')
                    return 'copied'
                cur.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
                self.stdout.write(f'Created an empty {name} ({TEMPLATE_DB} not found).')
                return 'empty'
        finally:
            conn.close()

    # ── A copy of the live system ──────────────────────────────────────────
    def _tool(self, exe):
        path = os.path.join(PG_BIN, exe)
        if not os.path.exists(path):
            found = shutil.which(exe)
            if not found:
                raise CommandError(f'{exe} not found (looked in {PG_BIN} and PATH).')
            path = found
        return path

    def _copy_live(self, db, name):
        import psycopg2
        from psycopg2 import sql
        url = os.environ.get('DATABASE_URL', '').strip()
        if not url:
            raise CommandError('DATABASE_URL (the live database) is not set in backend/.env.')
        # pg_dump needs a direct connection, not Neon's connection pooler.
        parts = urlsplit(url)
        live = urlunsplit(parts._replace(netloc=parts.netloc.replace('-pooler.', '.')))
        host = parts.hostname or ''
        if host in ('127.0.0.1', 'localhost'):
            raise CommandError('DATABASE_URL points at this PC, not at the live database.')

        dump = os.path.join(tempfile.gettempdir(), 'slc_live_copy.dump')
        self.stdout.write(f'Copying the live database from {host} (read-only)...')
        done = subprocess.run([self._tool('pg_dump.exe'), '--format=custom', '--no-owner', '--no-acl',
                               f'--file={dump}', live], capture_output=True, text=True)
        if done.returncode != 0:
            raise CommandError(f'pg_dump failed: {done.stderr.strip()[-800:]}')

        conn = psycopg2.connect(dbname='postgres', user=db['USER'], password=db['PASSWORD'],
                                host=db['HOST'], port=db['PORT'])
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity '
                            'WHERE datname = %s AND pid <> pg_backend_pid()', [name])
                cur.execute(sql.SQL('DROP DATABASE IF EXISTS {}').format(sql.Identifier(name)))
                cur.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        finally:
            conn.close()

        env = {**os.environ, 'PGPASSWORD': db['PASSWORD']}
        done = subprocess.run([self._tool('pg_restore.exe'), '--no-owner', '--no-acl',
                               f'--host={db["HOST"]}', f'--port={db["PORT"]}', f'--username={db["USER"]}',
                               f'--dbname={name}', dump], capture_output=True, text=True, env=env)
        try:
            os.remove(dump)                          # real people's data: no stray copy left behind
        except OSError:
            pass
        # Neon-only extensions and roles cannot be recreated here; pg_restore
        # reports them and carries on. Anything else is shown in full.
        problems = [line for line in done.stderr.splitlines()
                    if 'error' in line.lower() and 'neon' not in line.lower()
                    and 'extension' not in line.lower() and 'already exists' not in line.lower()]
        if done.returncode != 0 and problems:
            self.stdout.write(self.style.WARNING('pg_restore reported:\n  ' + '\n  '.join(problems[:20])))
        self.stdout.write(f'Restored the live data into {name}.')

    def _copy_live_media(self):
        """Every uploaded file from the live R2 bucket, read-only, into MEDIA_ROOT."""
        import boto3
        if not os.environ.get('R2_BUCKET_NAME'):
            self.stdout.write('No R2 bucket configured; uploads not copied.')
            return
        s3 = boto3.client(
            's3', region_name='auto',
            endpoint_url=f"https://{os.environ.get('R2_ACCOUNT_ID')}.r2.cloudflarestorage.com",
            aws_access_key_id=os.environ.get('R2_ACCESS_KEY_ID'),
            aws_secret_access_key=os.environ.get('R2_SECRET_ACCESS_KEY'))
        bucket = os.environ['R2_BUCKET_NAME']
        if os.path.isdir(settings.MEDIA_ROOT):
            shutil.rmtree(settings.MEDIA_ROOT)
        count = 0
        for page in s3.get_paginator('list_objects_v2').paginate(Bucket=bucket):
            for item in page.get('Contents', []):
                key = item['Key']
                target = os.path.normpath(os.path.join(settings.MEDIA_ROOT, *key.split('/')))
                if not target.startswith(os.path.normpath(settings.MEDIA_ROOT)) or key.endswith('/'):
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                s3.download_file(bucket, key, target)
                count += 1
        self.stdout.write(f'Copied {count} uploaded file(s) from the live storage to {settings.MEDIA_ROOT}.')

    # ── A registration window for the school year now running ──────────────
    def _active_period(self):
        from vehicles import school_year as sy
        from vehicles.models import RegistrationPeriod

        year = sy.school_year_of(sy.today())
        start, end = sy.period_dates(year)
        label = sy.label
        RegistrationPeriod.objects.filter(is_active=True).update(is_active=False)
        period, _ = RegistrationPeriod.objects.update_or_create(
            school_year=year,
            defaults={'label': label(year), 'start_date': start, 'end_date': end, 'is_active': True})
        self.stdout.write(f'Active registration period: {period.label}, {period.start_date} to {period.end_date}.')

    def _summary(self, created):
        from accounts.models import TwoFactorDevice, User
        admin = (User.objects.filter(role='admin', is_active=True, is_archived=False)
                 .order_by('pk').first())
        self.stdout.write(self.style.SUCCESS(
            '\nInstructor demo database ready: a COPY OF THE LIVE SYSTEM (stays on this PC).'
            if created == 'live' else '\nInstructor demo database ready.'))
        if created == 'live':
            self.stdout.write('  Log in with your own live account (same password and authenticator).')
            self.stdout.write(f"  Emails go to: {settings.SIM_EMAIL_TO or '(nowhere: set SIM_EMAIL_TO in backend/.env)'}")
            return
        if admin is None:
            self.stdout.write('No admin account yet: create one with  python manage.py createsuperuser')
            return
        device = TwoFactorDevice.objects.filter(user=admin, confirmed_at__isnull=False).first()
        # The fictional campus's accounts all use the demo password (seed_demo.py).
        demo_password = admin.email.endswith('@slc-sflu.edu.ph') and admin.check_password('Demo@2026!')
        self.stdout.write(f'  Admin login: {admin.email}'
                          + ('   password: Demo@2026!' if demo_password else ''))
        if device:
            self.stdout.write(f'  2FA: add this key to an authenticator app: {device.secret}')
            self.stdout.write('       or print the current code:  python manage.py sim_clock code')
        self.stdout.write(f"  Emails go to: {settings.SIM_EMAIL_TO or '(nowhere: set SIM_EMAIL_TO in backend/.env)'}")
