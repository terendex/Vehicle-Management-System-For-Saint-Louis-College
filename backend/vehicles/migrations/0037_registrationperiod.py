from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0036_systemsettings_open_campus_mode'),
    ]

    operations = [
        migrations.CreateModel(
            name='RegistrationPeriod',
            fields=[
                ('id',         models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('label',      models.CharField(max_length=150)),
                ('start_date', models.DateField()),
                ('end_date',   models.DateField()),
                ('is_active',  models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]
