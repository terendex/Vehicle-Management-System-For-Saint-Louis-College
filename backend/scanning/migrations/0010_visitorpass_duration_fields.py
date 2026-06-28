from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('scanning', '0009_remove_visitorpass_updated_at_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='visitorpass',
            name='allowed_duration',
            field=models.PositiveIntegerField(default=60, help_text='Allowed time inside in minutes'),
        ),
        migrations.AddField(
            model_name='visitorpass',
            name='expires_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
