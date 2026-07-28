"""Re-apply the tbl_* table naming to databases that were reverted.

A set of migrations on another branch renamed these tables back to Django's
app_model defaults and were applied to the shared database. Those migrations
have since been removed from the tree, so Django's migration state already
believes the tbl_* names are in place — only the database disagrees.

That is why every operation here is raw SQL with `state_operations=[]`: there
is no model state to change, just physical objects to rename. Each rename is
guarded on the source name existing, so this is a no-op on a database that is
already correct (including a freshly migrated one).
"""
from django.db import migrations

# legacy default name -> the name the models declare
TABLES = [
    ('accounts_user', 'tbl_user'),
    ('accounts_auditlog', 'tbl_audit_log'),
    ('accounts_notification', 'tbl_notification'),
]


def rename_sql(pairs):
    return '\n'.join(
        f"""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.tables
                       WHERE table_schema = 'public' AND table_name = '{old}')
               AND NOT EXISTS (SELECT 1 FROM information_schema.tables
                               WHERE table_schema = 'public' AND table_name = '{new}')
            THEN ALTER TABLE public."{old}" RENAME TO "{new}";
            END IF;
        END $$;"""
        for old, new in pairs
    )


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0026_auditlog_auditlog_created_at_and_more'),
    ]

    operations = [
        migrations.RunSQL(
            sql=rename_sql(TABLES),
            reverse_sql=rename_sql([(new, old) for old, new in TABLES]),
            state_operations=[],
        ),
    ]
