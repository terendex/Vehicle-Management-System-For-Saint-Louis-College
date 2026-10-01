"""A person's name as three parts: last name, first name, middle initial.

Accounts (accounts.User) and vehicle registrations (vehicles.VehicleRegistration)
used to keep one free-text `full_name` column. They now store the three parts,
and `full_name` survives only as a computed display value ("DELA CRUZ, JUAN S."),
so every screen, email and gate message that prints a name is unchanged. What
cannot use it is the database: filter, sort and search on the parts instead —
order_by('last_name', 'first_name') and name_search_q() below.
"""
from __future__ import annotations

import re

from django.db.models import Q

NAME_FIELDS = ('last_name', 'first_name', 'middle_initial')

_INITIAL = re.compile(r'^[A-Za-zÀ-ÿÑñ]\.?$')
# Surname particles: in "JUAN DELA CRUZ" the last name is DELA CRUZ, not CRUZ.
_PARTICLES = {'DE', 'DEL', 'DELA', 'DELOS', 'DELAS', 'LA', 'LAS', 'LOS', 'SAN', 'STA', 'STA.',
              'STO', 'STO.', 'SANTA', 'SANTO', 'VAN', 'VON', 'DI', 'DA', 'DOS', 'MC', 'MAC'}


def clean_initial(value) -> str:
    """'santos' / 'S.' / ' s ' -> 'S'; '' when there is no letter to take."""
    for ch in str(value or '').strip():
        if ch.isalpha():
            return ch.upper()
    return ''


def compose_full_name(last_name, first_name, middle_initial='') -> str:
    """The display form every screen shows: 'LAST, FIRST M.'."""
    last = ' '.join(str(last_name or '').split())
    first = ' '.join(str(first_name or '').split())
    name = ', '.join(p for p in (last, first) if p)
    initial = clean_initial(middle_initial)
    return f'{name} {initial}.' if name and initial else name


def split_full_name(text):
    """Best-effort (last, first, middle_initial) from a single-string name.

    Two shapes are on file. Applicants wrote "LAST, FIRST, MIDDLE" (the form
    joined its three boxes with commas); staff accounts were typed the
    Filipino way, "FIRST [MIDDLE] LAST", with no comma at all. The composed
    display form "LAST, FIRST M." also parses back to its parts.
    """
    text = ' '.join(str(text or '').split())
    if not text:
        return '', '', ''

    if ',' in text:
        parts = [p.strip() for p in text.split(',')]
        last = parts[0]
        if len(parts) >= 3:
            return last, parts[1], clean_initial(' '.join(parts[2:]))
        rest = parts[1].split()
        # "LAST, FIRST M." — a trailing lone initial is the middle one; a
        # two-word first name ("MARIE ANN") is left whole.
        if len(rest) >= 2 and _INITIAL.match(rest[-1]):
            return last, ' '.join(rest[:-1]), clean_initial(rest[-1])
        return last, ' '.join(rest), ''

    words = text.split()
    # FIRST [MIDDLE] LAST, where LAST takes any particles in front of it.
    cut = len(words) - 1
    while cut > 1 and words[cut - 1].upper() in _PARTICLES:
        cut -= 1
    if len(words) == 1:
        return words[0], '', ''
    last, rest = ' '.join(words[cut:]), words[:cut]
    if len(rest) == 1:
        return last, rest[0], ''
    # FIRST [MIDDLE] LAST. An explicit initial ("ALADIN C. VILLAREAL") marks
    # the split exactly; otherwise the second-to-last word is the middle name.
    for i, word in enumerate(rest[1:], start=1):
        if _INITIAL.match(word):
            return ' '.join(words[i + 1:]), ' '.join(words[:i]), clean_initial(word)
    return last, ' '.join(rest[:-1]), clean_initial(rest[-1])


def name_kwargs(text) -> dict:
    """split_full_name() as keyword arguments: last_name=, first_name=,
    middle_initial= — for code (and tests) that start from one string."""
    return dict(zip(NAME_FIELDS, split_full_name(text)))


def name_parts_of(obj) -> dict:
    """The three parts of `obj` (a User or a registration) as keyword arguments,
    for copying a name from one record onto another."""
    return {field: getattr(obj, field) or '' for field in NAME_FIELDS}


def name_search_q(term, prefix='') -> Q:
    """A filter matching a typed name, in either order, against the parts.

    Every word typed must appear in the last or first name (or be the middle
    initial), so "juan dela cruz", "Dela Cruz, Juan" and "cruz" all find
    DELA CRUZ, JUAN S. `prefix` points through a relation, e.g. 'user__'.
    """
    words = [w for w in re.split(r'[\s,.]+', str(term or '')) if w]
    q = Q()
    for word in words:
        part = (Q(**{f'{prefix}last_name__icontains': word})
                | Q(**{f'{prefix}first_name__icontains': word}))
        if len(word) == 1:
            part |= Q(**{f'{prefix}middle_initial__iexact': word})
        q &= part
    return q


LEGACY_NAME_MODELS = ('accounts.user', 'vehicles.vehicleregistration')


def upgrade_legacy_backup(payload: str) -> str:
    """Rewrite a backup taken before the name split so it loads today.

    Such a file carries `full_name` on every account and registration, which
    the deserializer refuses as an unknown field — every older backup would
    otherwise be unrestorable. The name is split into the three parts (unless
    the record already has them) and full_name dropped. A current backup comes
    back unchanged.
    """
    import json
    if '"full_name"' not in payload:
        return payload                     # cheap exit: nothing to upgrade
    records = json.loads(payload)
    changed = False
    for record in records if isinstance(records, list) else []:
        fields = record.get('fields') or {}
        if record.get('model') not in LEGACY_NAME_MODELS or 'full_name' not in fields:
            continue
        legacy = fields.pop('full_name')
        if not (fields.get('last_name') or fields.get('first_name')):
            fields['last_name'], fields['first_name'], fields['middle_initial'] = split_full_name(legacy)
        fields.setdefault('middle_initial', '')
        changed = True
    return json.dumps(records) if changed else payload


def name_parts_from(data, *, fallback_full_name=True):
    """(last, first, middle_initial) from a request payload.

    Takes the three fields; `middle_name` is accepted for the initial (the
    registration form's box was a full middle name). A payload carrying only
    the old single `full_name` — a browser still running the previous bundle —
    is split rather than refused.
    """
    get = (data.get if hasattr(data, 'get') else (lambda k, d=None: d))
    last = ' '.join(str(get('last_name') or '').split())
    first = ' '.join(str(get('first_name') or '').split())
    initial = clean_initial(get('middle_initial') or get('middle_name') or '')
    if not (last or first) and fallback_full_name and get('full_name'):
        return split_full_name(get('full_name'))
    return last, first, initial
