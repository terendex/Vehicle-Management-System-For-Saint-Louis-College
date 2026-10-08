"""In-process daily scheduler.

The archive and purge jobs are defined as Celery tasks and scheduled in
`config/celery.py`, which only fires when a worker AND a beat scheduler are
running — neither is deployed, because both would be extra always-on
containers. The documented fallback is a Windows Scheduled Task calling
`manage.py run_maintenance`, but that has to be registered by hand on the campus
machine, and if nobody registers it the System Settings cards promise automatic
archiving and retention that never happen.

So the server runs it itself. A daemon thread started with the ASGI app wakes up
periodically and asks the DailyJobRun ledger "has this job run today?" — not
"is it 00:05 now?" — so:

  * a machine switched off overnight runs the job when it next boots,
  * a restart cannot re-run a job that already completed today,
  * a job that *failed* releases its claim and retries on the next pass, so one
    bad run does not cost a day,
  * starting the thread in several processes is harmless, because claiming the
    day is a unique INSERT and only one process can win it.

This does not replace `manage.py run_maintenance`; that command still runs every
job on demand and ignores the ledger.
"""
from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import zlib

from django.db import IntegrityError, transaction
from django.utils import timezone

log = logging.getLogger(__name__)

# How often the thread re-checks. The jobs are date-keyed, so this is mostly the
# granularity of "how soon after midnight (or after a boot) does it run", not a
# schedule in itself. Hourly keeps the idle cost at 24 cheap queries a day.
#
# Automatic backups do not wait for it: the loop wakes at their exact time.
CHECK_INTERVAL_SECONDS = 3600

# Jobs the server runs by itself, in order: back up, archive, then purge. Each
# is something a screen promises happens automatically (System Settings, the
# Events list), so none may depend on someone remembering to register a
# Windows scheduled task.
#
# Order matters. Archiving stamps archived_at, and the purge measures the
# retention window from it — so a run that archives and purges in that order
# gives every account its full window, while the reverse could delete an account
# on the same pass that archived it if the clocks ever lined up.
#
# The backup runs first, before either of the jobs that delete rows. A snapshot
# taken ahead of the purge still contains the records the purge is about to
# remove, so the day's backup is the copy someone can go back to if the
# retention window turns out to have been set too short.
#
# auto_manage_events rolls event status over (today active, past archived).
# It deletes nothing, so it sits with the other archiving jobs; the gate and
# the parking reserve read the date directly, so the up-to-an-hour wait for
# this pass after midnight never lets a finished event act.
#
# scheduled_backup is the automatic backup, pinned to the calendar ("Fridays
# at 5 PM"), and the loop below wakes at its exact time rather than up to an
# hour after it.
DAILY_JOBS = (
    'scheduled_backup',
    'auto_manage_events',
    'expire_unpaid_registrations',
    'auto_archive_expired_accounts',
    'auto_archive_past_visits',
    'purge_old_records',
)

# Jobs claimed once per HOUR rather than once per day. The registration payment
# deadline is a time of day ("3 days from 2:14 PM"), and a once-a-day sweep
# would leave an expired application holding its plate and schedule slot for up
# to a day. Not keyed by machine like the backups: the work is all in the shared
# database, so a sweep by either server is the same sweep.
HOURLY_JOBS = frozenset({'expire_unpaid_registrations'})

_started = False
_lock = threading.Lock()
_stop = threading.Event()
# Set by wake() to cut the current sleep short — a settings save that moves the
# scheduled-backup time must not wait out an hour-long sleep computed before it.
_wake = threading.Event()


def _claim(job: str, today):
    """Insert today's ledger row for `job`. Returns the row, or None if another
    process (or an earlier run today) already claimed it."""
    from .models import DailyJobRun
    try:
        with transaction.atomic():
            return DailyJobRun.objects.create(job=job, run_date=today)
    except IntegrityError:
        return None


def _claim_key(job: str) -> str:
    """The ledger key this job claims on this pass.

    Almost everything here runs once a day, so the key is just the job name and
    the (job, run_date) unique constraint allows exactly one run per day.

    Automatic backups are the exception, twice over. They are keyed by
    machine: the ledger lives in the shared database but the backup file lands
    on the local disk, so when two servers point at one database (the campus
    PC and a cloud deployment) a shared key let whichever woke first take the
    backup — onto its own disk — and the campus PC, whose System Settings lists
    only its own folder, never took another one. Each machine claims its own.

    Their key also carries a fingerprint of the SLOT being served (the latest
    moment the schedule called for, plus the folder). Keyed by day instead, a catch-up
    backup taken in the morning for yesterday's 5 PM would hold the day's claim
    and silently skip today's 5 PM. Moving the time or the folder is likewise
    a new slot, looked at again on the next pass. The file in the folder, not
    the claim, is what stops a second backup for the same slot.

    HOURLY_JOBS always take the hour-keyed form ("job:hNN").
    """
    if job in HOURLY_JOBS:
        return f'{job}:h{timezone.localtime():%H}'
    if job == 'scheduled_backup':
        from accounts.backup_utils import scheduled_slots
        from .models import SystemSettings
        try:
            cfg = SystemSettings.get()
            slot, _ = scheduled_slots(cfg)
        except Exception:                               # noqa: BLE001 — pre-migrate
            return job
        schedule = '|'.join(str(v) for v in (
            slot.isoformat() if slot else 'off',
            (cfg.scheduled_backup_folder or '').strip(),
        ))
        # 64 wide: 17 for "scheduled_backup@", 30 of hostname, 9 for "#xxxxxxxx".
        return f'{job}@{socket.gethostname()[:30]}#{zlib.crc32(schedule.encode()):08x}'
    return job


# One pass at a time in this process. The claims already stop two processes
# doing the same day's job, but the instructor demo's Run jobs now runs the
# jobs without a claim, in the same process, often right as a clock move has
# woken this thread: two purges or two backups would then run side by side.
pass_lock = threading.Lock()


def run_due_jobs(force: bool = False) -> dict:
    """Run each daily job that has not run today. Safe to call at any time."""
    with pass_lock:
        return _run_due_jobs(force)


def _run_due_jobs(force: bool) -> dict:
    from . import tasks

    today = timezone.localdate()
    outcomes: dict[str, str] = {}

    # A deployment may leave jobs out (SCHEDULER_SKIP_JOBS).
    from django.conf import settings
    skip_jobs = set(getattr(settings, 'SCHEDULER_SKIP_JOBS', ()))
    for job in DAILY_JOBS:
        if job in skip_jobs:
            continue
        row = _claim(_claim_key(job), today)
        if row is None and not force:
            continue

        failed = False
        result = None
        try:
            result = getattr(tasks, job)()
            summary = str(result)[:255]
            log.info("[scheduler] %s: %s", job, summary)
        except Exception as exc:
            # A failing job must not kill the thread and take every later run
            # with it.
            failed = True
            summary = f"failed: {exc}"[:255]
            log.exception("[scheduler] %s failed", job)

        # A backup that was not due yet has not done the slot's work. Keeping
        # the claim would make a daily backup skip a whole day whenever the
        # first pass after a restart lands a little before the 24 hours are up.
        # The file age decides; the claim only stops two processes writing at
        # once.
        skipped = (job == 'scheduled_backup' and isinstance(result, dict)
                   and result.get('skipped') == 'not due')

        outcomes[job] = summary
        if row is not None:
            if failed or skipped:
                # Release the claim so the next pass retries in an hour rather
                # than leaving the day unarchived. The jobs are idempotent, so a
                # partial run finishing on the retry is correct; a job that keeps
                # failing keeps logging, which is the visible signal.
                row.delete()
            else:
                row.finished_at = timezone.now()
                row.result = summary
                row.save(update_fields=['finished_at', 'result'])

    return outcomes


def _loop():
    from django.db import close_old_connections

    while not _stop.is_set():
        try:
            # Long-lived thread: hand back connections between passes so it does
            # not sit on a Postgres connection for hours at a time.
            close_old_connections()
            run_due_jobs()
        except Exception:                              # noqa: BLE001
            # Includes the pre-migrate case where tbl_daily_job_run does not
            # exist yet. Log and retry rather than killing the thread.
            log.exception("[scheduler] pass failed; will retry")
        finally:
            close_old_connections()
        timeout = _seconds_until_next_pass()
        close_old_connections()
        _wake.wait(timeout)
        _wake.clear()


def _seconds_until_next_pass() -> float:
    """The hourly interval, or less when an automatic backup falls due sooner.

    The hourly pass is fine for "sometime after midnight", but "Fridays at
    5 PM" should mean 5 PM, not whenever the hour's wake-up happens to land.
    """
    try:
        from accounts.backup_utils import scheduled_slots
        from .models import SystemSettings
        _, upcoming = scheduled_slots(SystemSettings.get())
    except Exception:                                  # noqa: BLE001 — pre-migrate, DB down
        return CHECK_INTERVAL_SECONDS
    if upcoming is None:
        return CHECK_INTERVAL_SECONDS
    # A couple of seconds past the slot, so the pass lands after it, not on it.
    until = (upcoming - timezone.now()).total_seconds() + 2
    return max(1.0, min(CHECK_INTERVAL_SECONDS, until))


def wake():
    """Run a pass now instead of at the end of the current sleep. Called when
    the backup schedule is saved; a no-op if the thread is not running."""
    _wake.set()


def stop():
    """Ask the loop to exit at its next wake-up. Used by tests; the thread is a
    daemon, so process shutdown does not need it."""
    _stop.set()
    _wake.set()


def start():
    """Start the scheduler thread once per process. No-op when disabled, and
    when running a management command that is not the server."""
    global _started

    if os.getenv('DISABLE_DAILY_SCHEDULER', '').lower() in ('1', 'true', 'yes'):
        log.info("[scheduler] disabled by DISABLE_DAILY_SCHEDULER")
        return

    # Migrations, tests, shells and one-shot commands must not start a thread
    # that writes to the database. Only the server process schedules.
    argv = ' '.join(sys.argv)
    is_server = (
        'daphne' in argv or 'uvicorn' in argv or 'gunicorn' in argv
        or 'runserver' in argv
    )
    if not is_server:
        return

    # runserver's autoreloader runs the app twice; only the reloaded child
    # (RUN_MAIN=true) should own the thread.
    if 'runserver' in argv and os.environ.get('RUN_MAIN') != 'true':
        return

    with _lock:
        if _started:
            return
        _started = True

    _stop.clear()   # a previous stop() must not kill the new thread instantly
    _wake.clear()
    threading.Thread(target=_loop, name='daily-scheduler', daemon=True).start()
    log.info("[scheduler] started — checking every %ds for: %s",
             CHECK_INTERVAL_SECONDS, ', '.join(DAILY_JOBS))
