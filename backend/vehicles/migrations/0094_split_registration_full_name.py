"""Split VehicleRegistration.full_name into last_name / first_name / middle_initial.

The same change as accounts 0040, for the registration table: three new
columns, every existing name split into them, and full_name gone from the
model while its COLUMN stays (nullable) until `manage.py drop_legacy_full_name`
— a campus server still on the previous code reads and writes it. The new
columns carry a database default of '' so that code's INSERTs still succeed.

Owner change requests store their proposed edit as JSON; one filed before this
change may hold {"full_name": ...}, which nothing can apply any more, so it is
rewritten into the three parts.
"""
import re

from django.db import migrations, models

_INITIAL = re.compile(r'^[A-Za-zÀ-ÿÑñ]\.?$')
# Surname particles: in "JUAN DELA CRUZ" the last name is DELA CRUZ, not CRUZ.
_PARTICLES = {'DE', 'DEL', 'DELA', 'DELOS', 'DELAS', 'LA', 'LAS', 'LOS', 'SAN', 'STA', 'STA.',
              'STO', 'STO.', 'SANTA', 'SANTO', 'VAN', 'VON', 'DI', 'DA', 'DOS', 'MC', 'MAC'}


def _initial(value):
    for ch in str(value or '').strip():
        if ch.isalpha():
            return ch.upper()
    return ''


# A frozen copy of accounts.names.split_full_name (see accounts 0040).
def split_full_name(text):
    text = ' '.join(str(text or '').split())
    if not text:
        return '', '', ''
    if ',' in text:
        parts = [p.strip() for p in text.split(',')]
        last = parts[0]
        if len(parts) >= 3:
            return last, parts[1], _initial(' '.join(parts[2:]))
        rest = parts[1].split()
        if len(rest) >= 2 and _INITIAL.match(rest[-1]):
            return last, ' '.join(rest[:-1]), _initial(rest[-1])
        return last, ' '.join(rest), ''
    words = text.split()
    # FIRST [MIDDLE] LAST, where LAST takes any particles in front of it.
    cut = len(words) - 1
    while cut > 1 and words[cut - 1].upper() in _PARTICLES:
        cut -= 1
    if len(words) == 1:
        return words[0], '', ''
    last, rest = ' '.join(words[cut:]), words[:cut]
    if len(rest) == 1:
        return last, rest[0], ''
    for i, word in enumerate(rest[1:], start=1):
        if _INITIAL.match(word):
            return ' '.join(words[i + 1:]), ' '.join(words[:i]), _initial(word)
    return last, ' '.join(rest[:-1]), _initial(rest[-1])


def split_names(apps, schema_editor):
    Registration = apps.get_model('vehicles', 'VehicleRegistration')
    rows = []
    for reg in Registration.objects.filter(last_name='', first_name='').exclude(full_name=''):
        reg.last_name, reg.first_name, reg.middle_initial = split_full_name(reg.full_name)
        rows.append(reg)
    Registration.objects.bulk_update(rows, ['last_name', 'first_name', 'middle_initial'], batch_size=500)

    ChangeRequest = apps.get_model('vehicles', 'RegistrationChangeRequest')
    for request in ChangeRequest.objects.all():
        changes = dict(request.changes or {})
        if 'full_name' not in changes:
            continue
        last, first, initial = split_full_name(changes.pop('full_name'))
        changes.update(last_name=last, first_name=first, middle_initial=initial)
        request.changes = changes
        request.save(update_fields=['changes'])


def rejoin_names(apps, schema_editor):
    Registration = apps.get_model('vehicles', 'VehicleRegistration')
    # Only rows that have parts: one written by older code since the split
    # has blank parts and still holds its name in full_name — keep it.
    for reg in Registration.objects.exclude(last_name='', first_name=''):
        name = ', '.join(p for p in (reg.last_name, reg.first_name) if p)
        if reg.middle_initial:
            name = f'{name} {reg.middle_initial}.'
        reg.full_name = name
        reg.save(update_fields=['full_name'])


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0093_scheduled_backup'),
    ]

    operations = [
        migrations.AddField(
            model_name='vehicleregistration',
            name='last_name',
            field=models.CharField(default='', max_length=150),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='vehicleregistration',
            name='first_name',
            field=models.CharField(default='', max_length=150),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='vehicleregistration',
            name='middle_initial',
            field=models.CharField(blank=True, default='', max_length=1),
        ),
        migrations.RunSQL(
            "ALTER TABLE tbl_vehicle_registration "
            "ALTER COLUMN last_name SET DEFAULT '', "
            "ALTER COLUMN first_name SET DEFAULT '', "
            "ALTER COLUMN middle_initial SET DEFAULT ''",
            reverse_sql="ALTER TABLE tbl_vehicle_registration "
                        "ALTER COLUMN last_name DROP DEFAULT, "
                        "ALTER COLUMN first_name DROP DEFAULT, "
                        "ALTER COLUMN middle_initial DROP DEFAULT",
        ),
        migrations.RunPython(split_names, rejoin_names),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name='vehicleregistration', name='full_name'),
            ],
            database_operations=[
                migrations.RunSQL(
                    "ALTER TABLE tbl_vehicle_registration ALTER COLUMN full_name DROP NOT NULL",
                    reverse_sql="UPDATE tbl_vehicle_registration SET full_name = '' WHERE full_name IS NULL; "
                                "ALTER TABLE tbl_vehicle_registration ALTER COLUMN full_name SET NOT NULL",
                ),
            ],
        ),
    ]
