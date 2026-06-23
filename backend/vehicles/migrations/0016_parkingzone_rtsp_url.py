from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0015_update_rule_constraint_types'),
    ]

    operations = [
        migrations.AddField(
            model_name='parkingzone',
            name='rtsp_url',
            field=models.CharField(
                blank=True,
                max_length=500,
                help_text='RTSP stream URL, e.g. rtsp://192.168.1.170:554/stream',
            ),
        ),
    ]
