import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('scanning', '0005_accesslog_digital_id_used'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # Guard who issued the pass
        migrations.AddField(
            model_name='visitorpass',
            name='issued_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='issued_passes',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        # Timestamp when the visitor was scanned out
        migrations.AddField(
            model_name='visitorpass',
            name='exited_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        # Plate number stored directly for quick display (denormalised)
        migrations.AddField(
            model_name='visitorpass',
            name='plate_number',
            field=models.CharField(blank=True, max_length=20, default=''),
            preserve_default=False,
        ),
        # Office is now optional — not every visitor has a specific destination
        migrations.AlterField(
            model_name='visitorpass',
            name='office',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to='scanning.office',
            ),
        ),
        # Purpose is now optional
        migrations.AlterField(
            model_name='visitorpass',
            name='purpose',
            field=models.TextField(blank=True),
        ),
        # Replace status choices: active | exited | expired
        migrations.AlterField(
            model_name='visitorpass',
            name='status',
            field=models.CharField(
                default='active',
                max_length=20,
                choices=[
                    ('active',  'Active'),
                    ('exited',  'Exited'),
                    ('expired', 'Expired'),
                ],
            ),
        ),
        # Rename created_at → entered_at to reflect the new semantics
        migrations.RenameField(
            model_name='visitorpass',
            old_name='created_at',
            new_name='entered_at',
        ),
        # Remove office-confirmation field — guards issue passes directly
        migrations.RemoveField(
            model_name='visitorpass',
            name='confirmed_by',
        ),
    ]
