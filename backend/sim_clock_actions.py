"""What the Test Clock page and `manage.py sim_clock` can do, in one place.

Only reachable when sim_settings installed the simulated clock (the URL is not
even registered otherwise, and every function here refuses).
"""
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

import sim_clock

# The time-based jobs a demo needs to see happen, in the scheduler's order
# (vehicles/scheduler.py DAILY_JOBS): expire overdue applications and send
# reminders, archive accounts past July 31, roll events and scheduled visits
# over. Backups and the retention purge are left to the scheduler.
DEMO_JOBS = (
    'auto_manage_events',
    'expire_unpaid_registrations',
    'auto_archive_expired_accounts',
    'auto_archive_past_visits',
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
    return status()


def set_to(moment):
    """Make `moment` (aware, or naive campus time) the simulated now. Turns it on."""
    _require()
    if timezone.is_naive(moment):
        moment = timezone.make_aware(moment, timezone.get_current_timezone())
    sim_clock.write(enabled=True,
                    offset_seconds=(moment - sim_clock.real_now()).total_seconds())
    return status()


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
    'auto_manage_events':            'Events',
    'expire_unpaid_registrations':   'Unpaid registrations',
    'auto_archive_expired_accounts': 'Expired accounts',
    'auto_archive_past_visits':      'Scheduled visits',
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
