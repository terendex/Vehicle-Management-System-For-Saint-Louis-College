from django.db import migrations, models


def cdso_to_admin(apps, schema_editor):
    """CDSO is the admin office — fold any legacy 'cdso' users into 'admin'."""
    User = apps.get_model('accounts', 'User')
    User.objects.filter(role='cdso').update(role='admin')


def admin_to_cdso(apps, schema_editor):
    """Reverse is a best-effort no-op: we cannot tell which admins were cdso."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0023_alter_user_gate_assignment'),
    ]

    operations = [
        migrations.RunPython(cdso_to_admin, admin_to_cdso),
        migrations.AlterField(
            model_name='user',
            name='role',
            field=models.CharField(
                choices=[
                    ('admin', 'CDSO'),
                    ('security', 'Security Personnel'),
                    ('vehicle_owner', 'Registered Vehicle Owner'),
                ],
                default='vehicle_owner',
                max_length=20,
            ),
        ),
    ]
