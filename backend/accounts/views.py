import logging
import os
import re
from django.conf import settings
from rest_framework import generics, permissions, status, filters
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView
from django.shortcuts import get_object_or_404
from django.db.models import Q
from django.utils import timezone
from time_utils import day_range, day_start, day_end, filter_local_date_range
from .email_utils import notify_password_set
from .models import User, AuditLog, Notification
from .twofa_api import HasRecentTwoFactor
from .serializers import (
    UserSerializer,
    UserUpdateSerializer,
    RegisterSerializer,
    AdminReplaceSerializer,
    GuardCreateSerializer,
    AdminOwnerCreateSerializer,
    CustomTokenObtainPairSerializer,
    AuditLogSerializer,
    NotificationSerializer,
)

logger = logging.getLogger(__name__)


def get_client_ip(request):
    """Extract client IP from request."""
    x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded:
        return x_forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def log_action(request, action, target_user=None, details=''):
    """Create an audit log entry."""
    AuditLog.objects.create(
        actor=request.user,
        action=action,
        target_user=target_user,
        details=details,
        ip_address=get_client_ip(request),
    )


class StandardResultsSetPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100


class IsAdminRole(permissions.BasePermission):
    """Allow access only to users with admin role."""
    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role == 'admin'
        )


class IsAdminOrCdso(permissions.BasePermission):
    """Allow access to the CDSO (admin) role."""
    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role == 'admin'
        )


class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer


class RegisterView(generics.CreateAPIView):
    queryset            = User.objects.all()
    serializer_class    = RegisterSerializer
    permission_classes  = [IsAdminRole]

    def perform_create(self, serializer):
        user = serializer.save()
        log_action(self.request, AuditLog.Action.USER_CREATED, target_user=user)


class MeView(generics.RetrieveAPIView):
    serializer_class   = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user


# ──────────────────────────────────────────────
#  User Management (admin only)
# ──────────────────────────────────────────────

class UserListView(generics.ListAPIView):
    """List all users except admins, with optional ?search= by name."""
    serializer_class   = UserSerializer
    permission_classes = [IsAdminRole]
    pagination_class   = StandardResultsSetPagination

    def get_queryset(self):
        from django.db.models import Prefetch
        from vehicles.models import VehicleRegistration

        # The serializer only reads registrant_type off the earliest
        # registration, so fetch just those columns instead of every field
        # (registrations carry image/document fields we'd otherwise pull down).
        qs = (
            User.objects
            .exclude(role='admin')
            .prefetch_related(Prefetch(
                'registrations',
                queryset=VehicleRegistration.objects.only(
                    'id', 'user_id', 'registrant_type',
                ).order_by('id'),
            ))
            .order_by('-id')
        )
        search = self.request.query_params.get('search', '').strip()
        if search:
            qs = qs.filter(
                Q(full_name__icontains=search) |
                Q(email__icontains=search) |
                Q(user_code__icontains=search)
            )

        role = self.request.query_params.get('role', '').strip()
        if role in ['security', 'vehicle_owner']:
            qs = qs.filter(role=role)

        registrant_type = self.request.query_params.get('registrant_type', '').strip()
        if registrant_type in ['student', 'employee', 'fetcher']:
            qs = qs.filter(registrations__registrant_type=registrant_type).distinct()

        status_param = self.request.query_params.get('status', '').strip()
        if status_param == 'active':
            qs = qs.filter(is_active=True)
        elif status_param == 'disabled':
            qs = qs.filter(is_active=False)

        return qs


class UserDetailView(generics.RetrieveAPIView):
    """Get a single user by ID."""
    queryset           = User.objects.all()
    serializer_class   = UserSerializer
    permission_classes = [IsAdminRole]


class UserUpdateView(generics.UpdateAPIView):
    """Edit user details (full_name, email, role, photo)."""
    queryset           = User.objects.all()
    serializer_class   = UserUpdateSerializer
    permission_classes = [IsAdminRole]

    def get_serializer_context(self):
        return {**super().get_serializer_context(), 'request': self.request}

    def perform_update(self, serializer):
        old_user = serializer.instance
        changes = []
        for field in ['full_name', 'email', 'role']:
            old_val = getattr(old_user, field)
            new_val = serializer.validated_data.get(field, old_val)
            if old_val != new_val:
                changes.append(f"{field}: '{old_val}' → '{new_val}'")
        if 'photo' in serializer.validated_data:
            changes.append('photo updated')

        user = serializer.save()
        log_action(self.request, AuditLog.Action.USER_UPDATED, target_user=user, details='; '.join(changes))


class UserDeleteView(generics.DestroyAPIView):
    """Hard-delete a user, along with data that belongs to them (vehicles,
    registrations). Records that merely reference the user as an actor
    (audit logs, issued violations, scans, created events/notices) are left
    intact for accountability history — their FK just goes null."""
    queryset           = User.objects.all()
    serializer_class   = UserSerializer
    permission_classes = [IsAdminRole]

    def destroy(self, request, *args, **kwargs):
        from .models import delete_user_with_owned_records

        user = self.get_object()
        if user.role == 'admin':
            return Response(
                {'detail': 'Cannot delete an admin from this endpoint.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Logged before the delete: log_action reads the user it is pointed at.
        log_action(request, AuditLog.Action.USER_DELETED, target_user=user)
        delete_user_with_owned_records(user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserRegistrationPdfView(APIView):
    """Re-issue the approved-registration PDF for a vehicle owner.

    Owners are emailed this document when their registration is approved, but
    a lost email or a lost printout leaves them with nothing to present at the
    gate. This serves a byte-identical copy on demand from the CDSO's desk, so
    a reprint is never a different document from the original.

    Deliberately the same builder the approval email uses — not a second
    layout that could drift away from it.
    """
    permission_classes = [IsAdminRole]

    def get(self, request, pk):
        from django.http import HttpResponse
        from vehicles.models import VehicleRegistration
        from registration_pdf import (registration_confirmation_pdf,
                                      registration_pdf_filename)

        user = get_object_or_404(User, pk=pk)
        # Matches MyRegistrationView: registrations created before the account
        # existed are linked by email, not FK.
        registration = (
            VehicleRegistration.objects
            .filter(Q(user=user) | Q(email=user.email), status='accepted')
            .order_by('-reviewed_at')
            .first()
        )
        if not registration:
            return Response(
                {'detail': 'This account has no approved vehicle registration to print.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        pdf = registration_confirmation_pdf(registration)
        log_action(
            request, AuditLog.Action.RECORD_CREATED, target_user=user,
            details=f'Registration PDF re-issued | REG-{registration.id:06d} '
                    f'({registration.plate_number}) | For: {user.full_name}',
        )
        resp = HttpResponse(pdf, content_type='application/pdf')
        resp['Content-Disposition'] = (
            f'attachment; filename="{registration_pdf_filename(registration)}"'
        )
        return resp


class UserToggleStatusView(APIView):
    """Toggle a user's is_active flag."""
    permission_classes = [IsAdminRole]

    def post(self, request, pk):
        user = get_object_or_404(User, pk=pk)
        if user.role == 'admin':
            return Response(
                {'detail': 'Cannot disable an admin account.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        old_status = user.is_active
        user.is_active = not user.is_active
        user.save(update_fields=['is_active'])
        action = AuditLog.Action.USER_ENABLED if user.is_active else AuditLog.Action.USER_DISABLED
        log_action(request, action, target_user=user)
        return Response(UserSerializer(user).data)


class AdminReplaceView(APIView):
    """Create a new admin and delete the current admin."""
    permission_classes = [IsAdminRole]

    def post(self, request):
        from django.db import transaction

        serializer = AdminReplaceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        old_admin = request.user
        # Create, log and delete are one unit: a failure partway through used to
        # leave the system with two admin accounts (new one created, old one
        # never removed). The credentials email is sent inside save() and cannot
        # be recalled on a rollback, but credentials for an account that no
        # longer exists are unusable — a stray mail beats a split admin state.
        with transaction.atomic():
            new_admin = serializer.save()
            log_action(request, AuditLog.Action.ADMIN_REPLACED, target_user=new_admin,
                       details=f"Replaced admin: {old_admin.email}")
            old_admin.delete()
        return Response(
            {
                'detail': 'Admin replaced successfully.',
                'user': UserSerializer(new_admin).data,
            },
            status=status.HTTP_201_CREATED,
        )


class AdminCreateGuardView(APIView):
    """Admin creates a security-guard account with email + password credentials.
    Guards log in at the dedicated guard gate login page (credentials or QR badge)."""
    permission_classes = [IsAdminRole]

    def post(self, request):
        serializer = GuardCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        guard = serializer.save()
        log_action(request, AuditLog.Action.USER_CREATED, target_user=guard,
                   details=f'Guard account created: {guard.full_name}')
        return Response(UserSerializer(guard, context={'request': request}).data,
                        status=status.HTTP_201_CREATED)


class AdminCreateOwnerView(APIView):
    """Admin creates a vehicle-owner account directly.  Password is auto-generated
    and emailed to the owner; must_change_password is set."""
    permission_classes = [IsAdminRole]

    def post(self, request):
        serializer = AdminOwnerCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        owner = serializer.save()
        log_action(request, AuditLog.Action.USER_CREATED, target_user=owner,
                   details=f'Vehicle-owner account created by admin: {owner.full_name}')
        return Response(UserSerializer(owner, context={'request': request}).data,
                        status=status.HTTP_201_CREATED)


class DashboardStatsView(APIView):
    """Return dashboard stats. Admin gets full overview; security gets personal scan stats."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from django.db.models import Count
        from django.utils import timezone
        from datetime import timedelta
        from scanning.models import AccessLog

        user = request.user
        today = timezone.localdate()
        week_ago = today - timedelta(days=7)
        # Half-open UTC bounds so the timestamp indexes are usable — a
        # `__date` lookup would force a per-row timezone conversion instead.
        today_start, today_end = day_range(today)
        week_start = day_start(week_ago)

        if user.role == 'admin':
            from django.db.models.functions import ExtractWeekDay
            from vehicles.models import Vehicle, VehicleRegistration

            # Every query is a ~40ms round-trip to the DB, so each block below is
            # collapsed into ONE aggregate using conditional Count(filter=...)
            # instead of a series of separate .count() calls.
            from django.db.models import Q

            u_agg = User.objects.aggregate(
                total=Count('id'),
                security=Count('id', filter=Q(role='security')),
                owners=Count('id', filter=Q(role='vehicle_owner')),
                active=Count('id', filter=Q(is_active=True)),
                disabled=Count('id', filter=Q(is_active=False)),
                # Archived (auto-expired) owners are also is_active=False; keep the
                # manually-disabled slice distinct from the archived slice so the
                # dashboard donut doesn't double-count them.
                owners_disabled=Count('id', filter=Q(role='vehicle_owner', is_active=False, is_archived=False)),
                owners_archived=Count('id', filter=Q(role='vehicle_owner', is_archived=True)),
                owners_banned=Count('id', filter=Q(role='vehicle_owner', registration_banned=True)),
                own_student=Count('id', filter=Q(role='vehicle_owner', is_active=True, owner_type='student')),
                own_employee=Count('id', filter=Q(role='vehicle_owner', is_active=True, owner_type='employee')),
                own_fetcher=Count('id', filter=Q(role='vehicle_owner', is_active=True, owner_type='fetcher')),
                own_visitor=Count('id', filter=Q(role='vehicle_owner', is_active=True, owner_type='visitor')),
            )
            total_users         = u_agg['total']
            security_count      = u_agg['security']
            vehicle_owner_count = u_agg['owners']
            active_users        = u_agg['active']
            disabled_users      = u_agg['disabled']
            owners_disabled     = u_agg['owners_disabled']

            # Vehicle-owner category breakdown (active owners split by owner_type,
            # plus a disabled slice) for the owners pie. Active-by-type + disabled
            # are mutually exclusive so they sum cleanly in a donut.
            owners_active_by_type = {
                'student':  u_agg['own_student'],
                'employee': u_agg['own_employee'],
                'fetcher':  u_agg['own_fetcher'],
                'visitor':  u_agg['own_visitor'],
            }

            v_agg = Vehicle.objects.aggregate(
                total=Count('id'),
                authorized=Count('id', filter=Q(is_authorized=True)),
            )
            total_vehicles        = v_agg['total']
            authorized_vehicles   = v_agg['authorized']
            unauthorized_vehicles = total_vehicles - authorized_vehicles

            # Vehicle-type breakdown for the vehicle-types chart.
            #
            # vehicle_type is free text and has been written with inconsistent
            # casing ("Motorcycle" and "motorcycle" both exist, plus values like
            # "SUV" that are not in any fixed list). Grouping on the raw column
            # therefore returned split buckets, and the dashboard — which matched
            # a hardcoded lowercase set — silently dropped everything it did not
            # recognise. The chart showed 4 of 9 vehicles while its centre label
            # read the true total. Fold to lowercase here so each type is counted
            # once and the slices always add up to `total`.
            vehicles_by_type = {}
            for row in Vehicle.objects.values('vehicle_type').annotate(count=Count('id')):
                key = (row['vehicle_type'] or '').strip().lower() or 'unknown'
                vehicles_by_type[key] = vehicles_by_type.get(key, 0) + row['count']

            # Suppliers are their own model (not User owners) but form a registered
            # vehicle category alongside students/employees/fetchers. Counted by
            # plate (supplier vehicles), scoped to active supplier companies.
            from vehicles.models import SupplierPlate
            active_suppliers = SupplierPlate.objects.filter(supplier__is_active=True).count()

            # One aggregate query gives the full per-status picture for today
            today_by_status = {
                row['status']: row['count']
                for row in AccessLog.objects.filter(scanned_at__gte=today_start,
                                                    scanned_at__lt=today_end)
                                            .values('status').annotate(count=Count('id'))
            }
            today_scans      = sum(today_by_status.values())
            authorized_today = today_by_status.get('authorized', 0)
            denied_today     = today_by_status.get('denied', 0) + today_by_status.get('wrong_day', 0)
            unknown_today    = today_by_status.get('unknown', 0)

            # Week total + today's visitor-pass entries in a single pass.
            # (Visitor passes create an ownerless vehicle, so user is null.)
            al_agg = AccessLog.objects.filter(scanned_at__gte=week_start).aggregate(
                week=Count('id'),
                visitor_today=Count('id', filter=Q(
                    scanned_at__gte=today_start, scanned_at__lt=today_end,
                    status='authorized', vehicle__user__isnull=True,
                )),
            )
            week_scans       = al_agg['week']
            visitor_today    = al_agg['visitor_today']
            registered_today = authorized_today - visitor_today

            # Day distribution: authorized entries per weekday (Mon–Sat)
            # Django ExtractWeekDay: 1=Sunday, 2=Monday, …, 7=Saturday
            DAY_MAP = {2: 'Mon', 3: 'Tue', 4: 'Wed', 5: 'Thu', 6: 'Fri', 7: 'Sat'}
            day_rows = (
                AccessLog.objects
                .filter(status='authorized', scanned_at__gte=week_start)
                .annotate(wd=ExtractWeekDay('scanned_at'))
                .filter(wd__in=DAY_MAP.keys())
                .values('wd')
                .annotate(count=Count('id'))
            )
            day_dist_map = {row['wd']: row['count'] for row in day_rows}
            day_distribution = [
                {'day': DAY_MAP[wd], 'count': day_dist_map.get(wd, 0)}
                for wd in sorted(DAY_MAP.keys())
            ]
            authorized_week = sum(day_dist_map.values())

            # Registration totals + per-day load (Mon–Sat) in ONE query. This
            # previously ran 6 days x 2 statuses = 12 separate count queries.
            from vehicles.views import SCHEDULE_SLOT_LIMIT
            WEEK_DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
            _ACC, _PEN = VehicleRegistration.Status.ACCEPTED, VehicleRegistration.Status.PENDING
            _reg_expr = {
                'pending':  Count('id', filter=Q(status=_PEN)),
                'accepted': Count('id', filter=Q(status=_ACC)),
                'rejected': Count('id', filter=Q(status=VehicleRegistration.Status.REJECTED)),
            }
            for _i, _day in enumerate(WEEK_DAYS):
                _reg_expr[f'd{_i}_acc'] = Count('id', filter=Q(campus_days__contains=[_day], status=_ACC))
                _reg_expr[f'd{_i}_pen'] = Count('id', filter=Q(campus_days__contains=[_day], status=_PEN))
            reg_agg = VehicleRegistration.objects.aggregate(**_reg_expr)

            pending_registrations  = reg_agg['pending']
            accepted_registrations = reg_agg['accepted']
            rejected_registrations = reg_agg['rejected']
            day_registrations = [
                {
                    'day':      _day,
                    'accepted': reg_agg[f'd{_i}_acc'],
                    'pending':  reg_agg[f'd{_i}_pen'],
                    'capacity': SCHEDULE_SLOT_LIMIT,
                }
                for _i, _day in enumerate(WEEK_DAYS)
            ]

            # Violations & visitor passes — surfaced on the dashboard KPI strip
            from violations.models import Violation
            from scanning.models import VisitorPass
            viol_agg = Violation.objects.aggregate(
                open=Count('id', filter=Q(is_resolved=False)),
                fee=Count('id', filter=Q(status=Violation.Status.FEE_IMPOSED)),
            )
            open_violations = viol_agg['open']
            fee_imposed     = viol_agg['fee']
            active_passes   = VisitorPass.objects.filter(
                valid_date=today, status=VisitorPass.Status.ACTIVE
            ).count()

            # Violation breakdown by type (last 30 days) for the violations trend chart
            month_ago = today - timedelta(days=30)
            violations_by_type = {
                row['violation_type']: row['count']
                for row in Violation.objects.filter(issued_at__date__gte=month_ago)
                                            .values('violation_type').annotate(count=Count('id'))
            }

            recent_admin_logs    = AuditLog.objects.select_related('actor', 'target_user').filter(
                actor__role='admin'
            ).order_by('-created_at')[:10]
            recent_security_logs = AuditLog.objects.select_related('actor', 'target_user').filter(
                actor__role='security'
            ).order_by('-created_at')[:10]

            data = {
                'role': 'admin',
                'users': {
                    'total':         total_users,
                    'security':      security_count,
                    'vehicle_owner': vehicle_owner_count,
                    'active':        active_users,
                    'disabled':      disabled_users,
                },
                'vehicles': {
                    'total':        total_vehicles,
                    'authorized':   authorized_vehicles,
                    'unauthorized': unauthorized_vehicles,
                    'by_type':      vehicles_by_type,
                },
                'registrations': {
                    'pending':  pending_registrations,
                    'accepted': accepted_registrations,
                    'rejected': rejected_registrations,
                    'total':    pending_registrations + accepted_registrations + rejected_registrations,
                },
                'owners': {
                    'student':  owners_active_by_type.get('student', 0),
                    'employee': owners_active_by_type.get('employee', 0),
                    'fetcher':  owners_active_by_type.get('fetcher', 0),
                    'visitor':  owners_active_by_type.get('visitor', 0),
                    'disabled': owners_disabled,
                    'archived': u_agg['owners_archived'],
                    'banned':   u_agg['owners_banned'],
                    'total':    vehicle_owner_count,
                },
                'suppliers': {
                    'active': active_suppliers,
                },
                'scans': {
                    'today':            today_scans,
                    'week':             week_scans,
                    'authorized_today': authorized_today,
                    'authorized_week':  authorized_week,
                    'registered_today': registered_today,
                    'visitor_today':    visitor_today,
                    'denied_today':     denied_today,
                    'unknown_today':    unknown_today,
                    'today_by_status':  today_by_status,
                },
                'violations': {
                    'open':        open_violations,
                    'fee_imposed': fee_imposed,
                    'by_type':     violations_by_type,
                },
                'visitor_passes': {
                    'active_today': active_passes,
                },
                'day_distribution': day_distribution,
                'day_registrations': day_registrations,
                'recent_activity': {
                    'admin':    AuditLogSerializer(recent_admin_logs, many=True).data,
                    'security': AuditLogSerializer(recent_security_logs, many=True).data,
                },
            }
        else:
            # All five figures in one pass instead of five round-trips.
            from django.db.models import Q
            my_agg = AccessLog.objects.filter(scanned_by=user).aggregate(
                total=Count('id'),
                today=Count('id', filter=Q(scanned_at__gte=today_start, scanned_at__lt=today_end)),
                week=Count('id', filter=Q(scanned_at__gte=week_start)),
                authorized=Count('id', filter=Q(scanned_at__gte=today_start, scanned_at__lt=today_end,
                                                status='authorized')),
                denied=Count('id', filter=Q(scanned_at__gte=today_start, scanned_at__lt=today_end,
                                            status__in=['denied', 'wrong_day'])),
            )
            my_scans_today = my_agg['today']
            my_scans_week  = my_agg['week']
            my_total_scans = my_agg['total']
            my_authorized  = my_agg['authorized']
            my_denied      = my_agg['denied']

            # select_related: the serializer reads scanned_by / on_duty_guard /
            # vehicle.user, which would otherwise fire ~3 extra queries per row.
            my_access_logs = (
                AccessLog.objects
                .select_related('scanned_by', 'on_duty_guard', 'vehicle', 'vehicle__user')
                .filter(scanned_by=user)
                .order_by('-scanned_at')[:10]
            )

            from scanning.serializers import AccessLogSerializer
            data = {
                'role': 'security',
                'scans': {
                    'today':            my_scans_today,
                    'week':             my_scans_week,
                    'total':            my_total_scans,
                    'authorized_today': my_authorized,
                    'denied_today':     my_denied,
                },
                'recent_scans': AccessLogSerializer(my_access_logs, many=True).data,
            }

        return Response(data)


# ──────────────────────────────────────────────
#  Audit Log Views
# ──────────────────────────────────────────────

def _apply_created_at_range(qs, date_from, date_to):
    """Inclusive local-date range filter on AuditLog.created_at."""
    return filter_local_date_range(qs, 'created_at', date_from, date_to)


class AuditLogListView(generics.ListAPIView):
    """List audit logs - admin only.

    This lists administrative actions only. Vehicle-owner gate movement is
    deliberately not recorded here (see AuditLog's docstring); the operational
    gate history lives in scanning.AccessLog.
    """
    serializer_class   = AuditLogSerializer
    permission_classes = [IsAdminRole]
    pagination_class   = StandardResultsSetPagination

    def get_queryset(self):
        return _filter_audit_logs(self.request)[0]


def _filter_audit_logs(request):
    """Apply the same filters the Audit Log UI uses.

    Returns (ordered_queryset, filters_desc) so the Excel and PDF exports stay
    identical to what the operator sees on screen.
    """
    qs = AuditLog.objects.select_related('actor', 'target_user').all()
    action    = request.query_params.get('action', '').strip()
    date_from = request.query_params.get('date_from', '').strip()
    date_to   = request.query_params.get('date_to', '').strip()
    search    = request.query_params.get('search', '').strip()
    if action:
        qs = qs.filter(action=action)
    qs = _apply_created_at_range(qs, date_from, date_to)
    if search:
        qs = qs.filter(
            Q(actor__user_code__icontains=search) |
            Q(actor__full_name__icontains=search) |
            Q(actor__email__icontains=search) |
            Q(details__icontains=search)
        )

    action_labels = dict(AuditLog.Action.choices)
    filters_desc = []
    if action:
        filters_desc.append(f"Action: {action_labels.get(action, action)}")
    if date_from or date_to:
        filters_desc.append(f"Period: {date_from or 'start'} to {date_to or 'today'}")
    if search:
        filters_desc.append(f"Search: '{search}'")
    return qs.order_by('-created_at'), filters_desc


AUDIT_REPORT_HEADERS = ['#', 'Date & Time', 'Actor', 'Role', 'Action', 'Details']


def _audit_report_rows(qs):
    from django.utils import timezone as tz
    action_labels = dict(AuditLog.Action.choices)
    rows = []
    for i, log in enumerate(qs, start=1):
        actor = log.actor.full_name if log.actor else 'System'
        role  = (log.actor.role if log.actor else '').replace('_', ' ').title()
        rows.append([
            i,
            tz.localtime(log.created_at).strftime('%b %d, %Y %I:%M:%S %p'),
            actor, role,
            action_labels.get(log.action, log.action),
            log.details or '',
        ])
    return rows


class AuditLogExportView(APIView):
    """Download the (filtered) audit log as a branded Excel report — admin only."""
    permission_classes = [IsAdminRole]

    def get(self, request):
        from django.utils import timezone as tz
        from report_utils import branded_excel_response, report_filename
        qs, filters_desc = _filter_audit_logs(request)
        rows = _audit_report_rows(qs[:5000])
        subtitle = (f"Generated {tz.localtime().strftime('%B %d, %Y %I:%M %p')} "
                    f"by {getattr(request.user, 'full_name', '')} · "
                    + ('; '.join(filters_desc) if filters_desc else 'All records')
                    + f" · {len(rows)} entries")
        return branded_excel_response(
            filename=report_filename('Audit Log Report', 'xlsx'),
            sheet_title='Audit Log',
            report_title='Audit Log Report',
            subtitle=subtitle,
            headers=AUDIT_REPORT_HEADERS,
            rows=rows,
            col_widths=[5, 21, 24, 12, 20, 95],
        )


class AuditLogPdfExportView(APIView):
    """Download the (filtered) audit log as a branded PDF report — admin only."""
    permission_classes = [IsAdminRole]

    def get(self, request):
        from django.utils import timezone as tz
        from report_utils import branded_pdf_response, report_filename
        qs, filters_desc = _filter_audit_logs(request)
        rows = _audit_report_rows(qs[:5000])
        subtitle = (('; '.join(filters_desc) if filters_desc else 'All records')
                    + f" · {len(rows)} entries")
        return branded_pdf_response(
            filename=report_filename('Audit Log Report', 'pdf'),
            report_title='Audit Log Report',
            subtitle=subtitle,
            generated_by=getattr(request.user, 'full_name', ''),
            headers=AUDIT_REPORT_HEADERS,
            rows=rows,
            # Date & Time needs 91pt but only had 86pt, so every single row
            # wrapped to two lines — doubling the height of the whole report.
            # Actor was using 64pt of its 109pt, so 5mm moves across and both
            # fit comfortably. Total is unchanged at 267mm (the printable width).
            col_widths_mm=[10, 39, 37, 22, 38, 121],
        )


# ── System Backup & Restore ─────────────────────────────
# The app list, the exclusions and the on-disk layout live in backup_utils so
# the scheduled job (vehicles.tasks.auto_backup) and the buttons on this page
# produce exactly the same kind of file. Re-exported here because tests and
# other modules have imported these names from this module since before that
# helper existed.
from .backup_utils import (                                        # noqa: E402
    AUTO_PREFIX, BACKUP_APPS, BACKUP_EXCLUDE, MANUAL_PREFIX, SAFETY_PREFIX,
    dump_backup, list_backups, prune_backups, safe_path, stamp, write_backup,
)


class SystemBackupView(APIView):
    """Download a JSON snapshot of all application data — admin (CDSO) only.

    Step-up protected: the file contains every account, plate and registration
    in the system, so one click on a borrowed session is a full data breach.
    `step_up_on_read` opts this GET into the check, which otherwise exempts
    safe methods.
    """
    permission_classes = [IsAdminRole, HasRecentTwoFactor]
    step_up_on_read = True

    def get(self, request):
        from django.http import HttpResponse
        from vehicles.models import SystemSettings

        payload = dump_backup()

        # Keep a copy on the server as well. A manual download is the moment the
        # data was known-good enough for someone to want it saved, and keeping
        # it means the restore list is not empty on a system where the automatic
        # schedule has only just been switched on. Rotated by the same keep count
        # as the automatic ones, so repeated downloads cannot fill the disk. A
        # disk problem here must not cost the admin the download they asked for.
        try:
            write_backup(MANUAL_PREFIX, payload)
            prune_backups(SystemSettings.get().auto_backup_keep)
        except OSError:
            logger.warning("Could not keep a server-side copy of the manual backup", exc_info=True)

        response = HttpResponse(payload, content_type='application/json')
        response['Content-Disposition'] = (
            f'attachment; filename="slc-vms-backup-{stamp(seconds=False)}.json"')
        log_action(request, AuditLog.Action.RECORD_UPDATED, details='System backup downloaded')
        return response


class SystemBackupListView(APIView):
    """The backup files sitting on the server — admin (CDSO) only.

    Covers the automatic backups, the copy kept on each manual download, and the
    pre-restore safety snapshots. This reads filenames and sizes, not the data
    inside them, so it needs admin but no step-up; downloading one of them does.
    """
    permission_classes = [IsAdminRole]

    def get(self, request):
        from vehicles.models import SystemSettings

        cfg = SystemSettings.get()
        return Response({
            'backups': list_backups(),
            'auto_backup_frequency': cfg.auto_backup_frequency,
            'auto_backup_keep': cfg.auto_backup_keep,
        })


class SystemBackupFileView(APIView):
    """Download or delete one saved backup file — admin (CDSO) only.

    Same step-up as taking a fresh backup, and for the same reason: the file
    holds every account and plate in the system whether it was written a minute
    ago or last term.
    """
    permission_classes = [IsAdminRole, HasRecentTwoFactor]
    step_up_on_read = True

    def get(self, request, name):
        from django.http import FileResponse

        path = safe_path(name)
        if not path:
            return Response({'error': 'Backup file not found.'},
                            status=status.HTTP_404_NOT_FOUND)
        log_action(request, AuditLog.Action.RECORD_UPDATED,
                   details=f'Saved backup downloaded ({name})')
        return FileResponse(open(path, 'rb'), as_attachment=True,
                            filename=name, content_type='application/json')

    def delete(self, request, name):
        import os

        path = safe_path(name)
        if not path:
            return Response({'error': 'Backup file not found.'},
                            status=status.HTTP_404_NOT_FOUND)
        try:
            os.remove(path)
        except OSError as exc:
            return Response({'error': f'Could not delete the file. ({exc})'},
                            status=status.HTTP_400_BAD_REQUEST)
        log_action(request, AuditLog.Action.RECORD_UPDATED,
                   details=f'Saved backup deleted ({name})')
        return Response({'deleted': name}, status=status.HTTP_200_OK)


class SystemRestoreView(APIView):
    """Merge a JSON backup into the live data — admin (CDSO) only.

    Takes either an uploaded file (`file`) or the name of a backup already on
    the server (`filename`) — an automatic one, or a pre-restore snapshot from
    an earlier attempt. Both paths run the same validation, the same safety
    snapshot and the same atomic load; a saved file simply skips the download /
    re-upload round trip.

    "Restore" overstates it, and the difference matters. `loaddata` writes each
    record by primary key: rows in the file overwrite the matching live rows and
    missing ones are inserted, but nothing is ever deleted. An account or
    vehicle created after the backup was taken therefore survives the restore
    untouched. This is a merge, not a rewind to the backup's date, and the
    confirmation dialog says so in those words.

    Making it a true rewind would mean emptying every backed-up table first.
    That is technically possible — the excluded ML tables carry no foreign keys
    into these, so nothing would cascade unnoticed — but it turns a recoverable
    operation into one that destroys everything created since the file was
    written, including the row of the admin performing it. It is not a change to
    make quietly; the honest description above is the safer half of the trade.

    Safety measures: admin-only, a fresh two-factor step-up, file validated as a
    JSON fixture, an automatic pre-restore snapshot of current data saved to
    disk, and a load that runs in a single transaction and rolls back completely
    on any error.

    The step-up is the one that matters most on this endpoint: a restore
    overwrites live data wholesale, and a crafted fixture can rewrite the admin
    account itself.
    """
    permission_classes = [IsAdminRole, HasRecentTwoFactor]

    def post(self, request):
        import json, os, tempfile
        from django.core.management import call_command
        from django.db import transaction

        upload = request.FILES.get('file')
        filename = (request.data.get('filename') or '').strip()

        if upload:
            source = upload.name
            if upload.size > 50 * 1024 * 1024:
                return Response({'error': 'Backup file is too large (max 50 MB).'},
                                status=status.HTTP_400_BAD_REQUEST)
            raw = upload.read()
        elif filename:
            path = safe_path(filename)
            if not path:
                return Response({'error': 'Saved backup not found.'},
                                status=status.HTTP_404_NOT_FOUND)
            source = filename
            with open(path, 'rb') as fh:
                raw = fh.read()
        else:
            return Response({'error': 'No backup file provided.'},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            parsed = json.loads(raw.decode('utf-8'))
            if not isinstance(parsed, list):
                raise ValueError('not a fixture list')
        except Exception:
            return Response({'error': 'Invalid backup file — expected a JSON data fixture.'},
                            status=status.HTTP_400_BAD_REQUEST)

        # 1) Auto safety snapshot of current data before overwriting anything.
        safety_name, _ = write_backup(SAFETY_PREFIX)

        # 2) Load the fixture atomically (rolls back on any error).
        tmp = tempfile.NamedTemporaryFile('wb', suffix='.json', delete=False)
        try:
            tmp.write(raw)
            tmp.close()
            with transaction.atomic():
                call_command('loaddata', tmp.name, verbosity=0)
        except Exception as exc:
            return Response(
                {'error': f'Restore failed and was rolled back. No changes were applied. ({exc})',
                 'safety_backup': safety_name},
                status=status.HTTP_400_BAD_REQUEST,
            )
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

        log_action(request, AuditLog.Action.RECORD_UPDATED,
                   details=f'System restore from backup ({len(parsed)} records, source: {source})')
        return Response({'restored': len(parsed), 'safety_backup': safety_name},
                        status=status.HTTP_200_OK)


class AuditLogClearView(APIView):
    """Delete all audit log records — admin only."""
    permission_classes = [IsAdminRole]

    def delete(self, request):
        deleted_count, _ = AuditLog.objects.all().delete()
        return Response({'deleted': deleted_count}, status=status.HTTP_200_OK)


class AuditLogStatsView(APIView):
    """Get audit log statistics."""
    permission_classes = [IsAdminRole]

    def get(self, request):
        from django.db.models import Count
        from django.utils import timezone
        from datetime import timedelta
        
        today = timezone.localdate()
        week_ago = today - timedelta(days=7)
        
        stats = {
            'total_logs': AuditLog.objects.count(),
            'today_logs': AuditLog.objects.filter(
                created_at__gte=day_start(today), created_at__lt=day_end(today)).count(),
            'week_logs': AuditLog.objects.filter(created_at__gte=day_start(week_ago)).count(),
            'by_action': AuditLog.objects.values('action').annotate(count=Count('action')),
        }
        return Response(stats)


# ──────────────────────────────────────────────
#  Password Change (any authenticated user)
# ──────────────────────────────────────────────

class ChangePasswordView(APIView):
    """Allow any authenticated user to change their own password.
    Clears the must_change_password flag after a successful change.

    Step-up protected for accounts that carry two-factor: the current password
    alone is not enough, because a session left open on an unlocked machine
    already has it. Guards, who carry no second factor, are unaffected — and so
    is anyone still completing their first-login enrollment, since a confirmed
    device is what arms the check.
    """
    permission_classes = [permissions.IsAuthenticated, HasRecentTwoFactor]

    def post(self, request):
        user = request.user
        current_password = request.data.get('current_password', '').strip()
        new_password = request.data.get('new_password', '').strip()
        confirm_password = request.data.get('confirm_password', '').strip()

        if not current_password:
            return Response({'error': 'Current password is required.'}, status=status.HTTP_400_BAD_REQUEST)
        if not user.check_password(current_password):
            return Response({'error': 'Current password is incorrect.'}, status=status.HTTP_400_BAD_REQUEST)
        if not new_password:
            return Response({'error': 'New password is required.'}, status=status.HTTP_400_BAD_REQUEST)
        if new_password != confirm_password:
            return Response({'error': 'New passwords do not match.'}, status=status.HTTP_400_BAD_REQUEST)
        if current_password == new_password:
            return Response({'error': 'New password must be different from current password.'}, status=status.HTTP_400_BAD_REQUEST)

        # Validate strength
        import re
        errors = []
        if len(new_password) < 8:
            errors.append('Password must be at least 8 characters.')
        if not re.search(r'[A-Z]', new_password):
            errors.append('Password must contain at least one uppercase letter.')
        if not re.search(r'[a-z]', new_password):
            errors.append('Password must contain at least one lowercase letter.')
        if not re.search(r'[0-9]', new_password):
            errors.append('Password must contain at least one number.')
        if not re.search(r'[!@#$%^&*()_+\-=\[\]{};\'\"\\|,.<>\/?]', new_password):
            errors.append('Password must contain at least one special character.')
        if errors:
            return Response({'errors': errors}, status=status.HTTP_400_BAD_REQUEST)

        # Read before the flag is cleared: a user still carrying
        # must_change_password is replacing the temporary password they were
        # issued, which is the moment the account becomes theirs — that gets the
        # welcome. Every later change gets the security notice instead.
        was_first_change = user.must_change_password

        user.set_password(new_password)
        user.must_change_password = False
        user.save(update_fields=['password', 'must_change_password'])

        notify_password_set(user, was_first_change)
        return Response({'message': 'Password changed successfully.'})


# ──────────────────────────────────────────────
#  Vehicle Owner: own registration record
# ──────────────────────────────────────────────

class MyRegistrationView(APIView):
    """Returns the VehicleRegistration record for the logged-in vehicle owner."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if request.user.role != 'vehicle_owner':
            return Response({'error': 'Only vehicle owners can access this endpoint.'}, status=status.HTTP_403_FORBIDDEN)
        from vehicles.models import VehicleRegistration
        from vehicles.serializers import VehicleRegistrationSerializer
        registration = (
            VehicleRegistration.objects
            .filter(Q(user=request.user) | Q(email=request.user.email), status='accepted')
            .order_by('-reviewed_at')
            .first()
        )
        if not registration:
            return Response({'error': 'No accepted registration found for this account.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(VehicleRegistrationSerializer(registration).data)


class MyPlateSwapView(APIView):
    """One-time, self-service replacement of a conduction number with the real
    plate, for owners whose brand-new car has since received its plate. Updates
    both the Vehicle and the accepted registration, and can only happen once —
    afterwards the account has a plate and no conduction number."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        if request.user.role != 'vehicle_owner':
            return Response({'error': 'Only vehicle owners can update their plate.'},
                            status=status.HTTP_403_FORBIDDEN)

        from django.db import transaction
        from vehicles.models import VehicleRegistration, _normalize_plate
        from vehicles.views import _plate_conflict
        from scanning.ml.validator import is_valid_ph_plate

        new_plate = _normalize_plate(request.data.get('plate_number') or '')
        if not new_plate:
            return Response({'plate_number': 'A plate number is required.'}, status=status.HTTP_400_BAD_REQUEST)
        if not is_valid_ph_plate(new_plate):
            return Response({'plate_number': 'Enter a valid Philippine plate number.'}, status=status.HTTP_400_BAD_REQUEST)

        registration = (
            VehicleRegistration.objects
            .filter(Q(user=request.user) | Q(email=request.user.email), status='accepted')
            .order_by('-reviewed_at')
            .first()
        )
        if not registration:
            return Response({'error': 'No accepted registration found for this account.'},
                            status=status.HTTP_404_NOT_FOUND)
        # Only a conduction-only registration is eligible; once a real plate is set
        # the option is spent and must not run again.
        if registration.plate_number or not registration.conduction_number:
            return Response({'error': 'This account already has a plate number on file.'},
                            status=status.HTTP_400_BAD_REQUEST)

        # The new plate must not belong to anyone else (active registration or owned vehicle).
        active = (VehicleRegistration.objects
                  .filter(status__in=['pending', 'accepted']).exclude(pk=registration.pk))
        conflict = _plate_conflict(new_plate, active)
        if conflict:
            return Response({'plate_number': conflict}, status=status.HTTP_400_BAD_REQUEST)

        old_conduction = registration.conduction_number
        with transaction.atomic():
            vehicle = registration.vehicle
            if vehicle is not None:
                vehicle.plate_number = new_plate
                vehicle.conduction_number = ''
                vehicle.save(update_fields=['plate_number', 'conduction_number'])
            registration.plate_number = new_plate
            registration.conduction_number = ''
            registration.save()  # normalizes and persists

        AuditLog.objects.create(
            actor=request.user,
            action=AuditLog.Action.USER_UPDATED,
            target_user=request.user,
            details=(f"Plate number set by owner | Conduction {old_conduction} -> Plate {new_plate} | "
                     f"{request.user.email}"),
        )
        return Response({'plate_number': new_plate,
                         'message': 'Your plate number has been saved and verified.'})


# ──────────────────────────────────────────────
#  Guard QR Login (passwordless, for gate stations)
# ──────────────────────────────────────────────

class GuardQrLoginView(APIView):
    """
    Authenticate a security guard by scanning their QR badge.
    QR format: SLC-GUARD:{user_code}:{guard_qr_secret}
    Logs out any previously active guard session implicitly — the new JWT supersedes the old one.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        import uuid as _uuid
        from rest_framework_simplejwt.tokens import RefreshToken

        qr_data = (request.data.get('qr_data') or '').strip()
        if not qr_data.startswith('SLC-GUARD:'):
            return Response({'detail': 'Invalid QR code.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            _, user_code, secret_str = qr_data.split(':', 2)
            secret = _uuid.UUID(secret_str)
        except (ValueError, AttributeError):
            return Response({'detail': 'Malformed QR code.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = User.objects.get(user_code=user_code, guard_qr_secret=secret, role='security', is_active=True)
        except User.DoesNotExist:
            return Response({'detail': 'QR code not recognised or guard account is disabled.'}, status=status.HTTP_401_UNAUTHORIZED)

        # QR login is passwordless — refuse it until the guard has completed
        # their first credentials login and replaced the temporary password.
        if user.must_change_password:
            return Response(
                {'detail': 'QR login is disabled until you sign in with your credentials and change your temporary password.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        refresh = RefreshToken.for_user(user)
        refresh['role'] = user.role
        refresh['full_name'] = user.full_name
        refresh['email'] = user.email
        refresh['must_change_password'] = user.must_change_password

        AuditLog.objects.create(
            actor=user,
            action=AuditLog.Action.GUARD_LOGIN,
            details=f'Guard QR login: {user.full_name} ({user.user_code})',
            ip_address=get_client_ip(request),
        )

        return Response({
            'access':  str(refresh.access_token),
            'refresh': str(refresh),
            'user': {
                'id':                 user.id,
                'user_code':          user.user_code,
                'full_name':          user.full_name,
                'email':              user.email,
                'role':               user.role,
                'must_change_password': user.must_change_password,
                'photo_url': request.build_absolute_uri(user.photo.url) if user.photo else None,
            },
        })


class GuardQrCodeView(APIView):
    """
    Generate (or retrieve) a guard's QR secret.
    Admin: GET /accounts/guard-qr/{pk}/ — returns the QR payload string for that guard.
    The guard themselves can also hit this endpoint to get their own code.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        import uuid as _uuid

        if request.user.role == 'admin':
            user = get_object_or_404(User, pk=pk, role='security')
        elif request.user.pk == int(pk) and request.user.role == 'security':
            user = request.user
        else:
            return Response({'detail': 'Not authorised.'}, status=status.HTTP_403_FORBIDDEN)

        if user.must_change_password:
            return Response(
                {'detail': 'QR badge is locked — this guard must log in with their credentials and change their temporary password first.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        if not user.guard_qr_secret:
            user.guard_qr_secret = _uuid.uuid4()
            User.objects.filter(pk=user.pk).update(guard_qr_secret=user.guard_qr_secret)

        qr_payload = f'SLC-GUARD:{user.user_code}:{user.guard_qr_secret}'
        return Response({
            'user_code':   user.user_code,
            'full_name':   user.full_name,
            'qr_payload':  qr_payload,
            'photo_url':   request.build_absolute_uri(user.photo.url) if user.photo else None,
        })


class PasswordResetRequestView(APIView):
    """Step 1: accept an email, generate a token, send a reset link."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.http import urlsafe_base64_encode
        from django.utils.encoding import force_bytes
        from django.core.mail import send_mail
        from django.conf import settings as django_settings

        email = request.data.get('email', '').strip().lower()
        if not email:
            return Response({'error': 'Email is required.'}, status=status.HTTP_400_BAD_REQUEST)

        # Always return the same message to avoid leaking which emails exist
        SAFE_MSG = 'If an account with that email exists, a password reset link has been sent.'

        try:
            user = User.objects.get(email__iexact=email, is_active=True, is_archived=False)
        except User.DoesNotExist:
            return Response({'message': SAFE_MSG})

        token = default_token_generator.make_token(user)
        uid   = urlsafe_base64_encode(force_bytes(user.pk))
        # PUBLIC_SITE_URL: a reset link built from the campus half's LAN address
        # is unreachable for anyone resetting their password from off campus.
        frontend_url = getattr(django_settings, 'PUBLIC_SITE_URL', '') or 'http://localhost:5173'
        reset_link   = f"{frontend_url}/reset-password?uid={uid}&token={token}"

        html_message = f"""
        <html>
          <body style="font-family:Arial,sans-serif;color:#1A1D2E;background:#F0F2F7;padding:20px;margin:0;">
            <div style="max-width:540px;margin:0 auto;background:#fff;border-radius:12px;border-top:4px solid #2A2B61;box-shadow:0 4px 20px rgba(0,0,0,.08);overflow:hidden;">
              <div style="padding:28px 32px 24px;">
                <h2 style="color:#2A2B61;margin:0 0 8px;">Password Reset Request</h2>
                <p style="color:#5A5F72;font-size:14px;margin:0 0 20px;">
                  We received a request to reset the password for your SLC Smart Parking and Vehicle Verification System account.
                </p>
                <p style="margin:0 0 8px;">Hello, <strong>{user.full_name or user.email}</strong>,</p>
                <p style="color:#5A5F72;font-size:14px;margin:0 0 24px;">
                  Click the button below to set a new password. This link expires in <strong>1 hour</strong>.
                </p>
                <div style="text-align:center;margin:0 0 24px;">
                  <a href="{reset_link}"
                     style="display:inline-block;padding:13px 32px;background:#2A2B61;color:#fff;
                            border-radius:10px;font-size:15px;font-weight:600;text-decoration:none;">
                    Reset My Password
                  </a>
                </div>
                <p style="color:#9CA3B0;font-size:12px;margin:0 0 8px;">
                  If the button doesn't work, copy and paste this link into your browser:
                </p>
                <p style="word-break:break-all;font-size:12px;color:#2A2B61;margin:0 0 24px;">{reset_link}</p>
                <p style="color:#9CA3B0;font-size:12px;margin:0;">
                  If you did not request a password reset, you can safely ignore this email.
                  Your password will not change.
                </p>
              </div>
              <div style="background:#F8FAFC;border-top:1px solid #E2E6EE;padding:14px 32px;text-align:center;">
                <p style="font-size:12px;color:#7C80A3;margin:0;">Saint Louis College Smart Parking and Vehicle Verification System</p>
                <p style="font-size:11px;color:#B0B4C7;margin:4px 0 0;">This is an automated message. Please do not reply.</p>
              </div>
            </div>
          </body>
        </html>
        """
        
        # fail_silently=False + an explicit log. The response stays SAFE_MSG
        # either way — it must not reveal whether the address exists — but the
        # send itself must not fail invisibly: with fail_silently=True an
        # expired SMTP credential produced a cheerful "a reset link has been
        # sent" for every request while nothing was delivered and nothing was
        # written to the log, so there was no way to tell the two apart.
        try:
            send_mail(
                subject='SPVVS — Password Reset',
                message=(
                    f"Hello {user.full_name or user.email},\n\n"
                    f"Reset your password by visiting:\n{reset_link}\n\n"
                    f"This link expires in 1 hour.\n\n"
                    f"If you did not request this, ignore this email."
                ),
                from_email=django_settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                html_message=html_message,
                fail_silently=False,
            )
        except Exception:
            logger.exception(
                "Failed to send the password-reset email to user %s — they were "
                "told a link was sent, but none was delivered.", user.pk,
            )

        return Response({'message': SAFE_MSG})


class PasswordResetConfirmView(APIView):
    """Step 2: validate the token and set the new password."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        import re
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.http import urlsafe_base64_decode
        from django.utils.encoding import force_str

        uid             = request.data.get('uid', '').strip()
        token           = request.data.get('token', '').strip()
        new_password    = request.data.get('new_password', '').strip()
        confirm_password = request.data.get('confirm_password', '').strip()

        if not all([uid, token, new_password, confirm_password]):
            return Response({'error': 'All fields are required.'}, status=status.HTTP_400_BAD_REQUEST)

        if new_password != confirm_password:
            return Response({'error': 'Passwords do not match.'}, status=status.HTTP_400_BAD_REQUEST)

        # Decode UID and fetch user
        try:
            pk   = force_str(urlsafe_base64_decode(uid))
            user = User.objects.get(pk=pk)
        except (User.DoesNotExist, ValueError, TypeError, Exception):
            return Response({'error': 'Invalid reset link.'}, status=status.HTTP_400_BAD_REQUEST)

        if not default_token_generator.check_token(user, token):
            return Response(
                {'error': 'This reset link has expired or is invalid. Please request a new one.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if user.check_password(new_password):
            return Response(
                {'error': 'New password must be different from your current password.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate password strength (same rules as ChangePasswordView)
        errors = []
        if len(new_password) < 8:
            errors.append('Password must be at least 8 characters.')
        if not re.search(r'[A-Z]', new_password):
            errors.append('Password must contain at least one uppercase letter.')
        if not re.search(r'[a-z]', new_password):
            errors.append('Password must contain at least one lowercase letter.')
        if not re.search(r'[0-9]', new_password):
            errors.append('Password must contain at least one number.')
        if not re.search(r'[!@#$%^&*()_+\-=\[\]{};\'\"\\|,.<>\/?]', new_password):
            errors.append('Password must contain at least one special character.')
        if errors:
            return Response({'errors': errors}, status=status.HTTP_400_BAD_REQUEST)

        # Same split as ChangePasswordView. A brand-new user who never logged in
        # with their temporary password and used "forgot password" instead still
        # arrives here for their first change, so they get the welcome too.
        was_first_change = user.must_change_password

        user.set_password(new_password)
        user.must_change_password = False
        # Demand the second factor on the next login. A reset proves control of
        # the mailbox, nothing more — and the mailbox is exactly what an attacker
        # takes first. Guards are skipped because they carry no second factor to
        # ask for; the flag would sit unread and never be cleared.
        from . import twofa
        user.must_verify_2fa = twofa.requires_2fa(user)
        user.save(update_fields=['password', 'must_change_password', 'must_verify_2fa'])

        notify_password_set(user, was_first_change)

        return Response({
            'message': 'Password reset successfully. You can now log in with your new password.',
            'role': user.role,
            'twofa_required_next_login': user.must_verify_2fa,
        })


# ──────────────────────────────────────────────
#  Guard QR Login & Shift Management
# ──────────────────────────────────────────────

class QRLoginView(APIView):
    """Guard scans their QR badge — clocks out previous shift, creates new shift, issues JWT."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        from scanning.models import GuardShift
        from scanning.serializers import GuardShiftSerializer
        from rest_framework_simplejwt.tokens import RefreshToken

        token_str  = (request.data.get('qr_token') or '').strip()
        gate_param = (request.data.get('gate')     or '').strip()

        if not token_str:
            return Response({'error': 'qr_token is required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            guard = User.objects.get(qr_token=token_str, role='security', is_active=True)
        except (User.DoesNotExist, ValueError):
            return Response({'error': 'Invalid or unrecognized QR code.'}, status=status.HTTP_401_UNAUTHORIZED)

        # QR login is passwordless — refuse it until the guard has completed
        # their first credentials login and replaced the temporary password.
        if guard.must_change_password:
            return Response(
                {'error': 'QR login is disabled until you sign in with your credentials and change your temporary password.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        from scanning.models import Gate
        valid_gates = Gate.active_ids()
        gate = gate_param if gate_param in valid_gates else guard.gate_assignment
        if not gate or gate not in valid_gates:
            return Response(
                {'error': 'Gate selection required. Please choose a gate before scanning.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Persist the shift gate on the guard's profile. Server-side scan
        # attribution (manual entry, override, exit, HTTP/WS scans) reads
        # request.user.gate_assignment to tag each log; without this it stays
        # None and those scans fall to the orphan 'main' gate, invisible in
        # every gate's Audit Log.
        if guard.gate_assignment != gate:
            User.objects.filter(pk=guard.pk).update(gate_assignment=gate)
            guard.gate_assignment = gate

        now = timezone.now()

        # Clock out whoever is currently active at this gate (the previous guard)
        GuardShift.objects.filter(gate=gate, clocked_out_at__isnull=True).update(
            clocked_out_at=now,
            clocked_out_by=guard,
        )

        shift   = GuardShift.objects.create(guard=guard, gate=gate)
        refresh = RefreshToken.for_user(guard)

        return Response({
            'access':  str(refresh.access_token),
            'refresh': str(refresh),
            'user': {
                'id':                   guard.id,
                'user_code':            guard.user_code,
                'full_name':            guard.full_name,
                'email':                guard.email,
                'role':                 guard.role,
                'gate_assignment':      gate,   # current shift gate (also persisted above)
                'must_change_password': guard.must_change_password,
            },
            'shift': GuardShiftSerializer(shift).data,
        })


class GuardCredentialLoginView(APIView):
    """Guard logs in with email + password at the gate station — clocks out the
    previous shift at the selected gate, creates a new shift, issues JWT.
    Only accounts with role='security' may use this endpoint; everyone else
    logs in through the regular /api/auth/login/ endpoint."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        from scanning.models import GuardShift
        from scanning.serializers import GuardShiftSerializer
        from rest_framework_simplejwt.tokens import RefreshToken

        email      = (request.data.get('email')    or '').strip().lower()
        password   = request.data.get('password')  or ''
        gate_param = (request.data.get('gate')     or '').strip()

        if not email or not password:
            return Response({'error': 'Email and password are required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            guard = User.objects.get(email__iexact=email, role='security', is_archived=False)
        except User.DoesNotExist:
            return Response({'error': 'Incorrect email or password.'}, status=status.HTTP_401_UNAUTHORIZED)

        if not guard.check_password(password):
            return Response({'error': 'Incorrect email or password.'}, status=status.HTTP_401_UNAUTHORIZED)

        if not guard.is_active:
            return Response(
                {'error': 'Your account has been disabled. Please contact the administrator.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        from scanning.models import Gate
        valid_gates = Gate.active_ids()
        gate = gate_param if gate_param in valid_gates else guard.gate_assignment
        if not gate or gate not in valid_gates:
            return Response(
                {'error': 'Gate selection required. Please choose a gate before logging in.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Persist the shift gate on the guard's profile. Server-side scan
        # attribution (manual entry, override, exit, HTTP/WS scans) reads
        # request.user.gate_assignment to tag each log; without this it stays
        # None and those scans fall to the orphan 'main' gate, invisible in
        # every gate's Audit Log.
        if guard.gate_assignment != gate:
            User.objects.filter(pk=guard.pk).update(gate_assignment=gate)
            guard.gate_assignment = gate

        now = timezone.now()

        # Clock out whoever is currently active at this gate (the previous guard)
        GuardShift.objects.filter(gate=gate, clocked_out_at__isnull=True).update(
            clocked_out_at=now,
            clocked_out_by=guard,
        )

        shift   = GuardShift.objects.create(guard=guard, gate=gate)
        refresh = RefreshToken.for_user(guard)

        AuditLog.objects.create(
            actor=guard,
            action=AuditLog.Action.GUARD_LOGIN,
            details=f'Guard credential login: {guard.full_name} ({guard.user_code}) at {gate}',
            ip_address=get_client_ip(request),
        )

        return Response({
            'access':  str(refresh.access_token),
            'refresh': str(refresh),
            'user': {
                'id':                   guard.id,
                'user_code':            guard.user_code,
                'full_name':            guard.full_name,
                'email':                guard.email,
                'role':                 guard.role,
                'gate_assignment':      gate,   # current shift gate (also persisted above)
                'must_change_password': guard.must_change_password,
            },
            'shift': GuardShiftSerializer(shift).data,
        })


class GuardQrAvailabilityView(APIView):
    """Public: whether a guard can log in by QR badge (i.e. has completed
    their first credentials login and password change). With ?email= the
    check is for that specific guard; without it, whether any guard can.
    The gate login page shows the QR Badge tab only when this is true.
    Returns only a boolean — no user data is exposed."""
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        email = (request.query_params.get('email') or '').strip()
        qs = User.objects.filter(role='security', is_active=True, must_change_password=False)
        if email:
            qs = qs.filter(email__iexact=email)
        return Response({'qr_available': qs.exists()})


class GuardQRView(APIView):
    """Admin only: return a guard's QR token for badge printing."""
    permission_classes = [IsAdminRole]

    def get(self, request, pk):
        guard = get_object_or_404(User, pk=pk, role='security')
        if guard.must_change_password:
            return Response(
                {'detail': 'QR badge is locked — this guard must log in with their credentials and change their temporary password first.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response({
            'id':       guard.id,
            'full_name': guard.full_name,
            'qr_token': str(guard.qr_token),
        })


class NotificationListView(APIView):
    """Admin/CDSO: notification-bell feed with unread count."""
    permission_classes = [IsAdminOrCdso]

    def get(self, request):
        qs = Notification.objects.all()
        if request.query_params.get('unread_only') in ('1', 'true'):
            qs = qs.filter(is_read=False)
        try:
            limit = min(int(request.query_params.get('limit', 30)), 100)
        except (TypeError, ValueError):
            limit = 30
        unread_count = Notification.objects.filter(is_read=False).count()
        return Response({
            'results':      NotificationSerializer(qs[:limit], many=True).data,
            'unread_count': unread_count,
        })


class NotificationMarkReadView(APIView):
    """Admin/CDSO: mark notifications read — {"ids": [...]} or {"all": true}."""
    permission_classes = [IsAdminOrCdso]

    def post(self, request):
        if request.data.get('all'):
            qs = Notification.objects.filter(is_read=False)
        else:
            ids = request.data.get('ids') or []
            if not isinstance(ids, list) or not ids:
                return Response({'error': 'Provide "ids" (list) or "all": true.'}, status=400)
            qs = Notification.objects.filter(pk__in=ids, is_read=False)
        updated = qs.update(is_read=True)
        # queryset.update() skips post_save, so tell open pages explicitly
        if updated:
            try:
                from realtime.broadcast import broadcast_change
                broadcast_change('notification', 'updated')
            except Exception:
                pass
        return Response({'updated': updated})


class NotificationClearView(APIView):
    """Admin/CDSO: clear (delete) notifications from the bell feed.
    Body: {"read_only": true} deletes only already-read items; otherwise all."""
    permission_classes = [IsAdminOrCdso]

    def post(self, request):
        qs = Notification.objects.all()
        if request.data.get('read_only'):
            qs = qs.filter(is_read=True)
        deleted, _ = qs.delete()
        if deleted:
            try:
                from realtime.broadcast import broadcast_change
                broadcast_change('notification', 'updated')
            except Exception:
                pass
        return Response({'deleted': deleted})



