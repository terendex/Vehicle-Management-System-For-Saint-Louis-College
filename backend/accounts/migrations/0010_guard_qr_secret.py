import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0009_add_owner_profile_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='guard_qr_secret',
            field=models.UUIDField(blank=True, null=True, unique=True),
        ),
    ]
