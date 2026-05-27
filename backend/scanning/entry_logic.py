from django.utils import timezone
from .models import SCHEDULE_DAYS, VisitorPass, AccessLog
from vehicles.models import Owner

def check_entry(vehicle) -> dict:
    """
    Returns:
        status  — authorized | denied | wrong_day | pending | unknown
        allowed — True/False
        message — human-readable reason
    """
    owner = vehicle.owner

    if not owner:
        return _result('denied', False, 'Vehicle has no registered owner.')

    if not vehicle.is_authorized:
        return _result('denied', False, 'Vehicle is not authorized for entry.')

    owner_type = owner.owner_type

    # ── EMPLOYEE ──────────────────────────────────────────────────────
    if owner_type == Owner.OwnerType.EMPLOYEE:
        return _result('authorized', True, f'Employee — {owner.full_name}. Entry granted.')

    # ── STUDENT or FETCHER ────────────────────────────────────────────
    if owner_type in [Owner.OwnerType.STUDENT, Owner.OwnerType.FETCHER]:
        today     = timezone.localdate().weekday()     # 0=Mon ... 6=Sun
        allowed_days = SCHEDULE_DAYS.get(owner.schedule, [])

        if today not in allowed_days:
            day_name  = timezone.localdate().strftime('%A')
            return _result(
                'wrong_day', False,
                f'{owner_type.capitalize()} is on {owner.schedule} schedule. Today is {day_name}.'
            )
        return _result('authorized', True, f'{owner_type.capitalize()} — {owner.full_name}. Entry granted.')

    # ── VISITOR ───────────────────────────────────────────────────────
    if owner_type == Owner.OwnerType.VISITOR:
        today = timezone.localdate()
        pass_ = VisitorPass.objects.filter(
            vehicle=vehicle,
            valid_date=today,
        ).order_by('-created_at').first()

        if not pass_:
            return _result('denied', False, 'No visitor pass found for today. Please register at the gate.')

        if pass_.status == VisitorPass.Status.PENDING:
            return _result('pending', False, f'Visitor pass pending confirmation from {pass_.office.name}.')

        if pass_.status == VisitorPass.Status.REJECTED:
            return _result('denied', False, f'Visitor pass was rejected by {pass_.office.name}.')

        if pass_.status == VisitorPass.Status.EXPIRED:
            return _result('denied', False, 'Visitor pass has expired.')

        if pass_.status == VisitorPass.Status.CONFIRMED:
            return _result('authorized', True, f'Visitor confirmed by {pass_.office.name}. Entry granted.')

    return _result('denied', False, 'Unknown owner type.')


def _result(status, allowed, message):
    return {'status': status, 'allowed': allowed, 'message': message}