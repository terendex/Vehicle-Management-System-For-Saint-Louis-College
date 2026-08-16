"""Owner accounts follow vehicles.0072: the TTHS rotation becomes TTHF.

Same two moves — restate the choices ('TTHS' dropped, 'Any Day' / 'All Days'
spelled out as the week they really cover) and carry existing rows across, days
included, so an account's schedule code and its campus_days agree.
"""
from django.db import migrations, models

OLD_DAYS = ['Tuesday', 'Thursday', 'Saturday']
NEW_DAYS = ['Tuesday', 'Thursday', 'Friday']


def tths_becomes_tthf(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    for user in User.objects.filter(schedule='TTHS'):
        user.schedule = 'TTHF'
        user.campus_days = [NEW_DAYS[OLD_DAYS.index(d)] if d in OLD_DAYS else d
                            for d in (user.campus_days or [])]
        user.save(update_fields=['schedule', 'campus_days'])


def tthf_becomes_tths(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    for user in User.objects.filter(schedule='TTHF'):
        user.schedule = 'TTHS'
        user.campus_days = [OLD_DAYS[NEW_DAYS.index(d)] if d in NEW_DAYS else d
                            for d in (user.campus_days or [])]
        user.save(update_fields=['schedule', 'campus_days'])


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0032_user_confiscated_at_user_confiscated_until_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='schedule',
            field=models.CharField(
                blank=True,
                choices=[
                    ('MWF', 'Monday-Wednesday-Friday'),
                    ('TTHF', 'Tuesday-Thursday-Friday'),
                    ('MIXED', 'Custom / Mixed Days'),
                    ('ANY', 'Any Campus Day (Monday-Saturday)'),
                    ('ALL', 'All Campus Days (Monday-Saturday)'),
                ],
                max_length=10,
                null=True,
            ),
        ),
        migrations.RunPython(tths_becomes_tthf, tthf_becomes_tths),
    ]
