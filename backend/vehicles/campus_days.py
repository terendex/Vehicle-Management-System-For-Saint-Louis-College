"""Campus-day rules — one definition, shared by every path that writes them.

A student's campus days and the schedule group derived from them were being
computed in three separate places (the public registration form's endpoint, the
CDSO override inside AcceptRegistrationView, and the admin owner-create
serializer) and two of them disagreed: one asked whether the chosen days were
*exactly* {Monday, Wednesday, Friday}, the other whether they merely fell inside
it. A student who picked Monday and Wednesday was therefore filed as MIXED when
they registered online and MWF when a CDSO officer entered the same two days by
hand. Only one of those can be right, so the rule lives here now.

The subset reading is the one kept: somebody who comes in on Monday and
Wednesday is on the MWF rotation, they simply do not use all three days.
"""

# Saturday is a class day here; Sunday is not a campus day at all.
ALL_DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
VALID_DAYS = frozenset(ALL_DAYS)

MWF_DAYS = frozenset({'Monday', 'Wednesday', 'Friday'})
TTHS_DAYS = frozenset({'Tuesday', 'Thursday', 'Saturday'})

# The ordinary allowance. More than this is a special case that needs a reason
# from CDSO — except for SpEd students, who attend every day.
MAX_CAMPUS_DAYS = 3
UNLIMITED_DAYS_LEVELS = ('sped',)


def clean_campus_days(days):
    """Normalise a submitted day list: keep only real campus days, drop
    duplicates, and return them in calendar order.

    Returns (cleaned, rejected) so the caller can tell "you sent nonsense" from
    "you sent nothing". The public endpoint used to hand `campus_days` to the
    serializer untouched, so a JSONField happily stored ['Funday', 'DROP TABLE']
    — values that then matched no weekday anywhere in entry_logic, quietly
    leaving the owner with a pass that admitted them on no day at all.
    """
    if not isinstance(days, (list, tuple)):
        return [], []
    seen, cleaned, rejected = set(), [], []
    for raw in days:
        day = raw.strip().title() if isinstance(raw, str) else None
        if day in VALID_DAYS:
            if day not in seen:
                seen.add(day)
                cleaned.append(day)
        else:
            rejected.append(raw)
    cleaned.sort(key=ALL_DAYS.index)
    return cleaned, rejected


def allows_unlimited_days(student_level):
    """SpEd students attend daily, so the 3-day cap does not apply to them."""
    return (student_level or '').strip().lower() in UNLIMITED_DAYS_LEVELS


def schedule_group(days):
    """The schedule code for a set of campus days.

    MWF/TTHS when every chosen day sits on one side of the rotation, MIXED when
    they straddle both, ANY when no days were chosen (employees and fetchers).
    """
    chosen = set(days or [])
    if not chosen:
        return 'ANY'
    in_mwf = bool(chosen & MWF_DAYS)
    in_tths = bool(chosen & TTHS_DAYS)
    if in_mwf and not in_tths:
        return 'MWF'
    if in_tths and not in_mwf:
        return 'TTHS'
    return 'MIXED'
