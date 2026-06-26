from rest_framework import generics, permissions, status, filters
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView
from django.shortcuts import get_object_or_404
from django.db.models import Q
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
        from django.db.models import Count
        from django.utils import timezone
        from datetime import timedelta
        from scanning.models import AccessLog

        user = request.user
        today = timezone.localdate()
        week_ago = today - timedelta(days=7)

        if user.role == 'admin':
            from vehicles.models import Vehicle, VehicleRegistration

            total_users         = User.objects.count()
            security_count      = User.objects.filter(role='security').count()
            vehicle_owner_count = User.objects.filter(role='vehicle_owner').count()
            active_users        = User.objects.filter(is_active=True).count()
            disabled_users      = User.objects.filter(is_active=False).count()

            total_vehicles        = Vehicle.objects.count()
            authorized_vehicles   = Vehicle.objects.filter(is_authorized=True).count()
            unauthorized_vehicles = total_vehicles - authorized_vehicles
            pending_registrations = VehicleRegistration.objects.filter(
                status=VehicleRegistration.Status.PENDING
            ).count()

            today_scans      = AccessLog.objects.filter(scanned_at__date=today).count()
            week_scans       = AccessLog.objects.filter(scanned_at__date__gte=week_ago).count()
            authorized_today = AccessLog.objects.filter(scanned_at__date=today, status='authorized').count()
            denied_today     = AccessLog.objects.filter(
                scanned_at__date=today, status__in=['denied', 'wrong_day']
            ).count()
            unknown_today    = AccessLog.objects.filter(scanned_at__date=today, status='unknown').count()

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
                },
                'registrations': {
                    'pending': pending_registrations,
                },
                'scans': {
                    'today':            today_scans,
                    'week':             week_scans,
                    'authorized_today': authorized_today,
                    'denied_today':     denied_today,
                    'unknown_today':    unknown_today,
                },
                'recent_activity': {
                    'admin':    AuditLogSerializer(recent_admin_logs, many=True).data,
                    'security': AuditLogSerializer(recent_security_logs, many=True).data,
                },
            }
        else:
            my_scans_today = AccessLog.objects.filter(scanned_by=user, scanned_at__date=today).count()
            my_scans_week  = AccessLog.objects.filter(scanned_at__date__gte=week_ago, scanned_by=user).count()
            my_total_scans = AccessLog.objects.filter(scanned_by=user).count()
            my_authorized  = AccessLog.objects.filter(scanned_by=user, scanned_at__date=today, status='authorized').count()
            my_denied      = AccessLog.objects.filter(
                scanned_by=user, scanned_at__date=today, status__in=['denied', 'wrong_day']
            ).count()

            my_access_logs = AccessLog.objects.filter(scanned_by=user).order_by('-scanned_at')[:10]

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

class AuditLogListView(generics.ListAPIView):
    """List audit logs - admin sees all, security sees only their own actions."""
    serializer_class   = AuditLogSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class   = StandardResultsSetPagination

    def get_queryset(self):
        if self.request.user.role == 'admin':
            qs = AuditLog.objects.select_related('actor', 'target_user').all()
        else:
            qs = AuditLog.objects.select_related('actor', 'target_user').filter(actor=self.request.user)

        action = self.request.query_params.get('action', '').strip()
        if action:
            qs = qs.filter(action=action)

        date_from = self.request.query_params.get('date_from', '').strip()
        date_to   = self.request.query_params.get('date_to', '').strip()
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)

        # Actor search: match user_code, full_name, or email (case-insensitive)
        search = self.request.query_params.get('search', '').strip()
        if search:
            qs = qs.filter(
                Q(actor__user_code__icontains=search) |
                Q(actor__full_name__icontains=search) |
                Q(actor__email__icontains=search)
            )

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


# ──────────────────────────────────────────────
#  Password Change (any authenticated user)
# ──────────────────────────────────────────────

class ChangePasswordView(APIView):
    """Allow any authenticated user to change their own password.
    Clears the must_change_password flag after a successful change."""
    permission_classes = [permissions.IsAuthenticated]

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

        user.set_password(new_password)
        user.must_change_password = False
        user.save(update_fields=['password', 'must_change_password'])
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
        try:
            registration = VehicleRegistration.objects.get(
                email=request.user.email,
                status='accepted'
            )
            return Response(VehicleRegistrationSerializer(registration).data)
        except VehicleRegistration.DoesNotExist:
            return Response({'error': 'No accepted registration found for this account.'}, status=status.HTTP_404_NOT_FOUND)


# ──────────────────────────────────────────────
#  Password Reset (unauthenticated)
# ──────────────────────────────────────────────

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
            user = User.objects.get(email__iexact=email, is_active=True)
        except User.DoesNotExist:
            return Response({'message': SAFE_MSG})

        token = default_token_generator.make_token(user)
        uid   = urlsafe_base64_encode(force_bytes(user.pk))
        frontend_url = getattr(django_settings, 'FRONTEND_URL', 'http://localhost:5173')
        reset_link   = f"{frontend_url}/reset-password?uid={uid}&token={token}"

        html_message = f"""
        <html>
          <body style="font-family:Arial,sans-serif;color:#1A1D2E;background:#F0F2F7;padding:20px;margin:0;">
            <div style="max-width:540px;margin:0 auto;background:#fff;border-radius:12px;border-top:4px solid #2A2B61;box-shadow:0 4px 20px rgba(0,0,0,.08);overflow:hidden;">
              <div style="padding:28px 32px 24px;">
                <h2 style="color:#2A2B61;margin:0 0 8px;">Password Reset Request</h2>
                <p style="color:#5A5F72;font-size:14px;margin:0 0 20px;">
                  We received a request to reset the password for your SLC Vehicle Management System account.
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
                <p style="font-size:12px;color:#7C80A3;margin:0;">Saint Louis College Vehicle Management System</p>
                <p style="font-size:11px;color:#B0B4C7;margin:4px 0 0;">This is an automated message. Please do not reply.</p>
              </div>
            </div>
          </body>
        </html>
        """

        send_mail(
            subject='SLC Vehicle Management — Password Reset',
            message=(
                f"Hello {user.full_name or user.email},\n\n"
                f"Reset your password by visiting:\n{reset_link}\n\n"
                f"This link expires in 1 hour.\n\n"
                f"If you did not request this, ignore this email."
            ),
            from_email=django_settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            html_message=html_message,
            fail_silently=True,
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

        user.set_password(new_password)
        user.must_change_password = False
        user.save(update_fields=['password', 'must_change_password'])

        return Response({'message': 'Password reset successfully. You can now log in with your new password.'})