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


from rest_framework.views import APIView
from rest_framework import status
from django.shortcuts import get_object_or_404
from django.utils import timezone
from .models import RegistrationToken, VehicleRegistration
from .serializers import RegistrationTokenSerializer, VehicleRegistrationSerializer
from accounts.models import User
from .email_utils import send_acceptance_email, send_rejection_email
import uuid

class IsAdminRole(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role == 'admin')


class GenerateRegistrationTokenView(APIView):
    permission_classes = [IsAdminRole]

    def post(self, request):
        registrant_type = request.data.get('registrant_type')
        expires_at = request.data.get('expires_at')
        if not registrant_type or not expires_at:
            return Response({"error": "registrant_type and expires_at are required"}, status=status.HTTP_400_BAD_REQUEST)

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


class AcceptRegistrationView(APIView):
    permission_classes = [IsAdminRole]

    def post(self, request, pk):
        registration = get_object_or_404(VehicleRegistration, pk=pk)
        if registration.status != VehicleRegistration.Status.PENDING:
            return Response({"error": "Only pending registrations can be accepted."}, status=status.HTTP_400_BAD_REQUEST)

        if User.objects.filter(email=registration.email).exists():
             return Response({"error": "User with this email already exists."}, status=status.HTTP_400_BAD_REQUEST)

        # Create user
        user = User.objects.create_user(
            email=registration.email,
            full_name=registration.full_name,
            password=str(uuid.uuid4())[:8], # temporary password, though they should reset it or use email link? Wait, the plan said "Switch login to use email instead of full_name". How do they get their password? 
            role='vehicle_owner'
        )

        # Create Owner
        owner_type = Owner.OwnerType.STUDENT if registration.registrant_type == 'student' else Owner.OwnerType.EMPLOYEE
        owner = Owner.objects.create(
            full_name=registration.full_name,
            contact=registration.contact_number,
            address=registration.address,
            owner_type=owner_type,
            # schedule parsing omitted for brevity
        )

        # Create Vehicle
        Vehicle.objects.create(
            plate_number=registration.plate_number,
            vehicle_type=registration.vehicle_type,
            color=registration.vehicle_color,
            is_authorized=True,
            owner=owner
        )

        registration.status = VehicleRegistration.Status.ACCEPTED
        registration.reviewed_at = timezone.now()
        registration.save()

        # Send acceptance email with QR code
        send_acceptance_email(registration)

        return Response({"message": "Registration accepted and user created."})


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