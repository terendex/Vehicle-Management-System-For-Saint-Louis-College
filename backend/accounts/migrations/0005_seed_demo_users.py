from django.db import migrations
from django.contrib.auth.hashers import make_password


def seed_demo_users(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    admin_data = {
        'email': 'admin@slc.edu.ph',
        'full_name': 'System Admin',
        'role': 'admin',
        'is_staff': True,
        'is_superuser': True,
        'is_active': True,
    }
    user, created = User.objects.get_or_create(
        email=admin_data['email'],
        defaults=admin_data,
    )
    if created:
        user.password = make_password('admin123')
        user.save()


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0004_alter_user_email_alter_user_full_name_and_more'),
    ]

    operations = [
        migrations.RunPython(seed_demo_users, migrations.RunPython.noop),
    ]
