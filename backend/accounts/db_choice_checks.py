# =============================================================================
# WHAT THIS FILE IS FOR
#
# Makes the database refuse a value the app does not know, so a typo made by
# hand in the Neon console ("Accepted" instead of "accepted") fails on the spot
# with a readable error instead of saving a row the app silently mistreats.
#
# Why it is needed: `choices=` on a Django field is enforced only by forms and
# serializers. The column itself is plain varchar, so the Neon table editor
# (or any raw SQL) could store anything.
#
# What this does: after every `migrate`, it gives each choice column in the
# project's apps a CHECK constraint named "<table>_<column>_valid" listing the
# allowed values, and rewrites it whenever the model's choices change. The
# allowed values are stored in the constraint's comment, so it is also where
# to look when Neon reports a violation.
#
# Why the app is unaffected: every value the app writes is already one of the
# choices (verified on the live data before this was added, and by the test
# suite). The constraints are added NOT VALID, so they check new and edited
# rows only; existing rows are never re-checked.
#
# When adding a new choice value: `migrate` must run before any code writes
# it, or the insert is refused. Railway's start.sh already runs migrate first.
# =============================================================================

import hashlib
import json
import logging

from django.apps import apps
from django.db import DEFAULT_DB_ALIAS, connections, transaction

logger = logging.getLogger(__name__)

# Only the project's own tables. Django's and third-party tables are left alone.
APP_LABELS = ('accounts', 'vehicles', 'scanning', 'violations')

# Marks a CHECK constraint as one this module owns, in its comment.
_TAG = 'slc-choices:'

# Values the app writes that are missing from the model's choices. The live
# plate stream saves training samples as source='stream', status='auto'
# (scanning/consumers.py), which the choice lists never gained. Allowed here
# so the stream keeps working; the choice lists themselves are left as they are.
EXTRA_ALLOWED = {
    ('scanning.MLTrainingSample', 'source'): ['stream'],
    ('scanning.MLTrainingSample', 'status'): ['auto'],
}


# The full set of values the column may hold, or None when it has no choices.
def _allowed_values(model, field):
    if not field.choices:
        return None
    values = {key for key, _ in field.flatchoices}
    values.update(EXTRA_ALLOWED.get((model._meta.label, field.name), []))
    if field.blank and field.get_internal_type() in ('CharField', 'TextField'):
        values.add('')                                  # a blank=True text field stores '' for "none chosen"
    if field.has_default() and not callable(field.default):
        values.add(field.default)
    return sorted(values, key=repr)


def _constraint_name(table, column):
    name = f'{table}_{column}_valid'
    if len(name) > 63:                                  # Postgres truncates identifiers past 63 bytes
        digest = hashlib.md5(name.encode()).hexdigest()[:8]
        name = f'{name[:50]}_{digest}_valid'
    return name


# The comment stored on the constraint: a signature to detect changed choices,
# followed by the allowed values for whoever reads the constraint in Neon.
def _comment(values):
    signature = hashlib.md5(json.dumps([repr(v) for v in values]).encode()).hexdigest()[:12]
    listed = ', '.join(str(v) if v != '' else "'' (blank)" for v in values)
    return f'{_TAG}{signature} Allowed: {listed}'


# Every CHECK constraint this module created, keyed by name.
def _existing(cursor):
    cursor.execute("""
        SELECT c.conname, rel.relname, d.description
        FROM pg_constraint c
        JOIN pg_class rel     ON rel.oid = c.conrelid
        JOIN pg_namespace n   ON n.oid = rel.relnamespace
        JOIN pg_description d ON d.objoid = c.oid AND d.classoid = 'pg_constraint'::regclass
        WHERE c.contype = 'c'
          AND n.nspname = current_schema()
          AND d.description LIKE %s
    """, [_TAG + '%'])
    return {name: (table, description) for name, table, description in cursor.fetchall()}


def sync_choice_checks(using=None, verbosity=1, **kwargs):
    """Add, update or drop the choice CHECK constraints to match the models.

    Returns the number of constraints added, changed or dropped. When
    everything already matches it issues one catalog query and nothing else.
    """
    connection = connections[using or DEFAULT_DB_ALIAS]
    if connection.vendor != 'postgresql':
        return 0

    quote = connection.ops.quote_name
    changed = 0
    with transaction.atomic(using=connection.alias), connection.cursor() as cursor, \
            connection.schema_editor(atomic=False) as editor:
        existing = _existing(cursor)
        wanted = set()
        for model in apps.get_models():
            meta = model._meta
            if meta.app_label not in APP_LABELS or not meta.managed or meta.proxy:
                continue
            for field in meta.local_concrete_fields:
                values = _allowed_values(model, field)
                if values is None:
                    continue
                name = _constraint_name(meta.db_table, field.column)
                comment = _comment(values)
                wanted.add(name)
                if existing.get(name) == (meta.db_table, comment):
                    continue                            # already exactly right

                table = quote(meta.db_table)
                listed = ', '.join(editor.quote_value(v) for v in values)
                cursor.execute(f'ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {quote(name)}')
                cursor.execute(
                    f'ALTER TABLE {table} ADD CONSTRAINT {quote(name)} '
                    f'CHECK ({quote(field.column)} IN ({listed})) NOT VALID'
                )
                cursor.execute(f'COMMENT ON CONSTRAINT {quote(name)} ON {table} IS %s', [comment])
                changed += 1
                if verbosity >= 2:
                    print(f'  CHECK {meta.db_table}.{field.column}: {len(values)} allowed value(s)')

        # A field that lost its choices (or was removed) leaves a stale constraint behind.
        for name, (table, _) in existing.items():
            if name not in wanted:
                cursor.execute(f'ALTER TABLE {quote(table)} DROP CONSTRAINT IF EXISTS {quote(name)}')
                changed += 1
    if changed:
        logger.info('db_choice_checks: updated %d check constraint(s)', changed)
        if verbosity >= 1:
            print(f'  Synced {changed} choice CHECK constraint(s).')
    return changed
