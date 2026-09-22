# =============================================================================
# WHAT THIS FILE IS FOR
#
# Every web address ("endpoint") the vehicles side of the system answers. The
# browser asks; these classes reply. It is the largest file in the project,
# and it covers, in order:
#
#   1. Vehicles, campus rules, lookup lists, parking zones and bays   <- this part
#   2. Cameras: registration, remote pan/tilt/zoom, live preview
#   3. Permission classes and the registration validation helpers
#   4. Approving, rejecting and walk-in registration
#   5. The public registration form and payment
#   6. Owner edits and the CDSO's review of them
#   7. Parking availability and system settings
#   8. Events and parking notices
#   9. Registration periods, suppliers, reports and scheduled visits
#
# Two words worth knowing throughout:
#   "ViewSet"  - one class serving a whole set of related addresses (list one,
#                fetch one, create, edit, delete), wired up by the router.
#   "action"   - an extra address bolted onto a ViewSet for something that is
#                not plain create/read/update/delete, e.g. .../authorize.
#
# Permissions are declared per class rather than checked inside each method, so
# the rule about WHO may do something sits next to the thing itself.
# =============================================================================

import logging
import secrets                                  # cryptographically strong randomness for temporary passwords
import string
import threading
import time as _time
import uuid
from decimal import Decimal, InvalidOperation   # money values, and the error raised by a bad one

import cv2                                      # only for the camera preview endpoints further down
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Value    # Q builds "this OR that" conditions
from django.db.models.functions import Lower, Replace, Upper   # for case/spacing-insensitive matching in the database
from django.http import StreamingHttpResponse, HttpResponse    # StreamingHttpResponse feeds the MJPEG preview
from rest_framework import viewsets, permissions
from rest_framework.decorators import action    # marks the extra endpoints on a ViewSet
from rest_framework.response import Response

from rest_framework import status as drf_status
from .models import Vehicle, RuleConstraint, ParkingSpace, ParkingZone, ReferenceItem, Camera, SystemSettings, ParkingNotice, RegistrationPeriod, Event, ScheduledVisit
from .models import _normalize_plate            # the one way a plate is tidied, shared with the models
from .serializers import VehicleSerializer, RuleConstraintSerializer, ParkingSpaceSerializer, ParkingZoneSerializer, ReferenceItemSerializer, CameraSerializer, ParkingNoticeSerializer, ScheduledVisitSerializer
from . import parking_camera                    # the background detector threads, one per watched zone

logger = logging.getLogger(__name__)
from accounts.audit import audit, AuditedViewSetMixin   # records staff actions; the mixin does it for whole ViewSets
from accounts.twofa_api import HasRecentTwoFactor       # permission: a second factor entered recently
from time_utils import filter_local_date_range
from accounts.models import AuditLog
# NOTE, factually: `from django.utils import timezone` sits at line ~913 rather
# than here, yet is used from line 292 onwards. That works — module-level
# imports all run when the file is first loaded, long before any view is
# called — but it is not where a reader looks for it.

# Vehicles themselves: the list, one vehicle's full profile, and the switch
# that authorises a vehicle at the gate.
class VehicleViewSet(AuditedViewSetMixin, viewsets.ModelViewSet):
    queryset           = Vehicle.objects.select_related('user').all()   # fetch each owner in the same query
    serializer_class   = VehicleSerializer                              # how a vehicle is turned into JSON
    permission_classes = [permissions.IsAuthenticated]                  # any signed-in role
    audit_label        = 'Vehicle'                                      # what the mixin calls this in the audit log

    # Flips a vehicle between authorised and not. PATCH .../vehicles/<id>/authorize/
    @action(detail=True, methods=['patch'])
    def authorize(self, request, pk=None):
        vehicle = self.get_object()
        vehicle.is_authorized = not vehicle.is_authorized    # a toggle: no separate on/off endpoints
        vehicle.save()
        audit(request, AuditLog.Action.RECORD_UPDATED,       # who did this, in the accountability trail
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
            or VehicleRegistration.objects.filter(       # older rows were never linked to the vehicle
                plate_number=vehicle.plate_number,
                status='accepted',
            ).order_by('-reviewed_at').first()           # most recently approved wins
        )

        # Split by whether they are settled, so the screen can show open cases first.
        active_violations   = vehicle.violations.filter(is_resolved=False).order_by('-issued_at')
        resolved_violations = vehicle.violations.filter(is_resolved=True).order_by('-issued_at')

        return {
            'vehicle':             VehicleSerializer(vehicle).data,
            'registration':        VehicleRegistrationSerializer(reg).data if reg else None,
            'active_violations':   ViolationSerializer(active_violations, many=True).data,
            'resolved_violations': ViolationSerializer(resolved_violations, many=True).data,
        }

    # The profile by row id: GET .../vehicles/<id>/profile/
    @action(detail=True, methods=['get'])
    def profile(self, request, pk=None):
        """Return full vehicle profile: owner, latest registration, active and resolved violations."""
        return Response(self._profile_payload(self.get_object()))

    # The same profile found by plate: GET .../vehicles/by-plate/?plate=ABC123
    # detail=False because there is no row id in the address yet — the plate is
    # what we are looking the vehicle up by.
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
        identifier = request.query_params.get('plate', '')   # whatever the caller typed or the detector read
        vehicle = Vehicle.resolve(identifier)                # tries plate, then conduction number
        if vehicle is None:
            return Response({
                'found': False,                              # a normal answer here, not an error
                'plate': _normalize_plate(identifier),       # echo it tidied, so the caller can display it
            })
        return Response({'found': True, **self._profile_payload(vehicle)})   # "**" merges the profile in alongside found

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
    # No permission_classes here: the rule depends on the method, so it is
    # decided in get_permissions() below instead.

    # Reading is open to any signed-in role; changing a rule needs BOTH the
    # admin role and a recent second factor (see the class docstring for why
    # neither check alone is enough).
    def get_permissions(self):
        if self.request.method in permissions.SAFE_METHODS:   # GET, HEAD, OPTIONS: read-only
            return [permissions.IsAuthenticated()]
        return [IsAdminOrCdso(), HasRecentTwoFactor()]        # both must pass

# The editable drop-down lists: departments and programs.
class ReferenceItemViewSet(AuditedViewSetMixin, viewsets.ModelViewSet):
    queryset           = ReferenceItem.objects.all()
    serializer_class   = ReferenceItemSerializer
    permission_classes = [permissions.IsAuthenticated]
    audit_label        = 'Reference Item'

    # Lets the caller ask for one list at a time: ?category=department
    def get_queryset(self):
        qs = super().get_queryset()
        category = self.request.query_params.get('category')
        if category:
            qs = qs.filter(category=category)
        return qs                                        # no category given: return both lists

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
            return False                                 # not signed in: nothing at all
        if request.method in permissions.SAFE_METHODS:
            return True                                  # reading is open to every role
        return getattr(request.user, 'role', None) == 'admin'   # writing is the CDSO's alone


# Individual bays. Drawn and edited through the zone's save-layout action
# below; this ViewSet is what the screens read them back through.
class ParkingSpaceViewSet(viewsets.ModelViewSet):
    queryset           = ParkingSpace.objects.all()
    serializer_class   = ParkingSpaceSerializer
    permission_classes = [ParkingReadOnlyUnlessAdmin]


# A parking area and everything done to it: the bay layout, the empty-lot
# baseline, the detector that watches it, and the live readouts behind the
# occupancy figures.
class ParkingZoneViewSet(AuditedViewSetMixin, viewsets.ModelViewSet):
    # select_related/prefetch_related fetch the camera and the bays up front,
    # so listing zones is a couple of queries rather than two per zone.
    queryset           = ParkingZone.objects.select_related('camera').prefetch_related('spaces').all()
    serializer_class   = ParkingZoneSerializer
    permission_classes = [ParkingReadOnlyUnlessAdmin]    # guards read, CDSO edits
    audit_label        = 'Parking Zone'

    # Extra information handed to the serializer for every zone in the response.
    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx['request'] = self.request
        # Build the category capacity/occupancy map once for the whole response.
        # Without this each zone would fetch it itself, turning a two-query page
        # into two queries per zone.
        from .capacity import category_state
        ctx['category_state'] = category_state()
        return ctx

    # Stores the picture the admin will draw the bays on.
    @action(detail=True, methods=['post'], url_path='upload-image')
    def upload_image(self, request, pk=None):
        zone = self.get_object()
        img  = request.FILES.get('image')                # an uploaded file, not JSON
        if not img:
            return Response({'error': 'No image provided.'}, status=400)
        zone.reference_image = img
        zone.save()
        return Response(self.get_serializer(zone).data)

    # Saves the whole bay layout for a zone in one go: the editor sends the
    # complete set of bays, and this makes the database match it.
    @action(detail=True, methods=['post'], url_path='save-layout')
    def save_layout(self, request, pk=None):
        """Bulk update the parking space layout for this zone."""
        zone        = self.get_object()
        spaces_data = request.data.get('spaces', [])
        submitted   = {s['space_number'] for s in spaces_data}   # the bay labels the editor kept

        # Remove spaces the admin deleted
        zone.spaces.exclude(space_number__in=submitted).delete()   # anything not submitted was deleted in the editor

        # Update or create each space (preserving is_occupied / occupied_by)
        result = []
        for s in spaces_data:
            points = s.get('points')                     # a freehand polygon, if the pen tool was used
            if points:
                # Work out the rectangle that encloses the polygon: the box is
                # what quick lookups compare against, the polygon is the shape.
                xs, ys = [p[0] for p in points], [p[1] for p in points]
                bbox = {'x1': min(xs), 'y1': min(ys), 'x2': max(xs), 'y2': max(ys)}
            else:
                points = None                            # a plain rectangle: no polygon to store
                bbox = {'x1': s.get('x1'), 'y1': s.get('y1'), 'x2': s.get('x2'), 'y2': s.get('y2')}

            # lens_index tags which view of a multi-lens camera the bay is in.
            # Coerced rather than trusted: it indexes a stacked frame, so a
            # negative or absurd value would quietly orphan the bay from every
            # view the editor can show.
            try:
                lens_index = max(0, int(s.get('lens_index') or 0))
            except (TypeError, ValueError):
                lens_index = 0

            # Match on zone + bay label; update that bay if it exists, create it
            # otherwise. Only the listed fields are written, so a bay keeps its
            # current is_occupied / occupied_by while the layout is edited.
            space, _ = ParkingSpace.objects.update_or_create(
                zone=zone,
                space_number=s['space_number'],
                defaults={**bbox, 'points': points, 'lens_index': lens_index},
            )
            result.append(space)

        return Response(ParkingSpaceSerializer(result, many=True).data)   # hand back the saved layout

    @action(detail=True, methods=['post'], url_path='set-baseline')
    def set_baseline(self, request, pk=None):
        """Use this zone's reference image as its empty-lot baseline.

        The reference image is the picture the bays were drawn on, so it is
        already from the exact camera position they belong to — and it is a
        picture the admin has looked at, which the live frame grabbed at the
        moment of the click was not. That frame is how a tricycle parked in the
        bay became the bay's idea of "empty": nobody saw it being captured.

        Whoever sets it is still responsible for the reference showing the bays
        empty. A vehicle in a bay in that picture bakes that vehicle into the
        bay's 'empty' reference, and the bay then reads free while it is taken.
        """
        import cv2 as _cv2
        import numpy as _np
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        zone = self.get_object()
        if not zone.reference_image:
            return Response(
                {'error': 'This zone has no reference image yet. Capture or upload one '
                          'with the bays empty, then set it as the baseline.'},
                status=400)

        try:
            with default_storage.open(zone.reference_image.name, 'rb') as fh:
                data = fh.read()                     # the stored picture, as raw bytes
        except Exception:
            return Response({'error': 'The reference image could not be read. Capture it again.'},
                            status=400)
        image = _cv2.imdecode(_np.frombuffer(data, _np.uint8), _cv2.IMREAD_COLOR)   # decode to check it is a real picture
        if image is None:
            return Response({'error': 'The reference image is not a readable picture. Capture it again.'},
                            status=400)

        # The scorer resizes a baseline to the live frame, which is harmless for
        # a smaller copy of the same view and wrong for any other view: every
        # bay would be compared against a different patch of ground. Only
        # checkable while the camera is sending, so a stopped camera is let
        # through rather than blocking a baseline set before it starts.
        thread = parking_camera.get_thread(zone.id)      # the detector thread for this zone, if one is running
        frame = thread.get_frame() if thread is not None and thread.running else None
        if frame is not None:
            # Compare shapes (width ÷ height), not sizes: the same view at a
            # different resolution is fine, a different view is not.
            ref_aspect  = image.shape[1] / image.shape[0]
            live_aspect = frame.shape[1] / frame.shape[0]
            if abs(ref_aspect - live_aspect) / live_aspect > 0.03:   # more than 3% apart: not the same camera view
                return Response(
                    {'error': 'The reference image is not the same shape as this camera\'s '
                              'picture, so it cannot be this camera\'s view. Capture the '
                              'reference from the live feed, then set it as the baseline.'},
                    status=400)

        # Store a COPY of the reference as the baseline, so later edits to the
        # reference picture do not silently change what "empty" means.
        zone.baseline_image.save(f'zone_{zone.id}_baseline.jpg',
                                 ContentFile(data), save=False)   # save=False: write the row once, below
        zone.baseline_captured_at = timezone.now()       # shown in the admin screen as the baseline's age
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
            return Response({})                          # no detector running: nothing to report, not an error
        return Response(thread.get_signals())            # the per-bay numbers the thresholds are compared against

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
        return Response(thread.get_tracked_vehicles())   # each followed vehicle and how long it has been still

    # Lets an event temporarily declare a different capacity for a zone.
    @action(detail=True, methods=['patch'], url_path='set-capacity')
    def set_capacity(self, request, pk=None):
        """Guard/admin sets (or clears) the event-mode capacity override for a zone."""
        zone = self.get_object()
        value = request.data.get('capacity_override')
        if value is None or str(value).strip() == '':
            zone.capacity_override = None                # blank means "back to the real bay count"
        else:
            try:
                zone.capacity_override = int(value)
                if zone.capacity_override < 0:
                    return Response({'error': 'Capacity must be a non-negative integer.'}, status=400)
            except (TypeError, ValueError):              # not a number at all
                return Response({'error': 'Capacity must be a number.'}, status=400)
        zone.save(update_fields=['capacity_override'])
        return Response(self.get_serializer(zone).data)

    # ── IP Camera ──────────────────────────────────────────────────────────────

    # Starts this zone's detector by hand. It normally starts itself, so this
    # is really "resume after someone pressed Stop".
    @action(detail=True, methods=['post'], url_path='start-camera')
    def start_camera(self, request, pk=None):
        zone = self.get_object()
        if not zone.camera or not zone.camera.rtsp_url:   # a zone with no camera has nothing to watch with
            return Response({'error': 'No camera assigned to this zone. Assign one from Device Management.'}, status=400)
        # Records the intent as well as acting on it: detection is automatic
        # now (detection_supervisor), so this flag is what a restart reads back.
        if not zone.detection_enabled:
            zone.detection_enabled = True                # remember the intent, so a restart keeps it on
            zone.save(update_fields=['detection_enabled'])
        parking_camera.start(zone.id, zone.camera.rtsp_url)   # and act on it now
        return Response({'status': 'started'})

    # Pauses this zone's detector, and makes the pause stick.
    @action(detail=True, methods=['post'], url_path='stop-camera')
    def stop_camera(self, request, pk=None):
        zone = self.get_object()
        # Must persist, or the supervisor would restart the detector on its next
        # pass and the button would look broken.
        if zone.detection_enabled:
            zone.detection_enabled = False               # without this the supervisor would start it again
            zone.save(update_fields=['detection_enabled'])
        parking_camera.stop(zone.id)
        return Response({'status': 'stopped'})

    # Which zones currently have a detector running. One call for all of them,
    # because the screen shows a badge per zone.
    @action(detail=False, methods=['get'], url_path='camera-status')
    def camera_status(self, request):
        """Returns {zone_id: is_running} for all zones."""
        return Response(parking_camera.status_dict())

    # The detector's raw boxes, for seeing why a bay reads as it does.
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
        for zone_id, thread in list(parking_camera.all_threads().items()):   # list(): threads may start or stop while we read
            try:
                out.extend(thread.get_alerts())          # one zone's alerts added to the combined list
            except Exception:
                # One unhealthy zone must not blank the alerts for every other
                # zone, so the failure is logged and the loop carries on.
                logger.exception("Failed reading alerts for zone %s", zone_id)
        return Response(out)


# ── PTZ helpers (ONVIF ContinuousMove / Stop / GotoHomePosition) ─────────────

# Discovered PTZ route per camera: which HTTP port, ONVIF paths, SOAP content
# type and profile token actually work. None of that changes for a given
# camera, but it used to be re-probed on *every* button press — worst case a
# port scan plus 10 SOAP attempts at a 5s timeout each. Press-and-hold on an
# arrow key made that cost repeat per request. Discover once, reuse after.
# =============================================================================
# MOVING A CAMERA (PAN / TILT / ZOOM)
#
# "PTZ" is pan-tilt-zoom: the arrow buttons on the camera screen. There is no
# single standard that every camera obeys, so this block is mostly about
# FINDING OUT how to talk to a particular unit, then remembering the answer:
#
#   which HTTP port it listens on      (80, 8080, 8000, 8899)
#   which language it speaks           ONVIF (SOAP/XML) or a vendor CGI URL
#   which path inside that language    /onvif/PTZ_service, /onvif/ptz, ...
#   which flavour of password it wants Digest, Basic, or none at all
#
# Discovery is expensive — a port scan plus a dozen XML requests — and the
# answer never changes for a given camera, so it is cached per camera and
# reused. Everything before CameraViewSet is that machinery.
# =============================================================================
_PTZ_LOCK  = threading.Lock()                    # these caches are read from several requests at once
_PTZ_CACHE: dict = {}                            # camera id → the route that worked last time


# Returns the remembered route for this camera, or None if there isn't a usable one.
def _ptz_cache_get(cam_id, ip):
    with _PTZ_LOCK:
        info = _PTZ_CACHE.get(cam_id)
    # Ignore the cache if the camera was re-pointed at a different address.
    return info if info and info.get('ip') == ip else None


# Remembers a route that worked.
def _ptz_cache_set(cam_id, info):
    with _PTZ_LOCK:
        _PTZ_CACHE[cam_id] = info


# Forgets it — called when a camera is edited or deleted, or a command fails.
def _ptz_cache_clear(cam_id):
    with _PTZ_LOCK:
        _PTZ_CACHE.pop(cam_id, None)             # pop with a default: not being there is fine


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
        return [('none', None)]                  # nothing to authenticate with
    order = [('digest', HTTPDigestAuth(username, password)),   # ONVIF firmware usually wants this
             ('basic',  (username, password)),                 # older units accept this
             ('none',   None)]                                 # and some want no header at all
    with _PTZ_LOCK:
        won = _AUTH_MODE_CACHE.get(base_url)     # which one worked last time for this camera
    if won:
        # Sort the known-good flavour to the front. False sorts before True, so
        # the matching entry moves first and the rest keep their order.
        order.sort(key=lambda pair: pair[0] != won)
    return order


# Records which password flavour a camera accepted, so the next call starts there.
def _auth_mode_worked(base_url, mode):
    with _PTZ_LOCK:
        _AUTH_MODE_CACHE[base_url] = mode


# "http://1.2.3.4:8080/onvif/ptz" → "http://1.2.3.4:8080": the camera's address
# without any path, which is what the auth cache is keyed on.
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
    # ONVIF's own password scheme (WS-Security): rather than sending the
    # password, send a one-way hash of (random number + timestamp + password).
    # The camera knows the password, so it can compute the same hash and
    # compare — and a recording of the request cannot be replayed later.
    nonce_raw = os.urandom(16)                   # the random number, 16 bytes
    nonce_b64 = base64.b64encode(nonce_raw).decode()   # sent in text form
    created   = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')   # UTC timestamp
    digest    = base64.b64encode(
        hashlib.sha1(nonce_raw + created.encode() + password.encode()).digest()   # the hash, as the standard specifies
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
    auths  = _http_auths(username, password, origin)   # password flavours to try, best guess first
    # Two nested attempts: each XML dialect, and within it each password style.
    for ct in (content_types or ['application/soap+xml; charset=utf-8',   # SOAP 1.2, the modern one
                                 'text/xml; charset=utf-8']):             # SOAP 1.1, for budget cameras
        for mode, auth in auths:
            try:
                r = _rq.post(endpoint, data=data,
                             headers={'Content-Type': ct},
                             timeout=5, auth=auth)   # 5s: a camera that is slower than this is not usable anyway
                if r.status_code in (401, 403):
                    continue                        # wrong password style: try the next one
                if r.status_code < 500:
                    _auth_mode_worked(origin, mode) # remember what worked, even for a SOAP-level error
                    return r, ct
            except Exception:
                break                               # connection failed: this dialect is hopeless, try the next
    raise Exception(f'SOAP request failed for {endpoint}')   # nothing answered


# The addresses different firmwares put their ONVIF services on. Tried in turn
# until one answers; capitalisation varies between vendors, which is why the
# same word appears more than once.
MEDIA_PATHS = ['/onvif/media_service', '/onvif/Media', '/onvif/media',
               '/onvif/device_service', '/onvif/']
PTZ_PATHS   = ['/onvif/PTZ_service', '/onvif/ptz_service', '/onvif/PTZ',
               '/onvif/ptz', '/onvif/']


def _ptz_get_token(base_url, username, password, media_path=None, content_type=None):
    """Discover the profile token. Returns (token, media_path, content_type).

    Passing a known media_path/content_type turns the probe into one request.
    """
    from xml.etree import ElementTree as ET
    # A "profile token" names one of the camera's stream profiles; PTZ commands
    # have to say which profile they apply to. Ask the camera for its profiles
    # and take the first token in the reply.
    # Try common ONVIF media service paths — cameras vary on capitalisation
    for path in ([media_path] if media_path else MEDIA_PATHS):   # one known path, or all candidates
        try:
            resp, ct = _ptz_soap(f'{base_url}{path}',
                                 '<trt:GetProfiles xmlns:trt="http://www.onvif.org/ver10/media/wsdl"/>',
                                 username, password,
                                 [content_type] if content_type else None)
            for el in ET.fromstring(resp.text).iter():   # walk every element in the XML reply
                t = el.get('token')
                if t:
                    return t, path, ct               # first token wins, plus the route that found it
        except Exception:
            continue                                 # this path did not work; try the next
    if media_path:
        raise Exception('cached ONVIF media path stopped responding')   # the remembered route has gone stale
    return 'Profile_1', None, None                   # nothing answered: try the name most firmwares use


# Sends one PTZ command, trying each known service path until one accepts it.
def _ptz_send(base_url, username, password, body_xml, ptz_path=None, content_type=None):
    """Send a PTZ command. Returns (ptz_path, content_type) that worked."""
    for path in ([ptz_path] if ptz_path else PTZ_PATHS):
        try:
            r, ct = _ptz_soap(f'{base_url}{path}', body_xml, username, password,
                              [content_type] if content_type else None)
            if r.status_code < 400:
                return path, ct                      # accepted: hand back the working route to cache
        except Exception:
            continue
    raise Exception('No PTZ service path responded successfully')


# Start moving. "Continuous move" means keep going at this speed until told to
# stop, which is what an arrow button held down should do. pan/tilt/zoom are
# -1.0 to 1.0, where the sign is the direction.
def _ptz_move(base_url, username, password, token, pan, tilt, zoom, **route):
    return _ptz_send(base_url, username, password,
        '<tptz:ContinuousMove xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl">'
        f'<tptz:ProfileToken>{token}</tptz:ProfileToken>'
        '<tptz:Velocity>'
        f'<tt:PanTilt xmlns:tt="http://www.onvif.org/ver10/schema" x="{pan:.2f}" y="{tilt:.2f}"/>'
        f'<tt:Zoom xmlns:tt="http://www.onvif.org/ver10/schema" x="{zoom:.2f}"/>'
        '</tptz:Velocity></tptz:ContinuousMove>', **route
    )


# Stop moving, both the pan/tilt motors and the zoom.
def _ptz_stop(base_url, username, password, token, **route):
    return _ptz_send(base_url, username, password,
        '<tptz:Stop xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl">'
        f'<tptz:ProfileToken>{token}</tptz:ProfileToken>'
        '<tptz:PanTilt>true</tptz:PanTilt><tptz:Zoom>true</tptz:Zoom>'
        '</tptz:Stop>', **route
    )


# Return to the position the camera was set up pointing at.
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

    stored  = (getattr(cam, 'password', '') or '').strip()   # the password field admins edit
    url     = (getattr(cam, 'rtsp_url', '') or '')
    url_user = url_pw = ''
    # Pull any credentials out of the stream address, which looks like
    # rtsp://user:pass@1.2.3.4:554/stream1
    if '://' in url and '@' in url:
        rest  = url.split('://', 1)[1]          # drop the "rtsp://"
        creds = rest.rsplit('@', 1)[0]          # rsplit: passwords may contain '@'
        if ':' in creds:
            url_user, url_pw = creds.split(':', 1)   # split once: passwords may contain ':' too
        else:
            url_user, url_pw = creds, ''        # a username with no password
        url_user, url_pw = unquote(url_user), unquote(url_pw)   # undo %40-style URL escaping

    if url_user:
        return url_user, (stored or url_pw)     # URL's username, but prefer the password admins maintain

    return (getattr(cam, 'device_id', '') or ''), stored   # no URL credentials: fall back to the device id


def _try_cgi_ptz(base_url, username, password, command, speed_int, cgi_form=None):
    """CGI fallback for cameras that use HTTP but not ONVIF (Dahua/Hi3510 style).

    Returns the index of the URL form that worked so it can be reused.
    """
    import requests as _rq
    # The same command spelled the way each vendor's firmware expects. An
    # unknown command falls back to "stop", which is the safe thing to send.
    dahua_code = {
        'up': 'Up', 'down': 'Down', 'left': 'Left', 'right': 'Right',
        'zoom_in': 'ZoomTele', 'zoom_out': 'ZoomWide', 'stop': 'Stop', 'home': 'GotoPreset',
    }.get(command, 'Stop')
    hi3510_act = {
        'up': 'up', 'down': 'down', 'left': 'left', 'right': 'right',
        'zoom_in': 'zoomadd', 'zoom_out': 'zoomdec', 'stop': 'stop', 'home': 'poscall',
    }.get(command, 'stop')
    # Three URL shapes, covering the common non-ONVIF firmwares.
    forms = [
        f'{base_url}/cgi-bin/ptz.cgi?action=start&channel=1&code={dahua_code}&arg1=0&arg2={speed_int}&arg3=0',
        f'{base_url}/cgi-bin/ptzctrl.cgi?ptzcmd&{hi3510_act}&{speed_int}',
        f'{base_url}/cgi-bin/hi3510/ptzctrl.cgi?-step=0&-act={hi3510_act}&-speed={speed_int}',
    ]
    candidates = ([(cgi_form, forms[cgi_form])] if cgi_form is not None   # a known-good form, or
                  else list(enumerate(forms)))                           # all of them, numbered
    auths = _http_auths(username, password, base_url)

    errors = []                                      # collected so a failure can say what went wrong
    for idx, url in candidates:
        for mode, auth in auths:
            try:
                r = _rq.get(url, auth=auth, timeout=3)
                if r.status_code < 400:
                    _auth_mode_worked(base_url, mode)
                    return idx                       # this form works; the caller caches the number
                if r.status_code in (401, 403):
                    continue          # try the next credential form
                errors.append(f'{r.status_code}')
                break                                # a real refusal: this URL shape is wrong, move on
            except Exception as e:
                errors.append(str(e))
                break
    raise Exception('CGI PTZ failed: ' + '; '.join(errors) or 'CGI PTZ failed')


# Device Management: adding cameras, checking they answer, and driving them.
class CameraViewSet(AuditedViewSetMixin, viewsets.ModelViewSet):
    queryset           = Camera.objects.all()
    serializer_class   = CameraSerializer
    permission_classes = [permissions.IsAuthenticated]
    audit_label        = 'Camera'

    # The lowest camera number not already taken, so numbering closes gaps left
    # by deleted cameras rather than climbing forever.
    def _next_cam_number(self):
        existing = set(Camera.objects.values_list('cam_number', flat=True))   # a set: fast "is it taken?"
        n = 1
        while n in existing:
            n += 1
        return n

    # Adding a camera. Overridden so the number and display name are assigned
    # here rather than being typed in and possibly duplicated.
    def create(self, request, *args, **kwargs):
        num        = self._next_cam_number()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)    # invalid input answers 400 automatically
        cam = serializer.save(cam_number=num, name=f'Cam {num}')   # e.g. "Cam 3"
        audit(request, AuditLog.Action.RECORD_CREATED,
              f"Camera added | {cam} | IP: {cam.ip} | By: {request.user.full_name}")
        return Response(serializer.data, status=drf_status.HTTP_201_CREATED)

    # Editing a camera: keep the normal behaviour, then forget the remembered
    # PTZ route, since the address or password may just have changed.
    def perform_update(self, serializer):
        # super() keeps the audit-log entry; we only add cache invalidation.
        # IP/credentials may have changed, so the discovered PTZ route is no
        # longer trustworthy — force rediscovery on the next command.
        super().perform_update(serializer)
        _ptz_cache_clear(serializer.instance.pk)

    # Deleting a camera: same idea, but the id has to be read BEFORE the delete.
    def perform_destroy(self, instance):
        cam_id = instance.pk          # delete() clears the pk
        super().perform_destroy(instance)
        _ptz_cache_clear(cam_id)

    # Lets the add-camera form show the number and name it is about to get.
    @action(detail=False, methods=['get'], url_path='next-name')
    def next_name(self, request):
        n = self._next_cam_number()
        return Response({'cam_number': n, 'name': f'Cam {n}'})

    # Asks the camera itself which stream address it answers on, so nobody has
    # to know the vendor's URL format.
    @action(detail=False, methods=['post'], url_path='detect-rtsp')
    def detect_rtsp(self, request):
        """Ask the camera which stream path it answers on.

        Replaces the vendor picker in the add-camera form: which firmware a unit
        runs is not something the person mounting it should have to know, and
        the camera can be asked directly.
        """
        from . import rtsp_probe

        try:
            channel = int(request.data.get('channel') or 1)   # which lens/channel on a multi-channel unit
        except (TypeError, ValueError):
            channel = 1                              # anything unparseable means the first channel

        result = rtsp_probe.detect(                  # does the real work: tries known URL shapes
            ip=request.data.get('ip', ''),
            device_id=request.data.get('device_id', ''),
            password=request.data.get('password', ''),
            channel=channel,
        )
        # NOTE: `status` here is the module imported at line ~984, further down
        # the file, not the `drf_status` alias used above. Both name the same
        # thing; module-level imports run at load time, so it resolves.
        return Response(result, status=(status.HTTP_200_OK if result['ok']
                                        else status.HTTP_400_BAD_REQUEST))

    # "Is this camera reachable?" — opens a bare TCP connection to the RTSP
    # port. It proves the network path without logging in or pulling video.
    @action(detail=True, methods=['post'], url_path='ping')
    def ping(self, request, pk=None):
        import socket
        cam = self.get_object()
        try:
            with socket.create_connection((cam.ip, 554), timeout=3):   # 554 is the standard RTSP port
                return Response({'reachable': True, 'ip': cam.ip})     # connecting is the whole test
        except (socket.timeout, ConnectionRefusedError, OSError):
            return Response({'reachable': False, 'ip': cam.ip})        # unreachable is an answer, not an error

    # The arrow buttons. One endpoint for every direction, plus stop and home.
    #
    # The shape of this method is: try the remembered route first (fast), and
    # only if that fails work out a new one (slow), then remember it.
    @action(detail=True, methods=['post'], url_path='ptz')
    def ptz(self, request, pk=None):
        import socket
        cam     = self.get_object()
        command = (request.data.get('command') or 'stop').strip()   # default to stop: the harmless command
        speed   = min(max(float(request.data.get('speed', 0.5)), 0.1), 1.0)   # clamp into 0.1–1.0 whatever was sent

        # Each command as a (pan, tilt, zoom) velocity; the sign is direction.
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

        # Runs the command one way (ONVIF or CGI) against one address, and
        # reports back the route that succeeded so it can be cached.
        def _send(base, route):
            """Run `command` against `base`. Returns the route that worked."""
            if route.get('method') == 'cgi':
                idx = _try_cgi_ptz(base, cam_user, cam_pw, command,
                                   speed_int, route.get('cgi_form'))
                return {**route, 'method': 'cgi', 'cgi_form': idx}   # remember which URL shape worked

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
            kw = {'ptz_path': route.get('ptz_path'), 'content_type': route.get('ptz_ct')}   # pass the known route through
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
        cached = _ptz_cache_get(cam.pk, cam.ip)      # None if never discovered, or the camera moved
        if cached:
            try:
                route = _send(cached['base'], cached)   # one request instead of a probe
                _ptz_cache_set(cam.pk, {**route, 'ip': cam.ip, 'base': cached['base']})   # refresh what we learned
                return Response({'ok': True, 'command': command,
                                 'method': route['method'], 'cached': True})
            except Exception:
                # Camera rebooted, moved, or firmware changed — rediscover below.
                _ptz_cache_clear(cam.pk)

        # ── Discovery: probe ports, ONVIF paths, then the CGI fallback ─────────
        last_err = 'No HTTP port reachable on the camera'   # replaced as soon as anything answers
        for port in [80, 8080, 8000, 8899]:          # the ports these cameras are usually found on
            try:
                with socket.create_connection((cam.ip, port), timeout=1.5):
                    pass                             # opening and closing is the whole test
            except OSError:
                continue                             # nothing listening here; try the next port
            base = f'http://{cam.ip}' if port == 80 else f'http://{cam.ip}:{port}'   # port 80 needs no suffix
            # Try ONVIF first, then CGI fallback
            onvif_err = None
            for method in ('onvif', 'cgi'):
                try:
                    route = _send(base, {'method': method})   # empty route = discover everything
                    _ptz_cache_set(cam.pk, {**route, 'ip': cam.ip, 'base': base})   # so the next press is fast
                    return Response({'ok': True, 'command': command,
                                     'method': route['method'], 'cached': False})
                except Exception as exc:
                    if method == 'onvif':
                        onvif_err = str(exc)         # hold it: the CGI attempt may still succeed
                    else:
                        last_err = f'onvif: {onvif_err} | cgi: {exc}'   # both failed; report both reasons

        return Response({'ok': False, 'error': f'PTZ unavailable: {last_err}'}, status=400)

    # Lets the caller narrow the camera list, e.g. ?assignment=entry&gate_id=gate1
    def get_queryset(self):
        qs = super().get_queryset()
        assignment = self.request.query_params.get('assignment')
        if assignment:
            qs = qs.filter(assignment=assignment)    # entry cameras or parking cameras
        gate_id = self.request.query_params.get('gate_id')
        if gate_id:
            qs = qs.filter(gate_id=gate_id)          # cameras covering one gate
        return qs


# Draws a "no picture" image, so the viewer always has something to show
# rather than a broken image icon.
def _make_placeholder_jpeg(text: str) -> bytes:
    """Generate a dark grey JPEG with centred status text — sent when no live frame is available."""
    import numpy as np
    blank = np.full((480, 640, 3), 30, dtype=np.uint8)   # a 640x480 picture filled with near-black
    (tw, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)   # measure the text first
    x = max(0, (640 - tw) // 2)                  # centre it horizontally
    cv2.putText(blank, text, (x, 248), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (160, 160, 160), 2)   # y=248: middle-ish
    _, buf = cv2.imencode('.jpg', blank, [cv2.IMWRITE_JPEG_QUALITY, 60])   # low quality is plenty for flat grey
    return buf.tobytes()


# Wraps one JPEG in the separator an MJPEG stream needs. MJPEG is simply a
# never-ending HTTP response of JPEGs one after another, which is why a plain
# <img> tag can display a live feed.
def _mjpeg_frame(jpeg_bytes: bytes) -> bytes:
    return b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + jpeg_bytes + b'\r\n'


import asyncio                                   # (module-level import, placed here beside its only user)

async def parking_stream_view(request, pk):
    """
    Async MJPEG stream for a parking zone camera.
    Auth via ?token=<JWT> because <img> tags cannot send Authorization headers.
    """
    from rest_framework_simplejwt.authentication import JWTAuthentication
    from rest_framework_simplejwt.exceptions import TokenError, InvalidToken

    token_str = request.GET.get('token', '')     # the token rides in the URL, as the docstring explains
    if not token_str:
        return HttpResponse(status=401)          # 401: not authenticated
    try:
        JWTAuthentication().get_validated_token(token_str)   # checks signature and expiry
    except (TokenError, InvalidToken):
        return HttpResponse(status=401)

    zone_id = int(pk)                            # which parking zone's camera to show

    # Rendered once, up front, so the loop below never pays to draw them.
    _connecting = _make_placeholder_jpeg('Connecting to camera...')
    _no_camera  = _make_placeholder_jpeg('Camera not running')

    # Produces frames forever; Django sends each one as it is yielded, which is
    # what keeps the response open as a live stream.
    async def _generate():
        loop = asyncio.get_event_loop()
        try:
            while True:
                thread = parking_camera.get_thread(zone_id)   # the detector thread holding this zone's frames
                if not thread:
                    yield _mjpeg_frame(_no_camera)   # show the placeholder rather than closing the stream
                    await asyncio.sleep(0.5)         # check again shortly, in case it starts
                    continue

                # get_jpeg() encodes at most once per frame and shares the
                # result across every viewer, so N watchers cost the same as
                # one. Still run in the thread pool — the first viewer to reach
                # a new frame does the encode and must not block the loop.
                jpeg_bytes = await loop.run_in_executor(None, thread.get_jpeg)
                if jpeg_bytes is None:
                    yield _mjpeg_frame(_connecting)  # thread exists but has no picture yet
                    await asyncio.sleep(0.1)
                    continue

                yield _mjpeg_frame(jpeg_bytes)       # a real frame: hand it to the browser
                await asyncio.sleep(1 / 20)  # cap at 20 fps
        except (asyncio.CancelledError, GeneratorExit):
            return                                   # the viewer closed the tab; stop quietly

    return StreamingHttpResponse(
        _generate(),
        # This content type is what tells the browser to keep replacing the
        # picture as new parts arrive, instead of waiting for the end.
        content_type='multipart/x-mixed-replace; boundary=frame',
    )


from rest_framework.views import APIView
from rest_framework import status
from rest_framework.parsers import JSONParser, MultiPartParser, FormParser
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from .models import RegistrationChangeRequest, VehicleRegistration
from .registration_edits import (READ_ONLY_REASONS, apply_changes, clean_changes,
                                 current_values, describe, editable_for)
from .serializers import VehicleRegistrationSerializer
from .control_numbers import allocate_control_number, is_ebike, peek_next_control_number
from .campus_days import (ALL_DAYS, MAX_CAMPUS_DAYS, SCHEDULE_DAY_LABELS,
                          SCHEDULE_GROUP_DAYS, clean_campus_days,
                          resolve_student_schedule, schedule_group)
from accounts.models import User
from .email_utils import (send_acceptance_email, send_rejection_email,
                          send_pending_email, send_receipt_received_email,
                          send_registration_updated_email,
                          send_change_request_decision_email,
                          send_in_background)


# =============================================================================
# WHO MAY DO WHAT
#
# A "permission class" answers one yes/no question before a view runs. Listing
# one in permission_classes is how an endpoint states its own access rule.
#
# NOTE, factually (no code changed): IsAdminRole and IsAdminOrCdso have
# identical bodies — both allow exactly role == 'admin'. The second name is
# historical: 'admin' IS the CDSO since the separate 'cdso' role was folded
# into it (migration 0024), so the "or" no longer adds anything.
# =============================================================================
class IsAdminRole(permissions.BasePermission):
    def has_permission(self, request, view):
        # bool(...) because the checks can yield None for an anonymous caller,
        # and a permission must answer True or False.
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
        zone_id   = request.data.get('zone_id')      # which lot the alert came from
        space_ids = request.data.get('space_ids') or []   # the bays the car is straddling
        plate     = (request.data.get('plate_number') or '').strip().upper().replace(' ', '')   # what the guard read off the car
        if not zone_id or not space_ids or not plate:
            return Response({'error': 'zone_id, space_ids and plate_number are required.'},
                            status=status.HTTP_400_BAD_REQUEST)

        vehicle = Vehicle.resolve(plate)             # plate first, then conduction number
        if vehicle is None:
            return Response({'error': 'No vehicle found for that plate or conduction number.'},
                            status=status.HTTP_404_NOT_FOUND)   # nothing to attribute the violation to

        # Pull the evidence captured when the straddle was detected and clear the alert.
        thread = parking_camera.get_thread(int(zone_id))
        # pop_alert does both jobs at once: hands back the photo AND removes the
        # alert, so the card disappears from the guard's screen.
        evidence = thread.pop_alert(space_ids) if thread is not None else None

        from scanning.views import _auto_log_violation   # the shared "raise a violation" helper
        from violations.models import Violation
        gate_id = getattr(request.user, 'gate_assignment', None) or 'main'   # attribute it to the guard's gate
        _auto_log_violation(
            vehicle,
            f"Double parking attributed by guard {request.user.full_name}",   # names who decided, in the record
            gate_id=gate_id,
            vtype=Violation.Type.DOUBLE_PARKING,
            evidence_bytes=evidence,                 # the boxed photo taken when it was detected
        )

        try:
            # Tell every open screen at once, so the alert card clears without
            # anyone refreshing.
            from realtime.broadcast import broadcast_change
            broadcast_change('parkingspace', 'double_parking_attributed', zone_id=int(zone_id))
        except Exception:
            logger.exception("double-parking attribution broadcast failed")   # the violation still stands

        return Response({'status': 'attributed',
                         'plate_number': vehicle.plate_number or vehicle.conduction_number or plate})


# The CDSO's review queue: the applications waiting for a decision.
class PendingRegistrationsListView(APIView):
    permission_classes = [IsAdminOrCdso]

    def get(self, request):
        # Defaults to pending, but the screen can ask for accepted or rejected
        # to review what was already decided.
        status_filter = request.query_params.get('status', VehicleRegistration.Status.PENDING)
        # select_related pulls the department in the same SELECT, prefetch_related
        # collects every fetcher's per-student assessments in one more, and the
        # prebuilt block-count map replaces one COUNT per row with one query for
        # the page — together they turn a 3N+1 query pattern into a flat 3.
        registrations = list(                        # list(): fetch once, then reuse for the block counts below
            VehicleRegistration.objects
            .filter(status=status_filter)
            .select_related('department')
            .prefetch_related('fetcher_assessments')
            .order_by('-created_at')                 # newest application first
        )
        return Response(VehicleRegistrationSerializer(
            registrations,
            many=True,
            context={
                'request': request,
                # One query for the whole page's violation-block counts, instead
                # of one per application.
                'block_counts': VehicleRegistrationSerializer.build_block_counts(registrations),
            },
        ).data)


# =============================================================================
# REGISTRATION HELPERS
#
# Everything below here supports the registration endpoints in the next
# segments. Two themes run through it:
#
#   1. One identity, one active pass. A plate, conduction sticker, email,
#      driver's licence or school ID may belong to at most ONE pending or
#      accepted registration. The _*_conflict functions each check one of
#      those, and _registration_conflict runs them in order.
#
#   2. Ask the database, not Python. Each check is written so PostgreSQL can
#      answer it from an index. The long comments record what the earlier
#      versions did instead — fetching every active registration and comparing
#      in a loop, which grew slower with every application on file.
# =============================================================================

# Builds the one-time password a newly approved owner is emailed.
def _generate_temp_password():
    """Generate a secure temporary password that meets all strength requirements."""
    # One character guaranteed from each required group, so the result always
    # satisfies the password rules rather than passing by luck.
    lowercase = secrets.choice(string.ascii_lowercase)
    uppercase = secrets.choice(string.ascii_uppercase)
    digit     = secrets.choice(string.digits)
    special   = secrets.choice('!@#$%^&*()_+-=')
    # Fill remaining 8 chars from full set
    alphabet  = string.ascii_letters + string.digits + '!@#$%^&*()_+-='
    rest      = [secrets.choice(alphabet) for _ in range(8)]   # 12 characters in total
    password_chars = [lowercase, uppercase, digit, special] + rest
    # Shuffle, or the first four characters would always be in the same order.
    # SystemRandom, like secrets, draws on the operating system's randomness.
    secrets.SystemRandom().shuffle(password_chars)
    return ''.join(password_chars)


# The same tidy-up the models use, kept here so this file can call it directly.
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


# Anything unrecognised becomes a car, which is the safe default for counting
# and for the gate: it is the commonest type and takes a full bay.
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
    defaults = {                                     # written whether the row is created or adopted
        'vehicle_type':  _vehicle_type_for(registration.vehicle_type),
        'color':         registration.vehicle_color,
        'is_authorized': True,                       # approval is what authorises the vehicle at the gate
        'user':          user,                       # and gives it an owner
    }
    # Match on whichever identifier this registration actually carries, and
    # write the other one too, so a car that gains a plate loses its sticker.
    if plate:
        defaults['conduction_number'] = conduction  # normally ''
        vehicle_obj, _ = Vehicle.objects.update_or_create(plate_number=plate, defaults=defaults)
    else:
        defaults['plate_number'] = plate            # ''
        vehicle_obj, _ = Vehicle.objects.update_or_create(conduction_number=conduction, defaults=defaults)
    return vehicle_obj


def _assign_system_id(registration):
    """Stamp the registration's system ID on acceptance and return it.

    The prefix names what the holder actually is — a fetcher is not staff, so
    minting them an SLC-EMP- code mislabels them on their own dashboard. Only
    two columns exist to hold the value: students get their own, and everyone
    else shares `system_employee_id` as the non-student slot. Storage is not
    the label, so the fetcher's code lands in that slot carrying SLC-FET-.
    Every reader already falls back across the two columns."""
    padded_id = str(registration.pk).zfill(6)        # row number as six digits, e.g. 42 → "000042"
    kind = registration.registrant_type
    if kind == 'student':
        registration.system_student_id = f"SLC-STU-{padded_id}"
        return registration.system_student_id
    prefix = 'SLC-FET' if kind == 'fetcher' else 'SLC-EMP'   # the prefix carries the meaning...
    registration.system_employee_id = f"{prefix}-{padded_id}"   # ...even though both share this column
    return registration.system_employee_id


# Is this plate already spoken for? Two ways it can be: another live
# application holds it, or an owned vehicle already carries it.
# `qs` is the set of active registrations to search, built by the caller.
def _plate_conflict(plate_number, qs):
    plate_norm = _normalize_plate(plate_number)
    if not plate_norm:
        return None                                  # no plate given (a conduction-only application)
    # Stored plates may vary in spacing/case, so compare normalized values.
    #
    # This normalisation runs in SQL, not Python. It used to pull every active
    # registration's plate over the wire and scan them in a loop — O(N) on
    # every submission and every keystroke of the availability check, which at
    # 2,000 rows already cost ~1.1s per call against Neon. The expression index
    # `vehreg_plate_norm` matches this exact expression, so Postgres answers it
    # with an index lookup instead.
    if qs.exclude(plate_number='').annotate(         # skip conduction-only rows, whose plate is blank
        _plate_norm=Upper(Replace('plate_number', Value(' '), Value('')))   # UPPER(plate with spaces removed), computed in SQL
    ).filter(_plate_norm=plate_norm).exists():       # .exists(): ask "is there one?", do not fetch rows
        return "This plate number already has an active registration."
    # Unowned Vehicle rows are adopted by update_or_create at accept time,
    # so only plates already tied to an account are conflicts.
    #
    # Exact, not __iexact: Vehicle plates are always stored normalised (see
    # _upsert_vehicle_for_registration and Vehicle.resolve, which both look them
    # up with an exact match). __iexact wrapped the column in UPPER() and made
    # this a sequential scan of tbl_vehicle on every submission and every
    # keystroke of the availability check, ignoring uniq_vehicle_plate_number.
    if Vehicle.objects.filter(plate_number=plate_norm, user__isnull=False).exists():   # user__isnull=False: only OWNED vehicles
        return "This plate number is already registered to an existing vehicle pass."
    return None                                      # free to use


# Same question for the email address, which also becomes the portal login.
def _email_conflict(email, qs):
    email_norm = (email or '').strip().lower()
    if not email_norm:
        return None
    # Stored emails are normalized on save, but compare defensively anyway.
    # Done in SQL against the `vehreg_email_norm` expression index — the old
    # Python loop fetched every active registration's email per call.
    if qs.exclude(email='').annotate(
        _email_norm=Lower('email')                   # matches the vehreg_email_norm index
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
    if User.objects.filter(email__iexact=email_norm, is_archived=False).exists():   # is_archived=False: expired accounts do not block
        return "This email address is already tied to an existing account."
    return None


def _conduction_conflict(conduction_number, qs):
    """Conduction sticker equivalent of _plate_conflict: unique among active
    registrations and not already tied to an owned Vehicle.

    conduction_number is a new field, always normalized (upper, no spaces) on
    save, so both checks are exact indexed lookups — O(1)-ish, no table scan.
    """
    norm = _normalize_plate(conduction_number)       # conduction numbers are tidied the same way as plates
    if not norm:
        return None
    if qs.filter(conduction_number=norm).exists():   # exact match: the column is always stored normalised
        return "This conduction number already has an active registration."
    if Vehicle.objects.filter(conduction_number=norm, user__isnull=False).exists():
        return "This conduction number is already tied to an existing vehicle pass."
    return None


# One driver's licence, one active pass: it is the licence holder who is being
# permitted to drive on campus, whichever vehicle they bring.
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


# The school's own ID numbers. Each is only checked for the type it belongs to,
# so a student ID and an employee ID never collide with each other.
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
    # "Active" means pending or accepted: a rejected or expired application
    # releases its plate, email and IDs for somebody to use again.
    if statuses is None:
        statuses = [VehicleRegistration.Status.PENDING, VehicleRegistration.Status.ACCEPTED]
    qs = VehicleRegistration.objects.filter(status__in=statuses)
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)               # when editing, a row must not conflict with itself

    # Checked in order, stopping at the first problem, so the applicant is told
    # one clear reason rather than a list.
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
    return _id_conflict(registrant_type, student_id, employee_id, qs)   # None here means everything is free


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

    # Build "matches ANY of these identifiers" — the ban follows the person, so
    # changing car or email address is not a way around it.
    conds = Q()
    if email_norm:
        conds |= Q(_email_norm=email_norm)           # "|=" adds another OR branch
    if plate_norm:
        conds |= Q(_plate_norm=plate_norm)
    if conduction_norm:
        conds |= Q(conduction_number=conduction_norm)
    if student_id:
        conds |= Q(student_id=student_id)
    if employee_id:
        conds |= Q(employee_id=employee_id)
    if not conds:
        return None                                  # nothing identifying was supplied: nothing to match

    # The ban lives on the ACCOUNT; these rows are how we get from an
    # identifier to the account that used it.
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
    dept_raw = data.pop('department', None)          # removed: the label is not what the column stores
    if isinstance(dept_raw, list):
        dept_raw = dept_raw[0] if dept_raw else None   # form uploads arrive as one-item lists

    # Turn the choices round: label ("Teaching") → stored value ("teaching").
    dept_label_to_value = {
        label: value for value, label in VehicleRegistration.DepartmentType.choices
    }
    data['department'] = None                        # the FK to a ReferenceItem row is not set from this form
    dept_value = dept_label_to_value.get(dept_raw, '')   # '' for a label we do not recognise
    if dept_value:
        data['department_type'] = dept_value         # the value the fee rules read
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
        drivers_license = drivers_license[0] if drivers_license else ''   # again, form values may arrive as lists
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
    # Reads one form value as tidy text, whether it arrived as a string or a
    # one-item list.
    def _val(key):
        v = data.get(key, '')
        if isinstance(v, list):
            v = v[0] if v else ''
        return (v or '').strip()

    if registrant_type != 'student':
        return None                                  # only student applications have this split

    level       = _val('student_level')
    driver_name = _val('driver_name')

    # A minor must name an adult driver. Checked here, not only in the browser,
    # so a direct API call cannot skip it.
    if level in MINOR_STUDENT_LEVELS and not driver_name:
        return ("Junior High and Elementary students are minors and cannot drive. "
                "An authorized driver (parent/guardian) is required.")

    # If someone else will drive, the record must say who they are to the
    # student and whose licence is on file.
    if driver_name:
        if not _val('driver_relationship'):
            return "Please specify the authorized driver's relationship to the student."
        if not _val('drivers_license'):
            return "The authorized driver's license number is required."
    return None                                      # valid


def _acceptance_email_failed_notice(registration):
    """The admin-bell replacement for the old `email_status: 'failed'` warning.

    The pass is already issued at this point; what the CDSO needs to know is
    that the owner never received the credentials to use it.
    """
    from accounts.notifications import notify
    plate = registration.plate_number or registration.conduction_number or ''   # name the vehicle however we can

    # Returns the function rather than calling it: the caller decides when to
    # raise the notice, typically only once the approval itself has committed.
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

    def _notice():                                   # same deferred pattern as above
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

        registration = get_object_or_404(VehicleRegistration, pk=pk)   # 404 rather than a crash for a bad id
        # The document states the registration is approved, so an application
        # still under review has no printable confirmation — printing one would
        # put a pass in someone's hands that the CDSO has not granted.
        if registration.status != VehicleRegistration.Status.ACCEPTED:
            return Response(
                {'detail': 'Only an accepted registration can be printed.'},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )

        pdf = registration_confirmation_pdf(registration, include_documents=True)   # the filed copy includes the scans
        audit(request, AuditLog.Action.RECORD_CREATED,     # printing a pass document is itself worth recording
              f"Registration PDF printed | REG-{registration.id:06d} "
              f"({registration.plate_number}) | For: {registration.full_name}")

        resp = HttpResponse(pdf, content_type='application/pdf')
        resp['Content-Disposition'] = (                    # "attachment" makes the browser download it
            f'attachment; filename="{registration_pdf_filename(registration)}"'
        )
        return resp


# =============================================================================
# APPROVING AN APPLICATION
#
# This is where an application becomes a vehicle pass, and it is the most
# consequential endpoint in the file: one POST creates a portal account, issues
# a temporary password, authorises a vehicle at the gate and emails credentials
# to a real person.
#
# It reads as a sequence of gates, each of which can refuse, followed by one
# all-or-nothing write:
#
#   1. Is it still pending?                      (nothing to approve twice)
#   2. Does any identifier now clash?            (plate, email, licence, IDs)
#   3. Has the applicant been banned since?      (re-checked, not trusted from submission)
#   4. Payment: exempt or receipted — an unsettled fee refuses the approval
#   5. Is the plate flagged from a 3rd offence?  (CDSO must acknowledge)
#   6. Campus-day overrides, and whether they exceed the normal allowance
#   7. ── transaction ── account + vehicle + system ID + the registration row
#   8. After it commits: email the owner, in the background
#
# Step 7 is one transaction for a reason recorded in the comment there; step 8
# is deliberately outside it.
# =============================================================================
class AcceptRegistrationView(APIView):
    permission_classes = [IsAdminOrCdso]

    def post(self, request, pk):
        registration = get_object_or_404(VehicleRegistration, pk=pk)
        # Only a pending application can be approved: this also makes a
        # double-click harmless, since the second one finds it already accepted.
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
            # Only ACCEPTED rows count here, not other pendings: two people may
            # both have applied, and approving one of them is how that is settled.
            statuses=[VehicleRegistration.Status.ACCEPTED],
            exclude_pk=registration.pk,              # never conflict with itself
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
        exempt    = registration.payment_status == VehicleRegistration.PaymentStatus.EXEMPT   # nothing was owed
        or_number = (request.data.get('or_number') or '').strip() or (registration.or_number or '').strip()   # typed now, else what is on file

        if exempt:
            # Nothing was owed, so there is no receipt to demand. Requiring one
            # here used to force CDSO to invent an OR number for fee-exempt
            # staff before the accept button would enable.
            or_number = ''
        elif or_number and (not or_number.isdigit() or len(or_number) > 7):
            return Response({"error": "Official Receipt (OR) number must be at most 7 digits."}, status=status.HTTP_400_BAD_REQUEST)

        # An outstanding fee is a HARD block on approval.
        #
        # This used to be permitted as long as CDSO typed a justification into
        # unpaid_accept_reason, which meant a pass could be issued - and open
        # the gate - against money that had never been collected. The field is
        # kept and the rows written while the old rule stood keep their text,
        # but nothing writes to it any more.
        #
        # "Settled" is deliberately wider than payment_status == PAID. An OR
        # number typed at the counter is the same proof as an uploaded
        # receipt, and the save below records it as PAID either way; a
        # fee-exempt applicant never owed anything to begin with.
        settled = bool(exempt or or_number
                       or registration.payment_status == VehicleRegistration.PaymentStatus.PAID)
        if not settled:
            return Response(
                {"error": "unpaid_registration_cannot_be_accepted",
                 "detail": f"{registration.full_name} has not settled the Vehicle Pass fee. "
                           f"Enter the Official Receipt number to approve this application, "
                           f"or mark the applicant fee-exempt if nothing is owed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # A plate flagged by a 3rd-offense fee violation requires additional
        # review before it can be registered again. CDSO must explicitly
        # acknowledge the flag (soft block) to proceed.
        from violations.models import Violation
        blocks = Violation.registration_block_for_plate(registration.plate_number)
        block_count = blocks.count()
        # A "soft" block: the first attempt is refused with the details, and the
        # page re-sends with acknowledge_block once the reviewer has read them.
        if block_count and not request.data.get('acknowledge_block'):
            latest = blocks.first()                  # newest first, per that method's ordering
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
        schedule_override    = request.data.get('schedule', '').strip()   # or a whole rotation instead
        special_case_reason  = request.data.get('special_case_reason', '').strip()   # required past the allowance

        # Early validation: up to 3 campus days is the normal allowance — granting
        # more than 3 makes it a special case that requires a reason.
        if campus_days_override is not None and isinstance(campus_days_override, list):
            _cleaned_check, _ = clean_campus_days(campus_days_override)   # drop anything that is not a campus day
            # Checked BEFORE the transaction, so the reviewer is told what is
            # missing without anything having been created.
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
        with transaction.atomic():                   # everything inside either all happens, or none of it does
            # Create user with a secure temporary password
            temp_password = _generate_temp_password()   # emailed once; the owner must change it at first login
            owner_type  = {                          # the registrant type becomes the account's owner type,
                'student':  User.OwnerType.STUDENT,  # which is what the gate rules are chosen by
                'employee': User.OwnerType.EMPLOYEE,
                'fetcher':  User.OwnerType.FETCHER,
            }.get(registration.registrant_type, User.OwnerType.EMPLOYEE)
            schedule    = registration.schedule or ('MWF' if registration.registrant_type == 'student' else 'ANY')   # a sensible default per type
            campus_days = registration.campus_days or []
            user = User.objects.create_user(
                email=registration.email,            # the email on the form becomes the portal login
                full_name=registration.full_name,
                password=temp_password,
                role='vehicle_owner',
                must_change_password=True,           # forces a change at first sign-in
                owner_type=owner_type,
                schedule=schedule,
                campus_days=campus_days,
                # DPO: contact and address are not collected,
                # so nothing is carried onto the account either.
            )

            # Record that CDSO knowingly accepted a flagged plate (audit trail)
            if block_count:
                try:
                    AuditLog.objects.create(
                        actor=request.user,          # the named officer who overrode the flag
                        action=AuditLog.Action.USER_CREATED,
                        target_user=user,
                        details=(
                            f"Registration accepted despite registration block | "
                            f"Plate: {registration.plate_number} | "
                            f"Prior flagged violations: {block_count} | Reviewed by: {request.user.full_name}"
                        ),
                    )
                except Exception:
                    pass                             # never fail the approval over its own audit note

            # Create or update Vehicle linked directly to User, keyed on the
            # registration's plate or conduction number (brand-new car).
            vehicle_obj = _upsert_vehicle_for_registration(registration, user)   # this is what authorises it at the gate

            # Auto-generate unique system ID
            _assign_system_id(registration)          # sets the field; the save() below writes it

            registration.or_number = or_number
            # No unpaid branch here any more: the settled check above refuses
            # the request outright, so anything reaching this point either paid
            # or was exempt.
            if not exempt and registration.payment_status != VehicleRegistration.PaymentStatus.PAID:
                # An OR number reached us without going through the applicant's
                # upload — a walk-in who brought the paper to the counter. Same
                # proof, so it is recorded the same way; only the receipt image
                # is missing.
                registration.payment_status = VehicleRegistration.PaymentStatus.PAID
                registration.amount_paid    = registration.pass_fee()   # snapshot today's fee, not a live lookup
                registration.paid_at        = timezone.now()
            registration.user = user        # direct FK to account
            registration.vehicle = vehicle_obj  # 1:1 link registration → vehicle

            # Apply campus_days / schedule overrides
            if campus_days_override is not None and isinstance(campus_days_override, list):
                cleaned, _ = clean_campus_days(campus_days_override)   # cleaned again: this is the value being stored

                # More than the allowance is a special case (validated above)
                if len(cleaned) > MAX_CAMPUS_DAYS:
                    registration.is_special_case     = True   # marks it as exceptional on every later screen
                    registration.special_case_reason = special_case_reason

                registration.campus_days = cleaned
                # Re-derive the schedule group from the new days, by the same
                # rule the public form uses (see vehicles/campus_days.py).
                registration.schedule = schedule_group(cleaned)
            elif schedule_override:
                registration.schedule = schedule_override   # a rotation was chosen instead of specific days
            registration.status = VehicleRegistration.Status.ACCEPTED   # the moment it becomes a pass
            registration.reviewed_at = timezone.now()
            registration.save()

            # Sync final campus_days / schedule onto the user account so entry_logic
            # can check actual days rather than a fixed MWF/TTHF group.
            # This is the copy the GATE reads, so the two must not drift apart.
            user.campus_days = registration.campus_days or []
            user.schedule    = registration.schedule or user.schedule
            user.save(update_fields=['campus_days', 'schedule'])

            # No refresh_from_db() here: User.save() assigns self.user_code
            # before writing it, so the in-memory instance already carries it.
            # Re-reading the row cost a round trip to fetch what we just set.
            # A bare "OR: " told a later reader nothing about why a pass was
            # issued without a receipt. Only two cases can reach this point now
            # that an unsettled fee is refused, and the note names which.
            if exempt:
                or_note = 'OR: n/a (fee exempt)'     # nothing was ever owed
            else:
                or_note = f'OR: {or_number}'         # paid, with the receipt number
            audit(request, AuditLog.Action.RECORD_UPDATED,
                  f"Registration accepted | Plate: {registration.plate_number} | "
                  f"Applicant: {registration.full_name} ({registration.registrant_type}) | "
                  f"{or_note} | By: {request.user.full_name}",
                  target_user=user)
        # ── Past this line the transaction has committed: the pass exists. ──
        system_id = registration.system_student_id if registration.registrant_type == 'student' else registration.system_employee_id   # the two-column fallback

        # Acceptance mail with the QR code and credentials, handed to a background
        # thread. The transaction above has already committed, so this was never
        # able to affect the outcome — it only made the reviewer wait on a mail
        # server. A failed send raises an admin notification instead of the
        # response field the CDSO page used to warn from.
        send_in_background(
            send_acceptance_email, registration, temp_password, user.user_code,   # the credentials the owner needs
            on_failure=_acceptance_email_failed_notice(registration),   # a bell notice if it never arrives
        )

        return Response({
            "message": "Registration accepted and user created.",
            "email_status": 'queued',                # "queued", not "sent": the send has not happened yet
            "account": {
                "user_code": user.user_code,
                "system_id": system_id,
                "email": registration.email,
                "full_name": registration.full_name,
                "registrant_type": registration.registrant_type,
                "plate_number": registration.plate_number,
                "vehicle_type": registration.vehicle_type,
                "vehicle_color": registration.vehicle_color,
                "program_year": registration.program_year,
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


# Turning an application down. Much simpler than approval: nothing is created,
# and rejecting also releases the plate, email and IDs for a fresh attempt,
# because the conflict checks only count pending and accepted rows.
class RejectRegistrationView(APIView):
    permission_classes = [IsAdminOrCdso]

    def post(self, request, pk):
        registration = get_object_or_404(VehicleRegistration, pk=pk)
        if registration.status != VehicleRegistration.Status.PENDING:
            return Response({"error": "Only pending registrations can be rejected."}, status=status.HTTP_400_BAD_REQUEST)

        reason = request.data.get('reason')
        # Required: the applicant is emailed this, and without it they have
        # nothing to correct before applying again.
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
        # Sent inline rather than in the background, so the reviewer is told
        # straight away whether the applicant actually heard about it.
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

# The counter version of the whole flow: someone walks in, and the CDSO fills
# the form in for them. It is submission and approval in one request, so it
# repeats the same checks as the public form AND the approval path, then ends
# with the same transaction: registration + account + vehicle together.
class CdsoDirectRegisterView(APIView):
    """
    CDSO registers a walk-in applicant directly.
    No pending step — user account + vehicle are created immediately.
    """
    permission_classes = [IsAdminOrCdso]

    def post(self, request):
        registrant_type = request.data.get('registrant_type', '')
        if registrant_type not in ('student', 'employee', 'fetcher'):   # visitors use a gate pass, not a registration
            return Response({"error": "Invalid registrant type."}, status=status.HTTP_400_BAD_REQUEST)

        data = dict(request.data)                    # a copy, because _normalize_department edits it in place

        # Resolved before the receipt check, not after: Cleaning and Services
        # staff pay nothing, so there is no Official Receipt to demand from them.
        # Asking anyway meant CDSO had to invent a number to register a walk-in
        # from that department.
        department_type = _normalize_department(data)
        exempt = VehicleRegistration.is_fee_exempt(registrant_type, department_type)

        or_number = request.data.get('or_number', '').strip()
        if exempt:
            or_number = ''                           # nothing to pay, so nothing to record
        else:
            # Unlike the online path, a walk-in cannot be approved unpaid: the
            # applicant is at the counter with the receipt in hand.
            if not or_number:
                return Response({"error": "Official Receipt (OR) number is required."}, status=status.HTTP_400_BAD_REQUEST)
            if not or_number.isdigit() or len(or_number) > 7:
                return Response({"error": "Official Receipt (OR) number must be at most 7 digits."}, status=status.HTTP_400_BAD_REQUEST)

        # E-bikes get a system-issued control number, never a typed identifier.
        ebike = is_ebike(request.data.get('vehicle_type'))
        if ebike:
            data['plate_number'] = ''                # cleared here; allocated inside the transaction below
            data['conduction_number'] = ''

        # 1:1 guard — plate, email and student/employee ID must not already have an active
        # registration (also blocks an email already tied to an existing account)
        conflict = _registration_conflict(
            registrant_type,
            '' if ebike else request.data.get('plate_number', ''),   # an e-bike has no plate to clash with yet
            request.data.get('email', ''),
            request.data.get('student_id', ''),
            request.data.get('employee_id', ''),
            drivers_license=request.data.get('drivers_license', ''),
        )
        if conflict:
            return Response({"error": conflict}, status=status.HTTP_400_BAD_REQUEST)

        driver_error = _validate_authorized_driver(registrant_type, request.data)   # the minor-driver rule
        if driver_error:
            return Response({"error": driver_error}, status=status.HTTP_400_BAD_REQUEST)

        license_error = _license_db_conflict(request.data.get('drivers_license', ''))   # belt-and-braces before saving
        if license_error:
            return Response({"error": license_error}, status=status.HTTP_400_BAD_REQUEST)

        # Walk-ins go through the same campus-day rules as the online form.
        # This path validated none of them: day names were stored unchecked, and
        # because `schedule` was only ever read back off the row, a caller who
        # supplied campus_days without a schedule got the blanket 'MWF' default
        # no matter which days those actually were.
        if registrant_type in ('employee', 'fetcher'):
            data['campus_days'] = []                 # staff and fetchers are not tied to specific days...
            data['schedule'] = 'ANY'                 # ...so any campus day is allowed
        else:
            # Students pick days. clean_campus_days hands back what it accepted
            # and what it refused, so an unrecognised day is named in the error
            # rather than silently dropped.
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
            data['schedule'] = schedule_group(campus_days)   # derive the rotation from the days, never trust a sent one

        serializer = VehicleRegistrationSerializer(data=data)   # field-level validation of the whole form
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)   # per-field messages for the form

        # Same all-or-nothing rule as AcceptRegistrationView: the registration,
        # the account and the vehicle are created together or not at all, so a
        # failure half-way cannot strand an account holding the walk-in's email.
        with transaction.atomic():
            # The control number is allocated inside the transaction, so a
            # failure later does not burn a number. It is stored IN plate_number
            # so the gate and QR code keep working unchanged.
            identity = {'plate_number': allocate_control_number()} if ebike else {}
            registration = serializer.save(
                **identity,
                registrant_type=registrant_type,
                source=VehicleRegistration.Source.DIRECT,   # marks it as a counter registration
                status=VehicleRegistration.Status.ACCEPTED, # approved on the spot: no pending step
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
            # From here the steps mirror AcceptRegistrationView exactly: account,
            # vehicle, system ID, then link them onto the registration row.
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
                # DPO: contact and address are not collected,
                # so nothing is carried onto the account either.
            )

            vehicle_obj = _upsert_vehicle_for_registration(registration, user)   # authorises it at the gate

            _assign_system_id(registration)
            registration.user = user
            registration.vehicle = vehicle_obj  # 1:1 link registration → vehicle
            registration.save()                 # one save for the system ID and both links
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
            # Fewer fields than the approval response: the counter screen prints
            # a slip from these, rather than filling in an account modal.
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
#
# Everything from here to the end of the payment view answers WITHOUT an
# account — it is the path a new applicant walks once a year:
#
#   1. Is registration open, until when, at what price?  RegistrationStatusView
#   2. If it is an e-bike, what number will I get?       EbikeControlNumberPreviewView
#   3. Is there still room on the schedule I want?       ScheduleSlotsView
#   4. Is my plate / email / licence already taken?      RegistrationAvailabilityView
#   5. Submit.                                           PublicOpenRegistrationView
#   6. Pay at Accounting, then file the OR number.       RegistrationPaymentView
#
# Steps 1-4 only read, so they can answer anyone who asks. Step 5 is the only
# one that writes, and it re-runs every check steps 1-4 already performed: the
# answers sitting in the browser are a courtesy to the applicant, never the
# authority on whether a registration may be filed.

# These four are left over from when the window was a fixed stretch of the
# calendar. Nothing reads them any more — the live window is a RegistrationPeriod
# row an admin sets, which _registration_window() below reads instead. Left in
# place: this pass comments the code, it does not remove any of it.
REGISTRATION_OPEN_MONTH  = 6   # June  (tentative — 2 months before school year)
REGISTRATION_OPEN_DAY    = 1
REGISTRATION_CLOSE_MONTH = 10  # October (tentative — end of first semester enrollment window)
REGISTRATION_CLOSE_DAY   = 31
# How many students may hold any one campus day. Read here and by the admin
# dashboard in accounts/views.py, so both draw the line in the same place.
SCHEDULE_SLOT_LIMIT      = 100  # per day


# The single answer to "may I register, until when, and what will it cost?".
# Built in one place so the status endpoint, the submit handler and the price
# on the form can never drift apart.
def _registration_window():
    settings_obj = SystemSettings.get()          # the one settings row; the fees are editable, not hardcoded
    fees = {
        # float() is for the wire only — JSON has no decimal type. Every amount
        # the server actually charges or stores stays a Decimal (see fee_for).
        "vehicle_pass_fee":          float(settings_obj.vehicle_pass_fee),           # what a student owes
        "vehicle_pass_fee_employee": float(settings_obj.vehicle_pass_fee_employee),  # what a non-exempt employee owes
        # Departments that pay nothing. Sent rather than hardcoded in the form so
        # the price shown to an applicant comes from the same place the backend
        # charges from — adding a department here updates both at once.
        "fee_exempt_departments": sorted(VehicleRegistration.FEE_EXEMPT_DEPARTMENTS),
        "department_options": [                      # fills the form's department dropdown
            {"value": value, "label": label}         # value is what gets stored, label is what the applicant reads
            for value, label in VehicleRegistration.DepartmentType.choices
        ],
    }
    period = RegistrationPeriod.get_active()         # the period an admin flagged active, or None
    if period:
        today = timezone.localdate()                 # campus-local date, so "today" means today here
        is_open = period.start_date <= today <= period.end_date   # inclusive at both ends: the closing day still counts
        return {
            "is_open": is_open,
            "open_date":  period.start_date.isoformat(),   # ISO strings — the form displays these, it does no date maths
            "close_date": period.end_date.isoformat(),
            "slot_limit": SCHEDULE_SLOT_LIMIT,
            **fees,
        }
    # No active period on record: closed, rather than open by default. With no
    # dates to register against there is nothing to join. The fees still go out
    # so the form can show what a pass costs even while registration is shut.
    return {
        "is_open":    False,
        "open_date":  None,
        "close_date": None,
        "slot_limit": SCHEDULE_SLOT_LIMIT,
        **fees,
    }


# The form's first call: open or closed, until when, and at what price.
class RegistrationStatusView(APIView):
    permission_classes = [permissions.AllowAny]   # a would-be applicant has no account yet

    def get(self, request):
        return Response(_registration_window())   # read-only, and the same dict the submit handler checks against


class EbikeControlNumberPreviewView(APIView):
    """The control number the registration form shows once E-Bike is picked.

    A preview: the number is issued at submission, and another applicant may
    take this one first — the submit response carries the number actually given.
    """
    permission_classes = [permissions.AllowAny]
    # Public page: a stale Bearer token left in the browser must not 401 it.
    authentication_classes = []

    def get(self, request):
        # The highest number issued so far, plus one. Nothing is locked and
        # nothing is reserved: holding a number for anyone who merely opened the
        # form would burn the sequence every time somebody browsed and left.
        return Response({'control_number': peek_next_control_number()})


# ALL_DAYS now comes from .campus_days — the same list the validators use, so
# the slot grid and the accepted day names cannot drift apart.


# How full each campus day is. Feeds the applicant's schedule picker and the
# CDSO's day picker from one set of counts, so the two cannot disagree.
class ScheduleSlotsView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        # A slot is taken while an application is alive, so PENDING counts too —
        # otherwise a day could be handed out twice over while a queue of
        # submissions sat waiting for review.
        active = [VehicleRegistration.Status.PENDING, VehicleRegistration.Status.ACCEPTED]
        base = VehicleRegistration.objects.filter(status__in=active, registrant_type='student')   # only students hold named days; employees and fetchers are 'ANY'
        limit = SCHEDULE_SLOT_LIMIT

        # One query with a FILTER per day, not one .count() per day.
        #
        # The loop this replaces issued six separate COUNT(*) statements. On
        # Railway, sitting beside the database, that was invisible. From the
        # campus half each one is a ~45ms round-trip to Neon, so a 528-byte
        # response cost ~300ms of waiting - and the count grows with ALL_DAYS.
        # Same filters, same numbers, one trip.
        #
        # Day names are plain alphabetic (Monday..Saturday), so they are safe to
        # use directly as aggregate aliases; the prefix only keeps them clear of
        # any model field name.
        counts = base.aggregate(**{
            f'day_{day}': Count('pk', filter=Q(campus_days__contains=[day]))
            for day in ALL_DAYS
        })

        result = {}
        for day in ALL_DAYS:
            used = counts[f'day_{day}']              # live registrations listing this day
            result[day] = {
                "used": used,
                "limit": limit,
                "available": max(0, limit - used),   # clamped: a day pushed past capacity by CDSO overrides reads as 0 left, never negative
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
                "label": SCHEDULE_DAY_LABELS[code],                      # "Mon · Wed · Fri" — what the applicant actually reads
                "used": max(result[d]['used'] for d in days),            # the busiest day speaks for the whole rotation
                "limit": limit,
                "available": min(result[d]['available'] for d in days),  # and the tightest day decides what is left to give
            }
            for code, days in SCHEDULE_GROUP_DAYS.items()
        }
        return Response(result)   # both shapes in one reply: the per-day grid and the rotation summary


# The "is this already taken?" endpoint the form calls as the applicant types.
# It is advisory only: it tells somebody early, in the box they are standing in,
# what PublicOpenRegistrationView would otherwise have told them after they
# filled in the whole form and pressed submit.
class RegistrationAvailabilityView(APIView):
    """Live duplicate check used to warn the user in the registration form's text boxes
    before they submit, e.g. 'This plate number already has an active registration.'"""
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        # Every field is optional. The form calls this as each box is filled in,
        # so a half-typed form asks only about what it has; a missing parameter
        # defaults to '' and the matching helper simply reports it as free.
        plate_number    = request.query_params.get('plate_number', '')
        email           = request.query_params.get('email', '')
        drivers_license = request.query_params.get('drivers_license', '')
        student_id      = request.query_params.get('student_id', '')
        employee_id     = request.query_params.get('employee_id', '')
        conduction      = request.query_params.get('conduction_number', '')

        # Only a LIVE application blocks a new one. A rejected or expired row is
        # history and must not keep a plate or an email hostage for good.
        statuses = [VehicleRegistration.Status.PENDING, VehicleRegistration.Status.ACCEPTED]
        qs = VehicleRegistration.objects.filter(status__in=statuses)

        # Each helper returns a ready-to-show sentence, or None when the value is
        # free — so the hint under the field can name the conflict instead of
        # just turning red. The same helpers run again at submit time; these are
        # the identical functions, not a second copy of the rules.
        return Response({
            'plate_number':      _plate_conflict(plate_number, qs),
            'conduction_number': _conduction_conflict(conduction, qs),
            'email':             _email_conflict(email, qs),
            'drivers_license':   _license_conflict(drivers_license, qs),
            # One helper answers both, told which to check by its first argument
            # and handed '' for the other. Still answered even though the form no
            # longer asks for these two — the submit handler drops them outright
            # (see the Data Privacy Office block in PublicOpenRegistrationView).
            'student_id':        _id_conflict('student', student_id, '', qs),
            'employee_id':       _id_conflict('employee', '', employee_id, qs),
            # The 3rd-offence block, checked against every identifier at once:
            # a banned person changing one field does not get past it.
            'banned':            _registration_ban(plate_number, email, student_id, employee_id,
                                                   conduction_number=conduction),
        })


# The public form's submit handler — the one way a registration is filed
# without a CDSO officer sitting at a desk.
#
# It is a long gauntlet of checks and then one short save. The order is
# deliberate: the cheapest and most final questions come first, so somebody who
# cannot register at all is told so before anything else is worked out.
#
#    1. Is registration open?                     (a closed window ends it here)
#    2. Is the registrant type one we accept?
#    3. Plate, or conduction number, or neither   (an e-bike is issued a number)
#    4. Is this person banned from registering?   (the 3rd-offence ladder)
#    5. Does any identifier already hold a live registration?
#    6. Strip what must not be stored             (form-only fields, and the
#                                                  columns the DPO withdrew)
#    7. Authorised driver, and the licence number
#    8. Fetcher: classification, and the students they collect
#    9. Student: resolve the rotation, then check that rotation's capacity
#   10. ── transaction ── issue the e-bike control number and INSERT the row
#   11. After it commits: the acknowledgement email, in the background
#
# Every step up to 10 returns a 400/403 carrying a sentence written to be shown
# to the applicant word for word. Nothing is written until the transaction, so
# a rejection at any point leaves no trace behind.
class PublicOpenRegistrationView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        # 1. The window, re-read from the database rather than trusted from the
        # form. A browser left open across the closing date would otherwise
        # still submit against a window that shut days ago.
        window = _registration_window()
        if not window["is_open"]:
            return Response(
                {"error": "Vehicle Pass registration is currently closed.", "period_closed": True},
                status=status.HTTP_403_FORBIDDEN,
            )

        # 2. Three kinds of applicant, and nothing else. The type decides which
        # rules apply further down (fees, campus days, the fetcher questions),
        # so an unrecognised one cannot be allowed through to be handled as a
        # default.
        registrant_type = request.data.get('registrant_type', '')
        if registrant_type not in ['student', 'employee', 'fetcher']:
            return Response({"error": "Invalid registrant type."}, status=status.HTTP_400_BAD_REQUEST)

        # An e-bike has neither a plate nor a conduction sticker: the system
        # issues it a control number (FM-001, ...) at save time, and whatever
        # identifier the payload carries is ignored rather than trusted.
        # 3. What identifies the vehicle.
        ebike = is_ebike(request.data.get('vehicle_type'))
        if ebike:
            plate_in = conduction_in = ''            # both blanked: whatever the payload sent is discarded, not trusted
        else:
            # A brand-new car registers with a conduction number instead of a plate.
            # Exactly one of the two must be provided — never both, never neither.
            plate_in      = (request.data.get('plate_number') or '').strip()   # `or ''` so a JSON null is handled like a missing field
            conduction_in = (request.data.get('conduction_number') or '').strip()
            if plate_in and conduction_in:           # both given: we would not know which one the gate should match on
                return Response(
                    {"error": "Enter either a plate number or a conduction number, not both."},
                    status=status.HTTP_400_BAD_REQUEST)
            if not plate_in and not conduction_in:
                return Response(
                    {"error": "A plate number is required (or a conduction number for a brand-new vehicle)."},
                    status=status.HTTP_400_BAD_REQUEST)

        # Hard block: applicants who reached the maximum number of violations and
        # were archived on expiry may never register again.
        # 4. Checked before anything else about the application, because no
        # amount of valid detail can change the answer. Every identifier goes in
        # together so swapping one of them does not slip past the block.
        ban = _registration_ban(
            plate_in,
            request.data.get('email', ''),
            request.data.get('student_id', ''),
            request.data.get('employee_id', ''),
            conduction_number=conduction_in,
        )
        if ban:
            # 403, and a flag the form reads to show the "speak to CDSO" notice
            # rather than a field-level error the applicant could try to correct.
            return Response({"error": ban, "registration_banned": True}, status=status.HTTP_403_FORBIDDEN)

        # 1:1 guard — plate/conduction, email and student/employee ID must not
        # already have an active registration
        # 5. The same checks RegistrationAvailabilityView answered as they
        # typed, run again here — that endpoint informs the applicant, this one
        # decides. The browser's copy may be stale by seconds or hours, and a
        # direct POST never asked it at all.
        conflict = _registration_conflict(
            registrant_type,
            plate_in,
            request.data.get('email', ''),
            request.data.get('student_id', ''),
            request.data.get('employee_id', ''),
            drivers_license=request.data.get('drivers_license', ''),
            conduction_number=conduction_in,
        )
        if conflict:
            return Response({"error": conflict}, status=status.HTTP_400_BAD_REQUEST)   # the helper's own sentence, shown as-is

        # 6. From here on the work is done on `data`, a plain mutable copy —
        # request.data itself is not written to, so what arrived stays readable
        # if anything below needs to look at it again.
        data = dict(request.data)
        data['plate_number'] = plate_in              # the cleaned values win over whatever was posted
        data['conduction_number'] = conduction_in
        department_type = _normalize_department(data)   # label -> stored value; also what the fee exemption is decided on

        # Strip fields that are not model columns (e.g. form-only UI fields)
        # These are real form fields, just not columns: the name parts are
        # joined into full_name and the address parts into one line by the
        # serializer, and privacy_consent is a tick box the form enforces.
        # Passing them to the serializer would be an unknown-field error.
        for extra in ('last_name', 'first_name', 'middle_name',
                      'house_street', 'barangay', 'city_municipality', 'province',
                      'student_strand', 'student_grade',
                      'student_program', 'student_year',
                      'privacy_consent'):
            data.pop(extra, None)

        # Data Privacy Office. These columns still exist (the
        # schema is shared with the other branches and must not move), but the
        # form no longer asks for them and nothing may write them from here: a
        # stale bundle or a hand-rolled POST would otherwise still file the very
        # data the DPO asked us to stop collecting.
        for withheld in ('address', 'contact_number', 'age',
                         'student_id', 'employee_id', 'driver_contact'):
            data.pop(withheld, None)

        # 7. Who may drive the vehicle. For a student it may be somebody else
        # entirely (a parent, a driver), and that person's details are what the
        # guard checks at the gate, so they are validated as a set.
        driver_error = _validate_authorized_driver(registrant_type, data)
        if driver_error:
            return Response({"error": driver_error}, status=status.HTTP_400_BAD_REQUEST)

        # One licence, one active registration. Checked here as well as in the
        # partial unique index behind it: the index would raise an IntegrityError
        # the applicant cannot read, this returns a sentence they can act on.
        license_error = _license_db_conflict(data.get('drivers_license', ''))
        if license_error:
            return Response({"error": license_error}, status=status.HTTP_400_BAD_REQUEST)

        # 8. Fetchers only: somebody who drives a student in and out. What
        # they may do on campus depends on the classification, and who they are
        # fetching has to be on record, so both are required here rather than
        # left to CDSO to chase up later.
        if registrant_type == 'fetcher':
            # Classification is required: drop_and_go (allotted times only) or
            # standby (allowed to park inside campus while waiting).
            fetcher_type = (data.get('fetcher_type') or '').strip()
            if fetcher_type not in ('drop_and_go', 'standby'):   # no default: the two grant different access
                return Response(
                    {"error": "Please choose a fetcher classification: Fetcher/Drop & Go or Standby."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            # At least one student must be listed
            students = data.get('fetcher_students') or []
            # isinstance as well as the length: this arrives as JSON from an
            # anonymous caller, so a string or a dict here is entirely possible
            # and would otherwise be iterated character by character below.
            if not isinstance(students, list) or len(students) == 0:
                return Response(
                    {"error": "At least one student must be listed on a fetcher registration."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            valid_levels = {c[0] for c in VehicleRegistration.StudentLevel.choices}   # the stored values, taken from the model so the two cannot drift
            cleaned_students = []                    # built up entry by entry; only this list is stored
            for s in students:
                if not isinstance(s, dict):          # same reasoning: anything at all can arrive inside the list
                    return Response({"error": "Invalid student entry."}, status=status.HTTP_400_BAD_REQUEST)
                # DPO: the fetched student's ID number is no
                # longer collected — they are identified by name and level, the
                # same way the applicant themselves now is.
                # Rebuilt field by field rather than stored as sent: this lands
                # in a JSONField, which would keep any extra keys a caller chose
                # to include. Three keys go in, and only these three.
                entry = {
                    'full_name':     (s.get('full_name') or '').strip(),
                    'student_level': (s.get('student_level') or '').strip(),
                    'program_year':  (s.get('program_year') or '').strip(),   # optional: a name and a level are enough
                }
                if not entry['full_name'] or not entry['student_level']:
                    return Response(
                        {"error": "Each student needs a full name and education level."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if entry['student_level'] not in valid_levels:
                    return Response({"error": "Invalid education level for a listed student."}, status=status.HTTP_400_BAD_REQUEST)
                cleaned_students.append(entry)
            data['fetcher_students'] = cleaned_students   # the cleaned list replaces what was posted
        else:
            # Not a fetcher, so these two must not be carried along. A payload
            # that included them anyway would otherwise file a student or
            # employee with a fetcher classification the gate would act on.
            data.pop('fetcher_type', None)
            data.pop('fetcher_students', None)

        # 9. Campus days. Employees and fetchers come in whenever they are
        # needed, so they hold no particular day and take up no slot: 'ANY' with
        # an empty day list is what entry_logic reads as "no day restriction".
        # Students are the only ones the schedule capacity applies to.
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
            # Returns the whole rotation and its code, or a ready-to-show
            # error. student_level is passed because SpEd students attend every
            # campus day and are the one exception to the 3-day allowance.
            campus_days, schedule_code, day_error = resolve_student_schedule(
                data.get('schedule'), data.get('campus_days', []), data.get('student_level'))
            if day_error:
                return Response({"error": day_error}, status=status.HTTP_400_BAD_REQUEST)

            # Capacity is checked against the resolved days, not the submitted
            # ones — a rotation takes a slot on each of its days, including the
            # ones the applicant never explicitly picked.
            active = [VehicleRegistration.Status.PENDING, VehicleRegistration.Status.ACCEPTED]
            base = VehicleRegistration.objects.filter(status__in=active, registrant_type='student')

            # One query with a FILTER per day, for the same reason as
            # ScheduleSlotsView above: a .count() per day is a round-trip per
            # day, and this runs on the path a student waits on when they press
            # submit. full_days is still built by walking campus_days, so the
            # order of the names in the error message is unchanged.
            day_counts = base.aggregate(**{
                f'day_{day}': Count('pk', filter=Q(campus_days__contains=[day]))
                for day in campus_days
            })
            full_days = [day for day in campus_days
                         if day_counts[f'day_{day}'] >= SCHEDULE_SLOT_LIMIT]   # >= not ==: a day over capacity through a CDSO override is still full
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

            data['campus_days'] = campus_days        # the resolved rotation, never the raw list that was posted
            data['schedule'] = schedule_code         # kept in step with it, so the two can never describe different weeks

        # 10. Field-level validation — types, lengths, required columns — is
        # the serializer's job; everything above it is the campus rules, which
        # it has no way to know about. Only now is anything about to be written.
        serializer = VehicleRegistrationSerializer(data=data)
        if serializer.is_valid():
            # Cleaning and Services staff owe nothing, so they never pass through
            # the Accounting Office and have no receipt to upload. Resolved from
            # the request rather than read back off the saved row: it costs no
            # query, and it rides along in the INSERT instead of a second UPDATE.
            exempt = VehicleRegistration.is_fee_exempt(registrant_type, department_type)
            # The control number is allocated in the same transaction as the
            # INSERT — see allocate_control_number for why that matters.
            with transaction.atomic():
                # The control number goes in the plate_number column: the gate,
                # the QR and every lookup already work off that field, so an
                # e-bike needs no separate path through any of them.
                identity = {'plate_number': allocate_control_number()} if ebike else {}
                registration = serializer.save(
                    registrant_type=registrant_type,
                    source=VehicleRegistration.Source.PUBLIC,
                    # Set here, not taken from the payload: what somebody owes
                    # is the school's decision, not a field they can submit.
                    payment_status=(VehicleRegistration.PaymentStatus.EXEMPT if exempt
                                    else VehicleRegistration.PaymentStatus.UNPAID),
                    # 0.00 for the exempt, None for everyone else — None means
                    # "not yet paid", which is not the same as having paid zero.
                    amount_paid=(Decimal('0.00') if exempt else None),
                    **identity,                      # the control number, or nothing at all
                )
            # ── Past this line the row exists: the application is filed. ──
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
                on_failure=_pending_email_failed_notice(registration),   # raises an admin notification if the send fails
            )
            return Response(
                {"message": "Registration submitted successfully. Please wait for CDSO review.",
                 "id": registration.id,
                 # The number actually issued, which may not be the one the
                 # preview endpoint showed — this is the authoritative answer.
                 "control_number": registration.plate_number if ebike else None,
                 "email_status": 'queued'},          # queued, not sent: the mail is still in flight when this returns
                status=status.HTTP_201_CREATED,
            )
        # The serializer's own field errors, keyed by field name so the form can
        # put each message under the box it belongs to.
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class UploadRegistrationDocumentsView(APIView):
    """Closed on the Data Privacy Office's instruction.

    This used to attach the applicant's supporting documents (driver's licence
    photo, assessment form, and one assessment form per student a fetcher
    collects) to a just-submitted registration. The DPO's instruction was that a
    copy of the licence need not be collected, and the rest of the attachments
    were withdrawn with it, so nothing is uploaded from the public form any more.

    The route is kept and answers plainly rather than being removed: a browser
    still running the previous bundle would otherwise get a 404 it reads as a
    network fault and retry, and the retry would be an upload we must not accept.
    The reply is 410 Gone — the endpoint existed, and is deliberately closed.
    """
    permission_classes = [permissions.AllowAny]
    parser_classes = [MultiPartParser, FormParser]

    CLOSED_MESSAGE = (
        "Supporting documents are no longer collected online. Nothing further is "
        "needed from you — bring your driver's licence to the CDSO Office when you "
        "collect your vehicle pass."
    )

    def post(self, request):
        # Whatever was sent is not read and not saved. The flag lets an older
        # bundle show the message as information rather than as a failure the
        # applicant should try again.
        return Response({"error": self.CLOSED_MESSAGE, "uploads_disabled": True},
                        status=status.HTTP_410_GONE)


# The old name, kept so the previously built frontend bundle's
# /register/license-image/ calls keep resolving to the same handler.
UploadLicenseImageView = UploadRegistrationDocumentsView


# ──────────────────────────────────────────────
# Public receipt upload (applicant-driven proof of payment)
# ──────────────────────────────────────────────

# Turns the token out of the applicant's email into the registration it stands
# for — the one gate every request on this path goes through.
def _payment_registration(token):
    """Resolve a receipt-upload token to a still-reviewable registration.

    Only PENDING rows are reachable: once CDSO has accepted or rejected the
    application the receipt on file is part of the decision, and letting the
    link keep overwriting it would rewrite the evidence after the fact.
    """
    if not token:
        return None                                  # no token at all: nothing to look up
    try:
        # Both conditions in the one query, so a token for an already-reviewed
        # application is indistinguishable from a token that never existed —
        # the caller cannot tell the two apart from the outside.
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
    email to file the Official Receipt number themselves, instead of a reviewer
    re-keying it at a counter.

    DPO: the receipt photo that used to accompany the number is
    no longer collected. CDSO checks the paper receipt the applicant brings
    against the number on file, rather than an image on the review screen. A file
    sent by a browser still running the previous bundle is ignored, not stored.

    Authorised by the unguessable payment_token alone. The (id, email) pair the
    document upload uses is not a secret any more: school addresses are now
    <8-digit ID>@slc-sflu.edu.ph and registration ids are sequential.
    """
    permission_classes = [permissions.AllowAny]
    # JSON is what the form sends now; the multipart parsers stay so an older
    # bundle's upload is parsed and discarded rather than 415'd.
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get(self, request):
        """Everything the upload page needs to render, and nothing more.

        Deliberately not the full registration: this endpoint is reachable by
        anyone holding the link, so it returns what the applicant already knows
        about their own application, not the record CDSO sees.
        """
        registration = _payment_registration(request.query_params.get('token'))
        if registration is None:
            # One message for every failure — wrong token, already reviewed,
            # nonexistent. Saying which would let somebody holding a guessed
            # token learn whether it named a real application.
            return Response(
                {"error": "This payment link is no longer valid. It may have expired, "
                          "or your application may already have been reviewed."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response({
            "full_name":       registration.full_name,       # so the applicant can see they opened the right link
            # Whichever identifies this vehicle: a brand-new car has only the
            # conduction number, and an e-bike's control number lives in
            # plate_number, so this one field covers all three cases.
            "plate_number":    registration.plate_number or registration.conduction_number,
            "registrant_type": registration.registrant_type,
            "amount_due":      str(registration.pass_fee()),  # str(), not float(): the amount is shown, never recomputed here
            "payment_status":  registration.payment_status,   # drives what the page offers: pay, already paid, or exempt
            "or_number":       registration.or_number,        # already filed, if they are coming back to the link
            "has_receipt":     bool(registration.or_receipt_image),   # bool, not the file: the image itself is not exposed on a public link
        })

    def post(self, request):
        # Resolved again from scratch. The GET that rendered the page proves
        # nothing about this request — each one stands on its own token.
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

        or_number = (request.data.get('or_number') or '').strip()   # what the Accounting Office printed on their receipt

        # Same shape the accept flow has always enforced, applied at the point
        # the number is actually typed instead of days later at the counter.
        if not or_number:
            return Response({"error": "Official Receipt (OR) number is required."},
                            status=status.HTTP_400_BAD_REQUEST)
        # isdigit() AND a length cap: the OR number is a printed numeral, so a
        # typed-in letter is a mistake worth catching while the receipt is still
        # in the applicant's hand rather than at the CDSO counter days later.
        if not or_number.isdigit() or len(or_number) > 7:
            return Response({"error": "Official Receipt (OR) number must be at most 7 digits."},
                            status=status.HTTP_400_BAD_REQUEST)

        # Four fields together record the payment; written in one save so a
        # row can never be left half-paid.
        registration.or_number        = or_number
        # Snapshot of what was owed at the moment of payment — see the field.
        registration.amount_paid      = registration.pass_fee()
        registration.paid_at          = timezone.now()   # when it was filed, which is not necessarily when they paid the cashier
        registration.payment_status   = VehicleRegistration.PaymentStatus.PAID   # what moves it into the CDSO review queue
        # update_fields, so this cannot overwrite anything a reviewer changed on
        # the row while the applicant had the page open.
        registration.save(update_fields=[
            'or_number', 'amount_paid', 'paid_at', 'payment_status',
        ])

        # The receipt number is what completes the registration form, so this is
        # the mail that carries it: the PDF the CDSO files. Backgrounded like the
        # approval mail — a dead SMTP host must not fail a payment that is
        # already recorded.
        send_in_background(send_receipt_received_email, registration)

        return Response(
            {"message": "Receipt number received. Your application is now queued for CDSO review.",
             "payment_status": registration.payment_status},   # echoed back so the page can re-render from the answer, not from what it assumed
            status=status.HTTP_200_OK,
        )


# ──────────────────────────────────────────────
# Correcting a registration's details
# ──────────────────────────────────────────────
#
# Two paths, split on whether CDSO has already approved the registration. The
# whitelist and every validation rule are shared — see
# vehicles/registration_edits.py, which explains why — so the only thing that
# differs is when the change lands:
#
#   PENDING   the applicant edits the row directly. Nobody has reviewed it, so
#             a detail they mistyped is still theirs to fix. Reached with the
#             token from their acknowledgement email, the same handle the
#             receipt step already uses.
#
#   ACCEPTED  the owner files a RegistrationChangeRequest and CDSO approves it.
#             The row issued a pass, a gate QR, a Vehicle and an account by this
#             point, and a guard matches a car against it — so it is not the
#             owner's to rewrite unilaterally.


# Finds the one approved registration an owner's dashboard is about.
def _accepted_registration_for(user):
    """The accepted registration behind a vehicle-owner account, or None.

    Matched on the FK *or* the email, and newest-reviewed first — the same query
    `accounts.views.MyRegistrationView` runs, because it has to resolve to the
    same row the owner is looking at on their dashboard. (The email arm is what
    covers rows filed before the account existed: the FK is only set at
    approval.)
    """
    return (VehicleRegistration.objects
            .filter(Q(user=user) | Q(email=user.email),          # the FK where it is set, otherwise the address the row was filed under
                    status=VehicleRegistration.Status.ACCEPTED)  # only an approved row has a pass worth correcting
            .order_by('-reviewed_at')                            # most recently approved first, so a renewal supersedes last year's
            .first())                                            # None when nothing is approved — every caller checks for it


# Carries an approved change out to the records the registration created.
def _mirror_registration_change(registration, changed_fields):
    """Carry an approved change out to the records built from the registration.

    The registration row is the application; the pass the change is really about
    lives in other places, and leaving any of them behind is how an owner ends
    up with a corrected dashboard and a gate that still refuses them:

      * `Vehicle` — what the plate reader and the guard's lookup resolve.
      * `User.full_name` — what the portal and every notice address them as.
      * nothing for the QR: it is `VEHICLE:{plate}|ID:{id}`, rebuilt live from
        the registration wherever it is displayed. The copy attached to the
        original approval email is the one exception, and the decision email
        says to use the portal's instead.

    A plate move adopts the new plate's Vehicle row rather than renaming the old
    one in place. Renaming would trip `uniq_vehicle_plate_number` against an
    unowned row for that plate (a visitor pass leaves one behind), and deleting
    that row to make space would take its visitor passes with it — they
    `CASCADE`. So the new row is adopted the way an approval adopts one, and the
    old row is released rather than deleted: it keeps whatever history hangs off
    it, and stops admitting the car.
    """
    touched = set(changed_fields)                # a set, so the membership tests below read directly

    # The account, if the approval created one. user_id rather than .user: this
    # asks whether there IS an account without fetching it to find out.
    if 'full_name' in touched and registration.user_id:
        user = registration.user
        user.full_name = registration.full_name
        user.save(update_fields=['full_name'])   # the name only; nothing else on the account is this function's business

    # The four fields that describe the car. Anything else on the whitelist —
    # a licence number, a program — lives only on the registration row, so
    # there is nothing further to carry out.
    vehicle_fields = {'plate_number', 'conduction_number', 'vehicle_type', 'vehicle_color'}
    if not (touched & vehicle_fields):
        return                                   # nothing about the vehicle moved

    old_vehicle = registration.vehicle
    # The distinction the rest of this function turns on: did it become a
    # DIFFERENT car, or the same car described better?
    identity_moved = bool(touched & {'plate_number', 'conduction_number'})

    if not identity_moved and old_vehicle is not None:
        # Same car, different description — no row to adopt.
        old_vehicle.vehicle_type = _vehicle_type_for(registration.vehicle_type)   # the form's wording, mapped to the model's own choice value
        old_vehicle.color = registration.vehicle_color
        old_vehicle.save(update_fields=['vehicle_type', 'color'])
        return

    # The identity moved (or there was no Vehicle at all). Adopt the row for
    # the new plate through the very helper an approval uses, so an edited
    # registration and a freshly approved one produce the same Vehicle.
    new_vehicle = _upsert_vehicle_for_registration(registration, registration.user)
    if old_vehicle is not None and old_vehicle.pk != new_vehicle.pk:
        # Released, not deleted — see the docstring. Unowned and unauthorized
        # is what stops the old plate admitting the car; the row itself stays,
        # and so does every visitor pass and scan hanging off it.
        old_vehicle.user = None
        old_vehicle.is_authorized = False
        old_vehicle.save(update_fields=['user', 'is_authorized'])
    registration.vehicle = new_vehicle           # the registration now points at the car it actually describes
    registration.save(update_fields=['vehicle'])


# Path one of the two: the applicant fixes their own PENDING application.
class RegistrationSelfEditView(APIView):
    """The applicant's own correction step, while the application is pending.

    Same authorisation as the receipt step — the unguessable `payment_token`
    from their acknowledgement email, and only ever a PENDING row (see
    `_payment_registration`, which is where "already reviewed" stops being
    editable). Once CDSO has decided, a correction is a change request against
    the accepted registration instead, and that one needs approval.

    Payment does not close editing: paying the fee is not the review, and the
    fee does not depend on any field on this whitelist. The one thing an edit
    re-sends is the acknowledgement PDF, which is rebuilt from the row — the
    copy the applicant is holding would otherwise still show the typo.
    """
    permission_classes = [permissions.AllowAny]   # authorised by the emailed token, not by an account — there is no account yet
    parser_classes = [JSONParser, MultiPartParser, FormParser]   # JSON today; the multipart parsers keep an older bundle's form post readable

    EXPIRED_MESSAGE = (
        "This link is no longer valid. It may have expired, or your application "
        "may already have been reviewed — the CDSO Office can still correct your "
        "details for you."
    )

    def get(self, request):
        # Everything the edit form needs to draw itself: which boxes it may
        # offer, what goes in them, and what it must show without offering.
        registration = _payment_registration(request.query_params.get('token'))   # the same PENDING-only lookup the receipt step uses
        if registration is None:
            return Response({"error": self.EXPIRED_MESSAGE},
                            status=status.HTTP_404_NOT_FOUND)
        return Response({
            "registrant_type": registration.registrant_type,   # the form asks different questions of a student, an employee and a fetcher
            "student_level":   registration.student_level,
            "reference":       "REG-%s" % str(registration.pk).zfill(6),   # a handle they can quote at the CDSO desk; the token itself is never shown
            # Worked out per registration rather than fixed: an e-bike's issued
            # control number, for one, is not among the fields it offers.
            "editable":        [{"field": f.name, "label": f.label}
                                for f in editable_for(registration)],
            "values":          current_values(registration),   # what to prefill each box with
            # Shown read-only beside the form: they are what the applicant uses
            # to recognise their own application, and being unable to edit the
            # email is easier to accept when you can see which one it is.
            "locked": {
                "email":           registration.email,
                "registrant_type": registration.get_registrant_type_display(),
            },
        })

    def post(self, request):
        # Resolved from the token again. The GET that drew the form proves
        # nothing about this request, and the row may have been reviewed since.
        registration = _payment_registration(request.data.get('token'))
        if registration is None:
            return Response({"error": self.EXPIRED_MESSAGE},
                            status=status.HTTP_404_NOT_FOUND)

        raw = {k: v for k, v in request.data.items() if k != 'token'}   # everything but the token is a candidate change
        # The whitelist, the per-field cleaners and the duplicate checks all
        # live in registration_edits, so this path and the CDSO-approved one
        # below enforce one set of rules rather than two copies of it.
        changes, errors = clean_changes(registration, raw)
        if errors:
            return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)   # keyed by field, so each message lands under its own box
        if not changes:
            # Submitted without altering anything. A 200, not an error: nothing
            # went wrong, there is simply nothing to do.
            return Response({"message": "Nothing was changed.", "changed": []},
                            status=status.HTTP_200_OK)

        summary = describe(registration, changes)   # the old-to-new rows, built BEFORE the write while the old values still exist
        with transaction.atomic():
            apply_changes(registration, changes)    # writes the fields onto the object and deliberately does not save
            registration.save()                     # the one save, inside the transaction that owns it

        # Not audited against a user: the applicant has no account yet, and an
        # AuditLog row needs an actor to mean anything. The notification below
        # is what tells CDSO the row moved under them, which is what a reviewer
        # with the application already open actually needs to know.
        _notify_registration_edited(registration, summary)

        # Backgrounded, like every other mail on this path: a dead SMTP host
        # must not fail a correction that is already saved.
        send_in_background(send_registration_updated_email, registration, summary)

        return Response({
            "message": "Your details have been updated. A new acknowledgement has "
                       "been emailed to you.",
            "changed": summary,                    # so the page can confirm exactly what it understood them to change
            "values":  current_values(registration),   # re-read after the write, so the form redraws from the row rather than from its own optimism
        }, status=status.HTTP_200_OK)


# Path two: the owner of an ACCEPTED registration asks CDSO for a change.
class OwnerChangeRequestView(APIView):
    """The owner's side of an approval-gated correction.

    GET returns their own request history, newest first, so the dashboard can
    say "waiting on CDSO" rather than appearing to have forgotten the edit.
    POST files a new one.

    One open request at a time, enforced here with a readable 409 and in the
    database by `uniq_pending_change_request_per_registration`. Stacking them
    would mean approving in any order leaves the row holding whichever was
    decided last rather than what the reviewer read.
    """
    permission_classes = [permissions.IsAuthenticated]

    # The two questions both methods have to ask before doing anything, asked
    # once. Returns (registration, None) when the caller may proceed, and
    # (None, response) when it may not — so each method is two lines of guard.
    def _guard(self, request):
        # An admin or a guard has their own screens for this; only the person
        # the registration belongs to may file a request against it.
        if request.user.role != 'vehicle_owner':
            return None, Response(
                {"error": "Only vehicle owners can change their registration details."},
                status=status.HTTP_403_FORBIDDEN)
        # Never taken from the request: the registration is the one this
        # account owns, so there is no id an owner could point at someone else's.
        registration = _accepted_registration_for(request.user)
        if registration is None:
            # An account with nothing approved — a pending applicant who somehow
            # has a login, or an owner whose registration expired.
            return None, Response(
                {"error": "No approved registration found for this account."},
                status=status.HTTP_404_NOT_FOUND)
        return registration, None

    def get(self, request):
        registration, error = self._guard(request)
        if error:
            return error
        requests = (RegistrationChangeRequest.objects
                    .filter(registration=registration)
                    .select_related('reviewed_by')[:20])   # the reviewer's name is rendered on every row, so fetch it in the same query; the model orders newest-first, and 20 is all a dashboard shows
        return Response({
            "editable": [{"field": f.name, "label": f.label}   # the same whitelist the pending path offers, worked out for this registration
                         for f in editable_for(registration)],
            "values":   current_values(registration),
            "locked":   dict(READ_ONLY_REASONS),   # field -> why it cannot be changed, so the form can say so instead of just greying the box out
            "requests": [_serialize_change_request(r) for r in requests],   # the history, which is what lets the page say "waiting on CDSO"
        })

    def post(self, request):
        registration, error = self._guard(request)
        if error:
            return error

        # Asked in Python first so the ordinary case gets a sentence the owner
        # can act on; the database constraint below is what actually holds.
        if RegistrationChangeRequest.objects.filter(
            registration=registration,
            status=RegistrationChangeRequest.Status.PENDING,
        ).exists():
            return Response(
                {"error": "You already have a change waiting for CDSO approval. "
                          "Cancel it first if you need to change something else."},
                status=status.HTTP_409_CONFLICT)

        before = current_values(registration)    # read first: once the request is approved the row holds the new values on both sides
        changes, errors = clean_changes(registration, dict(request.data))   # the same cleaners the pending path runs
        if errors:
            return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)
        if not changes:
            # A 400 here, where the pending path returns 200 for the same
            # situation: filing an empty request would put a row in the CDSO
            # queue for a reviewer to open and find nothing in.
            return Response(
                {"error": "Nothing was changed, so there is nothing to submit."},
                status=status.HTTP_400_BAD_REQUEST)

        try:
            change_request = RegistrationChangeRequest.objects.create(
                registration=registration,
                requested_by=request.user,        # who asked — not necessarily who the registration names, on a shared account
                changes=changes,                  # the cleaned field -> new value map, not the raw payload
                # Snapshot for the audit trail, and what a *decided* request is
                # rendered from — once a change is applied the row holds the new
                # value on both sides. See the model docstring.
                previous={k: before.get(k, '') for k in changes},
            )
        except IntegrityError:
            # uniq_pending_change_request_per_registration. The .exists() check
            # above catches this in every ordinary case; this is the double
            # submit that got through it, and it deserves the same readable
            # answer rather than a 500.
            return Response(
                {"error": "You already have a change waiting for CDSO approval. "
                          "Cancel it first if you need to change something else."},
                status=status.HTTP_409_CONFLICT)

        # Field NAMES only, never the values: the audit log is read by staff
        # who have no business seeing a licence number they were not shown.
        audit(request, AuditLog.Action.RECORD_CREATED,
              "Registration change requested by owner | Plate: %s | Fields: %s | "
              "Awaiting CDSO approval"
              % (registration.plate_number or registration.conduction_number,
                 ', '.join(sorted(changes))),
              target_user=request.user)
        _notify_change_requested(registration, change_request)   # rings the admin bell; the queue is otherwise silent

        return Response(_serialize_change_request(change_request),
                        status=status.HTTP_201_CREATED)   # the filed request, in the same shape the history list uses


# The owner changes their mind before CDSO gets to it.
class OwnerChangeRequestCancelView(APIView):
    """The owner withdraws their own pending request.

    Marked cancelled rather than deleted: "they asked and then thought better of
    it" is part of the account's history, and it is also what frees the partial
    unique constraint so they can file a corrected request straight away.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        if request.user.role != 'vehicle_owner':
            return Response({"error": "Only vehicle owners can cancel their own request."},
                            status=status.HTTP_403_FORBIDDEN)
        # requested_by in the filter, not checked afterwards: somebody else's
        # request is simply not found, which is also the honest answer to give.
        change_request = (RegistrationChangeRequest.objects
                          .filter(pk=pk, requested_by=request.user)
                          .first())
        if change_request is None:
            return Response({"error": "Change request not found."},
                            status=status.HTTP_404_NOT_FOUND)
        # A decided request is history. Cancelling one already approved would
        # claim a change was withdrawn that has in fact been applied.
        if change_request.status != RegistrationChangeRequest.Status.PENDING:
            return Response(
                {"error": "This request has already been reviewed and cannot be cancelled."},
                status=status.HTTP_400_BAD_REQUEST)

        change_request.status = RegistrationChangeRequest.Status.CANCELLED   # leaving PENDING is what releases the one-open-request constraint
        change_request.reviewed_at = timezone.now()   # when it stopped being open; reviewed_by stays empty because nobody reviewed it
        change_request.save(update_fields=['status', 'reviewed_at'])

        audit(request, AuditLog.Action.RECORD_UPDATED,
              "Registration change request #%s cancelled by owner" % change_request.pk,
              target_user=request.user)
        return Response(_serialize_change_request(change_request))


# The CDSO's side: the queue of requests waiting on a decision.
class ChangeRequestListView(APIView):
    """The CDSO review queue. `?status=` filters; pending is the default and is
    deliberately oldest-first — the queue is worked through, not browsed."""
    permission_classes = [IsAdminOrCdso]

    def get(self, request):
        wanted = (request.query_params.get('status') or 'pending').strip().lower()   # pending by default: the queue is what a reviewer opens this screen for
        # All three relations are read for every row below (the applicant, who
        # asked, who decided), so they are joined in rather than fetched one
        # query per row.
        qs = (RegistrationChangeRequest.objects
              .select_related('registration', 'requested_by', 'reviewed_by'))
        if wanted != 'all':
            # Checked against the model's own choices, so an unknown filter is
            # refused instead of quietly returning an empty queue that reads
            # like "nothing to do".
            valid = {c for c, _ in RegistrationChangeRequest.Status.choices}
            if wanted not in valid:
                return Response({"error": "Unknown status filter."},
                                status=status.HTTP_400_BAD_REQUEST)
            qs = qs.filter(status=wanted)
        if wanted == 'pending':
            qs = qs.order_by('created_at')       # oldest first, overriding the model's newest-first default: a queue is worked through, not browsed
        return Response([_serialize_change_request(r, for_review=True)   # for_review adds the applicant's details a reviewer needs
                         for r in qs[:200]])     # capped: a screen nobody can read past 200 rows of


# The decision itself — the only place a change against an accepted
# registration actually lands. Reject is a few lines; approve is the rest of
# the class, because approving has to re-check, apply, mirror and record.
class ChangeRequestDecisionView(APIView):
    """CDSO approves or rejects one pending change request.

    Approval re-validates before it applies. The request may have been sitting
    in the queue for days, and `clean_changes` is the only thing that knows
    whether the plate it asks for is still free — so a request that was valid
    when filed can be refused here, with the reason the owner needs rather than
    an IntegrityError.
    """
    permission_classes = [IsAdminOrCdso]

    # `decision` comes from the URL ('approve' or 'reject'), so the two
    # outcomes are separate routes rather than a flag in the body that could be
    # mistyped into the wrong one.
    def post(self, request, pk, decision):
        change_request = (RegistrationChangeRequest.objects
                          .select_related('registration')   # the registration is read on every path below
                          .filter(pk=pk)
                          .first())
        if change_request is None:
            return Response({"error": "Change request not found."},
                            status=status.HTTP_404_NOT_FOUND)
        # Already decided — most often two reviewers with the queue open at the
        # same time. Says which way it went, so the second one knows.
        if change_request.status != RegistrationChangeRequest.Status.PENDING:
            return Response(
                {"error": "This request has already been %s."
                          % change_request.get_status_display().lower()},
                status=status.HTTP_400_BAD_REQUEST)

        registration = change_request.registration
        note = (request.data.get('note') or '').strip()   # optional on approve, required on reject

        if decision == 'reject':
            # A reason is required because the owner is emailed it. "Declined"
            # with nothing after it leaves them with no idea what to file next.
            if not note:
                return Response({"error": "A reason is required when declining a change."},
                                status=status.HTTP_400_BAD_REQUEST)
            # Nothing is applied and nothing is mirrored: a rejection leaves the
            # registration exactly as it was, so there is no transaction here.
            change_request.status = RegistrationChangeRequest.Status.REJECTED
            change_request.decision_note = note
            change_request.reviewed_by = request.user
            change_request.reviewed_at = timezone.now()
            change_request.save(update_fields=[
                'status', 'decision_note', 'reviewed_by', 'reviewed_at'])

            audit(request, AuditLog.Action.RECORD_UPDATED,
                  "Registration change request #%s declined | Applicant: %s | "
                  "Reason: %s | By: %s"
                  % (change_request.pk, registration.full_name, note,
                     request.user.full_name),
                  target_user=registration.user)
            send_in_background(send_change_request_decision_email,
                               registration, change_request, [])   # an empty summary: nothing changed, so there is no before-and-after to show
            return Response(_serialize_change_request(change_request, for_review=True))   # the decided row, so the queue can redraw it in place

        # ── Approve ──
        # Re-validated against the row as it stands now, not as it stood when
        # the request was filed.
        changes, errors = clean_changes(registration, change_request.changes)
        if errors:
            # 409, not 400: the request was valid when filed and the reviewer
            # did nothing wrong — the world moved underneath it.
            return Response(
                {"error": "This change can no longer be applied — the details it asks "
                          "for are not available any more. Decline it and ask the owner "
                          "to file a corrected one.",
                 "errors": errors},
                status=status.HTTP_409_CONFLICT)
        if not changes:
            # Someone (CDSO at the counter, an earlier approval) already made
            # the same edit. The request is satisfied, so close it rather than
            # leaving it open against a row it no longer changes.
            change_request.status = RegistrationChangeRequest.Status.APPROVED
            change_request.decision_note = (
                note or 'Already applied — the registration matched the request.')
            change_request.reviewed_by = request.user
            change_request.reviewed_at = timezone.now()
            change_request.save(update_fields=[
                'status', 'decision_note', 'reviewed_by', 'reviewed_at'])
            return Response(_serialize_change_request(change_request, for_review=True))

        summary = describe(registration, changes)   # built before the write, while the old values are still on the row
        # All four writes in one transaction. Applying the change but failing to
        # mirror it is the worst outcome available here: the owner's dashboard
        # would show the new plate while the gate still knows the old one.
        with transaction.atomic():
            touched = apply_changes(registration, changes)   # returns the field names actually written, which is what the mirror works from
            registration.save()
            _mirror_registration_change(registration, touched)   # out to the Vehicle row and the account
            change_request.status = RegistrationChangeRequest.Status.APPROVED
            change_request.decision_note = note      # optional here — an approval explains itself
            change_request.reviewed_by = request.user
            change_request.reviewed_at = timezone.now()
            change_request.save(update_fields=[
                'status', 'decision_note', 'reviewed_by', 'reviewed_at'])
        # ── Past this line it has committed: the pass describes the new car. ──

        # The full before-and-after here, unlike the owner's own filing above:
        # this is the record of what a staff member changed on somebody else's
        # registration, and "which fields" would not be enough to answer for it.
        audit(request, AuditLog.Action.RECORD_UPDATED,
              "Registration change request #%s approved | Applicant: %s | %s | By: %s"
              % (change_request.pk, registration.full_name,
                 ' | '.join("%s: %s -> %s" % (row['label'], row['old'] or '(blank)',   # '(blank)' so an empty field reads as empty rather than as a gap in the line
                                              row['new'])
                            for row in summary),
                 request.user.full_name),
              target_user=registration.user)
        send_in_background(send_change_request_decision_email,
                           registration, change_request, summary)

        return Response(_serialize_change_request(change_request, for_review=True))


# One request in the shape both screens read it in. The `decided` switch below
# is the whole point of the function — see the docstring.
def _serialize_change_request(change_request, for_review=False):
    """One request as the owner portal and the CDSO queue both read it.

    A request still waiting has its diff rebuilt from the live registration, so
    a reviewer is always shown the change against what approving it would
    overwrite — not against a snapshot that may be days stale.

    A decided one is read from that snapshot instead. Approving applies the
    change, so the row then holds the new value on both sides and the live diff
    would render "RED -> RED" — which tells the owner nothing about what was
    done to their registration.
    """
    registration = change_request.registration
    decided = change_request.status != RegistrationChangeRequest.Status.PENDING   # approved, rejected or cancelled — anything that is no longer open
    data = {
        "id":            change_request.pk,
        "status":        change_request.status,               # the stored value, for the page's own logic
        "status_label":  change_request.get_status_display(), # and the readable one, so the page never spells it itself
        # `previous` is passed only once the request is decided: while it is
        # open the diff is rebuilt live against the registration as it stands.
        "changes":       describe(registration, change_request.changes or {},
                                 previous=change_request.previous if decided else None),
        "decision_note": change_request.decision_note,
        "created_at":    change_request.created_at,
        "reviewed_at":   change_request.reviewed_at,
        "reviewed_by":   change_request.reviewed_by.full_name if change_request.reviewed_by else '',   # '' rather than None: a cancelled request was never reviewed
    }
    # Only the CDSO queue gets these. The owner is looking at their own
    # registration and already knows whose it is — sending it anyway would put
    # the applicant's email into a response that did not need to carry it.
    if for_review:
        data.update({
            "registration_id": registration.pk,   # lets the queue link straight to the application
            "full_name":       registration.full_name,
            "email":           registration.email,
            "registrant_type": registration.registrant_type,
            "plate_number":    registration.plate_number or registration.conduction_number,   # whichever identifies this vehicle
            # Falls back to the applicant's name for a row whose requester is
            # gone — a deleted account nulls the FK but leaves the request.
            "requested_by":    (change_request.requested_by.full_name
                                if change_request.requested_by else registration.full_name),
        })
    return data


def _notify_change_requested(registration, change_request):
    """Admin-bell entry for a filed request — the queue is otherwise silent."""
    from accounts.notifications import notify as bell   # imported here rather than at module level, to keep the import graph acyclic
    # Labels, not values: the bell is read by any admin, and what was asked for
    # is the reviewer's business once they open the request itself.
    fields = ', '.join(sorted(row['label'] for row in
                              describe(registration, change_request.changes or {})))
    plate = registration.plate_number or registration.conduction_number   # whichever identifies the vehicle, for the notification's title
    bell('registration', 'change_requested',
         "Detail change requested — %s" % plate,
         "%s asked to change: %s. Waiting for CDSO approval."
         % (registration.full_name, fields),
         severity='info', plate_number=plate, link='/admin/vehicles')


def _notify_registration_edited(registration, summary):
    """Admin-bell entry for a pending applicant's own correction.

    Worth a notification even though no approval is needed: a reviewer may have
    the application open, and the row they are reading has just moved.
    """
    from accounts.notifications import notify as bell
    fields = ', '.join(sorted(row['label'] for row in summary))   # the summary is already built here, so there is nothing to re-derive
    plate = registration.plate_number or registration.conduction_number
    bell('registration', 'registration_edited',   # a different kind from the one above, so the bell can tell "corrected" from "asked to correct"
         "Applicant corrected their details — %s" % plate,
         "%s updated: %s before review." % (registration.full_name, fields),
         severity='info', plate_number=plate, link='/admin/vehicles')


# ──────────────────────────────────────────────
# Department & Program lists (public, for registration form)
# ──────────────────────────────────────────────

# The department dropdown on the public registration form. Curated by admin as
# ReferenceItem rows, so adding one needs no code change.
class DepartmentListView(APIView):
    permission_classes = [permissions.AllowAny]   # read by the form before anyone has an account

    def get(self, request):
        # is_active, so retiring a department stops it being offered without
        # rewriting the registrations that already name it. values_list with
        # flat=True: a plain list of names is the whole response.
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
            # Excluded by name rather than deleted: the rows are still what
            # older registrations point at, and removing them would strand them.
            .exclude(name__icontains='Grade 11')
            .exclude(name__icontains='Grade 12')
            .values_list('name', flat=True)
        )
        return Response(names)


# ──────────────────────────────────────────────
# Parking Availability (for vehicle owners)
# ──────────────────────────────────────────────

# "Signed in, and an owner rather than staff." Defined here but not referenced
# anywhere in the backend — ParkingAvailabilityView below opens itself to any
# signed-in role instead. Recorded, not changed: this pass comments code.
class IsVehicleOwnerRole(permissions.BasePermission):
    def has_permission(self, request, view):
        # All three parts matter: AnonymousUser has no role, and an
        # unauthenticated request would otherwise fail on the attribute.
        return bool(request.user and request.user.is_authenticated and request.user.role == 'vehicle_owner')


# "Where can I park?", answered from what the cameras can currently see.
class ParkingAvailabilityView(APIView):
    permission_classes = [permissions.IsAuthenticated]   # any signed-in role, not owners alone — guards and admin read the same figures

    def get(self, request):
        """Live availability for the owner portal.

        `summary` per category: capacity, bays parked in (`occupied`, from the
        cameras) and the free spaces that leaves, plus `on_campus` — vehicles
        inside the gates per the entry/exit scans, parked or not. The gate
        figure is reported beside the parking one and never subtracted from it.
        `unmonitored` counts zones with no baseline, whose bays are not scored.

        `zones` and `spaces` are the bay map: which specific slots look free, so
        an owner can see where to head.

        Three queries flat, whatever the number of zones, bays or vehicles: the
        spaces page, the declared-capacity aggregate, and the ledger count. The
        per-zone name lookup used to run inside the aggregation loop, one SELECT
        per zone; it now reads the `select_related` row already in hand.
        """
        from .capacity import category_state   # imported here, not at module level, to keep this module's import graph flat

        # Optional filter. Anything other than the two known categories is
        # ignored rather than refused — an unrecognised value simply means
        # "show me everything", which is what the page defaults to anyway.
        category = request.query_params.get('category', '')
        qs = ParkingSpace.objects.select_related('zone').all()   # the zone is read for every bay below, so it is joined in once here
        if category in ['motorcycle', 'car']:
            qs = qs.filter(zone__vehicle_category=category)   # filtered on the zone's category: a bay belongs to whatever its zone is for

        # Materialise once, then serialize from the same rows. Aggregating over
        # the model objects (whose `zone` is already joined) is what removes the
        # per-zone query.
        space_rows = list(qs)                    # the query runs once, here; everything below reads this list
        spaces = ParkingSpaceSerializer(space_rows, many=True).data   # the bay map, for the "which slot is free" view

        state = category_state()                 # the authoritative tallies, worked out once for both categories
        summary = {}
        for cat in ('car', 'motorcycle'):
            # Both categories are computed above regardless; this only decides
            # which of them the caller asked to be told about.
            if category in ('car', 'motorcycle') and cat != category:
                continue
            cat_state = state.get(cat, {})       # {} when a category has no zones configured at all
            # Every read is .get with a default: a partially configured campus
            # should render a screen full of zeroes, not raise a KeyError.
            summary[cat] = {
                'total':     cat_state.get('capacity', 0),      # bays declared for this category
                'occupied':  cat_state.get('occupied', 0),      # bays a camera currently sees a vehicle in
                'on_campus': cat_state.get('on_campus', 0),     # vehicles inside the gates per the scans — reported beside the parking figure, never subtracted from it
                'unmonitored': cat_state.get('unmonitored', 0), # zones with no baseline, whose bays nobody can score
                # Bays an event under way has declared it will fill. Reported
                # separately from 'occupied' so the screen can say WHY the free
                # count dropped instead of looking like a miscount.
                'reserved':  cat_state.get('reserved', 0),
                'available': cat_state.get('available', 0),
                'is_full':   cat_state.get('is_full', False),
                'source':    'camera_bays',      # says where these numbers came from, so a screen never presents a camera estimate as a count of passes
            }

        # The same bays again, this time tallied per zone. Counted in Python
        # over the rows already in hand rather than asked of the database a
        # second time — the join above is what makes `space.zone` free here.
        zone_agg = {}  # zone_id -> bay tallies for that zone
        for space in space_rows:
            zone = space.zone
            if zone is None:
                continue                         # a bay not yet assigned to a zone: it belongs in no zone's tally
            entry = zone_agg.get(zone.id)
            if entry is None:                    # first bay seen for this zone, so start its tally
                entry = zone_agg[zone.id] = {
                    'zone_id':   zone.id,
                    'zone_name': zone.name,
                    'category':  zone.vehicle_category,
                    'total':     0,
                    'occupied':  0,
                }
            entry['total'] += 1                  # every bay counts toward its zone's size
            if space.is_occupied:
                entry['occupied'] += 1

        zones = []
        for z in zone_agg.values():
            # Guarded against a zone with no bays: it cannot happen through the
            # loop above, which only creates an entry when it sees one, but the
            # division is the kind that takes a whole page down with it.
            fill_pct = round(z['occupied'] / z['total'] * 100) if z['total'] > 0 else 0
            zones.append({**z, 'available': z['total'] - z['occupied'],   # free bays in this specific zone
                          'fill_pct': fill_pct, 'source': 'camera_bays'})

        return Response({
            "spaces":  spaces,                   # bay by bay: where to actually head
            "summary": summary,                  # per category: the headline numbers
            "zones":   zones,                    # per zone: how full each area is
            # The event holding bays back right now, so the screen can name it
            # rather than leaving the smaller free count unexplained.
            "event":   state.get('event'),
            # Missed exit scans today — surfaced so a gate that stopped scanning
            # exits is visible rather than quietly inflating the count.
            "stale_excluded": state.get('stale_excluded', 0),
        })


# The one row that configures the whole system, and the three ways it is
# touched: GET reads it, PUT rewrites all of it, PATCH flips a toggle.
class SystemSettingsView(APIView):
    """System-wide configuration. Readable by any signed-in role — screens all
    over the app need the retention, fee and event-mode values — but writable
    only by the CDSO with a fresh two-factor step-up.

    A write here can silently turn off account expiry, shorten the retention
    window that deletes archived accounts, or open the campus, so it is exactly
    the kind of quiet change a stolen session would be used for.
    """

    # Permissions per method rather than one `permission_classes` list, because
    # reading and writing are not the same risk. Instantiated, not named: DRF
    # expects objects here, where the class attribute would take the classes.
    def get_permissions(self):
        if self.request.method == 'GET':
            return [permissions.IsAuthenticated()]   # every role reads these values somewhere
        # Any write: CDSO only, AND a fresh step-up token. HasRecentTwoFactor
        # exempts safe methods itself, so it would pass a GET through — the
        # branch above is what actually keeps reads open.
        return [IsAdminOrCdso(), HasRecentTwoFactor()]

    # One shape for the whole row, used by all three methods AND as the
    # before/after snapshot the audit line is diffed from — which is why it
    # lists every field rather than only the ones a screen happens to show.
    def _serialize(self, obj):
        return {
            "retention_years":    obj.retention_years,
            "scan_dedup_seconds": obj.scan_dedup_seconds,
            "event_mode_parking": obj.event_mode_parking,
            "event_mode_entry":   obj.event_mode_entry,
            "open_campus_mode":   obj.open_campus_mode,
            # ISO strings, and None when unset — dates go out as text so the
            # form displays them without doing any date maths of its own.
            "registration_start": obj.registration_start.isoformat() if obj.registration_start else None,
            "registration_end":   obj.registration_end.isoformat()   if obj.registration_end   else None,
            # float() for the wire only; the column and every charge stay Decimal.
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
        return Response(self._serialize(SystemSettings.get()))   # SystemSettings.get() creates the single row on first call, so this never 404s

    # The full rewrite. Its shape is: read every value (falling back to what is
    # already stored), validate every one of them into `errors`, refuse the
    # whole request if anything is wrong, then write and apply the knock-on
    # effects. Nothing is assigned to `obj` until every check has passed, so a
    # request with one bad field cannot half-apply.
    def put(self, request):
        obj = SystemSettings.get()
        before = self._serialize(obj)            # the snapshot the audit line is diffed against at the end
        errors = {}                              # field -> message; collected rather than raised, so the form gets every problem at once

        from datetime import date as date_type   # aliased, as every datetime import in this file is (_dt, _date); the plain name is never bound here

        # Every field defaults to what is already stored, which is what makes a
        # PUT carrying only some keys leave the rest alone instead of blanking
        # them. The values are still raw here — validation is the next block.
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

        # The pattern every numeric field below follows: coerce, then range
        # check, and record a message instead of raising. int() of a string is
        # deliberate — a form posts "5", not 5.
        try:
            retention_years = int(retention_years)
            # How long archived records are kept before deletion. The ceiling is
            # as important as the floor: this is the number that decides when
            # archived accounts are permanently removed.
            if not (1 <= retention_years <= 10):
                errors["retention_years"] = "Must be between 1 and 10 years."
        except (TypeError, ValueError):
            errors["retention_years"] = "Must be an integer."

        try:
            scan_dedup_seconds = int(scan_dedup_seconds)
            # How long the gate ignores a repeat of the same plate. Too short
            # and one car arriving is logged twice; too long and a car that
            # genuinely left and returned is missed.
            if not (5 <= scan_dedup_seconds <= 300):
                errors["scan_dedup_seconds"] = "Must be between 5 and 300 seconds."
        except (TypeError, ValueError):
            errors["scan_dedup_seconds"] = "Must be an integer."

        # Handles both callers: a browser sending "2026-06-01", and the default
        # above handing back the date object already on the row.
        def parse_date(val):
            if not val:
                return None                      # blank clears the date rather than being an error
            if isinstance(val, date_type):
                return val                       # already a date — the stored value came straight through
            from datetime import datetime
            return datetime.strptime(str(val), "%Y-%m-%d").date()   # strict format: raises ValueError, which the callers catch

        try:
            registration_start = parse_date(registration_start)
        except ValueError:
            errors["registration_start"] = "Invalid date format. Use YYYY-MM-DD."

        try:
            registration_end = parse_date(registration_end)
        except ValueError:
            errors["registration_end"] = "Invalid date format. Use YYYY-MM-DD."

        # Only once both parsed, and only when both are set: comparing a date
        # against a string that failed to parse would raise here instead of
        # reporting the parse error the applicant actually needs to see.
        if not errors and registration_start and registration_end and registration_end < registration_start:
            errors["registration_end"] = "End date must be on or after the start date."

        # Decimal(str(...)), never Decimal(float): going through a float first
        # is what turns a fee of 300.10 into 300.09999999999999. This is money.
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
        except (TypeError, ValueError, InvalidOperation):   # InvalidOperation is Decimal's own complaint about unparseable text
            errors["vehicle_pass_fee_employee"] = "Must be a number."

        # Expiration is not optional — the period is the only control. Whatever
        # the client sends for the flag is ignored, so no request can turn owner
        # accounts into accounts that live forever.
        account_expiry_enabled = True            # overwritten unconditionally: the value read from the payload above is discarded here
        try:
            account_expiry_months = int(account_expiry_months)
            # 0 is allowed on its own so the period can be expressed purely in
            # days; the pair being zero together is what is refused, below.
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
        # Zero months AND zero days is a period of no length: the loop near the
        # end of this method would date an owner's expiry to the day they
        # joined, which is already past. Refused here rather than discovered
        # later by accounts going dark the moment they are created.
        if not errors and account_expiry_months == 0 and account_expiry_days == 0:
            errors["account_expiry_months"] = (
                "Account expiration cannot be switched off. Set at least 1 month or 1 day."
            )

        try:
            parked_after_seconds = int(parked_after_seconds)
            # How long a vehicle must sit still in a bay before the camera calls
            # it parked rather than manoeuvring.
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
        # Both guards are needed: either name still holding its raw payload
        # value would make the comparison below meaningless, or raise on a
        # string. Checked by key rather than on `errors` as a whole, so an
        # unrelated bad field does not skip this rule.
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
        auto_backup_frequency = str(auto_backup_frequency or 'off').lower()   # `or 'off'` covers None and ''; .lower() so "Daily" is accepted
        if auto_backup_frequency not in valid_freqs:
            errors["auto_backup_frequency"] = "Must be one of: off, hourly, daily, weekly, monthly."
        try:
            auto_backup_keep = int(auto_backup_keep)
            # How many backups are retained. Still range-checked when the
            # frequency is 'off': the number stays on the row and applies again
            # the moment somebody switches backups back on.
            if not (1 <= auto_backup_keep <= 90):
                errors["auto_backup_keep"] = "Must be between 1 and 90 backups."
        except (TypeError, ValueError):
            errors["auto_backup_keep"] = "Must be an integer."

        # All or nothing. One bad field and the row is left exactly as it was,
        # which is why every assignment below this line and none above it.
        if errors:
            return Response(errors, status=400)

        # Every field written, from the validated locals rather than from
        # request.data — the names below hold coerced values, not what was sent.
        obj.retention_years    = retention_years
        obj.scan_dedup_seconds = scan_dedup_seconds
        # The three booleans are the only fields with no validation of their
        # own: bool() accepts anything, and there is no wrong answer to a toggle.
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
        obj.save()                               # a full save, not update_fields: every column above was just reassigned
        # ── Past this line the settings are stored. What follows makes them take effect. ──

        # Running zones share one cached copy of the thresholds; dropping it
        # makes the change land on the next frame in this process rather than up
        # to a TTL later. No restart, and no reaching into the threads.
        parking_camera.invalidate_dwell_settings()

        # Apply a lowered keep-count now rather than at the next scheduled run.
        # An admin who reduces it is usually looking at a disk that is filling
        # up, and "it will tidy itself tomorrow" is not the answer they came for.
        if before["auto_backup_keep"] != auto_backup_keep:   # only when the number actually moved; pruning on every save would be wasted work
            from accounts.backup_utils import prune_backups
            prune_backups(auto_backup_keep)      # deletes everything past the newest `keep` of each rotating kind, straight away

        # Give an expiry date to any owner still missing one, using the duration
        # the admin just chose and counting from their join date. Owners that
        # already have a date keep it — frozen-at-creation semantics, so changing
        # the period never moves the goalposts on an existing account.
        #
        # Runs on every save, not just the first: an owner with no expires_at is
        # an account that would live forever, which is the state expiration is
        # meant to make impossible.
        from datetime import timedelta
        from dateutil.relativedelta import relativedelta   # months are not a fixed number of days, so timedelta alone cannot add them
        from accounts.models import User as _User          # aliased to stay clear of any local name in this long method
        # Only owners, only live ones, and only those with no date yet — the
        # `expires_at__isnull=True` filter is what makes this leave existing
        # accounts alone rather than re-dating everyone on every save.
        owners = list(_User.objects.filter(
            role='vehicle_owner', is_active=True, is_archived=False, expires_at__isnull=True,
        ))
        for owner in owners:
            # Counted from when they joined, not from today: a settings save is
            # not meant to hand anybody a fresh term they did not have.
            owner.expires_at = (owner.date_joined.date()
                                + relativedelta(months=account_expiry_months)
                                + timedelta(days=account_expiry_days))
        if owners:
            # batch_size matters here: Postgres' default is one CASE statement
            # covering every row, which stops being a query at a few thousand
            # owners. This is the one place a settings save touches many rows.
            _User.objects.bulk_update(owners, ['expires_at'], batch_size=500)   # one statement per 500 rows, and only the one column

        after   = self._serialize(obj)           # the row as it now stands, in the same shape as `before`
        # Diffed rather than logged wholesale: the audit line names only what
        # actually moved, so reading it later answers "what did they change?"
        # instead of restating every setting on the system.
        changed = [f"{k}: {before[k]} -> {after[k]}" for k in after if before[k] != after[k]]
        if changed:
            audit(request, AuditLog.Action.RECORD_UPDATED,
                  f"System Settings updated | {'; '.join(changed)} | By: {request.user.full_name}")

        return Response(self._serialize(obj))    # serialized a third time, from the saved row, so the form redraws from what was stored

    # The toggles, on their own. A PUT would work, but it demands every other
    # field be sent back correctly just to flip one switch — and these three are
    # flipped from a header bar during an event, not from the settings form.
    # Still CDSO-only with a step-up: get_permissions gates every non-GET.
    def patch(self, request):
        """Lightweight partial update — supports toggling event_mode_parking, event_mode_entry, and open_campus_mode."""
        obj = SystemSettings.get()
        update_fields = []                       # only the keys actually present are touched, which is what makes this partial
        # `in request.data`, not .get(): absent means "leave it", while a
        # present False means "turn it off", and .get() cannot tell them apart.
        if 'event_mode_parking' in request.data:
            obj.event_mode_parking = bool(request.data['event_mode_parking'])
            update_fields.append('event_mode_parking')
        if 'event_mode_entry' in request.data:
            obj.event_mode_entry = bool(request.data['event_mode_entry'])
            update_fields.append('event_mode_entry')
        if 'open_campus_mode' in request.data:
            obj.open_campus_mode = bool(request.data['open_campus_mode'])
            update_fields.append('open_campus_mode')
        if update_fields:                        # a PATCH naming none of the three writes nothing and audits nothing
            obj.save(update_fields=update_fields)   # only the toggled columns, so this cannot clobber a PUT running alongside it
            toggles = '; '.join(f"{f}: {getattr(obj, f)}" for f in update_fields)   # read back off the object, so the log states what was stored
            audit(request, AuditLog.Action.RECORD_UPDATED,
                  f"System Settings updated | {toggles} | By: {request.user.full_name}")
        return Response(self._serialize(obj))    # the whole row either way, so the caller never has to merge the reply into what it had


# ──────────────────────────────────────────────
# Events (Admin/CDSO manage campus events + organizer plates)
# ──────────────────────────────────────────────
#
# An event is a day the campus behaves differently, and it reaches two places
# the CDSO does not touch by hand:
#
#   * parking — `parking_share` declares how much of the bays the event will
#     fill, and the availability figures hold that much back while it runs
#   * the gate — an organizer's plate is admitted for the event even without a
#     vehicle pass
#
# Both of those read `is_under_way()`, which is true only while the event is
# active, unarchived, dated today and inside its window — so an event that is
# merely on record changes nothing.
#
# Four small helpers come first because the list and detail views share every
# one of them; the two views are then mostly about which fields a request is
# allowed to name.

def _serialize_event(ev):
    """One shape for an event, shared by the list and detail views.

    They had a byte-identical `_serialize` each; adding the time and parking
    fields to one and not the other is exactly the drift that copy invited.
    """
    return {
        'id':               ev.id,
        'name':             ev.name,
        'date':             ev.date.isoformat(),
        # 24-hour HH:MM, which is what the form's time inputs post back — these
        # two are for editing, and `time_display` below is for reading.
        'start_time':       ev.start_time.strftime('%H:%M') if ev.start_time else None,
        'end_time':         ev.end_time.strftime('%H:%M') if ev.end_time else None,
        'time_display':     ev.time_display,   # "9:00 AM - 3:00 PM" or "All day" — built by the model, so every screen words it identically
        'parking_share':    ev.parking_share,               # the stored choice
        'parking_share_label': ev.get_parking_share_display(),   # and its wording, so no screen spells it itself
        'parking_share_fraction': ev.share_fraction,        # the same choice as a 0.0-1.0 multiplier, for anything doing the arithmetic
        # The three states are not the same question and all three go out:
        # is_under_way is "right now"; is_active is the switch; archived is
        # "finished with". An event can be active and still not under way.
        'is_under_way':     ev.is_under_way(),
        'is_active':        ev.is_active,
        'archived':         ev.archived,
        'organizer_plates': ev.organizer_plates,   # already canonical on the row — see _clean_organizer_plates
        'created_at':       ev.created_at.isoformat(),
        'created_by_name':  ev.created_by.full_name if ev.created_by else None,   # None when the creating account has since been deleted
    }


# "What time?" from a form, as a time object — or nothing, which is its own
# valid answer here: an event with no times set runs all day.
def _parse_event_time(raw):
    """'' / None -> None (no time set); 'HH:MM' -> a time. Raises ValueError."""
    if raw in (None, ''):
        return None                              # unset, deliberately — not an error
    from datetime import datetime as _dt
    text = str(raw).strip()
    # Two formats because browsers disagree: some time inputs post "14:30" and
    # others "14:30:00". Both mean the same thing, so both are accepted.
    for fmt in ('%H:%M', '%H:%M:%S'):
        try:
            return _dt.strptime(text, fmt).time()
        except ValueError:
            continue                             # not this format; try the next
    # Raised rather than returned: every caller wraps this and turns the message
    # into a field error, so the wording here is what the admin reads.
    raise ValueError('Invalid time format. Use HH:MM (24-hour).')


def _clean_organizer_plates(raw):
    """Organizer identifiers in the form the gate compares against: upper-case,
    no spaces, control numbers as FM-001, no repeats. A plate, a conduction
    number or an e-bike control number may all be listed. Spaces were kept
    before, while every scan path strips them from the plate it reads — so
    "ABC 1234" typed into an event never matched the ABC1234 that drove up."""
    from .models import canonical_identifier   # the one normaliser; the gate compares against exactly what it returns
    plates = []
    for p in raw or []:                          # `or []` so a missing or null list is simply an empty one
        plate = canonical_identifier(str(p or ''))   # upper-cased, spaces removed, and FM001/FM-1 re-spelled as FM-001
        # Membership on a list rather than a set: the order the admin typed
        # them in is preserved, and an event's list is short enough that the
        # scan costs nothing.
        if plate and plate not in plates:        # drops blanks and repeats
            plates.append(plate)
    return plates


def _apply_event_times(ev, data, errors):
    """Read start_time / end_time / parking_share off `data` onto `ev`.

    Only keys actually present are touched, so a PATCH that sends just the name
    cannot blank an event's times.
    """
    # `in data`, never .get(): absent means "leave it alone", while a present
    # '' means "clear the time". A .get() default could not tell them apart,
    # and this helper is shared with a PATCH that often names neither.
    for field in ('start_time', 'end_time'):
        if field in data:
            try:
                setattr(ev, field, _parse_event_time(data[field]))
            except ValueError as exc:
                errors[field] = str(exc)         # recorded, not raised: the caller wants every field's problem at once

    if 'parking_share' in data:
        share = (data['parking_share'] or Event.ParkingShare.NONE)   # blank means the event reserves no parking
        # Checked against the model's own choices: an unrecognised value would
        # otherwise store fine and then reserve 0.0 of the parking silently,
        # because share_fraction returns 0.0 for anything it does not know.
        if share not in Event.ParkingShare.values:
            errors['parking_share'] = 'Choose how much of parking the event fills.'
        else:
            ev.parking_share = share

    # An event that ends before it starts is a typo every time, and it would
    # make is_under_way() false for every minute of the day.
    # `not errors` first: if a time failed to parse, the attribute still holds
    # whatever it held before, and comparing those two would be meaningless.
    # `<=` and not `<`, because an event that ends the minute it starts has no
    # window for is_under_way() to fall inside either.
    if (not errors and ev.start_time and ev.end_time
            and ev.end_time <= ev.start_time):
        errors['end_time'] = 'The end time must be after the start time.'
    return errors                                # the same dict that came in, so callers can chain their own checks into it


# List every event, and add one.
class EventListCreateView(APIView):
    permission_classes = [IsAdminOrCdso]         # staff only: an event changes who the gate admits

    # Kept as a method although it only forwards — the two views called
    # self._serialize() before the shared function existed, and leaving the
    # call shape alone is what let the duplicate body be removed safely.
    def _serialize(self, ev):
        return _serialize_event(ev)

    def get(self, request):
        # Everything, including archived events: this is the management screen,
        # and it is the caller that decides what to show. Ordering comes from
        # the model (newest date first). select_related because every row
        # renders its creator's name.
        events = Event.objects.select_related('created_by').all()
        return Response([self._serialize(e) for e in events])

    def post(self, request):
        # A name and a date are the whole of what an event must have; times,
        # parking share and organizer plates are all optional and all have
        # sensible absences (all day, reserves nothing, nobody listed).
        name             = (request.data.get('name') or '').strip()
        date_str         = request.data.get('date')
        organizer_plates = request.data.get('organizer_plates', [])

        # Returned one at a time, unlike the times below: these two are fatal on
        # their own, so there is nothing to be gained by collecting them.
        if not name:
            return Response({'name': 'Name is required.'}, status=400)
        if not date_str:
            return Response({'date': 'Date is required.'}, status=400)

        try:
            from datetime import datetime as _dt
            date_obj = _dt.strptime(str(date_str), '%Y-%m-%d').date()
        except ValueError:
            return Response({'date': 'Invalid date format. Use YYYY-MM-DD.'}, status=400)

        plates = _clean_organizer_plates(organizer_plates)
        # Built but not saved. The times are applied onto the unsaved object so
        # they can be validated in place, and nothing reaches the database
        # until every one of them has passed.
        ev = Event(
            name=name, date=date_obj, organizer_plates=plates, created_by=request.user,
        )
        errors = _apply_event_times(ev, request.data, {})   # a fresh dict: this is a create, so nothing has been collected yet
        if errors:
            return Response(errors, status=400)
        ev.save()                                # is_active and archived take the model's defaults; an event is not switched on by creating it
        # The plate COUNT, not the plates: who was admitted is on the scan
        # records, and an audit line is not the place to list vehicles.
        audit(request, AuditLog.Action.RECORD_CREATED,
              f"Event added | {ev.name} on {ev.date} ({ev.time_display}) | "
              f"Parking: {ev.get_parking_share_display()} | "
              f"Organizer plates: {len(plates)} | By: {request.user.full_name}")
        return Response(self._serialize(ev), status=201)


# Edit or remove one event. PATCH throughout, never PUT: an event is amended a
# field at a time from the management screen, so every block below is guarded
# on the key being present rather than on its value.
class EventDetailView(APIView):
    permission_classes = [IsAdminOrCdso]

    def _serialize(self, ev):
        return _serialize_event(ev)

    def patch(self, request, pk):
        try:
            ev = Event.objects.select_related('created_by').get(pk=pk)   # the creator's name is in the reply, so join it in
        except Event.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=404)

        if 'name' in request.data:
            name = (request.data['name'] or '').strip()
            # Present but blank is a mistake, not an instruction: an event with
            # no name is unfindable on the screen that manages it.
            if not name:
                return Response({'name': 'Name cannot be empty.'}, status=400)
            ev.name = name

        # Moving the date is the one edit with side effects, because both
        # "finished" and "running" were answers about the OLD date and neither
        # survives the move.
        if 'date' in request.data:
            try:
                from datetime import datetime as _dt, date as _date
                new_date = _dt.strptime(str(request.data['date']), '%Y-%m-%d').date()
                ev.date = new_date
                today = _date.today()
                # Rescheduling unarchives the event; activation follows the new date
                ev.archived  = False
                ev.is_active = (new_date == today)   # moved to today: on. Moved anywhere else: off until its day comes
            except ValueError:
                return Response({'date': 'Invalid date format.'}, status=400)

        # After the date block on purpose: a request that names both gets the
        # switch it explicitly asked for, rather than the one the new date
        # implies. That is what lets an admin arm tomorrow's event early.
        if 'is_active' in request.data:
            ev.is_active = bool(request.data['is_active'])

        if 'organizer_plates' in request.data:
            # Replaces the list outright — there is no add-one endpoint, so the
            # screen always sends the whole list as it should end up.
            ev.organizer_plates = _clean_organizer_plates(request.data['organizer_plates'])

        errors = _apply_event_times(ev, request.data, {})   # the same helper the create path uses, so the rules cannot differ between them
        if errors:
            return Response(errors, status=400)   # nothing has been saved yet, so the event is untouched

        ev.save()                                # one save covering every block above
        audit(request, AuditLog.Action.RECORD_UPDATED,
              f"Event updated | {ev.name} on {ev.date} ({ev.time_display}) | "
              f"Parking: {ev.get_parking_share_display()} | By: {request.user.full_name}")
        return Response(self._serialize(ev))

    def delete(self, request, pk):
        try:
            ev = Event.objects.get(pk=pk)        # no select_related: nothing below reads the creator
        except Event.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=404)
        desc = f"{ev.name} on {ev.date}"         # read off the row BEFORE it is gone, so the audit line still has something to say
        # A real delete, unlike a parking notice below, which is only
        # deactivated. Safe because the one thing that points at an Event —
        # scanning.AccessLog.event — is SET_NULL, so the scans of organizers
        # who came and went survive and simply stop naming the event.
        ev.delete()
        audit(request, AuditLog.Action.RECORD_DELETED,
              f"Event deleted | {desc} | By: {request.user.full_name}")
        return Response(status=204)              # 204: deleted, and there is nothing left to return


# ──────────────────────────────────────────────
# Parking Notices (CDSO/Admin broadcast, owner read)
# ──────────────────────────────────────────────
#
# A notice is an announcement the CDSO sends out: stored so it shows in every
# owner's portal, and emailed at the same moment so it reaches them whether or
# not they log in. Both classes below are written the same way — the route is
# open to any signed-in account, and the role is checked inside each method
# that writes, because owners must be able to READ what admins post.

class ParkingNoticeView(APIView):
    # A method rather than `permission_classes`, though it returns the same
    # thing for every verb. The role split lives inside post() instead, since
    # GET and POST here serve different people rather than different risks.
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
        notices = ParkingNotice.objects.filter(is_active=True)   # is_active is the soft-delete flag: a removed notice is still on the row
        # The join-date cut is the whole of the rule in the docstring: a notice
        # posted before this account existed was never sent to them.
        if request.user.role != 'admin':
            notices = notices.filter(created_at__gte=request.user.date_joined)
        return Response(ParkingNoticeSerializer(notices, many=True).data)   # newest first, from the model's own ordering

    def post(self, request):
        """CDSO (admin) create and broadcast a notice to all vehicle owners."""
        # The real gate on writing. get_permissions above let any signed-in
        # account reach this method, so without this check an owner could
        # broadcast to every other owner.
        if request.user.role != 'admin':
            return Response({'error': 'Permission denied.'}, status=403)

        serializer = ParkingNoticeSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)
        notice = serializer.save(created_by=request.user)   # saved first: the record is the notice, and the email is a copy of it
        audit(request, AuditLog.Action.RECORD_CREATED,
              f"Parking notice broadcast | {notice.title} | By: {request.user.full_name}")

        # Email blast to all active vehicle owners
        from accounts.models import User as UserModel
        from django.core.mail import EmailMultiAlternatives
        from django.conf import settings

        # Active owners only — an archived or deactivated account is not a
        # person the CDSO is announcing anything to.
        recipients = list(
            UserModel.objects.filter(role='vehicle_owner', is_active=True)
            .values_list('email', flat=True)     # just the addresses; no User objects are needed
        )
        email_status = 'no_recipients'           # the default, reported honestly when there is nobody to send to
        if recipients:
            # Built as an f-string, so `notice.title` and `notice.body` below
            # go into the HTML exactly as typed — nothing escapes them the way
            # a Django template would. The notice is stored and then mailed to
            # every active owner, so markup written into either field persists
            # on the row and is delivered: a stored-XSS path into their inboxes.
            # The author is checked to be an admin one block up, and the
            # plain-text alternative beside this is unaffected — that bounds it,
            # it does not close it. Flagged for a fix; unchanged by this pass,
            # which only comments.
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
            #
            # Sent inline, on the request, where every other mail in this file
            # is handed to send_in_background. The admin therefore waits for the
            # whole blast, and a slow mail host holds the response open.
            # Recorded here for a later look; nothing is changed by this pass.
            try:
                email = EmailMultiAlternatives(
                    subject=f"SLC Parking Notice: {notice.title}",
                    body=f"Parking Notice\n\n{notice.title}\n\n{notice.body}",
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    to=[settings.DEFAULT_FROM_EMAIL],   # addressed to the office itself, because a mail needs a To: and the owners are all on BCC
                    bcc=recipients,
                )
                email.attach_alternative(html_msg, 'text/html')   # the plain-text body above stays as the fallback for clients that want it
                email.send(fail_silently=False)   # NOT silent: the except below is what turns a failure into a reported status
                email_status = 'sent'
            except Exception:
                # Report through the app logger (message + full traceback) so the
                # failure lands in the configured logs, not only on the console.
                logger.exception('Parking notice broadcast failed')
                email_status = 'failed'

        # 201 whatever the mail did. The notice exists and is already visible
        # in every owner's portal, so the send is reported beside it rather
        # than being allowed to fail the request that created it.
        data = ParkingNoticeSerializer(notice).data
        data['email_status'] = email_status      # 'sent', 'failed' or 'no_recipients' — the screen says which
        data['recipient_count'] = len(recipients)   # so "sent" comes with how many it reached
        return Response(data, status=201)


# Taking a notice down.
class ParkingNoticeDetailView(APIView):
    def get_permissions(self):
        return [permissions.IsAuthenticated()]   # same shape as above: the role check that matters is inside delete()

    def delete(self, request, pk):
        """CDSO (admin) deactivate (soft-delete) a notice."""
        if request.user.role != 'admin':     # the route is open to any signed-in account, so this is the gate
            return Response({'error': 'Permission denied.'}, status=403)
        notice = get_object_or_404(ParkingNotice, pk=pk)
        # Deactivated, not deleted — the opposite of how an event is removed.
        # The notice was emailed to every owner, so the copy on record is what
        # answers "what exactly did we tell them?" long after it left the portal.
        notice.is_active = False
        notice.save(update_fields=['is_active'])   # one column, so nothing else on the row can be disturbed
        audit(request, AuditLog.Action.RECORD_DELETED,
              f"Parking notice removed | {notice.title} | By: {request.user.full_name}")
        return Response({'message': 'Notice deactivated.'}, status=200)


# ──────────────────────────────────────────────
# Registration Period management (Admin/CDSO)
# ──────────────────────────────────────────────
#
# A period is the window the public registration form opens and closes on —
# _registration_window() near the top of this file reads whichever row is
# active. Exactly one may be active at a time, and that rule is kept by hand
# here rather than by a constraint: every path that activates one deactivates
# the rest first.
#
# Old periods are kept rather than deleted, so "which window was this
# registration filed under?" still has an answer years later.

# One shape for a period, used by every method below.
def _serialize_period(p):
    return {
        'id':         p.id,
        'label':      p.label,               # e.g. "AY 2026-2027 First Semester"
        'start_date': p.start_date.isoformat(),   # ISO text: the form shows these, it does no date maths
        'end_date':   p.end_date.isoformat(),
        'is_active':  p.is_active,           # the one flag that decides whether registration is open at all
        'created_at': p.created_at.isoformat(),
    }


# Validation for both the create and the edit, so a period cannot be created
# under one set of rules and then edited under another.
def _clean_period_payload(data, *, partial=False, current=None):
    """Validate a registration-period payload for create (all fields) or edit.

    `partial` keeps any field the caller left out at its `current` value, so a
    PATCH that only moves the end date does not have to resend the label.
    Returns (cleaned, errors) — cleaned is only complete when errors is empty.
    """
    from datetime import datetime as _dt

    # Strict: one format, and it raises on anything else. The callers below
    # catch that and turn it into a field message.
    def _as_date(raw):
        return _dt.strptime(str(raw), '%Y-%m-%d').date()

    errors = {}                                  # field -> message, collected so the form gets every problem at once
    cleaned = {}                                 # only trustworthy once errors is empty

    # The condition each field below repeats: take what was sent if it was
    # sent, and on a PATCH fall back to what the row already holds. `not
    # partial` makes a create demand every field, since there is nothing to
    # fall back to.
    if 'label' in data or not partial:
        label = (data.get('label') or '').strip()
        if not label:
            errors['label'] = 'Label is required.'   # present but blank is a mistake, not "leave it alone"
        cleaned['label'] = label
    else:
        cleaned['label'] = current.label         # untouched by this request

    for field in ('start_date', 'end_date'):
        if field in data or not partial:
            try:
                cleaned[field] = _as_date(data.get(field))
            except (ValueError, TypeError):      # TypeError as well: a missing key reaches str(None), which is not a date either
                errors[field] = 'Required. Use YYYY-MM-DD.'   # one message for both "absent" and "unparseable", since the fix is the same
        else:
            cleaned[field] = getattr(current, field)

    # Returns None for `cleaned` on failure rather than a half-filled dict, so
    # a caller that forgets to check `errors` cannot write partial values.
    if errors:
        return None, errors
    # Only reachable once both dates parsed. `<` and not `<=`: a one-day window
    # that opens and closes on the same date is legitimate.
    if cleaned['end_date'] < cleaned['start_date']:
        return None, {'end_date': 'End date must be on or after start date.'}
    return cleaned, {}


# List the periods, and open a new one.
class RegistrationPeriodListCreateView(APIView):
    # Split by method: the dates are shown on screens every role sees, but
    # only staff may move them.
    def get_permissions(self):
        if self.request.method == 'GET':
            return [permissions.IsAuthenticated()]
        return [IsAdminOrCdso()]

    def get(self, request):
        # Every period, archived ones included — this is the history as well as
        # the current window. Newest first, from the model's own ordering.
        return Response([_serialize_period(p) for p in RegistrationPeriod.objects.all()])

    def post(self, request):
        cleaned, errors = _clean_period_payload(request.data)
        if errors:
            return Response(errors, status=400)
        label, start, end = cleaned['label'], cleaned['start_date'], cleaned['end_date']

        # Creating a period activates it, which means standing the previous one
        # down first. .update() rather than a loop: one statement, and it
        # covers however many rows are wrongly active, not just the one.
        #
        # Not wrapped in a transaction — if the create below failed, no period
        # would be active and registration would read as closed until an admin
        # activated one by hand. Recorded, not changed: this pass comments code.
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
        before = f"{period.label} ({period.start_date} to {period.end_date})"   # captured before the write, for the audit line's left-hand side
        # partial=True with the row as `current`, so a request naming only the
        # end date keeps the label and start date it already had.
        cleaned, errors = _clean_period_payload(request.data, partial=True, current=period)
        if errors:
            return Response(errors, status=400)

        period.label      = cleaned['label']
        period.start_date = cleaned['start_date']
        period.end_date   = cleaned['end_date']
        # is_active is deliberately not in this list: whether a period is the
        # live one is the activate endpoint's decision, not an edit's.
        period.save(update_fields=['label', 'start_date', 'end_date'])
        audit(request, AuditLog.Action.RECORD_UPDATED,
              f"Registration period edited | {before} -> {period.label} "
              f"({period.start_date} to {period.end_date}) | By: {request.user.full_name}")
        return Response(_serialize_period(period))


# The switch: which period registration currently runs against. POST turns one
# on, DELETE turns one off — and DELETE really does mean off, not gone.
class RegistrationPeriodActivateView(APIView):
    permission_classes = [IsAdminOrCdso]

    def post(self, request, pk):
        """Set this period as the active one (deactivates all others)."""
        period = get_object_or_404(RegistrationPeriod, pk=pk)
        # Stand the others down first, then raise this one. The same
        # one-at-a-time rule the create path keeps, and the reason `get_active()`
        # can settle for .first().
        RegistrationPeriod.objects.filter(is_active=True).update(is_active=False)
        period.is_active = True
        period.save(update_fields=['is_active'])   # one column: the dates and label are not this endpoint's business
        audit(request, AuditLog.Action.RECORD_UPDATED,
              f"Registration period activated | {period.label} | By: {request.user.full_name}")
        return Response(_serialize_period(period))

    def delete(self, request, pk):
        """Deactivate without deleting — archives the period."""
        period = get_object_or_404(RegistrationPeriod, pk=pk)
        # A DELETE route that does not delete. The row is what past
        # registrations were filed under, so it stays; clearing the flag is
        # what closes registration.
        period.is_active = False
        period.save(update_fields=['is_active'])
        # RECORD_UPDATED, not RECORD_DELETED — the audit line says what actually
        # happened to the row rather than what the HTTP verb was.
        audit(request, AuditLog.Action.RECORD_UPDATED,
              f"Registration period archived | {period.label} | By: {request.user.full_name}")
        return Response(_serialize_period(period))


# ──────────────────────────────────────────────
# Supplier Management (Admin only)
# ──────────────────────────────────────────────
#
# A supplier is a company that delivers to the campus — canteen stock,
# maintenance, deliveries — and its plates are admitted at the gate without a
# vehicle pass, because the vehicle belongs to a company rather than a person.
#
# One plate belongs to exactly one supplier: SupplierPlate.plate_number is
# unique across the whole table, which is why the checks below search every
# supplier's plates rather than just this one's.

# Imported here rather than at the top of the file, where the other models are.
# Not required by anything — these are ordinary module-level imports that
# happen to sit mid-file. Recorded, not moved: this pass comments code.
from .models import Supplier, SupplierPlate
from .serializers import SupplierSerializer, SupplierPlateSerializer


# List every supplier, and add one.
class SupplierListCreateView(APIView):
    permission_classes = [IsAdminRole]        # admin only, tighter than the IsAdminOrCdso used elsewhere in this file

    def get(self, request):
        # prefetch_related, not select_related: plates are a reverse
        # many-to-one, so they come back in one extra query for the whole list
        # instead of one per supplier. Ordered by company name, from the model.
        suppliers = Supplier.objects.prefetch_related('plates').all()
        return Response(SupplierSerializer(suppliers, many=True).data)

    def post(self, request):
        company_name = (request.data.get('company_name') or '').strip()
        if not company_name:
            return Response({'company_name': 'Company name is required.'}, status=400)
        # __iexact, although the column is already unique: the database
        # constraint is case-sensitive, so "Acme" and "ACME" would both be
        # accepted as separate companies. This catches that, and returns a
        # sentence rather than the IntegrityError the constraint would raise.
        if Supplier.objects.filter(company_name__iexact=company_name).exists():
            return Response({'company_name': 'A supplier with this name already exists.'}, status=400)

        # Store plates in the same normalized form scans use (no spaces),
        # otherwise gate lookups can never match them
        # dict.fromkeys rather than a set: it drops duplicates while keeping
        # the order they were typed in, so the audit line below reads back the
        # way the admin entered them.
        plate_numbers = list(dict.fromkeys(
            _normalize_plate(p) for p in (request.data.get('plates') or []) if p and p.strip()
        ))
        if plate_numbers:
            # Searched across ALL suppliers, not just this one, because a plate
            # may belong to only one company. Every clashing plate is named at
            # once, so a list of twenty is not fixed one rejection at a time.
            existing = SupplierPlate.objects.filter(plate_number__in=plate_numbers).values_list('plate_number', flat=True)
            if existing:
                return Response({'plates': f"Plate(s) already registered: {', '.join(existing)}."}, status=400)

        category = request.data.get('category') or Supplier.Category.OTHER   # unstated means "Other", which is a real answer here
        if category not in Supplier.Category.values:   # checked against the model's own choices, which Django does not enforce on .create()
            return Response({'category': 'Invalid supplier category.'}, status=400)

        # Two statements, not one transaction: a supplier with no plates is a
        # usable row an admin can add plates to, so a failure here does not
        # leave anything that has to be cleaned up.
        supplier = Supplier.objects.create(company_name=company_name, category=category)
        SupplierPlate.objects.bulk_create(   # one INSERT for the whole list, not one per plate
            SupplierPlate(supplier=supplier, plate_number=p) for p in plate_numbers
        )
        audit(request, AuditLog.Action.RECORD_CREATED,
              f"Supplier added | {supplier.company_name} | Plates: {', '.join(plate_numbers) or 'none'} | By: {request.user.full_name}")
        return Response(SupplierSerializer(supplier).data, status=201)


# Edit or remove one supplier.
class SupplierDetailView(APIView):
    permission_classes = [IsAdminRole]

    # Field at a time, guarded on the key being present — the same shape the
    # event PATCH uses, and for the same reason: absent means "leave it".
    def patch(self, request, pk):
        supplier = get_object_or_404(Supplier, pk=pk)
        if 'company_name' in request.data:
            name = (request.data['company_name'] or '').strip()
            if not name:
                return Response({'company_name': 'Company name cannot be empty.'}, status=400)
            # No uniqueness check on the way in, unlike the create above: the
            # column's own constraint is what stops a rename onto a name that
            # is already taken. Recorded, not changed.
            supplier.company_name = name
        if 'is_active' in request.data:
            # The switch the gate reads: deactivating a supplier stops its
            # plates being admitted without removing the record of them.
            supplier.is_active = bool(request.data['is_active'])
        if 'category' in request.data:
            category = request.data['category']
            if category not in Supplier.Category.values:
                return Response({'category': 'Invalid supplier category.'}, status=400)   # returned before the save, so nothing partial lands
            supplier.category = category
        supplier.save()                          # a full save covering whichever of the three blocks ran
        audit(request, AuditLog.Action.RECORD_UPDATED,
              f"Supplier updated | {supplier.company_name} | Active: {supplier.is_active} | By: {request.user.full_name}")
        return Response(SupplierSerializer(supplier).data)

    def delete(self, request, pk):
        supplier = get_object_or_404(Supplier, pk=pk)
        name = supplier.company_name             # read before the row goes, so the audit line still has a name to give
        # A real delete, and it takes the company's plates with it:
        # SupplierPlate.supplier is CASCADE. Deactivating (the PATCH above) is
        # the way to stop a supplier without losing which plates were theirs.
        supplier.delete()
        audit(request, AuditLog.Action.RECORD_DELETED,
              f"Supplier deleted | {name} | By: {request.user.full_name}")
        return Response(status=204)


# One plate at a time, for a supplier that already exists.
class SupplierPlateView(APIView):
    """Add or remove a plate for a specific supplier."""
    permission_classes = [IsAdminRole]

    def post(self, request, pk):
        supplier = get_object_or_404(Supplier, pk=pk)   # 404s before anything is validated, so a bad id is not reported as a bad plate
        # Same normalized form scans use (no spaces) so gate lookups match
        plate_number = _normalize_plate(request.data.get('plate_number'))
        if not plate_number:                     # covers missing, null, blank and whitespace-only alike, since all normalise to ''
            return Response({'plate_number': 'Plate number is required.'}, status=400)
        # Across every supplier again, for the same one-company-per-plate rule.
        # The message deliberately does not name which company holds it —
        # an admin can look it up, and the answer is not this endpoint's to give.
        if SupplierPlate.objects.filter(plate_number=plate_number).exists():
            return Response({'plate_number': 'This plate is already registered to a supplier.'}, status=400)
        sp = SupplierPlate.objects.create(supplier=supplier, plate_number=plate_number)
        audit(request, AuditLog.Action.RECORD_CREATED,
              f"Supplier plate added | {plate_number} to {supplier.company_name} | By: {request.user.full_name}")
        return Response(SupplierPlateSerializer(sp).data, status=201)

    def delete(self, request, pk, plate_pk):
        # Both ids in the lookup: a plate id that belongs to a different
        # supplier is simply not found, rather than being deleted through the
        # wrong company's URL.
        plate = get_object_or_404(SupplierPlate, pk=plate_pk, supplier_id=pk)
        desc = f"{plate.plate_number} from {plate.supplier.company_name}"   # built before the delete, while the relation still resolves
        plate.delete()                           # a real delete: the plate is simply no longer theirs, and nothing hangs off the row
        audit(request, AuditLog.Action.RECORD_DELETED,
              f"Supplier plate removed | {desc} | By: {request.user.full_name}")
        return Response(status=204)


# ── Vehicle Registrations Report (CDSO/admin — branded PDF & Excel) ──────────
#
# Two downloads of the same thing in different formats, plus a summary that
# counts rather than lists. The filtering, the row building and the subtitle
# are pulled out into helpers precisely so the PDF and the Excel cannot come
# back with different numbers from the same query string.

# The column set, defined once: the Excel and the PDF share it, so the two
# files have the same columns in the same order.
REGISTRATION_REPORT_HEADERS = ['#', 'Date', 'Plate', 'Registrant', 'Type', 'Vehicle', 'Status']


def _filter_registrations_report(request):
    """Filter registrations for a report — mirrors the management page knobs."""
    qs = VehicleRegistration.objects.all()   # every status, including rejected and expired: a report is the record, not the working queue
    date_from = request.query_params.get('date_from', '').strip()
    date_to   = request.query_params.get('date_to', '').strip()
    status_f  = request.query_params.get('status', '').strip()
    search    = request.query_params.get('search', '').strip()
    # Campus-local dates, inclusive at both ends, and an unparseable date is
    # ignored rather than raising — see filter_local_date_range, which exists
    # because the plain `__date__gte` form both defeated the index and turned
    # a mistyped query parameter into a 500.
    qs = filter_local_date_range(qs, 'created_at', date_from, date_to)
    if status_f:
        # Not checked against the choices: an unknown status simply matches
        # nothing, and an empty report for a filter nobody set is harmless.
        qs = qs.filter(status=status_f)
    if search:
        # Plate or name, the two things somebody looking for one registration
        # actually has to hand.
        qs = qs.filter(Q(plate_number__icontains=search) | Q(full_name__icontains=search))

    # `desc` is the filter written out for the report's subtitle, so a printed
    # copy says on its face what it was filtered to — a page of numbers with no
    # statement of what was excluded is the kind of report that gets misread.
    status_labels = dict(VehicleRegistration.Status.choices)
    desc = []
    if date_from or date_to:
        desc.append(f"Period: {date_from or 'start'} to {date_to or 'today'}")   # names the open end, rather than leaving a blank
    if status_f:
        desc.append(f"Status: {status_labels.get(status_f, status_f)}")   # the readable label, falling back to the raw value for an unknown one
    if search:
        desc.append(f"Search: '{search}'")
    return qs.order_by('-created_at'), desc      # newest first, and the description alongside — both callers need both


# Turns registration rows into the flat list of cells both report formats take.
def _registration_report_rows(qs):
    from django.utils import timezone as tz
    # The label maps are built once, outside the loop: a get_..._display() call
    # per row would do this lookup thousands of times over.
    reg_labels    = dict(VehicleRegistration.RegistrantType.choices)
    status_labels = dict(VehicleRegistration.Status.choices)
    rows = []
    for i, r in enumerate(qs, start=1):          # start=1 so the '#' column reads as a human numbering, not an index
        rows.append([
            i,
            tz.localtime(r.created_at).strftime('%b %d, %Y'),   # campus-local: a report printed here must not date rows by UTC
            # An em dash rather than a blank for every empty field, so a gap in
            # a printed table reads as "nothing recorded" instead of looking
            # like a column that failed to render.
            r.plate_number or '—',
            r.full_name or '—',
            reg_labels.get(r.registrant_type, r.registrant_type or '—'),   # falls back to the stored value, then to a dash
            r.vehicle_type or '—',
            status_labels.get(r.status, r.status),   # status always has a value, so no dash case here
        ])
    return rows


# The one line under the report title that says what is in it.
def _registration_report_subtitle(desc, count):
    # 'All records' rather than an empty string when nothing was filtered: the
    # subtitle should still assert something, and the count is what a reader
    # checks the table against.
    return ('; '.join(desc) if desc else 'All records') + f" · {count} entries"


class RegistrationReportExcelView(APIView):
    """Download the (filtered) vehicle registrations as a branded Excel report — admin only."""
    permission_classes = [IsAdminOrCdso]

    def get(self, request):
        from django.utils import timezone as tz
        from report_utils import branded_excel_response, report_filename   # imported per call: reportlab/openpyxl are heavy and only these endpoints need them
        qs, desc = _filter_registrations_report(request)
        # Capped at 5,000. The slice is applied to the queryset, so the LIMIT
        # reaches the database rather than 100,000 rows being fetched and
        # discarded — and `len(rows)` below therefore counts what is actually
        # in the file, which is what the subtitle should state.
        rows = _registration_report_rows(qs[:5000])
        # The Excel subtitle carries who generated it and when; the PDF below
        # does not, because branded_pdf_response takes `generated_by` as its
        # own argument and prints it itself.
        subtitle = (f"Generated {tz.localtime().strftime('%B %d, %Y %I:%M %p')} "
                    f"by {getattr(request.user, 'full_name', '')} · "   # getattr with a default: an unnamed account must not break a download
                    + _registration_report_subtitle(desc, len(rows)))
        return branded_excel_response(
            filename=report_filename('Vehicle Registrations Report', 'xlsx'),
            sheet_title='Registrations',
            report_title='Vehicle Registrations Report',
            subtitle=subtitle,
            headers=REGISTRATION_REPORT_HEADERS,
            rows=rows,
            col_widths=[5, 16, 16, 28, 14, 16, 14],   # character widths, widest for the registrant's name
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
            # Millimetres, summing to 237 of the 267 available (A4 landscape
            # less report_utils' 15mm margins), so the table sits short of the
            # full width rather than filling it.
            col_widths_mm=[10, 30, 30, 60, 40, 40, 27],
        )


# The bucket anything unrecognised falls into. A plain string, not a member of
# any enum, precisely so it cannot collide with a real stored value.
OTHER_KEY = 'other'


# Every number the summary page and the summary PDF show, from one GROUP BY.
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
    # Labels for display, values for the keys. Both are copies — `list(...)`
    # and `dict(...)` — because the Other bucket is appended to them further
    # down, and appending to the enum's own list would leak into every other
    # caller in the process.
    type_labels    = dict(VehicleRegistration.RegistrantType.choices)
    status_labels  = dict(VehicleRegistration.Status.choices)
    payment_labels = dict(VehicleRegistration.PaymentStatus.choices)
    types     = list(VehicleRegistration.RegistrantType.values)
    statuses  = list(VehicleRegistration.Status.values)
    payments  = list(VehicleRegistration.PaymentStatus.values)

    # Every cell exists up front, including the Other row and column, so the
    # accumulate loop never has to branch on a missing key.
    known_types = set(types)                 # a set, because the loop below tests membership once per group
    grid = {t: {st: 0 for st in statuses + [OTHER_KEY]} for t in types + [OTHER_KEY]}       # type x status
    pay_grid = {t: {pm: 0 for pm in payments + [OTHER_KEY]} for t in types + [OTHER_KEY]}   # type x payment
    # Status x payment as well, so the page can scope the payment tiles to the
    # status the table is actually showing. Free: the GROUP BY below already
    # carries all three columns, so this is a third accumulation over rows we
    # have in hand, not another query.
    status_pay_grid = {st: {pm: 0 for pm in payments + [OTHER_KEY]}
                       for st in statuses + [OTHER_KEY]}

    # Tracked rather than inferred from the grid afterwards: a zero in the
    # Other row could equally mean "no such rows" or "the bucket exists and is
    # empty", and only the first should hide the row.
    seen_other_type = seen_other_status = seen_other_payment = False
    # .values(...).annotate(...) is the GROUP BY: one row back per distinct
    # combination, with its count, however many registrations there are.
    for row in (qs.values('registrant_type', 'status', 'payment_status')
                  .annotate(n=Count('id'))):
        # Each axis independently: a row can be a known type with an unknown
        # status, and it still has to land in exactly one cell of each grid.
        t  = row['registrant_type'] if row['registrant_type'] in known_types else OTHER_KEY
        st = row['status'] if row['status'] in status_labels else OTHER_KEY       # the label dict doubles as the membership test
        pm = row['payment_status'] if row['payment_status'] in payment_labels else OTHER_KEY
        seen_other_type    = seen_other_type    or t  == OTHER_KEY
        seen_other_status  = seen_other_status  or st == OTHER_KEY
        seen_other_payment = seen_other_payment or pm == OTHER_KEY
        # The same count added into all three grids, which is what makes them
        # reconcile: every grid totals to the same number of registrations.
        grid[t][st] += row['n']
        pay_grid[t][pm] += row['n']
        status_pay_grid[st][pm] += row['n']

    # Appended only now, and only when something landed there — the grids were
    # built with the Other key already present, so this adds it to the lists
    # that decide what gets RENDERED, not to the counting.
    if seen_other_type:
        types.append(OTHER_KEY)
        type_labels[OTHER_KEY] = 'Other'
    if seen_other_status:
        statuses.append(OTHER_KEY)
        status_labels[OTHER_KEY] = 'Other'
    if seen_other_payment:
        payments.append(OTHER_KEY)
        payment_labels[OTHER_KEY] = 'Other'

    # The margins of the grid: rows summed across, columns summed down. Derived
    # rather than counted separately, so a total can never disagree with the
    # cells printed above it. `types` and `statuses` now include Other where it
    # was seen, so nothing counted is left out of a total.
    by_type    = {t: sum(grid[t][st] for st in statuses) for t in types}
    by_status  = {st: sum(grid[t][st] for t in types) for st in statuses}
    by_payment = {pm: sum(pay_grid[t][pm] for t in types) for pm in payments}   # from pay_grid, so it totals the same rows by the other axis
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
        'total':          sum(by_type.values()),   # summed off a margin, not counted again — it cannot drift from the grid
    }


class RegistrationSummaryView(APIView):
    """Headline counts for the registration management page — admin/CDSO."""
    permission_classes = [IsAdminOrCdso]

    def get(self, request):
        # Unfiltered, unlike the PDF below: this feeds the page's tiles, which
        # describe everything on record rather than a chosen slice.
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
            # The flat payment totals, for when no status is selected. The
            # per-status splits above are what a selected status reads from.
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
        # Filtered by the same helper the row-by-row reports use, so a summary
        # and a listing downloaded from the same screen describe the same set.
        qs, desc = _filter_registrations_report(request)
        counts = _registration_counts(qs)        # no 5,000 cap here: this counts rather than lists, so size does not grow the file
        type_labels = counts['type_labels']

        def section(axis_keys, axis_labels, cells, totals):
            """One type-by-axis table: a row per registrant type, then the
            all-types row. The type column carries the label, so the rest of
            the landscape width is shared evenly by the count columns."""
            headers = (['Registrant Type']
                       + [axis_labels.get(k, k) for k in axis_keys] + ['Total'])
            # A row per type, each ending in that type's own total — so every
            # row reads across to a figure the reader can check.
            rows = [[type_labels.get(t, t)]
                    + [cells[t][k] for k in axis_keys]
                    + [counts['by_type'][t]]
                    for t in counts['types']]
            # And the margin row. Its last cell is the grand total, which is the
            # one number both this row and the Total column have to agree on.
            rows.append(['ALL TYPES']
                        + [totals[k] for k in axis_keys] + [counts['total']])
            n = len(axis_keys)
            # 267mm is A4 landscape less report_utils' 15mm margins. 60 for the
            # type name and 30 for the total are fixed; whatever is left is
            # shared evenly, so the table fills the page whether there are four
            # status columns or nine. `if n else 0` guards the division for an
            # axis with no keys at all.
            return headers, rows, [60] + [(267 - 60 - 30) / n if n else 0] * n + [30]

        # The same function twice, once per axis: status is the main table and
        # payment is the extra one appended below it.
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


# The last group in this file: who the CDSO is expecting, and when.
class ScheduledVisitListCreateView(APIView):
    """Advance coordination for visitors/suppliers — lets CDSO log who is
    expected on a given day, before they show up at the gate."""
    permission_classes = [IsAdminRole]

    def get(self, request):
        visits = ScheduledVisit.objects.select_related('supplier').all()   # the supplier's name renders on every row, so join it in
        # Any truthy value turns the filter on — the caller is the CDSO screen
        # sending `?upcoming=1`, so the parameter's value is never inspected.
        upcoming_only = request.query_params.get('upcoming')
        if upcoming_only:
            # Two conditions, because "upcoming" means both: still to come, and
            # not already ticked off. Someone expected today who has not turned
            # up yet is still upcoming, which is why it is >= and not >.
            visits = visits.filter(expected_date__gte=timezone.localdate(), is_arrived=False)
        return Response(ScheduledVisitSerializer(visits, many=True).data)   # soonest first, from the model's ordering

    def post(self, request):
        visitor_name = (request.data.get('visitor_name') or '').strip()
        expected_date = request.data.get('expected_date')   # taken raw — see the note on the create below
        category = request.data.get('category') or ScheduledVisit.Category.OTHER

        if not visitor_name:
            return Response({'visitor_name': 'Name is required.'}, status=400)
        # Presence only. Whether it is a DATE is never checked here, unlike
        # every other date in this file, which is parsed with strptime and
        # answered with a 400 — see _clean_period_payload just above, or
        # _parse_event_time. The consequence is spelled out at the create.
        if not expected_date:
            return Response({'expected_date': 'Expected date is required.'}, status=400)
        if category not in ScheduledVisit.Category.values:
            return Response({'category': 'Invalid category.'}, status=400)

        # Optional: a visitor need not be tied to a supplier at all, and the
        # FK is SET_NULL, so one that is may outlive the company record.
        supplier = None
        supplier_id = request.data.get('supplier')
        if supplier_id:
            supplier = get_object_or_404(Supplier, pk=supplier_id)   # a named supplier that does not exist is a 404, not a silently unlinked visit

        visit = ScheduledVisit.objects.create(
            visitor_name=visitor_name,
            category=category,
            supplier=supplier,
            plate_number=_normalize_plate(request.data.get('plate_number') or ''),   # normalised so a gate scan can match it; blank is allowed, the vehicle may not be known yet
            purpose=(request.data.get('purpose') or '').strip(),
            # expected_date reaches the DateField as whatever was posted. A
            # well-formed "YYYY-MM-DD" is converted for us; anything else —
            # "21/09/2026", or a word — raises django.core.exceptions
            # .ValidationError here, which DRF does not translate, so the
            # caller gets a 500 rather than the 400 every other date input in
            # this file returns. Recorded, not changed: this pass comments code.
            expected_date=expected_date,
            notes=(request.data.get('notes') or '').strip(),
        )
        audit(request, AuditLog.Action.RECORD_CREATED,
              f"Scheduled visit added | {visitor_name} expected {expected_date} | By: {request.user.full_name}")
        return Response(ScheduledVisitSerializer(visit).data, status=201)


# Tick a visit off, or drop it.
class ScheduledVisitDetailView(APIView):
    permission_classes = [IsAdminRole]

    def patch(self, request, pk):
        visit = get_object_or_404(ScheduledVisit, pk=pk)
        # Only is_arrived is honoured. Anything else in the payload — a new
        # date, a different name — is read and ignored, so a visit is corrected
        # by deleting it and logging it again rather than by editing it.
        if 'is_arrived' in request.data:
            visit.is_arrived = bool(request.data['is_arrived'])
        # Saved unconditionally, so a PATCH naming nothing still writes the row
        # back unchanged. And no audit line is written — the only staff write
        # in this section without one (every Supplier, SupplierPlate, period
        # and ScheduledVisit endpoint around it audits), so ticking somebody
        # off as arrived leaves no trace of who did it. Recorded, not changed.
        visit.save()
        return Response(ScheduledVisitSerializer(visit).data)

    def delete(self, request, pk):
        visit = get_object_or_404(ScheduledVisit, pk=pk)
        desc = f"{visit.visitor_name} ({visit.expected_date})"   # captured before the row goes, so the audit line can still name them
        # A real delete. A scheduled visit is an expectation, not a record of
        # anything that happened — what actually happened at the gate is on the
        # scan logs, which this does not touch.
        visit.delete()
        audit(request, AuditLog.Action.RECORD_DELETED,
              f"Scheduled visit removed | {desc} | By: {request.user.full_name}")
        return Response(status=204)