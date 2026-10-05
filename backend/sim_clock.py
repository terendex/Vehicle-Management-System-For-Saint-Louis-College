"""A simulated clock for the local instructor demo. Never on in production.

The time-based rules (3 working days to pay, registration closing, passes
ending on July 31) can only be shown working by letting days pass. This moves
the date the server lives in, so the real code runs against it: the hourly
sweep really expires the application, the real email really goes out, the row
really changes status. Nothing on a screen is faked.

How it works: sim_settings.py calls install() while settings load, before any
app or model is imported, and install() replaces django.utils.timezone.now
with simulated_now(). Everything that asks Django for the time follows it:
timezone.localdate()/localtime(), auto_now fields, default=timezone.now, the
scheduler's day keys, the deadline sweeps. JWT expiry is computed by
SimpleJWT from the OS clock, so moving the date never logs anybody out.

The state (on/off and the offset from the real time) is a small JSON file next
to this one (sim_clock.json, git-ignored), not a database table: the file can
be read before Django has started, and a table would be a migration that also
lands on the shared live database. Every process reading it (the web server,
its scheduler thread, `manage.py sim_clock`) sees a change within a second.

Where it can run: install() refuses unless the database is on this machine
and the process is not on Railway. The campus server and Railway share the
live Neon database, and a simulated date there would expire real students'
applications and email them.
"""
import json
import logging
import os
import threading
import time
from datetime import timedelta

log = logging.getLogger('sim_clock')

RECHECK_SECONDS = 1.0           # how stale a process's view of the file may get

_path = None                    # the state file, set by install()
_real_now = None                # Django's own timezone.now, kept before it is replaced
_lock = threading.Lock()
_state = {'checked': -1e9, 'mtime': None, 'enabled': False, 'offset': 0.0}


class SimClockRefused(RuntimeError):
    """The simulated clock was asked to run against a database that is not local."""


def installed():
    return _real_now is not None


def _read():
    """Refresh the cached state from the file, at most once a RECHECK_SECONDS."""
    if _path is None:
        return
    tick = time.monotonic()
    if tick - _state['checked'] < RECHECK_SECONDS:
        return
    with _lock:
        _state['checked'] = tick
        try:
            mtime = os.stat(_path).st_mtime
        except OSError:
            _state.update(mtime=None, enabled=False, offset=0.0)
            return
        if mtime == _state['mtime']:
            return
        try:
            with open(_path, encoding='utf-8') as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return                                       # caught mid-write: keep the last good state
        _state.update(mtime=mtime, enabled=bool(data.get('enabled')),
                      offset=float(data.get('offset_seconds') or 0))


def real_now():
    """The actual time, whatever the simulated clock says."""
    if _real_now is not None:
        return _real_now()
    from django.utils import timezone
    return timezone.now()


def offset():
    """How far the simulated clock is ahead of (or behind) the real one."""
    _read()
    return timedelta(seconds=_state['offset']) if _state['enabled'] else timedelta(0)


def simulated_now():
    """What django.utils.timezone.now returns once installed."""
    return _real_now() + offset()


def state():
    _read()
    return {
        'enabled':        _state['enabled'],
        'offset_seconds': _state['offset'],
        'real_now':       real_now(),
        'now':            real_now() + offset() if installed() else real_now(),
    }


def write(*, enabled=None, offset_seconds=None):
    """Change the clock. Written atomically, so a reader never sees half a file."""
    if _path is None:
        raise SimClockRefused('The simulated clock is not installed in this process.')
    _read()
    data = {
        'enabled':        _state['enabled'] if enabled is None else bool(enabled),
        'offset_seconds': _state['offset'] if offset_seconds is None else float(offset_seconds),
    }
    tmp = f'{_path}.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(data, fh)
    os.replace(tmp, _path)
    with _lock:                                           # this process sees it at once
        _state.update(checked=time.monotonic(), mtime=os.stat(_path).st_mtime,
                      enabled=data['enabled'], offset=data['offset_seconds'])
    log.warning('SIMULATED CLOCK %s (offset %s)', 'ON' if data['enabled'] else 'OFF',
                describe_offset(data['offset_seconds']))
    return state()


def describe_offset(seconds):
    """'+4 days 2 h', '-1 h', '0'."""
    seconds = int(round(seconds or 0))
    if not seconds:
        return '0'
    sign = '+' if seconds > 0 else '-'
    days, rest = divmod(abs(seconds), 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    parts = []
    if days:
        parts.append(f'{days} day{"s" if days != 1 else ""}')
    if hours:
        parts.append(f'{hours} h')
    if minutes and not days:
        parts.append(f'{minutes} min')
    return sign + ' '.join(parts or ['0 min'])


def install(path, databases):
    """Replace django.utils.timezone.now with the simulated clock.

    Called from sim_settings.py, before apps load. Refuses (raises) unless the
    default database is on this machine and this is not a Railway process.
    """
    global _real_now, _path
    host = str(databases.get('default', {}).get('HOST') or '').strip().lower()
    if (host not in ('127.0.0.1', 'localhost', '::1')
            or 'neon.tech' in host
            or os.getenv('RAILWAY_ENVIRONMENT') or os.getenv('RAILWAY_PUBLIC_DOMAIN')):
        raise SimClockRefused(
            f'Refusing to start the simulated clock against database host {host!r}. '
            'It only runs on a local demo database (sim_settings.py).')
    if installed():
        return
    from django.utils import timezone
    _real_now = timezone.now
    _path = str(path)
    timezone.now = simulated_now
    _read()
    log.warning('SIMULATED CLOCK INSTALLED on database %s@%s (currently %s, offset %s)',
                databases['default'].get('NAME'), host,
                'ON' if _state['enabled'] else 'OFF', describe_offset(_state['offset']))


# ── Email ───────────────────────────────────────────────────────────────
# Real mail, so instructors see what an applicant receives, but only ever to
# the demo's own inbox: the demo database is full of invented addresses on a
# real-looking domain, and nothing it sends may reach anyone else.

from django.core.mail.backends.base import BaseEmailBackend   # noqa: E402


class RedirectEmailBackend(BaseEmailBackend):
    """Delivers through SIM_REAL_EMAIL_BACKEND, every message to SIM_EMAIL_TO."""

    def __init__(self, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently)
        from django.conf import settings
        from django.utils.module_loading import import_string
        self.to = (getattr(settings, 'SIM_EMAIL_TO', '') or '').strip()
        backend = (settings.SIM_REAL_EMAIL_BACKEND if self.to
                   else 'django.core.mail.backends.console.EmailBackend')
        self.inner = import_string(backend)(fail_silently=fail_silently, **kwargs)

    def open(self):
        return self.inner.open()

    def close(self):
        return self.inner.close()

    def send_messages(self, email_messages):
        for message in email_messages or []:
            original = ', '.join(list(message.to) + list(message.cc) + list(message.bcc))
            message.extra_headers['X-SLC-Demo-Original-To'] = original
            if self.to:
                message.to, message.cc, message.bcc = [self.to], [], []
        return self.inner.send_messages(email_messages)
