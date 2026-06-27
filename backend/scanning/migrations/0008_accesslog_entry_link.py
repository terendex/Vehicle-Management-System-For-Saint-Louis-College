import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('scanning', '0007_accesslog_override'),
    ]

    operations = [
        migrations.AddField(
            model_name='accesslog',
            name='paired_entry',
            field=models.ForeignKey(
                blank=True,
                help_text='For exit logs: points to the matching entry log.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='exit_log',
                to='scanning.accesslog',
            ),
        ),
    ]
