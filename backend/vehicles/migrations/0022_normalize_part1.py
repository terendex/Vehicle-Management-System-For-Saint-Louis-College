import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0021_drop_token_system'),
    ]

    operations = [
        # Add temp FK column (can't reuse name 'department' while CharField still exists)
        migrations.AddField(
            model_name='vehicleregistration',
            name='department_fk',
            field=models.ForeignKey(
                blank=True, null=True,
                limit_choices_to={'category': 'department'},
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='employee_registrations',
                to='vehicles.referenceitem',
            ),
        ),
        # Drop the redundant column — category is derivable via space.zone.vehicle_category
        migrations.RemoveField(
            model_name='parkingspace',
            name='vehicle_category',
        ),
        migrations.AlterModelOptions(
            name='parkingspace',
            options={'ordering': ['zone__vehicle_category', 'space_number']},
        ),
    ]
