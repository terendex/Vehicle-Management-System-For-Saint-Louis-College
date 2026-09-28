"""Run the daily maintenance jobs without Celery.

These jobs are defined as Celery tasks and scheduled in
`config/celery.py`'s beat_schedule, which means they only run when a Celery
worker AND a beat scheduler are running. The Railway deployment has neither.
The server now runs every one of them itself on a daily in-process thread
(vehicles/scheduler.py), so this command is for running them on demand.

A Celery task object is directly callable and executes its body in-process, so
this needs no broker and no worker:

    python manage.py run_maintenance

The jobs key off the campus (Manila) date from Django's TIME_ZONE, not the OS
clock, so it gives the same answer on a UTC container as on the campus PC.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run the daily purge + event rollover jobs synchronously (no Celery needed)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--skip-purge',
            action='store_true',
            help="Only roll events over; do not delete anything past the retention window.",
        )

    def handle(self, *args, **options):
        from vehicles.tasks import (auto_backup, auto_manage_events,
                                    auto_archive_expired_accounts, auto_archive_past_visits,
                                    purge_old_records)

        # Events first: it is the job with visible consequences, and it should
        # still run even if the purge fails.
        result = auto_manage_events()
        self.stdout.write(self.style.SUCCESS(
            f"auto_manage_events: activated {result['activated']}, archived {result['archived']}, "
            f"reset {result['pending']} to pending"
        ))

        # Before anything that deletes: the day's snapshot should still hold
        # the rows the purge below is about to remove. Respects the configured
        # frequency, so this is a no-op when automatic backups are off or the
        # last one is still recent.
        result = auto_backup()
        self.stdout.write(self.style.SUCCESS(
            f"auto_backup: {result.get('created') or result.get('skipped')}"
        ))

        result = auto_archive_expired_accounts()
        self.stdout.write(self.style.SUCCESS(
            f"auto_archive_expired_accounts: archived {result.get('archived', 0)} expired owner account(s)"
        ))

        result = auto_archive_past_visits()
        self.stdout.write(self.style.SUCCESS(
            f"auto_archive_past_visits: archived {result.get('archived', 0)} past scheduled visit(s)"
        ))

        if options['skip_purge']:
            self.stdout.write("purge_old_records: skipped (--skip-purge)")
            return

        result = purge_old_records()
        self.stdout.write(self.style.SUCCESS(
            f"purge_old_records: deleted {result['deleted_logs']} access logs, "
            f"{result['deleted_violations']} violations, "
            f"{result['deleted_accounts']} archived accounts"
        ))
