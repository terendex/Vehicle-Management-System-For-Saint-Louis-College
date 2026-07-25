from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0052_alter_parkingnotice_id_alter_parkingspace_id_and_more'),
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
