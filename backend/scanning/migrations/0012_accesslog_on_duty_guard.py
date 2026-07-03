from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('scanning', '0011_guardshift'),
    ]

    operations = [
        migrations.AddField(
            model_name='accesslog',
            name='on_duty_guard',
            field=models.ForeignKey(blank=True, help_text='Guard clocked in at this gate when the scan happened.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='on_duty_scans', to=settings.AUTH_USER_MODEL),
        ),
    ]
