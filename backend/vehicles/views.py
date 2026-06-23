import secrets
import string
import time as _time
import uuid

import cv2
from django.http import StreamingHttpResponse, HttpResponse
from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Owner, Vehicle, RuleConstraint, VehicleTypeAccess, ParkingSpace, ParkingZone, Department, Program
from .serializers import OwnerSerializer, VehicleSerializer, RuleConstraintSerializer, VehicleTypeAccessSerializer, ParkingSpaceSerializer, ParkingZoneSerializer
from . import parking_camera

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

class ParkingSpaceViewSet(viewsets.ModelViewSet):
    queryset           = ParkingSpace.objects.all()
    serializer_class   = ParkingSpaceSerializer
    permission_classes = [permissions.IsAuthenticated]


class ParkingZoneViewSet(viewsets.ModelViewSet):
    queryset           = ParkingZone.objects.prefetch_related('spaces').all()
    serializer_class   = ParkingZoneSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx['request'] = self.request
        return ctx

    @action(detail=True, methods=['post'], url_path='upload-image')
    def upload_image(self, request, pk=None):
        zone = self.get_object()
        img  = request.FILES.get('image')
        if not img:
            return Response({'error': 'No image provided.'}, status=400)
        zone.reference_image = img
        zone.save()
        return Response(self.get_serializer(zone).data)

    @action(detail=True, methods=['post'], url_path='save-layout')
    def save_layout(self, request, pk=None):
        """Bulk update the parking space layout for this zone."""
        zone        = self.get_object()
        spaces_data = request.data.get('spaces', [])
        submitted   = {s['space_number'] for s in spaces_data}

        # Remove spaces the admin deleted
        zone.spaces.exclude(space_number__in=submitted).delete()

        # Update or create each space (preserving is_occupied / occupied_by)
        result = []
        for s in spaces_data:
            space, _ = ParkingSpace.objects.update_or_create(
                zone=zone,
                space_number=s['space_number'],
                defaults={
                    'vehicle_category': zone.vehicle_category,
                    'x1': s.get('x1'),
                    'y1': s.get('y1'),
                    'x2': s.get('x2'),
                    'y2': s.get('y2'),
                },
            )
            result.append(space)

        return Response(ParkingSpaceSerializer(result, many=True).data)

    # ── IP Camera ──────────────────────────────────────────────────────────────

    @action(detail=True, methods=['post'], url_path='start-camera')
    def start_camera(self, request, pk=None):
        zone = self.get_object()
        if not zone.rtsp_url:
            return Response({'error': 'No RTSP URL configured for this zone.'}, status=400)
        parking_camera.start(zone.id, zone.rtsp_url)
        return Response({'status': 'started'})

    @action(detail=True, methods=['post'], url_path='stop-camera')
    def stop_camera(self, request, pk=None):
        zone = self.get_object()
        parking_camera.stop(zone.id)
        return Response({'status': 'stopped'})

    @action(detail=False, methods=['get'], url_path='camera-status')
    def camera_status(self, request):
        """Returns {zone_id: is_running} for all zones."""
        return Response(parking_camera.status_dict())


def _make_placeholder_jpeg(text: str) -> bytes:
    """Generate a dark grey JPEG with centred status text — sent when no live frame is available."""
    import numpy as np
    blank = np.full((480, 640, 3), 30, dtype=np.uint8)
    (tw, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)
    x = max(0, (640 - tw) // 2)
    cv2.putText(blank, text, (x, 248), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (160, 160, 160), 2)
    _, buf = cv2.imencode('.jpg', blank, [cv2.IMWRITE_JPEG_QUALITY, 60])
    return buf.tobytes()


def _mjpeg_frame(jpeg_bytes: bytes) -> bytes:
    return b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + jpeg_bytes + b'\r\n'


import asyncio

async def parking_stream_view(request, pk):
    """
    Async MJPEG stream for a parking zone camera.
    Auth via ?token=<JWT> because <img> tags cannot send Authorization headers.
    """
    from rest_framework_simplejwt.authentication import JWTAuthentication
    from rest_framework_simplejwt.exceptions import TokenError, InvalidToken

    token_str = request.GET.get('token', '')
    if not token_str:
        return HttpResponse(status=401)
    try:
        JWTAuthentication().get_validated_token(token_str)
    except (TokenError, InvalidToken):
        return HttpResponse(status=401)

    zone_id = int(pk)

    _connecting = _make_placeholder_jpeg('Connecting to camera...')
    _no_camera  = _make_placeholder_jpeg('Camera not running')

    async def _generate():
        loop = asyncio.get_event_loop()
        try:
            while True:
                thread = parking_camera.get_thread(zone_id)
                if not thread:
                    yield _mjpeg_frame(_no_camera)
                    await asyncio.sleep(0.5)
                    continue

                frame = thread.get_frame()
                if frame is None:
                    yield _mjpeg_frame(_connecting)
                    await asyncio.sleep(0.1)
                    continue

                # encode in thread-pool so we don't block the event loop
                fr = frame  # local capture
                jpeg_bytes = await loop.run_in_executor(
                    None,
                    lambda: cv2.imencode('.jpg', fr, [cv2.IMWRITE_JPEG_QUALITY, 75])[1].tobytes()
                )
                yield _mjpeg_frame(jpeg_bytes)
                await asyncio.sleep(1 / 20)  # cap at 20 fps
        except (asyncio.CancelledError, GeneratorExit):
            return

    return StreamingHttpResponse(
        _generate(),
        content_type='multipart/x-mixed-replace; boundary=frame',
    )


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

        or_number = request.data.get('or_number', '').strip()
        if not or_number:
            return Response({"error": "Official Receipt (OR) number is required before accepting."}, status=status.HTTP_400_BAD_REQUEST)

        # Admin may override campus_days (free day picker) and/or schedule group
        campus_days_override = request.data.get('campus_days', None)  # list or None
        schedule_override    = request.data.get('schedule', '').strip()

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
            user_code=user.user_code,
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

        registration.or_number = or_number
        registration.user = user  # direct FK — eliminates email-string lookups

        # Apply campus_days / schedule overrides
        if campus_days_override is not None and isinstance(campus_days_override, list):
            valid_days = {'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'}
            cleaned = [d for d in campus_days_override if d in valid_days]
            registration.campus_days = cleaned
            # Re-derive schedule group from the new days
            mwf_days  = {'Monday', 'Wednesday', 'Friday'}
            tths_days = {'Tuesday', 'Thursday', 'Saturday'}
            days_set  = set(cleaned)
            mwf_n  = len(days_set & mwf_days)
            tths_n = len(days_set & tths_days)
            if mwf_n > 0 and tths_n == 0:
                registration.schedule = 'MWF'
            elif tths_n > 0 and mwf_n == 0:
                registration.schedule = 'TTHS'
            elif mwf_n > 0 and tths_n > 0:
                registration.schedule = 'MIXED'
        elif schedule_override:
            registration.schedule = schedule_override
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


# ──────────────────────────────────────────────
# Registration Window & Open Public Registration
# ──────────────────────────────────────────────

REGISTRATION_OPEN_MONTH  = 6   # June  (tentative — 2 months before school year)
REGISTRATION_OPEN_DAY    = 1
REGISTRATION_CLOSE_MONTH = 10  # October (tentative — end of first semester enrollment window)
REGISTRATION_CLOSE_DAY   = 31
SCHEDULE_SLOT_LIMIT      = 100  # per day


def _registration_window():
    now = timezone.localtime(timezone.now())
    m, d = now.month, now.day
    is_open = (
        (REGISTRATION_OPEN_MONTH, REGISTRATION_OPEN_DAY)
        <= (m, d)
        <= (REGISTRATION_CLOSE_MONTH, REGISTRATION_CLOSE_DAY)
    )
    return {
        "is_open": is_open,
        "open_date": f"June 1 (tentative)",
        "close_date": f"October 31 (tentative)",
        "slot_limit": SCHEDULE_SLOT_LIMIT,
    }


class RegistrationStatusView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        return Response(_registration_window())


ALL_DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']


class ScheduleSlotsView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        active = [VehicleRegistration.Status.PENDING, VehicleRegistration.Status.ACCEPTED]
        base = VehicleRegistration.objects.filter(status__in=active, registrant_type='student')
        limit = SCHEDULE_SLOT_LIMIT
        result = {}
        for day in ALL_DAYS:
            used = base.filter(campus_days__contains=[day]).count()
            result[day] = {
                "used": used,
                "limit": limit,
                "available": max(0, limit - used),
            }
        return Response(result)


class PublicOpenRegistrationView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        window = _registration_window()
        if not window["is_open"]:
            return Response(
                {"error": "Vehicle Pass registration is currently closed.", "period_closed": True},
                status=status.HTTP_403_FORBIDDEN,
            )

        registrant_type = request.data.get('registrant_type', '')
        if registrant_type not in ['student', 'employee', 'fetcher']:
            return Response({"error": "Invalid registrant type."}, status=status.HTTP_400_BAD_REQUEST)

        data = dict(request.data)

        if registrant_type == 'employee' or registrant_type == 'fetcher':
            data['schedule'] = 'ANY'
            data['campus_days'] = []
        else:
            # Validate per-day slot limits for students
            campus_days = data.get('campus_days', [])
            if not campus_days:
                return Response({"error": "Students must select at least one campus day."}, status=status.HTTP_400_BAD_REQUEST)

            active = [VehicleRegistration.Status.PENDING, VehicleRegistration.Status.ACCEPTED]
            base = VehicleRegistration.objects.filter(status__in=active, registrant_type='student')
            full_days = []
            for day in campus_days:
                used = base.filter(campus_days__contains=[day]).count()
                if used >= SCHEDULE_SLOT_LIMIT:
                    full_days.append(day)
            if full_days:
                return Response(
                    {"error": f"The following day(s) are full: {', '.join(full_days)}. Please choose other days."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Derive schedule group from majority of selected days
            mwf_days  = {'Monday', 'Wednesday', 'Friday'}
            tths_days = {'Tuesday', 'Thursday', 'Saturday'}
            days_set  = set(campus_days)
            mwf_n  = len(days_set & mwf_days)
            tths_n = len(days_set & tths_days)
            if mwf_n >= tths_n and mwf_n > 0:
                data['schedule'] = 'MWF'
            elif tths_n > 0:
                data['schedule'] = 'TTHS'

        data['token'] = str(uuid.uuid4())  # internal uniqueness token

        serializer = VehicleRegistrationSerializer(data=data)
        if serializer.is_valid():
            serializer.save(registrant_type=registrant_type)
            return Response({"message": "Registration submitted successfully."}, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# ──────────────────────────────────────────────
# Department & Program lists (public, for registration form)
# ──────────────────────────────────────────────

class DepartmentListView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        names = list(Department.objects.filter(is_active=True).values_list('name', flat=True))
        return Response(names)


class ProgramListView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        names = list(Program.objects.filter(is_active=True).values_list('name', flat=True))
        return Response(names)


# ──────────────────────────────────────────────
# Parking Availability (for vehicle owners)
# ──────────────────────────────────────────────

class IsVehicleOwnerRole(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role == 'vehicle_owner')


class ParkingAvailabilityView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        category = request.query_params.get('category', '')
        qs = ParkingSpace.objects.all()
        if category in ['motorcycle', 'car']:
            qs = qs.filter(vehicle_category=category)
        spaces = ParkingSpaceSerializer(qs, many=True).data
        summary = {}
        for s in spaces:
            cat = s['vehicle_category']
            if cat not in summary:
                summary[cat] = {'total': 0, 'occupied': 0, 'available': 0}
            summary[cat]['total'] += 1
            if s['is_occupied']:
                summary[cat]['occupied'] += 1
            else:
                summary[cat]['available'] += 1
        return Response({"spaces": spaces, "summary": summary})