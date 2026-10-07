from __future__ import annotations

import logging
import os

from celery import shared_task
from django.db import models
from django.utils import timezone

log = logging.getLogger(__name__)


@shared_task(name="vehicles.purge_old_records")
def purge_old_records():
    """Apply the retention window: delete AccessLog rows, Violation rows, and
    archived accounts older than it.

    Archived accounts are the end of the owner lifecycle — expiry archives them
    (recoverable, history intact), and retention deletes them once the window
    passes. Only archived accounts are ever deleted here; a live account is
    never touched no matter how old it is.

    `archived_at` is the clock, not `date_joined`: the retention window is time
    spent archived, so an account archived yesterday survives its full term even
    if it was created ten years ago. A row with is_archived but no archived_at
    (archived before that field existed) has no clock to measure and is left
    alone rather than deleted on a guess.
    """
    from dateutil.relativedelta import relativedelta

    from accounts.models import User, delete_users_with_owned_records
    from .models import SystemSettings
    from scanning.models import AccessLog
    from violations.models import Violation

    cfg = SystemSettings.get()
    # relativedelta, not days=years*365 — the latter drifts a day every leap year.
    cutoff = timezone.now() - relativedelta(years=cfg.retention_years)

    deleted_logs, _       = AccessLog.objects.filter(scanned_at__lt=cutoff).delete()
    deleted_violations, _ = Violation.objects.filter(issued_at__lt=cutoff).delete()

    # Handed over as a queryset, not iterated: the sweep runs as a subquery, so
    # this is a fixed handful of statements whether it deletes one account or
    # ten thousand. `user_archived_at` (partial, WHERE is_archived) is the index
    # that keeps finding them a range scan instead of a walk of every user.
    stale = User.objects.filter(
        is_archived=True,
        archived_at__isnull=False,
        archived_at__lt=cutoff,
    ).exclude(role=User.Role.ADMIN)
    _, _, deleted_accounts = delete_users_with_owned_records(stale)

    log.info(
        "[purge_old_records] Removed %d AccessLog + %d Violation records and %d archived "
        "account(s) older than %d year(s)",
        deleted_logs, deleted_violations, deleted_accounts, cfg.retention_years,
    )
    return {
        "deleted_logs": deleted_logs,
        "deleted_violations": deleted_violations,
        "deleted_accounts": deleted_accounts,
    }


@shared_task(name="vehicles.auto_archive_expired_accounts")
def auto_archive_past_visits():
    """Archive scheduled visits the day after their date, keeping their outcome
    (Arrived / No-show) — see vehicles.scheduled_visits.auto_archive_due.
    Idempotent: archived visits are not live, so a re-run finds nothing."""
    from .scheduled_visits import auto_archive_due
    archived = auto_archive_due()
    log.info("[auto_archive_past_visits] Archived %d visit(s)", archived)
    return {"archived": archived}


def auto_archive_expired_accounts():
    """Archive vehicle-owner accounts whose expires_at has passed.

    Archiving = is_archived + is_active False, the owner's active registrations
    moved to EXPIRED (which frees their plate/email/ID for re-registration), an
    audit entry, and a notification email. Idempotent: already-archived rows are
    excluded, so re-running does nothing. No-op unless expiry is enabled.

    Done as a batch, not an account at a time: the whole run is a fixed number of
    statements (plus one email each, which is inherently per-owner). The DB work
    sits in one transaction — the jobs are idempotent and the scheduler releases
    a failed claim and retries within the hour, so an all-or-nothing batch is
    safer than a half-finished walk.
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
    due = list(User.objects.filter(
        role=User.Role.VEHICLE_OWNER,
        is_active=True,
        is_archived=False,
        expires_at__isnull=False,
        expires_at__lt=today,
    ))
    if not due:
        log.info("[auto_archive_expired_accounts] Nothing due (today=%s)", today)
        return {"archived": 0, "banned": 0}

    due_ids = [u.pk for u in due]

    # Who reached maximum violations? A registration-blocking (3rd-offense) flag
    # on any of the owner's vehicles means the person may not register again.
    # One query for the whole batch rather than an .exists() per account — and it
    # has to run before the unlink below, which is what severs vehicle -> user.
    banned_ids = set(
        Violation.objects
        .filter(vehicle__user_id__in=due_ids, registration_blocked=True)
        .values_list('vehicle__user_id', flat=True)
    )

    next_window = RegistrationPeriod.get_active()
    now = timezone.now()
    banned    = [u for u in due if u.pk in banned_ids]
    unbanned  = [u for u in due if u.pk not in banned_ids]
    active_reg = [VehicleRegistration.Status.PENDING, VehicleRegistration.Status.ACCEPTED]

    with transaction.atomic():
        # An unbanned owner's warnings close with the account; a banned owner's
        # 3rd offense stays standing, as the record of the ban. Before the
        # vehicle unlink below, which one of the matches goes through.
        archived_violations = Violation.archive_standing_for_owners(unbanned)

        for group, is_banned in ((banned, True), (unbanned, False)):
            if not group:
                continue
            ids = [u.pk for u in group]
            User.objects.filter(pk__in=ids).update(
                is_archived=True, is_active=False, archived_at=now,
                registration_banned=is_banned,
            )
            # Vehicles are no longer authorized. For a non-banned owner also
            # unlink the plate (user=None) so _plate_conflict frees it for
            # re-registration. A banned owner keeps the link so the plate stays
            # traceably blocked.
            owner_vehicles = Vehicle.objects.filter(user_id__in=ids)
            if is_banned:
                owner_vehicles.update(is_authorized=False)
            else:
                owner_vehicles.update(is_authorized=False, user=None)

        # Move active registrations → EXPIRED (releases the registration-level
        # uniqueness on plate / email / IDs / license). Matched by owner *or* by
        # address, for rows submitted before the account existed. Both sides are
        # stored lower-cased, so `__in` is the batch form of the old `__iexact`.
        emails = [u.email.lower() for u in due]
        VehicleRegistration.objects.filter(
            models.Q(user_id__in=due_ids) | models.Q(email__in=emails),
            status__in=active_reg,
        ).update(status=VehicleRegistration.Status.EXPIRED)

        AuditLog.objects.bulk_create([
            AuditLog(
                actor=None,  # system job
                action=AuditLog.Action.USER_ARCHIVED,
                target_user=user,
                details=(f"Account auto-archived on expiry | Expired: {user.expires_at} | "
                         f"{user.email}" + (" | BANNED (max violations)" if user.pk in banned_ids else "")
                         + (f" | {archived_violations[user.pk]} violation(s) archived"
                            if archived_violations.get(user.pk) else "")),
            )
            for user in due
        ])

    # Outside the transaction: a bounced email must not roll back the archiving,
    # and an SMTP round trip must not be holding a DB transaction open.
    for user in due:
        try:
            send_account_archived_email(
                user, banned=user.pk in banned_ids, next_window=next_window)
        except Exception:
            log.exception("[auto_archive_expired_accounts] email failed for %s", user.email)

    log.info("[auto_archive_expired_accounts] Archived %d expired owner account(s), %d banned (today=%s)",
             len(due), len(banned), today)
    return {"archived": len(due), "banned": len(banned),
            "violations_archived": sum(archived_violations.values())}


@shared_task(name="vehicles.expire_unpaid_registrations")
def expire_unpaid_registrations():
    """Expire online applications whose 3-day payment deadline has passed, and
    remind the ones whose deadline is under a day away.

    Hourly on the in-process scheduler (see scheduler.HOURLY_JOBS); the rule
    and the work are in vehicles.registration_deadline. Idempotent.

    Hourly claims put 24 rows a day in the job ledger, and each reminder adds
    one more; nothing reads either after a week, so this job clears out its
    own week-old ones. Expiry runs first, so an application is never reminded
    in the same pass that expires it.
    """
    from datetime import timedelta

    from django.db.models import Q

    from .models import DailyJobRun
    from .registration_deadline import REMINDER_JOB, expire_overdue, remind_due

    expired = expire_overdue()
    reminded = remind_due()
    DailyJobRun.objects.filter(
        Q(job__startswith='expire_unpaid_registrations:') | Q(job__startswith=f'{REMINDER_JOB}:'),
        run_date__lt=timezone.localdate() - timedelta(days=7),
    ).delete()
    return {"expired": len(expired), "reminded": len(reminded)}


@shared_task(name="vehicles.auto_manage_events")
def auto_manage_events():
    """Point every event's stored flags at its date: today's active, past ones
    archived, coming ones pending.

    Runs daily on the in-process scheduler (vehicles/scheduler.py). Campus
    (Manila) date, never date.today(): the cloud server's own clock is UTC, and
    the first pass after Manila midnight would still be yesterday there. Three
    fixed queries whatever the number of events, and each also repairs a row
    whose flags were edited by hand to disagree with its date.
    """
    from django.db.models import Q
    from .models import Event

    today = timezone.localdate()

    activated = (Event.objects.filter(date=today).filter(Q(is_active=False) | Q(archived=True))
                 .update(is_active=True, archived=False))
    archived  = (Event.objects.filter(date__lt=today).filter(Q(is_active=True) | Q(archived=False))
                 .update(is_active=False, archived=True))
    pending   = (Event.objects.filter(date__gt=today).filter(Q(is_active=True) | Q(archived=True))
                 .update(is_active=False, archived=False))

    # .update() fires no post_save, so the realtime signal never sees these —
    # an Events page left open overnight would keep yesterday's badges.
    if activated or archived or pending:
        from realtime.broadcast import broadcast_change
        broadcast_change("event", "changed")

    log.info("[auto_manage_events] Activated %d, archived %d, reset %d to pending (today=%s)",
             activated, archived, pending, today)
    return {"activated": activated, "archived": archived, "pending": pending}


@shared_task(name="vehicles.auto_backup")
def auto_backup():
    """Take a scheduled backup of system data, then rotate the old ones.

    The scheduler calls this on every pass (hourly); the *frequency* setting is
    applied here rather than there, by asking how old the newest automatic
    backup is. A weekly schedule is therefore "at least seven days since the
    last one", not "every Monday" — a server that was switched off on Monday
    still gets its weekly backup when it next boots, which is the same catch-up
    behaviour the rest of the daily jobs have.

    Because the age of the file on disk is the clock, no state can drift out of
    step with reality: deleting the newest backup makes the next pass take one,
    and a restored database cannot make the schedule think it already ran.

    Hourly is the fastest setting, and it is bounded by how often the scheduler
    wakes — once an hour. The slack below is what absorbs the few seconds or
    minutes of drift in when that wake-up actually lands, so an hourly schedule
    does not skip an hour just for arriving 59 minutes and 50 seconds later.

    Nothing here can lose data: it only ever writes a new backup and rotates
    older routine ones beyond the keep count. The pre-restore snapshots — the
    files someone reaches for after a bad restore — are never rotated.
    """
    from dateutil.relativedelta import relativedelta

    from accounts.backup_utils import (
        AUTO_PREFIX, latest_auto_backup, prune_backups, write_backup,
    )
    from django.utils.dateparse import parse_datetime
    from .models import SystemSettings

    cfg = SystemSettings.get()
    freq = cfg.auto_backup_frequency
    if freq == 'off':
        return {"skipped": "automatic backups are off"}

    now = timezone.now()
    # (interval, slack). The slack has to stay well under the interval — an hour
    # of it would make "hourly" mean "every pass", which is the one thing the
    # age check is here to prevent.
    interval, slack = {
        'hourly':  (relativedelta(hours=1),   relativedelta(minutes=5)),
        'daily':   (relativedelta(days=1),    relativedelta(hours=1)),
        'weekly':  (relativedelta(weeks=1),   relativedelta(hours=1)),
        'monthly': (relativedelta(months=1),  relativedelta(hours=1)),
    }.get(freq, (None, None))
    if interval is None:
        log.warning("[auto_backup] unknown frequency %r — skipping", freq)
        return {"skipped": f"unknown frequency {freq}"}

    latest = latest_auto_backup()
    if latest:
        due_at = parse_datetime(latest['created_at']) + interval - slack
        if now < due_at:
            log.info("[auto_backup] not due yet — next after %s", due_at)
            return {"skipped": "not due", "next_due": due_at.isoformat()}

    name, size = write_backup(AUTO_PREFIX)
    removed = prune_backups(cfg.auto_backup_keep)

    log.info("[auto_backup] wrote %s (%d bytes); pruned %d old backup(s)",
             name, size, len(removed))
    return {"created": name, "bytes": size, "pruned": len(removed)}


@shared_task(name="vehicles.scheduled_backup")
def scheduled_backup():
    """Take the calendar-scheduled backup into the chosen folder, if one is due.

    Separate from auto_backup and deliberately different in kind: that one is
    "at least N since the last", this one is pinned to the calendar — daily at
    a time, weekly on a weekday, monthly on a day — and can write somewhere
    other than the app's own folder.

    Due means: the most recent slot the schedule called for has no scheduled
    backup in the folder taken at or after it. That gives the same catch-up as
    every other job here — a PC switched off at 5 PM on Friday takes Friday's
    backup when it next boots — and it cannot double up, because the file
    itself is the record of the slot being done. Switching the schedule on (or
    pointing it at a new folder) after today's time has passed therefore takes
    one straight away, which doubles as proof the folder works.

    Missing drive or unwritable folder: raises, so the scheduler logs it and
    retries on the next pass instead of quietly marking the day done. The one
    exception is a folder that is not a path on this machine at all — the
    cloud server reading a Windows path set on the campus PC — which is not
    this machine's job and is skipped.
    """
    from accounts.backup_utils import (
        BackupFolderError, latest_scheduled_backup, prune_scheduled, scheduled_dir,
        scheduled_folder_setting, scheduled_prefix, scheduled_slots, write_backup,
    )
    from django.utils.dateparse import parse_datetime
    from .models import SystemSettings

    cfg = SystemSettings.get()
    if cfg.scheduled_backup_frequency == 'off':
        return {"skipped": "scheduled backups are off"}

    folder_setting = scheduled_folder_setting()
    if folder_setting and not os.path.isabs(folder_setting):
        return {"skipped": "folder is not on this machine"}
    try:
        folder = scheduled_dir(folder_setting)
    except BackupFolderError as exc:
        raise RuntimeError(str(exc)) from exc

    previous, upcoming = scheduled_slots(cfg)
    latest = latest_scheduled_backup(folder)
    if latest and parse_datetime(latest['created_at']) >= previous:
        return {"skipped": "not due", "next_due": upcoming.isoformat()}

    name, size = write_backup(scheduled_prefix(), folder=folder)
    removed = prune_scheduled(cfg.scheduled_backup_keep, folder)

    log.info("[scheduled_backup] wrote %s to %s (%d bytes); pruned %d old backup(s)",
             name, folder, size, len(removed))
    return {"created": name, "folder": folder, "bytes": size, "pruned": len(removed)}
