from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.views import APIView
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from .models import Violation
from .serializers import ViolationSerializer
from vehicles.models import Vehicle, VehicleRegistration


class ViolationViewSet(viewsets.ModelViewSet):
    queryset           = Violation.objects.select_related('vehicle__user').all()
    serializer_class   = ViolationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_context(self):
        return {**super().get_serializer_context(), 'request': self.request}

    def perform_create(self, serializer):
        """Allow passing plate_number instead of vehicle FK."""
        plate = self.request.data.get('plate_number', '').strip().upper()
        if plate and 'vehicle' not in self.request.data:
            vehicle = get_object_or_404(Vehicle, plate_number=plate)
            fine = Violation.compute_fine(vehicle)
            serializer.save(vehicle=vehicle, fine_amount=fine, issued_by=self.request.user)
        else:
            serializer.save(issued_by=self.request.user)

    def _notify_resolved(self, instance):
        try:
            from .email_utils import send_violation_resolved_email
            send_violation_resolved_email(instance)
        except Exception:
            pass

    def update(self, request, *args, **kwargs):
        was_resolved = self.get_object().is_resolved
        response = super().update(request, *args, **kwargs)
        if not was_resolved and response.data.get('is_resolved'):
            instance = self.get_object()
            self._notify_resolved(instance)
        return response

    def partial_update(self, request, *args, **kwargs):
        was_resolved = self.get_object().is_resolved
        response = super().partial_update(request, *args, **kwargs)
        if not was_resolved and response.data.get('is_resolved'):
            instance = self.get_object()
            self._notify_resolved(instance)
        return response

    @action(detail=True, methods=['post'], url_path='release')
    def release(self, request, pk=None):
        """CDSO/admin releases a violation so the owner can see it."""
        violation = self.get_object()
        violation.is_released = True
        violation.save(update_fields=['is_released'])
        return Response(ViolationSerializer(violation).data)

    @action(detail=True, methods=['post'], url_path='unrelease')
    def unrelease(self, request, pk=None):
        """Undo a release (revert to hidden)."""
        violation = self.get_object()
        violation.is_released = False
        violation.save(update_fields=['is_released'])
        return Response(ViolationSerializer(violation).data)


class GuardViolationsView(APIView):
    """Returns violations issued by the currently authenticated security guard."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from django.utils import timezone
        date_str = request.query_params.get('date', '')
        qs = Violation.objects.filter(issued_by=request.user).select_related('vehicle__user').order_by('-issued_at')
        if date_str:
            try:
                from datetime import date as _date
                d = _date.fromisoformat(date_str)
                qs = qs.filter(issued_at__date=d)
            except ValueError:
                pass
        return Response(ViolationSerializer(qs, many=True, context={'request': request}).data)


class MyViolationsView(APIView):
    """Returns released violations for the authenticated vehicle owner."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        try:
            reg = VehicleRegistration.objects.filter(
                user=request.user,
                status=VehicleRegistration.Status.ACCEPTED,
            ).latest('reviewed_at')
        except VehicleRegistration.DoesNotExist:
            return Response([])

        # Prefer FK link; fall back to plate lookup for legacy records
        vehicle = (
            reg.vehicle
            or Vehicle.objects.filter(plate_number=reg.plate_number).first()
        )
        if not vehicle:
            return Response([])

        violations = Violation.objects.filter(
            vehicle=vehicle,
        ).order_by('-issued_at')
        return Response(ViolationSerializer(violations, many=True, context={'request': request}).data)
