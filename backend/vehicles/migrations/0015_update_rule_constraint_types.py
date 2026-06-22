from django.db import migrations


def migrate_constraint_types(apps, schema_editor):
    RuleConstraint = apps.get_model('vehicles', 'RuleConstraint')

    # Rename student → student_vehicle (merge both MWF+TTHS rules into one)
    student_rules = RuleConstraint.objects.filter(constraint_type='student')
    first = student_rules.first()
    if first:
        first.constraint_type = 'student_vehicle'
        first.name = 'Student Vehicle Schedule'
        first.days = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat']
        first.save()
        student_rules.exclude(pk=first.pk).delete()

    # Delete visitor rule (visitor access handled via VisitorPass, not RuleConstraint)
    RuleConstraint.objects.filter(constraint_type='visitor').delete()

    # Add student_ebike rule if missing
    if not RuleConstraint.objects.filter(constraint_type='student_ebike').exists():
        RuleConstraint.objects.create(
            name='Student E-Bike Schedule',
            constraint_type='student_ebike',
            days=['mon', 'tue', 'wed', 'thu', 'fri', 'sat'],
            start_time='06:00',
            end_time='19:00',
            enabled=True,
        )

    # Add fetcher rule if missing
    if not RuleConstraint.objects.filter(constraint_type='fetcher').exists():
        RuleConstraint.objects.create(
            name='Fetcher / Drop & Go Schedule',
            constraint_type='fetcher',
            days=['mon', 'tue', 'wed', 'thu', 'fri', 'sat'],
            start_time='06:00',
            end_time='19:00',
            enabled=True,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0014_parking_zones'),
    ]

    operations = [
        migrations.AlterField(
            model_name='ruleconstraint',
            name='constraint_type',
            field=__import__('django.db.models', fromlist=['CharField']).CharField(
                max_length=20,
                choices=[
                    ('student_vehicle', 'Student — Vehicle'),
                    ('student_ebike',   'Student — E-Bike'),
                    ('employee',        'Employee'),
                    ('fetcher',         'Fetcher / Drop & Go'),
                ],
            ),
        ),
        migrations.RunPython(migrate_constraint_types, migrations.RunPython.noop),
    ]
