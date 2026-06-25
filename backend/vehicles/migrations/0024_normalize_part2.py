from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0023_normalize_dept_data'),
    ]

    operations = [
        # Drop the old free-text column now that FK is populated
        migrations.RemoveField(
            model_name='vehicleregistration',
            name='department',
        ),
        # Rename temp FK to the canonical name
        migrations.RenameField(
            model_name='vehicleregistration',
            old_name='department_fk',
            new_name='department',
        ),
    ]
