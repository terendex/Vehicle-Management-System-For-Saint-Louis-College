"""Show, and push out by hand, the mail waiting in the retry outbox.

The server retries failed mail on its own (vehicles/email_outbox.py). This is
for when you do not want to wait: right after fixing a Brevo key or getting the
campus connection back, or to revive messages the outbox already gave up on.

    python manage.py retry_emails                    # list what is queued
    python manage.py retry_emails --now              # retry everything pending, now
    python manage.py retry_emails --include-failed   # ...and the ones it gave up on

On Railway: `railway run python backend/manage.py retry_emails --now`.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from vehicles import email_outbox
from vehicles.models import EmailOutbox


class Command(BaseCommand):
    help = "List the email retry queue, or retry it immediately."

    def add_arguments(self, parser):
        parser.add_argument('--now', action='store_true',
                            help="Retry every pending message now instead of on schedule.")
        parser.add_argument('--include-failed', action='store_true',
                            help="Also revive messages the outbox gave up on (implies --now). "
                                 "Password-reset mail is skipped: its link has expired.")

    def handle(self, *args, **opts):
        now = timezone.now()
        if opts['include_failed']:
            revived = 0
            for row in EmailOutbox.objects.filter(status=EmailOutbox.Status.FAILED):
                if (row.message or {}).get('hard_deadline'):
                    continue
                row.status = EmailOutbox.Status.PENDING
                row.expires_at = now + email_outbox.max_age()
                row.save(update_fields=['status', 'expires_at'])
                revived += 1
            self.stdout.write(f"revived {revived} given-up message(s)")
            opts['now'] = True

        if opts['now']:
            EmailOutbox.objects.filter(status=EmailOutbox.Status.PENDING).update(next_attempt_at=now)
            outcome = email_outbox.run_due()
            outcome.pop('next_in', None)
            self.stdout.write(self.style.SUCCESS(f"retry pass: {outcome}"))

        rows = list(EmailOutbox.objects.order_by('status', 'next_attempt_at'))
        if not rows:
            self.stdout.write("outbox is empty - nothing is waiting to be sent.")
            return
        self.stdout.write(f"\n{len(rows)} message(s) in the outbox:")
        for row in rows:
            when = ('gave up' if row.status == EmailOutbox.Status.FAILED
                    else f"next try {timezone.localtime(row.next_attempt_at):%Y-%m-%d %H:%M}")
            self.stdout.write(
                f"  #{row.pk}  {row.status:<7}  {row.attempts:>3} attempts  {when}\n"
                f"         {row.subject}\n"
                f"         to {row.recipients}\n"
                f"         last error: {row.last_error[:160]}")
