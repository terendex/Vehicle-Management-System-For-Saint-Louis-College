from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('violations', '0002_violation_fine_and_release'),
    ]

    operations = [
        migrations.AddField(
            model_name='violation',
            name='evidence',
            field=models.ImageField(blank=True, null=True, upload_to='violations/evidence/'),
        ),
    ]
