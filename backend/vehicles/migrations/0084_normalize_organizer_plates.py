import re

from django.db import migrations

# A copy, not an import: a migration must keep meaning what it meant when it
# was written, whatever vehicles.models.canonical_identifier later becomes.
_CONTROL = re.compile(r'^FM-?(\d{1,6})$')


def _canonical(value):
    norm = str(value or '').strip().upper().replace(' ', '')
    m = _CONTROL.match(norm)
    return f'FM-{int(m.group(1)):03d}' if m else norm


def normalize(apps, schema_editor):
    """Rewrite stored organizer plates the way the gate compares them: upper
    case, no spaces, control numbers as FM-001, no repeats. Plates saved before
    the event API cleaned them could carry a space and so never matched a
    scanned plate."""
    Event = apps.get_model('vehicles', 'Event')
    for ev in Event.objects.exclude(organizer_plates=[]):
        cleaned = []
        for p in ev.organizer_plates or []:
            plate = _canonical(p)
            if plate and plate not in cleaned:
                cleaned.append(plate)
        if cleaned != ev.organizer_plates:
            ev.organizer_plates = cleaned
            ev.save(update_fields=['organizer_plates'])


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0083_parkingzone_baseline_by_default'),
    ]

    operations = [
        migrations.RunPython(normalize, migrations.RunPython.noop),
    ]
