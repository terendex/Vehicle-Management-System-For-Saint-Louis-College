import uuid
from django.db import migrations, models


def assign_unique_qr_tokens(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    for user in User.objects.filter(qr_token__isnull=True):
        user.qr_token = uuid.uuid4()
        user.save(update_fields=['qr_token'])


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0009_add_owner_profile_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='gate_assignment',
            field=models.CharField(
                blank=True, null=True, max_length=10,
                choices=[('gate1', 'Gate 1'), ('gate4', 'Gate 4')],
            ),
        ),
        # Step 1: add as nullable so existing rows don't conflict
        migrations.AddField(
            model_name='user',
            name='qr_token',
            field=models.UUIDField(null=True, blank=True),
        ),
        # Step 2: populate unique tokens for all existing users
        migrations.RunPython(assign_unique_qr_tokens, migrations.RunPython.noop),
        # Step 3: make non-null, unique, with a default for future rows
        migrations.AlterField(
            model_name='user',
            name='qr_token',
            field=models.UUIDField(default=uuid.uuid4, unique=True),
        ),
    ]
