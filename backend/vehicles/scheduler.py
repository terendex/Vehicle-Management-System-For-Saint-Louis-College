"""In-process daily scheduler.

The archiving job is defined as a Celery task and scheduled in
`config/celery.py`, which only fires when a worker AND a beat scheduler are
running — neither is deployed, because both would be extra always-on
containers. The documented fallback is a Windows Scheduled Task calling
`manage.py run_maintenance`, but that has to be registered by hand on the campus
machine, and if nobody registers it the System Settings card promises automatic
archiving that never happens.

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
import sys
import threading

from django.db import IntegrityError, transaction
from django.utils import timezone

log = logging.getLogger(__name__)

# How often the thread re-checks. The jobs are date-keyed, so this is only the
# granularity of "how soon after midnight (or after a boot) does it run", not a
# schedule in itself. Hourly keeps the idle cost at 24 cheap queries a day.
CHECK_INTERVAL_SECONDS = 3600

# Jobs the server runs by itself, in order. Deliberately NOT purge_old_records:
# that one deletes AccessLog and Violation rows, and turning a delete loose on a
# schedule nobody has been running is not a side effect to introduce quietly.
# It stays on `manage.py run_maintenance`.
DAILY_JOBS = (
    'auto_archive_expired_accounts',
)

_started = False
_lock = threading.Lock()
_stop = threading.Event()


def _claim(job: str, today):
    """Insert today's ledger row for `job`. Returns the row, or None if another
    process (or an earlier run today) already claimed it."""
    from .models import DailyJobRun
    try:
        with transaction.atomic():
            return DailyJobRun.objects.create(job=job, run_date=today)
    except IntegrityError:
        return None


def run_due_jobs(force: bool = False) -> dict:
    """Run each daily job that has not run today. Safe to call at any time."""
    from . import tasks

    today = timezone.localdate()
    outcomes: dict[str, str] = {}

    for job in DAILY_JOBS:
        row = _claim(job, today)
        if row is None and not force:
            continue

        failed = False
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

        outcomes[job] = summary
        if row is not None:
            if failed:
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
        _stop.wait(CHECK_INTERVAL_SECONDS)


def stop():
    """Ask the loop to exit at its next wake-up. Used by tests; the thread is a
    daemon, so process shutdown does not need it."""
    _stop.set()


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
    threading.Thread(target=_loop, name='daily-scheduler', daemon=True).start()
    log.info("[scheduler] started — checking every %ds for: %s",
             CHECK_INTERVAL_SECONDS, ', '.join(DAILY_JOBS))
