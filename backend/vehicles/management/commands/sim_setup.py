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
"""
import os
import shutil

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

TEMPLATE_DB = 'slc_manual_demo'
TEMPLATE_MEDIA = os.path.join(settings.BASE_DIR, '..', 'docs', 'user-manual', 'capture', 'demo_media')


class Command(BaseCommand):
    help = "Create the instructor demo database (slc_sim_demo) from the screenshot demo's."

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true',
                            help='drop the demo database first and start over')

    def handle(self, reset=False, **options):
        import sim_clock_actions as actions
        if not getattr(settings, 'SIM_CLOCK_ENABLED', False):
            raise CommandError('Run this under sim_settings (.\\dev.ps1 -SimClock -SimSetup).')
        db = settings.DATABASES['default']
        name = db['NAME']
        if not name.startswith('slc_sim') or db['HOST'] not in ('127.0.0.1', 'localhost'):
            raise CommandError(f'Refusing to set up {name!r} on {db["HOST"]!r}.')

        created = self._create(db, name, reset)
        if created == 'copied' and os.path.isdir(TEMPLATE_MEDIA):
            if reset and os.path.isdir(settings.MEDIA_ROOT):
                shutil.rmtree(settings.MEDIA_ROOT)
            shutil.copytree(TEMPLATE_MEDIA, settings.MEDIA_ROOT, dirs_exist_ok=True)
            self.stdout.write(f'Copied the demo uploads to {settings.MEDIA_ROOT}.')
        self.stdout.write('Applying migrations...')
        call_command('migrate', interactive=False, verbosity=0)
        self._active_period()
        actions.reset()
        self._summary(created)

    # ── The database ────────────────────────────────────────────────────────
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
        self.stdout.write(self.style.SUCCESS('\nInstructor demo database ready.'))
        if admin is None:
            self.stdout.write('No admin account yet: create one with  python manage.py createsuperuser')
            return
        device = TwoFactorDevice.objects.filter(user=admin, confirmed_at__isnull=False).first()
        self.stdout.write(f'  Admin login: {admin.email}'
                          + ('   password: Demo@2026!' if created == 'copied' else ''))
        if device:
            self.stdout.write(f'  2FA: add this key to an authenticator app: {device.secret}')
            self.stdout.write('       or print the current code:  python manage.py sim_clock code')
        self.stdout.write(f"  Emails go to: {settings.SIM_EMAIL_TO or '(nowhere: set SIM_EMAIL_TO in backend/.env)'}")
