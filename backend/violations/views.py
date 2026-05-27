from rest_framework import viewsets, permissions
from .models import Violation
from .serializers import ViolationSerializer

class ViolationViewSet(viewsets.ModelViewSet):
    queryset           = Violation.objects.select_related('vehicle').all()
    serializer_class   = ViolationSerializer
    permission_classes = [permissions.IsAuthenticated]