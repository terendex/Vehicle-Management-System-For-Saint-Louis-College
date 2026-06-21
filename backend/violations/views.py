from rest_framework import viewsets, permissions
from rest_framework.views import APIView
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from .models import Violation
from .serializers import ViolationSerializer
from vehicles.models import Vehicle, VehicleRegistration


class ViolationViewSet(viewsets.ModelViewSet):
    queryset           = Violation.objects.select_related('vehicle').all()
    serializer_class   = ViolationSerializer
    permission_classes = [permissions.IsAuthenticated]


class MyViolationsView(APIView):
    """Returns violations for the authenticated vehicle owner's vehicle."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        try:
            reg = VehicleRegistration.objects.filter(
                user=request.user,
                status=VehicleRegistration.Status.ACCEPTED,
            ).latest('reviewed_at')
            vehicle = get_object_or_404(Vehicle, plate_number=reg.plate_number)
        except VehicleRegistration.DoesNotExist:
            return Response([])

        violations = Violation.objects.filter(vehicle=vehicle).order_by('-issued_at')
        return Response(ViolationSerializer(violations, many=True).data)