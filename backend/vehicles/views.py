import logging
import secrets
import string
import threading
import time as _time
import uuid
from decimal import Decimal, InvalidOperation

import cv2
from django.db import transaction
from django.db.models import Count, Q, Value
from django.db.models.functions import Lower, Replace, Upper
from django.http import StreamingHttpResponse, HttpResponse
from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response

from rest_framework import status as drf_status
from .models import Vehicle, RuleConstraint, ParkingSpace, ParkingZone, ReferenceItem, Camera, SystemSettings, ParkingNotice, RegistrationPeriod, Event, ScheduledVisit
from .models import _normalize_plate
from .serializers import VehicleSerializer, RuleConstraintSerializer, ParkingSpaceSerializer, ParkingZoneSerializer, ReferenceItemSerializer, CameraSerializer, ParkingNoticeSerializer, ScheduledVisitSerializer
from . import parking_camera

logger = logging.getLogger(__name__)
from accounts.audit import audit, AuditedViewSetMixin
from accounts.twofa_api import HasRecentTwoFactor
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

    @staticmethod
    def _profile_payload(vehicle):
        """Owner, latest accepted registration and violations for one vehicle.

        Shared by `profile` (by row id) and `by_plate` (by a typed or detected
        plate) so the two can never drift into telling a guard different things
        about the same car.
        """
        from violations.serializers import ViolationSerializer
        from .models import VehicleRegistration
        from .serializers import VehicleRegistrationSerializer

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

        return {
            'vehicle':             VehicleSerializer(vehicle).data,
            'registration':        VehicleRegistrationSerializer(reg).data if reg else None,
            'active_violations':   ViolationSerializer(active_violations, many=True).data,
            'resolved_violations': ViolationSerializer(resolved_violations, many=True).data,
        }

    @action(detail=True, methods=['get'])
    def profile(self, request, pk=None):
        """Return full vehicle profile: owner, latest registration, active and resolved violations."""
        return Response(self._profile_payload(self.get_object()))

    @action(detail=False, methods=['get'], url_path='by-plate')
    def by_plate(self, request):
        """Same profile, found by plate or conduction number instead of row id.

        Parking needs this: a bay carries only the plate the detector read off
        the car, and asking who that is must not record anything. The obvious
        alternative — /scan/manual-entry/ — answers the same question but logs
        a gate entry as a side effect, which would put a car through the
        barrier because someone clicked a parking space.

        A plate nobody registered is a normal answer here, not an error: the
        lot is full of visitors and delivery vehicles. It comes back as
        `found: false` so the caller can say so plainly.
        """
        identifier = request.query_params.get('plate', '')
        vehicle = Vehicle.resolve(identifier)
        if vehicle is None:
            return Response({
                'found': False,
                'plate': _normalize_plate(identifier),
            })
        return Response({'found': True, **self._profile_payload(vehicle)})

class RuleConstraintViewSet(AuditedViewSetMixin, viewsets.ModelViewSet):
    """Campus schedule rules. Read by any signed-in role; changed only by the
    CDSO, and only with a fresh two-factor step-up.

    These rules decide who may enter campus and when, so editing one is a
    change to the access-control policy itself rather than to a record. The
    admin-only write check is not redundant with the step-up: guards carry no
    second factor by design, so a step-up alone would have waved a guard token
    straight through to the policy every gate reads.
    """

    queryset           = RuleConstraint.objects.all()
    serializer_class   = RuleConstraintSerializer
    audit_label        = 'Schedule Rule'

    def get_permissions(self):
        if self.request.method in permissions.SAFE_METHODS:
            return [permissions.IsAuthenticated()]
        return [IsAdminOrCdso(), HasRecentTwoFactor()]

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
        # Build the category capacity/occupancy map once for the whole response.
        # Without this each zone would fetch it itself, turning a two-query page
        # into two queries per zone.
        from .capacity import category_state
        ctx['category_state'] = category_state()
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

            # lens_index tags which view of a multi-lens camera the bay is in.
            # Coerced rather than trusted: it indexes a stacked frame, so a
            # negative or absurd value would quietly orphan the bay from every
            # view the editor can show.
            try:
                lens_index = max(0, int(s.get('lens_index') or 0))
            except (TypeError, ValueError):
                lens_index = 0

            space, _ = ParkingSpace.objects.update_or_create(
                zone=zone,
                space_number=s['space_number'],
                defaults={**bbox, 'points': points, 'lens_index': lens_index},
            )
            result.append(space)

        return Response(ParkingSpaceSerializer(result, many=True).data)

    @action(detail=True, methods=['post'], url_path='set-baseline')
    def set_baseline(self, request, pk=None):
        """Capture the current frame as this zone's empty-lot baseline.

        Taken from the live feed rather than uploaded, because the baseline is
        only meaningful from the exact camera position the bays were drawn
        against. Refuses when the camera is not running: a baseline captured
        from a dead feed is worse than none — it would silently score every bay
        against a blank frame.

        Whoever presses this is responsible for the lot actually being empty. A
        car sitting in a bay at capture time bakes that car into the bay's
        'empty' reference, and the bay then reads free while it is occupied.
        """
        import cv2 as _cv2
        from django.core.files.base import ContentFile

        zone = self.get_object()
        thread = parking_camera.get_thread(zone.id)
        if thread is None or not thread.running:
            return Response(
                {'error': 'Start this zone\'s camera first — the baseline is captured from the live feed.'},
                status=400)

        frame = thread.get_frame()
        if frame is None:
            return Response({'error': 'No frame received from the camera yet. Try again in a moment.'},
                            status=400)

        ok, buf = _cv2.imencode('.jpg', frame, [_cv2.IMWRITE_JPEG_QUALITY, 92])
        if not ok:
            return Response({'error': 'Could not encode the captured frame.'}, status=500)

        zone.baseline_image.save(f'zone_{zone.id}_baseline.jpg',
                                 ContentFile(buf.tobytes()), save=False)
        zone.baseline_captured_at = timezone.now()
        zone.save(update_fields=['baseline_image', 'baseline_captured_at'])

        return Response(self.get_serializer(zone).data)

    @action(detail=True, methods=['get'], url_path='signals')
    def signals(self, request, pk=None):
        """Raw per-bay scores from the classic scorer — the tuning readout.

        Thresholds this cheap are only tunable if the numbers behind them are
        visible; without this the alternative is adjusting constants blind.
        """
        zone = self.get_object()
        thread = parking_camera.get_thread(zone.id)
        if thread is None:
            return Response({})
        return Response(thread.get_signals())

    @action(detail=True, methods=['get'], url_path='tracked-vehicles')
    def tracked_vehicles(self, request, pk=None):
        """Vehicles this zone is following and how long each has been still.

        The companion to `signals` for the dwell thresholds: occupancy and
        double parking now wait for a vehicle to stop, and without this
        "why is that bay still free" has no answer but guesswork.
        """
        zone = self.get_object()
        thread = parking_camera.get_thread(zone.id)
        if thread is None:
            return Response([])
        return Response(thread.get_tracked_vehicles())

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
        # Records the intent as well as acting on it: detection is automatic
        # now (detection_supervisor), so this flag is what a restart reads back.
        if not zone.detection_enabled:
            zone.detection_enabled = True
            zone.save(update_fields=['detection_enabled'])
        parking_camera.start(zone.id, zone.camera.rtsp_url)
        return Response({'status': 'started'})

    @action(detail=True, methods=['post'], url_path='stop-camera')
    def stop_camera(self, request, pk=None):
        zone = self.get_object()
        # Must persist, or the supervisor would restart the detector on its next
        # pass and the button would look broken.
        if zone.detection_enabled:
            zone.detection_enabled = False
            zone.save(update_fields=['detection_enabled'])
        parking_camera.stop(zone.id)
        return Response({'status': 'stopped'})

    @action(detail=False, methods=['get'], url_path='camera-status')
    def camera_status(self, request):
        """Returns {zone_id: is_running} for all zones."""
        return Response(parking_camera.status_dict())

    @action(detail=False, methods=['get'], url_path='detections')
    def detections(self, request):
        """The boxes behind the occupancy verdict, per running zone.

        GET and readable by guards as well as the CDSO: "why is that bay still
        green" is a question asked standing in front of the lot, and the honest
        answer is whatever the detector last saw.
        """
        return Response(parking_camera.detections_dict())

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


# Which HTTP auth flavour a camera's web server actually accepts, keyed by
# origin (http://ip[:port]). ONVIF firmware challenges with Digest — the units
# here answer 401 to a Basic header forever, which is what made PTZ look like a
# missing service. Credentials changing does not invalidate this: the flavour
# stays right, the wrong password just 401s through to the other attempts.
_AUTH_MODE_CACHE: dict = {}


def _http_auths(username, password, base_url):
    """Auth attempts, in order, for a camera's HTTP/ONVIF endpoints.

    Returns (mode, auth) pairs. An open camera can reject a credential header
    it never asked for, so a bare unauthenticated attempt always stays in the
    list. The flavour that last worked for this host is tried first.
    """
    from requests.auth import HTTPDigestAuth
    if not username:
        return [('none', None)]
    order = [('digest', HTTPDigestAuth(username, password)),
             ('basic',  (username, password)),
             ('none',   None)]
    with _PTZ_LOCK:
        won = _AUTH_MODE_CACHE.get(base_url)
    if won:
        order.sort(key=lambda pair: pair[0] != won)
    return order


def _auth_mode_worked(base_url, mode):
    with _PTZ_LOCK:
        _AUTH_MODE_CACHE[base_url] = mode


def _origin(url):
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    return f'{parts.scheme}://{parts.netloc}'


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
    # Try SOAP 1.2 first, fall back to SOAP 1.1 (text/xml) for budget cameras.
    # The WS-Security header above is only half the story: firmware that wants
    # HTTP Digest never reads it, so every transport auth flavour is tried too.
    origin = _origin(endpoint)
    auths  = _http_auths(username, password, origin)
    for ct in (content_types or ['application/soap+xml; charset=utf-8',
                                 'text/xml; charset=utf-8']):
        for mode, auth in auths:
            try:
                r = _rq.post(endpoint, data=data,
                             headers={'Content-Type': ct},
                             timeout=5, auth=auth)
                if r.status_code in (401, 403):
                    continue
                if r.status_code < 500:
                    _auth_mode_worked(origin, mode)
                    return r, ct
            except Exception:
                break
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


def camera_http_credentials(cam):
    """(username, password) to use for this camera's HTTP/ONVIF calls.

    The **username** comes from the rtsp_url when it carries one. On the units
    here `device_id` is a hardware serial, not a login — the real ONVIF account
    is `admin`, and it only ever appears in the URL. Sending the serial gets a
    SOAP fault on every request, which the PTZ view then reports as
    "No PTZ service path responded successfully".

    The **password** prefers the stored field, because that is the one admins
    edit; the URL's password is the fallback for a camera added by hand. The
    device ID stays the last-resort username for units with no URL credentials.
    """
    from urllib.parse import unquote

    stored  = (getattr(cam, 'password', '') or '').strip()
    url     = (getattr(cam, 'rtsp_url', '') or '')
    url_user = url_pw = ''
    if '://' in url and '@' in url:
        rest  = url.split('://', 1)[1]
        creds = rest.rsplit('@', 1)[0]          # rsplit: passwords may contain '@'
        if ':' in creds:
            url_user, url_pw = creds.split(':', 1)
        else:
            url_user, url_pw = creds, ''
        url_user, url_pw = unquote(url_user), unquote(url_pw)

    if url_user:
        return url_user, (stored or url_pw)

    return (getattr(cam, 'device_id', '') or ''), stored


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
    auths = _http_auths(username, password, base_url)

    errors = []
    for idx, url in candidates:
        for mode, auth in auths:
            try:
                r = _rq.get(url, auth=auth, timeout=3)
                if r.status_code < 400:
                    _auth_mode_worked(base_url, mode)
                    return idx
                if r.status_code in (401, 403):
                    continue          # try the next credential form
                errors.append(f'{r.status_code}')
                break
            except Exception as e:
                errors.append(str(e))
                break
    raise Exception('CGI PTZ failed: ' + '; '.join(errors) or 'CGI PTZ failed')


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

        try:
            channel = int(request.data.get('channel') or 1)
        except (TypeError, ValueError):
            channel = 1

        result = rtsp_probe.detect(
            ip=request.data.get('ip', ''),
            device_id=request.data.get('device_id', ''),
            password=request.data.get('password', ''),
            channel=channel,
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

        # Resolved once per request: from the rtsp_url if it carries them,
        # otherwise the device ID with no password.
        cam_user, cam_pw = camera_http_credentials(cam)

        def _send(base, route):
            """Run `command` against `base`. Returns the route that worked."""
            if route.get('method') == 'cgi':
                idx = _try_cgi_ptz(base, cam_user, cam_pw, command,
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
                    base, cam_user, cam_pw, media_path, media_ct,
                )
            kw = {'ptz_path': route.get('ptz_path'), 'content_type': route.get('ptz_ct')}
            if command == 'stop':
                ptz_path, ptz_ct = _ptz_stop(base, cam_user, cam_pw, token, **kw)
            elif command == 'home':
                ptz_path, ptz_ct = _ptz_home(base, cam_user, cam_pw, token, **kw)
            elif command in vel_map:
                pan, tilt, zoom = vel_map[command]
                ptz_path, ptz_ct = _ptz_move(base, cam_user, cam_pw,
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
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from .models import FetcherStudentAssessment, VehicleRegistration
from .serializers import VehicleRegistrationSerializer
from .campus_days import (ALL_DAYS, MAX_CAMPUS_DAYS, SCHEDULE_DAY_LABELS,
                          SCHEDULE_GROUP_DAYS, clean_campus_days,
                          resolve_student_schedule, schedule_group)
from accounts.models import User
from .email_utils import (send_acceptance_email, send_rejection_email,
                          send_pending_email, send_receipt_received_email,
                          send_in_background)


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
        # select_related pulls the department in the same SELECT, prefetch_related
        # collects every fetcher's per-student assessments in one more, and the
        # prebuilt block-count map replaces one COUNT per row with one query for
        # the page — together they turn a 3N+1 query pattern into a flat 3.
        registrations = list(
            VehicleRegistration.objects
            .filter(status=status_filter)
            .select_related('department')
            .prefetch_related('fetcher_assessments')
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
    # so only plates already tied to an account are conflicts.
    #
    # Exact, not __iexact: Vehicle plates are always stored normalised (see
    # _upsert_vehicle_for_registration and Vehicle.resolve, which both look them
    # up with an exact match). __iexact wrapped the column in UPPER() and made
    # this a sequential scan of tbl_vehicle on every submission and every
    # keystroke of the availability check, ignoring uniq_vehicle_plate_number.
    if Vehicle.objects.filter(plate_number=plate_norm, user__isnull=False).exists():
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
    #
    # __iexact, not an exact match: BaseUserManager.normalize_email only
    # lower-cases the *domain*, so a live account may be stored as
    # `Juan@slc.edu.ph` while the registration holds the fully-lowercased
    # `juan@slc.edu.ph`. An exact match missed that pair and let the flow run on
    # into create_user(), where uniq_active_user_email turned it into a 500
    # instead of this readable 400. AcceptRegistrationView used to repeat this
    # very query a second time for the same reason; this one already covers it.
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

    # Match on the *normalised* columns, never __iexact. Every __iexact here
    # wrapped its column in UPPER(), so none of the registration indexes applied
    # and this ban check — which runs on every public submission and on every
    # keystroke of the availability endpoint — degraded into a full scan of
    # tbl_vehicle_registration. plate/conduction are stored upper-with-no-spaces
    # and email lower-cased by VehicleRegistration.save(), and the annotations
    # below match the vehreg_plate_norm / vehreg_email_norm expression indexes,
    # so the same question is now answered from an index.
    qs = VehicleRegistration.objects.annotate(
        _plate_norm=Upper(Replace('plate_number', Value(' '), Value(''))),
        _email_norm=Lower('email'),
    )

    conds = Q()
    if email_norm:
        conds |= Q(_email_norm=email_norm)
    if plate_norm:
        conds |= Q(_plate_norm=plate_norm)
    if conduction_norm:
        conds |= Q(conduction_number=conduction_norm)
    if student_id:
        conds |= Q(student_id=student_id)
    if employee_id:
        conds |= Q(employee_id=employee_id)
    if not conds:
        return None

    if qs.filter(conds, user__registration_banned=True).exists():
        return ("This applicant reached the maximum number of traffic violations and is no "
                "longer eligible to register a vehicle pass. Please contact the CDSO office.")
    return None


def _normalize_department(data):
    """Map the form's department label onto the model's department_type.

    The form sends a human-readable label ("Teaching", "Cleaning and Services");
    the column stores the choice value. Driven off DepartmentType rather than an
    if/elif chain, which silently fell through to None for any label it did not
    know — so a new department would have been accepted and stored blank.

    Mutates `data` in place and returns the resolved department_type (or '').
    Shared by the public form and the CDSO walk-in: the walk-in path did not map
    the label at all, so a walk-in employee's department never reached the row,
    and with it the fee exemption never applied.
    """
    dept_raw = data.pop('department', None)
    if isinstance(dept_raw, list):
        dept_raw = dept_raw[0] if dept_raw else None

    dept_label_to_value = {
        label: value for value, label in VehicleRegistration.DepartmentType.choices
    }
    data['department'] = None
    dept_value = dept_label_to_value.get(dept_raw, '')
    if dept_value:
        data['department_type'] = dept_value
    return dept_value


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
    # Upper-cased, then matched exactly — save() stores this stripped and
    # upper-cased, so case-folding buys nothing while __iexact wrapped the
    # column in UPPER() and stopped Postgres using
    # uniq_active_registration_drivers_license. That turned the pre-check into
    # a scan of every active registration on each submission; same anti-pattern
    # _id_conflict documents above.
    license_clean = (drivers_license or '').strip().upper()
    if not license_clean:
        return None
    active = [VehicleRegistration.Status.PENDING, VehicleRegistration.Status.ACCEPTED]
    if VehicleRegistration.objects.filter(
        status__in=active, drivers_license=license_clean
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


def _acceptance_email_failed_notice(registration):
    """The admin-bell replacement for the old `email_status: 'failed'` warning.

    The pass is already issued at this point; what the CDSO needs to know is
    that the owner never received the credentials to use it.
    """
    from accounts.notifications import notify
    plate = registration.plate_number or registration.conduction_number or ''

    def _notice():
        notify(
            'registration', 'acceptance_email_failed',
            f"Approval email failed — {plate}",
            f"{registration.full_name}'s vehicle pass was approved, but the email "
            f"carrying their portal credentials could not be delivered to "
            f"{registration.email}. Give them their login details directly.",
            severity='warning', plate_number=plate, link='/admin/vehicles',
        )
    return _notice


def _pending_email_failed_notice(registration):
    """Same idea for the acknowledgement mail a public submission triggers.

    Worth raising even though nothing is issued yet: that email carries the
    applicant's reference number *and* the link they upload their Official
    Receipt through. Without it they have no way back into the flow, and the
    application sits unpaid in the queue looking like an applicant who never
    bothered — so the CDSO has to know to send it to them by hand.
    """
    from accounts.notifications import notify
    plate = registration.plate_number or registration.conduction_number or ''

    def _notice():
        notify(
            'registration', 'pending_email_failed',
            f"Acknowledgement email failed — {plate}",
            f"{registration.full_name}'s application was submitted, but the "
            f"acknowledgement email could not be delivered to {registration.email}. "
            f"They have not received their receipt-upload link.",
            severity='warning', plate_number=plate, link='/admin/vehicles',
        )
    return _notice


class RegistrationPdfView(APIView):
    """Print the approved-registration confirmation for one registration.

    The CDSO reviews and approves applications on this page, so the printed
    copy is issued from here too — it used to live in User Management, which
    meant finding the owner's account to print a document about a registration
    the reviewer was already looking at.

    Deliberately the same builder the approval email uses, so a reprint is
    never a different document from the one the owner received. The one
    difference is `include_documents`: the filed copy carries the scans the
    applicant uploaded, which the owner's emailed copy has no reason to.
    """
    permission_classes = [IsAdminOrCdso]

    def get(self, request, pk):
        from registration_pdf import (registration_confirmation_pdf,
                                      registration_pdf_filename)

        registration = get_object_or_404(VehicleRegistration, pk=pk)
        # The document states the registration is approved, so an application
        # still under review has no printable confirmation — printing one would
        # put a pass in someone's hands that the CDSO has not granted.
        if registration.status != VehicleRegistration.Status.ACCEPTED:
            return Response(
                {'detail': 'Only an accepted registration can be printed.'},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )

        pdf = registration_confirmation_pdf(registration, include_documents=True)
        audit(request, AuditLog.Action.RECORD_CREATED,
              f"Registration PDF printed | REG-{registration.id:06d} "
              f"({registration.plate_number}) | For: {registration.full_name}")

        resp = HttpResponse(pdf, content_type='application/pdf')
        resp['Content-Disposition'] = (
            f'attachment; filename="{registration_pdf_filename(registration)}"'
        )
        return resp


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
            # A brand-new car is identified by its conduction sticker; leaving
            # it out meant the one identifier a plate-less registration actually
            # has went unchecked at approval time.
            conduction_number=registration.conduction_number,
            statuses=[VehicleRegistration.Status.ACCEPTED],
            exclude_pk=registration.pk,
        )
        if conflict:
            return Response({"error": conflict}, status=status.HTTP_400_BAD_REQUEST)

        # Re-checked here, not just at submission: approval can happen days or
        # weeks after the form was filled in, and the applicant may have hit the
        # violation ceiling in between. Without this the hard block was only
        # ever as fresh as the moment they submitted.
        ban = _registration_ban(
            registration.plate_number,
            registration.email,
            registration.student_id,
            registration.employee_id,
            conduction_number=registration.conduction_number,
        )
        if ban:
            return Response({"error": ban, "registration_banned": True},
                            status=status.HTTP_403_FORBIDDEN)

        # ── Payment gate ──
        # An Official Receipt number is the payment record, wherever it came
        # from: the applicant now files their own (number + receipt photo, via
        # the link in their pending email), but a reviewer keying one in at the
        # counter for somebody who brought the paper instead counts just the
        # same. The request value wins over the stored one so CDSO can correct a
        # typo it spots against the uploaded image.
        exempt    = registration.payment_status == VehicleRegistration.PaymentStatus.EXEMPT
        or_number = (request.data.get('or_number') or '').strip() or (registration.or_number or '').strip()

        if exempt:
            # Nothing was owed, so there is no receipt to demand. Requiring one
            # here used to force CDSO to invent an OR number for fee-exempt
            # staff before the accept button would enable.
            or_number = ''
        elif or_number and (not or_number.isdigit() or len(or_number) > 7):
            return Response({"error": "Official Receipt (OR) number must be at most 7 digits."}, status=status.HTTP_400_BAD_REQUEST)

        # Approving with no receipt at all is allowed, but never silently: the
        # reason is stored on the registration so a pass issued against an
        # unsettled fee always carries its own justification.
        unpaid        = not exempt and not or_number
        unpaid_reason = (request.data.get('unpaid_accept_reason') or '').strip()
        if unpaid and not unpaid_reason:
            return Response(
                {"error": "unpaid_acceptance_requires_reason",
                 "detail": f"{registration.full_name} has not submitted an Official Receipt. "
                           f"Enter the OR number, or give a reason for approving this "
                           f"application while the fee is still unpaid."},
                status=status.HTTP_400_BAD_REQUEST,
            )

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
            _cleaned_check, _ = clean_campus_days(campus_days_override)
            if len(_cleaned_check) > MAX_CAMPUS_DAYS and not special_case_reason:
                return Response(
                    {"error": f"A reason is required when granting more than "
                              f"{MAX_CAMPUS_DAYS} campus days."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # One transaction: without it a failure part-way through (most likely the
        # Vehicle upsert hitting uniq_vehicle_plate_number) left the just-created
        # User committed but orphaned — no registration, no vehicle. That account
        # then held the applicant's email under uniq_active_user_email, so the
        # registration stayed pending and every retry was rejected with "already
        # tied to an existing account". The acceptance email is deliberately sent
        # after this block commits, never inside it.
        with transaction.atomic():
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
            if unpaid:
                # payment_status stays UNPAID on purpose. The pass is issued, but
                # the fee is still owed — flipping it to paid here would erase the
                # one fact Accounting needs to chase, and the reason would then be
                # the only trace that money never changed hands.
                registration.unpaid_accept_reason = unpaid_reason
            elif not exempt and registration.payment_status != VehicleRegistration.PaymentStatus.PAID:
                # An OR number reached us without going through the applicant's
                # upload — a walk-in who brought the paper to the counter. Same
                # proof, so it is recorded the same way; only the receipt image
                # is missing.
                registration.payment_status = VehicleRegistration.PaymentStatus.PAID
                registration.amount_paid    = registration.pass_fee()
                registration.paid_at        = timezone.now()
            registration.user = user        # direct FK to account
            registration.vehicle = vehicle_obj  # 1:1 link registration → vehicle

            # Apply campus_days / schedule overrides
            if campus_days_override is not None and isinstance(campus_days_override, list):
                cleaned, _ = clean_campus_days(campus_days_override)

                # More than the allowance is a special case (validated above)
                if len(cleaned) > MAX_CAMPUS_DAYS:
                    registration.is_special_case     = True
                    registration.special_case_reason = special_case_reason

                registration.campus_days = cleaned
                # Re-derive the schedule group from the new days, by the same
                # rule the public form uses (see vehicles/campus_days.py).
                registration.schedule = schedule_group(cleaned)
            elif schedule_override:
                registration.schedule = schedule_override
            registration.status = VehicleRegistration.Status.ACCEPTED
            registration.reviewed_at = timezone.now()
            registration.save()

            # Sync final campus_days / schedule onto the user account so entry_logic
            # can check actual days rather than a fixed MWF/TTHF group.
            user.campus_days = registration.campus_days or []
            user.schedule    = registration.schedule or user.schedule
            user.save(update_fields=['campus_days', 'schedule'])

            # No refresh_from_db() here: User.save() assigns self.user_code
            # before writing it, so the in-memory instance already carries it.
            # Re-reading the row cost a round trip to fetch what we just set.
            # A bare "OR: " told a later reader nothing about why a pass was
            # issued without a receipt. This is the permanent record of that
            # decision, so it says which of the two reasons applied.
            if exempt:
                or_note = 'OR: n/a (fee exempt)'
            elif unpaid:
                or_note = f'OR: none — approved unpaid: {unpaid_reason}'
            else:
                or_note = f'OR: {or_number}'
            audit(request, AuditLog.Action.RECORD_UPDATED,
                  f"Registration accepted | Plate: {registration.plate_number} | "
                  f"Applicant: {registration.full_name} ({registration.registrant_type}) | "
                  f"{or_note} | By: {request.user.full_name}",
                  target_user=user)
        system_id = registration.system_student_id if registration.registrant_type == 'student' else registration.system_employee_id

        # Acceptance mail with the QR code and credentials, handed to a background
        # thread. The transaction above has already committed, so this was never
        # able to affect the outcome — it only made the reviewer wait on a mail
        # server. A failed send raises an admin notification instead of the
        # response field the CDSO page used to warn from.
        send_in_background(
            send_acceptance_email, registration, temp_password, user.user_code,
            on_failure=_acceptance_email_failed_notice(registration),
        )

        return Response({
            "message": "Registration accepted and user created.",
            "email_status": 'queued',
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
                # Both halves of the schedule, not just the days. The account
                # modal formats `schedule` into "Mon · Wed · Fri" and only falls
                # back to campus_days for a MIXED row — sending the days alone
                # left every newly approved student showing "—" for Schedule,
                # even though the same value renders fine in the table behind
                # the modal (that comes off the serializer, which is __all__).
                "schedule": registration.schedule,
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
            email_status = 'sent'
        except Exception:
            logger.exception(
                "Failed to send rejection email to %s (registration %s) — the "
                "registration is still rejected; the applicant was not told.",
                registration.email, registration.pk,
            )
            email_status = 'failed'

        return Response({"message": "Registration rejected.", "email_status": email_status})


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

        data = dict(request.data)

        # Resolved before the receipt check, not after: Cleaning and Services
        # staff pay nothing, so there is no Official Receipt to demand from them.
        # Asking anyway meant CDSO had to invent a number to register a walk-in
        # from that department.
        department_type = _normalize_department(data)
        exempt = VehicleRegistration.is_fee_exempt(registrant_type, department_type)

        or_number = request.data.get('or_number', '').strip()
        if exempt:
            or_number = ''
        else:
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

        # Walk-ins go through the same campus-day rules as the online form.
        # This path validated none of them: day names were stored unchecked, and
        # because `schedule` was only ever read back off the row, a caller who
        # supplied campus_days without a schedule got the blanket 'MWF' default
        # no matter which days those actually were.
        if registrant_type in ('employee', 'fetcher'):
            data['campus_days'] = []
            data['schedule'] = 'ANY'
        else:
            campus_days, rejected = clean_campus_days(data.get('campus_days', []))
            if rejected:
                return Response(
                    {"error": f"Not a campus day: {', '.join(str(d) for d in rejected)}. "
                              f"Choose from {', '.join(ALL_DAYS)}."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not campus_days:
                return Response({"error": "Students must have at least one campus day."},
                                status=status.HTTP_400_BAD_REQUEST)
            data['campus_days'] = campus_days
            data['schedule'] = schedule_group(campus_days)

        serializer = VehicleRegistrationSerializer(data=data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # Same all-or-nothing rule as AcceptRegistrationView: the registration,
        # the account and the vehicle are created together or not at all, so a
        # failure half-way cannot strand an account holding the walk-in's email.
        with transaction.atomic():
            registration = serializer.save(
                registrant_type=registrant_type,
                source=VehicleRegistration.Source.DIRECT,
                status=VehicleRegistration.Status.ACCEPTED,
                or_number=or_number,
                reviewed_at=timezone.now(),
                # A walk-in is registered at the counter with the receipt in
                # hand — there is no unpaid window to model for this path. An
                # exempt walk-in never had a receipt to bring, so it is recorded
                # as exempt rather than as a payment that never happened.
                payment_status=(VehicleRegistration.PaymentStatus.EXEMPT if exempt
                                else VehicleRegistration.PaymentStatus.PAID),
                paid_at=(None if exempt else timezone.now()),
                # Computed from the resolved department rather than read back off
                # the saved row, which would cost a second SystemSettings query.
                amount_paid=VehicleRegistration.fee_for(registrant_type, department_type),
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
            # user.user_code is already populated in memory — see the note in
            # AcceptRegistrationView.
        system_id = registration.system_student_id if registrant_type == 'student' else registration.system_employee_id

        # Same reasoning as AcceptRegistrationView: the account exists either way,
        # so the send belongs off the request path with a notification on failure.
        send_in_background(
            send_acceptance_email, registration, temp_password, user.user_code,
            on_failure=_acceptance_email_failed_notice(registration),
        )
        email_status = 'queued'

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


# ALL_DAYS now comes from .campus_days — the same list the validators use, so
# the slot grid and the accepted day names cannot drift apart.


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

        # The public form books a whole rotation, so what it needs is the
        # rotation's headroom: its tightest day, since one full day closes the
        # schedule. The per-day grid stays for the CDSO day picker, which still
        # assigns days one at a time.
        #
        # Friday is on both rotations, so its count is MWF students + TTHF
        # students and it is normally the tightest day of the two — meaning
        # `used` here is the rotation's busiest day, not its headcount.
        result['groups'] = {
            code: {
                "days": days,
                "label": SCHEDULE_DAY_LABELS[code],
                "used": max(result[d]['used'] for d in days),
                "limit": limit,
                "available": min(result[d]['available'] for d in days),
            }
            for code, days in SCHEDULE_GROUP_DAYS.items()
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
        department_type = _normalize_department(data)

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
            # campus_days lands in a JSONField, so whatever arrives is what gets
            # stored — the day names were never checked here, only in the React
            # form. A direct POST could file a registration for 'Funday' (a day
            # entry_logic can never match, leaving a pass valid on no day) or
            # for all six days, silently taking more than the 3-day allowance
            # that CDSO otherwise has to approve as a special case.
            #
            # An applicant now picks a rotation rather than loose days, so the
            # resolution lives in campus_days.resolve_student_schedule and both
            # `schedule` (what the form sends) and `campus_days` (older clients,
            # direct callers) arrive at the same whole week.
            campus_days, schedule_code, day_error = resolve_student_schedule(
                data.get('schedule'), data.get('campus_days', []), data.get('student_level'))
            if day_error:
                return Response({"error": day_error}, status=status.HTTP_400_BAD_REQUEST)

            active = [VehicleRegistration.Status.PENDING, VehicleRegistration.Status.ACCEPTED]
            base = VehicleRegistration.objects.filter(status__in=active, registrant_type='student')
            full_days = []
            for day in campus_days:
                used = base.filter(campus_days__contains=[day]).count()
                if used >= SCHEDULE_SLOT_LIMIT:
                    full_days.append(day)
            if full_days:
                # A rotation is taken as a whole, so one full day closes the
                # whole schedule — saying "Friday is full, pick another day"
                # would offer a choice the form no longer has.
                label = SCHEDULE_DAY_LABELS.get(schedule_code, schedule_code)
                return Response(
                    {"error": f"The {label} schedule is full "
                              f"({', '.join(full_days)} at capacity). "
                              f"Please choose the other schedule."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            data['campus_days'] = campus_days
            data['schedule'] = schedule_code

        serializer = VehicleRegistrationSerializer(data=data)
        if serializer.is_valid():
            # Cleaning and Services staff owe nothing, so they never pass through
            # the Accounting Office and have no receipt to upload. Resolved from
            # the request rather than read back off the saved row: it costs no
            # query, and it rides along in the INSERT instead of a second UPDATE.
            exempt = VehicleRegistration.is_fee_exempt(registrant_type, department_type)
            registration = serializer.save(
                registrant_type=registrant_type,
                source=VehicleRegistration.Source.PUBLIC,
                payment_status=(VehicleRegistration.PaymentStatus.EXEMPT if exempt
                                else VehicleRegistration.PaymentStatus.UNPAID),
                amount_paid=(Decimal('0.00') if exempt else None),
            )
            # Acknowledgement mail, handed to a background thread like the
            # acceptance and receipt mails. The registration is already
            # committed, so the send never affected the outcome — it only made
            # the applicant sit on the submit button while Brevo (or Gmail's
            # SMTP, on the campus half) completed a round trip, which is
            # seconds on top of a request that otherwise takes tens of
            # milliseconds. The applicant still has a second upload to wait on
            # after this one, so it was the worst place in the flow to block.
            #
            # The failure is still not swallowed: a bare `except: pass` here
            # once meant an expired SMTP credential looked exactly like a
            # healthy system. It is logged, and it raises an admin notification
            # — which matters more here than anywhere else, because this mail
            # carries the link the applicant needs to upload their receipt.
            send_in_background(
                send_pending_email, registration,
                on_failure=_pending_email_failed_notice(registration),
            )
            return Response(
                {"message": "Registration submitted successfully. Please wait for CDSO review.",
                 "id": registration.id,
                 "email_status": 'queued'},
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class UploadRegistrationDocumentsView(APIView):
    """Public follow-up step to PublicOpenRegistrationView — attaches the applicant's
    supporting documents (driver's license photo, assessment form) to a just-submitted
    registration. Kept as a separate multipart request so the main JSON registration
    payload (with its nested campus_days / fetcher_students structures) doesn't have
    to be reworked into form-data.

    A fetcher is not enrolled themselves, so the enrolment proofs for the students
    they collect arrive here too — one file per listed student, as
    `fetcher_assessment_<index>`, the index being the position in fetcher_students.

    Every file is optional individually, but at least one has to be present —
    a request carrying none is a client bug, not a no-op worth recording."""
    permission_classes = [permissions.AllowAny]
    parser_classes = [MultiPartParser, FormParser]

    # Mirrors the model's FileExtensionValidator on assessment_form. Checked here
    # too so the applicant gets a plain 400 instead of a 500 from full_clean.
    ASSESSMENT_EXTENSIONS = ('jpg', 'jpeg', 'png', 'webp', 'heic', 'heif', 'pdf')
    FETCHER_ASSESSMENT_PREFIX = 'fetcher_assessment_'

    def _fetcher_assessments(self, request):
        """The per-student files, as ({student_index: uploaded_file}, error).

        A field name that is not `prefix + digits` is reported rather than
        silently skipped: a quietly dropped attachment reads to the applicant
        as one that was accepted.
        """
        files = {}
        for key in request.FILES:
            if not key.startswith(self.FETCHER_ASSESSMENT_PREFIX):
                continue
            suffix = key[len(self.FETCHER_ASSESSMENT_PREFIX):]
            if not suffix.isdigit():
                return None, "Malformed attachment field '%s'." % key
            files[int(suffix)] = request.FILES[key]
        return files, None

    def _bad_extension(self, upload):
        return upload.name.lower().rsplit('.', 1)[-1] not in self.ASSESSMENT_EXTENSIONS

    def post(self, request):
        registration_id = request.data.get('registration_id')
        email = (request.data.get('email') or '').strip()
        image = request.FILES.get('image')
        assessment = request.FILES.get('assessment_form')

        fetcher_files, field_error = self._fetcher_assessments(request)
        if field_error:
            return Response({"error": field_error}, status=status.HTTP_400_BAD_REQUEST)

        if not registration_id or not email:
            return Response({"error": "registration_id and email are required."}, status=status.HTTP_400_BAD_REQUEST)
        if not image and not assessment and not fetcher_files:
            return Response({"error": "No file uploaded."}, status=status.HTTP_400_BAD_REQUEST)
        if assessment and self._bad_extension(assessment):
            return Response(
                {"error": "The assessment form must be a JPG, PNG, WEBP, HEIC or PDF file."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        for upload in fetcher_files.values():
            if self._bad_extension(upload):
                return Response(
                    {"error": "Each student's assessment form must be a JPG, PNG, WEBP, HEIC or PDF file."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        try:
            registration = VehicleRegistration.objects.get(
                id=registration_id,
                email__iexact=email,
                status=VehicleRegistration.Status.PENDING,
            )
        except VehicleRegistration.DoesNotExist:
            return Response({"error": "Registration not found."}, status=status.HTTP_404_NOT_FOUND)

        # An index with no student behind it would file a document the review
        # screen has no row to show it against.
        listed = len(registration.fetcher_students or [])
        if any(not 0 <= i < listed for i in fetcher_files):
            return Response(
                {"error": "An assessment form was sent for a student who is not on this registration."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        updated = []
        if image:
            registration.drivers_license_image = image
            updated.append('drivers_license_image')
        if assessment:
            registration.assessment_form = assessment
            updated.append('assessment_form')
        if updated:
            registration.save(update_fields=updated)

        for index, upload in sorted(fetcher_files.items()):
            # update_or_create, not create: a retried upload replaces the file
            # on record instead of tripping the uniqueness constraint that keeps
            # the reviewer looking at exactly one document per student.
            FetcherStudentAssessment.objects.update_or_create(
                registration=registration, student_index=index,
                defaults={'assessment_form': upload},
            )
            updated.append('%s%d' % (self.FETCHER_ASSESSMENT_PREFIX, index))

        return Response({"message": "Documents uploaded.", "uploaded": updated}, status=status.HTTP_200_OK)


# The old name, kept so the previously built frontend bundle's
# /register/license-image/ calls keep resolving to the same handler.
UploadLicenseImageView = UploadRegistrationDocumentsView


# ──────────────────────────────────────────────
# Public receipt upload (applicant-driven proof of payment)
# ──────────────────────────────────────────────

def _payment_registration(token):
    """Resolve a receipt-upload token to a still-reviewable registration.

    Only PENDING rows are reachable: once CDSO has accepted or rejected the
    application the receipt on file is part of the decision, and letting the
    link keep overwriting it would rewrite the evidence after the fact.
    """
    if not token:
        return None
    try:
        return VehicleRegistration.objects.get(
            payment_token=token,
            status=VehicleRegistration.Status.PENDING,
        )
    except (VehicleRegistration.DoesNotExist, ValueError, ValidationError):
        # ValueError/ValidationError: a malformed token is a bad link, not a 500.
        return None


class RegistrationPaymentView(APIView):
    """The applicant's own proof-of-payment step.

    They pay at the Accounting Office, then follow the link in their pending
    email to upload the Official Receipt themselves — CDSO verifies the image
    against the number at review time instead of re-keying it at a counter.

    Authorised by the unguessable payment_token alone. The (id, email) pair the
    document upload uses is not a secret any more: school addresses are now
    <8-digit ID>@slc-sflu.edu.ph and registration ids are sequential.
    """
    permission_classes = [permissions.AllowAny]
    parser_classes = [MultiPartParser, FormParser]

    RECEIPT_EXTENSIONS = ('jpg', 'jpeg', 'png', 'webp', 'heic', 'heif', 'pdf')
    RECEIPT_MAX_BYTES  = 5 * 1024 * 1024

    def get(self, request):
        """Everything the upload page needs to render, and nothing more.

        Deliberately not the full registration: this endpoint is reachable by
        anyone holding the link, so it returns what the applicant already knows
        about their own application, not the record CDSO sees.
        """
        registration = _payment_registration(request.query_params.get('token'))
        if registration is None:
            return Response(
                {"error": "This payment link is no longer valid. It may have expired, "
                          "or your application may already have been reviewed."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response({
            "full_name":       registration.full_name,
            "plate_number":    registration.plate_number or registration.conduction_number,
            "registrant_type": registration.registrant_type,
            "amount_due":      str(registration.pass_fee()),
            "payment_status":  registration.payment_status,
            "or_number":       registration.or_number,
            "has_receipt":     bool(registration.or_receipt_image),
        })

    def post(self, request):
        registration = _payment_registration(request.data.get('token'))
        if registration is None:
            return Response(
                {"error": "This payment link is no longer valid. It may have expired, "
                          "or your application may already have been reviewed."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Exempt applicants have nothing to pay and so nothing to prove. Told
        # plainly rather than letting them hunt for a receipt that never existed.
        if registration.payment_status == VehicleRegistration.PaymentStatus.EXEMPT:
            return Response(
                {"error": "No payment is required for this application — your department is "
                          "exempt from the Vehicle Pass fee. Just proceed to the CDSO Office."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        or_number = (request.data.get('or_number') or '').strip()
        receipt   = request.FILES.get('receipt')

        # Same shape the accept flow has always enforced, applied at the point
        # the number is actually typed instead of days later at the counter.
        if not or_number:
            return Response({"error": "Official Receipt (OR) number is required."},
                            status=status.HTTP_400_BAD_REQUEST)
        if not or_number.isdigit() or len(or_number) > 7:
            return Response({"error": "Official Receipt (OR) number must be at most 7 digits."},
                            status=status.HTTP_400_BAD_REQUEST)
        if not receipt:
            return Response({"error": "A photo or scan of the Official Receipt is required."},
                            status=status.HTTP_400_BAD_REQUEST)
        if receipt.name.lower().rsplit('.', 1)[-1] not in self.RECEIPT_EXTENSIONS:
            return Response({"error": "The receipt must be a JPG, PNG, WEBP, HEIC or PDF file."},
                            status=status.HTTP_400_BAD_REQUEST)
        if receipt.size > self.RECEIPT_MAX_BYTES:
            return Response({"error": "Please keep the receipt under 5MB."},
                            status=status.HTTP_400_BAD_REQUEST)

        registration.or_number        = or_number
        registration.or_receipt_image = receipt
        # Snapshot of what was owed at the moment of payment — see the field.
        registration.amount_paid      = registration.pass_fee()
        registration.paid_at          = timezone.now()
        registration.payment_status   = VehicleRegistration.PaymentStatus.PAID
        registration.save(update_fields=[
            'or_number', 'or_receipt_image', 'amount_paid', 'paid_at', 'payment_status',
        ])

        # The receipt is what completes the registration form, so this is the
        # mail that carries it: the PDF the CDSO files, with the uploaded
        # documents printed into it. Backgrounded like the approval mail — the
        # applicant is waiting on an upload, not on a mail server, and a dead
        # SMTP host must not fail a payment that is already recorded.
        send_in_background(send_receipt_received_email, registration)

        return Response(
            {"message": "Receipt received. Your application is now queued for CDSO review.",
             "payment_status": registration.payment_status},
            status=status.HTTP_200_OK,
        )


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
        """Live availability for the owner portal.

        `summary` is the number that matters — capacity and occupancy per
        category, counted from the gate ledger, so it reflects vehicles actually
        on campus rather than how many bays a camera happens to have resolved.

        `zones` and `spaces` are the bay map: which specific slots look free, so
        an owner can see where to head. Those stay camera-derived, and a zone
        with no camera simply reports its bays as free.

        Three queries flat, whatever the number of zones, bays or vehicles: the
        spaces page, the declared-capacity aggregate, and the ledger count. The
        per-zone name lookup used to run inside the aggregation loop, one SELECT
        per zone; it now reads the `select_related` row already in hand.
        """
        from .capacity import category_state

        category = request.query_params.get('category', '')
        qs = ParkingSpace.objects.select_related('zone').all()
        if category in ['motorcycle', 'car']:
            qs = qs.filter(zone__vehicle_category=category)

        # Materialise once, then serialize from the same rows. Aggregating over
        # the model objects (whose `zone` is already joined) is what removes the
        # per-zone query.
        space_rows = list(qs)
        spaces = ParkingSpaceSerializer(space_rows, many=True).data

        state = category_state()
        summary = {}
        for cat in ('car', 'motorcycle'):
            if category in ('car', 'motorcycle') and cat != category:
                continue
            cat_state = state.get(cat, {})
            summary[cat] = {
                'total':     cat_state.get('capacity', 0),
                'occupied':  cat_state.get('occupied', 0),
                'available': cat_state.get('available', 0),
                'is_full':   cat_state.get('is_full', False),
                'source':    'gate_ledger',
            }

        zone_agg = {}  # zone_id -> bay tallies for that zone
        for space in space_rows:
            zone = space.zone
            if zone is None:
                continue
            entry = zone_agg.get(zone.id)
            if entry is None:
                entry = zone_agg[zone.id] = {
                    'zone_id':   zone.id,
                    'zone_name': zone.name,
                    'category':  zone.vehicle_category,
                    'total':     0,
                    'occupied':  0,
                }
            entry['total'] += 1
            if space.is_occupied:
                entry['occupied'] += 1

        zones = []
        for z in zone_agg.values():
            fill_pct = round(z['occupied'] / z['total'] * 100) if z['total'] > 0 else 0
            zones.append({**z, 'available': z['total'] - z['occupied'],
                          'fill_pct': fill_pct, 'source': 'camera_bays'})

        return Response({
            "spaces":  spaces,
            "summary": summary,
            "zones":   zones,
            # Missed exit scans today — surfaced so a gate that stopped scanning
            # exits is visible rather than quietly inflating the count.
            "stale_excluded": state.get('stale_excluded', 0),
        })


class SystemSettingsView(APIView):
    """System-wide configuration. Readable by any signed-in role — screens all
    over the app need the retention, fee and event-mode values — but writable
    only by the CDSO with a fresh two-factor step-up.

    A write here can silently turn off account expiry, shorten the retention
    window that deletes archived accounts, or open the campus, so it is exactly
    the kind of quiet change a stolen session would be used for.
    """

    def get_permissions(self):
        if self.request.method == 'GET':
            return [permissions.IsAuthenticated()]
        return [IsAdminOrCdso(), HasRecentTwoFactor()]

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
            "parked_after_seconds":      obj.parked_after_seconds,
            "double_park_after_seconds": obj.double_park_after_seconds,
            "auto_backup_frequency": obj.auto_backup_frequency,
            "auto_backup_keep":      obj.auto_backup_keep,
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
        parked_after_seconds      = request.data.get("parked_after_seconds",      obj.parked_after_seconds)
        double_park_after_seconds = request.data.get("double_park_after_seconds", obj.double_park_after_seconds)
        auto_backup_frequency     = request.data.get("auto_backup_frequency", obj.auto_backup_frequency)
        auto_backup_keep          = request.data.get("auto_backup_keep",      obj.auto_backup_keep)

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

        # Expiration is not optional — the period is the only control. Whatever
        # the client sends for the flag is ignored, so no request can turn owner
        # accounts into accounts that live forever.
        account_expiry_enabled = True
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
        if not errors and account_expiry_months == 0 and account_expiry_days == 0:
            errors["account_expiry_months"] = (
                "Account expiration cannot be switched off. Set at least 1 month or 1 day."
            )

        try:
            parked_after_seconds = int(parked_after_seconds)
            if not (1 <= parked_after_seconds <= 120):
                errors["parked_after_seconds"] = "Must be between 1 and 120 seconds."
        except (TypeError, ValueError):
            errors["parked_after_seconds"] = "Must be an integer."
        try:
            double_park_after_seconds = int(double_park_after_seconds)
            if not (1 <= double_park_after_seconds <= 300):
                errors["double_park_after_seconds"] = "Must be between 1 and 300 seconds."
        except (TypeError, ValueError):
            errors["double_park_after_seconds"] = "Must be an integer."
        # A car cannot be badly parked before it counts as parked at all. Without
        # this the camera could issue a double-parking fine against a vehicle its
        # own occupancy logic still considers to be manoeuvring.
        if ("parked_after_seconds" not in errors
                and "double_park_after_seconds" not in errors
                and double_park_after_seconds < parked_after_seconds):
            errors["double_park_after_seconds"] = (
                "Must be at least as long as the parked threshold "
                f"({parked_after_seconds}s)."
            )

        # "off" is a real frequency, not a missing value — it is how automatic
        # backups are switched off, so it is accepted like any other choice.
        valid_freqs = {'off', 'hourly', 'daily', 'weekly', 'monthly'}
        auto_backup_frequency = str(auto_backup_frequency or 'off').lower()
        if auto_backup_frequency not in valid_freqs:
            errors["auto_backup_frequency"] = "Must be one of: off, hourly, daily, weekly, monthly."
        try:
            auto_backup_keep = int(auto_backup_keep)
            if not (1 <= auto_backup_keep <= 90):
                errors["auto_backup_keep"] = "Must be between 1 and 90 backups."
        except (TypeError, ValueError):
            errors["auto_backup_keep"] = "Must be an integer."

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

        obj.account_expiry_enabled = account_expiry_enabled
        obj.account_expiry_months  = account_expiry_months
        obj.account_expiry_days    = account_expiry_days

        obj.parked_after_seconds      = parked_after_seconds
        obj.double_park_after_seconds = double_park_after_seconds

        obj.auto_backup_frequency = auto_backup_frequency
        obj.auto_backup_keep      = auto_backup_keep
        obj.save()

        # Running zones share one cached copy of the thresholds; dropping it
        # makes the change land on the next frame in this process rather than up
        # to a TTL later. No restart, and no reaching into the threads.
        parking_camera.invalidate_dwell_settings()

        # Apply a lowered keep-count now rather than at the next scheduled run.
        # An admin who reduces it is usually looking at a disk that is filling
        # up, and "it will tidy itself tomorrow" is not the answer they came for.
        if before["auto_backup_keep"] != auto_backup_keep:
            from accounts.backup_utils import prune_backups
            prune_backups(auto_backup_keep)

        # Give an expiry date to any owner still missing one, using the duration
        # the admin just chose and counting from their join date. Owners that
        # already have a date keep it — frozen-at-creation semantics, so changing
        # the period never moves the goalposts on an existing account.
        #
        # Runs on every save, not just the first: an owner with no expires_at is
        # an account that would live forever, which is the state expiration is
        # meant to make impossible.
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
            # batch_size matters here: Postgres' default is one CASE statement
            # covering every row, which stops being a query at a few thousand
            # owners. This is the one place a settings save touches many rows.
            _User.objects.bulk_update(owners, ['expires_at'], batch_size=500)

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
        """Active notices, limited to the ones broadcast since the reader joined.

        A notice is a broadcast, not a bulletin board: it went out by email to
        the owners who existed when it was sent. Someone who registers later was
        never a recipient, so replaying the backlog in their portal would show
        them announcements about weeks they were not on campus for. Only what is
        posted from their account's creation onwards is theirs to see.

        The CDSO (admin) is exempt — System Settings is where notices are
        managed and removed, so it has to list every active one regardless of
        which admin account is looking.
        """
        notices = ParkingNotice.objects.filter(is_active=True)
        if request.user.role != 'admin':
            notices = notices.filter(created_at__gte=request.user.date_joined)
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
                    <p style="font-size:12px;color:#7C80A3;margin:0;">Saint Louis College Smart Parking and Vehicle Verification System</p>
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


def _clean_period_payload(data, *, partial=False, current=None):
    """Validate a registration-period payload for create (all fields) or edit.

    `partial` keeps any field the caller left out at its `current` value, so a
    PATCH that only moves the end date does not have to resend the label.
    Returns (cleaned, errors) — cleaned is only complete when errors is empty.
    """
    from datetime import datetime as _dt

    def _as_date(raw):
        return _dt.strptime(str(raw), '%Y-%m-%d').date()

    errors = {}
    cleaned = {}

    if 'label' in data or not partial:
        label = (data.get('label') or '').strip()
        if not label:
            errors['label'] = 'Label is required.'
        cleaned['label'] = label
    else:
        cleaned['label'] = current.label

    for field in ('start_date', 'end_date'):
        if field in data or not partial:
            try:
                cleaned[field] = _as_date(data.get(field))
            except (ValueError, TypeError):
                errors[field] = 'Required. Use YYYY-MM-DD.'
        else:
            cleaned[field] = getattr(current, field)

    if errors:
        return None, errors
    if cleaned['end_date'] < cleaned['start_date']:
        return None, {'end_date': 'End date must be on or after start date.'}
    return cleaned, {}


class RegistrationPeriodListCreateView(APIView):
    def get_permissions(self):
        if self.request.method == 'GET':
            return [permissions.IsAuthenticated()]
        return [IsAdminOrCdso()]

    def get(self, request):
        return Response([_serialize_period(p) for p in RegistrationPeriod.objects.all()])

    def post(self, request):
        cleaned, errors = _clean_period_payload(request.data)
        if errors:
            return Response(errors, status=400)
        label, start, end = cleaned['label'], cleaned['start_date'], cleaned['end_date']

        RegistrationPeriod.objects.filter(is_active=True).update(is_active=False)
        period = RegistrationPeriod.objects.create(label=label, start_date=start, end_date=end, is_active=True)
        audit(request, AuditLog.Action.RECORD_CREATED,
              f"Registration period added | {period.label} ({period.start_date} to {period.end_date}) | By: {request.user.full_name}")
        return Response(_serialize_period(period), status=201)


class RegistrationPeriodDetailView(APIView):
    """Edit a registration period in place — including the active one.

    A window that is already running is the one most likely to need a change:
    the deadline gets extended, or the label was picked wrong. Editing it beats
    archiving and re-creating, which would leave a duplicate row behind.
    """
    permission_classes = [IsAdminOrCdso]

    def patch(self, request, pk):
        period = get_object_or_404(RegistrationPeriod, pk=pk)
        before = f"{period.label} ({period.start_date} to {period.end_date})"
        cleaned, errors = _clean_period_payload(request.data, partial=True, current=period)
        if errors:
            return Response(errors, status=400)

        period.label      = cleaned['label']
        period.start_date = cleaned['start_date']
        period.end_date   = cleaned['end_date']
        period.save(update_fields=['label', 'start_date', 'end_date'])
        audit(request, AuditLog.Action.RECORD_UPDATED,
              f"Registration period edited | {before} -> {period.label} "
              f"({period.start_date} to {period.end_date}) | By: {request.user.full_name}")
        return Response(_serialize_period(period))


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


OTHER_KEY = 'other'


def _registration_counts(qs):
    """Cross-tab of registrant type x status and type x payment, with totals.

    Payment is a second, independent axis rather than more `Status` values (see
    PaymentStatus on the model), so it is counted into its own grid over the
    same rows: both grids total to the same number.

    One GROUP BY for the whole grid — the page shows every status at once but
    the list itself only ever loads one, so the numbers cannot be counted off
    the rows on screen.

    Every row lands in exactly one cell, so `total`, `by_type` and `by_status`
    always reconcile with each other and with the table. `choices` is not a
    database constraint, so a legacy or hand-edited row can carry a value that
    is no longer in either enum; those fall into an explicit "Other" bucket that
    is reported only when it has rows. Dropping them instead would make the
    report's total disagree with the rows printed above it, which reads as a
    broken report rather than as odd data.
    """
    type_labels    = dict(VehicleRegistration.RegistrantType.choices)
    status_labels  = dict(VehicleRegistration.Status.choices)
    payment_labels = dict(VehicleRegistration.PaymentStatus.choices)
    types     = list(VehicleRegistration.RegistrantType.values)
    statuses  = list(VehicleRegistration.Status.values)
    payments  = list(VehicleRegistration.PaymentStatus.values)

    # Every cell exists up front, including the Other row and column, so the
    # accumulate loop never has to branch on a missing key.
    known_types = set(types)
    grid = {t: {st: 0 for st in statuses + [OTHER_KEY]} for t in types + [OTHER_KEY]}
    pay_grid = {t: {pm: 0 for pm in payments + [OTHER_KEY]} for t in types + [OTHER_KEY]}
    # Status x payment as well, so the page can scope the payment tiles to the
    # status the table is actually showing. Free: the GROUP BY below already
    # carries all three columns, so this is a third accumulation over rows we
    # have in hand, not another query.
    status_pay_grid = {st: {pm: 0 for pm in payments + [OTHER_KEY]}
                       for st in statuses + [OTHER_KEY]}

    seen_other_type = seen_other_status = seen_other_payment = False
    for row in (qs.values('registrant_type', 'status', 'payment_status')
                  .annotate(n=Count('id'))):
        t  = row['registrant_type'] if row['registrant_type'] in known_types else OTHER_KEY
        st = row['status'] if row['status'] in status_labels else OTHER_KEY
        pm = row['payment_status'] if row['payment_status'] in payment_labels else OTHER_KEY
        seen_other_type    = seen_other_type    or t  == OTHER_KEY
        seen_other_status  = seen_other_status  or st == OTHER_KEY
        seen_other_payment = seen_other_payment or pm == OTHER_KEY
        grid[t][st] += row['n']
        pay_grid[t][pm] += row['n']
        status_pay_grid[st][pm] += row['n']

    if seen_other_type:
        types.append(OTHER_KEY)
        type_labels[OTHER_KEY] = 'Other'
    if seen_other_status:
        statuses.append(OTHER_KEY)
        status_labels[OTHER_KEY] = 'Other'
    if seen_other_payment:
        payments.append(OTHER_KEY)
        payment_labels[OTHER_KEY] = 'Other'

    by_type    = {t: sum(grid[t][st] for st in statuses) for t in types}
    by_status  = {st: sum(grid[t][st] for t in types) for st in statuses}
    by_payment = {pm: sum(pay_grid[t][pm] for t in types) for pm in payments}
    return {
        'types':          types,
        'statuses':       statuses,
        'payments':       payments,
        'type_labels':    type_labels,
        'status_labels':  status_labels,
        'payment_labels': payment_labels,
        'grid':           grid,
        'pay_grid':       pay_grid,
        'status_pay_grid': status_pay_grid,
        'by_type':        by_type,
        'by_status':      by_status,
        'by_payment':     by_payment,
        'total':          sum(by_type.values()),
    }


class RegistrationSummaryView(APIView):
    """Headline counts for the registration management page — admin/CDSO."""
    permission_classes = [IsAdminOrCdso]

    def get(self, request):
        counts = _registration_counts(VehicleRegistration.objects.all())
        type_labels    = counts['type_labels']
        status_labels  = counts['status_labels']
        payment_labels = counts['payment_labels']
        return Response({
            'total': counts['total'],
            # Each status carries its own payment and type split. The table only
            # ever loads one status, so the payment and type tiles scope their
            # counts to it — a tile reading 120 above a table showing 8 rows is
            # read as a broken page, not as two different questions.
            'by_status': [
                {'key': st, 'label': status_labels.get(st, st), 'count': counts['by_status'][st],
                 'by_payment': {pm: counts['status_pay_grid'][st][pm]
                                for pm in counts['payments']},
                 'by_type': {t: counts['grid'][t][st] for t in counts['types']}}
                for st in counts['statuses']
            ],
            'by_payment': [
                {'key': pm, 'label': payment_labels.get(pm, pm), 'count': counts['by_payment'][pm]}
                for pm in counts['payments']
            ],
            'by_type': [
                {'key': t, 'label': type_labels.get(t, t), 'count': counts['by_type'][t],
                 'by_status': counts['grid'][t], 'by_payment': counts['pay_grid'][t]}
                for t in counts['types']
            ],
        })


class RegistrationSummaryReportPdfView(APIView):
    """Branded PDF of how many registered, broken down by registrant type and status."""
    permission_classes = [IsAdminOrCdso]

    def get(self, request):
        from report_utils import branded_pdf_response, report_filename
        qs, desc = _filter_registrations_report(request)
        counts = _registration_counts(qs)
        type_labels = counts['type_labels']

        def section(axis_keys, axis_labels, cells, totals):
            """One type-by-axis table: a row per registrant type, then the
            all-types row. The type column carries the label, so the rest of
            the landscape width is shared evenly by the count columns."""
            headers = (['Registrant Type']
                       + [axis_labels.get(k, k) for k in axis_keys] + ['Total'])
            rows = [[type_labels.get(t, t)]
                    + [cells[t][k] for k in axis_keys]
                    + [counts['by_type'][t]]
                    for t in counts['types']]
            rows.append(['ALL TYPES']
                        + [totals[k] for k in axis_keys] + [counts['total']])
            n = len(axis_keys)
            return headers, rows, [60] + [(267 - 60 - 30) / n if n else 0] * n + [30]

        status_headers, status_rows, status_widths = section(
            counts['statuses'], counts['status_labels'], counts['grid'], counts['by_status'])
        pay_headers, pay_rows, pay_widths = section(
            counts['payments'], counts['payment_labels'], counts['pay_grid'], counts['by_payment'])

        subtitle = (('; '.join(desc) if desc else 'All records')
                    + f" · {counts['total']} registrations")
        return branded_pdf_response(
            filename=report_filename('Registration Summary Report', 'pdf'),
            report_title='Vehicle Registration Summary Report',
            subtitle=subtitle,
            generated_by=getattr(request.user, 'full_name', ''),
            headers=status_headers,
            rows=status_rows,
            col_widths_mm=status_widths,
            extra_tables=[{
                'title': 'Vehicle Pass Fee — by registrant type',
                'headers': pay_headers,
                'rows': pay_rows,
                'col_widths_mm': pay_widths,
            }],
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