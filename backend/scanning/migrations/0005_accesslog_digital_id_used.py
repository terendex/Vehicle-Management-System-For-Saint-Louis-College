from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('scanning', '0004_accesslog_vehicle_type_alter_accesslog_plate_number'),
    ]

    operations = [
        migrations.AddField(
            model_name='accesslog',
            name='digital_id_used',
            field=models.CharField(
                blank=True,
                default='',
                max_length=50,
                help_text='The digital ID (user_code / ID number) presented by an unplated vehicle rider',
            ),
            preserve_default=False,
        ),
    ]
