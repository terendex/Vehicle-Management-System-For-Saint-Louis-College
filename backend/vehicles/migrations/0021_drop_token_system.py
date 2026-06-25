from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0020_schema_part2_drop_old_tables'),
    ]

    operations = [
        # Drop RegistrationToken table
        migrations.DeleteModel(
            name='RegistrationToken',
        ),

        # Remove token column from VehicleRegistration
        migrations.RemoveField(
            model_name='vehicleregistration',
            name='token',
        ),

        # Update source choices: token → public
        migrations.AlterField(
            model_name='vehicleregistration',
            name='source',
            field=models.CharField(
                default='public',
                max_length=20,
                choices=[('public', 'Online/Public Form'), ('direct', 'CDSO Walk-in')],
            ),
        ),
    ]
