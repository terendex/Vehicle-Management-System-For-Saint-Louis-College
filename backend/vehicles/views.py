from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import Owner, Vehicle
from .serializers import OwnerSerializer, VehicleSerializer

class OwnerViewSet(viewsets.ModelViewSet):
    queryset           = Owner.objects.all()
    serializer_class   = OwnerSerializer
    permission_classes = [permissions.IsAuthenticated]

class VehicleViewSet(viewsets.ModelViewSet):
    queryset           = Vehicle.objects.select_related('owner').all()
    serializer_class   = VehicleSerializer
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=True, methods=['patch'])
    def authorize(self, request, pk=None):
        vehicle = self.get_object()
        vehicle.is_authorized = not vehicle.is_authorized
        vehicle.save()
        return Response({'plate': vehicle.plate_number, 'is_authorized': vehicle.is_authorized})