"""Finish removing username: drop the leftover tbl_user.username column.

Migration accounts 0043 took username off the User model (sign-in is by email,
the name is last_name / first_name / middle_initial) but left the COLUMN in
place, nullable. A campus install still running the previous code selects it
on every account query; dropping it under that install would take it down.

Run this once every server is on the new code (Railway deployed, campus
updated):

    python manage.py drop_legacy_username            # report only
    python manage.py drop_legacy_username --apply    # drop the column

Nothing needs copying first: the column never held anything the system reads.
Safe to run again: a column already gone is reported and skipped.
"""
from django.core.management.base import BaseCommand
from django.db import connection, transaction


def _has_column(cursor):
    cursor.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = 'tbl_user' "
        "AND column_name = 'username'")
    return cursor.fetchone() is not None


class Command(BaseCommand):
    help = "Drop the unused tbl_user.username column once every server runs the new code."

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Actually drop the column. Without it, only report.')

    def handle(self, *args, apply=False, **options):
        with transaction.atomic(), connection.cursor() as cursor:
            if not _has_column(cursor):
                self.stdout.write('tbl_user: username already dropped')
                return
            cursor.execute("SELECT COUNT(*) FROM tbl_user WHERE COALESCE(username, '') <> ''")
            filled = cursor.fetchone()[0]
            self.stdout.write(f'tbl_user: username column present, {filled} row(s) with a value '
                              '(unused by the system)')
            if not apply:
                self.stdout.write('Nothing changed. Re-run with --apply once every server runs the new code.')
                return
            cursor.execute("ALTER TABLE tbl_user DROP COLUMN username")
            self.stdout.write(self.style.SUCCESS('tbl_user: username dropped'))
