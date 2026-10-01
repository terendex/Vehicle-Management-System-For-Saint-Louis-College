"""Split User.full_name into last_name / first_name / middle_initial.

last_name and first_name already exist (AbstractUser's own columns, empty until
now); middle_initial is new. Every account's name is split into them, and
full_name leaves the model.

The full_name COLUMN is not dropped here — it is made nullable and left in
place. Two servers share this database and only Railway migrates on deploy: a
campus install still running the previous code SELECTs and INSERTs full_name,
so dropping it now would take that server down until it is updated. Once every
server runs this code, `manage.py drop_legacy_full_name` re-splits anything the
old code wrote in the meantime and drops the column.

The new column gets a database-level default of '' for the same reason: the old
code does not know it exists, and its INSERTs must not fail on NOT NULL.
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


# A frozen copy of accounts.names.split_full_name, so this migration keeps
# doing exactly what it did even if that module changes later.
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
    User = apps.get_model('accounts', 'User')
    rows = []
    # Only rows whose parts are still empty: never overwrite a name already split.
    for user in User.objects.filter(last_name='', first_name='').exclude(full_name=''):
        user.last_name, user.first_name, user.middle_initial = split_full_name(user.full_name)
        rows.append(user)
    User.objects.bulk_update(rows, ['last_name', 'first_name', 'middle_initial'], batch_size=500)


def rejoin_names(apps, schema_editor):
    """Reverse: rebuild full_name from the parts ("LAST, FIRST M.")."""
    User = apps.get_model('accounts', 'User')
    # Only rows that have parts: one written by older code since the split
    # has blank parts and still holds its name in full_name — keep it.
    for user in User.objects.exclude(last_name='', first_name=''):
        name = ', '.join(p for p in (user.last_name, user.first_name) if p)
        if user.middle_initial:
            name = f'{name} {user.middle_initial}.'
        user.full_name = name
        user.save(update_fields=['full_name'])


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0039_rename_seeded_admin_email'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='middle_initial',
            field=models.CharField(blank=True, default='', max_length=1),
        ),
        migrations.RunSQL(
            "ALTER TABLE tbl_user ALTER COLUMN middle_initial SET DEFAULT ''",
            reverse_sql="ALTER TABLE tbl_user ALTER COLUMN middle_initial DROP DEFAULT",
        ),
        migrations.RunPython(split_names, rejoin_names),
        # Gone from the model; the column stays, nullable, until
        # drop_legacy_full_name (see the module docstring).
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name='user', name='full_name'),
            ],
            database_operations=[
                migrations.RunSQL(
                    "ALTER TABLE tbl_user ALTER COLUMN full_name DROP NOT NULL",
                    reverse_sql="UPDATE tbl_user SET full_name = '' WHERE full_name IS NULL; "
                                "ALTER TABLE tbl_user ALTER COLUMN full_name SET NOT NULL",
                ),
            ],
        ),
    ]
