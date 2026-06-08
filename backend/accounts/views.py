from rest_framework import generics, permissions, status, filters
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView
from django.shortcuts import get_object_or_404
from .models import User, AuditLog
from .serializers import (
    UserSerializer,
    UserUpdateSerializer,
    RegisterSerializer,
    AdminReplaceSerializer,
    CustomTokenObtainPairSerializer,
    AuditLogSerializer,
)


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
        qs = User.objects.exclude(role='admin').order_by('-id')
        search = self.request.query_params.get('search', '').strip()
        if search:
            qs = qs.filter(full_name__icontains=search)
            
        role = self.request.query_params.get('role', '').strip()
        if role in ['security', 'vehicle_owner']:
            qs = qs.filter(role=role)
            
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
    """Edit user details (full_name, email, role)."""
    queryset           = User.objects.all()
    serializer_class   = UserUpdateSerializer
    permission_classes = [IsAdminRole]

    def perform_update(self, serializer):
        old_user = serializer.instance
        changes = []
        for field in ['full_name', 'email', 'role']:
            old_val = getattr(old_user, field)
            new_val = serializer.validated_data.get(field, old_val)
            if old_val != new_val:
                changes.append(f"{field}: '{old_val}' → '{new_val}'")
        
        user = serializer.save()
        log_action(self.request, AuditLog.Action.USER_UPDATED, target_user=user, details='; '.join(changes))


class UserDeleteView(generics.DestroyAPIView):
    """Hard-delete a user."""
    queryset           = User.objects.all()
    serializer_class   = UserSerializer
    permission_classes = [IsAdminRole]

    def destroy(self, request, *args, **kwargs):
        user = self.get_object()
        if user.role == 'admin':
            return Response(
                {'detail': 'Cannot delete an admin from this endpoint.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        log_action(request, AuditLog.Action.USER_DELETED, target_user=user)
        user.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


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
        serializer = AdminReplaceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        old_admin = request.user
        new_admin = serializer.save()
        log_action(request, AuditLog.Action.ADMIN_REPLACED, target_user=new_admin, details=f"Replaced admin: {old_admin.email}")
        old_admin.delete()
        return Response(
            {
                'detail': 'Admin replaced successfully.',
                'user': UserSerializer(new_admin).data,
            },
            status=status.HTTP_201_CREATED,
        )


class DashboardStatsView(APIView):
    """Return dashboard stats. Admin gets full overview; security gets personal scan stats."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from django.db.models import Count, Q
        from django.utils import timezone
        from datetime import timedelta
        from scanning.models import AccessLog

        user = request.user
        today = timezone.localdate()
        week_ago = today - timedelta(days=7)

        if user.role == 'admin':
            total_users = User.objects.count()
            security_count = User.objects.filter(role='security').count()
            vehicle_owner_count = User.objects.filter(role='vehicle_owner').count()
            active_users = User.objects.filter(is_active=True).count()
            disabled_users = User.objects.filter(is_active=False).count()

            from vehicles.models import Vehicle, VehicleRegistration
            total_vehicles = Vehicle.objects.count()
            authorized_vehicles = Vehicle.objects.filter(is_authorized=True).count()
            pending_registrations = VehicleRegistration.objects.filter(status=VehicleRegistration.Status.PENDING).count()

            today_scans = AuditLog.objects.filter(action=AuditLog.Action.SCAN, created_at__date=today).count()
            week_scans = AuditLog.objects.filter(action=AuditLog.Action.SCAN, created_at__date__gte=week_ago).count()

            recent_admin_logs = AuditLog.objects.filter(actor__role='admin').order_by('-created_at')[:10]
            recent_security_logs = AuditLog.objects.filter(actor__role='security').order_by('-created_at')[:10]

            data = {
                'role': 'admin',
                'users': {
                    'total': total_users,
                    'security': security_count,
                    'vehicle_owner': vehicle_owner_count,
                    'active': active_users,
                    'disabled': disabled_users,
                },
                'vehicles': {
                    'total': total_vehicles,
                    'authorized': authorized_vehicles,
                },
                'registrations': {
                    'pending': pending_registrations,
                },
                'scans': {
                    'today': today_scans,
                    'week': week_scans,
                },
                'recent_activity': {
                    'admin': AuditLogSerializer(recent_admin_logs, many=True).data,
                    'security': AuditLogSerializer(recent_security_logs, many=True).data,
                },
            }
        else:
            my_scans_today = AuditLog.objects.filter(
                actor=user, action=AuditLog.Action.SCAN, created_at__date=today
            ).count()
            my_scans_week = AuditLog.objects.filter(
                actor=user, action=AuditLog.Action.SCAN, created_at__date__gte=week_ago
            ).count()
            my_total_scans = AuditLog.objects.filter(actor=user, action=AuditLog.Action.SCAN).count()

            my_access_logs = AccessLog.objects.filter(scanned_by=user).order_by('-scanned_at')[:10]

            from scanning.serializers import AccessLogSerializer
            data = {
                'role': 'security',
                'scans': {
                    'today': my_scans_today,
                    'week': my_scans_week,
                    'total': my_total_scans,
                },
                'recent_scans': AccessLogSerializer(my_access_logs, many=True).data,
            }

        return Response(data)


# ──────────────────────────────────────────────
#  Audit Log Views
# ──────────────────────────────────────────────

class AuditLogListView(generics.ListAPIView):
    """List audit logs - admin sees all, security sees only their own actions."""
    serializer_class   = AuditLogSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class   = StandardResultsSetPagination

    def get_queryset(self):
        if self.request.user.role == 'admin':
            qs = AuditLog.objects.all()
        else:
            qs = AuditLog.objects.filter(actor=self.request.user)
        
        # Filter by action type
        action = self.request.query_params.get('action', '').strip()
        if action:
            qs = qs.filter(action=action)
        
        # Filter by date range
        date_from = self.request.query_params.get('date_from', '').strip()
        date_to = self.request.query_params.get('date_to', '').strip()
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)
        
        return qs.order_by('-created_at')


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
            'today_logs': AuditLog.objects.filter(created_at__date=today).count(),
            'week_logs': AuditLog.objects.filter(created_at__date__gte=week_ago).count(),
            'by_action': AuditLog.objects.values('action').annotate(count=Count('action')),
        }
        return Response(stats)