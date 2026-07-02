from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


def bump_unchanged_default(apps, schema_editor):
    """Existing singleton rows still on the old default (30s) move to the new one (60s)."""
    SystemSettings = apps.get_model('vehicles', 'SystemSettings')
    SystemSettings.objects.filter(scan_dedup_seconds=30).update(scan_dedup_seconds=60)


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0042_event_archived'),
    ]

    operations = [
        migrations.AlterField(
            model_name='systemsettings',
            name='scan_dedup_seconds',
            field=models.IntegerField(default=60, validators=[MinValueValidator(5), MaxValueValidator(300)]),
        ),
        migrations.RunPython(bump_unchanged_default, migrations.RunPython.noop),
    ]
