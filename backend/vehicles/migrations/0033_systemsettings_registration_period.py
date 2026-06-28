from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0032_merge_20260627_1813'),
    ]

    operations = [
        migrations.AddField(
            model_name='systemsettings',
            name='registration_start',
            field=models.DateField(
                blank=True,
                null=True,
                help_text='First day vehicle registrations are accepted.',
            ),
        ),
        migrations.AddField(
            model_name='systemsettings',
            name='registration_end',
            field=models.DateField(
                blank=True,
                null=True,
                help_text='Last day vehicle registrations are accepted.',
            ),
        ),
    ]
