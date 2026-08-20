from django.db import migrations

# Migration 0015 meant every entry type to have a rule, but it only *created*
# the fetcher and student_ebike rows — student_vehicle came from renaming an
# existing 'student' row, which a fresh database never has, and employee and
# supplier were never seeded at all. The gate reads a missing rule as "no
# restriction", so a new deployment enforced no day or time window on three of
# the four entry types until somebody opened Rule Constraints and saved.
#
# Only fills the gaps: an existing deployment already has these rows and is
# left exactly as it stands, including any edits made to them.
DEFAULTS = [
    ('student_vehicle', 'Student Vehicle Schedule',  '06:00', '19:00', None),
    ('employee',        'Standard Employee Shift',   '06:00', '20:00', None),
    ('fetcher',         'Fetcher / Drop & Go Schedule', '06:00', '19:00', 15),
    ('supplier',        'Supplier Delivery Window',  '06:00', '19:00', 15),
]

CAMPUS_DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat']


def seed_missing(apps, schema_editor):
    RuleConstraint = apps.get_model('vehicles', 'RuleConstraint')
    for constraint_type, name, start, end, max_stay in DEFAULTS:
        if RuleConstraint.objects.filter(constraint_type=constraint_type).exists():
            continue
        RuleConstraint.objects.create(
            name=name,
            constraint_type=constraint_type,
            days=list(CAMPUS_DAYS),
            start_time=start,
            end_time=end,
            max_stay_minutes=max_stay,
            enabled=True,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0073_systemsettings_auto_backup_frequency_and_more'),
    ]

    operations = [
        migrations.RunPython(seed_missing, migrations.RunPython.noop),
    ]
