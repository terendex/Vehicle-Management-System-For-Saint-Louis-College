import django.db.models.deletion
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0019_data_migration'),
    ]

    operations = [
        # Remove owner FK from Vehicle (data already in user FK from migration 0019)
        migrations.RemoveField(
            model_name='vehicle',
            name='owner',
        ),

        # Drop Owner model
        migrations.DeleteModel(
            name='Owner',
        ),

        # Drop Department model
        migrations.DeleteModel(
            name='Department',
        ),

        # Drop Program model
        migrations.DeleteModel(
            name='Program',
        ),
    ]
