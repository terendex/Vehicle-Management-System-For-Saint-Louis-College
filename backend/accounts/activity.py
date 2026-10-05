"""One user's schedule, vehicles, violations and gate visits, for the admin.

Behind GET /api/accounts/users/<pk>/activity/: the View Profile modal shows the
schedule and a violations summary from it, and the Activity window lists the
visits and violations in full. Each piece reuses the rule it reports on, so the
page can never describe a schedule the gate does not enforce:

  * the days come from scanning.entry_logic (the owner's own campus days,
    within the days their RuleConstraint allows);
  * the hours and the stay limit are that RuleConstraint's;
  * visits are merged entry/exit pairs from the Vehicle Log's own merge.
"""
from django.db.models import Q
from django.utils import timezone

# Which RuleConstraint governs each kind of owner. A visitor has none: their
# pass carries its own duration.
RULE_FOR_OWNER_TYPE = {
    'student':  'student_vehicle',
    'employee': 'employee',
    'fetcher':  'fetcher',
}

DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
VISIT_LIMIT = 300


def _schedule(user):
    from scanning.entry_logic import DAY_TO_WEEKDAY, _allowed_weekdays, _get_active_rule
    if user.role != 'vehicle_owner':
        return None
    rule_type = RULE_FOR_OWNER_TYPE.get(user.owner_type or '')
    rule = _get_active_rule(rule_type) if rule_type else None
    own = sorted(set(_allowed_weekdays(user)))
    if rule is not None:
        rule_days = sorted(DAY_TO_WEEKDAY[d] for d in rule.days if d in DAY_TO_WEEKDAY)
        days = [d for d in own if d in rule_days] if user.campus_days or user.schedule else rule_days
    else:
        rule_days, days = [], own
    return {
        'owner_type':       user.owner_type or '',
        'owner_type_label': user.get_owner_type_display() if user.owner_type else '',
        'days':             [DAY_NAMES[d] for d in days],
        'registered_days':  list(user.campus_days or []),
        'rule_name':        rule.name if rule else '',
        'start_time':       rule.start_time if rule else '',
        'end_time':         rule.end_time if rule else '',
        'max_stay_minutes': rule.max_stay_minutes if rule else None,
        'expires_at':       user.expires_at.isoformat() if user.expires_at else None,
    }


def _violations(user):
    from violations.models import Violation, format_overstay
    rows = (Violation.objects
            .filter(Q(owner=user) | Q(vehicle__user=user))
            .distinct().order_by('-issued_at'))
    labels = dict(Violation.Type.choices)
    labels[Violation.Type.UNAUTHORIZED] = labels[Violation.Type.UNAUTHORIZED_ENTRY]
    out = []
    for v in rows:
        settled = v.is_resolved or v.status in (Violation.Status.CLEARED, Violation.Status.LIFTED)
        label = labels.get(v.violation_type, v.violation_type)
        if v.overstay_minutes:
            label = f'{label} ({format_overstay(v.overstay_minutes)})'
        out.append({
            'id':               v.pk,
            'issued_at':        v.issued_at.isoformat(),
            'violation_type':   v.violation_type,
            'label':            label,
            'overstay_minutes': v.overstay_minutes,
            'status':           v.status,
            'status_label':     v.get_status_display(),
            'offense_number':   v.offense_number,
            'settled':          settled,
            'plate':            v.identifier,
            'notes':            v.notes,
        })
    return out


def _visits(user, date_from='', date_to=''):
    from datetime import timedelta
    from scanning.models import AccessLog, Gate
    from scanning.occupancy import STALE_ENTRY_HOURS
    from scanning.views import _merge_access_log_visits, _visit_duration_minutes
    from time_utils import filter_local_date_range

    plates = [p for p in user.vehicles.values_list('plate_number', flat=True) if p]
    qs = (AccessLog.objects
          .filter(Q(vehicle__user=user) | Q(plate_number__in=plates))
          .select_related('paired_entry')
          .order_by('-scanned_at'))
    qs = filter_local_date_range(qs, 'scanned_at', date_from, date_to)
    logs = list(qs[:VISIT_LIMIT])
    visible, exit_by_entry = _merge_access_log_visits(logs)
    gates = dict(Gate.objects.values_list('gate_id', 'label'))
    statuses = dict(AccessLog.Status.choices)
    now, today = timezone.now(), timezone.localdate()
    out = []
    for log in visible:
        exit_log = exit_by_entry.get(log.id)
        # "Still inside" is the occupancy ledger's rule (scanning/occupancy.py):
        # an entry of today, not older than STALE_ENTRY_HOURS. An older entry
        # with no exit is a missed exit scan, not a car that never left.
        open_entry = log.status == AccessLog.Status.AUTHORIZED and exit_log is None
        inside = (open_entry and timezone.localdate(log.scanned_at) == today
                  and log.scanned_at >= now - timedelta(hours=STALE_ENTRY_HOURS))
        if log.status == AccessLog.Status.EXITED:          # a lone exit: its entry is outside the list
            entered = log.paired_entry.scanned_at if log.paired_entry_id else None
            exited, exit_gate = log.scanned_at, log.gate_id
            gate = log.paired_entry.gate_id if log.paired_entry_id else ''
        else:
            entered, gate = log.scanned_at, log.gate_id
            exited = exit_log.scanned_at if exit_log else None
            exit_gate = exit_log.gate_id if exit_log else ''
        out.append({
            'id':               log.id,
            'plate':            log.plate_number,
            'status':           log.status,
            'status_label':     statuses.get(log.status, log.status),
            'entered_at':       entered.isoformat() if entered else None,
            'exited_at':        exited.isoformat() if exited else None,
            'gate':             gates.get(gate, gate),
            'exit_gate':        gates.get(exit_gate, exit_gate),
            'duration_minutes': (_visit_duration_minutes(log, exit_log) if exit_log else None),
            'still_inside':     inside,
            'no_exit':          open_entry and not inside,
            'denied_reason':    log.denied_reason,
        })
    return out


def user_activity(user, date_from='', date_to=''):
    violations = _violations(user)
    visits = _visits(user, date_from, date_to)
    return {
        'user': {
            'id':        user.pk,
            'full_name': user.full_name,
            'email':     user.email,
            'role':      user.role,
            'user_code': user.user_code,
        },
        'schedule':   _schedule(user),
        'vehicles':   [{'id': v.pk, 'plate': v.plate_number, 'vehicle_type': v.vehicle_type,
                        'authorized': v.is_authorized}
                       for v in user.vehicles.order_by('plate_number')],
        'violations': violations,
        'visits':     visits,
        'counts': {
            'violations':        len(violations),
            'violations_active': sum(1 for v in violations if not v['settled']),
            'visits':            len(visits),
            'visit_limit':       VISIT_LIMIT,
        },
        'generated_at': timezone.now().isoformat(),
    }
