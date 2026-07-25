import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0055_systemsettings_vehicle_pass_fees'),
    ]

    operations = [
        migrations.AddField(
            model_name='supplier',
            name='category',
            field=models.CharField(
                choices=[
                    ('delivery', 'Delivery'),
                    ('maintenance', 'Maintenance'),
                    ('vendor', 'Vendor'),
                    ('contractor', 'Contractor'),
                    ('other', 'Other'),
                ],
                default='other',
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name='ScheduledVisit',
            fields=[
                ('id', models.BigAutoField(auto_created=True, db_column='scheduled_visit_id', primary_key=True, serialize=False)),
                ('visitor_name', models.CharField(max_length=200)),
                ('category', models.CharField(
                    choices=[
                        ('delivery', 'Delivery'),
                        ('maintenance', 'Maintenance'),
                        ('vendor', 'Vendor'),
                        ('contractor', 'Contractor'),
                        ('guest', 'Guest / Visitor'),
                        ('other', 'Other'),
                    ],
                    default='other', max_length=20,
                )),
                ('plate_number', models.CharField(blank=True, max_length=20)),
                ('purpose', models.CharField(blank=True, max_length=255)),
                ('expected_date', models.DateField()),
                ('notes', models.TextField(blank=True)),
                ('is_arrived', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('supplier', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='scheduled_visits', to='vehicles.supplier',
                )),
            ],
            options={
                'ordering': ['expected_date', 'visitor_name'],
            },
        ),
    ]
