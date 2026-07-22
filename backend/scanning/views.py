from datetime import timedelta

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser
from rest_framework import permissions, status
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.db.models import Q
from vehicles.models import Vehicle, SupplierPlate
from violations.models import Violation
from accounts.models import User, AuditLog
from .models import AccessLog, VisitorPass, Office, MLTrainingSample, GuardShift
from .entry_logic import check_entry, get_organizer_event, is_open_campus
from .ml.reader import read_plate
from .ml.collector import record_scan
from .ml.validator import is_valid_ph_plate
from vehicles.serializers import VehicleSerializer
from .serializers import VisitorPassSerializer, OfficeSerializer, AccessLogSerializer, GuardShiftSerializer, MLTrainingSampleSerializer


def get_client_ip(request):
    x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded:
        return x_forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


GATE_DISPLAY = {'gate1': 'Gate 1', 'gate4': 'Gate 4', 'main': 'Main'}


def _gate_label(gate_id: str) -> str:
    """Human-readable gate name for audit-log details. Falls back to the dynamic
    Gate row's label so gates beyond gate1/gate4 also read nicely."""
    if gate_id in GATE_DISPLAY:
        return GATE_DISPLAY[gate_id]
    try:
        from .models import Gate
        g = Gate.objects.filter(gate_id=gate_id).only('label').first()
        if g:
            return g.label
    except Exception:
        pass
    return gate_id or 'Main'


def _audit(request, action, details=''):
    try:
        AuditLog.objects.create(
            actor=request.user,
            action=action,
            details=details,
            ip_address=get_client_ip(request),
        )
    except Exception:
        pass


def _audit_manual_result(request, plate_number, owner_name, gate_id, result):
    """Audit a manual-entry outcome whose branch already wrote its AccessLog but
    not an audit entry (unregistered / open-campus / supplier plates). Skips
    no-op outcomes (duplicate / already_inside) that change no gate state, so
    every real entry/exit/denial a guard types lands in the audit trail."""
    status = result.get('status')
    if status in ('duplicate', 'already_inside'):
        return
    if status == 'exited':
        action = AuditLog.Action.VEHICLE_EXITED
    elif result.get('allowed'):
        action = AuditLog.Action.VEHICLE_ENTERED
    else:
        action = AuditLog.Action.SCAN
    dur = result.get('duration_minutes')
    _audit(
        request, action,
        f"Plate: {plate_number} | Owner: {owner_name} | Gate: {_gate_label(gate_id)} | "
        f"Guard: {getattr(request.user, 'full_name', '')} | Status: {status}"
        + (f" | Duration: {dur} min" if dur is not None else ""),
    )

# Auto-violations are issued at most once per type per vehicle per calendar day
# (see _auto_log_violation); the counter resets at midnight local time.
GRACE_PERIOD_SECONDS = 3             # duplicate-scan dedup window after entry (must be well below camera interval)
EXIT_COOLDOWN_SECONDS = 60           # block new entry for this many seconds after an exit
ENTRY_BREATHING_SECONDS = 60         # re-check within this window after entry stays informational (no exit flip)


def _log_status(entry: dict) -> str:
    """Map a check_entry result to a valid AccessLog status. Client-facing
    statuses like 'open_entry' and 'no_pass' aren't AccessLog choices — store
    those rows as AUTHORIZED/DENIED depending on whether entry was granted."""
    if entry['status'] in AccessLog.Status.values:
        return entry['status']
    return AccessLog.Status.AUTHORIZED if entry['allowed'] else AccessLog.Status.DENIED


def _inside_state(plate_number: str):
    """
    Returns ('outside', None), ('duplicate', entry), or ('inside', entry).
    'duplicate' — plate just authorized within GRACE_PERIOD_SECONDS; ignore the re-scan.
    'inside'    — plate is in campus but past the grace period; treat re-scan as exit.
    """
    today = timezone.localdate()
    last_entry = AccessLog.objects.filter(
        plate_number=plate_number,
        status=AccessLog.Status.AUTHORIZED,
        scanned_at__date=today,
        scanned_at__lte=timezone.now(),  # ignore future-dated rows from clock skew
    ).order_by('-scanned_at').first()

    if not last_entry:
        return ('outside', None)

    # Explicit paired-exit check — avoids reverse-FK isnull quirks on self-referential tables
    if AccessLog.objects.filter(paired_entry=last_entry).exists():
        return ('outside', None)

    seconds_ago = (timezone.now() - last_entry.scanned_at).total_seconds()
    if seconds_ago <= GRACE_PERIOD_SECONDS:
        return ('duplicate', last_entry)

    return ('inside', last_entry)


def _exit_cooldown_remaining(plate_number: str) -> int:
    """Seconds left in the post-exit cooldown, anchored to the exit row itself —
    repeated scans during the window never extend it. 0 when not in cooldown."""
    now = timezone.now()
    cutoff = now - timedelta(seconds=EXIT_COOLDOWN_SECONDS)
    last_exit = AccessLog.objects.filter(
        plate_number=plate_number,
        status=AccessLog.Status.EXITED,
        scanned_at__gte=cutoff,
        scanned_at__lte=now,  # future-dated rows (clock skew) must not wedge the gate
    ).order_by('-scanned_at').first()
    if not last_exit:
        return 0
    return max(0, EXIT_COOLDOWN_SECONDS - int((now - last_exit.scanned_at).total_seconds()))


def _in_exit_cooldown(plate_number: str) -> bool:
    """True if this plate exited within EXIT_COOLDOWN_SECONDS — suppress a new entry scan."""
    return _exit_cooldown_remaining(plate_number) > 0


def _already_inside(plate_number: str) -> bool:
    """True if the plate has an authorized entry today with no paired exit yet."""
    today = timezone.localdate()
    last_entry = AccessLog.objects.filter(
        plate_number=plate_number,
        status=AccessLog.Status.AUTHORIZED,
        scanned_at__date=today,
        scanned_at__lte=timezone.now(),
    ).order_by('-scanned_at').first()
    if not last_entry:
        return False
    return not AccessLog.objects.filter(paired_entry=last_entry).exists()


def _pair_entry_exit(exit_log) -> None:
    """Link exit_log to the most recent unpaired entry for the same plate today."""
    today = timezone.localdate()
    entry = AccessLog.objects.filter(
        plate_number=exit_log.plate_number,
        status=AccessLog.Status.AUTHORIZED,
        scanned_at__date=today,
        exit_log__isnull=True,
    ).order_by('-scanned_at').first()
    if entry:
        exit_log.paired_entry = entry
        exit_log.save(update_fields=['paired_entry'])


def _open_campus_unknown_result(plate_number: str, gate_id: str, user) -> dict:
    """
    Open Campus Mode: admit an unregistered plate at the gate. Runs the same
    entry/exit state machine registered vehicles use (grace-period dedup, exit
    pairing, post-exit cooldown) so the plate can enter AND exit cleanly while
    the mode is on. Rows are stored as AUTHORIZED/EXITED (vehicle=None); the
    client-facing entry status is 'open_entry', displayed as "Open Entry".
    """
    from django.db import transaction as _tx

    inside_status, last_entry = _inside_state(plate_number)

    if inside_status == 'duplicate':
        return {
            'status':         'duplicate',
            'allowed':        False,
            'message':        'Duplicate scan — already processed within grace period.',
            'vehicle':        None,
            'already_inside': True,
        }

    if inside_status == 'inside':
        seconds_inside = (timezone.now() - last_entry.scanned_at).total_seconds()
        if seconds_inside < ENTRY_BREATHING_SECONDS:
            return {
                'status':         'already_inside',
                'allowed':        False,
                'message':        'Vehicle just entered — within the 1-minute entry window.',
                'vehicle':        None,
                'already_inside': True,
            }
        with _tx.atomic():
            locked_entry = AccessLog.objects.select_for_update().filter(pk=last_entry.pk).first()
            if not locked_entry or AccessLog.objects.filter(paired_entry=locked_entry).exists():
                return {
                    'status':         'duplicate',
                    'allowed':        False,
                    'message':        'Duplicate scan — already processed.',
                    'vehicle':        None,
                    'already_inside': False,
                }
            exit_log = AccessLog.objects.create(
                plate_number=plate_number, status=AccessLog.Status.EXITED,
                gate_id=gate_id, scanned_by=user, paired_entry=locked_entry,
            )
        duration_minutes = int((exit_log.scanned_at - last_entry.scanned_at).total_seconds() / 60)
        return {
            'status':           'exited',
            'allowed':          False,
            'message':          f'Open Campus — exit recorded. Duration: {duration_minutes} min.',
            'vehicle':          None,
            'already_inside':   False,
            'duration_minutes': duration_minutes,
        }

    if _in_exit_cooldown(plate_number):
        return {
            'status':         'duplicate',
            'allowed':        False,
            'message':        'Exit cooldown — entry suppressed for 1 minute after exit.',
            'vehicle':        None,
            'already_inside': False,
        }

    AccessLog.objects.create(
        plate_number=plate_number, status=AccessLog.Status.AUTHORIZED,
        gate_id=gate_id, scanned_by=user,
    )
    return {
        'status':         'open_entry',
        'allowed':        True,
        'message':        'Open Campus Mode active — unregistered plate. Open entry granted.',
        'vehicle':        None,
        'has_violations': False,
        'already_inside': False,
    }


def _supplier_rule_denial() -> str | None:
    """Day/time-window check for supplier entries against the supplier
    RuleConstraint. Returns a denial message, or None when entry is allowed.
    Open Campus Mode bypasses the restriction like every other rule."""
    from vehicles.models import SystemSettings
    from .entry_logic import _get_active_rule, _is_within_days, _is_within_window
    rule = _get_active_rule('supplier')
    if not rule or SystemSettings.get().open_campus_mode:
        return None
    if not _is_within_days(rule):
        day_name = timezone.localdate().strftime('%A')
        return f'Supplier access restricted. Today ({day_name}) is not allowed by rule: {rule.name}.'
    if not _is_within_window(rule):
        return (f'Supplier access restricted. Outside allowed hours '
                f'({rule.start_time}–{rule.end_time}) per rule: {rule.name}.')
    return None


def _is_standby_fetcher(user) -> bool:
    """Standby fetchers are allowed to park inside campus while waiting, so the
    fetcher max-stay limit does not apply to them (only to Drop & Go)."""
    return bool(user) and user.registrations.filter(
        status='accepted', registrant_type='fetcher', fetcher_type='standby',
    ).exists()


def _check_stay_limit(plate_number: str, vehicle, constraint_type: str,
                      duration_minutes: int, gate_id: str = '', evidence_bytes=None) -> int:
    """
    Enforce the RuleConstraint max-stay limit for this constraint type at exit
    time. Returns overstay minutes (0 if none/no limit) and auto-issues a
    time-exceed violation when exceeded. Supplier plates have no Vehicle row,
    so one is adopted/created (unauthorized, unowned) to carry the violation.
    """
    from vehicles.models import RuleConstraint, Vehicle
    rule = RuleConstraint.objects.filter(
        constraint_type=constraint_type, enabled=True,
        max_stay_minutes__isnull=False,
    ).first()
    if not rule or duration_minutes <= rule.max_stay_minutes:
        return 0
    overstay = duration_minutes - rule.max_stay_minutes
    if vehicle is None:
        vehicle, _ = Vehicle.objects.get_or_create(
            plate_number=plate_number,
            defaults={'vehicle_type': 'car', 'is_authorized': False},
        )
    try:
        _auto_log_violation(
            vehicle,
            f'Overstay: exceeded allowed {rule.max_stay_minutes} min stay by {overstay} min '
            f'(rule: {rule.name})',
            gate_id,
            vtype=Violation.Type.TIME_EXCEED,
            evidence_bytes=evidence_bytes,
        )
    except Exception:
        pass
    return overstay


def _active_visitor_pass(plate_number: str):
    """Today's ACTIVE visitor pass for this plate, or None. Vehicles on an
    active pass must exit via the slip-QR scan, not by plate."""
    return VisitorPass.objects.filter(
        plate_number=plate_number,
        valid_date=timezone.localdate(),
        status=VisitorPass.Status.ACTIVE,
    ).order_by('-entered_at').first()


def _close_active_pass(plate_number: str, gate_id: str = '', evidence_bytes=None) -> int:
    """
    Mark today's ACTIVE visitor pass for this plate as exited — called from every
    exit path (camera toggle, manual Record Exit, QR scan) so passes don't stay
    open after the visitor leaves. Returns overstay in minutes (0 if none).
    An overstay also auto-issues a 'time_exceed' violation (once per day).
    """
    now = timezone.now()
    pass_ = VisitorPass.objects.filter(
        plate_number=plate_number,
        valid_date=timezone.localdate(),
        status=VisitorPass.Status.ACTIVE,
    ).order_by('-entered_at').first()
    if not pass_:
        return 0
    pass_.status = VisitorPass.Status.EXITED
    pass_.exited_at = now
    pass_.save(update_fields=['status', 'exited_at'])
    if pass_.expires_at and now > pass_.expires_at:
        overstay = int((now - pass_.expires_at).total_seconds() / 60)
        try:
            _auto_log_violation(
                pass_.vehicle,
                f'Visitor overstay: exceeded allowed {pass_.allowed_duration} min by {overstay} min',
                gate_id,
                vtype=Violation.Type.TIME_EXCEED,
                evidence_bytes=evidence_bytes,
            )
        except Exception:
            pass
        return overstay
    return 0


def _auto_log_violation(vehicle, message: str, gate_id: str = '', vtype: str = '',
                        evidence_bytes=None):
    """
    Auto-issue a violation at the gate — at most ONE violation of each type per
    vehicle per calendar day, no matter how often it is scanned or detected that
    day. A new day allows the type to be issued again. Past violations stay
    stored, and the cumulative (non-cleared) count drives severity: 1st/2nd
    offense → warning, 3rd → ₱150 fee imposed, which check_entry then uses to
    block the vehicle at the gate until CDSO clears it.
    """
    from decimal import Decimal
    from violations.models import FEE_ESCALATING_TYPES, FEE_THIRD_OFFENSE
    from .models import active_guard_for_gate

    vtype = vtype or Violation.Type.UNAUTHORIZED_ENTRY

    # One violation of this type per vehicle per calendar day
    dedup_types = [vtype]
    if vtype == Violation.Type.UNAUTHORIZED_ENTRY:
        dedup_types.append(Violation.Type.UNAUTHORIZED)  # legacy auto-logged rows
    already_today = Violation.objects.filter(
        vehicle=vehicle,
        violation_type__in=dedup_types,
        issued_at__date=timezone.localdate(),
    ).exists()
    if already_today:
        return

    offense_num  = Violation.compute_offense_number(vehicle, vtype)
    is_fee_event = offense_num == 3 and vtype in FEE_ESCALATING_TYPES
    violation = Violation.objects.create(
        vehicle              = vehicle,
        violation_type       = vtype,
        notes                = f'Auto-logged at gate: {message}',
        offense_number       = offense_num,
        fine_amount          = FEE_THIRD_OFFENSE if is_fee_event else Decimal('0.00'),
        status               = (Violation.Status.FEE_IMPOSED if is_fee_event
                                else Violation.Status.WARNING),
        registration_blocked = is_fee_event,
        is_released          = True,  # visible to the owner immediately
        on_duty_guard        = active_guard_for_gate(gate_id),
    )
    # Attach the camera frame as evidence (shown in admin table + owner email)
    if evidence_bytes:
        try:
            from django.core.files.base import ContentFile
            violation.evidence.save(
                f"auto_{vehicle.plate_number}_{int(timezone.now().timestamp())}.jpg",
                ContentFile(evidence_bytes), save=True,
            )
        except Exception:
            pass
    try:
        from violations.email_utils import send_violation_warning_email, send_fee_imposed_email
        if offense_num in (1, 2):
            send_violation_warning_email(violation)
        elif is_fee_event:
            send_fee_imposed_email(violation)
    except Exception:
        pass


class ScanView(APIView):
    parser_classes     = [MultiPartParser]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        file = request.FILES.get('image')
        if not file:
            return Response({'error': 'No image provided'}, status=400)

        raw_bytes = file.read()
        plates = read_plate(raw_bytes)
        ml_sample = record_scan(raw_bytes)

        results = []

        if not plates:
            AccessLog.objects.create(plate_number='', status='unreadable', scanned_by=request.user)
            return Response({
                'status': 'unreadable',
                'message': 'Could not read a valid PH plate.',
                'results': [],
                'sample_id': ml_sample.get("sample_id") if ml_sample else None,
            })

        # A gate explicitly supplied by the camera/client wins; otherwise fall
        # back to the scanning guard's own gate so the scan lands in that gate's
        # log rather than the orphan 'main' bucket (visible in no gate's view).
        gate_id = (request.data.get('gate_id') or request.query_params.get('gate_id') or '').strip()
        if not gate_id or gate_id == 'main':
            gate_id = getattr(request.user, 'gate_assignment', None) or 'main'

        for plate_info in plates:
            plate = plate_info["plate_text"]
            bbox = plate_info["bbox"]

            if not is_valid_ph_plate(plate):
                AccessLog.objects.create(plate_number=plate, status=AccessLog.Status.UNREADABLE, gate_id=gate_id, scanned_by=request.user)
                results.append({
                    'plate_number': plate,
                    'status': 'unreadable',
                    'allowed': False,
                    'message': 'Detected text does not match a valid Philippine plate format.',
                    'bbox': bbox,
                    'sample_id': ml_sample.get("sample_id") if ml_sample else None,
                })
                continue

            vehicle = Vehicle.objects.select_related('user').filter(plate_number=plate).first()

            if not vehicle:
                supplier_plate = SupplierPlate.objects.select_related('supplier').filter(
                    plate_number=plate, supplier__is_active=True
                ).first()

                if not supplier_plate:
                    if is_open_campus():
                        r = _open_campus_unknown_result(plate, gate_id, request.user)
                        results.append({
                            **r,
                            'plate_number': plate,
                            'bbox': bbox,
                            'sample_id': ml_sample.get("sample_id") if ml_sample else None,
                        })
                        continue
                    AccessLog.objects.create(plate_number=plate, status='unknown', gate_id=gate_id, scanned_by=request.user)
                    results.append({
                        'plate_number': plate,
                        'status': 'unknown',
                        'message': 'Plate not registered.',
                        'bbox': bbox,
                        'sample_id': ml_sample.get("sample_id") if ml_sample else None,
                    })
                    continue

                supplier_name = supplier_plate.supplier.company_name
                inside_status, last_entry = _inside_state(plate)

                if inside_status == 'duplicate':
                    results.append({
                        'plate_number':   plate,
                        'status':         'duplicate',
                        'allowed':        False,
                        'message':        'Duplicate scan — already processed within grace period.',
                        'is_supplier':    True,
                        'supplier_name':  supplier_name,
                        'already_inside': True,
                        'bbox':           bbox,
                        'sample_id':      ml_sample.get("sample_id") if ml_sample else None,
                    })
                    continue

                if inside_status == 'inside':
                    from django.db import transaction as _tx
                    with _tx.atomic():
                        locked_entry = AccessLog.objects.select_for_update().filter(pk=last_entry.pk).first()
                        if not locked_entry or AccessLog.objects.filter(paired_entry=locked_entry).exists():
                            results.append({'plate_number': plate, 'status': 'duplicate', 'allowed': False,
                                            'message': 'Duplicate scan — already processed.', 'bbox': bbox})
                            continue
                        exit_log = AccessLog.objects.create(
                            plate_number=plate, status=AccessLog.Status.EXITED,
                            gate_id=gate_id, scanned_by=request.user, paired_entry=locked_entry,
                        )
                    delta = exit_log.scanned_at - last_entry.scanned_at
                    duration_minutes = int(delta.total_seconds() / 60)
                    results.append({
                        'plate_number':     plate,
                        'status':           'exited',
                        'allowed':          False,
                        'message':          f'Supplier vehicle — {supplier_name}. Exit recorded. Duration: {duration_minutes} min.',
                        'is_supplier':      True,
                        'supplier_name':    supplier_name,
                        'already_inside':   False,
                        'duration_minutes': duration_minutes,
                        'bbox':             bbox,
                        'sample_id':        ml_sample.get("sample_id") if ml_sample else None,
                    })
                    continue

                if _in_exit_cooldown(plate):
                    results.append({
                        'plate_number':   plate,
                        'status':         'duplicate',
                        'allowed':        False,
                        'message':        'Exit cooldown — entry suppressed for 1 minute after exit.',
                        'is_supplier':    True,
                        'supplier_name':  supplier_name,
                        'already_inside': False,
                        'bbox':           bbox,
                    })
                    continue

                AccessLog.objects.create(
                    plate_number=plate, status=AccessLog.Status.AUTHORIZED,
                    gate_id=gate_id, scanned_by=request.user,
                )
                open_campus = is_open_campus()
                results.append({
                    'plate_number':  plate,
                    'status':        'open_entry' if open_campus else 'authorized',
                    'allowed':       True,
                    'message':       (f'Open Campus Mode active — Supplier vehicle {supplier_name}. Open entry granted.'
                                      if open_campus else
                                      f'Supplier vehicle — {supplier_name}. Entry permitted.'),
                    'is_supplier':   True,
                    'supplier_name': supplier_name,
                    'bbox':          bbox,
                    'sample_id':     ml_sample.get("sample_id") if ml_sample else None,
                })
                continue

            inside_status, last_entry = _inside_state(plate)

            if inside_status == 'duplicate':
                resp = {
                    'plate_number':   plate,
                    'status':         'duplicate',
                    'allowed':        False,
                    'message':        'Duplicate scan — already processed within grace period.',
                    'vehicle':        VehicleSerializer(vehicle).data,
                    'already_inside': True,
                    'bbox':           bbox,
                }
                if ml_sample:
                    resp['sample_id'] = ml_sample['sample_id']
                results.append(resp)
                continue

            if inside_status == 'inside':
                from django.db import transaction as _tx
                with _tx.atomic():
                    locked_entry = AccessLog.objects.select_for_update().filter(
                        pk=last_entry.pk
                    ).first()
                    if not locked_entry or AccessLog.objects.filter(paired_entry=locked_entry).exists():
                        results.append({'plate_number': plate, 'status': 'duplicate', 'allowed': False,
                                        'message': 'Duplicate scan — already processed.', 'bbox': bbox})
                        continue
                    exit_log = AccessLog.objects.create(
                        plate_number=plate,
                        vehicle=vehicle,
                        status=AccessLog.Status.EXITED,
                        gate_id=gate_id,
                        scanned_by=request.user,
                        paired_entry=locked_entry,
                    )

                delta = exit_log.scanned_at - last_entry.scanned_at
                duration_minutes = int(delta.total_seconds() / 60)

                owner_name = vehicle.user.full_name if vehicle.user else 'Unknown'
                guard_name = request.user.full_name
                _audit(request, AuditLog.Action.VEHICLE_EXITED,
                       f"Auto-exit (re-scan) | Plate: {plate} | Owner: {owner_name} | "
                       f"Duration: {duration_minutes} min | Gate: {_gate_label(gate_id)} | Guard: {guard_name}")

                resp = {
                    'plate_number':    plate,
                    'status':          'exited',
                    'allowed':         False,
                    'message':         f'{owner_name} — Exit recorded. Duration: {duration_minutes} min.',
                    'vehicle':         VehicleSerializer(vehicle).data,
                    'already_inside':  False,
                    'organizer_event': get_organizer_event(plate),
                    'duration_minutes': duration_minutes,
                    'bbox':            bbox,
                }
                if ml_sample:
                    resp['sample_id'] = ml_sample['sample_id']
                    resp['ml_confidence'] = ml_sample['confidence']
                results.append(resp)
                continue

            if _in_exit_cooldown(plate):
                resp = {
                    'plate_number':   plate,
                    'status':         'duplicate',
                    'allowed':        False,
                    'message':        'Exit cooldown — entry suppressed for 1 minute after exit.',
                    'vehicle':        VehicleSerializer(vehicle).data,
                    'already_inside': False,
                    'bbox':           bbox,
                }
                if ml_sample:
                    resp['sample_id'] = ml_sample['sample_id']
                results.append(resp)
                continue

            entry = check_entry(vehicle)
            has_violations = Violation.objects.filter(vehicle=vehicle, is_resolved=False).exists()
            already_inside = _already_inside(plate)

            AccessLog.objects.create(
                plate_number  = plate,
                vehicle       = vehicle,
                status        = _log_status(entry),
                denied_reason = '' if entry['allowed'] else entry['message'],
                gate_id       = gate_id,
                scanned_by    = request.user,
                snapshot      = request.FILES.get('image'),
            )

            owner_name = vehicle.user.full_name if vehicle.user else 'Unknown'
            guard_name = request.user.full_name
            if entry['allowed']:
                action  = AuditLog.Action.VEHICLE_ENTERED
                details = (
                    f"Plate: {plate} | Owner: {owner_name} | "
                    f"Vehicle: {vehicle.vehicle_type or 'N/A'} | Gate: {_gate_label(gate_id)} | Guard: {guard_name}"
                )
            else:
                action  = AuditLog.Action.SCAN
                details = (
                    f"Plate: {plate} | Owner: {owner_name} | "
                    f"Status: {entry['status']} | Reason: {entry['message']} | Gate: {_gate_label(gate_id)} | Guard: {guard_name}"
                )
            _audit(request, action, details)

            # 'no_pass'/'unknown' mean a visitor awaiting a pass — not a violation
            if not entry['allowed'] and entry['status'] not in ('no_pass', 'unknown'):
                _auto_log_violation(vehicle, entry['message'], gate_id)

            resp = {
                'plate_number':    plate,
                'status':          entry['status'],
                'allowed':         entry['allowed'],
                'message':         entry['message'],
                'constraint':      entry.get('constraint'),
                'vehicle':         VehicleSerializer(vehicle).data,
                'has_violations':  has_violations,
                'already_inside':  already_inside,
                'organizer_event': get_organizer_event(plate),
                'bbox':            bbox,
            }
            if ml_sample:
                resp['sample_id'] = ml_sample['sample_id']
                resp['ml_confidence'] = ml_sample['confidence']
            results.append(resp)

        return Response({'results': results})



class VisitorPassView(APIView):
    """Guard issues a visitor pass at the gate."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        """
        Create a visitor pass and return its data for thermal printing.
        Accepts plate_number directly; finds or creates the Vehicle record.
        """
        plate_number = (request.data.get('plate_number') or '').strip().upper().replace(' ', '')
        if not plate_number:
            return Response({'error': 'plate_number is required.'}, status=400)

        vehicle, vehicle_created = Vehicle.objects.get_or_create(
            plate_number=plate_number,
            defaults={'vehicle_type': 'car', 'is_authorized': False},
        )
        # A visitor pass for an unregistered plate auto-creates a Vehicle record —
        # surface that creation in the audit trail like any other vehicle add.
        if vehicle_created:
            _audit(
                request,
                AuditLog.Action.RECORD_CREATED,
                f"Vehicle added | {plate_number} (visitor) | By: {request.user.full_name}",
            )

        office_id = request.data.get('office')
        office = None
        if office_id:
            from .models import Office as OfficeModel
            office = OfficeModel.objects.filter(pk=office_id).first()

        try:
            allowed_duration = max(1, int(request.data.get('allowed_duration', 60)))
        except (TypeError, ValueError):
            allowed_duration = 60

        now = timezone.now()
        pass_ = VisitorPass.objects.create(
            vehicle=vehicle,
            plate_number=plate_number,
            office=office,
            purpose=request.data.get('purpose', ''),
            issued_by=request.user,
            valid_date=timezone.localdate(),
            allowed_duration=allowed_duration,
            expires_at=now + timedelta(minutes=allowed_duration),
        )

        # NOTE: the visitor's entry is NOT logged here. The AccessLog entry is
        # only created once the slip is confirmed printed (VisitorPassPrintedView)
        # — a visitor without a printed slip is not considered inside.
        gate_id = (request.data.get('gate_id')
                   or getattr(request.user, 'gate_assignment', None)
                   or 'main')

        guard_name  = request.user.full_name
        office_name = office.name if office else 'N/A'
        _audit(
            request,
            AuditLog.Action.VISITOR_ISSUED,
            f"Visitor pass issued | Plate: {plate_number} | "
            f"Purpose: {pass_.purpose or 'N/A'} | Office: {office_name} | "
            f"Duration: {allowed_duration} min | Gate: {_gate_label(gate_id)} | Guard: {guard_name}",
        )

        return Response(VisitorPassSerializer(pass_).data, status=201)

    def get(self, request):
        """List today's visitor passes."""
        passes = VisitorPass.objects.filter(
            valid_date=timezone.localdate()
        ).select_related('vehicle', 'office', 'issued_by')
        return Response(VisitorPassSerializer(passes, many=True).data)


class VisitorPassPrintedView(APIView):
    """Confirm the visitor slip was printed. Only at this point is the visitor's
    entry recorded in the AccessLog — a visitor whose slip was never printed is
    not considered inside. Idempotent: re-confirming does nothing."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        pass_ = get_object_or_404(VisitorPass, pk=pk)
        if pass_.printed_at:
            return Response(VisitorPassSerializer(pass_).data)

        pass_.printed_at = timezone.now()
        pass_.save(update_fields=['printed_at'])

        gate_id = (request.data.get('gate_id')
                   or getattr(request.user, 'gate_assignment', None)
                   or 'main')
        AccessLog.objects.create(
            plate_number=pass_.plate_number,
            vehicle=pass_.vehicle,
            status=AccessLog.Status.AUTHORIZED,
            gate_id=gate_id,
            scanned_by=request.user,
        )
        _audit(
            request,
            AuditLog.Action.VISITOR_ISSUED,
            f"Visitor slip printed — entry logged | Plate: {pass_.plate_number} | "
            f"Gate: {_gate_label(gate_id)} | Guard: {request.user.full_name}",
        )
        return Response(VisitorPassSerializer(pass_).data)


def _record_visitor_exit(request, pass_, gate_id):
    """Shared exit logic for slip-QR scans. Marks the pass exited, logs the
    exit AccessLog, and issues an overstay violation when applicable."""
    now = timezone.now()
    pass_.status    = VisitorPass.Status.EXITED
    pass_.exited_at = now
    pass_.save(update_fields=['status', 'exited_at'])

    AccessLog.objects.create(
        vehicle=pass_.vehicle,
        plate_number=pass_.plate_number,
        status=AccessLog.Status.EXITED,
        gate_id=gate_id,
        scanned_by=request.user,
    )

    duration_minutes = int((now - pass_.entered_at).total_seconds() / 60)
    overstay_minutes = (int((now - pass_.expires_at).total_seconds() / 60)
                        if pass_.expires_at and now > pass_.expires_at else 0)
    if overstay_minutes:
        try:
            _auto_log_violation(
                pass_.vehicle,
                f'Visitor overstay: exceeded allowed {pass_.allowed_duration} min by {overstay_minutes} min',
                gate_id,
                vtype=Violation.Type.TIME_EXCEED,
            )
        except Exception:
            pass
    _audit(
        request,
        AuditLog.Action.VISITOR_EXITED,
        f"Visitor exited (slip QR) | Plate: {pass_.plate_number} | "
        f"Duration: {duration_minutes} min | "
        + (f"OVERSTAYED by {overstay_minutes} min | " if overstay_minutes else "")
        + f"Gate: {_gate_label(gate_id)} | Guard: {request.user.full_name}",
    )
    return duration_minutes, overstay_minutes


class ExitScanView(APIView):
    """
    Guard scans the QR code on the returned thermal pass to record exit.
    The QR encodes the visitor pass ID.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        pass_ = get_object_or_404(VisitorPass, pk=pk)

        if pass_.status != VisitorPass.Status.ACTIVE:
            return Response(
                {'error': f'Pass is already marked as {pass_.status}.'},
                status=400,
            )

        gate_id = request.data.get('gate_id', 'main')
        _record_visitor_exit(request, pass_, gate_id)
        return Response(VisitorPassSerializer(pass_).data)


class VisitorQrExitView(APIView):
    """Record a visitor exit by scanning the QR on the printed slip.
    QR payload: SLC-VISITOR:{pass_id}. Visitor exits are recorded only through
    this scan — plate-based exit is refused for vehicles on an active pass."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        qr_data = (request.data.get('qr_data') or '').strip()
        if not qr_data.startswith('SLC-VISITOR:'):
            return Response({'error': 'Not a visitor slip QR.'}, status=400)
        try:
            pk = int(qr_data.split(':', 1)[1])
        except (ValueError, IndexError):
            return Response({'error': 'Malformed visitor slip QR.'}, status=400)

        pass_ = VisitorPass.objects.filter(pk=pk).select_related('vehicle', 'office').first()
        if not pass_:
            return Response({'error': 'Visitor pass not found.'}, status=404)
        if pass_.status != VisitorPass.Status.ACTIVE:
            return Response({'error': f'Pass is already marked as {pass_.status}.'}, status=400)

        gate_id = (request.data.get('gate_id')
                   or getattr(request.user, 'gate_assignment', None)
                   or 'main')
        duration_minutes, overstay_minutes = _record_visitor_exit(request, pass_, gate_id)
        data = VisitorPassSerializer(pass_).data
        data['duration_minutes'] = duration_minutes
        data['overstay_minutes'] = overstay_minutes
        return Response(data)


class OfficeListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        offices = Office.objects.all()
        return Response(OfficeSerializer(offices, many=True).data)


def _gate_dict(g):
    return {'id': g.id, 'gate_id': g.gate_id, 'label': g.label, 'is_active': g.is_active}


class GateListView(APIView):
    """List gates. Public (the guard gate-login kiosk needs it pre-auth) —
    returns active gates only unless an admin/CDSO asks for ?all=1.
    POST (admin/CDSO): create a new gate for school expansion."""
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        from .models import Gate
        qs = Gate.objects.all()
        is_staff = request.user.is_authenticated and getattr(request.user, 'role', '') == 'admin'
        if not (is_staff and request.query_params.get('all')):
            qs = qs.filter(is_active=True)
        return Response([_gate_dict(g) for g in qs])

    def post(self, request):
        from .models import Gate
        import re as _re
        if not (request.user.is_authenticated and getattr(request.user, 'role', '') == 'admin'):
            return Response({'error': 'Not authorised.'}, status=status.HTTP_403_FORBIDDEN)

        gate_id = (request.data.get('gate_id') or '').strip().lower()
        label   = (request.data.get('label') or '').strip()
        if not _re.fullmatch(r'gate\d{1,3}', gate_id):
            return Response({'error': "Gate ID must look like 'gate2', 'gate5', etc."}, status=status.HTTP_400_BAD_REQUEST)
        if not label:
            return Response({'error': 'A display label is required (e.g. "Gate 2 — North Entrance").'}, status=status.HTTP_400_BAD_REQUEST)
        if Gate.objects.filter(gate_id=gate_id).exists():
            return Response({'error': f'{gate_id} already exists.'}, status=status.HTTP_400_BAD_REQUEST)

        gate = Gate.objects.create(gate_id=gate_id, label=label)
        AuditLog.objects.create(
            actor=request.user, action=AuditLog.Action.RECORD_UPDATED,
            details=f'Gate created: {gate.label} ({gate.gate_id})',
        )
        return Response(_gate_dict(gate), status=status.HTTP_201_CREATED)


class GateDetailView(APIView):
    """Admin/CDSO: rename a gate or toggle it active/inactive."""
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, pk):
        from .models import Gate
        if getattr(request.user, 'role', '') != 'admin':
            return Response({'error': 'Not authorised.'}, status=status.HTTP_403_FORBIDDEN)
        try:
            gate = Gate.objects.get(pk=pk)
        except Gate.DoesNotExist:
            return Response({'error': 'Gate not found.'}, status=status.HTTP_404_NOT_FOUND)

        label = (request.data.get('label') or '').strip()
        if label:
            gate.label = label
        if 'is_active' in request.data:
            gate.is_active = bool(request.data.get('is_active'))
        gate.save(update_fields=['label', 'is_active'])
        AuditLog.objects.create(
            actor=request.user, action=AuditLog.Action.RECORD_UPDATED,
            details=f'Gate updated: {gate.label} ({gate.gate_id}) — active={gate.is_active}',
        )
        return Response(_gate_dict(gate))


class AccessLogListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qs = (
            AccessLog.objects
            .select_related('scanned_by', 'on_duty_guard', 'vehicle__user')
            .order_by('-scanned_at')
        )
        gate_id = request.query_params.get('gate_id')
        if gate_id:
            qs = qs.filter(gate_id=gate_id)
        date = request.query_params.get('date')
        if date:
            try:
                qs = qs.filter(scanned_at__date=date)
            except Exception:
                pass  # ignore malformed dates rather than 500
        limit = int(request.query_params.get('limit', 200))
        logs = qs[:limit]
        return Response(AccessLogSerializer(logs, many=True).data)


class MLTrainingSampleList(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        samples = MLTrainingSample.objects.all().order_by('-created_at')[:100]
        return Response(MLTrainingSampleSerializer(samples, many=True).data)


class MLTrainingSampleReview(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, pk):
        try:
            sample = MLTrainingSample.objects.get(pk=pk)
        except MLTrainingSample.DoesNotExist:
            return Response({'error': 'Sample not found'}, status=404)

        plate_number = request.data.get('plate_number')
        if plate_number is not None:
            sample.plate_number = plate_number
        action = request.data.get('action', sample.status)
        valid = dict(MLTrainingSample.STATUS_CHOICES).keys()
        if action not in valid and action not in ('approve', 'reject', 'mark_used'):
            return Response({'error': f'Invalid action. Must be one of {valid}'}, status=400)
        if action == 'approve':
            sample.status = MLTrainingSample.STATUS_CHOICES[2][0]  # 'verified'
        elif action == 'reject' or action == 'mark_used':
            sample.status = MLTrainingSample.STATUS_CHOICES[3][0]  # 'rejected'
        elif action == 'mark_used':
            sample.used_in_training = True
        else:
            sample.status = action
        sample.save()
        return Response(MLTrainingSampleSerializer(sample).data)


class TriggerRetrainView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        from scanning.tasks import ml_retrain_task
        task = ml_retrain_task.delay()
        return Response({
            'status': 'enqueued',
            'task_id': task.id,
            'message': 'Retrain task has been queued.',
        })


class MLStatsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        total      = MLTrainingSample.objects.count()
        unlabeled  = MLTrainingSample.objects.filter(status='unlabeled').count()
        auto_labeled = MLTrainingSample.objects.filter(status='auto_labeled').count()
        verified   = MLTrainingSample.objects.filter(status='verified').count()
        rejected   = MLTrainingSample.objects.filter(status='rejected').count()
        pending_train = MLTrainingSample.objects.filter(used_in_training=False).count()
        return Response({
            'total_samples': total,
            'unlabeled':     unlabeled,
            'auto_labeled':  auto_labeled,
            'verified':      verified,
            'rejected':      rejected,
            'pending_train': pending_train,
        })


class OverrideEntryView(APIView):
    """Guard overrides a denial and grants entry with a logged reason."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        plate_number = (request.data.get('plate_number') or '').strip().upper().replace(' ', '')
        reason       = (request.data.get('reason') or '').strip()

        if not plate_number:
            return Response({'error': 'plate_number is required.'}, status=400)
        if not reason:
            return Response({'error': 'reason is required.'}, status=400)

        vehicle = Vehicle.objects.filter(plate_number=plate_number).first()

        gate_id = getattr(request.user, 'gate_assignment', None) or 'main'
        AccessLog.objects.create(
            plate_number    = plate_number,
            vehicle         = vehicle,
            status          = AccessLog.Status.AUTHORIZED,
            is_override     = True,
            override_reason = reason,
            gate_id         = gate_id,
            scanned_by      = request.user,
        )

        guard_name = request.user.full_name
        owner_name = vehicle.user.full_name if vehicle and vehicle.user else 'Unregistered'
        _audit(
            request,
            AuditLog.Action.ENTRY_OVERRIDE,
            f"Entry override | Plate: {plate_number} | Owner: {owner_name} | "
            f"Reason: {reason} | Gate: {_gate_label(gate_id)} | Guard: {guard_name}",
        )

        return Response({'status': 'overridden', 'plate_number': plate_number})


class DenyEntryView(APIView):
    """Guard explicitly denies a visitor/unregistered plate at the gate.
    Logs a DENIED access row with the reason and an audit entry — no violation
    is issued (turning away a visitor is not an offense)."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        plate_number = (request.data.get('plate_number') or '').strip().upper().replace(' ', '')
        if not plate_number:
            return Response({'error': 'plate_number is required.'}, status=400)

        reason = (request.data.get('reason') or '').strip() or 'Entry denied at gate by guard.'

        vehicle = Vehicle.objects.filter(plate_number=plate_number).first()
        gate_id = getattr(request.user, 'gate_assignment', None) or 'main'
        AccessLog.objects.create(
            plate_number  = plate_number,
            vehicle       = vehicle,
            status        = AccessLog.Status.DENIED,
            denied_reason = reason,
            gate_id       = gate_id,
            scanned_by    = request.user,
        )

        guard_name = request.user.full_name
        owner_name = vehicle.user.full_name if vehicle and vehicle.user else 'Unregistered'
        _audit(
            request,
            AuditLog.Action.SCAN,
            f"Entry denied by guard | Plate: {plate_number} | Owner: {owner_name} | "
            f"Reason: {reason} | Gate: {_gate_label(gate_id)} | Guard: {guard_name}",
        )

        return Response({
            'plate_number': plate_number,
            'status':       'denied',
            'allowed':      False,
            'message':      f'Entry denied by guard. {reason}',
            'gate_id':      gate_id,
        })


class ExitLogView(APIView):
    """Guard records a vehicle exit and auto-pairs it to the matching entry."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        plate_number = (request.data.get('plate_number') or '').strip().upper().replace(' ', '')
        if not plate_number:
            return Response({'error': 'plate_number is required.'}, status=400)

        if not is_valid_ph_plate(plate_number):
            return Response({'error': 'Invalid plate format. Enter a valid Philippine plate number.'}, status=400)

        # Visitor vehicles exit by scanning the printed slip QR, never by plate.
        if _active_visitor_pass(plate_number):
            return Response({
                'error': 'This vehicle is on an active visitor pass. '
                         'Scan the QR on the printed visitor slip to record the exit.',
                'visitor_pass_required': True,
            }, status=409)

        vehicle = Vehicle.objects.filter(plate_number=plate_number).first()

        gate_id = getattr(request.user, 'gate_assignment', None) or 'main'
        exit_log = AccessLog.objects.create(
            plate_number = plate_number,
            vehicle      = vehicle,
            status       = AccessLog.Status.EXITED,
            gate_id      = gate_id,
            scanned_by   = request.user,
        )

        _pair_entry_exit(exit_log)
        overstay_minutes = _close_active_pass(plate_number, gate_id)

        duration_minutes = None
        entry_scanned_at = None
        if exit_log.paired_entry:
            delta            = exit_log.scanned_at - exit_log.paired_entry.scanned_at
            duration_minutes = int(delta.total_seconds() / 60)
            entry_scanned_at = exit_log.paired_entry.scanned_at

        # Stay-limit enforcement (fetcher / supplier rules)
        if duration_minutes is not None:
            if vehicle and vehicle.user and vehicle.user.owner_type == 'fetcher' and not _is_standby_fetcher(vehicle.user):
                overstay_minutes = max(overstay_minutes, _check_stay_limit(
                    plate_number, vehicle, 'fetcher', duration_minutes, gate_id))
            elif SupplierPlate.objects.filter(plate_number=plate_number, supplier__is_active=True).exists():
                overstay_minutes = max(overstay_minutes, _check_stay_limit(
                    plate_number, vehicle, 'supplier', duration_minutes, gate_id))

        owner_name = vehicle.user.full_name if vehicle and vehicle.user else 'Unknown'
        guard_name = request.user.full_name
        _audit(
            request,
            AuditLog.Action.VEHICLE_EXITED,
            f"Vehicle exited | Plate: {plate_number} | Owner: {owner_name} | "
            f"Duration: {duration_minutes if duration_minutes is not None else 'N/A'} min | "
            + (f"OVERSTAYED by {overstay_minutes} min | " if overstay_minutes else "")
            + f"Gate: {_gate_label(gate_id)} | Guard: {guard_name}",
        )

        return Response({
            'plate_number':    plate_number,
            'status':          'exited',
            'duration_minutes': duration_minutes,
            'overstay_minutes': overstay_minutes,
            'entry_scanned_at': entry_scanned_at,
            'scanned_at':      exit_log.scanned_at,
        })


class GuardMonitorView(APIView):
    """Admin-only: per-gate activity, current shifts, and cross-gate discrepancies."""

    def get(self, request):
        if not request.user.is_authenticated or request.user.role != 'admin':
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()

        from django.db.models import Count, Q
        from accounts.models import User as UserModel

        today  = timezone.localdate()
        guards = UserModel.objects.filter(role='security').order_by('full_name')

        # Current active shifts keyed by gate
        active_shifts = {}
        for shift in GuardShift.objects.filter(clocked_out_at__isnull=True).select_related('guard'):
            active_shifts[shift.gate] = {
                'guard_name':    shift.guard.full_name,
                'guard_code':    shift.guard.user_code,
                'clocked_in_at': shift.clocked_in_at,
                'shift_id':      shift.id,
            }

        result = []
        for guard in guards:
            today_logs = AccessLog.objects.filter(scanned_by=guard, scanned_at__date=today)

            stats = today_logs.aggregate(
                total      = Count('id'),
                authorized = Count('id', filter=Q(status=AccessLog.Status.AUTHORIZED)),
                denied     = Count('id', filter=Q(status__in=[
                    AccessLog.Status.DENIED, AccessLog.Status.WRONG_DAY, AccessLog.Status.UNKNOWN,
                ])),
                exited     = Count('id', filter=Q(status=AccessLog.Status.EXITED)),
            )

            visitors = VisitorPass.objects.filter(issued_by=guard, valid_date=today).count()

            recent = today_logs.select_related('vehicle__user').order_by('-scanned_at')[:10]
            last_log  = recent.first()
            last_seen = last_log.scanned_at if last_log else None

            photo_url = request.build_absolute_uri(guard.photo.url) if guard.photo else None

            # Shift history for today
            shifts_today = GuardShift.objects.filter(
                guard=guard, clocked_in_at__date=today,
            ).values('id', 'gate', 'clocked_in_at', 'clocked_out_at')

            result.append({
                'id':              guard.id,
                'full_name':       guard.full_name,
                'user_code':       guard.user_code,
                'photo_url':       photo_url,
                'gate_assignment': guard.gate_assignment,
                'last_seen':       last_seen,
                'is_active':       last_seen is not None,
                'stats': {
                    'total':      stats['total']      or 0,
                    'authorized': stats['authorized'] or 0,
                    'denied':     stats['denied']     or 0,
                    'exited':     stats['exited']     or 0,
                    'visitors':   visitors,
                },
                'recent_logs': AccessLogSerializer(recent, many=True).data,
                'shifts_today': list(shifts_today),
            })

        result.sort(key=lambda g: (not g['is_active'], g['full_name']))

        # Cross-gate discrepancies: vehicle entered one gate, exited a different gate today
        cross_gate = []
        exit_logs = (
            AccessLog.objects
            .filter(status=AccessLog.Status.EXITED, scanned_at__date=today, paired_entry__isnull=False)
            .select_related('paired_entry', 'vehicle__user')
        )
        for ex_log in exit_logs:
            entry = ex_log.paired_entry
            if entry and entry.gate_id != ex_log.gate_id and entry.gate_id and ex_log.gate_id:
                cross_gate.append({
                    'plate_number':  ex_log.plate_number,
                    'owner_name':    ex_log.vehicle.user.full_name if ex_log.vehicle and ex_log.vehicle.user else '—',
                    'entry_gate':    entry.gate_id,
                    'exit_gate':     ex_log.gate_id,
                    'entered_at':    entry.scanned_at,
                    'exited_at':     ex_log.scanned_at,
                })

        return Response({
            'guards':          result,
            'active_shifts':   active_shifts,
            'cross_gate_flags': cross_gate,
        })


class ExtendVisitorPassView(APIView):
    """Guard extends the allowed time for an active visitor pass."""
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, pk):
        pass_ = get_object_or_404(VisitorPass, pk=pk)

        if pass_.status != VisitorPass.Status.ACTIVE:
            return Response({'error': f'Cannot extend — pass is already {pass_.status}.'}, status=400)

        try:
            extra_minutes = int(request.data.get('extra_minutes', 0))
            if extra_minutes <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return Response({'error': 'extra_minutes must be a positive integer.'}, status=400)

        pass_.allowed_duration += extra_minutes
        if pass_.expires_at:
            pass_.expires_at += timedelta(minutes=extra_minutes)
        pass_.save(update_fields=['allowed_duration', 'expires_at'])

        guard_name = request.user.full_name
        _audit(
            request,
            AuditLog.Action.VISITOR_ISSUED,
            f"Visitor pass extended | Plate: {pass_.plate_number} | "
            f"+{extra_minutes} min | New total: {pass_.allowed_duration} min | Guard: {guard_name}",
        )

        return Response(VisitorPassSerializer(pass_).data)


class TestRtspView(APIView):
    """Quick probe: tries to open an RTSP URL and read one frame, returns ok/message."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        import cv2, os, concurrent.futures
        rtsp_url = (request.data.get('rtsp_url') or '').strip()
        if not rtsp_url.lower().startswith('rtsp://'):
            return Response({'ok': False, 'message': 'URL must start with rtsp://'}, status=400)

        def _probe():
            os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = (
                'rtsp_transport;tcp|buffer_size;0|max_delay;0|stimeout;5000000'
            )
            cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                cap.release()
                return False, 'Cannot connect — verify the URL, credentials, and that the camera is on the same network as this server.'
            ret, _ = cap.read()
            cap.release()
            if ret:
                return True, 'Camera connected and streaming successfully.'
            return False, 'Reached the camera but received no frames — check stream path or encoding settings.'

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(_probe)
            try:
                ok, msg = future.result(timeout=15)
            except concurrent.futures.TimeoutError:
                ok, msg = False, 'Connection timed out (15 s) — camera is unreachable from this server.'
            except Exception as e:
                ok, msg = False, f'Error: {e}'

        return Response({'ok': ok, 'message': msg})


# ─── Dual-Gate System Views ───────────────────────────────────────────────────

class ManualEntryView(APIView):
    """Guard manually types a plate number — no image scan required."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        plate_number = (request.data.get('plate_number') or '').strip().upper().replace(' ', '')
        if not plate_number:
            return Response({'error': 'plate_number is required.'}, status=400)

        if not is_valid_ph_plate(plate_number):
            return Response({'error': 'Invalid plate format. Enter a valid Philippine plate number.'}, status=400)

        gate_id = getattr(request.user, 'gate_assignment', None) or 'main'
        vehicle = Vehicle.objects.select_related('user').filter(plate_number=plate_number).first()

        if not vehicle:
            supplier_plate = SupplierPlate.objects.select_related('supplier').filter(
                plate_number=plate_number, supplier__is_active=True
            ).first()

            if not supplier_plate:
                if is_open_campus():
                    r = _open_campus_unknown_result(plate_number, gate_id, request.user)
                    _audit_manual_result(request, plate_number, 'Unregistered', gate_id, r)
                    return Response({**r, 'plate_number': plate_number, 'gate_id': gate_id})
                AccessLog.objects.create(
                    plate_number=plate_number,
                    status=AccessLog.Status.UNKNOWN,
                    gate_id=gate_id,
                    scanned_by=request.user,
                )
                _audit(request, AuditLog.Action.SCAN,
                       f"Plate: {plate_number} | Owner: Unknown | Gate: {_gate_label(gate_id)} | "
                       f"Guard: {request.user.full_name} | Status: unknown")
                return Response({
                    'plate_number': plate_number,
                    'status':       'unknown',
                    'allowed':      False,
                    'message':      'Plate not found in the system.',
                    'gate_id':      gate_id,
                })

            supplier_name = supplier_plate.supplier.company_name
            inside_status, last_entry = _inside_state(plate_number)

            if inside_status == 'duplicate':
                return Response({
                    'plate_number':   plate_number,
                    'status':         'duplicate',
                    'allowed':        False,
                    'message':        'Duplicate scan — already processed within grace period.',
                    'is_supplier':    True,
                    'supplier_name':  supplier_name,
                    'already_inside': True,
                    'gate_id':        gate_id,
                })

            if inside_status == 'inside':
                # Single check action toggles state like the camera: within the
                # breathing window a re-check is informational; past it, it records the exit
                seconds_inside = (timezone.now() - last_entry.scanned_at).total_seconds()
                if seconds_inside < ENTRY_BREATHING_SECONDS:
                    window_left = int(ENTRY_BREATHING_SECONDS - seconds_inside)
                    return Response({
                        'plate_number':        plate_number,
                        'status':              'already_inside',
                        'allowed':             False,
                        'message':             f'Supplier vehicle — {supplier_name} just entered. '
                                               f'Re-check in {window_left}s to record an exit.',
                        'is_supplier':         True,
                        'supplier_name':       supplier_name,
                        'already_inside':      True,
                        'retry_after_seconds': window_left,
                        'gate_id':             gate_id,
                    })
                from django.db import transaction as _tx
                with _tx.atomic():
                    locked_entry = AccessLog.objects.select_for_update().filter(pk=last_entry.pk).first()
                    if not locked_entry or AccessLog.objects.filter(paired_entry=locked_entry).exists():
                        return Response({
                            'plate_number':   plate_number,
                            'status':         'duplicate',
                            'allowed':        False,
                            'message':        'Duplicate scan — already processed.',
                            'is_supplier':    True,
                            'supplier_name':  supplier_name,
                            'already_inside': False,
                            'gate_id':        gate_id,
                        })
                    exit_log = AccessLog.objects.create(
                        plate_number=plate_number,
                        status=AccessLog.Status.EXITED,
                        gate_id=gate_id,
                        scanned_by=request.user,
                        paired_entry=locked_entry,
                    )
                duration_minutes = int((exit_log.scanned_at - last_entry.scanned_at).total_seconds() / 60)
                overstay_minutes = _check_stay_limit(plate_number, None, 'supplier', duration_minutes, gate_id)
                overstay_note = f' Overstayed by {overstay_minutes} min — violation issued.' if overstay_minutes else ''
                _audit(request, AuditLog.Action.VEHICLE_EXITED,
                       f"Exit (re-check) | Plate: {plate_number} | Supplier: {supplier_name} | "
                       f"Duration: {duration_minutes} min | "
                       + (f"OVERSTAYED by {overstay_minutes} min | " if overstay_minutes else "")
                       + f"Gate: {_gate_label(gate_id)} | Guard: {request.user.full_name}")
                return Response({
                    'plate_number':      plate_number,
                    'status':            'exited',
                    'allowed':           False,
                    'message':           f'Supplier vehicle — {supplier_name}. Exit recorded. Duration: {duration_minutes} min.{overstay_note}',
                    'is_supplier':       True,
                    'supplier_name':     supplier_name,
                    'already_inside':    False,
                    'duration_minutes':  duration_minutes,
                    'overstay_minutes':  overstay_minutes,
                    'gate_id':           gate_id,
                })

            cooldown_left = _exit_cooldown_remaining(plate_number)
            if cooldown_left:
                return Response({
                    'plate_number':        plate_number,
                    'status':              'duplicate',
                    'allowed':             False,
                    'message':             f'Exit cooldown — entry suppressed for {cooldown_left}s more.',
                    'is_supplier':         True,
                    'supplier_name':       supplier_name,
                    'already_inside':      False,
                    'retry_after_seconds': cooldown_left,
                    'gate_id':             gate_id,
                })

            deny_msg = _supplier_rule_denial()
            if deny_msg:
                AccessLog.objects.create(
                    plate_number=plate_number, status=AccessLog.Status.DENIED,
                    denied_reason=deny_msg, gate_id=gate_id, scanned_by=request.user,
                )
                _audit(request, AuditLog.Action.SCAN,
                       f"Plate: {plate_number} | Supplier: {supplier_name} | Gate: {_gate_label(gate_id)} | "
                       f"Guard: {request.user.full_name} | Status: denied — {deny_msg}")
                return Response({
                    'plate_number':  plate_number,
                    'status':        'denied',
                    'allowed':       False,
                    'message':       deny_msg,
                    'is_supplier':   True,
                    'supplier_name': supplier_name,
                    'gate_id':       gate_id,
                })

            AccessLog.objects.create(
                plate_number=plate_number, status=AccessLog.Status.AUTHORIZED,
                gate_id=gate_id, scanned_by=request.user,
            )
            _audit(request, AuditLog.Action.VEHICLE_ENTERED,
                   f"Plate: {plate_number} | Supplier: {supplier_name} | Gate: {_gate_label(gate_id)} | "
                   f"Guard: {request.user.full_name} | Status: entered")
            open_campus = is_open_campus()
            return Response({
                'plate_number':  plate_number,
                'status':        'open_entry' if open_campus else 'authorized',
                'allowed':       True,
                'message':       (f'Open Campus Mode active — Supplier vehicle {supplier_name}. Open entry granted.'
                                  if open_campus else
                                  f'Supplier vehicle — {supplier_name}. Entry permitted.'),
                'is_supplier':   True,
                'supplier_name': supplier_name,
                'gate_id':       gate_id,
            })

        inside_status, last_entry = _inside_state(plate_number)

        if inside_status == 'duplicate':
            return Response({
                'plate_number':   plate_number,
                'status':         'duplicate',
                'allowed':        False,
                'message':        'Duplicate scan — already processed within grace period.',
                'vehicle':        VehicleSerializer(vehicle).data,
                'already_inside': True,
                'gate_id':        gate_id,
            })

        if inside_status == 'inside':
            # Single check action toggles state like the camera: within the
            # breathing window a re-check is informational; past it, it records the exit
            seconds_inside = (timezone.now() - last_entry.scanned_at).total_seconds()
            owner_name = vehicle.user.full_name if vehicle.user else 'Unknown'
            # Visitor vehicles exit by scanning the printed slip QR, never by plate.
            if _active_visitor_pass(plate_number):
                return Response({
                    'plate_number':   plate_number,
                    'status':         'visitor_pass_required',
                    'allowed':        False,
                    'message':        'Visitor is inside on an active pass. Scan the QR on the '
                                      'printed visitor slip to record the exit.',
                    'vehicle':        VehicleSerializer(vehicle).data,
                    'already_inside': True,
                    'gate_id':        gate_id,
                })
            if seconds_inside < ENTRY_BREATHING_SECONDS:
                window_left = int(ENTRY_BREATHING_SECONDS - seconds_inside)
                return Response({
                    'plate_number':        plate_number,
                    'status':              'already_inside',
                    'allowed':             False,
                    'message':             f'{owner_name} — vehicle just entered. '
                                           f'Re-check in {window_left}s to record an exit.',
                    'vehicle':             VehicleSerializer(vehicle).data,
                    'already_inside':      True,
                    'retry_after_seconds': window_left,
                    'gate_id':             gate_id,
                })
            from django.db import transaction as _tx
            with _tx.atomic():
                locked_entry = AccessLog.objects.select_for_update().filter(
                    pk=last_entry.pk
                ).first()
                if not locked_entry or AccessLog.objects.filter(paired_entry=locked_entry).exists():
                    return Response({
                        'plate_number':   plate_number,
                        'status':         'duplicate',
                        'allowed':        False,
                        'message':        'Duplicate scan — already processed.',
                        'vehicle':        VehicleSerializer(vehicle).data,
                        'already_inside': False,
                        'gate_id':        gate_id,
                    })
                exit_log = AccessLog.objects.create(
                    plate_number=plate_number,
                    vehicle=vehicle,
                    status=AccessLog.Status.EXITED,
                    gate_id=gate_id,
                    scanned_by=request.user,
                    paired_entry=locked_entry,
                )
            duration_minutes = int((exit_log.scanned_at - last_entry.scanned_at).total_seconds() / 60)
            overstay_minutes = _close_active_pass(plate_number, gate_id)
            if vehicle.user and vehicle.user.owner_type == 'fetcher' and not _is_standby_fetcher(vehicle.user):
                overstay_minutes = max(overstay_minutes, _check_stay_limit(
                    plate_number, vehicle, 'fetcher', duration_minutes, gate_id))
            overstay_note = f' Overstayed by {overstay_minutes} min.' if overstay_minutes else ''
            guard_name = request.user.full_name
            _audit(request, AuditLog.Action.VEHICLE_EXITED,
                   f"Exit (re-check) | Plate: {plate_number} | Owner: {owner_name} | "
                   f"Duration: {duration_minutes} min | "
                   + (f"OVERSTAYED by {overstay_minutes} min | " if overstay_minutes else "")
                   + f"Gate: {_gate_label(gate_id)} | Guard: {guard_name}")
            return Response({
                'plate_number':    plate_number,
                'status':          'exited',
                'allowed':         False,
                'message':         f'{owner_name} — Exit recorded. Duration: {duration_minutes} min.{overstay_note}',
                'vehicle':         VehicleSerializer(vehicle).data,
                'already_inside':  False,
                'duration_minutes': duration_minutes,
                'overstay_minutes': overstay_minutes,
                'gate_id':         gate_id,
            })

        cooldown_left = _exit_cooldown_remaining(plate_number)
        if cooldown_left:
            return Response({
                'plate_number':        plate_number,
                'status':              'duplicate',
                'allowed':             False,
                'message':             f'Exit cooldown — entry suppressed for {cooldown_left}s more.',
                'vehicle':             VehicleSerializer(vehicle).data,
                'already_inside':      False,
                'retry_after_seconds': cooldown_left,
                'gate_id':             gate_id,
            })

        entry = check_entry(vehicle)
        has_violations = Violation.objects.filter(vehicle=vehicle, is_resolved=False).exists()

        # UI-only statuses (e.g. 'no_pass', 'open_entry') aren't valid AccessLog statuses
        AccessLog.objects.create(
            plate_number  = plate_number,
            vehicle       = vehicle,
            status        = _log_status(entry),
            gate_id       = gate_id,
            denied_reason = '' if entry['allowed'] else entry['message'],
            scanned_by    = request.user,
        )

        owner_name = vehicle.user.full_name if vehicle.user else 'Unknown'
        guard_name = request.user.full_name
        action = AuditLog.Action.VEHICLE_ENTERED if entry['allowed'] else AuditLog.Action.SCAN
        _audit(
            request, action,
            f"Plate: {plate_number} | Owner: {owner_name} | Gate: {_gate_label(gate_id)} | Guard: {guard_name} | Status: {entry['status']}",
        )

        # 'no_pass'/'unknown' mean a visitor awaiting a pass — not a violation
        if not entry['allowed'] and entry['status'] not in ('no_pass', 'unknown'):
            _auto_log_violation(vehicle, entry['message'], gate_id)

        return Response({
            'plate_number':    plate_number,
            'status':          entry['status'],
            'allowed':         entry['allowed'],
            'message':         entry['message'],
            'constraint':      entry.get('constraint'),
            'vehicle':         VehicleSerializer(vehicle).data,
            'has_violations':  has_violations,
            'already_inside':  False,
            'organizer_event': get_organizer_event(plate_number),
            'gate_id':         gate_id,
        })


class QRLoginView(APIView):
    """
    Kiosk QR scan login — validates guard's QR token, ends any active shift
    at their assigned gate, starts a new shift, and returns JWT tokens.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        from rest_framework_simplejwt.tokens import RefreshToken
        from django.utils import timezone as tz
        from .models import Gate

        qr_token = (request.data.get('qr_token') or '').strip()
        if not qr_token:
            return Response({'error': 'qr_token is required.'}, status=400)

        try:
            guard = User.objects.get(qr_token=qr_token, role='security', is_active=True)
        except User.DoesNotExist:
            return Response({'error': 'Invalid or unrecognised QR code.'}, status=403)

        # Gate is selected by the guard at the login screen — that selection IS their assignment.
        gate = (request.data.get('gate') or '').strip() or guard.gate_assignment
        if gate not in Gate.active_ids():
            return Response({'error': 'Please select a valid gate before scanning.'}, status=400)

        # Persist the gate the guard logged in at on their profile.
        if guard.gate_assignment != gate:
            User.objects.filter(pk=guard.pk).update(gate_assignment=gate)
            guard.gate_assignment = gate

        now = tz.now()

        # End previous active shift at this gate (whoever was logged in before)
        GuardShift.objects.filter(gate=gate, clocked_out_at__isnull=True).update(
            clocked_out_at=now,
            clocked_out_by=guard,
        )

        # Start new shift — records exact gate and clock-in time
        GuardShift.objects.create(guard=guard, gate=gate)

        # Issue JWT
        refresh  = RefreshToken.for_user(guard)
        access   = str(refresh.access_token)

        # Audit
        _audit_ip = get_client_ip(request)
        gate_label = _gate_label(gate)
        try:
            AuditLog.objects.create(
                actor=guard,
                action=AuditLog.Action.SCAN,
                details=f"Guard shift login | {gate_label} | {guard.full_name}",
                ip_address=_audit_ip,
            )
        except Exception:
            pass

        return Response({
            'access':  access,
            'refresh': str(refresh),
            'user': {
                'id':              guard.id,
                'user_code':       guard.user_code,
                'full_name':       guard.full_name,
                'email':           guard.email,
                'role':            guard.role,
                'gate_assignment': gate,
                'must_change_password': guard.must_change_password,
            },
        })


class CurrentShiftsView(APIView):
    """Return the currently active shift for each gate."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        active = (
            GuardShift.objects
            .filter(clocked_out_at__isnull=True)
            .select_related('guard')
        )
        result = {}
        for shift in active:
            result[shift.gate] = {
                'guard_name':    shift.guard.full_name,
                'guard_code':    shift.guard.user_code,
                'gate':          shift.gate,
                'clocked_in_at': shift.clocked_in_at,
            }
        return Response(result)


class GuardShiftListView(APIView):
    """Admin: paginated full shift history."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if request.user.role != 'admin':
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()

        from .serializers import GuardShiftSerializer as GSSer
        gate   = request.query_params.get('gate', '').strip()
        guard  = request.query_params.get('guard', '').strip()
        date   = request.query_params.get('date', '').strip()

        qs = GuardShift.objects.select_related('guard', 'clocked_out_by').all()
        if gate:
            qs = qs.filter(gate=gate)
        if guard:
            qs = qs.filter(guard__id=guard)
        if date:
            qs = qs.filter(clocked_in_at__date=date)

        return Response(GSSer(qs[:100], many=True).data)