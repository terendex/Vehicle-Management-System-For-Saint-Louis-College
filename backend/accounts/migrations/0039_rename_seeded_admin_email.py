"""Move the seeded administrator onto the CDSO office address.

Migration 0005 seeds the one administrator the system ships with, and it now
seeds cdso.slc.sflu@gmail.com. That only covers a database built from scratch:
0005 has already run on every existing install, so the row there still carries
the old admin@slc.edu.ph. This renames it forward.

The email is the USERNAME_FIELD, so this changes what that administrator types
at /login. The password is untouched.

Two guards, because a rename can collide where a create cannot:

  * Nothing to rename on a fresh database - 0005 already wrote the new
    address - so the filter simply matches no rows and this is a no-op.
  * If the new address is already taken by a live account, renaming would
    trip uniq_active_user_email (partial: unique WHERE is_archived = false)
    and fail the whole deploy. That account is the destination anyway, so the
    old row is left alone and a human can decide.
"""

from django.db import migrations


OLD_EMAIL = 'admin@slc.edu.ph'
NEW_EMAIL = 'cdso.slc.sflu@gmail.com'


def _rename(apps, frm, to):
    User = apps.get_model('accounts', 'User')
    if User.objects.filter(email=to, is_archived=False).exists():
        return
    # Live rows only. Archived accounts sit outside the partial unique index,
    # so an old archived namesake can stay on the address it was archived with.
    User.objects.filter(email=frm, is_archived=False).update(email=to)


def forwards(apps, schema_editor):
    _rename(apps, OLD_EMAIL, NEW_EMAIL)


def backwards(apps, schema_editor):
    _rename(apps, NEW_EMAIL, OLD_EMAIL)


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0038_purge_owner_activity_audit'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
