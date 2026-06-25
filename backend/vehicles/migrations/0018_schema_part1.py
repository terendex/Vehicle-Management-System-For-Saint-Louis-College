import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0017_alter_parkingzone_rtsp_url'),
        ('accounts', '0009_add_owner_profile_fields'),
    ]

    operations = [
        # 1. Create ReferenceItem (replaces Department + Program)
        migrations.CreateModel(
            name='ReferenceItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('category', models.CharField(max_length=20, choices=[('department','Department'),('program','Program')])),
                ('name', models.CharField(max_length=200)),
                ('is_active', models.BooleanField(default=True)),
                ('order', models.PositiveIntegerField(default=0)),
            ],
            options={
                'ordering': ['category', 'order', 'name'],
                'unique_together': {('category', 'name')},
            },
        ),

        # 2. Drop VehicleTypeAccess (dead code)
        migrations.DeleteModel(
            name='VehicleTypeAccess',
        ),

        # 3. Add user FK to Vehicle (nullable, alongside existing owner FK for now)
        migrations.AddField(
            model_name='vehicle',
            name='user',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='vehicles',
                to=settings.AUTH_USER_MODEL,
            ),
        ),

        # 4. Add new fields to VehicleRegistration
        migrations.AddField(
            model_name='vehicleregistration',
            name='department_type',
            field=models.CharField(
                blank=True, null=True, max_length=20,
                choices=[('teaching','Teaching'),('non_teaching','Non-Teaching')],
            ),
        ),
        migrations.AddField(
            model_name='vehicleregistration',
            name='source',
            field=models.CharField(
                default='token', max_length=20,
                choices=[('token','External Token'),('direct','Direct/CDSO')],
            ),
        ),
        migrations.AddField(
            model_name='vehicleregistration',
            name='program',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='registrations',
                to='vehicles.referenceitem',
                limit_choices_to={'category': 'program'},
            ),
        ),
    ]
