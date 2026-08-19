"""Remove audit rows that record a vehicle owner's own account activity.

Migration 0037 took owner *movement* out of the audit log. This takes out the
other half: what owners did to their own accounts — enabling two-factor,
regenerating backup codes, mistyping a code, setting their own plate number.
None of it is a staff act, so none of it belongs in an administrative trail
that admins can search and export.

Rows written before AuditLogManager existed often have no actor at all (they
came from server-shell sessions and render as "System"), so the actor column
alone cannot identify them. For the self-service actions the account holder is
the only person who can perform them, which makes target_user the one who
acted — that is the second branch below.

Staff acts on an owner's account are deliberately kept, owner named:
TWOFA_RESET by a CDSO, USER_CREATED/DISABLED/ARCHIVED, and so on. Those record
someone using a privilege over another person's account, which is the reason an
audit trail exists.
"""

from django.db import migrations
from django.db.models import Q


OWNER = 'vehicle_owner'

# Only the account holder can perform these, so an unattributed row is theirs.
SELF_SERVICE_ACTIONS = [
    'twofa_enabled', 'twofa_disabled', 'twofa_failed', 'twofa_backup_used',
]


def forwards(apps, schema_editor):
    AuditLog = apps.get_model('accounts', 'AuditLog')
    AuditLog.objects.filter(
        Q(actor__role=OWNER)
        | Q(actor__isnull=True, action__in=SELF_SERVICE_ACTIONS, target_user__role=OWNER)
    ).delete()


def backwards(apps, schema_editor):
    """Nothing to restore — the rows are deleted, which is the point."""


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0037_auditlog_drop_owner_movement'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
