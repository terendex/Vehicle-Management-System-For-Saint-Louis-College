from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0008_add_must_change_password'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='owner_type',
            field=models.CharField(
                blank=True, null=True, max_length=20,
                choices=[('student','Student'),('fetcher','Fetcher/Dropper'),('employee','Employee'),('visitor','Visitor')],
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='schedule',
            field=models.CharField(
                blank=True, null=True, max_length=10,
                choices=[('MWF','Monday-Wednesday-Friday'),('TTHS','Tuesday-Thursday-Saturday'),('ANY','Any Day'),('ALL','All Days')],
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='contact',
            field=models.CharField(blank=True, null=True, max_length=50),
        ),
        migrations.AddField(
            model_name='user',
            name='address',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='user',
            name='photo',
            field=models.ImageField(blank=True, null=True, upload_to='owners/'),
        ),
        migrations.AlterField(
            model_name='user',
            name='role',
            field=models.CharField(
                default='vehicle_owner', max_length=20,
                choices=[('admin','Admin'),('security','Security Personnel'),('vehicle_owner','Registered Vehicle Owner'),('cdso','CDSO Staff')],
            ),
        ),
    ]
