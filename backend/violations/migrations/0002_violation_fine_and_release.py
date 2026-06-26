from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('violations', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='violation',
            name='fine_amount',
            field=models.DecimalField(decimal_places=2, default=20.0, max_digits=6),
        ),
        migrations.AddField(
            model_name='violation',
            name='is_released',
            field=models.BooleanField(default=False),
        ),
    ]
