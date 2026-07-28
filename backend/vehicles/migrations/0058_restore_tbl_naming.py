"""Re-apply the tbl_* table and <table>_id column naming on reverted databases.

See accounts/0027_restore_tbl_naming for why this is raw SQL with no state
operations. Six primary keys were also shortened by the reverted migrations
and are restored here. ScheduledVisit is intentionally absent — it was added
after the revert and already carries the declared naming.
"""
from django.db import migrations

TABLES = [
    ('vehicles_camera', 'tbl_camera'),
    ('vehicles_event', 'tbl_event'),
    ('vehicles_parkingnotice', 'tbl_parking_notice'),
    ('vehicles_parkingspace', 'tbl_parking_space'),
    ('vehicles_parkingzone', 'tbl_parking_zone'),
    ('vehicles_referenceitem', 'tbl_reference_item'),
    ('vehicles_registrationperiod', 'tbl_registration_period'),
    ('vehicles_ruleconstraint', 'tbl_rule_constraint'),
    ('vehicles_supplier', 'tbl_supplier'),
    ('vehicles_supplierplate', 'tbl_supplier_plate'),
    ('vehicles_systemsettings', 'tbl_system_settings'),
    ('vehicles_vehicle', 'tbl_vehicle'),
    ('vehicles_vehicleregistration', 'tbl_vehicle_registration'),
]

COLUMNS = [
    ('tbl_vehicle_registration', 'registration_id', 'vehicle_registration_id'),
    ('tbl_parking_zone', 'zone_id', 'parking_zone_id'),
    ('tbl_system_settings', 'settings_id', 'system_settings_id'),
    ('tbl_registration_period', 'period_id', 'registration_period_id'),
    ('tbl_parking_notice', 'notice_id', 'parking_notice_id'),
    ('tbl_parking_space', 'space_id', 'parking_space_id'),
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
        ('vehicles', '0057_scheduledvisit_pk_column'),
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
