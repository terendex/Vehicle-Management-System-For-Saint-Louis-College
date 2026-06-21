import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0013_alter_registrationtoken_registrant_type_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='ParkingZone',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('vehicle_category', models.CharField(
                    choices=[('motorcycle', 'Motorcycle'), ('car', 'Car')],
                    max_length=20,
                )),
                ('reference_image', models.ImageField(blank=True, null=True, upload_to='parking_zones/')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['vehicle_category', 'name'],
            },
        ),
        migrations.AddField(
            model_name='parkingspace',
            name='zone',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='spaces',
                to='vehicles.parkingzone',
            ),
        ),
        migrations.AddField(
            model_name='parkingspace',
            name='x1',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='parkingspace',
            name='y1',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='parkingspace',
            name='x2',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='parkingspace',
            name='y2',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AlterUniqueTogether(
            name='parkingspace',
            unique_together=set(),
        ),
    ]
