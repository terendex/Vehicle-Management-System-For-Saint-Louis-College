"""Backfill Violation.owner, and retire the fee-era status.

The offence ladder is counted per account now, so every existing violation
needs the owner it was issued against. Two passes, in order of reliability:

  1. the vehicle's current owner, where the vehicle row still exists
  2. the owner_email snapshot taken at issue time, for violations whose vehicle
     has since been deleted

Rows that match neither keep owner=NULL — a gate-issued vehicle with no account
behind it never had an owner to record.

Historical `fee_imposed` rows are moved to `warning`. The fee no longer exists,
and leaving them at fee_imposed would have kept blocking those vehicles at the
gate through a check that has been removed — an unpayable fine with no way to
clear it.
"""
from django.db import migrations


def backfill(apps, schema_editor):
    Violation = apps.get_model('violations', 'Violation')
    User = apps.get_model('accounts', 'User')

    # Pass 1 — through the vehicle that is still attached.
    linked = 0
    for v in Violation.objects.filter(owner__isnull=True,
                                      vehicle__isnull=False).select_related('vehicle'):
        owner_id = v.vehicle.user_id
        if owner_id:
            Violation.objects.filter(pk=v.pk).update(owner_id=owner_id)
            linked += 1

    # Pass 2 — through the email snapshot, for orphaned rows. Live accounts
    # only: an archived row shares its address with whoever registered next,
    # and pinning an old offence to a new person is worse than leaving it null.
    by_email = 0
    orphans = Violation.objects.filter(owner__isnull=True).exclude(owner_email='')
    for v in orphans:
        match = list(User.objects.filter(email__iexact=v.owner_email,
                                         is_archived=False)[:2])
        if len(match) == 1:
            Violation.objects.filter(pk=v.pk).update(owner_id=match[0].pk)
            by_email += 1

    moved = Violation.objects.filter(status='fee_imposed').update(status='warning')
    print(f'  violations: linked {linked} by vehicle, {by_email} by email; '
          f'{moved} fee_imposed -> warning')


def noop(apps, schema_editor):
    """Irreversible by design: the original owner links cannot be distinguished
    from ones that were always null, and re-imposing fees on cleared rows would
    invent debts."""


class Migration(migrations.Migration):

    dependencies = [
        ('violations', '0015_violation_owner_alter_violation_status_and_more'),
        ('accounts', '0032_user_confiscated_at_user_confiscated_until_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill, noop),
    ]
