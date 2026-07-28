"""Re-apply the tbl_* table naming on reverted databases.

See accounts/0027_restore_tbl_naming for why this is raw SQL with no state
operations.
"""
from django.db import migrations

TABLES = [
    ('violations_violation', 'tbl_violation'),
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
        ('violations', '0010_violation_violation_vehicle_type_time_and_more'),
    ]

    operations = [
        migrations.RunSQL(
            sql=rename_sql(TABLES),
            reverse_sql=rename_sql([(new, old) for old, new in TABLES]),
            state_operations=[],
        ),
    ]
