"""What the Test Clock page and `manage.py sim_clock` can do, in one place.

Only reachable when sim_settings installed the simulated clock (the URL is not
even registered otherwise, and every function here refuses).
"""
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

import sim_clock

# Every time-based job, in the scheduler's order (vehicles/scheduler.py
# DAILY_JOBS), so Run jobs now is the same pass the scheduler makes: take the
# backups that are due (into backend/sim_backups), roll events over, expire
# overdue applications and send reminders, archive accounts past July 31 and
# scheduled visits past their day, then apply data retention. Backups come
# first, as on the real system, so the data is captured before anything is
# archived or purged.
DEMO_JOBS = (
    'auto_backup',
    'scheduled_backup',
    'auto_manage_events',
    'expire_unpaid_registrations',
    'auto_archive_expired_accounts',
    'auto_archive_past_visits',
    'purge_old_records',
)


def available():
    return bool(getattr(settings, 'SIM_CLOCK_ENABLED', False)) and sim_clock.installed()


def _require():
    if not available():
        raise sim_clock.SimClockRefused('The simulated clock only runs under sim_settings.')


def _display(moment):
    local = timezone.localtime(moment)
    hour = local.hour % 12 or 12
    return f"{local:%a}, {local:%b} {local.day} {local.year}, {hour}:{local:%M} {local:%p}"


def status():
    """The clock as every screen and the CLI report it."""
    _require()
    st = sim_clock.state()
    return {
        'available':         True,
        'enabled':           st['enabled'],
        'offset_seconds':    st['offset_seconds'],
        'offset_text':       sim_clock.describe_offset(st['offset_seconds']),
        'now':               st['now'].isoformat(),
        'now_display':       _display(st['now']),
        'real_now':          st['real_now'].isoformat(),
        'real_now_display':  _display(st['real_now']),
        'email_to':          getattr(settings, 'SIM_EMAIL_TO', ''),
        'database':          settings.DATABASES['default']['NAME'],
    }


def enable(on=True):
    _require()
    sim_clock.write(enabled=on)
    _after_move()
    return status()


def set_to(moment):
    """Make `moment` (aware, or naive campus time) the simulated now. Turns it on."""
    _require()
    if timezone.is_naive(moment):
        moment = timezone.make_aware(moment, timezone.get_current_timezone())
    sim_clock.write(enabled=True,
                    offset_seconds=(moment - sim_clock.real_now()).total_seconds())
    _after_move()
    return status()


def _after_move():
    """Keep the backups and the scheduler in step with the date just set."""
    _drop_future_backups()
    # The scheduler sleeps up to an hour on real time; wake it so its pass runs
    # at the new date now. A no-op outside the server process (the CLI).
    from vehicles import scheduler
    scheduler.wake()


def _drop_future_backups():
    """Delete the demo's backups dated after the simulated now.

    Moving the clock back means those backups have not been taken yet. Left in
    place they would make the next one look not due, and rotation (newest N by
    date) would delete a fresh backup before them. Only the demo's own folders,
    which sim_settings always sets, are ever touched.
    """
    import os
    from accounts.backup_utils import _NAME_RE, taken_at
    now = timezone.now()
    removed = []
    for folder in (getattr(settings, 'BACKUP_DIR', None),
                   getattr(settings, 'SIM_SCHEDULED_BACKUP_DIR', None)):
        if not folder or not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            if _NAME_RE.match(name) and os.path.isfile(path) and taken_at(name, path) > now:
                os.remove(path)
                removed.append(name)
    return removed


def advance(*, hours=0, days=0, working_days=0):
    """Move the simulated now forward (or back, with negatives). Turns it on."""
    _require()
    from time_utils import add_business_days
    st = sim_clock.state()
    target = st['now'] if st['enabled'] else sim_clock.real_now()
    if working_days:
        target = add_business_days(target, working_days)
    target = target + timedelta(days=days, hours=hours)
    return set_to(target)


def reset():
    """Back to the real time (offset 0), clock left on."""
    _require()
    sim_clock.write(enabled=True, offset_seconds=0)
    _after_move()
    return status()


def run_jobs():
    """Run DEMO_JOBS now, at the simulated time, and say what each one did."""
    _require()
    from vehicles import tasks
    results = []
    for job in DEMO_JOBS:
        try:
            outcome = getattr(tasks, job)()
            results.append({'job': job, 'label': JOB_LABELS[job], 'ok': True, 'result': _summarise(outcome)})
        except Exception as exc:                          # noqa: BLE001 — one failing job must not hide the rest
            results.append({'job': job, 'label': JOB_LABELS[job], 'ok': False, 'result': f'failed: {exc}'})
    return {**status(), 'jobs': results}


JOB_LABELS = {
    'auto_backup':                   'Automatic backup',
    'scheduled_backup':              'Scheduled backup',
    'auto_manage_events':            'Events',
    'expire_unpaid_registrations':   'Unpaid registrations',
    'auto_archive_expired_accounts': 'Expired accounts',
    'auto_archive_past_visits':      'Scheduled visits',
    'purge_old_records':             'Data retention',
}


def _summarise(outcome):
    if isinstance(outcome, dict):
        parts = [f'{k.replace("_", " ")}: {v}' for k, v in outcome.items()
                 if isinstance(v, (int, float, str)) and not isinstance(v, bool)]
        return ', '.join(parts) or 'done'
    return 'done' if outcome is None else str(outcome)[:200]


def parse_when(text):
    """'2026-10-09 10:00' or '2026-10-09' (00:00) as naive campus time."""
    text = str(text or '').strip()
    for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M', '%Y-%m-%d'):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError("Use 'YYYY-MM-DD HH:MM' or 'YYYY-MM-DD'.")


def parse_step(text):
    """'3d', '5h', '2wd' (working days), '-1d' as advance() keyword arguments."""
    text = str(text or '').strip().lower()
    for suffix, key in (('wd', 'working_days'), ('d', 'days'), ('h', 'hours')):
        if text.endswith(suffix):
            try:
                return {key: int(text[:-len(suffix)])}
            except ValueError:
                break
    raise ValueError("Use a number with d (days), h (hours) or wd (working days), e.g. 3d, 5h, 2wd.")
