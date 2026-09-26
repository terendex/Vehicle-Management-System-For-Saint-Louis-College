# =============================================================================
# WHAT THIS FILE IS FOR
#
# Makes the database itself carry out the same deletes the app does, so a row
# deleted by hand (the Neon console's Delete button, the SQL Editor, psql)
# cascades exactly like a delete made through the app instead of failing with
#   "violates foreign key constraint ... on table tbl_audit_log".
#
# Why that failed before: Django creates every foreign key as plain NO ACTION
# and performs CASCADE / SET_NULL itself, in Python, before it sends the
# DELETE. The database never knew the rules, so anyone deleting outside Django
# hit a bare refusal.
#
# What this does: after every `migrate`, it compares each foreign key's
# ON DELETE rule in Postgres with the model's on_delete and rewrites the ones
# that differ. That is idempotent, and it also repairs a constraint that a
# later migration recreates (Django drops and re-adds an FK on AlterField,
# which would silently put it back to NO ACTION).
#
# Why the app is unaffected: Django still does its own cascade first. By the
# time its DELETE reaches Postgres, it has already deleted or nulled every
# referencing row, so the database rule finds nothing left to act on. The
# rules only ever fire for deletes that bypass Django.
#
# What a hand delete still skips: the app-level side effects of a delete
# (the audit-log entry, realtime broadcasts to open pages). Those live in
# Python and cannot be reproduced by a constraint.
# =============================================================================

import logging

from django.apps import apps
from django.db import DEFAULT_DB_ALIAS, connections, models, transaction

logger = logging.getLogger(__name__)


# Postgres's one-letter codes for pg_constraint.confdeltype.
_NO_ACTION, _CASCADE, _SET_NULL = 'a', 'c', 'n'
_SQL = {_NO_ACTION: 'NO ACTION', _CASCADE: 'CASCADE', _SET_NULL: 'SET NULL'}

# Relations where the database should cascade even though the model says
# SET_NULL. These are the records a user OWNS: the app never lets them outlive
# the account, because delete_users_with_owned_records() in accounts/models.py
# deletes them explicitly before the user. A hand delete in the console has no
# such helper, so the database does the sweep instead.
#
# Keep this in step with delete_users_with_owned_records(). Violations are
# deliberately NOT here: the helper finds them through vehicle__user, which no
# single foreign key reproduces (an archived account's vehicles are already
# unlinked), so a hand delete leaves them in place with the owner nulled,
# name/email snapshot intact. That is the conservative side to err on.
OWNED_BY_USER = {
    ('vehicles.Vehicle', 'user'): _CASCADE,
    ('vehicles.VehicleRegistration', 'user'): _CASCADE,
}


# The rule the database should enforce for one model field.
def _wanted_rule(model, field):
    override = OWNED_BY_USER.get((model._meta.label, field.name))
    if override:
        return override
    on_delete = field.remote_field.on_delete
    if on_delete is models.CASCADE:
        return _CASCADE
    if on_delete is models.SET_NULL:
        return _SET_NULL
    # PROTECT / RESTRICT / DO_NOTHING: refusing the delete is the right answer.
    # SET_DEFAULT / SET(...): Python computes the value, so the database can only
    # refuse. NO ACTION covers both.
    return _NO_ACTION


# Every single-column foreign key in the public schema, keyed by (table, column).
def _current_constraints(cursor):
    cursor.execute("""
        SELECT rel.relname, a.attname, c.conname, c.confdeltype, c.confupdtype,
               ref.relname, ra.attname, c.condeferrable, c.condeferred
        FROM pg_constraint c
        JOIN pg_class rel      ON rel.oid = c.conrelid
        JOIN pg_class ref      ON ref.oid = c.confrelid
        JOIN pg_namespace n    ON n.oid = rel.relnamespace
        JOIN pg_attribute a    ON a.attrelid = c.conrelid  AND a.attnum = c.conkey[1]
        JOIN pg_attribute ra   ON ra.attrelid = c.confrelid AND ra.attnum = c.confkey[1]
        WHERE c.contype = 'f'
          AND n.nspname = current_schema()
          AND array_length(c.conkey, 1) = 1
    """)
    return {
        (table, column): {
            'name': name, 'rule': rule, 'on_update': on_update,
            'ref_table': ref_table, 'ref_column': ref_column,
            'deferrable': deferrable, 'deferred': deferred,
        }
        for table, column, name, rule, on_update, ref_table, ref_column, deferrable, deferred
        in cursor.fetchall()
    }


def sync_fk_delete_rules(using=None, verbosity=1, **kwargs):
    """Rewrite every foreign key whose ON DELETE rule differs from the model.

    Returns the number of constraints changed. Safe to run any number of times;
    when everything already matches it issues one catalog query and nothing else.
    """
    connection = connections[using or DEFAULT_DB_ALIAS]
    if connection.vendor != 'postgresql':
        return 0

    quote = connection.ops.quote_name
    changed = 0
    with transaction.atomic(using=connection.alias), connection.cursor() as cursor:
        current = _current_constraints(cursor)
        for model in apps.get_models(include_auto_created=True):   # auto_created = M2M through tables
            if not model._meta.managed or model._meta.proxy:
                continue
            for field in model._meta.local_fields:
                if not (isinstance(field, models.ForeignKey) and field.db_constraint):
                    continue
                fk = current.get((model._meta.db_table, field.column))
                want = _wanted_rule(model, field)
                if fk is None or fk['rule'] == want:
                    continue
                if want == _SET_NULL and not field.null:
                    continue                                # SET NULL on a NOT NULL column would just fail
                if fk['on_update'] != _NO_ACTION:
                    continue                                # not a Django-made constraint; leave it alone

                deferral = ''
                if fk['deferrable']:
                    deferral = ' DEFERRABLE INITIALLY ' + ('DEFERRED' if fk['deferred'] else 'IMMEDIATE')
                # One statement, so there is never a moment without the constraint.
                cursor.execute(
                    f"ALTER TABLE {quote(model._meta.db_table)} "
                    f"DROP CONSTRAINT {quote(fk['name'])}, "
                    f"ADD CONSTRAINT {quote(fk['name'])} "
                    f"FOREIGN KEY ({quote(field.column)}) "
                    f"REFERENCES {quote(fk['ref_table'])} ({quote(fk['ref_column'])}) "
                    f"ON DELETE {_SQL[want]}{deferral}"
                )
                changed += 1
                if verbosity >= 2:
                    print(f"  FK {model._meta.db_table}.{field.column}: "
                          f"ON DELETE {_SQL[fk['rule']]} -> {_SQL[want]}")
    if changed:
        logger.info('db_delete_rules: updated %d foreign key(s)', changed)
        if verbosity >= 1:
            print(f"  Synced ON DELETE rules on {changed} foreign key(s).")
    return changed
