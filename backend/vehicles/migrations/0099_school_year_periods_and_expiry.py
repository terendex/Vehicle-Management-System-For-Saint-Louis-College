"""Bring data saved under the old rules onto the school year (Aug 1 to Jul 31).

Agreed with the user on 2026-10-05, before any data was touched:

1. Registration periods saved with dates of their own ("legacy"): for each
   school year that has some but no proper period yet, the most recently
   created one becomes that school year's period (August 1 to July 31,
   label "S.Y. Y–Y+1"); any others are left exactly as they were, as history.
   It keeps its is_active flag.
   On the live database this turns the Oct 5 to Oct 30, 2026 period into
   S.Y. 2026–2027 and leaves the Sep 29 to Oct 13, 2026 one as history.

2. Owner accounts whose expiry was set by the old "12 months after
   acceptance" rule are moved to July 31 of the school year they were
   accepted in, the date every pass of that school year ends on. An account
   whose new date would already be past is left alone rather than archived
   by the next daily job; it is reported instead.

Written per school year and per account rather than by row id, so it does the
same thing on the live, demo and test databases. Re-running it changes
nothing. Reversing it is a no-op: the old dates are not kept.
"""
from datetime import date

from django.db import migrations
from django.utils import timezone


def _school_year_of(day):
    return day.year if day.month >= 8 else day.year - 1


def _period_dates(year):
    return date(year, 8, 1), date(year + 1, 7, 31)


def convert_legacy_periods(apps, schema_editor):
    Period = apps.get_model('vehicles', 'RegistrationPeriod')
    by_year = {}
    for period in Period.objects.order_by('-created_at', '-pk'):
        year = _school_year_of(period.start_date)
        by_year.setdefault(year, []).append(period)
    for year, periods in by_year.items():
        start, end = _period_dates(year)
        if any((p.start_date, p.end_date) == (start, end) for p in periods):
            continue                                   # the school year already has its period
        newest = periods[0]
        print(f'\n  S.Y. {year}-{year + 1}: period #{newest.pk} '
              f'({newest.start_date} to {newest.end_date}) -> {start} to {end}')
        Period.objects.filter(pk=newest.pk).update(
            start_date=start, end_date=end, label=f'S.Y. {year}–{year + 1}')


def move_owner_expiry_to_july_31(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    today = timezone.localdate()
    owners = User.objects.filter(role='vehicle_owner', is_archived=False, expires_at__isnull=False)
    for owner in owners:
        if (owner.expires_at.month, owner.expires_at.day) == (7, 31):
            continue                                   # already on the school year rule
        joined = timezone.localtime(owner.date_joined).date()
        until = date(_school_year_of(joined) + 1, 7, 31)
        if until < today:
            print(f'\n  owner #{owner.pk}: July 31 of their school year ({until}) is past; left at {owner.expires_at}')
            continue
        User.objects.filter(pk=owner.pk).update(expires_at=until)


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0098_parkingspace_occupant'),
        ('accounts', '0042_relabel_fetcher_driver'),
    ]

    operations = [
        migrations.RunPython(convert_legacy_periods, migrations.RunPython.noop),
        migrations.RunPython(move_owner_expiry_to_july_31, migrations.RunPython.noop),
    ]
