import logging
from datetime import datetime, timedelta

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser
from rest_framework import permissions, status
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.db.models import Q
from vehicles.models import Vehicle, SupplierPlate
from violations.models import Violation, NEW_STYLE_TYPES
from accounts.models import User, AuditLog
from accounts.views import IsAdminRole
from .models import AccessLog, VisitorPass, Office, MLTrainingSample, GuardShift
from .entry_logic import check_entry, get_organizer_event, is_open_campus
from .ml.reader import read_plate
from .ml.collector import record_scan
from .ml.validator import is_valid_ph_plate
from vehicles.serializers import VehicleSerializer
from .serializers import VisitorPassSerializer, OfficeSerializer, AccessLogSerializer, GuardShiftSerializer, MLTrainingSampleSerializer
from time_utils import day_range, filter_local_date_range

logger = logging.getLogger(__name__)


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
    day_start, day_end = day_range(timezone.localdate())
    last_entry = AccessLog.objects.filter(
        plate_number=plate_number,
        status=AccessLog.Status.AUTHORIZED,
        scanned_at__gte=day_start,
        scanned_at__lt=day_end,
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
    day_start, day_end = day_range(timezone.localdate())
    last_entry = AccessLog.objects.filter(
        plate_number=plate_number,
        status=AccessLog.Status.AUTHORIZED,
        scanned_at__gte=day_start,
        scanned_at__lt=day_end,
        scanned_at__lte=timezone.now(),
    ).order_by('-scanned_at').first()
    if not last_entry:
        return False
    return not AccessLog.objects.filter(paired_entry=last_entry).exists()


def _pair_entry_exit(exit_log) -> None:
    """Link exit_log to the most recent unpaired entry for the same plate today."""
    day_start, day_end = day_range(timezone.localdate())
    entry = AccessLog.objects.filter(
        plate_number=exit_log.plate_number,
        status=AccessLog.Status.AUTHORIZED,
        scanned_at__gte=day_start,
        scanned_at__lt=day_end,
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


def _request_image_bytes(request):
    """Bytes of the frame the guard's device posted with this scan, or None.

    Read non-destructively: the same upload is also saved as the AccessLog
    snapshot, and consuming the stream without rewinding leaves whichever
    consumer runs second with an empty file.
    """
    f = getattr(request, 'FILES', None) and request.FILES.get('image')
    if not f:
        return None
    try:
        pos = f.tell()
        f.seek(0)
        data = f.read()
        f.seek(pos)
        return data or None
    except Exception:
        return None


def _auto_log_violation(vehicle, message: str, gate_id: str = '', vtype: str = '',
                        evidence_bytes=None, entry_status: str = ''):
    """
    Auto-issue a violation at the gate — at most ONE violation of each type per
    vehicle per calendar day, no matter how often it is scanned or detected that
    day. A new day allows the type to be issued again.

    The per-day cap matters more than it used to: a confiscated account being
    detected is itself an offence, so without it a car sitting in front of a
    camera would climb the whole ladder in a minute.

    Past violations stay stored, and the cumulative (non-cleared) count per
    ACCOUNT drives the penalty — 1st offence costs a week of campus access, 2nd
    two weeks, 3rd the rest of the registration period.
    """
    from .models import active_guard_for_gate

    # Turning up at a gate while confiscated is its own offence, not another
    # "unauthorized entry" — the CDSO needs to see that the penalty was ignored
    # rather than that an unregistered car showed up.
    if not vtype and entry_status == 'confiscated':
        vtype = Violation.Type.CONFISCATED_ACTIVITY
    vtype = vtype or Violation.Type.UNAUTHORIZED_ENTRY

    _day_start, _day_end = day_range(timezone.localdate())
    owner = vehicle.user

    # ── One auto-logged offence per ACCOUNT per calendar day ─────────────────
    # The cap used to be per vehicle AND per type, which was right while each
    # type had its own ladder. Now that the ladder is one per account, a
    # per-type cap lets a single incident spend the whole ladder in seconds:
    # the first denied scan confiscates the account, and the very next scan is
    # "activity while confiscated" — a different type, so the old check waved it
    # through. Two scans of the same car became two strikes.
    #
    # One strike per day per account. A second incident tomorrow still counts.
    if owner is not None:
        if Violation.objects.filter(
            owner=owner,
            violation_type__in=NEW_STYLE_TYPES,
            issued_at__gte=_day_start,
            issued_at__lt=_day_end,
        ).exists():
            return
    else:
        # No account behind the plate (gate-issued vehicle) — fall back to the
        # per-vehicle, per-type cap, which is all that can be keyed on.
        dedup_types = [vtype]
        if vtype == Violation.Type.UNAUTHORIZED_ENTRY:
            dedup_types.append(Violation.Type.UNAUTHORIZED)  # legacy auto-logged rows
        if Violation.objects.filter(
            vehicle=vehicle,
            violation_type__in=dedup_types,
            issued_at__gte=_day_start,
            issued_at__lt=_day_end,
        ).exists():
            return

    offense_num  = Violation.compute_offense_number(owner)
    violation = Violation.objects.create(
        vehicle              = vehicle,
        owner                = owner,
        violation_type       = vtype,
        notes                = f'Auto-logged at gate: {message}',
        offense_number       = offense_num,
        status               = Violation.Status.WARNING,
        # Only the 3rd strike holds registration.
        registration_blocked = offense_num >= 3,
        is_released          = True,  # visible to the owner immediately
        on_duty_guard        = active_guard_for_gate(gate_id),
    )
    # Last resort: no frame was handed in, so take the newest one the gate
    # camera has. A violation with no photo is one nobody can contest or
    # confirm later, which is exactly what the lift flow needs to judge.
    if not evidence_bytes:
        try:
            from .gate_frames import latest_jpeg_for_gate
            evidence_bytes = latest_jpeg_for_gate(gate_id)
        except Exception:
            evidence_bytes = None

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
    # Impose the ladder, then tell the owner. Both are best-effort: the
    # violation itself is already recorded and must not be rolled back by a
    # mail server being down.
    try:
        from violations.penalty import apply_penalty, notify_owner
        penalty = apply_penalty(violation)
        notify_owner(violation, penalty)
    except Exception:
        logger.exception('Could not apply penalty for violation %s', violation.pk)


class ScanView(APIView):
    parser_classes     = [MultiPartParser]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        file = request.FILES.get('image')
        if not file:
            return Response({'error': 'No image provided'}, status=400)

        raw_bytes = file.read()
        plates = read_plate(raw_bytes)
        # Reuse the detections/OCR just computed — record_scan would otherwise
        # run the entire pipeline a second time on the same bytes.
        ml_sample = record_scan(raw_bytes, results=plates)

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

            vehicle = Vehicle.resolve(plate)

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

                deny_msg = _supplier_rule_denial()
                if deny_msg:
                    AccessLog.objects.create(
                        plate_number=plate, status=AccessLog.Status.DENIED,
                        denied_reason=deny_msg, gate_id=gate_id, scanned_by=request.user,
                    )
                    results.append({
                        'plate_number':  plate,
                        'status':        'denied',
                        'allowed':       False,
                        'message':       deny_msg,
                        'is_supplier':   True,
                        'supplier_name': supplier_name,
                        'bbox':          bbox,
                        'sample_id':     ml_sample.get("sample_id") if ml_sample else None,
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

            # 'no_pass'/'unknown' mean a visitor awaiting a pass — not a violation
            if not entry['allowed'] and entry['status'] not in ('no_pass', 'unknown'):
                _auto_log_violation(vehicle, entry['message'], gate_id,
                                    evidence_bytes=_request_image_bytes(request),
                                    entry_status=entry['status'])

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
                evidence_bytes=_request_image_bytes(request),
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


# -- Vehicle Log --------------------------------------------------------------
# The list screen (guard: one gate, one day — CDSO: all gates, a date range) and
# its two reports read through the same helpers below, so an exported report can
# never disagree with the table it was exported from.

# One UI choice covers several stored statuses, because that is how the question
# gets asked: "show me everything that was refused" is denied *and* wrong day.
ACCESS_LOG_STATUS_GROUPS = {
    'authorized': [AccessLog.Status.AUTHORIZED],
    'denied':     [AccessLog.Status.DENIED, AccessLog.Status.WRONG_DAY],
    'unknown':    [AccessLog.Status.UNKNOWN],
    'unreadable': [AccessLog.Status.UNREADABLE],
    'exited':     [AccessLog.Status.EXITED],
}


def _filter_access_logs(request):
    """Apply the filters the Vehicle Log screens use.

    Returns (ordered_queryset, filters_desc). `status` is deliberately NOT
    applied here — see _merge_access_log_visits: an exit row only folds into its
    entry while both are in the result set, so narrowing by status in SQL would
    strip the exit half of every visit. It is applied to the merged rows instead.
    """
    qs = (
        AccessLog.objects
        .select_related('scanned_by', 'on_duty_guard', 'vehicle__user')
        .order_by('-scanned_at')
    )
    filters_desc = []

    gate_id = (request.query_params.get('gate_id') or '').strip()
    if gate_id:
        qs = qs.filter(gate_id=gate_id)
        from .models import Gate
        label = Gate.objects.filter(gate_id=gate_id).values_list('label', flat=True).first()
        filters_desc.append(f'Gate: {label or gate_id}')

    date = (request.query_params.get('date') or '').strip()
    if date:
        try:
            _start, _end = day_range(datetime.strptime(date, '%Y-%m-%d').date())
            qs = qs.filter(scanned_at__gte=_start, scanned_at__lt=_end)
            filters_desc.append(f'Date: {date}')
        except Exception:
            pass  # ignore malformed dates rather than 500

    # Range form of the single-day filter above, for the CDSO's Vehicle Log.
    # Unparseable bounds are ignored, same as `date`.
    date_from = (request.query_params.get('date_from') or '').strip()
    date_to   = (request.query_params.get('date_to') or '').strip()
    qs = filter_local_date_range(qs, 'scanned_at', date_from, date_to)
    if date_from or date_to:
        filters_desc.append(f"Period: {date_from or 'start'} to {date_to or 'today'}")

    search = (request.query_params.get('search') or '').strip()
    if search:
        qs = qs.filter(
            Q(plate_number__icontains=search)
            | Q(vehicle__user__full_name__icontains=search)
            | Q(on_duty_guard__full_name__icontains=search)
            | Q(scanned_by__full_name__icontains=search)
        )
        filters_desc.append(f"Search: '{search}'")

    status_key = (request.query_params.get('status') or '').strip()
    if status_key in ACCESS_LOG_STATUS_GROUPS:
        labels = dict(AccessLog.Status.choices)
        filters_desc.append(
            'Status: ' + ', '.join(labels.get(v, v) for v in ACCESS_LOG_STATUS_GROUPS[status_key])
        )

    return qs, filters_desc


def _merge_access_log_visits(logs):
    """Fold each exit row into its paired entry row — one visit, one row.

    Returns (visible_logs, exit_by_entry_id). Pairing only happens when the
    entry is in `logs` too, so an exit whose entry fell outside the filter or
    the row cap still shows on its own rather than vanishing.
    """
    entries_by_id = {log.id: log for log in logs if log.status == AccessLog.Status.AUTHORIZED}
    exit_by_entry_id = {}
    for log in logs:
        if log.status == AccessLog.Status.EXITED and log.paired_entry_id in entries_by_id:
            exit_by_entry_id[log.paired_entry_id] = log

    merged_exit_ids = {exit_log.id for exit_log in exit_by_entry_id.values()}
    visible = [log for log in logs if log.id not in merged_exit_ids]
    return visible, exit_by_entry_id


def _visit_duration_minutes(entry_log, exit_log):
    return max(0, round((exit_log.scanned_at - entry_log.scanned_at).total_seconds() / 60))


def _apply_status_group(logs, status_key):
    """Narrow already-merged rows to one UI status group; unknown keys pass through."""
    wanted = ACCESS_LOG_STATUS_GROUPS.get(status_key)
    return [log for log in logs if log.status in wanted] if wanted else logs


class AccessLogListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qs, _ = _filter_access_logs(request)

        try:
            limit = int(request.query_params.get('limit', 200))
        except (TypeError, ValueError):
            limit = 200
        limit = max(1, min(limit, 1000))
        logs = list(qs[:limit])

        visible, exit_by_entry_id = _merge_access_log_visits(logs)
        entries_by_id = {log.id: log for log in logs}

        data = AccessLogSerializer(visible, many=True).data
        for row in data:
            exit_log = exit_by_entry_id.get(row['id'])
            if exit_log:
                row['exited_at'] = exit_log.scanned_at
                row['duration_minutes'] = _visit_duration_minutes(entries_by_id[row['id']], exit_log)
        return Response(data)


VEHICLE_LOG_REPORT_HEADERS = [
    '#', 'Date & Time', 'Plate', 'Owner', 'Type', 'Gate',
    'Status', 'Guard on Duty', 'Exit Time', 'Duration', 'Remarks',
]

# Rows are capped rather than streamed, the same way the audit report is: a year
# of scans is far more than anyone reads out of a PDF, and an unbounded export
# is a memory hazard on a shared dyno.
VEHICLE_LOG_REPORT_CAP = 5000


def _vehicle_log_report_rows(logs, exit_by_entry_id):
    from django.utils import timezone as tz
    from .models import Gate

    status_labels = dict(AccessLog.Status.choices)
    gate_labels = dict(Gate.objects.values_list('gate_id', 'label'))

    def duration_text(minutes):
        if minutes is None:
            return ''
        if minutes < 60:
            return f'{minutes} min'
        hours, mins = divmod(minutes, 60)
        return f'{hours}h {mins}m' if mins else f'{hours}h'

    rows = []
    for i, log in enumerate(logs, start=1):
        exit_log = exit_by_entry_id.get(log.id)
        minutes = _visit_duration_minutes(log, exit_log) if exit_log else None
        owner = getattr(getattr(log.vehicle, 'user', None), 'full_name', '') or 'Unregistered'

        remarks = []
        if log.is_override:
            remarks.append(f'Override: {log.override_reason}' if log.override_reason else 'Override')
        if log.denied_reason:
            remarks.append(log.denied_reason)
        if not exit_log and log.status == AccessLog.Status.AUTHORIZED:
            remarks.append('Still inside')

        rows.append([
            i,
            tz.localtime(log.scanned_at).strftime('%b %d, %Y %I:%M:%S %p'),
            log.plate_number or '',
            owner,
            (log.vehicle_type or '').title(),
            gate_labels.get(log.gate_id, log.gate_id or ''),
            status_labels.get(log.status, log.status),
            getattr(log.on_duty_guard, 'full_name', '') or '',
            tz.localtime(exit_log.scanned_at).strftime('%I:%M %p') if exit_log else '',
            duration_text(minutes),
            ' · '.join(remarks),
        ])
    return rows


def _vehicle_log_report_data(request):
    """(rows, filters_desc) for both formats — one filter path, one merge."""
    qs, filters_desc = _filter_access_logs(request)
    logs = list(qs[:VEHICLE_LOG_REPORT_CAP])
    visible, exit_by_entry_id = _merge_access_log_visits(logs)
    visible = _apply_status_group(visible, (request.query_params.get('status') or '').strip())
    return _vehicle_log_report_rows(visible, exit_by_entry_id), filters_desc


class VehicleLogExportView(APIView):
    """Download the (filtered) vehicle log as an Excel report — CDSO only."""
    permission_classes = [IsAdminRole]

    def get(self, request):
        from django.utils import timezone as tz
        from report_utils import branded_excel_response, report_filename
        rows, filters_desc = _vehicle_log_report_data(request)
        subtitle = (f"Generated {tz.localtime().strftime('%B %d, %Y %I:%M %p')} "
                    f"by {getattr(request.user, 'full_name', '')} · "
                    + ('; '.join(filters_desc) if filters_desc else 'All records')
                    + f" · {len(rows)} entries")
        return branded_excel_response(
            filename=report_filename('Vehicle Log Report', 'xlsx'),
            sheet_title='Vehicle Log',
            report_title='Vehicle Log Report',
            subtitle=subtitle,
            headers=VEHICLE_LOG_REPORT_HEADERS,
            rows=rows,
            col_widths=[5, 22, 14, 26, 12, 22, 14, 22, 12, 10, 40],
        )


class VehicleLogPdfExportView(APIView):
    """Download the (filtered) vehicle log as a branded PDF report — CDSO only."""
    permission_classes = [IsAdminRole]

    def get(self, request):
        from report_utils import branded_pdf_response, report_filename
        rows, filters_desc = _vehicle_log_report_data(request)
        subtitle = (('; '.join(filters_desc) if filters_desc else 'All records')
                    + f" · {len(rows)} entries")
        return branded_pdf_response(
            filename=report_filename('Vehicle Log Report', 'pdf'),
            report_title='Vehicle Log Report',
            subtitle=subtitle,
            generated_by=getattr(request.user, 'full_name', ''),
            headers=VEHICLE_LOG_REPORT_HEADERS,
            rows=rows,
            # 267mm of printable width on landscape A4. Date & Time gets enough
            # to stay on one line (the audit report learned that the hard way);
            # Remarks takes the slack, being the only free-text column.
            col_widths_mm=[8, 31, 21, 34, 15, 24, 21, 31, 16, 14, 52],
        )


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

        vehicle = Vehicle.resolve(plate_number)  # plate or conduction number

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

        vehicle = Vehicle.resolve(plate_number)  # plate or conduction number
        gate_id = getattr(request.user, 'gate_assignment', None) or 'main'
        AccessLog.objects.create(
            plate_number  = plate_number,
            vehicle       = vehicle,
            status        = AccessLog.Status.DENIED,
            denied_reason = reason,
            gate_id       = gate_id,
            scanned_by    = request.user,
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

        vehicle = Vehicle.resolve(plate_number)  # plate or conduction number

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
        _today_start, _today_end = day_range(today)
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
            today_logs = AccessLog.objects.filter(
                scanned_by=guard,
                scanned_at__gte=_today_start, scanned_at__lt=_today_end,
            )

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
                guard=guard,
                clocked_in_at__gte=_today_start, clocked_in_at__lt=_today_end,
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
            .filter(status=AccessLog.Status.EXITED, paired_entry__isnull=False,
                    scanned_at__gte=_today_start, scanned_at__lt=_today_end)
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
        import concurrent.futures
        rtsp_url = (request.data.get('rtsp_url') or '').strip()
        if not rtsp_url.lower().startswith('rtsp://'):
            return Response({'ok': False, 'message': 'URL must start with rtsp://'}, status=400)

        def _probe():
            # Both backends, exactly as the live feed opens it — otherwise this
            # test can pass on a camera the feed cannot show, or fail on one it
            # can. open_capture() only reports open once a frame has actually
            # decoded, so reaching this point is proof of video.
            from vehicles.ffmpeg_capture import open_capture

            cap = open_capture(rtsp_url)
            try:
                if not cap.isOpened():
                    return False, ('Cannot connect — verify the URL, credentials, and '
                                   'that the camera is on the same network as this '
                                   'server. If another app is watching this camera, '
                                   'close it: many units serve only one stream at a time.')
                ret, _ = cap.read()
                if ret:
                    return True, 'Camera connected and streaming successfully.'
                return False, ('Reached the camera but received no frames — check '
                               'stream path or encoding settings.')
            finally:
                cap.release()

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(_probe)
            try:
                ok, msg = future.result(timeout=45)
            except concurrent.futures.TimeoutError:
                ok, msg = False, 'Connection timed out (45 s) — camera is unreachable from this server.'
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

        gate_id = getattr(request.user, 'gate_assignment', None) or 'main'
        # A guard may type a conduction number for a brand-new car, which is not a
        # valid PH plate — accept it when it resolves to a registered vehicle, but
        # still reject free-text garbage that matches nothing.
        vehicle = Vehicle.resolve(plate_number)
        if not vehicle and not is_valid_ph_plate(plate_number):
            return Response({'error': 'Invalid plate format. Enter a valid Philippine plate or conduction number.'}, status=400)

        if not vehicle:
            supplier_plate = SupplierPlate.objects.select_related('supplier').filter(
                plate_number=plate_number, supplier__is_active=True
            ).first()

            if not supplier_plate:
                if is_open_campus():
                    r = _open_campus_unknown_result(plate_number, gate_id, request.user)
                    return Response({**r, 'plate_number': plate_number, 'gate_id': gate_id})
                AccessLog.objects.create(
                    plate_number=plate_number,
                    status=AccessLog.Status.UNKNOWN,
                    gate_id=gate_id,
                    scanned_by=request.user,
                )
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

        # 'no_pass'/'unknown' mean a visitor awaiting a pass — not a violation
        if not entry['allowed'] and entry['status'] not in ('no_pass', 'unknown'):
            _auto_log_violation(vehicle, entry['message'], gate_id,
                                    evidence_bytes=_request_image_bytes(request),
                                    entry_status=entry['status'])

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
                action=AuditLog.Action.GUARD_LOGIN,
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
            try:
                _start, _end = day_range(datetime.strptime(date, '%Y-%m-%d').date())
                qs = qs.filter(clocked_in_at__gte=_start, clocked_in_at__lt=_end)
            except (TypeError, ValueError):
                pass  # ignore malformed dates rather than 500

        return Response(GSSer(qs[:100], many=True).data)