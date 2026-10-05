"""Each registration period belongs to one school year (unique).

The admin now picks the school year and then the open and close dates inside
it (August 1 to July 31), so a period's school year can no longer be read off
its dates alone; it is stored, and the database refuses a second period for
the same school year.

Existing rows: 0099 already gave every school year that had periods one
covering exactly August 1 to July 31. That one is tagged with its school year
(the most recent, if a school year somehow has several). Every other period
stays untagged: history, which cannot be set active or edited. On the live
database #14 (S.Y. 2026–2027) is tagged and #13 stays as history.
"""
from datetime import date

from django.db import migrations, models


def _school_year_of(day):
    return day.year if day.month >= 8 else day.year - 1


def tag_school_year_periods(apps, schema_editor):
    Period = apps.get_model('vehicles', 'RegistrationPeriod')
    tagged = set()
    for period in Period.objects.order_by('-created_at', '-pk'):
        year = _school_year_of(period.start_date)
        whole_year = (period.start_date, period.end_date) == (date(year, 8, 1), date(year + 1, 7, 31))
        if whole_year and year not in tagged:
            Period.objects.filter(pk=period.pk).update(school_year=year)
            tagged.add(year)


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0099_school_year_periods_and_expiry'),
    ]

    operations = [
        migrations.AddField(
            model_name='registrationperiod',
            name='school_year',
            field=models.PositiveSmallIntegerField(blank=True, null=True, unique=True),
        ),
        migrations.RunPython(tag_school_year_periods, migrations.RunPython.noop),
    ]
