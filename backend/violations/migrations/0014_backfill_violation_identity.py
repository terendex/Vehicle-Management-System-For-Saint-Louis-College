"""Copy plate and owner onto violations issued before the snapshot existed.

Without this, every historical violation would show a blank plate and owner the
moment its vehicle or account is removed — the exact failure the snapshot exists
to prevent, applied retroactively to the whole table.

Data only. Kept apart from the schema step in 0013 because Postgres will not
ALTER or CREATE INDEX on a table with pending foreign-key trigger events, and
bulk UPDATEs queue those; see the note at the top of 0013.

Safe to run after the FK loosened: switching on_delete to SET_NULL changes what
a future delete does, it does not clear a single existing vehicle_id, so every
row still points at the vehicle it was issued against.
"""
from django.db import migrations
from django.db.models import OuterRef, Subquery


def backfill_identity(apps, schema_editor):
    """Bulk UPDATE ... FROM rather than a row-by-row walk, so a table with a
    hundred thousand violations costs the same handful of statements as one
    with ten."""
    Violation = apps.get_model('violations', 'Violation')
    if not Violation.objects.exists():
        return

    Vehicle = apps.get_model('vehicles', 'Vehicle')
    User    = apps.get_model('accounts', 'User')

    vehicles = Vehicle.objects.filter(pk=OuterRef('vehicle_id'))
    Violation.objects.filter(vehicle__isnull=False).update(
        plate_number=Subquery(vehicles.values('plate_number')[:1]),
        conduction_number=Subquery(vehicles.values('conduction_number')[:1]),
    )

    owners = User.objects.filter(
        pk=Subquery(Vehicle.objects.filter(pk=OuterRef(OuterRef('vehicle_id')))
                    .values('user_id')[:1])
    )
    Violation.objects.filter(vehicle__isnull=False, vehicle__user__isnull=False).update(
        owner_name=Subquery(owners.values('full_name')[:1]),
        owner_email=Subquery(owners.values('email')[:1]),
    )

    # A NULL from either subquery (a vehicle with no plate, say) would violate
    # the NOT NULL these columns carry, so normalise afterwards.
    Violation.objects.filter(plate_number__isnull=True).update(plate_number='')
    Violation.objects.filter(conduction_number__isnull=True).update(conduction_number='')
    Violation.objects.filter(owner_name__isnull=True).update(owner_name='')
    Violation.objects.filter(owner_email__isnull=True).update(owner_email='')


def noop(apps, schema_editor):
    """Reverse is a no-op: 0013 drops the columns, and nothing outside them was
    modified."""


class Migration(migrations.Migration):

    dependencies = [
        ('violations', '0013_violation_conduction_number_violation_owner_email_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill_identity, noop),
    ]
