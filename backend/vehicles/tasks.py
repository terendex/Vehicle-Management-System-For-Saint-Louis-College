from __future__ import annotations

import logging
from datetime import date, timedelta

from celery import shared_task
from django.db import models
from django.utils import timezone

log = logging.getLogger(__name__)


@shared_task(name="vehicles.purge_old_records")
def purge_old_records():
    """Delete AccessLog and Violation rows older than the configured retention period."""
    from .models import SystemSettings
    from scanning.models import AccessLog
    from violations.models import Violation

    cfg = SystemSettings.get()
    cutoff = timezone.now() - timedelta(days=cfg.retention_years * 365)

    deleted_logs, _       = AccessLog.objects.filter(scanned_at__lt=cutoff).delete()
    deleted_violations, _ = Violation.objects.filter(issued_at__lt=cutoff).delete()

    log.info(
        "[purge_old_records] Removed %d AccessLog + %d Violation records older than %d year(s)",
        deleted_logs, deleted_violations, cfg.retention_years,
    )
    return {"deleted_logs": deleted_logs, "deleted_violations": deleted_violations}


@shared_task(name="vehicles.auto_archive_expired_accounts")
def auto_archive_expired_accounts():
    """Archive vehicle-owner accounts whose expires_at has passed.

    Archiving = is_archived + is_active False, the owner's active registrations
    moved to EXPIRED (which frees their plate/email/ID for re-registration), an
    audit entry, and a notification email. Idempotent: already-archived rows are
    excluded, so re-running does nothing. No-op unless expiry is enabled.
    """
    from django.db import transaction
    from accounts.models import User, AuditLog
    from violations.models import Violation
    from .models import SystemSettings, VehicleRegistration, Vehicle, RegistrationPeriod
    from .email_utils import send_account_archived_email

    cfg = SystemSettings.get()
    if not cfg.account_expiry_enabled:
        return {"archived": 0, "skipped": "expiry disabled"}

    today = timezone.localdate()
    due = User.objects.filter(
        role=User.Role.VEHICLE_OWNER,
        is_active=True,
        is_archived=False,
        expires_at__isnull=False,
        expires_at__lt=today,
    )

    next_window = RegistrationPeriod.get_active()
    archived = 0
    banned_count = 0
    active_reg = [VehicleRegistration.Status.PENDING, VehicleRegistration.Status.ACCEPTED]
    for user in due:
        # Reached maximum violations? A registration-blocking (3rd-offense) flag on
        # any of the owner's vehicles means the person may not register again.
        reached_max = Violation.objects.filter(
            vehicle__user=user, registration_blocked=True,
        ).exists()

        with transaction.atomic():
            user.is_archived         = True
            user.is_active           = False
            user.archived_at         = timezone.now()
            user.registration_banned = reached_max
            user.save(update_fields=['is_archived', 'is_active', 'archived_at', 'registration_banned'])

            # Move active registrations → EXPIRED (releases the registration-level
            # uniqueness on plate / email / IDs / license).
            VehicleRegistration.objects.filter(
                models.Q(user=user) | models.Q(email__iexact=user.email),
                status__in=active_reg,
            ).update(status=VehicleRegistration.Status.EXPIRED)

            # Vehicles are no longer authorized. For a non-banned owner also unlink
            # the plate (user=None) so _plate_conflict frees it for re-registration.
            # A banned owner keeps the link so the plate stays traceably blocked.
            owner_vehicles = Vehicle.objects.filter(user=user)
            if reached_max:
                owner_vehicles.update(is_authorized=False)
            else:
                owner_vehicles.update(is_authorized=False, user=None)

            AuditLog.objects.create(
                actor=None,  # system job
                action=AuditLog.Action.USER_ARCHIVED,
                target_user=user,
                details=(f"Account auto-archived on expiry | Expired: {user.expires_at} | "
                         f"{user.email}" + (" | BANNED (max violations)" if reached_max else "")),
            )
        try:
            send_account_archived_email(user, banned=reached_max, next_window=next_window)
        except Exception:
            log.exception("[auto_archive_expired_accounts] email failed for %s", user.email)
        archived += 1
        banned_count += 1 if reached_max else 0

    log.info("[auto_archive_expired_accounts] Archived %d expired owner account(s), %d banned (today=%s)",
             archived, banned_count, today)
    return {"archived": archived, "banned": banned_count}


@shared_task(name="vehicles.auto_manage_events")
def auto_manage_events():
    """Activate events whose date is today; archive events whose date has passed."""
    from .models import Event

    today = date.today()

    activated = Event.objects.filter(date=today, is_active=False, archived=False).update(is_active=True)
    archived  = Event.objects.filter(date__lt=today, archived=False).update(is_active=False, archived=True)

    log.info("[auto_manage_events] Activated %d, archived %d events (today=%s)", activated, archived, today)
    return {"activated": activated, "archived": archived}
