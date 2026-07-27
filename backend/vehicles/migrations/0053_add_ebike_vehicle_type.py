from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0053_vehicleregistration_vehreg_created_at_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='vehicle',
            name='vehicle_type',
            field=models.CharField(
                choices=[
                    ('car', 'Car'),
                    ('motorcycle', 'Motorcycle'),
                    ('ebike', 'E-Bike'),
                    ('truck', 'Truck'),
                    ('van', 'Van'),
                    ('bus', 'Bus'),
                ],
                default='car',
                max_length=20,
            ),
        ),
    ]
