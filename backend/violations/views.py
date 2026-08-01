from decimal import Decimal
from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status as http_status
from django.shortcuts import get_object_or_404
from django.db.models import Q
from django.utils import timezone
from .models import Violation, NEW_STYLE_TYPES, FEE_ESCALATING_TYPES, FEE_THIRD_OFFENSE
from .serializers import ViolationSerializer
from vehicles.models import Vehicle, VehicleRegistration
from accounts.audit import audit
from accounts.models import AuditLog
from time_utils import day_range, filter_local_date_range


class IsStaffRole(permissions.BasePermission):
    """Allow access only to the CDSO (admin) or security roles."""
    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role in ('admin', 'security')
        )


class IsCDSOOrAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role == 'admin'
        )


class ViolationViewSet(viewsets.ModelViewSet):
    # issued_by / on_duty_guard are read by the serializer too — without them
    # here each row costs an extra user lookup.
    queryset           = Violation.objects.select_related(
        'vehicle__user', 'issued_by', 'on_duty_guard',
    ).all()
    serializer_class   = ViolationSerializer
    permission_classes = [IsStaffRole]

    def get_serializer_context(self):
        return {**super().get_serializer_context(), 'request': self.request}

    def perform_create(self, serializer):
        # Issuing a violation is the guard's job — the admin (CDSO) handles events,
        # parking-box placement, and clearing/lifting violations, but does not
        # issue them. (CDSO management actions live on separate endpoints.)
        if self.request.user.role != 'security':
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('Only security personnel can issue violations.')

        plate = self.request.data.get('plate_number', '').strip().upper().replace(' ', '')
        vehicle = serializer.validated_data.get('vehicle')
        if vehicle is None and plate:
            # A guard may type a plate or a conduction number (brand-new car).
            vehicle = Vehicle.resolve(plate)
            if vehicle is None:
                from rest_framework.exceptions import ValidationError
                raise ValidationError({'vehicle': 'No vehicle found for that plate or conduction number.'})
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


    @action(detail=True, methods=['post'], url_path='lift',
            permission_classes=[IsCDSOOrAdmin])
    def lift_violation(self, request, pk=None):
        """Void a violation as a false alarm and renumber what is left.

        Distinct from `clear`: clearing means the offence happened and the fee
        was settled against an Official Receipt. Lifting means it should never
        have been issued — an auto-logged camera artefact, a misread plate —
        so it stops counting and the owner's remaining violations of that type
        step back down (two warnings, lift one, the survivor becomes warning 1).

        A reason is required: this erases an offence from someone's record and
        the decision has to be answerable.
        """
        violation = self.get_object()

        if violation.status == Violation.Status.LIFTED:
            return Response({'detail': 'This violation has already been lifted.'},
                            status=http_status.HTTP_400_BAD_REQUEST)
        if violation.status == Violation.Status.CLEARED:
            return Response(
                {'detail': 'This violation was already settled with an Official Receipt. '
                           'Lifting it would imply a refund that this action cannot make.'},
                status=http_status.HTTP_400_BAD_REQUEST)

        reason = (request.data.get('reason') or '').strip()
        if not reason:
            return Response({'detail': 'A reason is required to lift a violation.'},
                            status=http_status.HTTP_400_BAD_REQUEST)

        vehicle, vtype = violation.vehicle, violation.violation_type
        prev_offense   = violation.offense_number

        violation.status               = Violation.Status.LIFTED
        violation.is_resolved          = True
        violation.lifted_reason        = reason
        violation.lifted_at            = timezone.now()
        violation.lifted_by            = request.user
        violation.fine_amount          = Decimal('0.00')
        violation.registration_blocked = False
        violation.save(update_fields=[
            'status', 'is_resolved', 'lifted_reason', 'lifted_at', 'lifted_by',
            'fine_amount', 'registration_blocked',
        ])

        resequenced = Violation.resequence_offenses(vehicle, vtype)

        audit(request, AuditLog.Action.RECORD_UPDATED,
              f"Violation lifted (false alarm) | Plate: {vehicle.plate_number} | "
              f"Type: {violation.get_violation_type_display()} | "
              f"Was offense {prev_offense} | Reason: {reason} | "
              f"{resequenced} remaining renumbered | By: {request.user.full_name}")

        # The owner's list and the admin table both read from this.
        try:
            from realtime.broadcast import broadcast_change
            broadcast_change('violation', 'updated')
        except Exception:
            pass

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
                _start, _end = day_range(d)
                qs = qs.filter(issued_at__gte=_start, issued_at__lt=_end)
            except ValueError:
                pass
        return Response(ViolationSerializer(qs, many=True, context={'request': request}).data)


class MyViolationsView(APIView):
    """Returns violations visible to the authenticated vehicle owner."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        # Every vehicle linked to this owner by FK, plus plate-matched vehicles
        # from any accepted registration (covers legacy rows never linked by FK).
        plates = VehicleRegistration.objects.filter(
            user=request.user,
            status=VehicleRegistration.Status.ACCEPTED,
        ).values_list('plate_number', flat=True)
        vehicles = Vehicle.objects.filter(
            Q(user=request.user) | Q(plate_number__in=list(plates))
        )

        # New-style: always visible (is_released=True on create)
        # Legacy: show released + all resolved/cleared (history stays visible)
        violations = Violation.objects.filter(
            vehicle__in=vehicles,
        ).filter(
            Q(is_released=True) | Q(is_resolved=True)
        ).select_related('vehicle__user', 'issued_by').order_by('-issued_at')
        return Response(ViolationSerializer(violations, many=True, context={'request': request}).data)


# ── Violations Report (CDSO/admin — branded PDF & Excel) ─────────────────────
VIOLATION_REPORT_HEADERS = ['#', 'Date & Time', 'Plate', 'Owner', 'Violation', 'Fee (PHP)', 'Status', 'Issued By']


def _filter_violations_report(request):
    """Filter the violations for a report — same knobs as the management page."""
    qs = Violation.objects.select_related('vehicle', 'vehicle__user', 'issued_by').all()
    date_from = request.query_params.get('date_from', '').strip()
    date_to   = request.query_params.get('date_to', '').strip()
    status_f  = request.query_params.get('status', '').strip()
    search    = request.query_params.get('search', '').strip()
    qs = filter_local_date_range(qs, 'issued_at', date_from, date_to)
    if status_f:
        qs = qs.filter(status=status_f)
    if search:
        qs = qs.filter(Q(vehicle__plate_number__icontains=search) |
                       Q(vehicle__user__full_name__icontains=search))

    status_labels = dict(Violation.Status.choices)
    desc = []
    if date_from or date_to:
        desc.append(f"Period: {date_from or 'start'} to {date_to or 'today'}")
    if status_f:
        desc.append(f"Status: {status_labels.get(status_f, status_f)}")
    if search:
        desc.append(f"Search: '{search}'")
    return qs.order_by('-issued_at'), desc


def _violation_report_rows(qs):
    from django.utils import timezone as tz
    type_labels   = dict(Violation.Type.choices)
    status_labels = dict(Violation.Status.choices)
    rows = []
    for i, v in enumerate(qs, start=1):
        plate     = v.vehicle.plate_number if v.vehicle else '—'
        owner     = v.vehicle.user.full_name if (v.vehicle and v.vehicle.user) else '—'
        issued_by = v.issued_by.full_name if v.issued_by else 'System'
        rows.append([
            i,
            tz.localtime(v.issued_at).strftime('%b %d, %Y %I:%M %p'),
            plate, owner,
            type_labels.get(v.violation_type, v.violation_type),
            f"{v.fine_amount:.2f}",
            status_labels.get(v.status, v.status),
            issued_by,
        ])
    return rows


def _violation_report_subtitle(request, desc, count):
    from django.utils import timezone as tz
    body = ('; '.join(desc) if desc else 'All records') + f" · {count} entries"
    return body


class ViolationReportExcelView(APIView):
    """Download the (filtered) violations as a branded Excel report — admin only."""
    permission_classes = [IsCDSOOrAdmin]

    def get(self, request):
        from django.utils import timezone as tz
        from report_utils import branded_excel_response, report_filename
        qs, desc = _filter_violations_report(request)
        rows = _violation_report_rows(qs[:5000])
        subtitle = (f"Generated {tz.localtime().strftime('%B %d, %Y %I:%M %p')} "
                    f"by {getattr(request.user, 'full_name', '')} · "
                    + _violation_report_subtitle(request, desc, len(rows)))
        return branded_excel_response(
            filename=report_filename('Violations Report', 'xlsx'),
            sheet_title='Violations',
            report_title='Violations Report',
            subtitle=subtitle,
            headers=VIOLATION_REPORT_HEADERS,
            rows=rows,
            col_widths=[5, 21, 16, 26, 22, 12, 14, 22],
        )


class ViolationReportPdfView(APIView):
    """Download the (filtered) violations as a branded PDF report — admin only."""
    permission_classes = [IsCDSOOrAdmin]

    def get(self, request):
        from django.utils import timezone as tz
        from report_utils import branded_pdf_response, report_filename
        qs, desc = _filter_violations_report(request)
        rows = _violation_report_rows(qs[:5000])
        return branded_pdf_response(
            filename=report_filename('Violations Report', 'pdf'),
            report_title='Violations Report',
            subtitle=_violation_report_subtitle(request, desc, len(rows)),
            generated_by=getattr(request.user, 'full_name', ''),
            headers=VIOLATION_REPORT_HEADERS,
            rows=rows,
            # Owner names ran past their column while Issued By sat mostly
            # empty (27pt used of 146pt). 6mm moves across; total unchanged.
            col_widths_mm=[10, 34, 26, 56, 40, 22, 30, 49],
        )
