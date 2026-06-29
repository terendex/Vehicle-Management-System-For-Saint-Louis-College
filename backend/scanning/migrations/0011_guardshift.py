from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('scanning', '0010_visitorpass_duration_fields'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='GuardShift',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('gate', models.CharField(max_length=10)),
                ('clocked_in_at', models.DateTimeField(auto_now_add=True)),
                ('clocked_out_at', models.DateTimeField(blank=True, null=True)),
                ('guard', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='shifts', to=settings.AUTH_USER_MODEL)),
                ('clocked_out_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='clocked_out_shifts', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-clocked_in_at'],
            },
        ),
    ]
