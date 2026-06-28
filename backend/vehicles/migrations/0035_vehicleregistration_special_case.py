from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0034_vehicleregistration_vehicle'),
    ]

    operations = [
        migrations.AddField(
            model_name='vehicleregistration',
            name='is_special_case',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='vehicleregistration',
            name='special_case_reason',
            field=models.TextField(blank=True),
        ),
    ]
