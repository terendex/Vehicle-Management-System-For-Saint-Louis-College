from django.db import migrations


def copy_owner_to_user(apps, schema_editor):
    Owner = apps.get_model('vehicles', 'Owner')
    User  = apps.get_model('accounts', 'User')

    for owner in Owner.objects.all():
        if not owner.user_code:
            continue
        user = User.objects.filter(user_code__iexact=owner.user_code).first()
        if user:
            user.owner_type = owner.owner_type
            user.schedule   = owner.schedule
            user.contact    = owner.contact
            user.address    = owner.address
            user.save(update_fields=['owner_type', 'schedule', 'contact', 'address'])


def populate_vehicle_user_fk(apps, schema_editor):
    Vehicle = apps.get_model('vehicles', 'Vehicle')
    Owner   = apps.get_model('vehicles', 'Owner')
    User    = apps.get_model('accounts', 'User')

    for vehicle in Vehicle.objects.select_related('owner').all():
        if not vehicle.owner_id:
            continue
        try:
            owner = Owner.objects.get(pk=vehicle.owner_id)
        except Owner.DoesNotExist:
            continue
        if not owner.user_code:
            continue
        user = User.objects.filter(user_code__iexact=owner.user_code).first()
        if user:
            vehicle.user = user
            vehicle.save(update_fields=['user_id'])


def copy_dept_and_prog_to_reference_item(apps, schema_editor):
    Department   = apps.get_model('vehicles', 'Department')
    Program      = apps.get_model('vehicles', 'Program')
    ReferenceItem = apps.get_model('vehicles', 'ReferenceItem')

    for dept in Department.objects.all():
        ReferenceItem.objects.get_or_create(
            category='department',
            name=dept.name,
            defaults={'is_active': dept.is_active, 'order': dept.order},
        )

    for prog in Program.objects.all():
        ReferenceItem.objects.get_or_create(
            category='program',
            name=prog.name,
            defaults={'is_active': prog.is_active, 'order': prog.order},
        )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0018_schema_part1'),
    ]

    operations = [
        migrations.RunPython(copy_owner_to_user,                    noop),
        migrations.RunPython(populate_vehicle_user_fk,              noop),
        migrations.RunPython(copy_dept_and_prog_to_reference_item,  noop),
    ]
