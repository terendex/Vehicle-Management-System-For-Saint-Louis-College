from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0041_supplier_models'),
    ]

    operations = [
        migrations.AddField(
            model_name='event',
            name='archived',
            field=models.BooleanField(default=False),
        ),
    ]
