from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser
from rest_framework import permissions
from vehicles.models import Vehicle
from violations.models import Violation
from .models import AccessLog, VisitorPass, Office
from .entry_logic import check_entry
from .ml.reader import read_plate
from vehicles.serializers import VehicleSerializer
from .serializers import VisitorPassSerializer, OfficeSerializer

class ScanView(APIView):
    parser_classes     = [MultiPartParser]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        file = request.FILES.get('file')
        if not file:
            return Response({'error': 'No image provided'}, status=400)

        plate, bbox = read_plate(file.read())

        if not plate:
            AccessLog.objects.create(plate_number='', status='unreadable')
            return Response({'status': 'unreadable', 'message': 'Could not read a valid PH plate.'})

        vehicle = Vehicle.objects.select_related('owner').filter(plate_number=plate).first()

        if not vehicle:
            AccessLog.objects.create(plate_number=plate, status='unknown')
            return Response({'plate_number': plate, 'status': 'unknown', 'message': 'Plate not registered.'})

        entry   = check_entry(vehicle)
        has_violations = Violation.objects.filter(vehicle=vehicle, is_resolved=False).exists()

        AccessLog.objects.create(
            plate_number  = plate,
            vehicle       = vehicle,
            status        = entry['status'],
            denied_reason = '' if entry['allowed'] else entry['message']
        )

        return Response({
            'plate_number':    plate,
            'status':          entry['status'],
            'allowed':         entry['allowed'],
            'message':         entry['message'],
            'vehicle':         VehicleSerializer(vehicle).data,
            'has_violations':  has_violations,
            'bbox':            bbox,
        })


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