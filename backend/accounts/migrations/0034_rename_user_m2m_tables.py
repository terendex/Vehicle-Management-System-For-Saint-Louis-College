"""Rename the User M2M through-tables to match `User.db_table = 'tbl_user'`.

Migration 0025 renamed the user table to `tbl_user` with AlterModelTable, but the
two auto-created through-tables for the inherited `groups` and `user_permissions`
fields were left behind as `accounts_user_groups` / `accounts_user_user_permissions`.

Django derives an auto-created M2M table name from the model's db_table at class
definition time, so at runtime it looks for `tbl_user_groups` — a table that does
not exist. Nothing in the app reads those fields (access control is the `role`
column), so the mismatch stayed invisible until something asked the ORM to walk
every relation on User. `dumpdata` does exactly that, which is why Backup &
Restore failed with:

    Unable to serialize database: relation "tbl_user_groups" does not exist

This is a database-only correction: the migration *state* already believes the
tables are named `tbl_user_*`, because that name is computed from db_table. Using
AlterField/RenameModel here would desynchronise the state from itself, so the
rename is issued as raw SQL with no state operations.

Both tables are empty in every known deployment (the app has never used Django
groups or permissions), so the rename moves no rows. It is written to be safe to
run against a database that is already correct — a fresh install creates the
tables under the right names, in which case each statement is a no-op.
"""
from django.db import migrations


def _rename(old, new):
    """Rename only when the old table exists and the new one does not.

    Guarded rather than a bare ALTER: this migration has to be a no-op on a
    freshly created database (where Django already made `new`) and on one that
    has been fixed by hand, without failing the deploy either way.
    """
    return f"""
    DO $$
    BEGIN
        IF to_regclass('public.{old}') IS NOT NULL
           AND to_regclass('public.{new}') IS NULL THEN
            ALTER TABLE public.{old} RENAME TO {new};
        END IF;
    END $$;
    """


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0033_schedule_choice_labels'),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                _rename('accounts_user_groups', 'tbl_user_groups'),
                _rename('accounts_user_user_permissions', 'tbl_user_user_permissions'),
            ],
            reverse_sql=[
                _rename('tbl_user_groups', 'accounts_user_groups'),
                _rename('tbl_user_user_permissions', 'accounts_user_user_permissions'),
            ],
            # The names Django computes from db_table are already what the
            # migration state holds; only the database is out of step.
            state_operations=[],
        ),
    ]
