"""Respell "offence" as "offense" in text already stored by the gate and the
penalty code, so old rows read the same as the ones written from now on.

Covers every column the code writes that wording into. Only the spelling
changes; the rest of each message is left as it was. Not reversed: putting
the old spelling back would also catch rows that were always "offense".
"""
from django.db import migrations
from django.db.models import F, Value
from django.db.models.functions import Replace

FIELDS = [
    ('scanning', 'AccessLog', 'denied_reason'),
    ('violations', 'Violation', 'notes'),
    ('accounts', 'User', 'confiscation_reason'),
    ('accounts', 'Notification', 'title'),
    ('accounts', 'Notification', 'message'),
]


def respell(apps, schema_editor):
    for app_label, model_name, field in FIELDS:
        model = apps.get_model(app_label, model_name)
        fixed = Replace(Replace(F(field), Value('Offence'), Value('Offense')),
                        Value('offence'), Value('offense'))
        model.objects.filter(**{f'{field}__icontains': 'offence'}).update(**{field: fixed})


class Migration(migrations.Migration):

    dependencies = [
        ('violations', '0019_violation_status_archived'),
        ('scanning', '0030_relabel_fetcher_driver'),
        ('accounts', '0043_remove_user_username'),
    ]

    operations = [
        migrations.RunPython(respell, migrations.RunPython.noop),
    ]
