import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0008_add_must_change_password'),
        ('vehicles', '0012_merge_20260621_1021'),  # merge already resolved the 0003/0011 branches
    ]

    operations = [
        # ── Fields that were in 0003_registration_updates but never applied ──

        migrations.AddField(
            model_name='vehicleregistration',
            name='schedule',
            field=models.CharField(
                blank=True,
                choices=[('MWF', 'Monday-Wednesday-Friday'), ('TTHS', 'Tuesday-Thursday-Saturday'), ('ANY', 'Any Day')],
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='vehicleregistration',
            name='or_number',
            field=models.CharField(
                blank=True,
                max_length=100,
                help_text='Official Receipt number from Accounting Office',
            ),
        ),
        migrations.AlterField(
            model_name='vehicleregistration',
            name='token',
            field=models.UUIDField(blank=True, null=True, unique=True),
        ),
        migrations.CreateModel(
            name='ParkingSpace',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('space_number', models.CharField(max_length=20)),
                ('vehicle_category', models.CharField(
                    choices=[('motorcycle', 'Motorcycle'), ('car', 'Car')],
                    max_length=20,
                )),
                ('is_occupied', models.BooleanField(default=False)),
                ('occupied_by', models.CharField(blank=True, max_length=20,
                                                  help_text='Plate number of occupying vehicle')),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['vehicle_category', 'space_number'],
                'unique_together': {('space_number', 'vehicle_category')},
            },
        ),

        # ── Schema fixes ──

        # Direct FK from VehicleRegistration → User (eliminates email-string lookup)
        migrations.AddField(
            model_name='vehicleregistration',
            name='user',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='registrations',
                to=settings.AUTH_USER_MODEL,
            ),
        ),

        # Indexes on hot query columns
        migrations.AlterField(
            model_name='vehicleregistration',
            name='email',
            field=models.EmailField(max_length=254, db_index=True),
        ),
        migrations.AlterField(
            model_name='vehicleregistration',
            name='plate_number',
            field=models.CharField(max_length=20, db_index=True),
        ),
        migrations.AlterField(
            model_name='vehicleregistration',
            name='status',
            field=models.CharField(
                choices=[('pending', 'Pending'), ('accepted', 'Accepted'), ('rejected', 'Rejected')],
                default='pending',
                max_length=20,
                db_index=True,
            ),
        ),
    ]
