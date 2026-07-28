"""Re-apply the tbl_* table and <table>_id column naming on reverted databases.

See accounts/0027_restore_tbl_naming for why this is raw SQL with no state
operations. Three primary keys were also shortened by the reverted migrations
(shift_id, sample_id, record_id) and are restored here.
"""
from django.db import migrations

TABLES = [
    ('scanning_accesslog', 'tbl_access_log'),
    ('scanning_gate', 'tbl_gate'),
    ('scanning_guardshift', 'tbl_guard_shift'),
    ('scanning_mltrainingsample', 'tbl_ml_training_sample'),
    ('scanning_office', 'tbl_office'),
    ('scanning_platerecognitionrecord', 'tbl_plate_recognition_record'),
    ('scanning_visitorpass', 'tbl_visitor_pass'),
]

# table (post-rename) -> (legacy column, declared column)
COLUMNS = [
    ('tbl_guard_shift', 'shift_id', 'guard_shift_id'),
    ('tbl_ml_training_sample', 'sample_id', 'ml_training_sample_id'),
    ('tbl_plate_recognition_record', 'record_id', 'plate_recognition_record_id'),
]


def rename_tables(pairs):
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


def rename_columns(triples):
    return '\n'.join(
        f"""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_schema = 'public' AND table_name = '{table}'
                         AND column_name = '{old}')
               AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                               WHERE table_schema = 'public' AND table_name = '{table}'
                                 AND column_name = '{new}')
            THEN ALTER TABLE public."{table}" RENAME COLUMN "{old}" TO "{new}";
            END IF;
        END $$;"""
        for table, old, new in triples
    )


class Migration(migrations.Migration):

    dependencies = [
        ('scanning', '0018_accesslog_accesslog_plate_status_time_and_more'),
    ]

    operations = [
        migrations.RunSQL(
            sql=rename_tables(TABLES) + rename_columns(COLUMNS),
            reverse_sql=(
                rename_columns([(t, new, old) for t, old, new in COLUMNS])
                + rename_tables([(new, old) for old, new in TABLES])
            ),
            state_operations=[],
        ),
    ]
