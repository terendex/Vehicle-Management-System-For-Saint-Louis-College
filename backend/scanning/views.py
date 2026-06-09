from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser
from rest_framework import permissions
from django.shortcuts import get_object_or_404
from django.utils import timezone
from vehicles.models import Vehicle
from violations.models import Violation
from .models import AccessLog, VisitorPass, Office, MLTrainingSample
from .entry_logic import check_entry
from .ml.reader import read_plate
from .ml.collector import record_scan
from vehicles.serializers import VehicleSerializer
from .serializers import VisitorPassSerializer, OfficeSerializer, AccessLogSerializer
from accounts.models import AuditLog


class ScanView(APIView):
    parser_classes     = [MultiPartParser]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        file = request.FILES.get('file')
        if not file:
            return Response({'error': 'No image provided'}, status=400)

        raw_bytes = file.read()
        plate, bbox = read_plate(raw_bytes)

        ml_sample = record_scan(raw_bytes)

        if not plate:
            AccessLog.objects.create(plate_number='', status='unreadable', scanned_by=request.user)
            return Response({
                'status': 'unreadable',
                'message': 'Could not read a valid PH plate.',
                'sample_id': ml_sample.get("sample_id") if ml_sample else None,
            })

        vehicle = Vehicle.objects.select_related('owner').filter(plate_number=plate).first()

        if not vehicle:
            AccessLog.objects.create(plate_number=plate, status='unknown', scanned_by=request.user)
            return Response({
                'plate_number': plate,
                'status': 'unknown',
                'message': 'Plate not registered.',
                'sample_id': ml_sample.get("sample_id") if ml_sample else None,
            })

        entry   = check_entry(vehicle)
        has_violations = Violation.objects.filter(vehicle=vehicle, is_resolved=False).exists()

        AccessLog.objects.create(
            plate_number  = plate,
            vehicle       = vehicle,
            status        = entry['status'],
            denied_reason = '' if entry['allowed'] else entry['message'],
            scanned_by    = request.user,
            snapshot      = request.FILES.get('file'),
        )

        AuditLog.objects.create(
            actor=request.user,
            action=AuditLog.Action.SCAN,
            details=f"Plate: {plate}, Status: {entry['status']}",
            ip_address=self.get_client_ip(request),
        )

        resp = {
            'plate_number':    plate,
            'status':          entry['status'],
            'allowed':         entry['allowed'],
            'message':         entry['message'],
            'constraint':      entry.get('constraint'),
            'vehicle':         VehicleSerializer(vehicle).data,
            'has_violations':  has_violations,
            'bbox':            bbox,
        }
        if ml_sample:
            resp['sample_id'] = ml_sample['sample_id']
            resp['ml_confidence'] = ml_sample['confidence']
        return Response(resp)

    def get_client_ip(self, request):
        x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded:
            return x_forwarded.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR')


class VisitorPassView(APIView):
    """Guard creates a visitor pass at the gate."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = VisitorPassSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=201)
        return Response(serializer.errors, status=400)

    def get(self, request):
        """List today's visitor passes."""
        from django.utils import timezone
        passes = VisitorPass.objects.filter(
            valid_date=timezone.localdate()
        ).select_related('vehicle', 'office')
        return Response(VisitorPassSerializer(passes, many=True).data)


class ConfirmVisitorView(APIView):
    """Office staff confirms or rejects a visitor."""
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, pk):
        try:
            pass_ = VisitorPass.objects.get(pk=pk)
        except VisitorPass.DoesNotExist:
            return Response({'error': 'Pass not found'}, status=404)

        action       = request.data.get('action')          # 'confirm' or 'reject'
        confirmed_by = request.data.get('confirmed_by', '')

        if action == 'confirm':
            pass_.status       = VisitorPass.Status.CONFIRMED
            pass_.confirmed_by = confirmed_by
        elif action == 'reject':
            pass_.status       = VisitorPass.Status.REJECTED
            pass_.confirmed_by = confirmed_by
        else:
            return Response({'error': 'action must be confirm or reject'}, status=400)

        pass_.save()
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