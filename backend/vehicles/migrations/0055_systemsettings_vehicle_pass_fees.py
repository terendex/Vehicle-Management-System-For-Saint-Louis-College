from django.core.validators import MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0054_vehicleregistration_drivers_license_image'),
    ]

    operations = [
        migrations.AddField(
            model_name='systemsettings',
            name='vehicle_pass_fee',
            field=models.DecimalField(
                decimal_places=2, default=300, max_digits=8,
                validators=[MinValueValidator(0)],
                help_text='Vehicle Pass registration fee (₱) for students and fetchers.',
            ),
        ),
        migrations.AddField(
            model_name='systemsettings',
            name='vehicle_pass_fee_employee',
            field=models.DecimalField(
                decimal_places=2, default=150, max_digits=8,
                validators=[MinValueValidator(0)],
                help_text='Vehicle Pass registration fee (₱) for employees.',
            ),
        ),
    ]
