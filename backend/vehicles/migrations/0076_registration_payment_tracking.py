import uuid

import django.core.validators
from django.db import migrations, models


# Mirrors VehicleRegistration.FEE_EXEMPT_DEPARTMENTS. Spelled out rather than
# imported: a migration has to keep describing the world as it was when written,
# and importing the live model would let a later edit rewrite this history.
FEE_EXEMPT_DEPARTMENTS = ('cleaning_services',)

BATCH = 1000


def backfill_payment(apps, schema_editor):
    """Give every existing row a token, and read payment state off what it has.

    Acceptance has always required an Official Receipt number, so any row that
    carries one was paid — the fact was simply never recorded as such. Cleaning
    and Services staff never had one to carry, so they are marked exempt rather
    than left looking like they owe money. The amount is left NULL rather than
    guessed from today's configured fee, which may not be what that applicant
    actually handed over.

    Batched bulk_update, not a save() per row: this runs against a remote
    Postgres, where a per-row round trip turns a few thousand registrations into
    several minutes of migration.
    """
    VehicleRegistration = apps.get_model('vehicles', 'VehicleRegistration')
    qs = (VehicleRegistration.objects
          .all()
          .only('id', 'or_number', 'payment_token', 'registrant_type', 'department_type')
          .order_by('pk'))

    batch = []
    for reg in qs.iterator(chunk_size=BATCH):
        if not reg.payment_token:
            reg.payment_token = uuid.uuid4()
        if reg.registrant_type == 'employee' and (reg.department_type or '') in FEE_EXEMPT_DEPARTMENTS:
            reg.payment_status = 'exempt'
        elif (reg.or_number or '').strip():
            reg.payment_status = 'paid'
        batch.append(reg)

        if len(batch) >= BATCH:
            VehicleRegistration.objects.bulk_update(batch, ['payment_token', 'payment_status'], batch_size=BATCH)
            batch.clear()

    if batch:
        VehicleRegistration.objects.bulk_update(batch, ['payment_token', 'payment_status'], batch_size=BATCH)


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0075_vehicleregistration_assessment_form'),
    ]

    operations = [
        migrations.AddField(
            model_name='vehicleregistration',
            name='payment_status',
            field=models.CharField(
                choices=[('unpaid', 'Unpaid'), ('paid', 'Paid'), ('exempt', 'Exempt')],
                db_index=True, default='unpaid', max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='vehicleregistration',
            name='or_receipt_image',
            field=models.FileField(
                blank=True, null=True, upload_to='receipts/',
                validators=[django.core.validators.FileExtensionValidator(
                    allowed_extensions=['jpg', 'jpeg', 'png', 'webp', 'heic', 'heif', 'pdf'],
                )],
            ),
        ),
        migrations.AddField(
            model_name='vehicleregistration',
            name='amount_paid',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True),
        ),
        migrations.AddField(
            model_name='vehicleregistration',
            name='paid_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='vehicleregistration',
            name='payment_token',
            field=models.UUIDField(blank=True, editable=False, null=True, unique=True),
        ),
        migrations.AddField(
            model_name='vehicleregistration',
            name='unpaid_accept_reason',
            field=models.TextField(blank=True),
        ),
        migrations.RunPython(backfill_payment, migrations.RunPython.noop),
    ]
