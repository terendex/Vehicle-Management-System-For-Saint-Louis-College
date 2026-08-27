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
from typing import NamedTuple

from django.conf import settings
from django.core import serializers
from django.core.management import call_command
from django.core.management.color import no_style
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.models.constants import OnConflict
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


# ── Restoring ────────────────────────────────────────────────────────────────
#
# `loaddata` writes a fixture one row at a time: for each record it issues an
# UPDATE, and an INSERT when that matched nothing. Against a local database
# nobody notices. Against Neon — the production database sits in Singapore and
# a single query costs ~40 ms of round trip — a 3,400-record backup is 3,400
# sequential round trips, and a restore takes several minutes with the browser
# staring at a spinner the whole time.
#
# The work itself is tiny; the latency is the whole cost. So the load below
# does the same thing in bulk: one INSERT ... ON CONFLICT (pk) DO UPDATE per
# model per batch instead of one statement per row. The same fixture produces
# the same rows — this is still a merge by primary key, nothing is deleted —
# but the several minutes collapse to a couple of seconds.

_MAX_BIND_PARAMS = 30000


def _upsert_fields(model):
    """(columns to write, columns to overwrite on conflict) for a bulk upsert.

    Field objects rather than names because they go straight to the insert
    compiler, which is what `bulk_create` resolves them to anyway.
    """
    fields = [f for f in model._meta.concrete_fields if not getattr(f, 'generated', False)]
    updatable = [f for f in fields if not f.primary_key]
    return fields, updatable


def _upsert(model, objs, using):
    """Write `objs` with one INSERT ... ON CONFLICT (pk) DO UPDATE per batch.

    Goes to the manager's `_insert` rather than `bulk_create` for one reason:
    `raw`. A raw insert is the mode `loaddata` uses, and the insert compiler
    checks it to decide whether to call `pre_save()` on each field — which is
    what stamps `auto_now` and `auto_now_add` columns with the current time.

    A restore must not do that. The timestamps in the file are the data: the
    moment a vehicle was logged through the gate, when an account was created,
    when a violation was issued. Letting `pre_save` run would rewrite every one
    of them to the moment of the restore, quietly flattening the whole history
    into a single instant. `bulk_create` gives no way to turn that off, so this
    calls the same private entry point it does and passes `raw=True`.
    """
    fields, updatable = _upsert_fields(model)
    manager = model._base_manager.using(using)

    if updatable:
        on_conflict = OnConflict.UPDATE
        update_fields, unique_fields = updatable, [model._meta.pk]
    else:
        # A table that is nothing but its primary key has no column to write on
        # a conflict; the row already existing is the whole result, so skipping
        # it is correct rather than an error.
        on_conflict = OnConflict.IGNORE
        update_fields = unique_fields = None

    # PostgreSQL binds at most 65535 parameters per statement and Django sends
    # one per column per row, so the batch is sized from the column count. That
    # keeps the widest table (access logs) under the ceiling instead of failing
    # on it.
    batch = max(1, _MAX_BIND_PARAMS // max(1, len(fields)))
    for start in range(0, len(objs), batch):
        manager._insert(
            objs[start:start + batch], fields=fields, raw=True,
            on_conflict=on_conflict,
            update_fields=update_fields, unique_fields=unique_fields,
        )


class LoadResult(NamedTuple):
    records: int
    # `model_name` for each model the load wrote to, which is the same name
    # `realtime.signals` broadcasts under. The caller needs them because bulk
    # writes fire no signals of their own — see below.
    resources: list[str]


def load_backup(payload: str, using: str = DEFAULT_DB_ALIAS) -> LoadResult:
    """Merge a JSON fixture into the database. Returns what it wrote.

    Behaviourally equal to `loaddata` on this data: rows in the file overwrite
    live rows with the same primary key, rows that do not exist yet are
    inserted, and nothing is ever deleted. It must be called inside a
    transaction — on any error the caller's rollback is what undoes a partial
    load.

    Two differences from `loaddata` are deliberate and both are improvements:

    * Bulk writes do not fire `pre_save`/`post_save`. Under `loaddata` every
      restored row fired them, which meant a restore queued one websocket
      broadcast per record and minted a fresh admin notification for every
      registration in the file — an inbox full of "new registration" alerts for
      registrations from months ago. The receivers never checked the `raw` flag
      that marks a fixture load, so there was no way for them to tell.
      `resources` is returned so the caller can send one broadcast per model
      instead of one per row: open pages subscribe by resource name and ignore
      anything else, so a single catch-all message would reach none of them.

    * Rows are written per model rather than in file order. Foreign keys are
      created DEFERRABLE INITIALLY DEFERRED on PostgreSQL, so references are
      only checked when the transaction commits and order cannot matter; this
      is the same property `loaddata` relies on to load a fixture whose parents
      appear after their children.
    """
    connection = connections[using]

    # Group by model, keeping the last row for any primary key that appears
    # twice. dumpdata never repeats one, but a fixture assembled by hand can,
    # and a repeat inside a single ON CONFLICT statement is a hard error in
    # PostgreSQL ("cannot affect row a second time"). Last-one-wins matches
    # what loaddata would have done, applying them in order.
    by_model: dict[type, dict] = {}
    m2m_pending = []
    total = 0
    for obj in serializers.deserialize('json', payload, using=using):
        model = obj.object.__class__
        by_model.setdefault(model, {})[obj.object.pk] = obj.object
        if obj.m2m_data:
            m2m_pending.append(obj)
        total += 1

    with connection.constraint_checks_disabled():
        for model, rows in by_model.items():
            _upsert(model, list(rows.values()), using)

        # Many-to-many rows live in their own tables and are untouched by the
        # upserts above. Nothing in a backup currently carries any — the only
        # m2m on a backed-up model is User.groups/user_permissions, and auth
        # groups and permissions are not in BACKUP_APPS — so this loop almost
        # always does nothing. It stays because a fixture that does carry m2m
        # data should not silently lose it.
        for obj in m2m_pending:
            for name, values in obj.m2m_data.items():
                getattr(obj.object, name).set(values)

    # Constraint checks were deferred, so ask for them now: a foreign key in
    # the file pointing at a row that does not exist should fail here, inside
    # the caller's transaction, rather than at commit where the traceback no
    # longer says which fixture caused it.
    if by_model:
        connection.check_constraints(
            table_names=[m._meta.db_table for m in by_model]
        )

    # Rows carry their own primary keys, which leaves each table's sequence
    # still pointing at whatever it reached before the restore. Without this
    # the next locally-created record collides with a restored one. loaddata
    # does the same thing at the end of a load, for the same reason; sending
    # every statement in one round trip keeps it off the critical path.
    if total:
        sql = connection.ops.sequence_reset_sql(no_style(), list(by_model))
        if sql:
            with connection.cursor() as cursor:
                cursor.execute('\n'.join(sql))

    return LoadResult(total, [m._meta.model_name for m in by_model])
