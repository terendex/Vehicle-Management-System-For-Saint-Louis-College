from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('scanning', '0006_visitorpass_thermal_flow'),
    ]

    operations = [
        migrations.AddField(
            model_name='accesslog',
            name='is_override',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='accesslog',
            name='override_reason',
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
