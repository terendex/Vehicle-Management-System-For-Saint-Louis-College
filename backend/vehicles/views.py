import logging
import secrets
import string
import threading
import time as _time
import uuid
from decimal import Decimal, InvalidOperation

import cv2
from django.db.models import Q, Value
from django.db.models.functions import Lower, Replace, Upper
from django.http import StreamingHttpResponse, HttpResponse
from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response

from rest_framework import status as drf_status
from .models import Vehicle, RuleConstraint, ParkingSpace, ParkingZone, ReferenceItem, Camera, SystemSettings, ParkingNotice, RegistrationPeriod, Event, ScheduledVisit
from .serializers import VehicleSerializer, RuleConstraintSerializer, ParkingSpaceSerializer, ParkingZoneSerializer, ReferenceItemSerializer, CameraSerializer, ParkingNoticeSerializer, ScheduledVisitSerializer
from . import parking_camera

logger = logging.getLogger(__name__)
from accounts.audit import audit, AuditedViewSetMixin
from time_utils import filter_local_date_range
from accounts.models import AuditLog

class VehicleViewSet(AuditedViewSetMixin, viewsets.ModelViewSet):
    queryset           = Vehicle.objects.select_related('user').all()
    serializer_class   = VehicleSerializer
    permission_classes = [permissions.IsAuthenticated]
    audit_label        = 'Vehicle'

    @action(detail=True, methods=['patch'])
    def authorize(self, request, pk=None):
        vehicle = self.get_object()
        vehicle.is_authorized = not vehicle.is_authorized
        vehicle.save()
        audit(request, AuditLog.Action.RECORD_UPDATED,
              f"Vehicle {'authorized' if vehicle.is_authorized else 'deauthorized'} | "
              f"Plate: {vehicle.plate_number} | By: {request.user.full_name}")
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

class RuleConstraintViewSet(AuditedViewSetMixin, viewsets.ModelViewSet):
    queryset           = RuleConstraint.objects.all()
    serializer_class   = RuleConstraintSerializer
    permission_classes = [permissions.IsAuthenticated]
    audit_label        = 'Schedule Rule'

class ReferenceItemViewSet(AuditedViewSetMixin, viewsets.ModelViewSet):
    queryset           = ReferenceItem.objects.all()
    serializer_class   = ReferenceItemSerializer
    permission_classes = [permissions.IsAuthenticated]
    audit_label        = 'Reference Item'

    def get_queryset(self):
        qs = super().get_queryset()
        category = self.request.query_params.get('category')
        if category:
            qs = qs.filter(category=category)
        return qs

class ParkingReadOnlyUnlessAdmin(permissions.BasePermission):
    """Any signed-in role may read parking data; only admin/CDSO may change it.

    Guards need the live parking map at the gate, so GET stays open to every
    authenticated role. Everything that *changes* something — creating a zone,
    editing the layout, deleting, toggling a space, starting or stopping camera
    detection — is admin only.

    This has to be enforced here, not just in the UI. The guard screen never
    offered an edit control, but both parking viewsets were plain
    IsAuthenticated, so a guard's own token could create, edit or DELETE any
    zone straight against the API. (ParkingSpace.zone is on_delete=SET_NULL,
    so a deleted zone orphans its spaces rather than removing them — the
    layout survives as unreachable rows.)

    NOTE: 'admin' is the CDSO role — the separate 'cdso' role was folded into
    it, matching IsAdminOrCdso elsewhere in this file.
    """

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return getattr(request.user, 'role', None) == 'admin'


class ParkingSpaceViewSet(viewsets.ModelViewSet):
    queryset           = ParkingSpace.objects.all()
    serializer_class   = ParkingSpaceSerializer
    permission_classes = [ParkingReadOnlyUnlessAdmin]


class ParkingZoneViewSet(AuditedViewSetMixin, viewsets.ModelViewSet):
    queryset           = ParkingZone.objects.select_related('camera').prefetch_related('spaces').all()
    serializer_class   = ParkingZoneSerializer
    permission_classes = [ParkingReadOnlyUnlessAdmin]
    audit_label        = 'Parking Zone'

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
            points = s.get('points')
            if points:
                xs, ys = [p[0] for p in points], [p[1] for p in points]
                bbox = {'x1': min(xs), 'y1': min(ys), 'x2': max(xs), 'y2': max(ys)}
            else:
                points = None
                bbox = {'x1': s.get('x1'), 'y1': s.get('y1'), 'x2': s.get('x2'), 'y2': s.get('y2')}

            space, _ = ParkingSpace.objects.update_or_create(
                zone=zone,
                space_number=s['space_number'],
                defaults={**bbox, 'points': points},
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
        if not zone.camera or not zone.camera.rtsp_url:
            return Response({'error': 'No camera assigned to this zone. Assign one from Device Management.'}, status=400)
        parking_camera.start(zone.id, zone.camera.rtsp_url)
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

    @action(detail=False, methods=['get'], url_path='alerts')
    def alerts(self, request):
        """Live double-parking alerts across every running zone.

        GET, so guards can see them too — spotting a car across two bays is
        exactly their job. They clear themselves when the vehicle moves, so
        this reflects what is happening now rather than a growing history;
        anything attributed to a plate is already recorded as a Violation.
        """
        out = []
        for zone_id, thread in list(parking_camera.all_threads().items()):
            try:
                out.extend(thread.get_alerts())
            except Exception:
                logger.exception("Failed reading alerts for zone %s", zone_id)
        return Response(out)


# ── PTZ helpers (ONVIF ContinuousMove / Stop / GotoHomePosition) ─────────────

# Discovered PTZ route per camera: which HTTP port, ONVIF paths, SOAP content
# type and profile token actually work. None of that changes for a given
# camera, but it used to be re-probed on *every* button press — worst case a
# port scan plus 10 SOAP attempts at a 5s timeout each. Press-and-hold on an
# arrow key made that cost repeat per request. Discover once, reuse after.
_PTZ_LOCK  = threading.Lock()
_PTZ_CACHE: dict = {}


def _ptz_cache_get(cam_id, ip):
    with _PTZ_LOCK:
        info = _PTZ_CACHE.get(cam_id)
    # Ignore the cache if the camera was re-pointed at a different address.
    return info if info and info.get('ip') == ip else None


def _ptz_cache_set(cam_id, info):
    with _PTZ_LOCK:
        _PTZ_CACHE[cam_id] = info


def _ptz_cache_clear(cam_id):
    with _PTZ_LOCK:
        _PTZ_CACHE.pop(cam_id, None)


def _ptz_soap(endpoint, body_xml, username, password, content_types=None):
    """POST an ONVIF SOAP envelope. Returns (response, content_type_that_worked).

    `content_types` pins the encoding to the one already known to work for this
    camera, skipping the SOAP 1.2 → 1.1 probe.
    """
    import requests as _rq, base64, hashlib, os, datetime
    nonce_raw = os.urandom(16)
    nonce_b64 = base64.b64encode(nonce_raw).decode()
    created   = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    digest    = base64.b64encode(
        hashlib.sha1(nonce_raw + created.encode() + password.encode()).digest()
    ).decode()
    envelope = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
        '<s:Header>'
        '<wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"'
        ' xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">'
        '<wsse:UsernameToken>'
        f'<wsse:Username>{username}</wsse:Username>'
        '<wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd#PasswordDigest">'
        f'{digest}</wsse:Password>'
        '<wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">'
        f'{nonce_b64}</wsse:Nonce>'
        f'<wsu:Created>{created}</wsu:Created>'
        '</wsse:UsernameToken></wsse:Security></s:Header>'
        f'<s:Body>{body_xml}</s:Body></s:Envelope>'
    )
    data = envelope.encode('utf-8')
    # Try SOAP 1.2 first, fall back to SOAP 1.1 (text/xml) for budget cameras
    for ct in (content_types or ['application/soap+xml; charset=utf-8',
                                 'text/xml; charset=utf-8']):
        try:
            r = _rq.post(endpoint, data=data,
                         headers={'Content-Type': ct},
                         timeout=5, auth=(username, password))
            if r.status_code < 500:
                return r, ct
        except Exception:
            pass
    raise Exception(f'SOAP request failed for {endpoint}')


MEDIA_PATHS = ['/onvif/media_service', '/onvif/Media', '/onvif/media',
               '/onvif/device_service', '/onvif/']
PTZ_PATHS   = ['/onvif/PTZ_service', '/onvif/ptz_service', '/onvif/PTZ',
               '/onvif/ptz', '/onvif/']


def _ptz_get_token(base_url, username, password, media_path=None, content_type=None):
    """Discover the profile token. Returns (token, media_path, content_type).

    Passing a known media_path/content_type turns the probe into one request.
    """
    from xml.etree import ElementTree as ET
    # Try common ONVIF media service paths — cameras vary on capitalisation
    for path in ([media_path] if media_path else MEDIA_PATHS):
        try:
            resp, ct = _ptz_soap(f'{base_url}{path}',
                                 '<trt:GetProfiles xmlns:trt="http://www.onvif.org/ver10/media/wsdl"/>',
                                 username, password,
                                 [content_type] if content_type else None)
            for el in ET.fromstring(resp.text).iter():
                t = el.get('token')
                if t:
                    return t, path, ct
        except Exception:
            continue
    if media_path:
        raise Exception('cached ONVIF media path stopped responding')
    return 'Profile_1', None, None


def _ptz_send(base_url, username, password, body_xml, ptz_path=None, content_type=None):
    """Send a PTZ command. Returns (ptz_path, content_type) that worked."""
    for path in ([ptz_path] if ptz_path else PTZ_PATHS):
        try:
            r, ct = _ptz_soap(f'{base_url}{path}', body_xml, username, password,
                              [content_type] if content_type else None)
            if r.status_code < 400:
                return path, ct
        except Exception:
            continue
    raise Exception('No PTZ service path responded successfully')


def _ptz_move(base_url, username, password, token, pan, tilt, zoom, **route):
    return _ptz_send(base_url, username, password,
        '<tptz:ContinuousMove xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl">'
        f'<tptz:ProfileToken>{token}</tptz:ProfileToken>'
        '<tptz:Velocity>'
        f'<tt:PanTilt xmlns:tt="http://www.onvif.org/ver10/schema" x="{pan:.2f}" y="{tilt:.2f}"/>'
        f'<tt:Zoom xmlns:tt="http://www.onvif.org/ver10/schema" x="{zoom:.2f}"/>'
        '</tptz:Velocity></tptz:ContinuousMove>', **route
    )


def _ptz_stop(base_url, username, password, token, **route):
    return _ptz_send(base_url, username, password,
        '<tptz:Stop xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl">'
        f'<tptz:ProfileToken>{token}</tptz:ProfileToken>'
        '<tptz:PanTilt>true</tptz:PanTilt><tptz:Zoom>true</tptz:Zoom>'
        '</tptz:Stop>', **route
    )


def _ptz_home(base_url, username, password, token, **route):
    return _ptz_send(base_url, username, password,
        '<tptz:GotoHomePosition xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl">'
        f'<tptz:ProfileToken>{token}</tptz:ProfileToken>'
        '</tptz:GotoHomePosition>', **route
    )


def _try_cgi_ptz(base_url, username, password, command, speed_int, cgi_form=None):
    """CGI fallback for cameras that use HTTP but not ONVIF (Dahua/Hi3510 style).

    Returns the index of the URL form that worked so it can be reused.
    """
    import requests as _rq
    dahua_code = {
        'up': 'Up', 'down': 'Down', 'left': 'Left', 'right': 'Right',
        'zoom_in': 'ZoomTele', 'zoom_out': 'ZoomWide', 'stop': 'Stop', 'home': 'GotoPreset',
    }.get(command, 'Stop')
    hi3510_act = {
        'up': 'up', 'down': 'down', 'left': 'left', 'right': 'right',
        'zoom_in': 'zoomadd', 'zoom_out': 'zoomdec', 'stop': 'stop', 'home': 'poscall',
    }.get(command, 'stop')
    forms = [
        f'{base_url}/cgi-bin/ptz.cgi?action=start&channel=1&code={dahua_code}&arg1=0&arg2={speed_int}&arg3=0',
        f'{base_url}/cgi-bin/ptzctrl.cgi?ptzcmd&{hi3510_act}&{speed_int}',
        f'{base_url}/cgi-bin/hi3510/ptzctrl.cgi?-step=0&-act={hi3510_act}&-speed={speed_int}',
    ]
    candidates = ([(cgi_form, forms[cgi_form])] if cgi_form is not None
                  else list(enumerate(forms)))
    errors = []
    for idx, url in candidates:
        try:
            r = _rq.get(url, auth=(username, password), timeout=3)
            if r.status_code < 400:
                return idx
            errors.append(f'{r.status_code}')
        except Exception as e:
            errors.append(str(e))
    raise Exception('CGI PTZ failed: ' + '; '.join(errors))


class CameraViewSet(AuditedViewSetMixin, viewsets.ModelViewSet):
    queryset           = Camera.objects.all()
    serializer_class   = CameraSerializer
    permission_classes = [permissions.IsAuthenticated]
    audit_label        = 'Camera'

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
        cam = serializer.save(cam_number=num, name=f'Cam {num}')
        audit(request, AuditLog.Action.RECORD_CREATED,
              f"Camera added | {cam} | IP: {cam.ip} | By: {request.user.full_name}")
        return Response(serializer.data, status=drf_status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        # super() keeps the audit-log entry; we only add cache invalidation.
        # IP/credentials may have changed, so the discovered PTZ route is no
        # longer trustworthy — force rediscovery on the next command.
        super().perform_update(serializer)
        _ptz_cache_clear(serializer.instance.pk)

    def perform_destroy(self, instance):
        cam_id = instance.pk          # delete() clears the pk
        super().perform_destroy(instance)
        _ptz_cache_clear(cam_id)

    @action(detail=False, methods=['get'], url_path='next-name')
    def next_name(self, request):
        n = self._next_cam_number()
        return Response({'cam_number': n, 'name': f'Cam {n}'})

    @action(detail=False, methods=['post'], url_path='detect-rtsp')
    def detect_rtsp(self, request):
        """Ask the camera which stream path it answers on.

        Replaces the vendor picker in the add-camera form: which firmware a unit
        runs is not something the person mounting it should have to know, and
        the camera can be asked directly.
        """
        from . import rtsp_probe

        result = rtsp_probe.detect(
            ip=request.data.get('ip', ''),
            device_id=request.data.get('device_id', ''),
            password=request.data.get('password', ''),
        )
        return Response(result, status=(status.HTTP_200_OK if result['ok']
                                        else status.HTTP_400_BAD_REQUEST))

    @action(detail=True, methods=['post'], url_path='ping')
    def ping(self, request, pk=None):
        import socket
        cam = self.get_object()
        try:
            with socket.create_connection((cam.ip, 554), timeout=3):
                return Response({'reachable': True, 'ip': cam.ip})
        except (socket.timeout, ConnectionRefusedError, OSError):
            return Response({'reachable': False, 'ip': cam.ip})

    @action(detail=True, methods=['post'], url_path='ptz')
    def ptz(self, request, pk=None):
        import socket
        cam     = self.get_object()
        command = (request.data.get('command') or 'stop').strip()
        speed   = min(max(float(request.data.get('speed', 0.5)), 0.1), 1.0)

        vel_map = {
            'up':       ( 0.0,   speed,  0.0),
            'down':     ( 0.0,  -speed,  0.0),
            'left':     (-speed,  0.0,   0.0),
            'right':    ( speed,  0.0,   0.0),
            'zoom_in':  ( 0.0,   0.0,   speed),
            'zoom_out': ( 0.0,   0.0,  -speed),
        }

        speed_int = max(1, round(speed * 10))  # CGI uses integer speeds 1-10

        def _send(base, route):
            """Run `command` against `base`. Returns the route that worked."""
            if route.get('method') == 'cgi':
                idx = _try_cgi_ptz(base, cam.device_id, cam.password, command,
                                   speed_int, route.get('cgi_form'))
                return {**route, 'method': 'cgi', 'cgi_form': idx}

            token      = route.get('token')
            media_path = route.get('media_path')
            media_ct   = route.get('media_ct')
            if not token:
                # Only pay for profile discovery when we haven't got a token
                # yet. A revoked token surfaces as a failed command below,
                # which clears the cache and triggers rediscovery.
                token, media_path, media_ct = _ptz_get_token(
                    base, cam.device_id, cam.password, media_path, media_ct,
                )
            kw = {'ptz_path': route.get('ptz_path'), 'content_type': route.get('ptz_ct')}
            if command == 'stop':
                ptz_path, ptz_ct = _ptz_stop(base, cam.device_id, cam.password, token, **kw)
            elif command == 'home':
                ptz_path, ptz_ct = _ptz_home(base, cam.device_id, cam.password, token, **kw)
            elif command in vel_map:
                pan, tilt, zoom = vel_map[command]
                ptz_path, ptz_ct = _ptz_move(base, cam.device_id, cam.password,
                                             token, pan, tilt, zoom, **kw)
            else:
                raise Exception(f'Unknown PTZ command: {command}')
            return {'method': 'onvif', 'token': token,
                    'media_path': media_path, 'media_ct': media_ct,
                    'ptz_path': ptz_path, 'ptz_ct': ptz_ct}

        # ── Fast path: reuse the route discovered on a previous press ──────────
        cached = _ptz_cache_get(cam.pk, cam.ip)
        if cached:
            try:
                route = _send(cached['base'], cached)
                _ptz_cache_set(cam.pk, {**route, 'ip': cam.ip, 'base': cached['base']})
                return Response({'ok': True, 'command': command,
                                 'method': route['method'], 'cached': True})
            except Exception:
                # Camera rebooted, moved, or firmware changed — rediscover below.
                _ptz_cache_clear(cam.pk)

        # ── Discovery: probe ports, ONVIF paths, then the CGI fallback ─────────
        last_err = 'No HTTP port reachable on the camera'
        for port in [80, 8080, 8000, 8899]:
            try:
                with socket.create_connection((cam.ip, port), timeout=1.5):
                    pass
            except OSError:
                continue
            base = f'http://{cam.ip}' if port == 80 else f'http://{cam.ip}:{port}'
            # Try ONVIF first, then CGI fallback
            onvif_err = None
            for method in ('onvif', 'cgi'):
                try:
                    route = _send(base, {'method': method})
                    _ptz_cache_set(cam.pk, {**route, 'ip': cam.ip, 'base': base})
                    return Response({'ok': True, 'command': command,
                                     'method': route['method'], 'cached': False})
                except Exception as exc:
                    if method == 'onvif':
                        onvif_err = str(exc)
                    else:
                        last_err = f'onvif: {onvif_err} | cgi: {exc}'

        return Response({'ok': False, 'error': f'PTZ unavailable: {last_err}'}, status=400)

    def get_queryset(self):
        qs = super().get_queryset()
        assignment = self.request.query_params.get('assignment')
        if assignment:
            qs = qs.filter(assignment=assignment)
        gate_id = self.request.query_params.get('gate_id')
        if gate_id:
            qs = qs.filter(gate_id=gate_id)
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

                # get_jpeg() encodes at most once per frame and shares the
                # result across every viewer, so N watchers cost the same as
                # one. Still run in the thread pool — the first viewer to reach
                # a new frame does the encode and must not block the loop.
                jpeg_bytes = await loop.run_in_executor(None, thread.get_jpeg)
                if jpeg_bytes is None:
                    yield _mjpeg_frame(_connecting)
                    await asyncio.sleep(0.1)
                    continue

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
from rest_framework.parsers import MultiPartParser, FormParser
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
        return bool(request.user and request.user.is_authenticated and request.user.role == 'admin')


class IsSecurityRole(permissions.BasePermission):
    """Guards only — issuing parking violations is their responsibility, not the
    admin's (admin handles events and placing parking boxes)."""
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role == 'security')


class AttributeDoubleParkingView(APIView):
    """A guard names the vehicle behind a double-parking alert. Resolves the plate
    or conduction number, issues a DOUBLE_PARKING violation using the boxed
    evidence photo captured at detection, and clears the alert so its card
    disappears."""
    permission_classes = [IsSecurityRole]

    def post(self, request):
        zone_id   = request.data.get('zone_id')
        space_ids = request.data.get('space_ids') or []
        plate     = (request.data.get('plate_number') or '').strip().upper().replace(' ', '')
        if not zone_id or not space_ids or not plate:
            return Response({'error': 'zone_id, space_ids and plate_number are required.'},
                            status=status.HTTP_400_BAD_REQUEST)

        vehicle = Vehicle.resolve(plate)
        if vehicle is None:
            return Response({'error': 'No vehicle found for that plate or conduction number.'},
                            status=status.HTTP_404_NOT_FOUND)

        # Pull the evidence captured when the straddle was detected and clear the alert.
        thread = parking_camera.get_thread(int(zone_id))
        evidence = thread.pop_alert(space_ids) if thread is not None else None

        from scanning.views import _auto_log_violation
        from violations.models import Violation
        gate_id = getattr(request.user, 'gate_assignment', None) or 'main'
        _auto_log_violation(
            vehicle,
            f"Double parking attributed by guard {request.user.full_name}",
            gate_id=gate_id,
            vtype=Violation.Type.DOUBLE_PARKING,
            evidence_bytes=evidence,
        )

        try:
            from realtime.broadcast import broadcast_change
            broadcast_change('parkingspace', 'double_parking_attributed', zone_id=int(zone_id))
        except Exception:
            logger.exception("double-parking attribution broadcast failed")

        return Response({'status': 'attributed',
                         'plate_number': vehicle.plate_number or vehicle.conduction_number or plate})


class PendingRegistrationsListView(APIView):
    permission_classes = [IsAdminOrCdso]

    def get(self, request):
        status_filter = request.query_params.get('status', VehicleRegistration.Status.PENDING)
        # select_related pulls the department in the same SELECT, and the
        # prebuilt block-count map replaces one COUNT per row with one query for
        # the page — together they turn a 2N+1 query pattern into a flat 2.
        registrations = list(
            VehicleRegistration.objects
            .filter(status=status_filter)
            .select_related('department')
            .order_by('-created_at')
        )
        return Response(VehicleRegistrationSerializer(
            registrations,
            many=True,
            context={
                'request': request,
                'block_counts': VehicleRegistrationSerializer.build_block_counts(registrations),
            },
        ).data)


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


def _normalize_plate(plate):
    return (plate or '').strip().upper().replace(' ', '')


# Registration forms use a rich vocabulary (Sedan, SUV, Tricycle, …) while the
# Vehicle model has fixed choices — map form values onto valid Vehicle.Type values.
_VEHICLE_TYPE_MAP = {
    'sedan':      Vehicle.Type.CAR,
    'suv':        Vehicle.Type.CAR,
    'car':        Vehicle.Type.CAR,
    'other':      Vehicle.Type.CAR,
    'motorcycle': Vehicle.Type.MOTORCYCLE,
    'tricycle':   Vehicle.Type.MOTORCYCLE,
    'e-bike':     Vehicle.Type.EBIKE,
    'ebike':      Vehicle.Type.EBIKE,
    'van':        Vehicle.Type.VAN,
    'truck':      Vehicle.Type.TRUCK,
    'bus':        Vehicle.Type.BUS,
}


def _vehicle_type_for(registration_vehicle_type):
    return _VEHICLE_TYPE_MAP.get(
        (registration_vehicle_type or '').strip().lower(), Vehicle.Type.CAR
    )


def _upsert_vehicle_for_registration(registration, user):
    """Create or adopt the Vehicle for an approved registration, keyed on
    whichever identifier the registration carries — real plate, or conduction
    number for a brand-new car. Stamps both fields so a later plate-swap can
    clear the conduction number. update_or_create adopts an existing unowned row
    (e.g. a plate first seen via a visitor pass)."""
    plate      = _normalize_plate(registration.plate_number)
    conduction = _normalize_plate(registration.conduction_number)
    defaults = {
        'vehicle_type':  _vehicle_type_for(registration.vehicle_type),
        'color':         registration.vehicle_color,
        'is_authorized': True,
        'user':          user,
    }
    if plate:
        defaults['conduction_number'] = conduction  # normally ''
        vehicle_obj, _ = Vehicle.objects.update_or_create(plate_number=plate, defaults=defaults)
    else:
        defaults['plate_number'] = plate            # ''
        vehicle_obj, _ = Vehicle.objects.update_or_create(conduction_number=conduction, defaults=defaults)
    return vehicle_obj


def _plate_conflict(plate_number, qs):
    plate_norm = _normalize_plate(plate_number)
    if not plate_norm:
        return None
    # Stored plates may vary in spacing/case, so compare normalized values.
    #
    # This normalisation runs in SQL, not Python. It used to pull every active
    # registration's plate over the wire and scan them in a loop — O(N) on
    # every submission and every keystroke of the availability check, which at
    # 2,000 rows already cost ~1.1s per call against Neon. The expression index
    # `vehreg_plate_norm` matches this exact expression, so Postgres answers it
    # with an index lookup instead.
    if qs.exclude(plate_number='').annotate(
        _plate_norm=Upper(Replace('plate_number', Value(' '), Value('')))
    ).filter(_plate_norm=plate_norm).exists():
        return "This plate number already has an active registration."
    # Unowned Vehicle rows are adopted by update_or_create at accept time,
    # so only plates already tied to an account are conflicts
    if Vehicle.objects.filter(plate_number__iexact=plate_norm, user__isnull=False).exists():
        return "This plate number is already registered to an existing vehicle pass."
    return None


def _email_conflict(email, qs):
    email_norm = (email or '').strip().lower()
    if not email_norm:
        return None
    # Stored emails are normalized on save, but compare defensively anyway.
    # Done in SQL against the `vehreg_email_norm` expression index — the old
    # Python loop fetched every active registration's email per call.
    if qs.exclude(email='').annotate(
        _email_norm=Lower('email')
    ).filter(_email_norm=email_norm).exists():
        return "This email address already has an active registration."
    # An email tied to an existing *live* account can't start a new pass. Archived
    # (expired) accounts keep their email but must not block re-registration.
    if User.objects.filter(email__iexact=email_norm, is_archived=False).exists():
        return "This email address is already tied to an existing account."
    return None


def _conduction_conflict(conduction_number, qs):
    """Conduction sticker equivalent of _plate_conflict: unique among active
    registrations and not already tied to an owned Vehicle.

    conduction_number is a new field, always normalized (upper, no spaces) on
    save, so both checks are exact indexed lookups — O(1)-ish, no table scan.
    """
    norm = _normalize_plate(conduction_number)
    if not norm:
        return None
    if qs.filter(conduction_number=norm).exists():
        return "This conduction number already has an active registration."
    if Vehicle.objects.filter(conduction_number=norm, user__isnull=False).exists():
        return "This conduction number is already tied to an existing vehicle pass."
    return None


def _license_conflict(drivers_license, qs):
    lic = (drivers_license or '').strip().upper()
    if not lic:
        return None
    # save() already stores this stripped and upper-cased, so an exact match is
    # the same test the Python loop was doing — but as an indexed lookup rather
    # than a fetch of every active registration's licence number.
    if qs.exclude(drivers_license='').filter(drivers_license=lic).exists():
        return "This driver's license already has an active registration."
    return None


def _id_conflict(registrant_type, student_id, employee_id, qs):
    # Exact, not __iexact: these are stripped on save and are numeric, so
    # case-folding buys nothing — but it wraps the column in UPPER(), which
    # stops Postgres using uniq_active_registration_student_id / _employee_id
    # and turns each check into a scan of every active registration.
    student_id = (student_id or '').strip()
    if registrant_type == 'student' and student_id:
        if qs.filter(registrant_type='student', student_id=student_id).exists():
            return "This student ID already has an active registration."

    employee_id = (employee_id or '').strip()
    if registrant_type == 'employee' and employee_id:
        if qs.filter(registrant_type='employee', employee_id=employee_id).exists():
            return "This employee ID already has an active registration."

    return None


def _registration_conflict(registrant_type, plate_number, email, student_id, employee_id,
                           drivers_license='', statuses=None, exclude_pk=None,
                           conduction_number=''):
    """
    Enforce 1:1 rules for registrations: plate/conduction number, email, driver's
    license, and student/employee ID may each belong to at most one active
    (pending/accepted) registration.
    Returns an error message string, or None if there is no conflict.
    """
    if statuses is None:
        statuses = [VehicleRegistration.Status.PENDING, VehicleRegistration.Status.ACCEPTED]
    qs = VehicleRegistration.objects.filter(status__in=statuses)
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)

    conflict = _plate_conflict(plate_number, qs)
    if conflict:
        return conflict
    conflict = _conduction_conflict(conduction_number, qs)
    if conflict:
        return conflict
    conflict = _email_conflict(email, qs)
    if conflict:
        return conflict
    conflict = _license_conflict(drivers_license, qs)
    if conflict:
        return conflict
    return _id_conflict(registrant_type, student_id, employee_id, qs)


def _registration_ban(plate_number, email, student_id, employee_id, conduction_number=''):
    """Hard block for people who reached the maximum number of violations and had
    their account archived on expiry (User.registration_banned). Their identity —
    email, plate, conduction number, or ID — may not start a new registration.
    Matched against the archived owners' now-EXPIRED registrations.

    Returns an error message string, or None if the applicant is not banned.
    """
    from accounts.models import User

    plate_norm      = _normalize_plate(plate_number)
    conduction_norm = _normalize_plate(conduction_number)
    email_norm = (email or '').strip().lower()
    student_id = (student_id or '').strip()
    employee_id = (employee_id or '').strip()

    conds = Q()
    if email_norm:
        conds |= Q(email__iexact=email_norm)
    if plate_norm:
        conds |= Q(plate_number__iexact=plate_norm)
    if conduction_norm:
        conds |= Q(conduction_number__iexact=conduction_norm)
    if student_id:
        conds |= Q(student_id__iexact=student_id)
    if employee_id:
        conds |= Q(employee_id__iexact=employee_id)
    if not conds:
        return None

    if VehicleRegistration.objects.filter(conds, user__registration_banned=True).exists():
        return ("This applicant reached the maximum number of traffic violations and is no "
                "longer eligible to register a vehicle pass. Please contact the CDSO office.")
    return None


def _license_db_conflict(drivers_license):
    """
    The DB has a partial unique index (uniq_active_registration_drivers_license):
    one license number per active registration. Pre-check it so applicants get a
    readable error instead of a 500 from the IntegrityError.

    Distinct from _license_conflict(drivers_license, qs) above: that one is
    scoped to an explicitly-built queryset (used inside _registration_conflict
    and the availability-check endpoint); this one queries the live table
    directly as a belt-and-suspenders check right before save().
    Returns an error message string, or None.
    """
    if isinstance(drivers_license, list):
        drivers_license = drivers_license[0] if drivers_license else ''
    license_clean = (drivers_license or '').strip()
    if not license_clean:
        return None
    active = [VehicleRegistration.Status.PENDING, VehicleRegistration.Status.ACCEPTED]
    if VehicleRegistration.objects.filter(
        status__in=active, drivers_license__iexact=license_clean
    ).exists():
        return (f"Driver's license {license_clean} is already on an active registration. "
                "Each license may only be tied to one registered vehicle — please contact "
                "the CDSO if this vehicle replaces a previous one.")
    return None


# Student levels whose registrants are minors and can never drive themselves
MINOR_STUDENT_LEVELS = ('jhs', 'elementary')


def _validate_authorized_driver(registrant_type, data):
    """
    Enforce the registrant/driver split for student registrations.
    JHS and Elementary students are minors, so an authorized adult driver
    (parent/guardian/authorized driver) is mandatory and self-driving is
    rejected even on direct API calls. When driver_name is present,
    drivers_license is understood to be the driver's license.
    Returns an error message string, or None if valid.
    """
    def _val(key):
        v = data.get(key, '')
        if isinstance(v, list):
            v = v[0] if v else ''
        return (v or '').strip()

    if registrant_type != 'student':
        return None

    level       = _val('student_level')
    driver_name = _val('driver_name')

    if level in MINOR_STUDENT_LEVELS and not driver_name:
        return ("Junior High and Elementary students are minors and cannot drive. "
                "An authorized driver (parent/guardian) is required.")

    if driver_name:
        if not _val('driver_relationship'):
            return "Please specify the authorized driver's relationship to the student."
        if not _val('drivers_license'):
            return "The authorized driver's license number is required."
    return None


class AcceptRegistrationView(APIView):
    permission_classes = [IsAdminOrCdso]

    def post(self, request, pk):
        registration = get_object_or_404(VehicleRegistration, pk=pk)
        if registration.status != VehicleRegistration.Status.PENDING:
            return Response({"error": "Only pending registrations can be accepted."}, status=status.HTTP_400_BAD_REQUEST)

        # 1:1 guard — the plate / email / student ID / employee ID must not already belong
        # to another accepted registration (covers duplicate pendings submitted
        # before this rule existed).
        conflict = _registration_conflict(
            registration.registrant_type,
            registration.plate_number,
            registration.email,
            registration.student_id,
            registration.employee_id,
            drivers_license=registration.drivers_license,
            statuses=[VehicleRegistration.Status.ACCEPTED],
            exclude_pk=registration.pk,
        )
        if conflict:
            return Response({"error": conflict}, status=status.HTTP_400_BAD_REQUEST)

        or_number = request.data.get('or_number', '').strip()
        if not or_number:
            return Response({"error": "Official Receipt (OR) number is required before accepting."}, status=status.HTTP_400_BAD_REQUEST)
        if not or_number.isdigit() or len(or_number) > 7:
            return Response({"error": "Official Receipt (OR) number must be at most 7 digits."}, status=status.HTTP_400_BAD_REQUEST)

        # A plate flagged by a 3rd-offense fee violation requires additional
        # review before it can be registered again. CDSO must explicitly
        # acknowledge the flag (soft block) to proceed.
        from violations.models import Violation
        blocks = Violation.registration_block_for_plate(registration.plate_number)
        block_count = blocks.count()
        if block_count and not request.data.get('acknowledge_block'):
            latest = blocks.first()
            return Response({
                "error": "registration_blocked",
                "detail": (
                    f"Plate {registration.plate_number} is flagged for additional review "
                    f"from a prior 3rd-offense violation. Confirm you have reviewed this "
                    f"before accepting."
                ),
                "registration_block": {
                    "count": block_count,
                    "latest_type": latest.get_violation_type_display(),
                    "latest_status": latest.get_status_display(),
                    "latest_issued_at": latest.issued_at.isoformat() if latest.issued_at else None,
                },
            }, status=status.HTTP_409_CONFLICT)

        # Admin may override campus_days (free day picker) and/or schedule group
        campus_days_override = request.data.get('campus_days', None)  # list or None
        schedule_override    = request.data.get('schedule', '').strip()
        special_case_reason  = request.data.get('special_case_reason', '').strip()

        # Early validation: up to 3 campus days is the normal allowance — granting
        # more than 3 makes it a special case that requires a reason.
        if campus_days_override is not None and isinstance(campus_days_override, list):
            valid_days = {'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'}
            _cleaned_check = [d for d in campus_days_override if d in valid_days]
            if len(_cleaned_check) > 3 and not special_case_reason:
                return Response(
                    {"error": "A reason is required when granting more than 3 campus days."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if User.objects.filter(email=registration.email, is_archived=False).exists():
             return Response({"error": "User with this email already exists."}, status=status.HTTP_400_BAD_REQUEST)

        # Create user with a secure temporary password
        temp_password = _generate_temp_password()
        owner_type  = {
            'student':  User.OwnerType.STUDENT,
            'employee': User.OwnerType.EMPLOYEE,
            'fetcher':  User.OwnerType.FETCHER,
        }.get(registration.registrant_type, User.OwnerType.EMPLOYEE)
        schedule    = registration.schedule or ('MWF' if registration.registrant_type == 'student' else 'ANY')
        campus_days = registration.campus_days or []
        user = User.objects.create_user(
            email=registration.email,
            full_name=registration.full_name,
            password=temp_password,
            role='vehicle_owner',
            must_change_password=True,
            owner_type=owner_type,
            schedule=schedule,
            campus_days=campus_days,
            contact=registration.contact_number,
            address=registration.address,
        )

        # Record that CDSO knowingly accepted a flagged plate (audit trail)
        if block_count:
            try:
                AuditLog.objects.create(
                    actor=request.user,
                    action=AuditLog.Action.USER_CREATED,
                    target_user=user,
                    details=(
                        f"Registration accepted despite registration block | "
                        f"Plate: {registration.plate_number} | "
                        f"Prior flagged violations: {block_count} | Reviewed by: {request.user.full_name}"
                    ),
                )
            except Exception:
                pass

        # Create or update Vehicle linked directly to User, keyed on the
        # registration's plate or conduction number (brand-new car).
        vehicle_obj = _upsert_vehicle_for_registration(registration, user)

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

            # More than 3 campus days is a special case (validated above)
            if len(cleaned) > 3:
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

        # Sync final campus_days / schedule onto the user account so entry_logic
        # can check actual days rather than a fixed MWF/TTHS group.
        user.campus_days = registration.campus_days or []
        user.schedule    = registration.schedule or user.schedule
        user.save(update_fields=['campus_days', 'schedule'])

        # Refresh user to get generated user_code
        user.refresh_from_db()
        audit(request, AuditLog.Action.RECORD_UPDATED,
              f"Registration accepted | Plate: {registration.plate_number} | "
              f"Applicant: {registration.full_name} ({registration.registrant_type}) | "
              f"OR: {or_number} | By: {request.user.full_name}",
              target_user=user)
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
                "student_level": registration.student_level,
                "driver_name": registration.driver_name,
                "driver_relationship": registration.driver_relationship,
                "driver_contact": registration.driver_contact,
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

        audit(request, AuditLog.Action.RECORD_UPDATED,
              f"Registration rejected | Plate: {registration.plate_number} | "
              f"Applicant: {registration.full_name} | Reason: {reason} | By: {request.user.full_name}")

        # Send rejection email
        try:
            send_rejection_email(registration, reason)
        except Exception as e:
            import traceback
            print(f"[EMAIL ERROR] Failed to send rejection email to {registration.email}: {e}")
            traceback.print_exc()

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
        if not or_number.isdigit() or len(or_number) > 7:
            return Response({"error": "Official Receipt (OR) number must be at most 7 digits."}, status=status.HTTP_400_BAD_REQUEST)

        # 1:1 guard — plate, email and student/employee ID must not already have an active
        # registration (also blocks an email already tied to an existing account)
        conflict = _registration_conflict(
            registrant_type,
            request.data.get('plate_number', ''),
            request.data.get('email', ''),
            request.data.get('student_id', ''),
            request.data.get('employee_id', ''),
            drivers_license=request.data.get('drivers_license', ''),
        )
        if conflict:
            return Response({"error": conflict}, status=status.HTTP_400_BAD_REQUEST)

        driver_error = _validate_authorized_driver(registrant_type, request.data)
        if driver_error:
            return Response({"error": driver_error}, status=status.HTTP_400_BAD_REQUEST)

        license_error = _license_db_conflict(request.data.get('drivers_license', ''))
        if license_error:
            return Response({"error": license_error}, status=status.HTTP_400_BAD_REQUEST)

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
        owner_type  = {
            'student':  User.OwnerType.STUDENT,
            'employee': User.OwnerType.EMPLOYEE,
            'fetcher':  User.OwnerType.FETCHER,
        }.get(registrant_type, User.OwnerType.EMPLOYEE)
        schedule    = registration.schedule or ('MWF' if registrant_type == 'student' else 'ANY')
        campus_days = registration.campus_days or []

        user = User.objects.create_user(
            email=registration.email,
            full_name=registration.full_name,
            password=temp_password,
            role='vehicle_owner',
            must_change_password=True,
            owner_type=owner_type,
            schedule=schedule,
            campus_days=campus_days,
            contact=registration.contact_number,
            address=registration.address,
        )

        vehicle_obj = _upsert_vehicle_for_registration(registration, user)

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
    settings_obj = SystemSettings.get()
    fees = {
        "vehicle_pass_fee":          float(settings_obj.vehicle_pass_fee),
        "vehicle_pass_fee_employee": float(settings_obj.vehicle_pass_fee_employee),
        # Departments that pay nothing. Sent rather than hardcoded in the form so
        # the price shown to an applicant comes from the same place the backend
        # charges from — adding a department here updates both at once.
        "fee_exempt_departments": sorted(VehicleRegistration.FEE_EXEMPT_DEPARTMENTS),
        "department_options": [
            {"value": value, "label": label}
            for value, label in VehicleRegistration.DepartmentType.choices
        ],
    }
    period = RegistrationPeriod.get_active()
    if period:
        today = timezone.localdate()
        is_open = period.start_date <= today <= period.end_date
        return {
            "is_open": is_open,
            "open_date":  period.start_date.isoformat(),
            "close_date": period.end_date.isoformat(),
            "slot_limit": SCHEDULE_SLOT_LIMIT,
            **fees,
        }
    return {
        "is_open":    False,
        "open_date":  None,
        "close_date": None,
        "slot_limit": SCHEDULE_SLOT_LIMIT,
        **fees,
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


class RegistrationAvailabilityView(APIView):
    """Live duplicate check used to warn the user in the registration form's text boxes
    before they submit, e.g. 'This plate number already has an active registration.'"""
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        plate_number    = request.query_params.get('plate_number', '')
        email           = request.query_params.get('email', '')
        drivers_license = request.query_params.get('drivers_license', '')
        student_id      = request.query_params.get('student_id', '')
        employee_id     = request.query_params.get('employee_id', '')
        conduction      = request.query_params.get('conduction_number', '')

        statuses = [VehicleRegistration.Status.PENDING, VehicleRegistration.Status.ACCEPTED]
        qs = VehicleRegistration.objects.filter(status__in=statuses)

        return Response({
            'plate_number':      _plate_conflict(plate_number, qs),
            'conduction_number': _conduction_conflict(conduction, qs),
            'email':             _email_conflict(email, qs),
            'drivers_license':   _license_conflict(drivers_license, qs),
            'student_id':        _id_conflict('student', student_id, '', qs),
            'employee_id':       _id_conflict('employee', '', employee_id, qs),
            'banned':            _registration_ban(plate_number, email, student_id, employee_id,
                                                   conduction_number=conduction),
        })


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

        # A brand-new car registers with a conduction number instead of a plate.
        # Exactly one of the two must be provided — never both, never neither.
        plate_in      = (request.data.get('plate_number') or '').strip()
        conduction_in = (request.data.get('conduction_number') or '').strip()
        if plate_in and conduction_in:
            return Response(
                {"error": "Enter either a plate number or a conduction number, not both."},
                status=status.HTTP_400_BAD_REQUEST)
        if not plate_in and not conduction_in:
            return Response(
                {"error": "A plate number is required (or a conduction number for a brand-new vehicle)."},
                status=status.HTTP_400_BAD_REQUEST)

        # Hard block: applicants who reached the maximum number of violations and
        # were archived on expiry may never register again.
        ban = _registration_ban(
            request.data.get('plate_number', ''),
            request.data.get('email', ''),
            request.data.get('student_id', ''),
            request.data.get('employee_id', ''),
            conduction_number=request.data.get('conduction_number', ''),
        )
        if ban:
            return Response({"error": ban, "registration_banned": True}, status=status.HTTP_403_FORBIDDEN)

        # 1:1 guard — plate/conduction, email and student/employee ID must not
        # already have an active registration
        conflict = _registration_conflict(
            registrant_type,
            request.data.get('plate_number', ''),
            request.data.get('email', ''),
            request.data.get('student_id', ''),
            request.data.get('employee_id', ''),
            drivers_license=request.data.get('drivers_license', ''),
            conduction_number=request.data.get('conduction_number', ''),
        )
        if conflict:
            return Response({"error": conflict}, status=status.HTTP_400_BAD_REQUEST)

        data = dict(request.data)

        # The form sends department as a human-readable label ("Teaching",
        # "Non-Teaching", "Services", "Cleaning"). Map it to department_type and
        # clear the FK field so the serializer doesn't choke.
        #
        # Driven off DepartmentType rather than an if/elif chain: the chain
        # silently fell through to department=None for any label it did not
        # know, so a new department would have been accepted and stored blank.
        dept_raw = data.pop('department', None)
        if isinstance(dept_raw, list):
            dept_raw = dept_raw[0] if dept_raw else None

        dept_label_to_value = {
            label: value for value, label in VehicleRegistration.DepartmentType.choices
        }
        data['department'] = None
        if dept_raw in dept_label_to_value:
            data['department_type'] = dept_label_to_value[dept_raw]

        # Strip fields that are not model columns (e.g. form-only UI fields)
        for extra in ('last_name', 'first_name', 'middle_name',
                      'house_street', 'barangay', 'city_municipality', 'province',
                      'student_strand', 'student_grade',
                      'student_program', 'student_year',
                      'privacy_consent'):
            data.pop(extra, None)

        driver_error = _validate_authorized_driver(registrant_type, data)
        if driver_error:
            return Response({"error": driver_error}, status=status.HTTP_400_BAD_REQUEST)

        license_error = _license_db_conflict(data.get('drivers_license', ''))
        if license_error:
            return Response({"error": license_error}, status=status.HTTP_400_BAD_REQUEST)

        if registrant_type == 'fetcher':
            # Classification is required: drop_and_go (allotted times only) or
            # standby (allowed to park inside campus while waiting).
            fetcher_type = (data.get('fetcher_type') or '').strip()
            if fetcher_type not in ('drop_and_go', 'standby'):
                return Response(
                    {"error": "Please choose a fetcher classification: Fetcher/Drop & Go or Standby."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            # At least one student must be listed
            students = data.get('fetcher_students') or []
            if not isinstance(students, list) or len(students) == 0:
                return Response(
                    {"error": "At least one student must be listed on a fetcher registration."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            valid_levels = {c[0] for c in VehicleRegistration.StudentLevel.choices}
            cleaned_students = []
            for s in students:
                if not isinstance(s, dict):
                    return Response({"error": "Invalid student entry."}, status=status.HTTP_400_BAD_REQUEST)
                entry = {
                    'full_name':     (s.get('full_name') or '').strip(),
                    'student_id':    (s.get('student_id') or '').strip(),
                    'student_level': (s.get('student_level') or '').strip(),
                    'program_year':  (s.get('program_year') or '').strip(),
                }
                if not entry['full_name'] or not entry['student_id'] or not entry['student_level']:
                    return Response(
                        {"error": "Each student needs a full name, student ID and education level."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if entry['student_level'] not in valid_levels:
                    return Response({"error": "Invalid education level for a listed student."}, status=status.HTTP_400_BAD_REQUEST)
                cleaned_students.append(entry)
            data['fetcher_students'] = cleaned_students
        else:
            data.pop('fetcher_type', None)
            data.pop('fetcher_students', None)

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

            # Derive schedule group from selected days
            mwf_days  = {'Monday', 'Wednesday', 'Friday'}
            tths_days = {'Tuesday', 'Thursday', 'Saturday'}
            days_set  = set(campus_days)
            if days_set == mwf_days:
                data['schedule'] = 'MWF'
            elif days_set == tths_days:
                data['schedule'] = 'TTHS'
            elif days_set:
                data['schedule'] = 'MIXED'

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
            return Response(
                {"message": "Registration submitted successfully. Please wait for CDSO review.", "id": registration.id},
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class UploadLicenseImageView(APIView):
    """Public follow-up step to PublicOpenRegistrationView — attaches a photo of the
    driver's license to a just-submitted registration. Kept as a separate multipart
    request so the main JSON registration payload (with its nested campus_days /
    fetcher_students structures) doesn't have to be reworked into form-data."""
    permission_classes = [permissions.AllowAny]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        registration_id = request.data.get('registration_id')
        email = (request.data.get('email') or '').strip()
        image = request.FILES.get('image')

        if not registration_id or not email:
            return Response({"error": "registration_id and email are required."}, status=status.HTTP_400_BAD_REQUEST)
        if not image:
            return Response({"error": "No image uploaded."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            registration = VehicleRegistration.objects.get(
                id=registration_id,
                email__iexact=email,
                status=VehicleRegistration.Status.PENDING,
            )
        except VehicleRegistration.DoesNotExist:
            return Response({"error": "Registration not found."}, status=status.HTTP_404_NOT_FOUND)

        registration.drivers_license_image = image
        registration.save(update_fields=['drivers_license_image'])
        return Response({"message": "License image uploaded."}, status=status.HTTP_200_OK)


# ──────────────────────────────────────────────
# Department & Program lists (public, for registration form)
# ──────────────────────────────────────────────

class DepartmentListView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        names = list(ReferenceItem.objects.filter(category='department', is_active=True).values_list('name', flat=True))
        return Response(names)


class ProgramListView(APIView):
    """College program list — excludes legacy Senior High (Grade 11/12) strand
    entries, which now have their own Track/Strand + Grade Level pickers on
    the registration form and don't belong in the college program dropdown."""
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        names = list(
            ReferenceItem.objects.filter(category='program', is_active=True)
            .exclude(name__icontains='Grade 11')
            .exclude(name__icontains='Grade 12')
            .values_list('name', flat=True)
        )
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
            "vehicle_pass_fee":          float(obj.vehicle_pass_fee),
            "vehicle_pass_fee_employee": float(obj.vehicle_pass_fee_employee),
            "account_expiry_enabled": obj.account_expiry_enabled,
            "account_expiry_months":  obj.account_expiry_months,
            "account_expiry_days":    obj.account_expiry_days,
        }

    def get(self, request):
        return Response(self._serialize(SystemSettings.get()))

    def put(self, request):
        obj = SystemSettings.get()
        before = self._serialize(obj)
        errors = {}

        from datetime import date as date_type
        retention_years      = request.data.get("retention_years",    obj.retention_years)
        scan_dedup_seconds   = request.data.get("scan_dedup_seconds", obj.scan_dedup_seconds)
        event_mode_parking   = request.data.get("event_mode_parking", obj.event_mode_parking)
        event_mode_entry     = request.data.get("event_mode_entry",   obj.event_mode_entry)
        open_campus_mode     = request.data.get("open_campus_mode",   obj.open_campus_mode)
        registration_start   = request.data.get("registration_start", obj.registration_start)
        registration_end     = request.data.get("registration_end",   obj.registration_end)
        vehicle_pass_fee          = request.data.get("vehicle_pass_fee",          obj.vehicle_pass_fee)
        vehicle_pass_fee_employee = request.data.get("vehicle_pass_fee_employee", obj.vehicle_pass_fee_employee)
        account_expiry_enabled    = request.data.get("account_expiry_enabled", obj.account_expiry_enabled)
        account_expiry_months     = request.data.get("account_expiry_months",  obj.account_expiry_months)
        account_expiry_days       = request.data.get("account_expiry_days",    obj.account_expiry_days)

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

        try:
            vehicle_pass_fee = Decimal(str(vehicle_pass_fee))
            if vehicle_pass_fee < 0:
                errors["vehicle_pass_fee"] = "Must be zero or greater."
        except (TypeError, ValueError, InvalidOperation):
            errors["vehicle_pass_fee"] = "Must be a number."

        try:
            vehicle_pass_fee_employee = Decimal(str(vehicle_pass_fee_employee))
            if vehicle_pass_fee_employee < 0:
                errors["vehicle_pass_fee_employee"] = "Must be zero or greater."
        except (TypeError, ValueError, InvalidOperation):
            errors["vehicle_pass_fee_employee"] = "Must be a number."

        account_expiry_enabled = bool(account_expiry_enabled)
        try:
            account_expiry_months = int(account_expiry_months)
            if not (0 <= account_expiry_months <= 120):
                errors["account_expiry_months"] = "Must be between 0 and 120 months."
        except (TypeError, ValueError):
            errors["account_expiry_months"] = "Must be an integer."
        try:
            account_expiry_days = int(account_expiry_days)
            if not (0 <= account_expiry_days <= 365):
                errors["account_expiry_days"] = "Must be between 0 and 365 days."
        except (TypeError, ValueError):
            errors["account_expiry_days"] = "Must be an integer."
        if (account_expiry_enabled and not errors
                and account_expiry_months == 0 and account_expiry_days == 0):
            errors["account_expiry_months"] = "Set at least 1 month or day when expiration is enabled."

        if errors:
            return Response(errors, status=400)

        obj.retention_years    = retention_years
        obj.scan_dedup_seconds = scan_dedup_seconds
        obj.event_mode_parking = bool(event_mode_parking)
        obj.event_mode_entry   = bool(event_mode_entry)
        obj.open_campus_mode   = bool(open_campus_mode)
        obj.registration_start = registration_start
        obj.registration_end   = registration_end
        obj.vehicle_pass_fee          = vehicle_pass_fee
        obj.vehicle_pass_fee_employee = vehicle_pass_fee_employee

        was_expiry_enabled = obj.account_expiry_enabled
        obj.account_expiry_enabled = account_expiry_enabled
        obj.account_expiry_months  = account_expiry_months
        obj.account_expiry_days    = account_expiry_days
        obj.save()

        # Backfill existing owners the first time expiry is turned on, using the
        # duration the admin just chose. Frozen-at-creation semantics: only owners
        # that don't already have an expires_at are touched.
        if account_expiry_enabled and not was_expiry_enabled and (account_expiry_months or account_expiry_days):
            from datetime import timedelta
            from dateutil.relativedelta import relativedelta
            from accounts.models import User as _User
            owners = list(_User.objects.filter(
                role='vehicle_owner', is_active=True, is_archived=False, expires_at__isnull=True,
            ))
            for owner in owners:
                owner.expires_at = (owner.date_joined.date()
                                    + relativedelta(months=account_expiry_months)
                                    + timedelta(days=account_expiry_days))
            if owners:
                _User.objects.bulk_update(owners, ['expires_at'])

        after   = self._serialize(obj)
        changed = [f"{k}: {before[k]} -> {after[k]}" for k in after if before[k] != after[k]]
        if changed:
            audit(request, AuditLog.Action.RECORD_UPDATED,
                  f"System Settings updated | {'; '.join(changed)} | By: {request.user.full_name}")

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
            toggles = '; '.join(f"{f}: {getattr(obj, f)}" for f in update_fields)
            audit(request, AuditLog.Action.RECORD_UPDATED,
                  f"System Settings updated | {toggles} | By: {request.user.full_name}")
        return Response(self._serialize(obj))


# ──────────────────────────────────────────────
# Events (Admin/CDSO manage campus events + organizer plates)
# ──────────────────────────────────────────────

class EventListCreateView(APIView):
    permission_classes = [IsAdminOrCdso]

    def _serialize(self, ev):
        return {
            'id':               ev.id,
            'name':             ev.name,
            'date':             ev.date.isoformat(),
            'is_active':        ev.is_active,
            'archived':         ev.archived,
            'organizer_plates': ev.organizer_plates,
            'created_at':       ev.created_at.isoformat(),
            'created_by_name':  ev.created_by.full_name if ev.created_by else None,
        }

    def get(self, request):
        events = Event.objects.select_related('created_by').all()
        return Response([self._serialize(e) for e in events])

    def post(self, request):
        name             = (request.data.get('name') or '').strip()
        date_str         = request.data.get('date')
        organizer_plates = request.data.get('organizer_plates', [])

        if not name:
            return Response({'name': 'Name is required.'}, status=400)
        if not date_str:
            return Response({'date': 'Date is required.'}, status=400)

        try:
            from datetime import datetime as _dt
            date_obj = _dt.strptime(str(date_str), '%Y-%m-%d').date()
        except ValueError:
            return Response({'date': 'Invalid date format. Use YYYY-MM-DD.'}, status=400)

        plates = [p.strip().upper() for p in (organizer_plates or []) if p.strip()]
        ev = Event.objects.create(
            name=name, date=date_obj, organizer_plates=plates, created_by=request.user,
        )
        audit(request, AuditLog.Action.RECORD_CREATED,
              f"Event added | {ev.name} on {ev.date} | Organizer plates: {len(plates)} | By: {request.user.full_name}")
        return Response(self._serialize(ev), status=201)


class EventDetailView(APIView):
    permission_classes = [IsAdminOrCdso]

    def _serialize(self, ev):
        return {
            'id':               ev.id,
            'name':             ev.name,
            'date':             ev.date.isoformat(),
            'is_active':        ev.is_active,
            'archived':         ev.archived,
            'organizer_plates': ev.organizer_plates,
            'created_at':       ev.created_at.isoformat(),
            'created_by_name':  ev.created_by.full_name if ev.created_by else None,
        }

    def patch(self, request, pk):
        try:
            ev = Event.objects.select_related('created_by').get(pk=pk)
        except Event.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=404)

        if 'name' in request.data:
            name = (request.data['name'] or '').strip()
            if not name:
                return Response({'name': 'Name cannot be empty.'}, status=400)
            ev.name = name

        if 'date' in request.data:
            try:
                from datetime import datetime as _dt, date as _date
                new_date = _dt.strptime(str(request.data['date']), '%Y-%m-%d').date()
                ev.date = new_date
                today = _date.today()
                # Rescheduling unarchives the event; activation follows the new date
                ev.archived  = False
                ev.is_active = (new_date == today)
            except ValueError:
                return Response({'date': 'Invalid date format.'}, status=400)

        if 'is_active' in request.data:
            ev.is_active = bool(request.data['is_active'])

        if 'organizer_plates' in request.data:
            plates = [p.strip().upper() for p in (request.data['organizer_plates'] or []) if p.strip()]
            ev.organizer_plates = plates

        ev.save()
        audit(request, AuditLog.Action.RECORD_UPDATED,
              f"Event updated | {ev.name} on {ev.date} | By: {request.user.full_name}")
        return Response(self._serialize(ev))

    def delete(self, request, pk):
        try:
            ev = Event.objects.get(pk=pk)
        except Event.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=404)
        desc = f"{ev.name} on {ev.date}"
        ev.delete()
        audit(request, AuditLog.Action.RECORD_DELETED,
              f"Event deleted | {desc} | By: {request.user.full_name}")
        return Response(status=204)


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
        """CDSO (admin) create and broadcast a notice to all vehicle owners."""
        if request.user.role != 'admin':
            return Response({'error': 'Permission denied.'}, status=403)

        serializer = ParkingNoticeSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)
        notice = serializer.save(created_by=request.user)
        audit(request, AuditLog.Action.RECORD_CREATED,
              f"Parking notice broadcast | {notice.title} | By: {request.user.full_name}")

        # Email blast to all active vehicle owners
        from accounts.models import User as UserModel
        from django.core.mail import EmailMultiAlternatives
        from django.conf import settings

        recipients = list(
            UserModel.objects.filter(role='vehicle_owner', is_active=True)
            .values_list('email', flat=True)
        )
        email_status = 'no_recipients'
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
            # BCC so owners never see each other's addresses
            try:
                email = EmailMultiAlternatives(
                    subject=f"SLC Parking Notice: {notice.title}",
                    body=f"Parking Notice\n\n{notice.title}\n\n{notice.body}",
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    to=[settings.DEFAULT_FROM_EMAIL],
                    bcc=recipients,
                )
                email.attach_alternative(html_msg, 'text/html')
                email.send(fail_silently=False)
                email_status = 'sent'
            except Exception as e:
                import traceback
                print(f"[EMAIL ERROR] Parking notice broadcast failed: {e}")
                traceback.print_exc()
                email_status = 'failed'

        data = ParkingNoticeSerializer(notice).data
        data['email_status'] = email_status
        data['recipient_count'] = len(recipients)
        return Response(data, status=201)


class ParkingNoticeDetailView(APIView):
    def get_permissions(self):
        return [permissions.IsAuthenticated()]

    def delete(self, request, pk):
        """CDSO (admin) deactivate (soft-delete) a notice."""
        if request.user.role != 'admin':
            return Response({'error': 'Permission denied.'}, status=403)
        notice = get_object_or_404(ParkingNotice, pk=pk)
        notice.is_active = False
        notice.save(update_fields=['is_active'])
        audit(request, AuditLog.Action.RECORD_DELETED,
              f"Parking notice removed | {notice.title} | By: {request.user.full_name}")
        return Response({'message': 'Notice deactivated.'}, status=200)


# ──────────────────────────────────────────────
# Registration Period management (Admin/CDSO)
# ──────────────────────────────────────────────

def _serialize_period(p):
    return {
        'id':         p.id,
        'label':      p.label,
        'start_date': p.start_date.isoformat(),
        'end_date':   p.end_date.isoformat(),
        'is_active':  p.is_active,
        'created_at': p.created_at.isoformat(),
    }


class RegistrationPeriodListCreateView(APIView):
    def get_permissions(self):
        if self.request.method == 'GET':
            return [permissions.IsAuthenticated()]
        return [IsAdminOrCdso()]

    def get(self, request):
        return Response([_serialize_period(p) for p in RegistrationPeriod.objects.all()])

    def post(self, request):
        from datetime import datetime as _dt
        label      = (request.data.get('label') or '').strip()
        start_raw  = request.data.get('start_date')
        end_raw    = request.data.get('end_date')
        errors = {}
        if not label:
            errors['label'] = 'Label is required.'

        start = end = None
        try:
            start = _dt.strptime(str(start_raw), '%Y-%m-%d').date()
        except (ValueError, TypeError):
            errors['start_date'] = 'Required. Use YYYY-MM-DD.'
        try:
            end = _dt.strptime(str(end_raw), '%Y-%m-%d').date()
        except (ValueError, TypeError):
            errors['end_date'] = 'Required. Use YYYY-MM-DD.'

        if errors:
            return Response(errors, status=400)
        if end < start:
            return Response({'end_date': 'End date must be on or after start date.'}, status=400)

        RegistrationPeriod.objects.filter(is_active=True).update(is_active=False)
        period = RegistrationPeriod.objects.create(label=label, start_date=start, end_date=end, is_active=True)
        audit(request, AuditLog.Action.RECORD_CREATED,
              f"Registration period added | {period.label} ({period.start_date} to {period.end_date}) | By: {request.user.full_name}")
        return Response(_serialize_period(period), status=201)


class RegistrationPeriodActivateView(APIView):
    permission_classes = [IsAdminOrCdso]

    def post(self, request, pk):
        """Set this period as the active one (deactivates all others)."""
        period = get_object_or_404(RegistrationPeriod, pk=pk)
        RegistrationPeriod.objects.filter(is_active=True).update(is_active=False)
        period.is_active = True
        period.save(update_fields=['is_active'])
        audit(request, AuditLog.Action.RECORD_UPDATED,
              f"Registration period activated | {period.label} | By: {request.user.full_name}")
        return Response(_serialize_period(period))

    def delete(self, request, pk):
        """Deactivate without deleting — archives the period."""
        period = get_object_or_404(RegistrationPeriod, pk=pk)
        period.is_active = False
        period.save(update_fields=['is_active'])
        audit(request, AuditLog.Action.RECORD_UPDATED,
              f"Registration period archived | {period.label} | By: {request.user.full_name}")
        return Response(_serialize_period(period))


# ──────────────────────────────────────────────
# Supplier Management (Admin only)
# ──────────────────────────────────────────────

from .models import Supplier, SupplierPlate
from .serializers import SupplierSerializer, SupplierPlateSerializer


class SupplierListCreateView(APIView):
    permission_classes = [IsAdminRole]

    def get(self, request):
        suppliers = Supplier.objects.prefetch_related('plates').all()
        return Response(SupplierSerializer(suppliers, many=True).data)

    def post(self, request):
        company_name = (request.data.get('company_name') or '').strip()
        if not company_name:
            return Response({'company_name': 'Company name is required.'}, status=400)
        if Supplier.objects.filter(company_name__iexact=company_name).exists():
            return Response({'company_name': 'A supplier with this name already exists.'}, status=400)

        # Store plates in the same normalized form scans use (no spaces),
        # otherwise gate lookups can never match them
        plate_numbers = list(dict.fromkeys(
            _normalize_plate(p) for p in (request.data.get('plates') or []) if p and p.strip()
        ))
        if plate_numbers:
            existing = SupplierPlate.objects.filter(plate_number__in=plate_numbers).values_list('plate_number', flat=True)
            if existing:
                return Response({'plates': f"Plate(s) already registered: {', '.join(existing)}."}, status=400)

        category = request.data.get('category') or Supplier.Category.OTHER
        if category not in Supplier.Category.values:
            return Response({'category': 'Invalid supplier category.'}, status=400)

        supplier = Supplier.objects.create(company_name=company_name, category=category)
        SupplierPlate.objects.bulk_create(
            SupplierPlate(supplier=supplier, plate_number=p) for p in plate_numbers
        )
        audit(request, AuditLog.Action.RECORD_CREATED,
              f"Supplier added | {supplier.company_name} | Plates: {', '.join(plate_numbers) or 'none'} | By: {request.user.full_name}")
        return Response(SupplierSerializer(supplier).data, status=201)


class SupplierDetailView(APIView):
    permission_classes = [IsAdminRole]

    def patch(self, request, pk):
        supplier = get_object_or_404(Supplier, pk=pk)
        if 'company_name' in request.data:
            name = (request.data['company_name'] or '').strip()
            if not name:
                return Response({'company_name': 'Company name cannot be empty.'}, status=400)
            supplier.company_name = name
        if 'is_active' in request.data:
            supplier.is_active = bool(request.data['is_active'])
        if 'category' in request.data:
            category = request.data['category']
            if category not in Supplier.Category.values:
                return Response({'category': 'Invalid supplier category.'}, status=400)
            supplier.category = category
        supplier.save()
        audit(request, AuditLog.Action.RECORD_UPDATED,
              f"Supplier updated | {supplier.company_name} | Active: {supplier.is_active} | By: {request.user.full_name}")
        return Response(SupplierSerializer(supplier).data)

    def delete(self, request, pk):
        supplier = get_object_or_404(Supplier, pk=pk)
        name = supplier.company_name
        supplier.delete()
        audit(request, AuditLog.Action.RECORD_DELETED,
              f"Supplier deleted | {name} | By: {request.user.full_name}")
        return Response(status=204)


class SupplierPlateView(APIView):
    """Add or remove a plate for a specific supplier."""
    permission_classes = [IsAdminRole]

    def post(self, request, pk):
        supplier = get_object_or_404(Supplier, pk=pk)
        # Same normalized form scans use (no spaces) so gate lookups match
        plate_number = _normalize_plate(request.data.get('plate_number'))
        if not plate_number:
            return Response({'plate_number': 'Plate number is required.'}, status=400)
        if SupplierPlate.objects.filter(plate_number=plate_number).exists():
            return Response({'plate_number': 'This plate is already registered to a supplier.'}, status=400)
        sp = SupplierPlate.objects.create(supplier=supplier, plate_number=plate_number)
        audit(request, AuditLog.Action.RECORD_CREATED,
              f"Supplier plate added | {plate_number} to {supplier.company_name} | By: {request.user.full_name}")
        return Response(SupplierPlateSerializer(sp).data, status=201)

    def delete(self, request, pk, plate_pk):
        plate = get_object_or_404(SupplierPlate, pk=plate_pk, supplier_id=pk)
        desc = f"{plate.plate_number} from {plate.supplier.company_name}"
        plate.delete()
        audit(request, AuditLog.Action.RECORD_DELETED,
              f"Supplier plate removed | {desc} | By: {request.user.full_name}")
        return Response(status=204)


# ── Vehicle Registrations Report (CDSO/admin — branded PDF & Excel) ──────────
REGISTRATION_REPORT_HEADERS = ['#', 'Date', 'Plate', 'Registrant', 'Type', 'Vehicle', 'Status']


def _filter_registrations_report(request):
    """Filter registrations for a report — mirrors the management page knobs."""
    qs = VehicleRegistration.objects.all()
    date_from = request.query_params.get('date_from', '').strip()
    date_to   = request.query_params.get('date_to', '').strip()
    status_f  = request.query_params.get('status', '').strip()
    search    = request.query_params.get('search', '').strip()
    qs = filter_local_date_range(qs, 'created_at', date_from, date_to)
    if status_f:
        qs = qs.filter(status=status_f)
    if search:
        qs = qs.filter(Q(plate_number__icontains=search) | Q(full_name__icontains=search))

    status_labels = dict(VehicleRegistration.Status.choices)
    desc = []
    if date_from or date_to:
        desc.append(f"Period: {date_from or 'start'} to {date_to or 'today'}")
    if status_f:
        desc.append(f"Status: {status_labels.get(status_f, status_f)}")
    if search:
        desc.append(f"Search: '{search}'")
    return qs.order_by('-created_at'), desc


def _registration_report_rows(qs):
    from django.utils import timezone as tz
    reg_labels    = dict(VehicleRegistration.RegistrantType.choices)
    status_labels = dict(VehicleRegistration.Status.choices)
    rows = []
    for i, r in enumerate(qs, start=1):
        rows.append([
            i,
            tz.localtime(r.created_at).strftime('%b %d, %Y'),
            r.plate_number or '—',
            r.full_name or '—',
            reg_labels.get(r.registrant_type, r.registrant_type or '—'),
            r.vehicle_type or '—',
            status_labels.get(r.status, r.status),
        ])
    return rows


def _registration_report_subtitle(desc, count):
    return ('; '.join(desc) if desc else 'All records') + f" · {count} entries"


class RegistrationReportExcelView(APIView):
    """Download the (filtered) vehicle registrations as a branded Excel report — admin only."""
    permission_classes = [IsAdminOrCdso]

    def get(self, request):
        from django.utils import timezone as tz
        from report_utils import branded_excel_response, report_filename
        qs, desc = _filter_registrations_report(request)
        rows = _registration_report_rows(qs[:5000])
        subtitle = (f"Generated {tz.localtime().strftime('%B %d, %Y %I:%M %p')} "
                    f"by {getattr(request.user, 'full_name', '')} · "
                    + _registration_report_subtitle(desc, len(rows)))
        return branded_excel_response(
            filename=report_filename('Vehicle Registrations Report', 'xlsx'),
            sheet_title='Registrations',
            report_title='Vehicle Registrations Report',
            subtitle=subtitle,
            headers=REGISTRATION_REPORT_HEADERS,
            rows=rows,
            col_widths=[5, 16, 16, 28, 14, 16, 14],
        )


class RegistrationReportPdfView(APIView):
    """Download the (filtered) vehicle registrations as a branded PDF report — admin only."""
    permission_classes = [IsAdminOrCdso]

    def get(self, request):
        from django.utils import timezone as tz
        from report_utils import branded_pdf_response, report_filename
        qs, desc = _filter_registrations_report(request)
        rows = _registration_report_rows(qs[:5000])
        return branded_pdf_response(
            filename=report_filename('Vehicle Registrations Report', 'pdf'),
            report_title='Vehicle Registrations Report',
            subtitle=_registration_report_subtitle(desc, len(rows)),
            generated_by=getattr(request.user, 'full_name', ''),
            headers=REGISTRATION_REPORT_HEADERS,
            rows=rows,
            col_widths_mm=[10, 30, 30, 60, 40, 40, 27],
        )


class ScheduledVisitListCreateView(APIView):
    """Advance coordination for visitors/suppliers — lets CDSO log who is
    expected on a given day, before they show up at the gate."""
    permission_classes = [IsAdminRole]

    def get(self, request):
        visits = ScheduledVisit.objects.select_related('supplier').all()
        upcoming_only = request.query_params.get('upcoming')
        if upcoming_only:
            visits = visits.filter(expected_date__gte=timezone.localdate(), is_arrived=False)
        return Response(ScheduledVisitSerializer(visits, many=True).data)

    def post(self, request):
        visitor_name = (request.data.get('visitor_name') or '').strip()
        expected_date = request.data.get('expected_date')
        category = request.data.get('category') or ScheduledVisit.Category.OTHER

        if not visitor_name:
            return Response({'visitor_name': 'Name is required.'}, status=400)
        if not expected_date:
            return Response({'expected_date': 'Expected date is required.'}, status=400)
        if category not in ScheduledVisit.Category.values:
            return Response({'category': 'Invalid category.'}, status=400)

        supplier = None
        supplier_id = request.data.get('supplier')
        if supplier_id:
            supplier = get_object_or_404(Supplier, pk=supplier_id)

        visit = ScheduledVisit.objects.create(
            visitor_name=visitor_name,
            category=category,
            supplier=supplier,
            plate_number=_normalize_plate(request.data.get('plate_number') or ''),
            purpose=(request.data.get('purpose') or '').strip(),
            expected_date=expected_date,
            notes=(request.data.get('notes') or '').strip(),
        )
        audit(request, AuditLog.Action.RECORD_CREATED,
              f"Scheduled visit added | {visitor_name} expected {expected_date} | By: {request.user.full_name}")
        return Response(ScheduledVisitSerializer(visit).data, status=201)


class ScheduledVisitDetailView(APIView):
    permission_classes = [IsAdminRole]

    def patch(self, request, pk):
        visit = get_object_or_404(ScheduledVisit, pk=pk)
        if 'is_arrived' in request.data:
            visit.is_arrived = bool(request.data['is_arrived'])
        visit.save()
        return Response(ScheduledVisitSerializer(visit).data)

    def delete(self, request, pk):
        visit = get_object_or_404(ScheduledVisit, pk=pk)
        desc = f"{visit.visitor_name} ({visit.expected_date})"
        visit.delete()
        audit(request, AuditLog.Action.RECORD_DELETED,
              f"Scheduled visit removed | {desc} | By: {request.user.full_name}")
        return Response(status=204)