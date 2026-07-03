from decimal import Decimal
from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status as http_status
from django.shortcuts import get_object_or_404
from django.db.models import Q
from .models import Violation, NEW_STYLE_TYPES, FEE_ESCALATING_TYPES, FEE_THIRD_OFFENSE
from .serializers import ViolationSerializer
from vehicles.models import Vehicle, VehicleRegistration
from accounts.audit import audit
from accounts.models import AuditLog


class IsStaffRole(permissions.BasePermission):
    """Allow access only to admin, cdso, or security roles."""
    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role in ('admin', 'cdso', 'security')
        )


class IsCDSOOrAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role in ('admin', 'cdso')
        )


class ViolationViewSet(viewsets.ModelViewSet):
    queryset           = Violation.objects.select_related('vehicle__user').all()
    serializer_class   = ViolationSerializer
    permission_classes = [IsStaffRole]

    def get_serializer_context(self):
        return {**super().get_serializer_context(), 'request': self.request}

    def perform_create(self, serializer):
        plate = self.request.data.get('plate_number', '').strip().upper()
        vehicle = serializer.validated_data.get('vehicle')
        if vehicle is None and plate:
            vehicle = get_object_or_404(Vehicle, plate_number=plate)
        if vehicle is None:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'vehicle': 'Provide a vehicle id or plate_number.'})

        vtype = serializer.validated_data.get('violation_type', '')

        if vtype in NEW_STYLE_TYPES:
            offense_num  = Violation.compute_offense_number(vehicle, vtype)
            is_fee_event = offense_num == 3 and vtype in FEE_ESCALATING_TYPES
            fine         = FEE_THIRD_OFFENSE if is_fee_event else Decimal('0.00')
            viol_status  = Violation.Status.FEE_IMPOSED if is_fee_event else Violation.Status.WARNING
            reg_blocked  = is_fee_event

            instance = serializer.save(
                vehicle              = vehicle,
                offense_number       = offense_num,
                fine_amount          = fine,
                status               = viol_status,
                registration_blocked = reg_blocked,
                is_released          = True,   # always visible to owner immediately
                issued_by            = self.request.user,
            )
            audit(self.request, AuditLog.Action.RECORD_CREATED,
                  f"Violation issued | Plate: {vehicle.plate_number} | "
                  f"Type: {instance.get_violation_type_display()} | Offense: {offense_num} | "
                  f"Status: {instance.get_status_display()} | By: {self.request.user.full_name}")
            self._notify_new_offense(instance)
        else:
            # Legacy violation types — fine from the caller, or computed.
            # Every violation notifies the owner immediately, so these are
            # released (visible) and emailed at creation like new-style ones.
            save_kwargs = {'vehicle': vehicle, 'is_released': True,
                           'issued_by': self.request.user}
            if not self.request.data.get('fine_amount'):
                save_kwargs['fine_amount'] = Violation.compute_fine(vehicle)
            instance = serializer.save(**save_kwargs)
            audit(self.request, AuditLog.Action.RECORD_CREATED,
                  f"Violation issued | Plate: {vehicle.plate_number} | "
                  f"Type: {instance.get_violation_type_display()} | "
                  f"Fine: {instance.fine_amount} | By: {self.request.user.full_name}")
            try:
                from .email_utils import send_violation_notified_email
                send_violation_notified_email(instance)
            except Exception:
                pass

    def _notify_new_offense(self, instance):
        try:
            from .email_utils import send_violation_warning_email, send_fee_imposed_email
            if instance.offense_number in (1, 2):
                send_violation_warning_email(instance)
            elif instance.offense_number == 3 and instance.status == Violation.Status.FEE_IMPOSED:
                send_fee_imposed_email(instance)
        except Exception:
            pass

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
            audit(request, AuditLog.Action.RECORD_UPDATED,
                  f"Violation resolved | Plate: {instance.vehicle.plate_number} | "
                  f"Type: {instance.get_violation_type_display()} | By: {request.user.full_name}")
            self._notify_resolved(instance)
        return response

    # ── Legacy release/unrelease actions ──────────────────────────────────────

    @action(detail=True, methods=['post'], url_path='release')
    def release(self, request, pk=None):
        """CDSO/admin releases a violation so the owner can see it, and sends them an email."""
        violation = self.get_object()
        was_released = violation.is_released
        violation.is_released = True
        violation.save(update_fields=['is_released'])
        if not was_released:
            try:
                from .email_utils import send_violation_notified_email
                send_violation_notified_email(violation)
            except Exception:
                pass
        return Response(ViolationSerializer(violation, context={'request': request}).data)

    @action(detail=True, methods=['post'], url_path='unrelease')
    def unrelease(self, request, pk=None):
        """Undo a release (revert to hidden)."""
        violation = self.get_object()
        violation.is_released = False
        violation.save(update_fields=['is_released'])
        return Response(ViolationSerializer(violation, context={'request': request}).data)

    # ── New CDSO workflow actions ──────────────────────────────────────────────

    @action(detail=True, methods=['post'], url_path='issue-report',
            permission_classes=[IsCDSOOrAdmin])
    def issue_cdso_report(self, request, pk=None):
        """CDSO marks that they've issued the official violation report to the owner."""
        violation = self.get_object()
        if violation.status != Violation.Status.FEE_IMPOSED:
            return Response(
                {'detail': 'Report can only be issued for fee-imposed violations.'},
                status=http_status.HTTP_400_BAD_REQUEST,
            )
        violation.cdso_report_issued = True
        violation.save(update_fields=['cdso_report_issued'])
        audit(request, AuditLog.Action.RECORD_UPDATED,
              f"CDSO violation report issued | Plate: {violation.vehicle.plate_number} | "
              f"Type: {violation.get_violation_type_display()} | By: {request.user.full_name}")
        return Response(ViolationSerializer(violation, context={'request': request}).data)

    @action(detail=True, methods=['post'], url_path='clear',
            permission_classes=[IsCDSOOrAdmin])
    def clear_violation(self, request, pk=None):
        """
        CDSO clears a fee-imposed violation after the owner presents the Official Receipt.
        Requires `official_receipt` in the request body.
        Sets status=cleared, is_resolved=True, stores the OR number.
        The warning cycle resets for this violation type; registration_blocked stays True.
        """
        violation = self.get_object()
        if violation.status != Violation.Status.FEE_IMPOSED:
            return Response(
                {'detail': 'Only fee-imposed violations can be cleared through this action.'},
                status=http_status.HTTP_400_BAD_REQUEST,
            )
        or_number = request.data.get('official_receipt', '').strip()
        if not or_number:
            return Response(
                {'detail': 'official_receipt is required to clear this violation.'},
                status=http_status.HTTP_400_BAD_REQUEST,
            )
        violation.official_receipt  = or_number
        violation.status            = Violation.Status.CLEARED
        violation.is_resolved       = True
        violation.save(update_fields=['official_receipt', 'status', 'is_resolved'])
        audit(request, AuditLog.Action.RECORD_UPDATED,
              f"Violation cleared | Plate: {violation.vehicle.plate_number} | "
              f"Type: {violation.get_violation_type_display()} | OR: {or_number} | "
              f"Entry access restored | By: {request.user.full_name}")
        self._notify_resolved(violation)
        return Response(ViolationSerializer(violation, context={'request': request}).data)


class GuardViolationsView(APIView):
    """Returns violations issued by the currently authenticated security guard."""
    permission_classes = [IsStaffRole]

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
    """Returns violations visible to the authenticated vehicle owner."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        try:
            reg = VehicleRegistration.objects.filter(
                user=request.user,
                status=VehicleRegistration.Status.ACCEPTED,
            ).latest('reviewed_at')
        except VehicleRegistration.DoesNotExist:
            return Response([])

        vehicle = (
            reg.vehicle
            or Vehicle.objects.filter(plate_number=reg.plate_number).first()
        )
        if not vehicle:
            return Response([])

        # New-style: always visible (is_released=True on create)
        # Legacy: show released + all resolved
        violations = Violation.objects.filter(
            vehicle=vehicle,
        ).filter(
            Q(is_released=True) | Q(is_resolved=True)
        ).order_by('-issued_at')
        return Response(ViolationSerializer(violations, many=True, context={'request': request}).data)
