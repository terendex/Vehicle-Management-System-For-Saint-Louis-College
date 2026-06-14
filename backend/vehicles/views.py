import secrets
import string
from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import Owner, Vehicle, RuleConstraint, VehicleTypeAccess
from .serializers import OwnerSerializer, VehicleSerializer, RuleConstraintSerializer, VehicleTypeAccessSerializer

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

class RuleConstraintViewSet(viewsets.ModelViewSet):
    queryset           = RuleConstraint.objects.all()
    serializer_class   = RuleConstraintSerializer
    permission_classes = [permissions.IsAuthenticated]

class VehicleTypeAccessViewSet(viewsets.ModelViewSet):
    queryset           = VehicleTypeAccess.objects.all()
    serializer_class   = VehicleTypeAccessSerializer
    permission_classes = [permissions.IsAuthenticated]


from rest_framework.views import APIView
from rest_framework import status
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from .models import RegistrationToken, VehicleRegistration
from .serializers import RegistrationTokenSerializer, VehicleRegistrationSerializer
from accounts.models import User
from .email_utils import send_acceptance_email, send_rejection_email


class IsAdminRole(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role == 'admin')


class GenerateRegistrationTokenView(APIView):
    permission_classes = [IsAdminRole]

    def post(self, request):
        registrant_type = request.data.get('registrant_type')
        expires_at_str = request.data.get('expires_at')
        if not registrant_type or not expires_at_str:
            return Response({"error": "registrant_type and expires_at are required"}, status=status.HTTP_400_BAD_REQUEST)

        # Parse the datetime-local string (e.g. "2026-06-30T10:00") and make it
        # timezone-aware so it can be safely compared with timezone.now() later.
        expires_at = parse_datetime(expires_at_str)
        if expires_at is None:
            return Response({"error": "Invalid expires_at format. Use YYYY-MM-DDTHH:MM."}, status=status.HTTP_400_BAD_REQUEST)
        if timezone.is_naive(expires_at):
            expires_at = timezone.make_aware(expires_at)

        token = RegistrationToken.objects.create(
            registrant_type=registrant_type,
            expires_at=expires_at
        )
        return Response(RegistrationTokenSerializer(token).data, status=status.HTTP_201_CREATED)


class ListRegistrationTokensView(APIView):
    permission_classes = [IsAdminRole]

    def get(self, request):
        tokens = RegistrationToken.objects.all().order_by('-created_at')
        return Response(RegistrationTokenSerializer(tokens, many=True).data)


class ToggleTokenView(APIView):
    permission_classes = [IsAdminRole]

    def post(self, request, pk):
        token = get_object_or_404(RegistrationToken, pk=pk)
        token.is_active = not token.is_active
        token.save()
        return Response(RegistrationTokenSerializer(token).data)


class DeleteTokenView(APIView):
    permission_classes = [IsAdminRole]

    def delete(self, request, pk):
        token = get_object_or_404(RegistrationToken, pk=pk)
        token.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ClearTokensView(APIView):
    permission_classes = [IsAdminRole]

    def delete(self, request):
        RegistrationToken.objects.filter(is_used=True).delete()
        RegistrationToken.objects.filter(expires_at__lt=timezone.now()).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ValidateTokenView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request, token):
        try:
            token_obj = RegistrationToken.objects.get(token=token)
            if not token_obj.is_valid:
                return Response({"error": "Token is invalid, used, expired, or disabled."}, status=status.HTTP_400_BAD_REQUEST)
            return Response({"registrant_type": token_obj.registrant_type})
        except RegistrationToken.DoesNotExist:
            return Response({"error": "Token not found"}, status=status.HTTP_404_NOT_FOUND)
        except ValueError:
            return Response({"error": "Invalid token format"}, status=status.HTTP_400_BAD_REQUEST)


class PublicRegisterVehicleView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        token_str = request.data.get('token')
        try:
            token_obj = RegistrationToken.objects.get(token=token_str)
        except (RegistrationToken.DoesNotExist, ValueError):
            return Response({"error": "Invalid token"}, status=status.HTTP_400_BAD_REQUEST)

        if not token_obj.is_valid:
            return Response({"error": "Token is invalid, used, expired, or disabled."}, status=status.HTTP_400_BAD_REQUEST)

        serializer = VehicleRegistrationSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(registrant_type=token_obj.registrant_type)
            token_obj.is_used = True
            token_obj.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class PendingRegistrationsListView(APIView):
    permission_classes = [IsAdminRole]

    def get(self, request):
        status_filter = request.query_params.get('status', VehicleRegistration.Status.PENDING)
        registrations = VehicleRegistration.objects.filter(status=status_filter).order_by('-created_at')
        return Response(VehicleRegistrationSerializer(registrations, many=True).data)


def _generate_temp_password():
    """Generate a secure temporary password that meets all strength requirements."""
    lowercase = secrets.choice(string.ascii_lowercase)
    uppercase = secrets.choice(string.ascii_uppercase)
    digit     = secrets.choice(string.digits)
    special   = secrets.choice('!@#$%^&*()_+-=')
    # Fill remaining 8 chars from full set
    alphabet  = string.ascii_letters + string.digits + '!@#$%^&*()_+-='
    rest      = [secrets.choice(alphabet) for _ in range(8)]
    password_chars = [lowercase, uppercase, digit, special] + rest
    secrets.SystemRandom().shuffle(password_chars)
    return ''.join(password_chars)


class AcceptRegistrationView(APIView):
    permission_classes = [IsAdminRole]

    def post(self, request, pk):
        registration = get_object_or_404(VehicleRegistration, pk=pk)
        if registration.status != VehicleRegistration.Status.PENDING:
            return Response({"error": "Only pending registrations can be accepted."}, status=status.HTTP_400_BAD_REQUEST)

        if User.objects.filter(email=registration.email).exists():
             return Response({"error": "User with this email already exists."}, status=status.HTTP_400_BAD_REQUEST)

        # Create user with a secure temporary password
        temp_password = _generate_temp_password()
        user = User.objects.create_user(
            email=registration.email,
            full_name=registration.full_name,
            password=temp_password,
            role='vehicle_owner',
            must_change_password=True,  # force password change on first login
        )

        # Create Owner
        owner_type = Owner.OwnerType.STUDENT if registration.registrant_type == 'student' else Owner.OwnerType.EMPLOYEE
        owner = Owner.objects.create(
            full_name=registration.full_name,
            contact=registration.contact_number,
            address=registration.address,
            owner_type=owner_type,
        )

        # Create Vehicle
        Vehicle.objects.create(
            plate_number=registration.plate_number,
            vehicle_type=registration.vehicle_type,
            color=registration.vehicle_color,
            is_authorized=True,
            owner=owner
        )

        # Auto-generate unique system ID
        padded_id = str(registration.pk).zfill(6)
        if registration.registrant_type == 'student':
            registration.system_student_id = f"SLC-STU-{padded_id}"
        else:
            registration.system_employee_id = f"SLC-EMP-{padded_id}"

        registration.status = VehicleRegistration.Status.ACCEPTED
        registration.reviewed_at = timezone.now()
        registration.save()

        # Refresh user to get generated user_code
        user.refresh_from_db()
        system_id = registration.system_student_id if registration.registrant_type == 'student' else registration.system_employee_id

        # Send acceptance email with QR code and credentials
        try:
            send_acceptance_email(registration, temp_password, user.user_code)
            email_status = 'sent'
        except Exception as e:
            # Log the failure but don't crash the acceptance
            import traceback
            print(f"[EMAIL ERROR] Failed to send acceptance email to {registration.email}: {e}")
            traceback.print_exc()
            email_status = 'failed'

        return Response({
            "message": "Registration accepted and user created.",
            "email_status": email_status,
            "account": {
                "user_code": user.user_code,
                "system_id": system_id,
                "email": registration.email,
                "full_name": registration.full_name,
                "registrant_type": registration.registrant_type,
                "plate_number": registration.plate_number,
                "vehicle_type": registration.vehicle_type,
                "vehicle_color": registration.vehicle_color,
                "contact_number": registration.contact_number,
                "address": registration.address,
                "student_id": registration.student_id,
                "program_year": registration.program_year,
                "employee_id": registration.employee_id,
                "department": registration.department,
                "campus_days": registration.campus_days,
                "drivers_license": registration.drivers_license,
                "conduction_number": registration.conduction_number,
            }
        })


class RejectRegistrationView(APIView):
    permission_classes = [IsAdminRole]

    def post(self, request, pk):
        registration = get_object_or_404(VehicleRegistration, pk=pk)
        if registration.status != VehicleRegistration.Status.PENDING:
            return Response({"error": "Only pending registrations can be rejected."}, status=status.HTTP_400_BAD_REQUEST)

        reason = request.data.get('reason')
        if not reason:
            return Response({"error": "Rejection reason is required."}, status=status.HTTP_400_BAD_REQUEST)

        registration.status = VehicleRegistration.Status.REJECTED
        registration.rejection_reason = reason
        registration.reviewed_at = timezone.now()
        registration.save()

        # Send rejection email
        send_rejection_email(registration, reason)

        return Response({"message": "Registration rejected."})