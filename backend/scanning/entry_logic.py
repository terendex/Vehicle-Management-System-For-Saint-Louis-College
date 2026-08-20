from django.utils import timezone
from django.db.models import Q
from .models import VisitorPass, AccessLog
from vehicles.models import RuleConstraint, Vehicle, SystemSettings, Event
from accounts.models import User

DAY_TO_WEEKDAY = {
    'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3,
    'fri': 4, 'sat': 5, 'sun': 6,
}

DAY_NAME_TO_WEEKDAY = {
    'Monday': 0, 'Tuesday': 1, 'Wednesday': 2, 'Thursday': 3,
    'Friday': 4, 'Saturday': 5, 'Sunday': 6,
}

# Legacy fallback: schedule code → weekday numbers, used only when user.campus_days is empty.
# 'TTHS' (Tue/Thu/Sat) was the second rotation before it became TTHF (Tue/Thu/Fri);
# the migration renames stored codes, but an unknown code here would mean "allowed
# on no day at all", so the old one keeps its original meaning.
_SCHEDULE_DAYS_FALLBACK = {
    'MWF':  [0, 2, 4],
    'TTHF': [1, 3, 4],
    'TTHS': [1, 3, 5],
    'ANY':  [0, 1, 2, 3, 4, 5, 6],
    'ALL':  [0, 1, 2, 3, 4, 5, 6],
    'MIXED': [0, 1, 2, 3, 4, 5, 6],
}


_SCHEDULE_TO_DAY_NAMES = {
    'MWF':  ['Monday', 'Wednesday', 'Friday'],
    'TTHF': ['Tuesday', 'Thursday', 'Friday'],
    'TTHS': ['Tuesday', 'Thursday', 'Saturday'],
}


def _allowed_weekdays(user) -> list[int]:
    """Return the weekday integers (Mon=0…Sun=6) this user is permitted."""
    campus_days = user.campus_days or []
    if campus_days:
        return [DAY_NAME_TO_WEEKDAY[d] for d in campus_days if d in DAY_NAME_TO_WEEKDAY]
    # Legacy: user created before campus_days was stored; fall back to schedule code.
    # An unrecognised code means we cannot say which days this owner registered
    # for, so it falls back to every day and lets the RuleConstraint do the
    # limiting — returning [] here read as "no personal restriction" to the
    # callers anyway, just without saying so.
    return _SCHEDULE_DAYS_FALLBACK.get(user.schedule or 'ANY', _SCHEDULE_DAYS_FALLBACK['ANY'])


def _registered_days_display(user) -> str:
    """Human-readable list of the user's registered campus days (for violation messages)."""
    campus_days = user.campus_days or []
    if campus_days:
        return ', '.join(campus_days)
    # Legacy users without campus_days — expand schedule code to full day names
    schedule = user.schedule or ''
    if schedule in _SCHEDULE_TO_DAY_NAMES:
        return ', '.join(_SCHEDULE_TO_DAY_NAMES[schedule])
    return schedule or 'Not specified'

def _time_to_minutes(t):
    if not t:
        return 0
    parts = t.split(':')
    return int(parts[0]) * 60 + int(parts[1])

def _get_active_rule(constraint_type):
    return RuleConstraint.objects.filter(
        constraint_type=constraint_type,
        enabled=True
    ).first()

def _is_within_window(rule, now=None):
    if now is None:
        now = timezone.localtime()
    current_minutes = now.hour * 60 + now.minute
    start_minutes = _time_to_minutes(rule.start_time)
    end_minutes = _time_to_minutes(rule.end_time)
    if start_minutes <= end_minutes:
        return start_minutes <= current_minutes <= end_minutes
    # Window wraps past midnight (e.g. 20:00–06:00 for a night shift). Read
    # straight through, this compared as start <= now <= end and was true for
    # no minute of the day, so the rule denied entry around the clock.
    return current_minutes >= start_minutes or current_minutes <= end_minutes

def _is_within_days(rule, today_weekday=None):
    if today_weekday is None:
        today_weekday = timezone.localdate().weekday()
    day_keys = {v: k for k, v in DAY_TO_WEEKDAY.items()}
    today_key = day_keys.get(today_weekday)
    if not today_key:
        return False
    return today_key in rule.days


def _day_denial(rule, user, today_weekday) -> str | None:
    """Day check for owner types that carry their own campus days.

    Two things have to agree before an owner may enter today: the rule's
    Allowed Days, which is the campus-wide ceiling the CDSO sets, and the
    owner's own registered days. The rule is checked first because "the campus
    is closed today" is a different fact from "you did not register for today",
    and a guard reading the wrong one of those would send the owner to the
    wrong office. Returns a denial message, or None when the day is allowed.
    """
    day_name = timezone.localdate().strftime('%A')
    if rule and not _is_within_days(rule, today_weekday):
        return (f'Campus closed to this entry type today ({day_name}) '
                f'per rule: {rule.name}.')
    allowed_days = _allowed_weekdays(user)
    if allowed_days and today_weekday not in allowed_days:
        return (f'Not allowed on campus today ({day_name}). '
                f'Registered days: {_registered_days_display(user)}.')
    return None


def get_organizer_event(plate_number: str):
    """Return the first active event listing this plate as an organizer, or None."""
    plate_upper = plate_number.strip().upper()
    event = Event.objects.filter(
        is_active=True,
        organizer_plates__contains=[plate_upper],
    ).first()
    if event:
        return {'id': event.id, 'name': event.name, 'date': event.date.isoformat()}
    return None


def is_open_campus() -> bool:
    """True while Open Campus Mode is enabled in system settings."""
    return SystemSettings.get().open_campus_mode


def check_entry(vehicle) -> dict:
    # Open Campus Mode — bypass all rules, allow everything. The client-facing
    # status is 'open_entry' (displayed as "Open Entry"); the AccessLog row is
    # still stored as AUTHORIZED so entry/exit pairing and stats keep working.
    settings = SystemSettings.get()
    if settings.open_campus_mode:
        owner_name = vehicle.user.full_name if vehicle.user else vehicle.plate_number
        return _result('open_entry', True, f'Open Campus Mode active — {owner_name}. Open entry granted.', None)

    user = vehicle.user

    # A confiscated account may not enter. Checked before every other rule —
    # including the schedule ones — because the penalty is not "you came on the
    # wrong day", and a guard reading "wrong day" would have no idea the account
    # is serving a violation penalty.
    #
    # The caller turns this status into a fresh offence (see
    # scanning/views.py): being detected during a confiscation is itself a
    # violation, which is what stops the penalty from being ignorable.
    if user is not None and user.is_confiscated:
        days = user.confiscation_days_left
        when = (f'{days} day(s) left' if days is not None
                else 'until the CDSO lifts it')
        return _result(
            'confiscated', False,
            f'Entry denied — account confiscated ({when}). '
            f'Offence {user.confiscation_level} of 3. '
            'Report to the CDSO office.',
            None,
        )

    # ── VISITOR / gate-issued vehicle (pass-based entry) ──────────────
    # Covers vehicles created at the gate when a pass is issued (no owner
    # account) and owners registered as visitors. Entry is granted purely by
    # an active, unexpired pass for today.
    if not user or user.owner_type == User.OwnerType.VISITOR:
        today = timezone.localdate()
        pass_ = VisitorPass.objects.filter(
            vehicle=vehicle,
            valid_date=today,
            status=VisitorPass.Status.ACTIVE,
        ).order_by('-entered_at').first()
        if pass_ and pass_.expires_at and pass_.expires_at < timezone.now():
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

    if not vehicle.is_authorized:
        return _result('denied', False, 'Vehicle is not authorized for entry.', None)

    if not user.is_active:
        return _result('denied', False, 'Owner account is suspended/disabled.', None)

    owner_type     = user.owner_type
    today_weekday  = timezone.localdate().weekday()
    now            = timezone.localtime()

    # ── EMPLOYEE ──────────────────────────────────────────────────────
    if owner_type == User.OwnerType.EMPLOYEE:
        rule = _get_active_rule(RuleConstraint.ConstraintType.EMPLOYEE)
        if rule:
            if not _is_within_days(rule, today_weekday):
                day_name = timezone.localdate().strftime('%A')
                return _result('wrong_day', False,
                    f'Employee access restricted. Today ({day_name}) is not allowed by rule: {rule.name}.',
                    rule.name)
            if not _is_within_window(rule, now):
                return _result('denied', False,
                    f'Employee access restricted. Outside allowed hours ({rule.start_time}–{rule.end_time}) per rule: {rule.name}.',
                    rule.name)
        return _result('authorized', True, f'Employee — {user.full_name}. Entry granted.', rule.name if rule else None)

    # ── STUDENT ───────────────────────────────────────────────────────
    if owner_type == User.OwnerType.STUDENT:
        rule = _get_active_rule(RuleConstraint.ConstraintType.STUDENT_VEHICLE)
        denial = _day_denial(rule, user, today_weekday)
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
        denial = _day_denial(rule, user, today_weekday)
        if denial:
            return _result('wrong_day', False, denial, rule.name if rule else None)
        # Standby fetchers are allowed to park inside campus while waiting, so
        # the drop-off/pick-up time window only restricts Drop & Go fetchers.
        is_standby = user.registrations.filter(
            status='accepted', registrant_type='fetcher', fetcher_type='standby',
        ).exists()
        if rule and not is_standby and not _is_within_window(rule, now):
            return _result('denied', False,
                f'Fetcher access restricted. Outside allowed hours ({rule.start_time}–{rule.end_time}).',
                rule.name)
        label = 'Fetcher (Standby)' if is_standby else 'Fetcher'
        return _result('authorized', True, f'{label} — {user.full_name}. Entry granted.', rule.name if rule else None)

    return _result('denied', False, 'Unknown owner type.', None)


def _result(status, allowed, message, constraint=None):
    result = {'status': status, 'allowed': allowed, 'message': message}
    if constraint:
        result['constraint'] = constraint
    return result
