"""Account expiration is no longer optional.

Owner accounts always expire; System Settings controls how long, not whether.
This turns the flag on for the existing settings row, gives a 0/0 period the
model default (12 months) so "on" means something, and backfills the owners who
were created while expiry was off — those have expires_at NULL and would
otherwise live forever, which is the exact state this change removes.

Backfill uses date_joined, matching the SystemSettings PUT: an account's window
runs from when it was created, not from when the setting changed.
"""
from datetime import timedelta

import django.core.validators
from django.db import migrations, models
from dateutil.relativedelta import relativedelta


def enable_expiry(apps, schema_editor):
    SystemSettings = apps.get_model('vehicles', 'SystemSettings')
    User = apps.get_model('accounts', 'User')

    cfg = SystemSettings.objects.filter(pk=1).first()
    if cfg is None:
        # No settings row yet — SystemSettings.get() will create one from the
        # (now enabled-by-default) field defaults on first access.
        return

    if cfg.account_expiry_months <= 0 and cfg.account_expiry_days <= 0:
        cfg.account_expiry_months = 12
        cfg.account_expiry_days = 0
    cfg.account_expiry_enabled = True
    cfg.save(update_fields=['account_expiry_enabled', 'account_expiry_months',
                            'account_expiry_days'])

    owners = list(User.objects.filter(
        role='vehicle_owner', is_active=True, is_archived=False, expires_at__isnull=True,
    ))
    for owner in owners:
        owner.expires_at = (owner.date_joined.date()
                            + relativedelta(months=cfg.account_expiry_months)
                            + timedelta(days=cfg.account_expiry_days))
    if owners:
        # Chunked: this runs against the live owner table on deploy, and
        # Postgres' default is a single CASE statement covering every row.
        User.objects.bulk_update(owners, ['expires_at'], batch_size=500)


def noop(apps, schema_editor):
    """Reversing only restores the column default; the flag and the backfilled
    dates stay, because guessing which of them predated this migration would be
    worse than leaving them."""


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0068_dailyjobrun'),
        ('accounts', '0029_user_registration_banned'),
    ]

    operations = [
        migrations.AlterField(
            model_name='systemsettings',
            name='account_expiry_enabled',
            field=models.BooleanField(
                default=True,
                help_text='Vehicle-owner accounts auto-archive after the set duration. '
                          'Not clearable from System Settings — the period is the control.',
            ),
        ),
        migrations.AlterField(
            model_name='systemsettings',
            name='account_expiry_days',
            field=models.IntegerField(
                default=0,
                help_text='Extra days (on top of months) before an owner account expires. '
                          'Months + days must total at least 1.',
                validators=[
                    django.core.validators.MinValueValidator(0),
                    django.core.validators.MaxValueValidator(365),
                ],
            ),
        ),
        migrations.RunPython(enable_expiry, noop),
    ]
