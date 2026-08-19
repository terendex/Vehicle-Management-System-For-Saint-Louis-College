"""Shared plumbing for system backups.

Everything that produces or manages a backup file goes through here: the manual
download in `accounts.views`, the pre-restore safety snapshot, and the scheduled
`vehicles.tasks.auto_backup` job. Keeping the app list, the exclusions and the
on-disk layout in one module means an automatic backup and a hand-clicked one
are byte-for-byte the same kind of file, and either can be fed back into the
restore endpoint.
"""
from __future__ import annotations

import io
import os
import re
from datetime import datetime

from django.conf import settings
from django.core.management import call_command
from django.utils import timezone as tz

# App labels whose data is captured in a backup. Excludes contenttypes,
# permissions, sessions, admin log entries and token blacklists (volatile /
# rebuildable) — the schema itself is versioned by migrations.
BACKUP_APPS = ['accounts', 'vehicles', 'scanning', 'violations', 'realtime']

# High-volume ML artefacts (plate-recognition crops and training samples) are
# rebuildable and would bloat every backup by ~20k rows / many MB, making
# restore impractically slow. Business data — including the access log — is kept.
#
# Two-factor rows are excluded for a different reason: they are not data, they
# are the pairing between an account and a physical phone. Restoring them would
# overwrite whatever secret a person's authenticator holds today with the one
# from the file, so their app would silently stop producing valid codes and an
# older, possibly discarded phone would start working again. On the only CDSO
# account that is a lock-out with no way back.
#
# Leaving them out means a restore never disturbs anyone's authenticator: live
# pairings survive untouched, and a restore onto an empty database simply asks
# each person to enroll at their next login, which is the correct outcome.
BACKUP_EXCLUDE = [
    'scanning.platerecognitionrecord',
    'scanning.mltrainingsample',
    'accounts.twofactordevice',
    'accounts.twofactorbackupcode',
]

# Filename prefixes, by how the file came to exist. The prefix is what the
# listing labels a file with and what the pruner matches on, so an automatic
# backup can be rotated away while a pre-restore snapshot — the only copy of
# what the system looked like before someone overwrote it — is never touched.
AUTO_PREFIX    = 'auto-backup-'
MANUAL_PREFIX  = 'manual-backup-'
SAFETY_PREFIX  = 'pre-restore-'

# One flat directory. Names carry the kind and the timestamp, so nothing needs
# a database row to be understood — a file copied off the server still explains
# itself, which is the point of a backup you may open a year from now.
_NAME_RE  = re.compile(r'^[A-Za-z0-9._-]+\.json$')
_STAMP_RE = re.compile(r'(\d{8})-(\d{4,6})')


def backup_dir() -> str:
    """Absolute path to the backups directory, created if missing."""
    path = os.path.join(settings.BASE_DIR, 'backups')
    os.makedirs(path, exist_ok=True)
    return path


def dump_backup() -> str:
    """Return the full data fixture as a JSON string."""
    buf = io.StringIO()
    call_command('dumpdata', *BACKUP_APPS, exclude=BACKUP_EXCLUDE, indent=2, stdout=buf)
    return buf.getvalue()


def stamp(seconds: bool = True) -> str:
    fmt = '%Y%m%d-%H%M%S' if seconds else '%Y%m%d-%H%M'
    return tz.localtime().strftime(fmt)


def write_backup(prefix: str, payload: str | None = None) -> tuple[str, int]:
    """Write a backup file named `<prefix><stamp>.json`. Returns (name, bytes)."""
    payload = dump_backup() if payload is None else payload
    name = f'{prefix}{stamp()}.json'
    path = os.path.join(backup_dir(), name)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(payload)
    return name, os.path.getsize(path)


def kind_of(name: str) -> str:
    if name.startswith(AUTO_PREFIX):
        return 'auto'
    if name.startswith(SAFETY_PREFIX):
        return 'safety'
    if name.startswith(MANUAL_PREFIX):
        return 'manual'
    return 'other'


def taken_at(name: str, path: str):
    """When the backup was taken, read from its filename.

    The filename is preferred over the file's mtime because copying, restoring
    or syncing a file rewrites mtime while the name keeps saying when the data
    inside it is from. mtime is only the fallback for hand-named files.
    """
    match = _STAMP_RE.search(name)
    if match:
        day, clock = match.groups()
        clock = clock.ljust(6, '0')
        try:
            naive = datetime.strptime(f'{day}{clock}', '%Y%m%d%H%M%S')
            return tz.make_aware(naive, tz.get_current_timezone())
        except ValueError:
            pass
    return tz.make_aware(datetime.fromtimestamp(os.path.getmtime(path)),
                         tz.get_current_timezone())


def safe_path(name: str) -> str | None:
    """Resolve a caller-supplied backup filename to a path inside the backups
    directory, or None if it is not a plain .json name living there.

    The name arrives from an API request, so `../../settings.py` has to be
    impossible: the regex rejects separators outright and the realpath check
    catches anything that still resolves outside the directory (a symlink, say).
    """
    if not name or not _NAME_RE.match(name):
        return None
    root = os.path.realpath(backup_dir())
    path = os.path.realpath(os.path.join(root, name))
    if os.path.dirname(path) != root or not os.path.isfile(path):
        return None
    return path


def list_backups() -> list[dict]:
    """Every backup file on disk, newest first."""
    root = backup_dir()
    items = []
    for name in os.listdir(root):
        if not name.endswith('.json'):
            continue
        path = os.path.join(root, name)
        if not os.path.isfile(path):
            continue
        items.append({
            'name': name,
            'kind': kind_of(name),
            'size': os.path.getsize(path),
            'created_at': taken_at(name, path).isoformat(),
        })
    items.sort(key=lambda item: item['created_at'], reverse=True)
    return items


# Kinds that rotate, each keeping its own newest `keep` files. Pre-restore
# snapshots are deliberately not in this list: they are the only record of what
# the system looked like before somebody overwrote it, and rotating them away on
# a schedule would delete the one file a person goes looking for after a bad
# restore. Automatic and manual copies are both routine and reproducible, so
# they rotate — otherwise a daily schedule fills the disk over a semester.
ROTATING_KINDS = ('auto', 'manual')


def prune_backups(keep: int) -> list[str]:
    """Delete all but the newest `keep` backups of each rotating kind.

    Returns the names removed.
    """
    keep = max(int(keep), 1)
    items = list_backups()
    removed = []
    for kind in ROTATING_KINDS:
        for item in [i for i in items if i['kind'] == kind][keep:]:
            try:
                os.remove(os.path.join(backup_dir(), item['name']))
                removed.append(item['name'])
            except OSError:
                pass
    return removed


def latest_auto_backup() -> dict | None:
    for item in list_backups():
        if item['kind'] == 'auto':
            return item
    return None
