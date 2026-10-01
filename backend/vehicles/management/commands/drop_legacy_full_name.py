"""Finish the name split: drop the leftover full_name columns.

Migrations accounts 0040 and vehicles 0094 split every name into last_name /
first_name / middle_initial and took full_name off the models, but left the
two COLUMNS in place (nullable). A campus install still running the previous
code reads and writes them; dropping them under it would take it down.

Run this once every server is on the new code (Railway deployed, campus
updated):

    python manage.py drop_legacy_full_name            # report only
    python manage.py drop_legacy_full_name --apply    # backfill, then drop

It first splits any row the old code created or renamed in the meantime (its
parts are blank while full_name is not), then drops the columns. Safe to run
again: a column already gone is reported and skipped.
"""
from django.core.management.base import BaseCommand
from django.db import connection, transaction

from accounts.names import split_full_name

TABLES = ('tbl_user', 'tbl_vehicle_registration')


def _has_column(cursor, table):
    cursor.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = %s AND column_name = 'full_name'",
        [table],
    )
    return cursor.fetchone() is not None


class Command(BaseCommand):
    help = "Backfill names written by older code, then drop the legacy full_name columns."

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Actually backfill and drop. Without it, only report.')

    def handle(self, *args, apply=False, **options):
        with transaction.atomic(), connection.cursor() as cursor:
            for table in TABLES:
                if not _has_column(cursor, table):
                    self.stdout.write(f'{table}: full_name already dropped')
                    continue
                # Rows whose parts are empty but whose old column has a name:
                # written by the previous code after the split migration ran.
                pk = 'user_id' if table == 'tbl_user' else 'vehicle_registration_id'
                cursor.execute(
                    f"SELECT {pk}, full_name FROM {table} "
                    f"WHERE last_name = '' AND first_name = '' "
                    f"AND COALESCE(full_name, '') <> ''")
                pending = cursor.fetchall()
                self.stdout.write(f'{table}: {len(pending)} row(s) to split'
                                  + ('' if apply else ' (dry run)'))
                if not apply:
                    continue
                for row_pk, full_name in pending:
                    last, first, initial = split_full_name(full_name)
                    cursor.execute(
                        f"UPDATE {table} SET last_name = %s, first_name = %s, middle_initial = %s "
                        f"WHERE {pk} = %s", [last, first, initial, row_pk])
                cursor.execute(f"ALTER TABLE {table} DROP COLUMN full_name")
                self.stdout.write(self.style.SUCCESS(f'{table}: full_name dropped'))
        if not apply:
            self.stdout.write('Nothing changed. Re-run with --apply once every server runs the new code.')
