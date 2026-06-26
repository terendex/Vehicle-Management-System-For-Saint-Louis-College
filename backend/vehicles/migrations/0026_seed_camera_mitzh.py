from django.db import migrations


def add_mitzh_camera(apps, schema_editor):
    Camera = apps.get_model('vehicles', 'Camera')
    Camera.objects.get_or_create(
        cam_number=1,
        defaults={
            'name':       'Mitzh-cctv',
            'ip':         '192.168.137.86',
            'device_id':  '110384665',
            'password':   '7PC9C8sM',
            'rtsp_url':   'rtsp://110384665:7PC9C8sM@192.168.68.101/stream1',
            'assignment': 'entry',
            'is_active':  True,
        }
    )


def remove_mitzh_camera(apps, schema_editor):
    Camera = apps.get_model('vehicles', 'Camera')
    Camera.objects.filter(cam_number=1, name='Mitzh-cctv').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0025_camera'),
        ('vehicles', '0025_remove_ebike_vehicle_type'),
    ]

    operations = [
        migrations.RunPython(add_mitzh_camera, remove_mitzh_camera),
    ]
