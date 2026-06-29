import secrets
import string
import time as _time
import uuid

import cv2
from django.http import StreamingHttpResponse, HttpResponse
from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response

from rest_framework import status as drf_status
from .models import Vehicle, RuleConstraint, ParkingSpace, ParkingZone, ReferenceItem, Camera, SystemSettings, ParkingNotice
from .serializers import VehicleSerializer, RuleConstraintSerializer, ParkingSpaceSerializer, ParkingZoneSerializer, ReferenceItemSerializer, CameraSerializer, ParkingNoticeSerializer
from . import parking_camera

class VehicleViewSet(viewsets.ModelViewSet):
    queryset           = Vehicle.objects.select_related('user').all()
    serializer_class   = VehicleSerializer
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=True, methods=['patch'])
    def authorize(self, request, pk=None):
        vehicle = self.get_object()
        vehicle.is_authorized = not vehicle.is_authorized
        vehicle.save()
        return Response({'plate': vehicle.plate_number, 'is_authorized': vehicle.is_authorized})

    @action(detail=True, methods=['get'])
    def profile(self, request, pk=None):
        """Return full vehicle profile: owner, latest registration, active and resolved violations."""
        from violations.serializers import ViolationSerializer
        from .models import VehicleRegistration
        from .serializers import VehicleRegistrationSerializer

        vehicle = self.get_object()

        # Latest accepted registration — FK-linked first, then plate fallback for legacy records
        reg = (
            vehicle.registrations.filter(status='accepted').order_by('-reviewed_at').first()
            or VehicleRegistration.objects.filter(
                plate_number=vehicle.plate_number,
                status='accepted',
            ).order_by('-reviewed_at').first()
        )

        active_violations   = vehicle.violations.filter(is_resolved=False).order_by('-issued_at')
        resolved_violations = vehicle.violations.filter(is_resolved=True).order_by('-issued_at')

        return Response({
            'vehicle':             VehicleSerializer(vehicle).data,
            'registration':        VehicleRegistrationSerializer(reg).data if reg else None,
            'active_violations':   ViolationSerializer(active_violations, many=True).data,
            'resolved_violations': ViolationSerializer(resolved_violations, many=True).data,
        })

class RuleConstraintViewSet(viewsets.ModelViewSet):
    queryset           = RuleConstraint.objects.all()
    serializer_class   = RuleConstraintSerializer
    permission_classes = [permissions.IsAuthenticated]

class ReferenceItemViewSet(viewsets.ModelViewSet):
    queryset           = ReferenceItem.objects.all()
    serializer_class   = ReferenceItemSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        category = self.request.query_params.get('category')
        if category:
            qs = qs.filter(category=category)
        return qs

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
                    'x1': s.get('x1'),
                    'y1': s.get('y1'),
                    'x2': s.get('x2'),
                    'y2': s.get('y2'),
                },
            )
            result.append(space)

        return Response(ParkingSpaceSerializer(result, many=True).data)

    @action(detail=True, methods=['patch'], url_path='set-capacity')
    def set_capacity(self, request, pk=None):
        """Guard/admin sets (or clears) the event-mode capacity override for a zone."""
        zone = self.get_object()
        value = request.data.get('capacity_override')
        if value is None or str(value).strip() == '':
            zone.capacity_override = None
        else:
            try:
                zone.capacity_override = int(value)
                if zone.capacity_override < 0:
                    return Response({'error': 'Capacity must be a non-negative integer.'}, status=400)
            except (TypeError, ValueError):
                return Response({'error': 'Capacity must be a number.'}, status=400)
        zone.save(update_fields=['capacity_override'])
        return Response(self.get_serializer(zone).data)

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


class CameraViewSet(viewsets.ModelViewSet):
    queryset           = Camera.objects.all()
    serializer_class   = CameraSerializer
    permission_classes = [permissions.IsAuthenticated]

    def _next_cam_number(self):
        existing = set(Camera.objects.values_list('cam_number', flat=True))
        n = 1
        while n in existing:
            n += 1
        return n

    def create(self, request, *args, **kwargs):
        num        = self._next_cam_number()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(cam_number=num, name=f'Cam {num}')
        return Response(serializer.data, status=drf_status.HTTP_201_CREATED)

    @action(detail=False, methods=['get'], url_path='next-name')
    def next_name(self, request):
        n = self._next_cam_number()
        return Response({'cam_number': n, 'name': f'Cam {n}'})

    def get_queryset(self):
        qs = super().get_queryset()
        assignment = self.request.query_params.get('assignment')
        if assignment:
            qs = qs.filter(assignment=assignment)
        return qs


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
from .models import VehicleRegistration
from .serializers import VehicleRegistrationSerializer
from accounts.models import User
from .email_utils import send_acceptance_email, send_rejection_email, send_pending_email


class IsAdminRole(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role == 'admin')


class IsAdminOrCdso(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role in ('admin', 'cdso'))


class PendingRegistrationsListView(APIView):
    permission_classes = [IsAdminOrCdso]

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
    permission_classes = [IsAdminOrCdso]

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
        special_case_reason  = request.data.get('special_case_reason', '').strip()

        # Early validation: if admin is adding days beyond the original, a reason is required
        if campus_days_override is not None and isinstance(campus_days_override, list):
            valid_days = {'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'}
            _cleaned_check = [d for d in campus_days_override if d in valid_days]
            _original_days = set(registration.campus_days or [])
            _added_days    = set(_cleaned_check) - _original_days
            if _added_days and not special_case_reason:
                return Response(
                    {"error": "A reason is required when adding days not in the original schedule."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if User.objects.filter(email=registration.email).exists():
             return Response({"error": "User with this email already exists."}, status=status.HTTP_400_BAD_REQUEST)

        # Create user with a secure temporary password
        temp_password = _generate_temp_password()
        owner_type = User.OwnerType.STUDENT if registration.registrant_type == 'student' else User.OwnerType.EMPLOYEE
        schedule   = registration.schedule or ('MWF' if registration.registrant_type == 'student' else 'ANY')
        user = User.objects.create_user(
            email=registration.email,
            full_name=registration.full_name,
            password=temp_password,
            role='vehicle_owner',
            must_change_password=True,
            owner_type=owner_type,
            schedule=schedule,
            contact=registration.contact_number,
            address=registration.address,
        )

        # Create or update Vehicle linked directly to User
        # update_or_create handles duplicate plates gracefully (plate_number is unique)
        plate_normalized = registration.plate_number.strip().upper().replace(' ', '')
        vehicle_obj, _ = Vehicle.objects.update_or_create(
            plate_number=plate_normalized,
            defaults={
                'vehicle_type': registration.vehicle_type,
                'color':        registration.vehicle_color,
                'is_authorized': True,
                'user':          user,
            }
        )

        # Auto-generate unique system ID
        padded_id = str(registration.pk).zfill(6)
        if registration.registrant_type == 'student':
            registration.system_student_id = f"SLC-STU-{padded_id}"
        else:
            registration.system_employee_id = f"SLC-EMP-{padded_id}"

        registration.or_number = or_number
        registration.user = user        # direct FK to account
        registration.vehicle = vehicle_obj  # 1:1 link registration → vehicle

        # Apply campus_days / schedule overrides
        if campus_days_override is not None and isinstance(campus_days_override, list):
            valid_days = {'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'}
            cleaned = [d for d in campus_days_override if d in valid_days]

            # Mark as special case if days were added beyond original request
            original_days = set(registration.campus_days or [])
            added_days    = set(cleaned) - original_days
            if added_days:
                registration.is_special_case     = True
                registration.special_case_reason = special_case_reason

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
                "department": registration.department.name if registration.department else '',
                "campus_days": registration.campus_days,
                "drivers_license": registration.drivers_license,
                "conduction_number": registration.conduction_number,
            }
        })


class RejectRegistrationView(APIView):
    permission_classes = [IsAdminOrCdso]

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
# CDSO Walk-in Direct Registration (auto-accepts)
# ──────────────────────────────────────────────

class CdsoDirectRegisterView(APIView):
    """
    CDSO registers a walk-in applicant directly.
    No pending step — user account + vehicle are created immediately.
    """
    permission_classes = [IsAdminOrCdso]

    def post(self, request):
        registrant_type = request.data.get('registrant_type', '')
        if registrant_type not in ('student', 'employee', 'fetcher'):
            return Response({"error": "Invalid registrant type."}, status=status.HTTP_400_BAD_REQUEST)

        or_number = request.data.get('or_number', '').strip()
        if not or_number:
            return Response({"error": "Official Receipt (OR) number is required."}, status=status.HTTP_400_BAD_REQUEST)

        email = request.data.get('email', '').strip()
        if User.objects.filter(email=email).exists():
            return Response({"error": "A user with this email already exists."}, status=status.HTTP_400_BAD_REQUEST)

        serializer = VehicleRegistrationSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        registration = serializer.save(
            registrant_type=registrant_type,
            source=VehicleRegistration.Source.DIRECT,
            status=VehicleRegistration.Status.ACCEPTED,
            or_number=or_number,
            reviewed_at=timezone.now(),
        )

        # Build user profile fields
        temp_password = _generate_temp_password()
        owner_type = User.OwnerType.STUDENT if registrant_type == 'student' else User.OwnerType.EMPLOYEE
        schedule   = registration.schedule or ('MWF' if registrant_type == 'student' else 'ANY')

        user = User.objects.create_user(
            email=registration.email,
            full_name=registration.full_name,
            password=temp_password,
            role='vehicle_owner',
            must_change_password=True,
            owner_type=owner_type,
            schedule=schedule,
            contact=registration.contact_number,
            address=registration.address,
        )

        plate_normalized = registration.plate_number.strip().upper().replace(' ', '')
        vehicle_obj, _ = Vehicle.objects.update_or_create(
            plate_number=plate_normalized,
            defaults={
                'vehicle_type':  registration.vehicle_type,
                'color':         registration.vehicle_color,
                'is_authorized': True,
                'user':          user,
            }
        )

        padded_id = str(registration.pk).zfill(6)
        if registrant_type == 'student':
            registration.system_student_id = f"SLC-STU-{padded_id}"
        else:
            registration.system_employee_id = f"SLC-EMP-{padded_id}"
        registration.user = user
        registration.vehicle = vehicle_obj  # 1:1 link registration → vehicle
        registration.save()

        user.refresh_from_db()
        system_id = registration.system_student_id if registrant_type == 'student' else registration.system_employee_id

        try:
            send_acceptance_email(registration, temp_password, user.user_code)
            email_status = 'sent'
        except Exception as e:
            import traceback
            print(f"[EMAIL ERROR] {e}")
            traceback.print_exc()
            email_status = 'failed'

        return Response({
            "message": "Walk-in registered and account created.",
            "email_status": email_status,
            "account": {
                "user_code":       user.user_code,
                "system_id":       system_id,
                "email":           registration.email,
                "full_name":       registration.full_name,
                "registrant_type": registrant_type,
                "plate_number":    registration.plate_number,
            }
        }, status=status.HTTP_201_CREATED)


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

        serializer = VehicleRegistrationSerializer(data=data)
        if serializer.is_valid():
            registration = serializer.save(
                registrant_type=registrant_type,
                source=VehicleRegistration.Source.PUBLIC,
            )
            try:
                send_pending_email(registration)
            except Exception:
                pass  # don't fail the submission if email errors
            return Response({"message": "Registration submitted successfully. Please wait for CDSO review."}, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# ──────────────────────────────────────────────
# Department & Program lists (public, for registration form)
# ──────────────────────────────────────────────

class DepartmentListView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        names = list(ReferenceItem.objects.filter(category='department', is_active=True).values_list('name', flat=True))
        return Response(names)


class ProgramListView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        names = list(ReferenceItem.objects.filter(category='program', is_active=True).values_list('name', flat=True))
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
        qs = ParkingSpace.objects.select_related('zone').all()
        if category in ['motorcycle', 'car']:
            qs = qs.filter(zone__vehicle_category=category)
        spaces = ParkingSpaceSerializer(qs, many=True).data
        summary = {}
        zone_agg = {}  # zone_id -> {name, category, total, occupied}
        for s in spaces:
            cat = s['vehicle_category']
            if cat not in summary:
                summary[cat] = {'total': 0, 'occupied': 0, 'available': 0}
            summary[cat]['total'] += 1
            if s['is_occupied']:
                summary[cat]['occupied'] += 1
            else:
                summary[cat]['available'] += 1
            # per-zone aggregation
            zid = s['zone']
            if zid not in zone_agg:
                zone_obj = ParkingZone.objects.filter(pk=zid).first()
                zone_agg[zid] = {
                    'zone_id':   zid,
                    'zone_name': zone_obj.name if zone_obj else str(zid),
                    'category':  cat,
                    'total':     0,
                    'occupied':  0,
                }
            zone_agg[zid]['total'] += 1
            if s['is_occupied']:
                zone_agg[zid]['occupied'] += 1

        zones = []
        for z in zone_agg.values():
            fill_pct = round(z['occupied'] / z['total'] * 100) if z['total'] > 0 else 0
            zones.append({**z, 'available': z['total'] - z['occupied'], 'fill_pct': fill_pct})

        return Response({"spaces": spaces, "summary": summary, "zones": zones})


class SystemSettingsView(APIView):
    def get_permissions(self):
        if self.request.method == 'GET':
            return [permissions.IsAuthenticated()]
        return [IsAdminOrCdso()]

    def _serialize(self, obj):
        return {
            "retention_years":    obj.retention_years,
            "scan_dedup_seconds": obj.scan_dedup_seconds,
            "event_mode_parking": obj.event_mode_parking,
            "event_mode_entry":   obj.event_mode_entry,
            "open_campus_mode":   obj.open_campus_mode,
            "registration_start": obj.registration_start.isoformat() if obj.registration_start else None,
            "registration_end":   obj.registration_end.isoformat()   if obj.registration_end   else None,
        }

    def get(self, request):
        return Response(self._serialize(SystemSettings.get()))

    def put(self, request):
        obj = SystemSettings.get()
        errors = {}

        from datetime import date as date_type
        retention_years      = request.data.get("retention_years",    obj.retention_years)
        scan_dedup_seconds   = request.data.get("scan_dedup_seconds", obj.scan_dedup_seconds)
        event_mode_parking   = request.data.get("event_mode_parking", obj.event_mode_parking)
        event_mode_entry     = request.data.get("event_mode_entry",   obj.event_mode_entry)
        open_campus_mode     = request.data.get("open_campus_mode",   obj.open_campus_mode)
        registration_start   = request.data.get("registration_start", obj.registration_start)
        registration_end     = request.data.get("registration_end",   obj.registration_end)

        try:
            retention_years = int(retention_years)
            if not (1 <= retention_years <= 10):
                errors["retention_years"] = "Must be between 1 and 10 years."
        except (TypeError, ValueError):
            errors["retention_years"] = "Must be an integer."

        try:
            scan_dedup_seconds = int(scan_dedup_seconds)
            if not (5 <= scan_dedup_seconds <= 300):
                errors["scan_dedup_seconds"] = "Must be between 5 and 300 seconds."
        except (TypeError, ValueError):
            errors["scan_dedup_seconds"] = "Must be an integer."

        def parse_date(val):
            if not val:
                return None
            if isinstance(val, date_type):
                return val
            from datetime import datetime
            return datetime.strptime(str(val), "%Y-%m-%d").date()

        try:
            registration_start = parse_date(registration_start)
        except ValueError:
            errors["registration_start"] = "Invalid date format. Use YYYY-MM-DD."

        try:
            registration_end = parse_date(registration_end)
        except ValueError:
            errors["registration_end"] = "Invalid date format. Use YYYY-MM-DD."

        if not errors and registration_start and registration_end and registration_end < registration_start:
            errors["registration_end"] = "End date must be on or after the start date."

        if errors:
            return Response(errors, status=400)

        obj.retention_years    = retention_years
        obj.scan_dedup_seconds = scan_dedup_seconds
        obj.event_mode_parking = bool(event_mode_parking)
        obj.event_mode_entry   = bool(event_mode_entry)
        obj.open_campus_mode   = bool(open_campus_mode)
        obj.registration_start = registration_start
        obj.registration_end   = registration_end
        obj.save()

        return Response(self._serialize(obj))

    def patch(self, request):
        """Lightweight partial update — supports toggling event_mode_parking, event_mode_entry, and open_campus_mode."""
        obj = SystemSettings.get()
        update_fields = []
        if 'event_mode_parking' in request.data:
            obj.event_mode_parking = bool(request.data['event_mode_parking'])
            update_fields.append('event_mode_parking')
        if 'event_mode_entry' in request.data:
            obj.event_mode_entry = bool(request.data['event_mode_entry'])
            update_fields.append('event_mode_entry')
        if 'open_campus_mode' in request.data:
            obj.open_campus_mode = bool(request.data['open_campus_mode'])
            update_fields.append('open_campus_mode')
        if update_fields:
            obj.save(update_fields=update_fields)
        return Response(self._serialize(obj))


# ──────────────────────────────────────────────
# Parking Notices (CDSO/Admin broadcast, owner read)
# ──────────────────────────────────────────────

class ParkingNoticeView(APIView):
    def get_permissions(self):
        return [permissions.IsAuthenticated()]

    def get(self, request):
        """All authenticated users see active notices."""
        notices = ParkingNotice.objects.filter(is_active=True)
        return Response(ParkingNoticeSerializer(notices, many=True).data)

    def post(self, request):
        """Admin/CDSO create and broadcast a notice to all vehicle owners."""
        if request.user.role not in ('admin', 'cdso'):
            return Response({'error': 'Permission denied.'}, status=403)

        serializer = ParkingNoticeSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)
        notice = serializer.save(created_by=request.user)

        # Email blast to all active vehicle owners
        from accounts.models import User as UserModel
        from django.core.mail import send_mail
        from django.conf import settings

        recipients = list(
            UserModel.objects.filter(role='vehicle_owner', is_active=True)
            .values_list('email', flat=True)
        )
        if recipients:
            html_msg = f"""
            <html>
              <body style="font-family:Arial,sans-serif;color:#1A1D2E;background:#F0F2F7;padding:20px;margin:0;">
                <div style="max-width:580px;margin:0 auto;background:#fff;border-radius:12px;border-top:4px solid #2A2B61;box-shadow:0 4px 20px rgba(0,0,0,.08);overflow:hidden;">
                  <div style="padding:28px 32px 24px;">
                    <h2 style="color:#2A2B61;margin:0 0 6px;">Parking Notice</h2>
                    <p style="color:#5A5F72;font-size:13px;margin:0 0 20px;">From the CDSO / SLC Vehicle Management Office</p>
                    <h3 style="margin:0 0 12px;color:#1A1D2E;">{notice.title}</h3>
                    <p style="color:#374151;font-size:14px;line-height:1.6;margin:0 0 24px;white-space:pre-line;">{notice.body}</p>
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
                subject=f"SLC Parking Notice: {notice.title}",
                message=f"Parking Notice\n\n{notice.title}\n\n{notice.body}",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=recipients,
                html_message=html_msg,
                fail_silently=True,
            )

        return Response(ParkingNoticeSerializer(notice).data, status=201)


class ParkingNoticeDetailView(APIView):
    def get_permissions(self):
        return [permissions.IsAuthenticated()]

    def delete(self, request, pk):
        """Admin/CDSO deactivate (soft-delete) a notice."""
        if request.user.role not in ('admin', 'cdso'):
            return Response({'error': 'Permission denied.'}, status=403)
        notice = get_object_or_404(ParkingNotice, pk=pk)
        notice.is_active = False
        notice.save(update_fields=['is_active'])
        return Response({'message': 'Notice deactivated.'}, status=200)