from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0053_add_ebike_vehicle_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='vehicleregistration',
            name='drivers_license_image',
            field=models.ImageField(blank=True, null=True, upload_to='licenses/'),
        ),
    ]
