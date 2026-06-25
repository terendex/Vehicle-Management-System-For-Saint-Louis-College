from django.db import migrations


def remove_ebike_data(apps, schema_editor):
    RuleConstraint = apps.get_model('vehicles', 'RuleConstraint')
    Vehicle = apps.get_model('vehicles', 'Vehicle')

    # Delete student_ebike rule constraints
    RuleConstraint.objects.filter(constraint_type='student_ebike').delete()

    # Reassign ebike vehicles to motorcycle
    Vehicle.objects.filter(vehicle_type='ebike').update(vehicle_type='motorcycle')


def reverse_remove_ebike_data(apps, schema_editor):
    pass  # intentionally not restoring deleted data


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0024_normalize_part2'),
    ]

    operations = [
        migrations.AlterField(
            model_name='ruleconstraint',
            name='constraint_type',
            field=__import__('django.db.models', fromlist=['CharField']).CharField(
                max_length=20,
                choices=[
                    ('student_vehicle', 'Student — Vehicle'),
                    ('employee',        'Employee'),
                    ('fetcher',         'Fetcher / Drop & Go'),
                ],
            ),
        ),
        migrations.RunPython(remove_ebike_data, reverse_remove_ebike_data),
    ]
