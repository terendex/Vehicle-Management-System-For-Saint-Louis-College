"""
Background daemon threads that read an IP camera RTSP stream and update
ParkingSpace occupancy directly in the DB.

One ParkingCameraThread per zone.  The thread stays alive until stop() is
called or the process exits (daemon=True).

Public API (called from views.py):
    start(zone_id, rtsp_url)  → ParkingCameraThread
    stop(zone_id)
    get_thread(zone_id)       → ParkingCameraThread | None
    status_dict()             → {zone_id: is_alive}
"""

import logging
import threading
import time

import cv2
import numpy as np

log = logging.getLogger(__name__)

_OPEN_CAP_LOCK = threading.Lock()

OCCUPY_THR = 4   # consecutive frames with vehicle inside → mark occupied
FREE_THR   = 20  # consecutive frames without vehicle    → mark free

# Sampling density for the space-coverage test (see _space_coverage).
COVERAGE_GRID = 6

# Fraction of a bay a vehicle must cover before that bay counts as taken.
# 0.35 tolerates a car parked off-centre or partly hidden by the one in front,
# while staying above the incidental overlap a neighbouring car's box produces.
OCCUPY_COVERAGE = 0.35

# A single vehicle covering this much of two or more bays is straddling.
# Tuned against the case this exists for: a car parked across a line typically
# reads ~1/3 of one bay and ~2/3 of the other, so a 0.5 threshold missed real
# straddles entirely. A correctly parked car reads near zero on its neighbour
# — its box has to genuinely intrude to reach 0.30.
DOUBLE_PARK_COVERAGE = 0.30

# Consecutive frames a straddle must persist before it is reported. A car is
# briefly across two bays every time it manoeuvres into one, so without this
# every normal park would raise a violation.
DOUBLE_PARK_FRAMES = 60  # ~6s at 10fps

# How long a zone's space layout is reused before re-reading it from the DB.
# The loop runs at ~10fps; re-fetching the layout every frame meant 20 queries
# a second per zone, and against Neon (~40ms per round trip) the DB alone
# needed more wall-clock time than the frame interval allowed. Space geometry
# only changes when an admin edits the zone, so a few seconds of staleness is
# invisible while making the per-frame DB cost effectively zero.
LAYOUT_TTL_SECONDS = 5.0

# JPEG quality for the shared MJPEG preview encode.
STREAM_JPEG_QUALITY = 75

_cameras: dict[int, "ParkingCameraThread"] = {}
_lock = threading.Lock()


def _point_in_polygon(x: float, y: float, points: list[list[float]]) -> bool:
    """Standard ray-casting point-in-polygon test (points are normalised [x, y] pairs)."""
    inside = False
    x1, y1 = points[-1]
    for x2, y2 in points:
        if (y1 > y) != (y2 > y):
            x_at_y = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < x_at_y:
                inside = not inside
        x1, y1 = x2, y2
    return inside


def _space_coverage(sp, box: dict, grid: int = COVERAGE_GRID) -> float:
    """Fraction of the parking space's area that falls inside `box` (0.0–1.0).

    Why not the old centroid test: a centroid answers "is the middle of the
    detection inside this bay", which cannot express a car straddling two bays.
    One point is in exactly one polygon, so double parking was undetectable by
    construction — the reason this function exists.

    Sampling a grid rather than clipping polygons keeps it dependency-free and
    handles the pen-tool's arbitrary polygons and the box tool's rectangles
    with the same code. At grid=6 that is 36 point tests per space per vehicle,
    which is noise next to the YOLO inference that produced the box.
    """
    bx1, by1 = box["x"], box["y"]
    bx2, by2 = bx1 + box["width"], by1 + box["height"]

    # Sample points spread over the space, at cell centres so the edges are not
    # over-weighted.
    if sp.points:
        xs = [p[0] for p in sp.points]
        ys = [p[1] for p in sp.points]
        sx1, sx2, sy1, sy2 = min(xs), max(xs), min(ys), max(ys)
    else:
        sx1, sx2, sy1, sy2 = sp.x1, sp.x2, sp.y1, sp.y2

    if sx2 <= sx1 or sy2 <= sy1:
        return 0.0

    inside_space = 0
    inside_both  = 0
    for i in range(grid):
        px = sx1 + (sx2 - sx1) * (i + 0.5) / grid
        for j in range(grid):
            py = sy1 + (sy2 - sy1) * (j + 0.5) / grid
            # For polygons the bounding box is not the space, so points outside
            # the polygon must not count toward the denominator.
            if sp.points and not _point_in_polygon(px, py, sp.points):
                continue
            inside_space += 1
            if bx1 <= px <= bx2 and by1 <= py <= by2:
                inside_both += 1

    return (inside_both / inside_space) if inside_space else 0.0


# ── Public API ─────────────────────────────────────────────────────────────────

def start(zone_id: int, rtsp_url: str) -> "ParkingCameraThread":
    with _lock:
        t = _cameras.get(zone_id)
        if t and t.is_alive():
            return t
        t = ParkingCameraThread(zone_id, rtsp_url)
        t.start()
        _cameras[zone_id] = t
    return t


def stop(zone_id: int) -> None:
    with _lock:
        t = _cameras.pop(zone_id, None)
    if t:
        t.stop()


def get_thread(zone_id: int) -> "ParkingCameraThread | None":
    return _cameras.get(zone_id)


def status_dict() -> dict[int, bool]:
    with _lock:
        return {zid: t.is_alive() for zid, t in list(_cameras.items())}


def all_threads() -> dict[int, "ParkingCameraThread"]:
    with _lock:
        return dict(_cameras)


# ── Camera thread ───────────────────────────────────────────────────────────────

class ParkingCameraThread(threading.Thread):
    def __init__(self, zone_id: int, rtsp_url: str):
        super().__init__(daemon=True, name=f"parking-cam-{zone_id}")
        self.zone_id  = zone_id
        self.rtsp_url = rtsp_url
        self._stop    = threading.Event()
        self._lock    = threading.Lock()
        self._latest: np.ndarray | None = None
        self._hyst:   dict[int, int]    = {}

        # Straddle streaks keyed by the tuple of bay ids a single vehicle
        # covers, and the alerts those streaks have raised.
        self._straddle: dict[tuple[int, ...], int]  = {}
        self._alerts:   dict[tuple[int, ...], dict] = {}
        # Boxed evidence JPEG captured at detection time (car box + straddled
        # bays drawn), so a guard attributing the plate later gets the scene as
        # it was, not a frame after the car has moved.
        self._alert_evidence: dict[tuple[int, ...], bytes] = {}

        # Frame sequence number, so viewers can tell whether the cached JPEG
        # still corresponds to the newest frame.
        self._seq         = 0
        self._jpeg        = None
        self._jpeg_seq    = -1

        # Cached zone layout (see LAYOUT_TTL_SECONDS).
        self._spaces      = []
        self._spaces_at   = 0.0

    def stop(self) -> None:
        self._stop.set()

    @property
    def running(self) -> bool:
        return self.is_alive() and not self._stop.is_set()

    def get_frame(self):
        """Return a copy of the latest decoded frame (thread-safe)."""
        with self._lock:
            return None if self._latest is None else self._latest.copy()

    def get_jpeg(self) -> bytes | None:
        """Latest frame as JPEG bytes, encoded at most once per frame.

        Every MJPEG viewer used to copy the frame and encode it itself, so the
        CPU cost scaled with the number of people watching — three admins on
        one zone meant the same image encoded three times, 20 times a second.
        The encode now happens once and all viewers share the result, so the
        cost is flat in viewer count. Nothing is encoded while nobody watches.
        """
        with self._lock:
            if self._latest is None:
                return None
            if self._jpeg_seq == self._seq:
                return self._jpeg          # already encoded by another viewer
            frame, seq = self._latest, self._seq

        # Encode outside the lock — it takes milliseconds and must not block
        # the capture thread from publishing the next frame.
        ok, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, STREAM_JPEG_QUALITY])
        if not ok:
            return None
        data = buf.tobytes()

        with self._lock:
            # Only publish if this is still the newest frame; a racing viewer
            # may have encoded a later one in the meantime.
            if seq == self._seq:
                self._jpeg, self._jpeg_seq = data, seq
        return data

    # ── Thread body ───────────────────────────────────────────────────────────

    def run(self) -> None:
        log.info("[ParkingCam] Starting zone %d -> %s", self.zone_id, self.rtsp_url)
        cap = self._open_cap()

        while not self._stop.is_set():
            ret, frame = cap.read()
            if not ret:
                log.warning("[ParkingCam] Read failed - reconnecting zone %d", self.zone_id)
                cap.release()
                time.sleep(2)
                cap = self._open_cap()
                continue

            with self._lock:
                self._latest = frame
                self._seq += 1      # invalidates the cached JPEG

            try:
                self._process_frame(frame)
            except Exception as exc:
                log.error("[ParkingCam] Processing error zone %d: %s", self.zone_id, exc)

            time.sleep(0.1)  # ~10 fps

        cap.release()
        log.info("[ParkingCam] Stopped zone %d", self.zone_id)

    def _open_cap(self) -> cv2.VideoCapture:
        import os
        with _OPEN_CAP_LOCK:
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
                "rtsp_transport;tcp"
                "|buffer_size;2097152"
                "|stimeout;5000000"
                "|timeout;5000000"
                "|threads;1"
                "|err_detect;ignore_err"
                "|fflags;discardcorrupt"
            )
            cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 10000)
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000)
        return cap

    def _load_spaces(self):
        """The zone's placed spaces, re-read from the DB at most every TTL.

        Returns the previous list if the refresh fails, so a transient DB blip
        doesn't blind the detector.
        """
        now = time.monotonic()
        if self._spaces_at and (now - self._spaces_at) < LAYOUT_TTL_SECONDS:
            return self._spaces

        from django.db import close_old_connections
        from vehicles.models import ParkingSpace

        close_old_connections()
        try:
            # One query, straight at the spaces — fetching the zone first and
            # then filtering its prefetched `spaces` cost two queries and threw
            # the prefetch away.
            self._spaces = list(
                ParkingSpace.objects.filter(zone_id=self.zone_id, x1__isnull=False)
            )
            self._spaces_at = now
        except Exception as exc:
            log.warning("[ParkingCam] Layout refresh failed zone %d: %s", self.zone_id, exc)
        return self._spaces

    def _process_frame(self, frame: np.ndarray) -> None:
        # Vehicle model only. This used to call detect_plates(), which ran the
        # plate detector on every frame of every zone and then let plate boxes
        # decide occupancy — see detect_vehicles() for why that was wrong.
        from scanning.ml.detection import detect_vehicles

        detections = detect_vehicles(frame)

        spaces = self._load_spaces()
        if not spaces:
            return

        # coverage[space_id][vehicle_index] — computed once, used for both the
        # occupancy decision and the straddle check.
        covered_by: dict[int, list[int]] = {}

        for sp in spaces:
            best = 0.0
            hits: list[int] = []
            for vi, det in enumerate(detections):
                cov = _space_coverage(sp, det["bbox"])
                if cov > best:
                    best = cov
                if cov >= OCCUPY_COVERAGE:
                    hits.append(vi)
            covered_by[sp.id] = hits

            hit  = best >= OCCUPY_COVERAGE
            prev = self._hyst.get(sp.id, 0)

            if hit:
                nxt = min(prev + 1, OCCUPY_THR)
                self._hyst[sp.id] = nxt
                if not sp.is_occupied and nxt >= OCCUPY_THR:
                    self._set_occupied(sp, True)
            else:
                nxt = max(prev - 1, -FREE_THR)
                self._hyst[sp.id] = nxt
                if sp.is_occupied and nxt <= -FREE_THR:
                    self._set_occupied(sp, False)

        self._check_double_parking(spaces, detections, frame)

    # ── Double parking ────────────────────────────────────────────────────────

    def _check_double_parking(self, spaces, detections, frame=None) -> None:
        """Flag a vehicle whose box substantially covers two or more bays.

        This is the case the old centroid test could not express at all: a
        centroid falls in exactly one polygon, so a car parked across the line
        looked like an ordinary single occupancy.
        """
        straddling: dict[tuple[int, ...], dict] = {}
        blocked: set[int] = set()

        for det in detections:
            covered = [sp for sp in spaces
                       if _space_coverage(sp, det["bbox"]) >= DOUBLE_PARK_COVERAGE]
            if len(covered) >= 2:
                key = tuple(sorted(sp.id for sp in covered))
                straddling[key] = det          # keep the box for plate lookup
                blocked.update(sp.id for sp in covered)

        # A straddled bay is unusable even if the car covers less of it than
        # OCCUPY_COVERAGE — nobody else can park there. Without this the
        # partly-covered side kept showing as free and the count overstated
        # capacity.
        for sp in spaces:
            if sp.id in blocked and not sp.is_occupied:
                self._hyst[sp.id] = OCCUPY_THR
                self._set_occupied(sp, True)

        # Age each active straddle; drop any that stopped happening so a car
        # manoeuvring through a bay does not accumulate toward the threshold.
        # The alert clears with it, so the banner reflects what is happening now
        # rather than accumulating stale warnings.
        for key in list(self._straddle):
            if key not in straddling:
                del self._straddle[key]
                with self._lock:
                    self._alerts.pop(key, None)
                    self._alert_evidence.pop(key, None)

        for key, det in straddling.items():
            n = self._straddle.get(key, 0) + 1
            self._straddle[key] = n
            if n == DOUBLE_PARK_FRAMES:      # == so it reports once per episode
                self._report_double_parking(key, det, frame)

    def _render_double_park_evidence(self, frame, det_bbox, spaces) -> "bytes | None":
        """Draw the offending car's box (red) and the straddled bays (amber) onto
        a copy of the frame and return JPEG bytes — the evidence photo a guard and
        the owner see. Falls back to the plain latest JPEG on any error."""
        try:
            img = frame.copy()
            h, w = img.shape[:2]
            for sp in spaces:
                pts = getattr(sp, 'points', None)
                if pts:
                    poly = np.array([[int(x * w), int(y * h)] for x, y in pts], dtype=np.int32)
                    cv2.polylines(img, [poly], True, (0, 191, 255), 2)
                elif sp.x1 is not None:
                    cv2.rectangle(img, (int(sp.x1 * w), int(sp.y1 * h)),
                                  (int(sp.x2 * w), int(sp.y2 * h)), (0, 191, 255), 2)
            if det_bbox:
                x1, y1 = int(det_bbox["x"] * w), int(det_bbox["y"] * h)
                x2 = int((det_bbox["x"] + det_bbox["width"]) * w)
                y2 = int((det_bbox["y"] + det_bbox["height"]) * h)
                cv2.rectangle(img, (x1, y1), (x2, y2), (0, 0, 255), 3)
                cv2.putText(img, "DOUBLE PARKING", (x1, max(20, y1 - 8)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            ok, buf = cv2.imencode('.jpg', img)
            return buf.tobytes() if ok else self.get_jpeg()
        except Exception:
            log.exception("[ParkingCam] evidence render failed zone %d", self.zone_id)
            return self.get_jpeg()

    def pop_alert(self, space_ids) -> "bytes | None":
        """Remove a double-parking alert (a guard has handled it) and return its
        captured boxed-evidence JPEG, or the latest frame if none was stored."""
        key = tuple(sorted(int(s) for s in space_ids))
        with self._lock:
            self._alerts.pop(key, None)
            evidence = self._alert_evidence.pop(key, None)
        # get_jpeg() takes self._lock, so fall back to it outside the block.
        return evidence or self.get_jpeg()

    def _read_plate(self, frame, bbox: dict) -> str:
        """Best-effort plate read from inside a straddling vehicle's box.

        Only the offending car's crop is OCR'd, not the whole frame, so a
        neighbouring vehicle's plate can never be the one that gets fined.
        Returns '' when nothing legible is found, which is the normal case for
        an overhead bay camera — the caller must treat that as "unattributed",
        never as a reason to guess.
        """
        if frame is None:
            return ''
        try:
            h, w = frame.shape[:2]
            x1 = max(0, int(bbox["x"] * w))
            y1 = max(0, int(bbox["y"] * h))
            x2 = min(w, int((bbox["x"] + bbox["width"]) * w))
            y2 = min(h, int((bbox["y"] + bbox["height"]) * h))
            if x2 - x1 < 40 or y2 - y1 < 40:
                return ''
            crop = frame[y1:y2, x1:x2]

            # read_plate() is the same detect+OCR+validate entry point the gate
            # scanner uses, so a plate read here is held to the same standard.
            from scanning.ml.reader import read_plate

            ok, buf = cv2.imencode('.jpg', crop)
            if not ok:
                return ''
            for res in read_plate(buf.tobytes()):
                text = (res.get("plate_text") or '').strip().upper()
                if text:
                    return text
        except Exception as exc:
            log.warning("[ParkingCam] plate read failed zone %d: %s", self.zone_id, exc)
        return ''

    def _report_double_parking(self, space_ids: tuple[int, ...], det=None, frame=None) -> None:
        """Record a sustained straddle, attribute it if the plate is readable.

        A Violation needs a Vehicle FK, so one is only issued when the plate
        inside the offending car's own box resolves to a registered vehicle.
        When it does not — the normal case for an overhead camera — the event
        is still raised as an alert for a guard to act on. It is never pinned
        on a guess: an unreadable plate must not become a fine against whoever
        happens to be parked nearby.
        """
        from django.db import close_old_connections
        from django.utils import timezone

        codes, space_objs = [], []
        try:
            close_old_connections()
            from vehicles.models import ParkingSpace
            space_objs = list(ParkingSpace.objects.filter(id__in=space_ids))
            codes = [s.space_number for s in space_objs]
        except Exception as exc:
            log.warning("[ParkingCam] Could not resolve space numbers %s: %s", space_ids, exc)

        label = ", ".join(codes) if codes else ", ".join(str(i) for i in space_ids)

        # Render the boxed evidence photo once, at detection, from the frame that
        # triggered the alert — used for the auto-violation and stashed for a
        # guard who attributes the plate later (by then the car may have moved).
        evidence = None
        if frame is not None:
            evidence = self._render_double_park_evidence(
                frame, det["bbox"] if det is not None else None, space_objs)
        if not evidence:
            evidence = self.get_jpeg()   # get_jpeg() takes self._lock — call before acquiring it
        with self._lock:
            self._alert_evidence[space_ids] = evidence

        plate, vehicle, violation_id = '', None, None
        if det is not None:
            plate = self._read_plate(frame, det["bbox"])

        if plate:
            try:
                close_old_connections()
                from vehicles.models import Vehicle
                vehicle = Vehicle.resolve(plate)  # plate or conduction number
            except Exception as exc:
                log.warning("[ParkingCam] vehicle lookup failed for %s: %s", plate, exc)

        if vehicle is not None:
            try:
                from violations.models import Violation
                from scanning.views import _auto_log_violation
                # Reuses the gate path: one per vehicle per day, offense
                # numbering, 3rd-offense fee, evidence image and owner email.
                _auto_log_violation(
                    vehicle,
                    f"Double parking detected by camera across bays {label}",
                    vtype=Violation.Type.DOUBLE_PARKING,
                    evidence_bytes=evidence or self.get_jpeg(),
                )
                violation_id = (Violation.objects
                                .filter(vehicle=vehicle,
                                        violation_type=Violation.Type.DOUBLE_PARKING)
                                .order_by('-issued_at')
                                .values_list('id', flat=True).first())
                log.warning("[ParkingCam] Double parking zone %d bays %s -> violation for %s",
                            self.zone_id, label, plate)
            except Exception:
                log.exception("[ParkingCam] failed to auto-log double-parking violation")
        else:
            log.warning("[ParkingCam] Double parking in zone %d across bays: %s "
                        "(plate %s - unattributed, needs a guard)",
                        self.zone_id, label, plate or 'unreadable')

        with self._lock:
            self._alerts[space_ids] = {
                "zone_id":      self.zone_id,
                "space_ids":    list(space_ids),
                "spaces":       codes,
                "plate":        plate,
                "attributed":   vehicle is not None,
                "violation_id": violation_id,
                "detected_at":  timezone.now().isoformat(),
            }

        try:
            from realtime.broadcast import broadcast_change
            broadcast_change('parkingspace', 'double_parking',
                             zone_id=self.zone_id, spaces=codes,
                             plate=plate, attributed=vehicle is not None)
        except Exception:
            log.exception("[ParkingCam] double-parking broadcast failed")

    def get_alerts(self) -> list[dict]:
        """Active double-parking alerts for this zone."""
        with self._lock:
            return list(self._alerts.values())

    def _set_occupied(self, sp, occupied: bool) -> None:
        """Persist an occupancy transition. Only fires on a state change, so
        this is the sole per-frame DB write path and it stays idle at rest."""
        from django.db import close_old_connections

        sp.is_occupied = occupied
        sp.occupied_by = "CAMERA" if occupied else ""
        try:
            close_old_connections()
            sp.save(update_fields=["is_occupied", "occupied_by"])
            self._hyst[sp.id] = 0
        except Exception as exc:
            # Roll the in-memory flag back so the next frames retry the write
            # instead of believing a state that never reached the database.
            sp.is_occupied = not occupied
            log.error("[ParkingCam] Occupancy save failed zone %d space %s: %s",
                      self.zone_id, sp.id, exc)
