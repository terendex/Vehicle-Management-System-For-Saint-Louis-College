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

# Saturday is a campus day, but it belongs to no student rotation — only CDSO
# assigns it. Sunday is not a campus day at all.
ALL_DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
VALID_DAYS = frozenset(ALL_DAYS)

MWF_DAYS = frozenset({'Monday', 'Wednesday', 'Friday'})
TTHF_DAYS = frozenset({'Tuesday', 'Thursday', 'Friday'})

# The two rotations an applicant may choose from, in calendar order. The public
# form offers these as whole schedules, not as six days to cherry-pick from: a
# student who ticked only Monday still got billed as "MWF" and still showed up
# in the admin's list as a Monday-Wednesday-Friday student, which is the kind of
# mismatch this mapping exists to stop.
#
# Friday is on BOTH rotations, so the two are not a partition of the week:
# anything deciding "which rotation is this" has to work off the exclusive days
# (Mon/Wed vs Tue/Thu), and Friday's slot capacity is shared by both intakes.
SCHEDULE_GROUP_DAYS = {
    'MWF':  ['Monday', 'Wednesday', 'Friday'],
    'TTHF': ['Tuesday', 'Thursday', 'Friday'],
}

# What each schedule code actually admits, spelled out. "Any day" reads as no
# restriction at all when the campus is in fact closed on Sunday.
SCHEDULE_DAY_LABELS = {
    'MWF':   'Mon · Wed · Fri',
    'TTHF':  'Tue · Thu · Fri',
    'ANY':   'Monday – Saturday',
    'MIXED': 'Monday – Saturday',
}

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

    MWF/TTHF when every chosen day fits inside one rotation, MIXED when they
    straddle both (or include Saturday, which is on neither), ANY when no days
    were chosen (employees and fetchers).

    Friday sits in both rotations, so a Friday-only set is genuinely ambiguous;
    it is read as MWF. Nothing user-facing relies on that — the form always
    names the rotation outright — but a CDSO officer who assigns Friday alone
    gets a stable answer instead of MIXED.
    """
    chosen = set(days or [])
    if not chosen:
        return 'ANY'
    if chosen <= MWF_DAYS:
        return 'MWF'
    if chosen <= TTHF_DAYS:
        return 'TTHF'
    return 'MIXED'


def resolve_student_schedule(schedule, days, student_level=None):
    """The public form's rule: a student registers for a *rotation*, not for a
    hand-picked set of days.

    Returns (campus_days, schedule_code, error) — error is None on success and a
    ready-to-show message otherwise.

    An applicant used to tick any one, two or three of the six days, so the
    system carried students whose stored days did not match the schedule their
    pass was issued under (one Monday, filed as MWF). Both entry points now
    resolve to a whole rotation: MWF or TTHF, and every day in it. `schedule`
    wins when it names a rotation; `days` is still honoured — snapped up to the
    rotation it sits on — so older clients and direct API callers land on the
    same footing instead of slipping through with a partial week.

    SpEd students are the one exception: they attend every campus day.
    """
    if allows_unlimited_days(student_level):
        return list(ALL_DAYS), schedule_group(ALL_DAYS), None

    # str() first: this is an AllowAny endpoint, so `schedule` can arrive as a
    # number or a list, and neither has .strip().
    code = str(schedule or '').strip().upper()
    if code not in SCHEDULE_GROUP_DAYS:
        cleaned, rejected = clean_campus_days(days)
        if rejected:
            return [], '', (f"Not a campus day: {', '.join(str(d) for d in rejected)}. "
                            f"Choose from {', '.join(ALL_DAYS)}.")
        if not cleaned:
            return [], '', ('Students must choose a schedule: '
                            f"{SCHEDULE_DAY_LABELS['MWF']} or {SCHEDULE_DAY_LABELS['TTHF']}.")
        code = schedule_group(cleaned)
        if code not in SCHEDULE_GROUP_DAYS:
            return [], '', ('A schedule must be one rotation: '
                            f"{SCHEDULE_DAY_LABELS['MWF']} or {SCHEDULE_DAY_LABELS['TTHF']}. "
                            'Days from both rotations cannot be combined.')

    return list(SCHEDULE_GROUP_DAYS[code]), code, None
