from django.db import migrations, models
from django.db.models import Q
from django.db.models.functions import Lower


# Accounts archived on expiry before the job closed their violations still show
# those warnings as active. Close them now, the way the job does from here on
# (Violation.archive_standing_for_owners): unbanned archived owners only, and
# only what is still standing. A banned owner's 3rd offence stays as it is.
def archive_standing_of_archived_owners(apps, schema_editor):
    # The column's CHECK (accounts/db_choice_checks.py) still lists the old
    # values until post_migrate rewrites it after this migrate, so it would
    # refuse 'archived' here. Drop it; the post_migrate sync adds it back with
    # the new value included.
    if schema_editor.connection.vendor == 'postgresql':
        from accounts.db_choice_checks import _constraint_name
        name = schema_editor.quote_name(_constraint_name('tbl_violation', 'status'))
        schema_editor.execute(f'ALTER TABLE tbl_violation DROP CONSTRAINT IF EXISTS {name}')

    User = apps.get_model('accounts', 'User')
    Violation = apps.get_model('violations', 'Violation')
    owners = list(User.objects.filter(role='vehicle_owner', is_archived=True, registration_banned=False)
                  .values_list('pk', 'email'))
    if not owners:
        return
    ids = [pk for pk, _ in owners]
    emails = [email.lower() for _, email in owners if email]
    (Violation.objects.annotate(_email=Lower('owner_email'))
     .filter(Q(owner_id__in=ids) | Q(vehicle__user_id__in=ids)
             | Q(owner__isnull=True, _email__in=emails),
             is_resolved=False, status__in=('warning', 'fee_imposed'))
     .update(status='archived', is_resolved=True))


class Migration(migrations.Migration):

    dependencies = [
        ('violations', '0018_violation_overstay_minutes'),
        ('accounts', '0029_user_registration_banned'),
    ]

    operations = [
        migrations.AlterField(
            model_name='violation',
            name='status',
            field=models.CharField(choices=[('warning', 'Warning'), ('fee_imposed', 'Fee Imposed (Legacy)'), ('cleared', 'Cleared'), ('lifted', 'Lifted (False Alarm)'), ('archived', 'Archived (Account Expired)')], default='warning', max_length=20),
        ),
        migrations.RunPython(archive_standing_of_archived_owners, migrations.RunPython.noop),
    ]
