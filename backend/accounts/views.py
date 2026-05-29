from rest_framework import generics, permissions, status, filters
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView
from django.shortcuts import get_object_or_404
from .models import User
from .serializers import (
    UserSerializer,
    UserUpdateSerializer,
    RegisterSerializer,
    AdminReplaceSerializer,
    CustomTokenObtainPairSerializer,
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
        user.is_active = not user.is_active
        user.save(update_fields=['is_active'])
        return Response(UserSerializer(user).data)


class AdminReplaceView(APIView):
    """Create a new admin and delete the current admin."""
    permission_classes = [IsAdminRole]

    def post(self, request):
        serializer = AdminReplaceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        old_admin = request.user
        new_admin = serializer.save()
        old_admin.delete()
        return Response(
            {
                'detail': 'Admin replaced successfully.',
                'user': UserSerializer(new_admin).data,
            },
            status=status.HTTP_201_CREATED,
        )