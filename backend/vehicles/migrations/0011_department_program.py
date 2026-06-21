from django.db import migrations, models

INITIAL_DEPARTMENTS = [
    'College of Engineering and Technology',
    'College of Business Administration',
    'College of Arts and Sciences',
    'College of Education',
    'College of Criminal Justice Education',
    'College of Architecture and Fine Arts',
    'College of Nursing',
    'College of Law',
    'College of Computer Studies',
    'College of Communication',
    'Senior High School',
    'Basic Education Department',
    'Graduate School',
    'Accounting Office',
    "Registrar's Office",
    'Human Resources Department',
    'Student Affairs Office',
    'Community Development and Student Office (CDSO)',
    'Library',
    'Research and Development Office',
    'Facilities Management Office',
    'Information Technology Office',
]

INITIAL_PROGRAMS = [
    'BSIT - 1', 'BSIT - 2', 'BSIT - 3', 'BSIT - 4',
    'BSCS - 1', 'BSCS - 2', 'BSCS - 3', 'BSCS - 4',
    'BSBA - 1', 'BSBA - 2', 'BSBA - 3', 'BSBA - 4',
    'BSA - 1',  'BSA - 2',  'BSA - 3',  'BSA - 4',
    'BSED - 1', 'BSED - 2', 'BSED - 3', 'BSED - 4',
    'BEED - 1', 'BEED - 2', 'BEED - 3', 'BEED - 4',
    'BSN - 1',  'BSN - 2',  'BSN - 3',  'BSN - 4',
    'BSCRIM - 1', 'BSCRIM - 2', 'BSCRIM - 3', 'BSCRIM - 4',
    'BSCE - 1', 'BSCE - 2', 'BSCE - 3', 'BSCE - 4',
    'BSEE - 1', 'BSEE - 2', 'BSEE - 3', 'BSEE - 4',
    'BSME - 1', 'BSME - 2', 'BSME - 3', 'BSME - 4',
    'BSARCH - 1', 'BSARCH - 2', 'BSARCH - 3', 'BSARCH - 4', 'BSARCH - 5',
    'AB Communication - 1', 'AB Communication - 2', 'AB Communication - 3', 'AB Communication - 4',
    'AB Political Science - 1', 'AB Political Science - 2', 'AB Political Science - 3', 'AB Political Science - 4',
    'JD - 1', 'JD - 2', 'JD - 3', 'JD - 4',
    'STEM - Grade 11', 'STEM - Grade 12',
    'ABM - Grade 11',  'ABM - Grade 12',
    'HUMSS - Grade 11', 'HUMSS - Grade 12',
    'TVL - Grade 11',  'TVL - Grade 12',
]


def seed(apps, schema_editor):
    Department = apps.get_model('vehicles', 'Department')
    Program    = apps.get_model('vehicles', 'Program')
    for i, name in enumerate(INITIAL_DEPARTMENTS):
        Department.objects.get_or_create(name=name, defaults={'order': i})
    for i, name in enumerate(INITIAL_PROGRAMS):
        Program.objects.get_or_create(name=name, defaults={'order': i})


def unseed(apps, schema_editor):
    pass  # leave data intact on reverse


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0010_alter_owner_schedule'),
    ]

    operations = [
        migrations.CreateModel(
            name='Department',
            fields=[
                ('id',        models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name',      models.CharField(max_length=200, unique=True)),
                ('is_active', models.BooleanField(default=True)),
                ('order',     models.PositiveIntegerField(default=0)),
            ],
            options={'ordering': ['order', 'name']},
        ),
        migrations.CreateModel(
            name='Program',
            fields=[
                ('id',        models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name',      models.CharField(max_length=200, unique=True)),
                ('is_active', models.BooleanField(default=True)),
                ('order',     models.PositiveIntegerField(default=0)),
            ],
            options={'ordering': ['order', 'name']},
        ),
        migrations.RunPython(seed, unseed),
    ]
