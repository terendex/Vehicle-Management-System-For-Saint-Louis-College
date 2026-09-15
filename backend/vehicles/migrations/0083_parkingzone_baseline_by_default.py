from django.db import migrations, models


def switch_all_zones_to_baseline(apps, schema_editor):
    """Every existing zone moves to baseline scoring.

    Safe before anyone has captured a baseline: a 'classic' zone without one
    keeps scoring on the detector (parking_camera._classic_hits returns None),
    and the zone editor says so until a baseline is set.
    """
    ParkingZone = apps.get_model('vehicles', 'ParkingZone')
    ParkingZone.objects.exclude(occupancy_method='classic').update(occupancy_method='classic')


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0082_registration_change_request'),
    ]

    operations = [
        migrations.AlterField(
            model_name='parkingzone',
            name='occupancy_method',
            field=models.CharField(
                choices=[('ml', 'Vehicle detector (YOLO)'), ('classic', 'Baseline comparison (no ML)')],
                default='classic',
                help_text="How this zone decides a bay is taken. 'classic' compares each bay against an empty baseline and needs no detector; it falls back to the detector until a baseline is captured.",
                max_length=20,
            ),
        ),
        # Not reversed: which zones were on the detector before is not recorded,
        # and flipping every zone back would be a guess.
        migrations.RunPython(switch_all_zones_to_baseline, migrations.RunPython.noop),
    ]
