from django.db import migrations
from django.contrib.auth.hashers import make_password


def seed_demo_users(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    users = [
        {
            'email': 'admin@slc.edu.ph',
            'full_name': 'System Admin',
            'password': 'admin123',
            'role': 'admin',
            'is_staff': True,
            'is_superuser': True,
            'is_active': True,
        },
        {
            'email': 'guard@slc.edu.ph',
            'full_name': 'Gate Guard',
            'password': 'guard123',
            'role': 'security',
            'is_staff': False,
            'is_superuser': False,
            'is_active': True,
        },
        {
            'email': 'office@slc.edu.ph',
            'full_name': 'Office Staff',
            'password': 'office123',
            'role': 'office_staff',
            'is_staff': False,
            'is_superuser': False,
            'is_active': True,
        },
    ]
    for data in users:
        pwd = data.pop('password')
        user, created = User.objects.get_or_create(
            email=data['email'],
            defaults=data,
        )
        if created:
            user.password = make_password(pwd)
            user.save()


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0004_alter_user_email_alter_user_full_name_and_more'),
    ]

    operations = [
        migrations.RunPython(seed_demo_users, migrations.RunPython.noop),
    ]
