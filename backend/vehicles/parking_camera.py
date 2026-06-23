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

OCCUPY_THR = 4   # consecutive frames with vehicle inside → mark occupied
FREE_THR   = 20  # consecutive frames without vehicle    → mark free

_cameras: dict[int, "ParkingCameraThread"] = {}
_lock = threading.Lock()


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

    def stop(self) -> None:
        self._stop.set()

    @property
    def running(self) -> bool:
        return self.is_alive() and not self._stop.is_set()

    def get_frame(self) -> np.ndarray | None:
        """Return a copy of the latest decoded frame (thread-safe)."""
        with self._lock:
            return None if self._latest is None else self._latest.copy()

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

            try:
                self._process_frame(frame)
            except Exception as exc:
                log.error("[ParkingCam] Processing error zone %d: %s", self.zone_id, exc)

            time.sleep(0.1)  # ~10 fps

        cap.release()
        log.info("[ParkingCam] Stopped zone %d", self.zone_id)

    def _open_cap(self) -> cv2.VideoCapture:
        import os
        # Force TCP transport — V380/most IP cameras are more reliable over TCP than default UDP.
        # timeout=5 s so connect failures don't block the thread forever.
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|timeout;5000000"
        cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _process_frame(self, frame: np.ndarray) -> None:
        from django.db import close_old_connections
        from scanning.ml.detection import detect_plates
        from vehicles.models import ParkingZone

        close_old_connections()
        detections = detect_plates(frame)

        try:
            zone   = ParkingZone.objects.prefetch_related("spaces").get(id=self.zone_id)
            spaces = list(zone.spaces.filter(x1__isnull=False))
        except Exception:
            return

        for sp in spaces:
            # bbox from detect_plates is already normalised 0-1 {x, y, width, height}
            hit = False
            for det in detections:
                b  = det["bbox"]
                cx = b["x"] + b["width"]  / 2
                cy = b["y"] + b["height"] / 2
                if sp.x1 <= cx <= sp.x2 and sp.y1 <= cy <= sp.y2:
                    hit = True
                    break

            prev = self._hyst.get(sp.id, 0)

            if hit:
                nxt = min(prev + 1, OCCUPY_THR)
                self._hyst[sp.id] = nxt
                if not sp.is_occupied and nxt >= OCCUPY_THR:
                    sp.is_occupied = True
                    sp.occupied_by = "CAMERA"
                    sp.save(update_fields=["is_occupied", "occupied_by"])
                    self._hyst[sp.id] = 0
            else:
                nxt = max(prev - 1, -FREE_THR)
                self._hyst[sp.id] = nxt
                if sp.is_occupied and nxt <= -FREE_THR:
                    sp.is_occupied = False
                    sp.occupied_by = ""
                    sp.save(update_fields=["is_occupied", "occupied_by"])
                    self._hyst[sp.id] = 0
