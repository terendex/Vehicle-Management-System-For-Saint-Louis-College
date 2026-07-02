from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('violations', '0006_alter_violation_fine_amount'),
    ]

    operations = [
        migrations.AddField(
            model_name='violation',
            name='on_duty_guard',
            field=models.ForeignKey(blank=True, help_text='Guard clocked in at the gate when this violation was auto-logged.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='on_duty_violations', to=settings.AUTH_USER_MODEL),
        ),
    ]
