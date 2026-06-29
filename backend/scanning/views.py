from datetime import timedelta

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser
from rest_framework import permissions
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.db.models import Q
from vehicles.models import Vehicle
from violations.models import Violation
from accounts.models import User, AuditLog
from .models import AccessLog, VisitorPass, Office, MLTrainingSample, GuardShift
from .entry_logic import check_entry
from .ml.reader import read_plate
from .ml.collector import record_scan
from vehicles.serializers import VehicleSerializer
from .serializers import VisitorPassSerializer, OfficeSerializer, AccessLogSerializer, GuardShiftSerializer


def get_client_ip(request):
    x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded:
        return x_forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


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

AUTO_VIOLATION_DEDUP_SECONDS = 300   # one auto-violation per plate per 5 min


def _already_inside(plate_number: str) -> bool:
    """True if the plate has an authorized entry today with no paired exit yet."""
    today = timezone.localdate()
    return AccessLog.objects.filter(
        plate_number=plate_number,
        status=AccessLog.Status.AUTHORIZED,
        scanned_at__date=today,
        exit_log__isnull=True,
    ).exists()


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


def _auto_log_violation(vehicle, message: str):
    """Create an unauthorized violation if none was logged in the last 5 minutes."""
    cutoff = timezone.now() - timedelta(seconds=AUTO_VIOLATION_DEDUP_SECONDS)
    already = Violation.objects.filter(
        vehicle=vehicle,
        violation_type=Violation.Type.UNAUTHORIZED,
        issued_at__gte=cutoff,
    ).exists()
    if not already:
        Violation.objects.create(
            vehicle=vehicle,
            violation_type=Violation.Type.UNAUTHORIZED,
            notes=f'Auto-logged: {message}',
            fine_amount=Violation.compute_fine(vehicle),
        )


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

        gate_id = (request.data.get('gate_id') or request.query_params.get('gate_id') or 'main').strip()

        for plate_info in plates:
            plate = plate_info["plate_text"]
            bbox = plate_info["bbox"]

            vehicle = Vehicle.objects.select_related('user').filter(plate_number=plate).first()

            if not vehicle:
                AccessLog.objects.create(plate_number=plate, status='unknown', gate_id=gate_id, scanned_by=request.user)
                results.append({
                    'plate_number': plate,
                    'status': 'unknown',
                    'message': 'Plate not registered.',
                    'bbox': bbox,
                    'sample_id': ml_sample.get("sample_id") if ml_sample else None,
                })
                continue

            entry = check_entry(vehicle)
            has_violations = Violation.objects.filter(vehicle=vehicle, is_resolved=False).exists()

            AccessLog.objects.create(
                plate_number  = plate,
                vehicle       = vehicle,
                status        = entry['status'],
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
                    f"Vehicle: {vehicle.vehicle_type or 'N/A'} | Guard: {guard_name}"
                )
            else:
                action  = AuditLog.Action.SCAN
                details = (
                    f"Plate: {plate} | Owner: {owner_name} | "
                    f"Status: {entry['status']} | Reason: {entry['message']} | Guard: {guard_name}"
                )
            _audit(request, action, details)

            if not entry['allowed']:
                _auto_log_violation(vehicle, entry['message'])

            resp = {
                'plate_number':    plate,
                'status':          entry['status'],
                'allowed':         entry['allowed'],
                'message':         entry['message'],
                'constraint':      entry.get('constraint'),
                'vehicle':         VehicleSerializer(vehicle).data,
                'has_violations':  has_violations,
                'already_inside':  _already_inside(plate),
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
        plate_number = (request.data.get('plate_number') or '').strip().upper()
        if not plate_number:
            return Response({'error': 'plate_number is required.'}, status=400)

        vehicle, _ = Vehicle.objects.get_or_create(
            plate_number=plate_number,
            defaults={'vehicle_type': 'car', 'is_authorized': False},
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

        # Log entry in AccessLog so the visitor appears in "Recent Scans"
        AccessLog.objects.create(
            plate_number=plate_number,
            vehicle=vehicle,
            status=AccessLog.Status.AUTHORIZED,
            gate_id='main',
            scanned_by=request.user,
        )

        guard_name  = request.user.full_name
        office_name = office.name if office else 'N/A'
        _audit(
            request,
            AuditLog.Action.VISITOR_ISSUED,
            f"Visitor pass issued | Plate: {plate_number} | "
            f"Purpose: {pass_.purpose or 'N/A'} | Office: {office_name} | "
            f"Duration: {allowed_duration} min | Guard: {guard_name}",
        )

        return Response(VisitorPassSerializer(pass_).data, status=201)

    def get(self, request):
        """List today's visitor passes."""
        passes = VisitorPass.objects.filter(
            valid_date=timezone.localdate()
        ).select_related('vehicle', 'office', 'issued_by')
        return Response(VisitorPassSerializer(passes, many=True).data)


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

        now = timezone.now()
        pass_.status    = VisitorPass.Status.EXITED
        pass_.exited_at = now
        pass_.save()

        AccessLog.objects.create(
            vehicle=pass_.vehicle,
            plate_number=pass_.plate_number,
            status=AccessLog.Status.EXITED,
            gate_id=request.data.get('gate_id', 'main'),
            scanned_by=request.user,
        )

        duration_minutes = int((now - pass_.entered_at).total_seconds() / 60)
        guard_name = request.user.full_name
        _audit(
            request,
            AuditLog.Action.VISITOR_EXITED,
            f"Visitor exited | Plate: {pass_.plate_number} | "
            f"Duration: {duration_minutes} min | Guard: {guard_name}",
        )

        return Response(VisitorPassSerializer(pass_).data)


class OfficeListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        offices = Office.objects.all()
        return Response(OfficeSerializer(offices, many=True).data)


class AccessLogListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qs = AccessLog.objects.all().order_by('-scanned_at')
        gate_id = request.query_params.get('gate_id')
        if gate_id:
            qs = qs.filter(gate_id=gate_id)
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
        plate_number = (request.data.get('plate_number') or '').strip().upper()
        reason       = (request.data.get('reason') or '').strip()

        if not plate_number:
            return Response({'error': 'plate_number is required.'}, status=400)
        if not reason:
            return Response({'error': 'reason is required.'}, status=400)

        vehicle = Vehicle.objects.filter(plate_number=plate_number).first()

        AccessLog.objects.create(
            plate_number    = plate_number,
            vehicle         = vehicle,
            status          = AccessLog.Status.AUTHORIZED,
            is_override     = True,
            override_reason = reason,
            scanned_by      = request.user,
        )

        guard_name = request.user.full_name
        owner_name = vehicle.user.full_name if vehicle and vehicle.user else 'Unregistered'
        _audit(
            request,
            AuditLog.Action.ENTRY_OVERRIDE,
            f"Entry override | Plate: {plate_number} | Owner: {owner_name} | "
            f"Reason: {reason} | Guard: {guard_name}",
        )

        return Response({'status': 'overridden', 'plate_number': plate_number})


class ExitLogView(APIView):
    """Guard records a vehicle exit and auto-pairs it to the matching entry."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        plate_number = (request.data.get('plate_number') or '').strip().upper()
        if not plate_number:
            return Response({'error': 'plate_number is required.'}, status=400)

        vehicle = Vehicle.objects.filter(plate_number=plate_number).first()

        exit_log = AccessLog.objects.create(
            plate_number = plate_number,
            vehicle      = vehicle,
            status       = AccessLog.Status.EXITED,
            scanned_by   = request.user,
        )

        _pair_entry_exit(exit_log)

        duration_minutes = None
        entry_scanned_at = None
        if exit_log.paired_entry:
            delta            = exit_log.scanned_at - exit_log.paired_entry.scanned_at
            duration_minutes = int(delta.total_seconds() / 60)
            entry_scanned_at = exit_log.paired_entry.scanned_at

        owner_name = vehicle.user.full_name if vehicle and vehicle.user else 'Unknown'
        guard_name = request.user.full_name
        _audit(
            request,
            AuditLog.Action.VEHICLE_EXITED,
            f"Vehicle exited | Plate: {plate_number} | Owner: {owner_name} | "
            f"Duration: {duration_minutes if duration_minutes is not None else 'N/A'} min | "
            f"Guard: {guard_name}",
        )

        return Response({
            'plate_number':    plate_number,
            'status':          'exited',
            'duration_minutes': duration_minutes,
            'entry_scanned_at': entry_scanned_at,
            'scanned_at':      exit_log.scanned_at,
        })


class GuardMonitorView(APIView):
    """Admin-only: per-gate activity, current shifts, and cross-gate discrepancies."""

    def get(self, request):
        if not request.user.is_authenticated or request.user.role not in ('admin', 'cdso'):
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
        plate_number = (request.data.get('plate_number') or '').strip().upper()
        if not plate_number:
            return Response({'error': 'plate_number is required.'}, status=400)

        gate_id = getattr(request.user, 'gate_assignment', None) or 'main'
        vehicle = Vehicle.objects.select_related('user').filter(plate_number=plate_number).first()

        if not vehicle:
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

        entry = check_entry(vehicle)
        has_violations = Violation.objects.filter(vehicle=vehicle, is_resolved=False).exists()

        AccessLog.objects.create(
            plate_number  = plate_number,
            vehicle       = vehicle,
            status        = entry['status'],
            gate_id       = gate_id,
            denied_reason = '' if entry['allowed'] else entry['message'],
            scanned_by    = request.user,
        )

        owner_name = vehicle.user.full_name if vehicle.user else 'Unknown'
        guard_name = request.user.full_name
        action = AuditLog.Action.VEHICLE_ENTERED if entry['allowed'] else AuditLog.Action.SCAN
        _audit(
            request, action,
            f"Plate: {plate_number} | Owner: {owner_name} | Gate: {gate_id} | Guard: {guard_name} | Status: {entry['status']}",
        )

        if not entry['allowed']:
            _auto_log_violation(vehicle, entry['message'])

        return Response({
            'plate_number':   plate_number,
            'status':         entry['status'],
            'allowed':        entry['allowed'],
            'message':        entry['message'],
            'constraint':     entry.get('constraint'),
            'vehicle':        VehicleSerializer(vehicle).data,
            'has_violations': has_violations,
            'already_inside': _already_inside(plate_number),
            'gate_id':        gate_id,
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

        qr_token = (request.data.get('qr_token') or '').strip()
        if not qr_token:
            return Response({'error': 'qr_token is required.'}, status=400)

        try:
            guard = User.objects.get(qr_token=qr_token, role='security', is_active=True)
        except User.DoesNotExist:
            return Response({'error': 'Invalid or unrecognised QR code.'}, status=403)

        gate = guard.gate_assignment
        if not gate:
            return Response({'error': 'This guard has no gate assignment. Contact the admin.'}, status=400)

        now = tz.now()

        # End previous active shift at this gate (whoever was logged in before)
        GuardShift.objects.filter(gate=gate, clocked_out_at__isnull=True).update(
            clocked_out_at=now,
            clocked_out_by=guard,
        )

        # Start new shift
        GuardShift.objects.create(guard=guard, gate=gate)

        # Issue JWT
        refresh  = RefreshToken.for_user(guard)
        access   = str(refresh.access_token)

        # Audit
        _audit_ip = get_client_ip(request)
        try:
            AuditLog.objects.create(
                actor=guard,
                action=AuditLog.Action.VEHICLE_ENTERED,
                details=f"Guard QR login | Gate: {gate} | Guard: {guard.full_name}",
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
                'gate_assignment': guard.gate_assignment,
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
        if request.user.role not in ('admin', 'cdso'):
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