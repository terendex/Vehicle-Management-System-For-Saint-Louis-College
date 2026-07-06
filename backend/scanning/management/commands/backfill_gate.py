"""One-off: reassign orphaned AccessLog rows from one gate_id to another.

Historically, guard logins did not persist gate_assignment, so manual/override/
exit scans were logged with gate_id='main' and never appeared in any gate's
Vehicle Log. The only entry camera has always been Gate 1 and Gate 4 has never
had activity, so those 'main' rows are Gate 1 history.

Usage (dry-run by default — shows counts, changes nothing):
    python manage.py backfill_gate

Apply the change (writes a JSON rollback file of affected ids next to manage.py):
    python manage.py backfill_gate --apply

Custom mapping / rollback:
    python manage.py backfill_gate --from gate1 --to main --apply
"""
import json
import datetime

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count

from scanning.models import AccessLog


class Command(BaseCommand):
    help = "Reassign AccessLog.gate_id from one value to another (default main -> gate1)."

    def add_arguments(self, parser):
        parser.add_argument('--from', dest='src', default='main',
                            help="Source gate_id to reassign (default: main)")
        parser.add_argument('--to', dest='dst', default='gate1',
                            help="Target gate_id (default: gate1)")
        parser.add_argument('--apply', action='store_true',
                            help="Actually perform the update. Omit for a dry run.")

    def handle(self, *args, **opts):
        src, dst, apply = opts['src'], opts['dst'], opts['apply']

        qs = AccessLog.objects.filter(gate_id=src)
        ids = list(qs.values_list('id', flat=True))
        self.stdout.write(f"Rows with gate_id={src!r}: {len(ids)}")

        if not ids:
            self.stdout.write(self.style.WARNING("Nothing to do."))
            return

        if not apply:
            self.stdout.write(self.style.WARNING(
                "DRY RUN — no changes made. Re-run with --apply to reassign "
                f"these {len(ids)} rows to gate_id={dst!r}."))
            return

        stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        rollback_path = f"backfill_gate_{src}_to_{dst}_{stamp}.json"
        with open(rollback_path, 'w') as f:
            json.dump({'reassigned_at': datetime.datetime.now().isoformat(),
                       'from': src, 'to': dst, 'ids': ids}, f)
        self.stdout.write(f"Rollback file written: {rollback_path}")

        with transaction.atomic():
            n = AccessLog.objects.filter(gate_id=src).update(gate_id=dst)
        self.stdout.write(self.style.SUCCESS(f"Reassigned {n} rows: {src!r} -> {dst!r}"))

        self.stdout.write("New gate_id distribution:")
        for row in AccessLog.objects.values('gate_id').annotate(c=Count('id')).order_by('-c'):
            self.stdout.write(f"  {row['gate_id']!r} -> {row['c']}")
