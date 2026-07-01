from django.db import migrations


def remove_demo_users(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    User.objects.filter(
        email__in=['guard@slc.edu.ph', 'office@slc.edu.ph']
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0012_merge_0011_migrations'),
    ]

    operations = [
        migrations.RunPython(remove_demo_users, migrations.RunPython.noop),
    ]
