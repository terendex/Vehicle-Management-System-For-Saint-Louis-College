# Generated for the rule change that makes an unsettled Vehicle Pass fee a hard
# block on approval (see VehicleRegistration accept in vehicles/views.py).
from django.db import migrations


def return_unpaid_accepted(apps, schema_editor):
    """Send registrations accepted while still unpaid back to pending review.

    Until now CDSO could approve an application with the fee outstanding as
    long as they typed a justification into ``unpaid_accept_reason``. That is
    refused outright from now on, which leaves the rows approved under the old
    rule in a state the new one cannot produce: status=accepted with
    payment_status=unpaid.

    They are returned to ``pending`` rather than deleted or marked paid.
    Nothing is destroyed - the OR fields, the applicant, the issued vehicle
    and the original ``unpaid_accept_reason`` text are all left exactly as
    they are - so the application simply re-enters the review queue and can be
    approved again the moment a real Official Receipt is recorded against it.

    Deliberately NOT touched: the Vehicle row that the original approval
    created. Removing it here would revoke gate access from people who are
    already driving in on the strength of that pass, which is a decision for
    whoever reviews the application, not for a migration.
    """
    Registration = apps.get_model('vehicles', 'VehicleRegistration')
    Registration.objects.filter(status='accepted', payment_status='unpaid').update(status='pending')


def reapprove_unpaid(apps, schema_editor):
    """Reverse: re-accept the rows this migration sent back.

    ``unpaid_accept_reason`` is what makes this reversible at all. It is only
    ever written by the old approve-anyway path, so a pending row carrying one
    is exactly a row that was accepted while unpaid before the forward
    migration ran.
    """
    Registration = apps.get_model('vehicles', 'VehicleRegistration')
    (Registration.objects
        .filter(status='pending', payment_status='unpaid')
        .exclude(unpaid_accept_reason='')
        .update(status='accepted'))


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0084_normalize_organizer_plates'),
    ]

    operations = [
        migrations.RunPython(return_unpaid_accepted, reapprove_unpaid),
    ]
