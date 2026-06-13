from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0006_auditlog'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='user_code',
            field=models.CharField(
                blank=True,
                db_index=True,
                max_length=20,
                null=True,
                unique=True,
                verbose_name='User Code',
            ),
        ),
    ]
