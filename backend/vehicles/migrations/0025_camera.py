from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0024_normalize_part2'),
    ]

    operations = [
        migrations.CreateModel(
            name='Camera',
            fields=[
                ('id',         models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('cam_number', models.PositiveIntegerField(unique=True)),
                ('name',       models.CharField(max_length=50)),
                ('ip',         models.CharField(max_length=100)),
                ('device_id',  models.CharField(max_length=100)),
                ('password',   models.CharField(max_length=100)),
                ('rtsp_url',   models.CharField(max_length=500)),
                ('assignment', models.CharField(max_length=20, choices=[('entry', 'Entry'), ('parking', 'Parking')])),
                ('is_active',  models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['cam_number'],
            },
        ),
    ]
