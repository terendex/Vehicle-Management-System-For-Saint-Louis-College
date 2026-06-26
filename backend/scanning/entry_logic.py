from django.utils import timezone
from django.db.models import Q
from .models import SCHEDULE_DAYS, VisitorPass, AccessLog
from vehicles.models import RuleConstraint, Vehicle
from accounts.models import User

DAY_TO_WEEKDAY = {
    'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3,
    'fri': 4, 'sat': 5, 'sun': 6,
}

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
    return start_minutes <= current_minutes <= end_minutes

def _is_within_days(rule, today_weekday=None):
    if today_weekday is None:
        today_weekday = timezone.localdate().weekday()
    day_keys = {v: k for k, v in DAY_TO_WEEKDAY.items()}
    today_key = day_keys.get(today_weekday)
    if not today_key:
        return False
    return today_key in rule.days

def check_entry(vehicle) -> dict:
    user = vehicle.user

    if not user:
        return _result('denied', False, 'Vehicle has no registered owner.', None)

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
        allowed_days = SCHEDULE_DAYS.get(user.schedule, [])
        if today_weekday not in allowed_days:
            day_name = timezone.localdate().strftime('%A')
            return _result('wrong_day', False,
                f'Student is on {user.schedule} schedule. Today ({day_name}) is not an allowed day.',
                rule.name if rule else None)
        if rule and not _is_within_window(rule, now):
            return _result('denied', False,
                f'Student access restricted. Outside allowed hours ({rule.start_time}–{rule.end_time}).',
                rule.name)
        return _result('authorized', True, f'Student — {user.full_name}. Entry granted.', rule.name if rule else None)

    # ── FETCHER ───────────────────────────────────────────────────────
    if owner_type == User.OwnerType.FETCHER:
        rule = _get_active_rule(RuleConstraint.ConstraintType.FETCHER)
        allowed_days = SCHEDULE_DAYS.get(user.schedule, [])
        if today_weekday not in allowed_days:
            day_name = timezone.localdate().strftime('%A')
            return _result('wrong_day', False,
                f'Fetcher is on {user.schedule} schedule. Today ({day_name}) is not an allowed day.',
                rule.name if rule else None)
        if rule and not _is_within_window(rule, now):
            return _result('denied', False,
                f'Fetcher access restricted. Outside allowed hours ({rule.start_time}–{rule.end_time}).',
                rule.name)
        return _result('authorized', True, f'Fetcher — {user.full_name}. Entry granted.', rule.name if rule else None)

    # ── VISITOR (thermal pass-based) ─────────────────────────────────
    if owner_type == User.OwnerType.VISITOR:
        today = timezone.localdate()
        pass_ = VisitorPass.objects.filter(
            vehicle=vehicle,
            valid_date=today,
            status=VisitorPass.Status.ACTIVE,
        ).order_by('-entered_at').first()

        if not pass_:
            return _result('denied', False,
                'No active visitor pass for today. Guard must issue a pass first.', None)
        return _result('authorized', True, 'Visitor pass active. Entry granted.', None)

    return _result('denied', False, 'Unknown owner type.', None)


def _result(status, allowed, message, constraint=None):
    result = {'status': status, 'allowed': allowed, 'message': message}
    if constraint:
        result['constraint'] = constraint
    return result
