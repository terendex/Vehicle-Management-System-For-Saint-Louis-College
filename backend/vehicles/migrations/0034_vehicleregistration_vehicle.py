from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0033_systemsettings_registration_period'),
    ]

    operations = [
        migrations.AddField(
            model_name='vehicleregistration',
            name='vehicle',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='registrations',
                to='vehicles.vehicle',
            ),
        ),
    ]
