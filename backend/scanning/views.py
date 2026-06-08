from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser
from rest_framework import permissions
from django.shortcuts import get_object_or_404
from django.utils import timezone
from vehicles.models import Vehicle
from violations.models import Violation
from .models import AccessLog, VisitorPass, Office
from .entry_logic import check_entry
from .ml.reader import read_plate
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

        plate, bbox = read_plate(file.read())

        if not plate:
            AccessLog.objects.create(plate_number='', status='unreadable', scanned_by=request.user)
            return Response({'status': 'unreadable', 'message': 'Could not read a valid PH plate.'})

        vehicle = Vehicle.objects.select_related('owner').filter(plate_number=plate).first()

        if not vehicle:
            AccessLog.objects.create(plate_number=plate, status='unknown', scanned_by=request.user)
            return Response({'plate_number': plate, 'status': 'unknown', 'message': 'Plate not registered.'})

        entry   = check_entry(vehicle)
        has_violations = Violation.objects.filter(vehicle=vehicle, is_resolved=False).exists()

        AccessLog.objects.create(
            plate_number  = plate,
            vehicle       = vehicle,
            status        = entry['status'],
            denied_reason = '' if entry['allowed'] else entry['message'],
            scanned_by    = request.user,
        )

        AuditLog.objects.create(
            actor=request.user,
            action=AuditLog.Action.SCAN,
            details=f"Plate: {plate}, Status: {entry['status']}",
            ip_address=self.get_client_ip(request),
        )

        return Response({
            'plate_number':    plate,
            'status':          entry['status'],
            'allowed':         entry['allowed'],
            'message':         entry['message'],
            'constraint':      entry.get('constraint'),
            'vehicle':         VehicleSerializer(vehicle).data,
            'has_violations':  has_violations,
            'bbox':            bbox,
        })

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