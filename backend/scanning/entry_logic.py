# =============================================================================
# WHAT THIS FILE IS FOR
#
# This is the gate's decision-maker. When a plate is read (by camera or typed by
# a guard), something has to answer one question: may this vehicle come in right
# now? That answer is produced here, by check_entry() at the bottom, and it is
# returned as a small dictionary the rest of the system can act on:
#
#     {'status': ..., 'allowed': True/False, 'message': ..., 'constraint': ...}
#
# "allowed" drives the barrier and the guard's screen; "message" is the sentence
# the guard reads out; "status" lets other code tell the reasons apart (for
# example, a confiscated account is treated differently from a wrong day).
#
# Two kinds of rule decide the outcome:
#   1. The CDSO's campus-wide rules (RuleConstraint): which days and which hours
#      a kind of entrant may come in at all.
#   2. The individual's own registration: the days that particular owner signed
#      up for (their "campus days").
#
# Everything above check_entry() is a helper it leans on.
# =============================================================================

from django.utils import timezone       # the clock and calendar, in campus local time
from django.db.models import Q          # lets several "match this" conditions be OR-ed together
from .models import VisitorPass, AccessLog          # gate passes, and the record of each scan
from vehicles.models import RuleConstraint, Vehicle, SystemSettings, Event  # rules, vehicles, settings, events
from accounts.models import User        # owner accounts (student, employee, fetcher, visitor)

# Python counts weekdays as Monday=0 … Sunday=6. A RuleConstraint stores its
# allowed days as short text keys, so this table converts between the two.
DAY_TO_WEEKDAY = {
    'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3,     # start of the week
    'fri': 4, 'sat': 5, 'sun': 6,               # end of the week
}

# The same idea for a person's own campus days, which are stored as full day
# names ("Monday") because that is what the registration form collects.
DAY_NAME_TO_WEEKDAY = {
    'Monday': 0, 'Tuesday': 1, 'Wednesday': 2, 'Thursday': 3,
    'Friday': 4, 'Saturday': 5, 'Sunday': 6,
}

# Legacy fallback: schedule code → weekday numbers, used only when user.campus_days is empty.
# 'TTHS' (Tue/Thu/Sat) was the second rotation before it became TTHF (Tue/Thu/Fri);
# the migration renames stored codes, but an unknown code here would mean "allowed
# on no day at all", so the old one keeps its original meaning.
_SCHEDULE_DAYS_FALLBACK = {
    'MWF':  [0, 2, 4],                          # Monday, Wednesday, Friday
    'TTHF': [1, 3, 4],                          # Tuesday, Thursday, Friday
    'TTHS': [1, 3, 5],                          # the older rotation: Tuesday, Thursday, Saturday
    'ANY':  [0, 1, 2, 3, 4, 5, 6],              # every day of the week
    'ALL':  [0, 1, 2, 3, 4, 5, 6],              # same meaning, different wording used in old data
    'MIXED': [0, 1, 2, 3, 4, 5, 6],             # same again, for owners with no fixed rotation
}


# The same legacy codes spelled out in words, for messages a person has to read.
_SCHEDULE_TO_DAY_NAMES = {
    'MWF':  ['Monday', 'Wednesday', 'Friday'],
    'TTHF': ['Tuesday', 'Thursday', 'Friday'],
    'TTHS': ['Tuesday', 'Thursday', 'Saturday'],
}


# Works out which days of the week this particular owner is allowed on campus,
# as numbers the rest of the file can compare against today's weekday.
def _allowed_weekdays(user) -> list[int]:
    """Return the weekday integers (Mon=0…Sun=6) this user is permitted."""
    campus_days = user.campus_days or []                # the days they registered for; [] when never set
    if campus_days:
        # Turn each stored day name into its number, ignoring anything unrecognised
        # so one bad entry cannot make the whole list collapse to "no days".
        return [DAY_NAME_TO_WEEKDAY[d] for d in campus_days if d in DAY_NAME_TO_WEEKDAY]
    # Legacy: user created before campus_days was stored; fall back to schedule code.
    # An unrecognised code means we cannot say which days this owner registered
    # for, so it falls back to every day and lets the RuleConstraint do the
    # limiting — returning [] here read as "no personal restriction" to the
    # callers anyway, just without saying so.
    return _SCHEDULE_DAYS_FALLBACK.get(user.schedule or 'ANY', _SCHEDULE_DAYS_FALLBACK['ANY'])


# Turns the same information into a sentence, so a denial can tell the driver
# which days they are actually registered for.
def _registered_days_display(user) -> str:
    """Human-readable list of the user's registered campus days (for violation messages)."""
    campus_days = user.campus_days or []                # preferred source: the days on their registration
    if campus_days:
        return ', '.join(campus_days)                   # e.g. "Monday, Wednesday, Friday"
    # Legacy users without campus_days — expand schedule code to full day names
    schedule = user.schedule or ''                      # e.g. 'MWF'; '' when the account has neither
    if schedule in _SCHEDULE_TO_DAY_NAMES:
        return ', '.join(_SCHEDULE_TO_DAY_NAMES[schedule])   # spell the code out in full day names
    return schedule or 'Not specified'                  # unknown code: show it as-is rather than invent days

# Converts a stored time such as "06:00" into minutes since midnight (360), so
# two times can be compared with plain number comparison.
def _time_to_minutes(t):
    if not t:
        return 0                                        # missing time counts as midnight
    parts = t.split(':')                                # "06:30" -> ["06", "30"]
    return int(parts[0]) * 60 + int(parts[1])           # hours to minutes, plus the minutes

# Fetches the rule currently in force for one kind of entrant (student,
# employee, fetcher, supplier), or None when the CDSO has not enabled one.
def _get_active_rule(constraint_type):
    return RuleConstraint.objects.filter(
        constraint_type=constraint_type,                # only rules for this kind of entrant
        enabled=True                                    # ignore rules the CDSO has switched off
    ).first()                                           # None when no enabled rule exists

# Answers "is the current time inside this rule's allowed hours?".
def _is_within_window(rule, now=None):
    if now is None:
        now = timezone.localtime()                      # default to the campus clock right now
    current_minutes = now.hour * 60 + now.minute        # the moment, as minutes since midnight
    start_minutes = _time_to_minutes(rule.start_time)   # when the rule opens
    end_minutes = _time_to_minutes(rule.end_time)       # when the rule closes
    if start_minutes <= end_minutes:
        # An ordinary same-day window such as 06:00–20:00: simply be between them.
        return start_minutes <= current_minutes <= end_minutes
    # Window wraps past midnight (e.g. 20:00–06:00 for a night shift). Read
    # straight through, this compared as start <= now <= end and was true for
    # no minute of the day, so the rule denied entry around the clock.
    return current_minutes >= start_minutes or current_minutes <= end_minutes

# Answers "does this rule allow entry on today's day of the week?".
def _is_within_days(rule, today_weekday=None):
    if today_weekday is None:
        today_weekday = timezone.localdate().weekday()  # today as a number, Monday=0
    day_keys = {v: k for k, v in DAY_TO_WEEKDAY.items()}  # flip the table: number -> 'mon'
    today_key = day_keys.get(today_weekday)             # today's short key
    if not today_key:
        return False                                    # unrecognised day: refuse rather than assume
    return today_key in rule.days                       # the rule stores its allowed days as these keys


# Combines the two day checks for owners who have their own campus days, and
# produces the sentence explaining the refusal when either check fails.
def _day_denial(rule, user, today_weekday) -> str | None:
    """Day check for owner types that carry their own campus days.

    Two things have to agree before an owner may enter today: the rule's
    Allowed Days, which is the campus-wide ceiling the CDSO sets, and the
    owner's own registered days. The rule is checked first because "the campus
    is closed today" is a different fact from "you did not register for today",
    and a guard reading the wrong one of those would send the owner to the
    wrong office. Returns a denial message, or None when the day is allowed.
    """
    day_name = timezone.localdate().strftime('%A')      # today in words, e.g. "Sunday", for the message
    if rule and not _is_within_days(rule, today_weekday):
        # The campus-wide rule bars today, so nobody of this type may enter.
        return (f'Campus closed to this entry type today ({day_name}) '
                f'per rule: {rule.name}.')
    allowed_days = _allowed_weekdays(user)              # the days this individual signed up for
    if allowed_days and today_weekday not in allowed_days:
        # The campus is open to this type, but this person did not register for today.
        return (f'Not allowed on campus today ({day_name}). '
                f'Registered days: {_registered_days_display(user)}.')
    return None                                         # None means "the day is fine, carry on"


# Finds the event that is running right now and names this vehicle as one of its
# organizers, so organizers can be recognised at the gate.
def organizer_event_for(*identifiers):
    """The event under way right now that lists any of these identifiers — a
    plate, conduction number or e-bike control number, however typed — as an
    organizer, or None. A registered vehicle is looked up by all of its
    identifiers, since the list may name it by any one of them.

    "Under way" is Event.is_under_way — switched on, not archived, today, and
    inside its start/end times when it has them — the same test the parking
    reserve uses. This used to check `is_active` alone, so an event nobody
    switched off kept tagging its organizers at the gate for weeks, while the
    parking screens (which did check the clock) had long stopped reserving for
    it.
    """
    from vehicles.models import canonical_identifier    # imported here to avoid a circular import at start-up
    wanted = {canonical_identifier(i) for i in identifiers} - {''}   # tidy each identifier; drop blanks
    if not wanted:
        return None                                     # nothing usable to search for
    listed = Q()                                        # start with an empty condition to build on
    for ident in wanted:
        listed |= Q(organizer_plates__contains=[ident])  # "or this identifier appears in the organizer list"
    candidates = Event.objects.filter(
        listed, is_active=True, archived=False, date=timezone.localdate(),
    )                                                   # today's live events naming any of those identifiers
    # Several events can be on today, so take the first one whose start/end
    # times mean it is actually running at this moment.
    return next((ev for ev in candidates if ev.is_under_way()), None)


# Reduces an event to the few fields the gate screens need to show.
def event_summary(event):
    """The organizer event as the scan responses carry it."""
    if event is None:
        return None                                     # no event: the response simply carries nothing
    return {'id': event.id, 'name': event.name, 'date': event.date.isoformat(),
            'time_display': event.time_display}


# Convenience wrapper: look the event up and hand it back ready for a response.
def get_organizer_event(*identifiers):
    """`organizer_event_for` in response form: {id, name, date, time_display} or None."""
    return event_summary(organizer_event_for(*identifiers))


# Collects every name this vehicle could be listed under, because an organizer
# list may name it by plate, by conduction number, or by whatever was typed.
def vehicle_identifiers(vehicle, typed=''):
    """Everything an event list might name this vehicle by."""
    if vehicle is None:
        return (typed,)                                 # unregistered: all we have is what the guard typed
    return (typed, vehicle.plate_number, getattr(vehicle, 'conduction_number', ''))


# Decides which category the arriving person belongs to, which is what the
# entries screen counts and what the gate record stores.
def classify_entrant(vehicle, plate_number: str = '') -> str:
    """Which kind of person is coming in: student, employee, fetcher, visitor,
    supplier, or unknown.

    Answered from the owner account first, because that is the only source that
    is actually authoritative about who someone is. A plate with no account
    falls back to the supplier roster, then to whether a visitor pass was
    issued for it today — a walk-in with a pass IS a visitor, and reading that
    row as "unregistered" is what made the visitor count on the entries screen
    always zero.

    Returns one of `AccessLog.Category` values; never raises.
    """
    from .models import AccessLog, VisitorPass          # local imports keep this helper importable anywhere
    from vehicles.models import SupplierPlate

    owner = getattr(vehicle, 'user', None) if vehicle else None   # the account behind the plate, if any
    if owner is not None and owner.owner_type:
        # owner_type and AccessLog.Category share their vocabulary
        # (student/employee/fetcher/visitor) — anything else is a role we have
        # no category for, which is exactly what 'unknown' means.
        if owner.owner_type in AccessLog.Category.values:
            return owner.owner_type                     # the account settles it

    plate = (plate_number or getattr(vehicle, 'plate_number', '') or '').strip().upper()  # tidy plate to compare
    if plate and SupplierPlate.objects.filter(
        plate_number=plate, supplier__is_active=True,   # only plates of suppliers still in service
    ).exists():
        return AccessLog.Category.SUPPLIER

    if vehicle is not None and VisitorPass.objects.filter(
        vehicle=vehicle, valid_date=timezone.localdate(),    # a pass issued for today
    ).exists():
        return AccessLog.Category.VISITOR

    return AccessLog.Category.UNKNOWN                   # no account, no supplier row, no pass


# Reports whether the CDSO has switched the campus to "open" mode, in which the
# gate lets everyone through.
def is_open_campus() -> bool:
    """True while Open Campus Mode is enabled in system settings."""
    return SystemSettings.get().open_campus_mode        # SystemSettings.get() returns the single settings row


# =============================================================================
# THE MAIN DECISION
#
# check_entry() is what the gate calls. It works down a ladder of questions and
# returns as soon as one of them settles the matter, in this order:
#   1. Is the campus in Open Campus Mode?  -> let everyone in
#   2. Is the owner's account confiscated? -> refuse, whatever the timetable says
#   3. Is this a visitor (or a plate with no account)? -> decide on their pass
#   4. Is the vehicle authorised, and the account still active?
#   5. Otherwise apply the rules for their type: employee, student, or fetcher
# The order matters: each step answers a different question, and the guard needs
# the reason that actually applies, not merely a true one.
# =============================================================================
def check_entry(vehicle) -> dict:
    # Open Campus Mode — bypass all rules, allow everything. The client-facing
    # status is 'open_entry' (displayed as "Open Entry"); the AccessLog row is
    # still stored as AUTHORIZED so entry/exit pairing and stats keep working.
    settings = SystemSettings.get()                     # the campus settings row (not Django's settings file)
    if settings.open_campus_mode:
        owner_name = vehicle.user.full_name if vehicle.user else vehicle.plate_number  # name it if we can, else the plate
        return _result('open_entry', True, f'Open Campus Mode active — {owner_name}. Open entry granted.', None)

    user = vehicle.user                                 # the owner account; None for a plate nobody registered

    # A confiscated account may not enter. Checked before every other rule —
    # including the schedule ones — because the penalty is not "you came on the
    # wrong day", and a guard reading "wrong day" would have no idea the account
    # is serving a violation penalty.
    #
    # The caller turns this status into a fresh offence (see
    # scanning/views.py): being detected during a confiscation is itself a
    # violation, which is what stops the penalty from being ignorable.
    if user is not None and user.is_confiscated:
        days = user.confiscation_days_left              # whole days left, or None when open-ended
        when = (f'{days} day(s) left' if days is not None
                else 'until the CDSO lifts it')         # phrase the remaining time for the guard
        return _result(
            'confiscated', False,
            f'Entry denied — account confiscated ({when}). '
            f'Offence {user.confiscation_level} of 3. '   # which step of the three-strike ladder they are on
            'Report to the CDSO office.',
            None,
        )

    # ── VISITOR / gate-issued vehicle (pass-based entry) ──────────────
    # Covers vehicles created at the gate when a pass is issued (no owner
    # account) and owners registered as visitors. Entry is granted purely by
    # an active, unexpired pass for today.
    if not user or user.owner_type == User.OwnerType.VISITOR:
        today = timezone.localdate()                    # passes are issued for one calendar day
        pass_ = VisitorPass.objects.filter(
            vehicle=vehicle,
            valid_date=today,                           # today's pass only
            status=VisitorPass.Status.ACTIVE,           # one that has not been closed off
        ).order_by('-entered_at').first()               # the most recently used one, if several exist
        if pass_ and pass_.expires_at and pass_.expires_at < timezone.now():
            # The pass exists but its time ran out, which is a different problem
            # from never having had one — so say so.
            return _result('no_pass', False,
                'Visitor pass expired — create a new pass to grant entry.', None)
        if pass_:
            return _result('authorized', True, 'Visitor pass active. Entry granted.', None)
        if not user:
            # Gate-created vehicle row (made when a visitor pass was issued).
            # Once that pass is exited, the plate reads as unregistered again —
            # the same lookup status it had before the pass existed.
            return _result('unknown', False, 'Plate not registered.', None)
        return _result('no_pass', False,
            'No active visitor pass for today. Create a visitor pass to grant entry.', None)

    # The vehicle itself must be approved — a registration can be on file but
    # not yet accepted, or withdrawn later.
    if not vehicle.is_authorized:
        return _result('denied', False, 'Vehicle is not authorized for entry.', None)

    # The person must still have a usable account, separately from the vehicle.
    if not user.is_active:
        return _result('denied', False, 'Owner account is suspended/disabled.', None)

    owner_type     = user.owner_type                    # which set of rules applies below
    today_weekday  = timezone.localdate().weekday()     # today as a number, for the day checks
    now            = timezone.localtime()               # this moment, for the hour checks

    # ── EMPLOYEE ──────────────────────────────────────────────────────
    if owner_type == User.OwnerType.EMPLOYEE:
        rule = _get_active_rule(RuleConstraint.ConstraintType.EMPLOYEE)   # the employee rule, if enabled
        if rule:
            # Employees are checked against the campus rule only: unlike students
            # and fetchers, they carry no personal list of campus days.
            if not _is_within_days(rule, today_weekday):
                day_name = timezone.localdate().strftime('%A')   # name the day in the refusal
                return _result('wrong_day', False,
                    f'Employee access restricted. Today ({day_name}) is not allowed by rule: {rule.name}.',
                    rule.name)
            if not _is_within_window(rule, now):
                return _result('denied', False,
                    f'Employee access restricted. Outside allowed hours ({rule.start_time}–{rule.end_time}) per rule: {rule.name}.',
                    rule.name)
        # No enabled rule, or every check passed: let them in and name the rule that applied.
        return _result('authorized', True, f'Employee — {user.full_name}. Entry granted.', rule.name if rule else None)

    # ── STUDENT ───────────────────────────────────────────────────────
    if owner_type == User.OwnerType.STUDENT:
        rule = _get_active_rule(RuleConstraint.ConstraintType.STUDENT_VEHICLE)
        denial = _day_denial(rule, user, today_weekday)     # campus days AND their own registered days
        if denial:
            return _result('wrong_day', False, denial, rule.name if rule else None)
        if rule and not _is_within_window(rule, now):
            return _result('denied', False,
                f'Student access restricted. Outside allowed hours ({rule.start_time}–{rule.end_time}).',
                rule.name)
        return _result('authorized', True, f'Student — {user.full_name}. Entry granted.', rule.name if rule else None)

    # ── FETCHER ───────────────────────────────────────────────────────
    if owner_type == User.OwnerType.FETCHER:
        rule = _get_active_rule(RuleConstraint.ConstraintType.FETCHER)
        denial = _day_denial(rule, user, today_weekday)     # same two day checks as students
        if denial:
            return _result('wrong_day', False, denial, rule.name if rule else None)
        # Standby fetchers are allowed to park inside campus while waiting, so
        # the drop-off/pick-up time window only restricts Drop & Go fetchers.
        is_standby = user.registrations.filter(
            status='accepted', registrant_type='fetcher', fetcher_type='standby',
        ).exists()                                      # true when an approved standby registration exists
        if rule and not is_standby and not _is_within_window(rule, now):
            return _result('denied', False,
                f'Fetcher access restricted. Outside allowed hours ({rule.start_time}–{rule.end_time}).',
                rule.name)
        label = 'Fetcher (Standby)' if is_standby else 'Fetcher'   # say which kind, so the guard sees why waiting is allowed
        return _result('authorized', True, f'{label} — {user.full_name}. Entry granted.', rule.name if rule else None)

    # Reached only when the account has a type this function has no rules for,
    # so the safe answer is to refuse and let a person sort it out.
    return _result('denied', False, 'Unknown owner type.', None)


# Builds the little dictionary every answer above is returned in, so they all
# have the same shape.
def _result(status, allowed, message, constraint=None):
    result = {'status': status, 'allowed': allowed, 'message': message}
    if constraint:
        result['constraint'] = constraint               # only present when a named rule decided the outcome
    return result
