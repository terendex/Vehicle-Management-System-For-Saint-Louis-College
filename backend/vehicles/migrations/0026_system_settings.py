import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0025_remove_ebike_vehicle_type'),
    ]

    operations = [
        migrations.CreateModel(
            name='SystemSettings',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('retention_years', models.IntegerField(
                    default=5,
                    validators=[
                        django.core.validators.MinValueValidator(1),
                        django.core.validators.MaxValueValidator(10),
                    ],
                )),
                ('scan_dedup_seconds', models.IntegerField(
                    default=30,
                    validators=[
                        django.core.validators.MinValueValidator(5),
                        django.core.validators.MaxValueValidator(300),
                    ],
                )),
            ],
            options={
                'verbose_name': 'System Settings',
                'verbose_name_plural': 'System Settings',
            },
        ),
    ]
