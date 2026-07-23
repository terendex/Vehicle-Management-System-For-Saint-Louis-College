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
        log.info("[ParkingCam] Starting zone %d → %s", self.zone_id, self.rtsp_url)
        cap = self._open_cap()

        while not self._stop.is_set():
            ret, frame = cap.read()
            if not ret:
                log.warning("[ParkingCam] Read failed — reconnecting zone %d", self.zone_id)
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
        from scanning.ml.detection import detect_plates

        # try_rotation=False: the rotation fallback only fires when no plate was
        # found, which for a parking lot is most frames — it would spend six
        # extra full inferences (each with its own warp + preprocess) per frame
        # at 10fps hunting for a plate this loop never reads. Occupancy is
        # decided from vehicle boxes, which the vehicle model returns directly.
        detections = detect_plates(frame, try_rotation=False)

        spaces = self._load_spaces()
        if not spaces:
            return

        for sp in spaces:
            # bbox from detect_plates is already normalised 0-1 {x, y, width, height}
            hit = False
            for det in detections:
                b  = det["bbox"]
                cx = b["x"] + b["width"]  / 2
                cy = b["y"] + b["height"] / 2
                if sp.points:
                    hit = _point_in_polygon(cx, cy, sp.points)
                else:
                    hit = sp.x1 <= cx <= sp.x2 and sp.y1 <= cy <= sp.y2
                if hit:
                    break

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
