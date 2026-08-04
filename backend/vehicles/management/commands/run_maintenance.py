"""Run the daily maintenance jobs without Celery.

These two jobs are defined as Celery tasks and scheduled in
`config/celery.py`'s beat_schedule, which means they only run when a Celery
worker AND a beat scheduler are running. The Railway deployment has neither —
both would be extra always-on containers — so without this command the events
list silently stops rolling over and old records are never purged.

A Celery task object is directly callable and executes its body in-process, so
this needs no broker and no worker. Point a scheduler at it once a day:

    python manage.py run_maintenance

Prefer running it on the campus machine rather than in the cloud: both jobs key
off `date.today()`, which reads the OS clock. The campus box runs on Manila
time, matching the campus day; a UTC container would roll events over about
eight hours early.
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
        from vehicles.tasks import auto_manage_events, auto_archive_expired_accounts, purge_old_records

        # Events first: it is the job with visible consequences, and it should
        # still run even if the purge fails.
        result = auto_manage_events()
        self.stdout.write(self.style.SUCCESS(
            f"auto_manage_events: activated {result['activated']}, archived {result['archived']}"
        ))

        result = auto_archive_expired_accounts()
        self.stdout.write(self.style.SUCCESS(
            f"auto_archive_expired_accounts: archived {result.get('archived', 0)} expired owner account(s)"
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
