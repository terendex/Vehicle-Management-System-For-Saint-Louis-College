"""Create admin-bell Notification rows for important system events.

Wired to model signals so every code path (gate auto-violations, manual CDSO
violations, public/online registrations, walk-in registrations, approvals,
rejections) produces a notification without each view having to remember to.
Creation is wrapped in try/except — a notification failure must never break
the domain write that triggered it.
"""
import logging
from django.db.models.signals import pre_save, post_save

logger = logging.getLogger(__name__)


def notify(category, event, title, message='', severity='info',
           plate_number='', link=''):
    from .models import Notification
    try:
        Notification.objects.create(
            category=category,
            event=event,
            severity=severity,
            title=title,
            message=message,
            plate_number=plate_number,
            link=link,
        )
    except Exception:
        logger.exception("[notifications] failed to create %s/%s", category, event)


# ── Violations ────────────────────────────────────────────────────────────────

def _on_violation_saved(sender, instance, created, **kwargs):
    if not created:
        return
    # The snapshot, so a conduction-only vehicle is named rather than blank and
    # the notification still reads correctly once the vehicle row is gone.
    plate = instance.identifier
    type_label = instance.get_violation_type_display()
    offense = f" (offense #{instance.offense_number})" if instance.offense_number else ''
    if instance.status == sender.Status.FEE_IMPOSED:
        severity = 'critical'
        title = f"Fee imposed — {plate}"
        message = (f"{type_label}{offense}: ₱{instance.fine_amount} fee imposed. "
                   f"Vehicle is blocked at the gate until cleared by CDSO.")
    else:
        severity = 'warning'
        title = f"Violation issued — {plate}"
        message = f"{type_label}{offense}. {instance.notes}".strip()
    notify(
        'violation', 'violation_issued', title, message,
        severity=severity, plate_number=plate, link='/admin/violations',
    )


# ── Vehicle registrations ─────────────────────────────────────────────────────

def _on_registration_presave(sender, instance, **kwargs):
    """Stash the previous status so post_save can detect transitions."""
    if instance.pk:
        try:
            instance._old_status = (
                sender.objects.filter(pk=instance.pk)
                .values_list('status', flat=True).first()
            )
        except Exception:
            instance._old_status = None
    else:
        instance._old_status = None


def _on_registration_saved(sender, instance, created, **kwargs):
    Status = sender.Status
    old_status = getattr(instance, '_old_status', None)
    plate = instance.plate_number or ''
    who = instance.full_name or instance.email

    if created:
        if instance.status == Status.PENDING:
            notify(
                'registration', 'registration_submitted',
                f"New registration — {plate}",
                f"{who} submitted a vehicle registration ({instance.get_registrant_type_display()}).",
                severity='info', plate_number=plate, link='/admin/vehicles',
            )
        elif instance.status == Status.ACCEPTED:
            # CDSO walk-in registrations are created already accepted
            notify(
                'registration', 'registration_accepted',
                f"Registration accepted — {plate}",
                f"Walk-in registration for {who} was recorded and accepted.",
                severity='info', plate_number=plate, link='/admin/vehicles',
            )
        return

    if old_status == instance.status or old_status is None:
        return

    if instance.status == Status.ACCEPTED:
        notify(
            'registration', 'registration_accepted',
            f"Registration approved — {plate}",
            f"Registration for {who} was approved.",
            severity='info', plate_number=plate, link='/admin/vehicles',
        )
    elif instance.status == Status.REJECTED:
        reason = f" Reason: {instance.rejection_reason}" if instance.rejection_reason else ''
        notify(
            'registration', 'registration_rejected',
            f"Registration rejected — {plate}",
            f"Registration for {who} was rejected.{reason}",
            severity='warning', plate_number=plate, link='/admin/vehicles',
        )


def connect_notification_signals():
    from violations.models import Violation
    from vehicles.models import VehicleRegistration

    post_save.connect(_on_violation_saved, sender=Violation,
                      dispatch_uid='notif_violation_saved', weak=False)
    pre_save.connect(_on_registration_presave, sender=VehicleRegistration,
                     dispatch_uid='notif_registration_presave', weak=False)
    post_save.connect(_on_registration_saved, sender=VehicleRegistration,
                      dispatch_uid='notif_registration_saved', weak=False)
