from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0027_parking_notice'),
    ]

    operations = [
        migrations.AddField(
            model_name='parkingzone',
            name='capacity_override',
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                help_text='Event-mode capacity override. If set, overrides the mapped space count as the effective capacity.',
            ),
        ),
        migrations.AddField(
            model_name='systemsettings',
            name='event_mode_parking',
            field=models.BooleanField(
                default=False,
                help_text='When enabled, guards can edit zone capacities and override full-parking restrictions.',
            ),
        ),
    ]
