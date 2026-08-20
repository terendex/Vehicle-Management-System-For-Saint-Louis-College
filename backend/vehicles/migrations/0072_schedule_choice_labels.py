"""The second student rotation becomes TTHF (Tue/Thu/Fri), not TTHS (Tue/Thu/Sat).

Two things happen here:

* the choices are restated — 'TTHS' is gone, and 'Any Day' now reads
  'Any Campus Day (Monday-Saturday)' because the campus is shut on Sunday and
  the old label overstated what the pass admits;
* stored 'TTHS' rows are renamed and their campus_days moved onto the new
  rotation, so a pass's code and its days cannot disagree. Saturday students
  become Friday students; there were none in this database when the rename was
  written, but any other environment gets the same treatment rather than rows
  whose label says Friday and whose days say Saturday.
"""
from django.db import migrations, models

OLD_DAYS = ['Tuesday', 'Thursday', 'Saturday']
NEW_DAYS = ['Tuesday', 'Thursday', 'Friday']


def tths_becomes_tthf(apps, schema_editor):
    Registration = apps.get_model('vehicles', 'VehicleRegistration')
    for reg in Registration.objects.filter(schedule='TTHS'):
        reg.schedule = 'TTHF'
        # Only the days that were on the old rotation move; anything a CDSO
        # officer added by hand is left where it is.
        reg.campus_days = [NEW_DAYS[OLD_DAYS.index(d)] if d in OLD_DAYS else d
                           for d in (reg.campus_days or [])]
        reg.save(update_fields=['schedule', 'campus_days'])


def tthf_becomes_tths(apps, schema_editor):
    Registration = apps.get_model('vehicles', 'VehicleRegistration')
    for reg in Registration.objects.filter(schedule='TTHF'):
        reg.schedule = 'TTHS'
        reg.campus_days = [OLD_DAYS[NEW_DAYS.index(d)] if d in NEW_DAYS else d
                           for d in (reg.campus_days or [])]
        reg.save(update_fields=['schedule', 'campus_days'])


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0071_parking_dwell_thresholds'),
    ]

    operations = [
        migrations.AlterField(
            model_name='vehicleregistration',
            name='schedule',
            field=models.CharField(
                blank=True,
                choices=[
                    ('MWF', 'Monday-Wednesday-Friday'),
                    ('TTHF', 'Tuesday-Thursday-Friday'),
                    ('MIXED', 'Mixed / Custom Days'),
                    ('ANY', 'Any Campus Day (Monday-Saturday)'),
                ],
                max_length=10,
            ),
        ),
        migrations.RunPython(tths_becomes_tthf, tthf_becomes_tths),
    ]
