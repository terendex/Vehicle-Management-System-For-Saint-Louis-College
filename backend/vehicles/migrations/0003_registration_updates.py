from django.db import migrations


class Migration(migrations.Migration):
    """
    Original version of this file conflicted with 0006_add_system_ids_to_registration.
    All intended operations have been moved to 0012_reg_fields_and_schema_fix.
    This migration is kept as a no-op so the graph stays intact.
    """

    dependencies = [
        ('vehicles', '0002_registrationtoken_vehicleregistration'),
    ]

    operations = []
