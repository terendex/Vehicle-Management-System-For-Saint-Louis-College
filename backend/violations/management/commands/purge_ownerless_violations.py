"""Delete violations that have no owner account behind them.

Deliberately a command you run, not a job that runs itself.

A violation is ownerless in two very different ways:

  * its owner's account was deleted, or its vehicle row is gone — history left
    behind with nobody attached to it, which is what this is for;
  * it never had an owner at all. Visitor and supplier overstays are logged
    against a plate with no account anywhere in the system.

The second group is why this is not wired into the nightly maintenance run. On
a schedule it would delete every visitor overstay within a day of it being
issued — the violation would be created, emailed about, and swept away before
anyone acted on it, which makes logging them pointless. Run manually you can see
the count first and decide.

Dry run by default. Nothing is deleted without --apply.

    python manage.py purge_ownerless_violations                 # report only
    python manage.py purge_ownerless_violations --apply         # deleted accounts only
    python manage.py purge_ownerless_violations --apply --include-never-owned
"""
from django.core.management.base import BaseCommand
from django.db.models import Q


class Command(BaseCommand):
    help = "Delete violations with no owner account. Dry run unless --apply is given."

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='Actually delete. Without this the command only reports.',
        )
        parser.add_argument(
            '--include-never-owned', action='store_true',
            help='Also delete violations issued against a plate that never had an '
                 'account — visitor and supplier overstays. Off by default.',
        )

    def handle(self, *args, **options):
        from violations.models import Violation

        apply_it     = options['apply']
        include_never = options['include_never_owned']

        # Lost its owner: the vehicle row is gone, or it survives but is no
        # longer linked to any account.
        lost = Violation.objects.filter(Q(vehicle__isnull=True) | Q(vehicle__user__isnull=True))

        # Of those, the ones that were issued against an owner at the time —
        # the snapshot is what still remembers there was one.
        orphaned     = lost.exclude(owner_name='')
        never_owned  = lost.filter(owner_name='')

        self.stdout.write(f"orphaned (owner account since removed): {orphaned.count()}")
        self.stdout.write(f"never owned (visitor / supplier)      : {never_owned.count()}")

        target = lost if include_never else orphaned
        total  = target.count()

        if not total:
            self.stdout.write(self.style.SUCCESS('Nothing to delete.'))
            return

        # Name them before deleting. A destructive sweep whose output is only a
        # number gives nobody a way to tell afterwards what went.
        self.stdout.write('')
        for v in target.order_by('-issued_at')[:50]:
            self.stdout.write(
                f"  {v.identifier or '(no plate)':<12} {v.violation_type:<22}"
                f" {v.owner_name or '(never owned)':<28} issued {v.issued_at:%Y-%m-%d}")
        if total > 50:
            self.stdout.write(f"  ... and {total - 50} more")
        self.stdout.write('')

        if not apply_it:
            self.stdout.write(self.style.WARNING(
                f"DRY RUN — {total} violation(s) would be deleted. "
                f"Re-run with --apply to delete them."))
            return

        deleted, _ = target.delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} violation row(s)."))
