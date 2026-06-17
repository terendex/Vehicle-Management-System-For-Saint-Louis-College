from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser
from rest_framework import permissions
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.db.models import Q
from vehicles.models import Vehicle, Owner
from violations.models import Violation
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

            vehicle = Vehicle.objects.select_related('owner').filter(plate_number=plate).first()

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

    The digital_id payload can be:
      • A SLC user code, e.g. "SLC-OWN-000001"   (primary — QR or typed)
      • A system student/employee ID, e.g. "SLC-STU-000001" / "SLC-EMP-000001"
      • A VehicleRegistration system_student_id or system_employee_id
      • Owner full_name or contact (fallback — guard-typed)

    Lookup order:
      1. accounts.User.user_code (exact) → Owner via full_name match on User.full_name
      2. VehicleRegistration.system_student_id / system_employee_id (exact)
         → find matching Owner by full_name
      3. Owner.full_name (case-insensitive contains) or Owner.contact (contains)
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        digital_id = (request.data.get('digital_id') or '').strip()
        vehicle_type = request.data.get('vehicle_type', 'bicycle')

        if not digital_id:
            return Response({'error': 'digital_id is required'}, status=400)

        owner = self._lookup_owner(digital_id)

        if not owner:
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

        entry = check_owner_entry(owner, vehicle_type)

        # Violations are linked to Vehicle, not directly to Owner.
        # Check if any vehicle owned by this owner has unresolved violations.
        has_violations = Violation.objects.filter(
            vehicle__owner=owner, is_resolved=False
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
                f"Owner: {owner.full_name}, Status: {entry['status']}"
            ),
        )

        return Response({
            'status':     entry['status'],
            'allowed':    entry['allowed'],
            'message':    entry['message'],
            'constraint': entry.get('constraint'),
            'owner': {
                'full_name':  owner.full_name,
                'owner_type': owner.owner_type,
                'schedule':   owner.schedule,
            },
            'vehicle_type':   vehicle_type,
            'has_violations': has_violations,
        })

    def _lookup_owner(self, digital_id: str):
        """
        Multi-tier owner lookup by digital ID.

        Returns the matching Owner instance, or None if not found.
        """
        from accounts.models import User
        from vehicles.models import Owner, VehicleRegistration

        # Tier 1: accounts.User.user_code (e.g. "SLC-OWN-000001")
        try:
            user = User.objects.get(user_code__iexact=digital_id)
            # Find the Owner whose full_name matches the User's full_name
            owner = Owner.objects.filter(full_name__iexact=user.full_name).first()
            if owner:
                return owner
        except User.DoesNotExist:
            pass

        # Tier 2: VehicleRegistration system IDs (system_student_id / system_employee_id)
        reg = VehicleRegistration.objects.filter(
            Q(system_student_id__iexact=digital_id) |
            Q(system_employee_id__iexact=digital_id)
        ).first()
        if reg:
            owner = Owner.objects.filter(full_name__iexact=reg.full_name).first()
            if owner:
                return owner

        # Tier 3: Owner.full_name or Owner.contact (fallback for guard-typed input)
        owner = Owner.objects.filter(
            Q(full_name__icontains=digital_id) |
            Q(contact__icontains=digital_id)
        ).first()
        return owner

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