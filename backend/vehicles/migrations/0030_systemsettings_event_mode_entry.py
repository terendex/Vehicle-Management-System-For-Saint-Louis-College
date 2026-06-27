from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0029_alter_parkingnotice_id_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='systemsettings',
            name='event_mode_entry',
            field=models.BooleanField(
                default=False,
                help_text='When enabled, guards can override denied entry scans at the gate.',
            ),
        ),
    ]
