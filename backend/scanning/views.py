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
from .models import AccessLog, VisitorPass, Office, MLTrainingSample
from .entry_logic import check_entry
from .ml.reader import read_plate
from .ml.collector import record_scan
from vehicles.serializers import VehicleSerializer
from .serializers import VisitorPassSerializer, OfficeSerializer, AccessLogSerializer

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

        for plate_info in plates:
            plate = plate_info["plate_text"]
            bbox = plate_info["bbox"]

            vehicle = Vehicle.objects.select_related('user').filter(plate_number=plate).first()

            if not vehicle:
                AccessLog.objects.create(plate_number=plate, status='unknown', scanned_by=request.user)
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
                scanned_by    = request.user,
                snapshot      = request.FILES.get('image'),
            )

            AuditLog.objects.create(
                actor=request.user,
                action=AuditLog.Action.SCAN,
                details=f"Plate: {plate}, Status: {entry['status']}",
                ip_address=self.get_client_ip(request),
            )

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

        pass_ = VisitorPass.objects.create(
            vehicle=vehicle,
            plate_number=plate_number,
            office=office,
            purpose=request.data.get('purpose', ''),
            issued_by=request.user,
            valid_date=timezone.localdate(),
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

        return Response(VisitorPassSerializer(pass_).data)


class OfficeListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        offices = Office.objects.all()
        return Response(OfficeSerializer(offices, many=True).data)


class AccessLogListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        logs = AccessLog.objects.all().order_by('-scanned_at')[:200]
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

        try:
            AuditLog.objects.create(
                actor   = request.user,
                action  = AuditLog.Action.SCAN,
                details = f"Override entry — Plate: {plate_number}, Reason: {reason}",
            )
        except Exception:
            pass

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

        try:
            AuditLog.objects.create(
                actor   = request.user,
                action  = AuditLog.Action.SCAN,
                details = f"Exit recorded — Plate: {plate_number}, Duration: {duration_minutes} min",
            )
        except Exception:
            pass

        return Response({
            'plate_number':    plate_number,
            'status':          'exited',
            'duration_minutes': duration_minutes,
            'entry_scanned_at': entry_scanned_at,
            'scanned_at':      exit_log.scanned_at,
        })