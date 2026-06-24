from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser
from rest_framework import permissions
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.db.models import Q
from vehicles.models import Vehicle
from violations.models import Violation
from accounts.models import User
from .models import AccessLog, VisitorPass, Office, MLTrainingSample
from .entry_logic import check_entry, check_owner_entry
from .ml.reader import read_plate
from .ml.collector import record_scan
from vehicles.serializers import VehicleSerializer
from .serializers import VisitorPassSerializer, OfficeSerializer, AccessLogSerializer
from accounts.models import AuditLog


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
            results.append(resp)

        return Response({'results': results})


class DigitalIDVerifyView(APIView):
    """
    Verify digital ID for unplated vehicle entry (bicycle, e_bike, electric_scooter).

    Lookup order:
      1. User.user_code (exact)
      2. VehicleRegistration.system_student_id / system_employee_id → User via email
      3. User.full_name or User.contact (fallback for guard-typed input)
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        digital_id   = (request.data.get('digital_id') or '').strip()
        vehicle_type = request.data.get('vehicle_type', 'bicycle')

        if not digital_id:
            return Response({'error': 'digital_id is required'}, status=400)

        user = self._lookup_user(digital_id)

        if not user:
            AccessLog.objects.create(
                plate_number='',
                vehicle_type=vehicle_type,
                digital_id_used=digital_id,
                status='unknown',
                scanned_by=request.user,
            )
            return Response({
                'status': 'unknown',
                'message': 'Digital ID not recognized.',
                'vehicle_type': vehicle_type,
            })

        entry = check_owner_entry(user, vehicle_type)

        has_violations = Violation.objects.filter(
            vehicle__user=user, is_resolved=False
        ).exists()

        AccessLog.objects.create(
            plate_number='',
            vehicle_type=vehicle_type,
            digital_id_used=digital_id,
            status=entry['status'],
            denied_reason='' if entry['allowed'] else entry['message'],
            scanned_by=request.user,
        )

        AuditLog.objects.create(
            actor=request.user,
            action=AuditLog.Action.SCAN,
            details=(
                f"Digital ID: {digital_id}, Vehicle: {vehicle_type}, "
                f"Owner: {user.full_name}, Status: {entry['status']}"
            ),
        )

        return Response({
            'status':     entry['status'],
            'allowed':    entry['allowed'],
            'message':    entry['message'],
            'constraint': entry.get('constraint'),
            'owner': {
                'full_name':  user.full_name,
                'owner_type': user.owner_type,
                'schedule':   user.schedule,
            },
            'vehicle_type':   vehicle_type,
            'has_violations': has_violations,
        })

    def _lookup_user(self, digital_id: str):
        from vehicles.models import VehicleRegistration

        # Tier 1: User.user_code exact match
        user = User.objects.filter(user_code__iexact=digital_id).first()
        if user:
            return user

        # Tier 2: VehicleRegistration system IDs → find User by email
        reg = VehicleRegistration.objects.filter(
            Q(system_student_id__iexact=digital_id) |
            Q(system_employee_id__iexact=digital_id)
        ).first()
        if reg:
            user = User.objects.filter(email__iexact=reg.email).first()
            if user:
                return user

        # Tier 3: full_name or contact fallback
        return User.objects.filter(
            Q(full_name__icontains=digital_id) |
            Q(contact__icontains=digital_id)
        ).first()

    def get_client_ip(self, request):
        x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded:
            return x_forwarded.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR')



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