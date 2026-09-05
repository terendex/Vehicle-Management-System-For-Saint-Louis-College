"""Clear the accounts and registrations on a DPO-trial database.

TEMPORARY — Data Privacy Office trial, `temporary` branch only.

Deletes every user except the admins, along with the vehicles, registrations and
violations they owned, so the registration flow can be exercised from an empty
slate. Audit history survives: AuditLog.actor and .target_user are SET_NULL by
design, so what happened stays readable after the account is gone.

    THIS IS DESTRUCTIVE AND THERE IS NO UNDO.

The whole point of this command is that it must never run against the shared
production database by accident, so it will not run anywhere unless you name the
database you think you are on:

    # 1. Look, change nothing. This is the default.
    python manage.py reset_trial_data

    # 2. Actually delete, having read the host it printed in step 1.
    python manage.py reset_trial_data --confirm-host ep-my-trial-branch

`--confirm-host` is matched against the live connection's host. Point it at a
Neon branch and the fragment will not match production's endpoint, so a stale
DATABASE_URL in your shell aborts instead of emptying the real thing.

Admins are kept because deleting the last one locks you out of the CDSO review
screens, and migration 0005 only seeds admin@slc.edu.ph on a fresh database — on
an existing one it has already run and will not seed again. Pass --include-admins
if you genuinely want them gone and are ready to run createsuperuser.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction


class Command(BaseCommand):
    help = ("TEMPORARY (DPO trial): delete all non-admin users and every "
            "vehicle/registration, so registration can be tested from empty.")

    def add_arguments(self, parser):
        parser.add_argument(
            '--confirm-host',
            default='',
            help="A fragment of the database host you intend to wipe. Nothing is "
                 "deleted unless this matches the live connection.",
        )
        parser.add_argument(
            '--include-admins',
            action='store_true',
            help="Also delete admin accounts. You will need createsuperuser afterwards.",
        )
        parser.add_argument(
            '--keep-guards',
            action='store_true',
            help="Keep security (guard) accounts too, so gate scanning and QR "
                 "login still work. Leaves only the vehicle owners to delete.",
        )
        parser.add_argument(
            '--purge-orphans',
            action='store_true',
            help="Also delete registrations and vehicles that no surviving user "
                 "owns. Off by default: rejected applications are already exempt "
                 "from the plate/email/licence uniqueness rules, so they block "
                 "nothing, and deleting them is cosmetic rather than useful.",
        )

    def handle(self, *args, **options):
        from accounts.models import User, delete_users_with_owned_records
        from vehicles.models import Vehicle, VehicleRegistration

        params = connection.get_connection_params()
        host = str(params.get('host') or 'local')
        dbname = str(params.get('dbname') or params.get('database') or '?')

        doomed = User.objects.all()
        if not options['include_admins']:
            doomed = doomed.exclude(role=User.Role.ADMIN)
        if options['keep_guards']:
            doomed = doomed.exclude(role=User.Role.SECURITY)

        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING('Connected to'))
        self.stdout.write(f'  host      {host}')
        self.stdout.write(f'  database  {dbname}')
        self.stdout.write('')
        # Counted through the users being deleted, not as a table total: with
        # --keep-guards this removes two owners, not the whole registration
        # table, and a headline of "6 registrations" would badly misdescribe it.
        owned_regs = VehicleRegistration.objects.filter(user__in=doomed).count()
        owned_cars = Vehicle.objects.filter(user__in=doomed).count()

        self.stdout.write(self.style.MIGRATE_HEADING('Would delete'))
        self.stdout.write(f'  users            {doomed.count()}')
        for row in doomed.order_by('role', 'email').values_list('role', 'email'):
            self.stdout.write(f'      {row[0]:<14} {row[1]}')
        self.stdout.write(f'  their registrations  {owned_regs}')
        self.stdout.write(f'  their vehicles       {owned_cars}')
        if options['purge_orphans']:
            self.stdout.write(
                f'  orphan registrations {VehicleRegistration.objects.count() - owned_regs}')
            self.stdout.write(
                f'  orphan vehicles      {Vehicle.objects.count() - owned_cars}')

        kept = User.objects.exclude(pk__in=doomed.values('pk'))
        self.stdout.write(f'  accounts kept    {kept.count()}')
        for row in kept.order_by('role', 'email').values_list('role', 'email'):
            self.stdout.write(f'      {row[0]:<14} {row[1]}')
        self.stdout.write('')

        admins_left = 0 if options['include_admins'] else User.objects.filter(
            role=User.Role.ADMIN).count()

        fragment = (options['confirm_host'] or '').strip()
        if not fragment:
            self.stdout.write(self.style.WARNING(
                'Dry run — nothing was deleted.'))
            self.stdout.write(
                'Re-run with --confirm-host <fragment of the host above> to go ahead.')
            return

        if fragment.lower() not in host.lower():
            raise CommandError(
                f'Refusing to delete anything.\n'
                f'  --confirm-host {fragment!r}\n'
                f'  actually connected to {host!r}\n'
                f'Those do not match, which usually means DATABASE_URL is not the '
                f'database you think it is.')

        if not options['include_admins'] and admins_left == 0:
            raise CommandError(
                'Refusing to delete anything: this database has no admin account, '
                'so the deletion would leave nobody able to log in. Create one '
                'first, or pass --include-admins if that is really what you want.')

        # One transaction: a half-cleared database is worse than an uncleared
        # one, because the leftovers still hold the plate/email/licence
        # uniqueness that the next test registration needs released.
        leftover_regs = leftover_cars = 0
        with transaction.atomic():
            vehicles, registrations, accounts = delete_users_with_owned_records(doomed)
            if options['purge_orphans']:
                # Rows nobody owned — rejected applications never linked to an
                # account. The sweep above only reaches what a deleted user
                # pointed at.
                leftover_regs, _ = VehicleRegistration.objects.filter(user__isnull=True).delete()
                leftover_cars, _ = Vehicle.objects.filter(user__isnull=True).delete()

        self.stdout.write(self.style.SUCCESS('Cleared.'))
        self.stdout.write(f'  accounts deleted       {accounts}')
        self.stdout.write(f'  registrations deleted  {registrations + leftover_regs}')
        self.stdout.write(f'  vehicles deleted       {vehicles + leftover_cars}')
        self.stdout.write('')
        self.stdout.write('Audit history was preserved — AuditLog.actor and '
                          '.target_user are SET_NULL by design.')
