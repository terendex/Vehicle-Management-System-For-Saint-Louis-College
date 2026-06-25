from django.db import migrations


def populate_department_fk(apps, schema_editor):
    VehicleRegistration = apps.get_model('vehicles', 'VehicleRegistration')
    ReferenceItem = apps.get_model('vehicles', 'ReferenceItem')

    for reg in VehicleRegistration.objects.exclude(department=''):
        item, _ = ReferenceItem.objects.get_or_create(
            category='department',
            name=reg.department,
            defaults={'is_active': True, 'order': 0},
        )
        VehicleRegistration.objects.filter(pk=reg.pk).update(department_fk=item)


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0022_normalize_part1'),
    ]

    operations = [
        migrations.RunPython(populate_department_fk, migrations.RunPython.noop),
    ]
