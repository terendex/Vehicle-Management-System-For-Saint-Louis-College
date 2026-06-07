from django.utils import timezone
from django.db.models import Q
from .models import SCHEDULE_DAYS, VisitorPass, AccessLog
from vehicles.models import Owner, RuleConstraint

DAY_TO_WEEKDAY = {
    'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3,
    'fri': 4, 'sat': 5, 'sun': 6,
}

def _time_to_minutes(t):
    """Convert HH:MM string to minutes since midnight."""
    if not t:
        return 0
    parts = t.split(':')
    return int(parts[0]) * 60 + int(parts[1])

def _get_active_rule(constraint_type):
    """Return the single active rule for the given type, or None."""
    return RuleConstraint.objects.filter(
        constraint_type=constraint_type,
        enabled=True
    ).first()

def _is_within_window(rule, now=None):
    """Check current time (or given time) is within rule start_time..end_time."""
    if now is None:
        now = timezone.localtime()
    current_minutes = now.hour * 60 + now.minute
    start_minutes = _time_to_minutes(rule.start_time)
    end_minutes = _time_to_minutes(rule.end_time)
    return start_minutes <= current_minutes <= end_minutes

def _is_within_days(rule, today_weekday=None):
    """Check if today's weekday (0=Mon) is in rule.days."""
    if today_weekday is None:
        today_weekday = timezone.localdate().weekday()
    day_keys = {v: k for k, v in DAY_TO_WEEKDAY.items()}
    today_key = day_keys.get(today_weekday)
    if not today_key:
        return False
    return today_key in rule.days

def check_entry(vehicle) -> dict:
    """
    Returns:
        status  — authorized | denied | wrong_day | pending | unknown | disabled
        allowed — True/False
        message — human-readable reason
        constraint — the active rule name (if any)
    """
    owner = vehicle.owner

    if not owner:
        return _result('denied', False, 'Vehicle has no registered owner.', None)

    if not vehicle.is_authorized:
        return _result('denied', False, 'Vehicle is not authorized for entry.', None)

    owner_type = owner.owner_type
    today_weekday = timezone.localdate().weekday()
    now = timezone.localtime()

    # ── EMPLOYEE ──────────────────────────────────────────────────────
    if owner_type == Owner.OwnerType.EMPLOYEE:
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
        return _result('authorized', True, f'Employee — {owner.full_name}. Entry granted.', rule.name if rule else None)

    # ── STUDENT or FETCHER ────────────────────────────────────────────
    if owner_type in [Owner.OwnerType.STUDENT, Owner.OwnerType.FETCHER]:
        rule = _get_active_rule(RuleConstraint.ConstraintType.STUDENT)
        if rule:
            if not _is_within_days(rule, today_weekday):
                day_name = timezone.localdate().strftime('%A')
                return _result('wrong_day', False,
                    f'{owner_type.capitalize()} access restricted. Today ({day_name}) is not allowed by rule: {rule.name}.',
                    rule.name)
            if not _is_within_window(rule, now):
                return _result('denied', False,
                    f'{owner_type.capitalize()} access restricted. Outside allowed hours ({rule.start_time}–{rule.end_time}) per rule: {rule.name}.',
                    rule.name)
            return _result('authorized', True, f'{owner_type.capitalize()} — {owner.full_name}. Entry granted.', rule.name)

        # Fall back to owner schedule
        allowed_days = SCHEDULE_DAYS.get(owner.schedule, [])
        if today_weekday not in allowed_days:
            day_name = timezone.localdate().strftime('%A')
            return _result(
                'wrong_day', False,
                f'{owner_type.capitalize()} is on {owner.schedule} schedule. Today is {day_name}.',
                None)
        return _result('authorized', True, f'{owner_type.capitalize()} — {owner.full_name}. Entry granted.', None)

    # ── VISITOR ───────────────────────────────────────────────────────
    if owner_type == Owner.OwnerType.VISITOR:
        rule = _get_active_rule(RuleConstraint.ConstraintType.VISITOR)
        if not rule:
            return _result('disabled', False,
                'Visitor access is currently disabled. Please register at the gate.', None)

        if not _is_within_days(rule, today_weekday):
            day_name = timezone.localdate().strftime('%A')
            return _result('wrong_day', False,
                f'Visitor access restricted. Today ({day_name}) is not allowed by rule: {rule.name}.',
                rule.name)

        if not _is_within_window(rule, now):
            return _result('denied', False,
                f'Visitor access restricted. Outside allowed hours ({rule.start_time}–{rule.end_time}) per rule: {rule.name}.',
                rule.name)

        today = timezone.localdate()
        pass_ = VisitorPass.objects.filter(
            vehicle=vehicle,
            valid_date=today,
        ).order_by('-created_at').first()

        if not pass_:
            return _result('denied', False,
                'No visitor pass found for today. Please register at the gate.',
                rule.name)

        if pass_.status == VisitorPass.Status.PENDING:
            return _result('pending', False,
                f'Visitor pass pending confirmation from {pass_.office.name}.',
                rule.name)

        if pass_.status == VisitorPass.Status.REJECTED:
            return _result('denied', False,
                f'Visitor pass was rejected by {pass_.office.name}.',
                rule.name)

        if pass_.status == VisitorPass.Status.EXPIRED:
            return _result('denied', False,
                'Visitor pass has expired.',
                rule.name)

        if pass_.status == VisitorPass.Status.CONFIRMED:
            return _result('authorized', True,
                f'Visitor confirmed by {pass_.office.name}. Entry granted.',
                rule.name)

    return _result('denied', False, 'Unknown owner type.', None)


def _result(status, allowed, message, constraint=None):
    result = {'status': status, 'allowed': allowed, 'message': message}
    if constraint:
        result['constraint'] = constraint
    return result