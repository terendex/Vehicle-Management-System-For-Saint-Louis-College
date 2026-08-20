"""
Background daemon threads that read an IP camera RTSP stream and update
ParkingSpace occupancy directly in the DB.

Two layers, because zones and cameras are not the same thing:

    _StreamReader        one per RTSP URL — owns the connection and publishes
                         decoded frames.
    ParkingCameraThread  one per zone — scores bays on the frames it is handed.

A camera may legitimately carry several zones: motorcycle bays and car bays in
one frame are two zones with two vehicle categories and two layouts, watched by
one lens. Zones each opened their own capture, so N zones on a camera meant N
simultaneous RTSP sessions to it — which many units simply refuse (the Yoosee
Y20 among them), leaving the second zone permanently black. Readers are shared
by URL and reference-counted, so that camera is now opened exactly once no
matter how many zones read it.

Both stay alive until stop() is called or the process exits (daemon=True).

Public API (called from views.py):
    start(zone_id, rtsp_url)  → ParkingCameraThread
    stop(zone_id)
    get_thread(zone_id)       → ParkingCameraThread | None
    status_dict()             → {zone_id: is_alive}
    stream_status()           → {rtsp_url: zones_reading_it}
"""

import logging
import threading
import time

import cv2
import numpy as np

from vehicles.vehicle_tracker import VehicleTracker

log = logging.getLogger(__name__)

OCCUPY_THR = 4   # consecutive frames with vehicle inside → mark occupied
FREE_THR   = 20  # consecutive frames without vehicle    → mark free

# Sampling density for the space-coverage test (see _space_coverage).
COVERAGE_GRID = 6

# Fraction of a bay a vehicle must cover before that bay counts as taken.
# 0.35 tolerates a car parked off-centre or partly hidden by the one in front,
# while staying above the incidental overlap a neighbouring car's box produces.
OCCUPY_COVERAGE = 0.35

# How much of a vehicle must sit inside a bay before that bay counts as one the
# vehicle is genuinely in. Paired with the bay-side thresholds below, so both
# questions have to agree.
#
# A correctly parked car overhangs its neighbour a little. Against a small or
# angled neighbouring bay that sliver can reach the bay-side straddle threshold
# while being a tiny part of the car — which is how a tidy row reported double
# parking. Requiring a real share of the vehicle too is what separates an
# overhang from a straddle.
#
# Bays do not overlap, so a vehicle's shares across them sum to at most 1: a
# genuine straddle splits roughly 35/65 to 50/50 and clears 0.25 on both sides,
# while an overhang lands near 0.05–0.15 and does not.
DOUBLE_PARK_VEHICLE_SHARE = 0.25

# For occupancy the vehicle side is an alternative, not an extra condition: a
# motorcycle wholly inside a car-sized bay covers little of it but the bay is
# plainly taken.
OCCUPY_VEHICLE_SHARE = 0.80

# A single vehicle covering this much of two or more bays is straddling.
# Tuned against the case this exists for: a car parked across a line typically
# reads ~1/3 of one bay and ~2/3 of the other, so a 0.5 threshold missed real
# straddles entirely. A correctly parked car reads near zero on its neighbour
# — its box has to genuinely intrude to reach 0.30.
DOUBLE_PARK_COVERAGE = 0.30

# ── Dwell thresholds ─────────────────────────────────────────────────────────
#
# How long a vehicle must sit still before the zone commits to a verdict about
# it. Both are measured by vehicle_tracker against the tracked box, so they mean
# the same thing whether the loop is scoring ten times a second or once every
# two seconds — the frame counts these replaced did not.
#
# A car crossing a bay on its way somewhere else, or reversing into a slot, is
# over two bays for several seconds every single time. Waiting for it to stop is
# what separates that from a car genuinely left across the line.

# Both are admin-settable from System Settings; these are the defaults, and the
# values a zone falls back on when the settings row cannot be read. Keep them in
# step with the model field defaults on SystemSettings.

# Stationary this long → the vehicle counts as parked, and the bays it covers
# are claimed.
PARKED_AFTER_SECONDS = 8.0

# Stationary this long *while* covering two or more bays → double parking.
# Longer than PARKED_AFTER_SECONDS on purpose: this one issues a fine, so it
# gets the benefit of the doubt that occupancy does not. The API enforces the
# ordering, so a zone never fines a car before it counts as parked.
DOUBLE_PARK_AFTER_SECONDS = 12.0

# How often the vehicle detector runs when a zone scores occupancy classically.
# Occupancy no longer needs it there, but double parking still does, and a
# straddle must persist seconds before it counts — so running the model ten
# times a second to answer a six-second question is pure waste.
DETECT_INTERVAL_SECONDS = 2.0

# A vehicle unseen for this long is treated as gone. It has to stay well clear
# of the slowest observation cadence — on the classic path the detector runs
# only every DETECT_INTERVAL_SECONDS — or a car would be re-identified as a new
# arrival every cycle and never accumulate any dwell time at all.
TRACK_LOST_SECONDS = max(6.0, DETECT_INTERVAL_SECONDS * 3)

# How long a zone's space layout is reused before re-reading it from the DB.
# The loop runs at ~10fps; re-fetching the layout every frame meant 20 queries
# a second per zone, and against Neon (~40ms per round trip) the DB alone
# needed more wall-clock time than the frame interval allowed. Space geometry
# only changes when an admin edits the zone, so a few seconds of staleness is
# invisible while making the per-frame DB cost effectively zero.
LAYOUT_TTL_SECONDS = 5.0

# JPEG quality for the shared MJPEG preview encode.
STREAM_JPEG_QUALITY = 75

# How long a zone waits for a new frame before looking up from its stream to
# check whether it has been stopped, or the reader beneath it has died.
FRAME_WAIT_SECONDS = 2.0

# Seconds between reconnect attempts after the stream drops.
RECONNECT_DELAY_SECONDS = 2.0

_cameras: dict[int, "ParkingCameraThread"] = {}
_readers: dict[str, "_StreamReader"] = {}
_lock = threading.Lock()

# The dwell thresholds live on a single settings row shared by every zone, so
# they are read once per process per TTL rather than once per zone. Ten zones
# asking the same question of the same row ten times over is ten Neon round
# trips (~40ms each) to learn one pair of integers.
_dwell_cache: "tuple[float, float] | None" = None
_dwell_at    = 0.0
_dwell_lock  = threading.Lock()


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


def _vehicle_share(sp, box: dict, grid: int = COVERAGE_GRID) -> float:
    """Fraction of the VEHICLE that lies inside this bay (0.0–1.0).

    The mirror of `_space_coverage`, and the two answer different questions.
    Bay coverage asks "how much of this bay is taken"; vehicle share asks "how
    much of this car is in it". Either alone misleads:

      * A car parked correctly still overhangs its neighbour slightly. Against a
        small or angled neighbouring bay that overhang can be 30% of the bay's
        area — enough to read as a straddle — while being only a few percent of
        the car. Vehicle share is what tells the two apart.
      * A motorcycle sitting in a car-sized bay covers little of it and would
        read free, though the whole machine is inside.

    Sampled over the vehicle's box rather than the bay's, so the denominator is
    the vehicle. Bays may be polygons, so membership goes through the same
    ray-casting test the coverage side uses.
    """
    bw, bh = box["width"], box["height"]
    if bw <= 0 or bh <= 0:
        return 0.0

    if not sp.points and (sp.x1 is None or sp.x2 is None):
        return 0.0
    if not sp.points:
        sx1, sx2 = min(sp.x1, sp.x2), max(sp.x1, sp.x2)
        sy1, sy2 = min(sp.y1, sp.y2), max(sp.y1, sp.y2)

    inside = 0
    for i in range(grid):
        px = box["x"] + bw * (i + 0.5) / grid
        for j in range(grid):
            py = box["y"] + bh * (j + 0.5) / grid
            if sp.points:
                if _point_in_polygon(px, py, sp.points):
                    inside += 1
            elif sx1 <= px <= sx2 and sy1 <= py <= sy2:
                inside += 1

    return inside / (grid * grid)


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


def dwell_settings() -> "tuple[float, float] | None":
    """(parked_after, double_park_after) from System Settings, cached per TTL.

    None when the row cannot be read — callers keep whatever they were using
    rather than snapping back to the defaults.
    """
    global _dwell_cache, _dwell_at

    now = time.monotonic()
    with _dwell_lock:
        if _dwell_cache is not None and (now - _dwell_at) < LAYOUT_TTL_SECONDS:
            return _dwell_cache

    from django.db import close_old_connections
    from vehicles.models import SystemSettings

    try:
        close_old_connections()
        row = (SystemSettings.objects
               .filter(pk=1)
               .values('parked_after_seconds', 'double_park_after_seconds')
               .first())
    except Exception as exc:
        log.warning("[ParkingCam] Dwell settings read failed: %s", exc)
        return None

    if not row:
        return None

    values = (float(row['parked_after_seconds']),
              float(row['double_park_after_seconds']))
    with _dwell_lock:
        _dwell_cache, _dwell_at = values, now
    return values


def invalidate_dwell_settings() -> None:
    """Drop the cached thresholds so the next read goes to the database.

    Called when an admin saves System Settings, so the change lands on the next
    frame in this process instead of up to a TTL later. Other processes still
    get it within the TTL, which is what bounds the staleness in a deployment
    running more than one worker.
    """
    global _dwell_cache
    with _dwell_lock:
        _dwell_cache = None


def stream_status() -> dict[str, int]:
    """{rtsp_url: how many zones are reading it} — one entry per open capture.

    The count is what makes the sharing visible: three zones on one camera is
    one entry reading 3, not three connections.
    """
    with _lock:
        return {url: r.refs for url, r in _readers.items()}


# ── Shared stream readers ──────────────────────────────────────────────────────

def _acquire_reader(url: str) -> "_StreamReader":
    """The reader for `url`, started if needed, with this caller counted in."""
    with _lock:
        r = _readers.get(url)
        # A reader that has been told to stop is finishing its last frame and
        # must not be handed out again, or the zone taking it would inherit a
        # capture about to close.
        if r is None or r.stopping or not r.is_alive():
            r = _StreamReader(url)
            _readers[url] = r
            r.start()
        r.refs += 1
        return r


def _release_reader(reader: "_StreamReader") -> None:
    """Drop one zone's claim; close the capture once the last zone lets go."""
    with _lock:
        reader.refs -= 1
        if reader.refs > 0:
            return
        if _readers.get(reader.url) is reader:
            del _readers[reader.url]
    reader.stop()


class _StreamReader(threading.Thread):
    """One RTSP connection, shared by every zone watching that URL.

    Frames are published, not queued: a reader keeps only the newest one, so a
    zone that falls behind skips ahead instead of accumulating a backlog. That
    is what lets zones score at their own pace (~10fps) off a camera sending
    frames at whatever rate it likes, and it is why two zones sharing a camera
    cannot slow each other down.
    """

    def __init__(self, url: str):
        super().__init__(daemon=True, name=f"parking-stream-{url[-24:]}")
        self.url  = url
        self.refs = 0                    # guarded by the module-level _lock
        self._stop = threading.Event()
        # A condition, not a plain lock: zones block here until a frame arrives
        # rather than polling, so an idle stream costs nothing.
        self._cond = threading.Condition()
        self._latest: np.ndarray | None = None
        self._seq      = 0
        self._jpeg     = None
        self._jpeg_seq = -1

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def stop(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()      # release anyone mid-wait

    def _open(self):
        """Open the stream with whichever backend can decode it.

        Deliberately not pinned to `rtsp_transport;tcp`: a fair number of
        cameras answer a TCP SETUP with a UDP transport, and FFmpeg then gives
        up with "Nonmatching transport in server reply", leaving the zone
        permanently black. `open_capture` lets FFmpeg negotiate, and falls back
        to the system FFmpeg when OpenCV's bundled 4.4 cannot decode the stream.
        """
        from vehicles.ffmpeg_capture import open_capture

        return open_capture(self.url)

    def run(self) -> None:
        log.info("[ParkingStream] Opening %s", self.url)
        cap = self._open()
        try:
            while not self._stop.is_set():
                ret, frame = cap.read()
                if not ret:
                    log.warning("[ParkingStream] Read failed — reconnecting %s", self.url)
                    cap.release()
                    if self._stop.wait(RECONNECT_DELAY_SECONDS):
                        break
                    cap = self._open()
                    continue

                with self._cond:
                    self._latest = frame
                    self._seq   += 1     # invalidates the cached JPEG
                    self._cond.notify_all()
        except Exception:
            # Without this the zones above would wait on a reader that is never
            # coming back. They watch is_alive() and rebuild one.
            log.exception("[ParkingStream] Reader crashed for %s", self.url)
        finally:
            cap.release()
            self._stop.set()
            with self._cond:
                self._cond.notify_all()
            log.info("[ParkingStream] Closed %s", self.url)

    def wait_for_frame(self, after_seq: int, timeout: float = FRAME_WAIT_SECONDS):
        """Block for the newest frame later than `after_seq`.

        Returns (frame, seq), or (None, after_seq) if the wait timed out or the
        reader shut down.

        A single zone gets the published array as-is: cap.read() allocates a
        fresh one each time, so the frame being scored is never the frame being
        written. Once a second zone joins they would be handed the *same* array,
        and one zone drawing on it — evidence rendering, an in-place OpenCV op
        deep in the detector — would corrupt what the other is scoring. So a
        shared reader hands out copies. One memcpy per zone per scored frame is
        far cheaper than the second RTSP decode this whole change removes, and
        the single-zone path, which is the common one, pays nothing.
        """
        deadline = time.monotonic() + timeout
        with self._cond:
            while self._seq <= after_seq and not self._stop.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None, after_seq
                self._cond.wait(remaining)
            if self._latest is None or self._seq <= after_seq:
                return None, after_seq
            # refs is only ever incremented before a zone's first wait, so a
            # zone that can reach this line is already counted in.
            frame = self._latest.copy() if self.refs > 1 else self._latest
            return frame, self._seq

    def get_frame(self):
        """A copy of the latest decoded frame (thread-safe)."""
        with self._cond:
            return None if self._latest is None else self._latest.copy()

    def get_jpeg(self) -> bytes | None:
        """Latest frame as JPEG bytes, encoded at most once per frame.

        Every MJPEG viewer used to copy the frame and encode it itself, so the
        CPU cost scaled with the number of people watching — three admins on one
        zone meant the same image encoded three times, 20 times a second. The
        encode now happens once per frame and all viewers share the result, so
        the cost is flat in viewer count, and now flat across zones sharing the
        camera too. Nothing is encoded while nobody watches.
        """
        with self._cond:
            if self._latest is None:
                return None
            if self._jpeg_seq == self._seq:
                return self._jpeg        # already encoded by another viewer
            frame, seq = self._latest, self._seq

        # Encode outside the lock — it takes milliseconds and must not block the
        # reader from publishing the next frame.
        ok, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, STREAM_JPEG_QUALITY])
        if not ok:
            return None
        data = buf.tobytes()

        with self._cond:
            # Only publish if this is still the newest frame; a racing viewer may
            # have encoded a later one in the meantime.
            if seq == self._seq:
                self._jpeg, self._jpeg_seq = data, seq
        return data


# ── Camera thread ───────────────────────────────────────────────────────────────

class ParkingCameraThread(threading.Thread):
    def __init__(self, zone_id: int, rtsp_url: str):
        super().__init__(daemon=True, name=f"parking-cam-{zone_id}")
        self.zone_id  = zone_id
        self.rtsp_url = rtsp_url
        self._stop    = threading.Event()
        self._lock    = threading.Lock()
        # The shared capture this zone reads from, attached for the life of
        # run(). None outside it — nothing may assume a frame source exists.
        self._reader: "_StreamReader | None" = None
        self._hyst:   dict[int, int]    = {}

        # Vehicle identity and stillness across frames. Every dwell threshold in
        # this file is measured off it — see vehicle_tracker for why the bay,
        # which is what used to be counted, cannot answer "has it stopped".
        self._tracker = VehicleTracker(lost_after=TRACK_LOST_SECONDS)

        # Live double-parking alerts, keyed by the tuple of bay ids straddled.
        # Keyed by bays rather than by track because that is the identity a
        # guard attributes against later (pop_alert takes space ids), and the
        # car may well be gone by then.
        self._alerts:   dict[tuple[int, ...], dict] = {}
        # Episodes already reported, so one straddle raises one violation however
        # many frames it spans. Held separately from _alerts because a guard
        # clearing an alert must not re-arm the report for a car still sitting
        # there — and because a check must not depend on the reporter having
        # written its own guard condition.
        self._reported: set[tuple[int, ...]] = set()
        # Boxed evidence JPEG captured at detection time (car box + straddled
        # bays drawn), so a guard attributing the plate later gets the scene as
        # it was, not a frame after the car has moved.
        self._alert_evidence: dict[tuple[int, ...], bytes] = {}

        # Cached zone layout (see LAYOUT_TTL_SECONDS).
        self._spaces      = []
        self._spaces_at   = 0.0

        # Cached zone settings — scoring method and which baseline image to use.
        self._cfg         = None
        self._cfg_at      = 0.0

        # Baseline measured once per layout/baseline change, not per frame.
        self._prepared    = None

        # Latest per-bay classic signals, for tuning thresholds against a real
        # camera instead of guessing at them twice.
        self._signals     = {}

        # When the detector last ran, for the classic path's slower cadence.
        self._last_detect = 0.0

        # Dwell thresholds in force. Refreshed from System Settings alongside
        # the zone config; the module constants are what a zone runs on until
        # that first read succeeds, and what it falls back to if it never does.
        self._parked_after      = PARKED_AFTER_SECONDS
        self._double_park_after = DOUBLE_PARK_AFTER_SECONDS

    def stop(self) -> None:
        self._stop.set()

    @property
    def running(self) -> bool:
        return self.is_alive() and not self._stop.is_set()

    # Frames belong to the shared reader, so both of these are pass-throughs.
    # A zone that is not running has no reader and therefore no frame — callers
    # already handle None (the baseline capture refuses, MJPEG skips a beat).
    def get_frame(self):
        """A copy of the latest decoded frame (thread-safe)."""
        reader = self._reader
        return reader.get_frame() if reader else None

    def get_jpeg(self) -> bytes | None:
        """Latest frame as JPEG bytes, encoded at most once per frame and
        shared by every viewer and every zone on this camera."""
        reader = self._reader
        return reader.get_jpeg() if reader else None

    # ── Thread body ───────────────────────────────────────────────────────────

    def run(self) -> None:
        log.info("[ParkingCam] Starting zone %d -> %s", self.zone_id, self.rtsp_url)
        reader       = _acquire_reader(self.rtsp_url)
        self._reader = reader
        last_seq     = -1

        try:
            while not self._stop.is_set():
                frame, last_seq = reader.wait_for_frame(last_seq)
                if frame is None:
                    # Either no new frame within the timeout — normal on a
                    # stuttering camera — or the reader is gone. Only the second
                    # needs acting on, and a fresh reader reopens the capture.
                    if not reader.is_alive() and not self._stop.is_set():
                        log.warning("[ParkingCam] Stream for zone %d ended — reopening",
                                    self.zone_id)
                        _release_reader(reader)
                        reader       = _acquire_reader(self.rtsp_url)
                        self._reader = reader
                        last_seq     = -1
                    continue

                try:
                    self._process_frame(frame)
                except Exception as exc:
                    log.error("[ParkingCam] Processing error zone %d: %s", self.zone_id, exc)

                # Scoring is paced here, not by the camera. Frames are published
                # rather than queued, so sleeping simply skips the ones that
                # arrive meanwhile instead of building a backlog.
                self._stop.wait(0.1)   # ~10 fps
        finally:
            self._reader = None
            _release_reader(reader)

        log.info("[ParkingCam] Stopped zone %d", self.zone_id)

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

    def _detect(self, frame: np.ndarray) -> list:
        """Vehicle boxes for this frame.

        Vehicle model only. This used to call detect_plates(), which ran the
        plate detector on every frame of every zone and then let plate boxes
        decide occupancy — see detect_vehicles() for why that was wrong.

        Per lens, not per frame. A dual-lens camera stacks two views into one
        picture, and running the detector across the join asked the model to
        read a scene no camera ever produced. The boxes come back in full-frame
        coordinates, so the space geometry — placed against the whole frame —
        needs no adjustment. Single-lens cameras are passed straight through and
        pay nothing.
        """
        from scanning.ml.detection import detect_vehicles
        from vehicles.lens_layout import detect_across_lenses

        return detect_across_lenses(frame, detect_vehicles)

    def _detector_hits(self, spaces, vehicles, now: float) -> dict:
        """{space_id: is a parked vehicle occupying this bay} from tracked boxes.

        Taken either way round: enough of the bay is covered, OR enough of a
        vehicle sits inside it. The second clause is what stops a motorcycle in
        a car-sized bay reading free — it covers little of the bay while being
        entirely within it.

        Only vehicles that have settled count. A car driving down the aisle
        crosses several bays on its way past, and each of those used to read as
        taken for as long as the box overlapped — briefly, but long enough for
        the four-frame hysteresis to latch it. Waiting for the vehicle to stop
        means a bay is claimed by a car that parked in it, not by one going by.
        """
        parked = [v for v in vehicles if v.has_settled(now, self._parked_after)]
        hits = {}
        for sp in spaces:
            taken = False
            for v in parked:
                if (_space_coverage(sp, v.bbox) >= OCCUPY_COVERAGE
                        or _vehicle_share(sp, v.bbox) >= OCCUPY_VEHICLE_SHARE):
                    taken = True
                    break
            hits[sp.id] = taken
        return hits

    def _classic_hits(self, frame, spaces, cfg) -> "dict | None":
        """{space_id: occupied} from baseline comparison, or None when this zone
        cannot be scored that way yet.

        Returning None rather than an empty result is what makes the fallback
        safe: a zone switched to 'classic' before anyone captured a baseline
        keeps running on the detector instead of reporting every bay free.
        """
        from vehicles import bay_occupancy

        prepared = self._prepare_baseline(frame, spaces, cfg)
        if prepared is None or not prepared.bays:
            return None

        try:
            signals = bay_occupancy.evaluate(prepared, frame)
        except Exception as exc:
            log.warning("[ParkingCam] classic scoring failed zone %d: %s", self.zone_id, exc)
            return None

        with self._lock:
            self._signals = signals
        # A bay the preparation could not measure is absent from `signals`;
        # `.get` leaves it alone rather than flipping it free on missing data.
        return {sp.id: signals[sp.id]['occupied'] for sp in spaces if sp.id in signals}

    def _apply_hits(self, spaces, hits: dict) -> None:
        """Run the occupancy hysteresis and persist any transitions.

        Shared by both scoring methods on purpose — swapping how a bay is judged
        must not change how long it takes to claim or release one.

        The counter is evidence toward the *next* change of state, so it resets
        when the evidence turns around. It used to run as one signed accumulator
        between -FREE_THR and +OCCUPY_THR, which is not what OCCUPY_THR says it
        is: a bay that had been free for a couple of seconds sat at -20, so
        claiming it took 24 positive frames rather than 4 — 2.4s instead of the
        documented 0.4s. Harmless while bays flipped the moment a box overlapped
        them; not harmless once a vehicle has to stand still first, because then
        every car that parks passes through that floor and the wait an admin
        configured is not the wait they get.
        """
        for sp in spaces:
            if sp.id not in hits:
                continue
            prev = self._hyst.get(sp.id, 0)

            if hits[sp.id]:
                nxt = min(max(prev, 0) + 1, OCCUPY_THR)
                self._hyst[sp.id] = nxt
                if not sp.is_occupied and nxt >= OCCUPY_THR:
                    self._set_occupied(sp, True)
            else:
                nxt = max(min(prev, 0) - 1, -FREE_THR)
                self._hyst[sp.id] = nxt
                if sp.is_occupied and nxt <= -FREE_THR:
                    self._set_occupied(sp, False)

    def _load_zone_config(self) -> dict:
        """The zone's scoring method and baseline, re-read at most every TTL.

        Same reasoning as _load_spaces: an admin changes these once in a while,
        the loop runs ten times a second, and against Neon a per-frame lookup
        would cost more wall clock than the frame interval allows.
        """
        now = time.monotonic()
        if self._cfg is not None and (now - self._cfg_at) < LAYOUT_TTL_SECONDS:
            return self._cfg

        from django.db import close_old_connections
        from vehicles.models import ParkingZone

        close_old_connections()
        try:
            row = (ParkingZone.objects
                   .filter(pk=self.zone_id)
                   .values('occupancy_method', 'baseline_image', 'baseline_captured_at')
                   .first())
            if row:
                self._cfg = {
                    'method':   row['occupancy_method'] or 'ml',
                    'baseline': row['baseline_image'] or '',
                    # Identity of the current baseline. A re-capture changes the
                    # timestamp even when the filename is reused, which is what
                    # forces the cached preparation to be rebuilt.
                    'token':    f"{row['baseline_image']}|{row['baseline_captured_at']}",
                }
                self._cfg_at = now
        except Exception as exc:
            log.warning("[ParkingCam] Zone config refresh failed zone %d: %s", self.zone_id, exc)

        self._refresh_dwell()
        return self._cfg or {'method': 'ml', 'baseline': '', 'token': ''}

    def _refresh_dwell(self) -> None:
        """Pick up the admin's dwell thresholds from the process-wide cache.

        Failing to read them leaves the previous values in place rather than
        snapping back to the defaults: a database blip must not silently make a
        lot that was tuned for 30 seconds start fining people at 12.
        """
        values = dwell_settings()
        if values is None:
            return
        self._parked_after, self._double_park_after = values

    def _read_baseline(self, name: str):
        """Decode the stored empty-lot image. None when unset or unreadable."""
        if not name:
            return None
        try:
            from django.core.files.storage import default_storage

            with default_storage.open(name, 'rb') as fh:
                buf = np.frombuffer(fh.read(), dtype=np.uint8)
            return cv2.imdecode(buf, cv2.IMREAD_COLOR)
        except Exception as exc:
            log.warning("[ParkingCam] Baseline unreadable zone %d (%s): %s",
                        self.zone_id, name, exc)
            return None

    def _prepare_baseline(self, frame, spaces, cfg):
        """The prepared baseline for the current layout, built at most once per
        change to the bays, the baseline image, or the frame size."""
        from vehicles import bay_occupancy

        signature = bay_occupancy.layout_signature(spaces, cfg['token'], frame.shape[:2])
        if self._prepared is not None and self._prepared.signature == signature:
            return self._prepared

        baseline = self._read_baseline(cfg['baseline'])
        if baseline is None:
            return None

        try:
            self._prepared = bay_occupancy.prepare_zone(
                baseline, spaces, frame.shape[:2], cfg['token'])
        except Exception as exc:
            log.warning("[ParkingCam] Baseline preparation failed zone %d: %s",
                        self.zone_id, exc)
            return None

        log.info("[ParkingCam] Zone %d baseline prepared for %d bay(s)",
                 self.zone_id, len(self._prepared.bays))
        return self._prepared

    def get_signals(self) -> dict:
        """Latest per-bay classic scores, for the tuning readout."""
        with self._lock:
            return dict(self._signals)

    def get_tracked_vehicles(self) -> list[dict]:
        """Vehicles the zone is currently following, and how long each has been
        still. The dwell thresholds are only tunable if this is visible —
        otherwise "why has this bay not gone occupied yet" has no answer."""
        now = time.monotonic()
        return [
            {
                "track_id":           t.track_id,
                "bbox":               t.bbox,
                "stationary_seconds": round(t.stationary_for(now), 1),
                "parked":             t.has_settled(now, self._parked_after),
                "seen_seconds":       round(max(0.0, now - t.first_seen), 1),
            }
            for t in self._tracker.tracks.values()
        ]

    def _track(self, frame: np.ndarray, now: float) -> list:
        """Run the detector and fold the result into this zone's tracker."""
        return self._tracker.update(self._detect(frame), now)

    def _process_frame(self, frame: np.ndarray) -> None:
        spaces = self._load_spaces()
        if not spaces:
            return

        cfg = self._load_zone_config()
        now = time.monotonic()

        hits = None
        if cfg['method'] == 'classic':
            hits = self._classic_hits(frame, spaces, cfg)

        # The tracker is fed once per frame at most, and both questions below
        # read the same result — running the detector twice on one frame would
        # double every cost and hand the tracker the same car twice.
        vehicles = None
        if hits is None:
            vehicles = self._track(frame, now)
            hits     = self._detector_hits(spaces, vehicles, now)

        self._apply_hits(spaces, hits)

        # Occupancy may be settled, but only the detector can see one car lying
        # across two bays — a per-bay signal reads that as two occupied bays,
        # exactly like two correctly parked cars. When the classic scorer is
        # driving, the detector still runs for this, just far less often: a
        # straddle has to persist well over a minute's worth of frames before it
        # counts, so checking ten times a second buys nothing.
        if vehicles is None and (now - self._last_detect) >= DETECT_INTERVAL_SECONDS:
            self._last_detect = now
            vehicles = self._track(frame, now)

        if vehicles is not None:
            self._check_double_parking(spaces, vehicles, frame, now)

    # ── Double parking ────────────────────────────────────────────────────────

    def _check_double_parking(self, spaces, vehicles, frame=None, now=None) -> None:
        """Flag a *stopped* vehicle whose box substantially covers two bays.

        Two conditions, and the second is what the tracker bought:

          * Geometry — the box genuinely lies across the line. This is the case
            the old centroid test could not express at all: a centroid falls in
            exactly one polygon, so a car parked across a line looked like an
            ordinary single occupancy.
          * Dwell — the vehicle has been stationary for
            DOUBLE_PARK_AFTER_SECONDS. Every car reversing into a slot is across
            two bays for several seconds on the way in, and the frame-streak
            this replaced could not tell that from a car left there, because it
            counted how long the *bays* looked straddled rather than how long
            the *vehicle* had been still.
        """
        now = time.monotonic() if now is None else now

        straddling: dict[tuple[int, ...], object] = {}
        blocked: set[int] = set()

        for v in vehicles:
            # Both directions must agree. Bay coverage alone counts a correctly
            # parked car's overhang into a small neighbouring bay as a straddle;
            # requiring a real share of the vehicle in each bay is what makes
            # "across the line" mean across the line.
            covered = [sp for sp in spaces
                       if _space_coverage(sp, v.bbox) >= DOUBLE_PARK_COVERAGE
                       and _vehicle_share(sp, v.bbox) >= DOUBLE_PARK_VEHICLE_SHARE]
            if len(covered) < 2:
                continue

            key = tuple(sorted(sp.id for sp in covered))
            # Tracked even while the car is still moving, so the alert for an
            # episode already reported does not flicker away and back as the
            # driver shuffles. Only the reporting below waits for the dwell.
            straddling[key] = v

            if v.has_settled(now, self._double_park_after):
                blocked.update(sp.id for sp in covered)
                if key not in self._reported:      # once per episode
                    self._reported.add(key)
                    self._report_double_parking(key, v, frame, now)

        # A straddled bay is unusable even if the car covers less of it than
        # OCCUPY_COVERAGE — nobody else can park there. Without this the
        # partly-covered side kept showing as free and the count overstated
        # capacity.
        for sp in spaces:
            if sp.id in blocked and not sp.is_occupied:
                self._hyst[sp.id] = OCCUPY_THR
                self._set_occupied(sp, True)

        # The episode is over once nothing straddles those bays any more: drop
        # the alert, so the banner reflects what is in the lot now rather than
        # accumulating stale warnings, and re-arm reporting for the next car.
        self._reported.difference_update(
            [k for k in self._reported if k not in straddling])
        with self._lock:
            for key in [k for k in self._alerts if k not in straddling]:
                self._alerts.pop(key, None)
                self._alert_evidence.pop(key, None)

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
        # Kept outside the block: get_jpeg() now goes to the shared reader and
        # may encode a frame, which is not work to do holding this zone's lock.
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

    def _report_double_parking(self, space_ids: tuple[int, ...], vehicle_track=None,
                               frame=None, now=None) -> None:
        """Record a settled straddle, attribute it if the plate is readable.

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
        bbox = getattr(vehicle_track, 'bbox', None)

        evidence = None
        if frame is not None:
            evidence = self._render_double_park_evidence(frame, bbox, space_objs)
        if not evidence:
            evidence = self.get_jpeg()   # may encode — do it before taking self._lock
        with self._lock:
            self._alert_evidence[space_ids] = evidence

        plate, vehicle, violation_id = '', None, None
        if bbox is not None:
            plate = self._read_plate(frame, bbox)

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
                # Reuses the gate path: one per vehicle per day, offence
                # numbering, the confiscation penalty, evidence image and owner
                # email.
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

        now = time.monotonic() if now is None else now
        with self._lock:
            self._alerts[space_ids] = {
                "zone_id":      self.zone_id,
                "space_ids":    list(space_ids),
                "spaces":       codes,
                "plate":        plate,
                "attributed":   vehicle is not None,
                "violation_id": violation_id,
                "detected_at":  timezone.now().isoformat(),
                # How long the car had been sitting still when this fired. Says
                # the alert is about a stopped vehicle, not a passing one.
                "stationary_seconds": round(
                    vehicle_track.stationary_for(now), 1) if vehicle_track else None,
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
