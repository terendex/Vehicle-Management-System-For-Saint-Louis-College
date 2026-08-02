import logging
import base64
import time
import threading
import uuid
from datetime import datetime, timedelta
from typing import Any
import asyncio
from asgiref.sync import sync_to_async
from django.utils import timezone
from django.conf import settings
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .gate_frames import set_latest_gate_frame
from .ml.detection import detect_plates, is_gpu_available
from .ml.database import save_record as db_save_record
from .ml.proximity_tracker import ProximityTracker
from .ml.reader import _ocr_crop
from .ml.validator import is_valid_ph_plate

logger = logging.getLogger(__name__)

SNAPSHOT_DIR = "snapshots"


def _resolve_gate(raw, user) -> str:
    """Decide which gate a scan belongs to.

    A camera explicitly configured with a gate wins. Otherwise fall back to the
    scanning guard's own gate_assignment so the scan still lands in that gate's
    log instead of the orphan 'main' bucket (which shows in no gate's view).
    """
    gid = (raw or '').strip()
    if gid and gid != 'main':
        return gid
    return getattr(user, 'gate_assignment', None) or 'main'

# Serialise VideoCapture construction so concurrent camera connections don't
# race on os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"].  On Windows, putenv()
# is not guaranteed thread-safe, and one camera's options can clobber another's
# just before cv2.VideoCapture() reads them.
_OPEN_CAP_LOCK = threading.Lock()

FRAME_RATE_LIMIT_MS = 100
_DEFAULT_DEDUP_SECONDS = 5  # fallback used if DB is unavailable at connect time
CAMERA_ENTRY_COOLDOWN_SECONDS = 60  # breathing space: camera won't exit a vehicle within this window after entry
NEGATIVE_SCAN_COOLDOWN_SECONDS = 60  # unregistered & denied/violation plates: same plate re-logged at most once per minute (DB-backed, survives reconnects)

# Per-track OCR accumulation settings
_OCR_LOCK_CONF    = 0.50   # lock immediately if any single read reaches this
_OCR_MIN_CONF     = 0.08   # minimum confidence to count a read — low to handle noisy vehicle crops
_OCR_MAX_ATTEMPTS = 15     # more attempts before force-locking, helps accumulate votes

# Locked-plate re-verification: quietly re-read each locked track's plate so a
# physical plate swap (or an early misread) is noticed without restarting the
# stream. A same-text read changes nothing; a confirmed different plate re-locks
# the track and runs the normal presence pipeline (per-plate cooldowns apply).
_OCR_REVERIFY_SECONDS = 5.0   # how often a locked track's plate is re-read
_REVERIFY_STRONG_CONF = 0.60  # a single read at this confidence switches immediately;
                              # weaker reads need two agreeing reads to switch

# ── Shared plate-presence registry ─────────────────────────────────────────────
#
# Shared across ALL consumers in this process (multiple cameras, WS reconnects).
# Each decision is *held* for a status-dependent cooldown (see
# _result_hold_seconds): while the plate stays in view within the hold, repeat
# reads are suppressed — no log spam, no per-frame re-processing. Once the hold
# expires (or the plate re-appears after leaving view), the entry/exit state
# machine runs again, so a vehicle waiting at the gate is re-evaluated about
# once a minute and the flow advances Entry → Exit → Entry.
#
# plate → {"result": dict, "decided_at": float, "last_seen": float}
_PLATE_PRESENCE: dict[str, dict] = {}
# plates whose decision is currently being computed — prevents duplicate DB writes
_PLATES_IN_FLIGHT: set[str] = set()
_PRESENCE_LOCK = threading.Lock()


class ScanLiveConsumer(AsyncJsonWebsocketConsumer):

    # ── lifecycle ──────────────────────────────────────────────────────────────

    async def connect(self):
        logger.info("[WS] Connection attempt from %s", self.scope.get("REMOTE_ADDR", "unknown"))
        qs = self.scope["query_string"].decode()
        token_key = (
            qs.split("token=")[-1].split("&")[0]
            if "token=" in qs
            else None
        )
        if not token_key:
            logger.warning("[WS] No token provided")
            await self.close(code=4001, reason="Authentication required")
            return
        self._user = await self._get_user_from_token(token_key)
        if self._user is None:
            logger.warning("[WS] Invalid token")
            await self.close(code=4001, reason="Invalid token")
            return

        # Read gate_id from query string, e.g. ?token=...&gate=gate1
        raw_gate = ''
        for part in qs.split('&'):
            if part.startswith('gate='):
                raw_gate = part[5:]
                break
        self._gate_id = _resolve_gate(raw_gate, self._user)

        self._tracker = ProximityTracker()
        self._frame_counter = 0
        self._pending_ocr: dict[int, bool] = {}
        # track_id → {votes, attempts, locked}
        self._ocr_state: dict[int, dict] = {}

        # plate → decided_at of the presence decision this client last received
        self._announced: dict[str, float] = {}

        self._fps = 0.0
        self._fps_counter = 0
        self._fps_start: float | None = None
        self._last_process_time: float = 0.0
        self._detection_in_progress = False
        self._loop = asyncio.get_running_loop()

        try:
            from vehicles.models import SystemSettings
            cfg = await sync_to_async(SystemSettings.get)()
            self._dedup_seconds = cfg.scan_dedup_seconds
        except Exception:
            self._dedup_seconds = _DEFAULT_DEDUP_SECONDS

        await self.accept()
        logger.info("[WS] Connection accepted for user: %s", self._user)
        await self.send_json({"type": "connected", "message": "Stream ready.", "gpu": is_gpu_available()})

        from .ml.detection import add_ml_status_listener
        _loop = self._loop
        async def _send_ml_status(stage, message):
            try:
                await self.send_json({"type": "ml_status", "stage": stage, "message": message})
            except Exception:
                pass
        def _ml_status_listener(stage, message):
            asyncio.run_coroutine_threadsafe(_send_ml_status(stage, message), _loop)
        self._ml_status_listener = _ml_status_listener
        add_ml_status_listener(_ml_status_listener)

    async def disconnect(self, code):
        logger.info("[WS] Disconnecting with code %s", code)
        from .ml.detection import remove_ml_status_listener
        if hasattr(self, '_ml_status_listener'):
            remove_ml_status_listener(self._ml_status_listener)

    # ── frame receive ──────────────────────────────────────────────────────────

    async def receive_json(self, content):
        if content.get("type") != "frame":
            return
        image_b64 = content.get("image_b64", "")
        if not image_b64:
            return

        try:
            image_bytes = base64.b64decode(image_b64)
        except Exception as exc:
            await self.send_json({"type": "error", "message": str(exc)})
            return

        # Keep the latest frame — attached as evidence when a scan auto-issues a violation
        self._last_frame_jpeg = image_bytes
        # Also publish it per-gate so violations raised outside this socket
        # (the REST scan endpoints, overstay sweeps) can still attach a photo.
        # Without this they were the only violations landing with no evidence.
        set_latest_gate_frame(getattr(self, '_gate_id', 'main'), image_bytes)

        # FPS accounting
        self._frame_counter += 1
        self._fps_counter += 1
        now_ts = time.time()
        if self._fps_start is None or self._fps_counter >= 10:
            if self._fps_start:
                self._fps = 10.0 / (now_ts - self._fps_start)
            self._fps_start = now_ts
            self._fps_counter = 0

        # Rate-limit: drop frames that arrive faster than FRAME_RATE_LIMIT_MS
        current_ms = now_ts * 1000
        if current_ms - self._last_process_time < FRAME_RATE_LIMIT_MS:
            return
        self._last_process_time = current_ms

        # Guard: don't queue another detection while the previous one is running
        if self._detection_in_progress:
            return
        self._detection_in_progress = True

        loop = asyncio.get_running_loop()
        try:
            detections = await loop.run_in_executor(None, self._run_detection, image_bytes)
        except Exception as exc:
            logger.error("[WS] Detection error: %s", exc)
            detections = []
        finally:
            self._detection_in_progress = False

        now = timezone.now()
        tracker_output = self._tracker.update(detections, img_w=getattr(self, "_last_img_w", 640))
        det_by_idx = {i: d for i, d in enumerate(detections)}

        # Evict OCR state for tracks the tracker has expired — prevents unbounded growth
        active_ids = set(self._tracker.tracks.keys())
        for stale_id in list(self._ocr_state.keys()):
            if stale_id not in active_ids:
                self._ocr_state.pop(stale_id, None)
                self._pending_ocr.pop(stale_id, None)

        active_tracks = []
        tracks_needing_ocr = []
        tracks_to_reverify = []

        for t_out in tracker_output:
            track_id    = t_out["track_id"]
            bbox        = t_out["bbox"]
            x, y, bw, bh = bbox["x"], bbox["y"], bbox["width"], bbox["height"]

            class_name   = t_out.get("class_name", "")
            vehicle_type = t_out.get("vehicle_type")
            plate_text   = t_out.get("plate_text", "")
            ocr_done     = t_out.get("ocr_done", False)

            d_idx = t_out.get("detection_index")
            det   = det_by_idx.get(d_idx) if d_idx is not None else None

            if (det and det.get("class_name") == "license_plate"
                    and det.get("crop") is not None):
                if not ocr_done:
                    tracks_needing_ocr.append(
                        (track_id, det["crop"], det.get("aspect_ratio", 1.0))
                    )
                else:
                    st = self._ocr_state.get(track_id)
                    if (st and st.get("locked")
                            and now_ts - st.get("verify_at", 0.0) >= _OCR_REVERIFY_SECONDS):
                        st["verify_at"] = now_ts  # claim before queueing
                        tracks_to_reverify.append(
                            (track_id, det["crop"], det.get("aspect_ratio", 1.0))
                        )

            w_img = getattr(self, "_last_img_w", 640)
            h_img = getattr(self, "_last_img_h", 480)
            active_tracks.append({
                "track_id":      track_id,
                "plate_text":    plate_text,
                "vehicle_type":  vehicle_type,
                "class_name":    class_name,
                "bbox":          [x / max(w_img, 1), y / max(h_img, 1),
                                  (x + bw) / max(w_img, 1), (y + bh) / max(h_img, 1)],
                "detection_conf": det.get("confidence", 0.0) if det else 0.0,
            })

        await self.send_json({
            "type":     "tracks",
            "tracks":   active_tracks,
            "frame_id": self._frame_counter,
            "fps":      round(self._fps, 1),
        })

        if tracks_needing_ocr:
            asyncio.create_task(self._run_ocr_for_tracks(tracks_needing_ocr))
        if tracks_to_reverify:
            asyncio.create_task(self._reverify_locked_tracks(tracks_to_reverify))

        # Refresh presence for tracks whose plates are already known — keeps the
        # sliding dedup window open while the vehicle stays in view
        if any(t.get("plate_text") for t in active_tracks):
            await self._process_scan_results(active_tracks, now)

    # ── detection (sync, runs in executor) ────────────────────────────────────

    def _run_detection(self, image_bytes: bytes) -> list[dict]:
        import cv2
        import numpy as np
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return []

        h, w = img.shape[:2]
        self._last_img_w = w
        self._last_img_h = h

        # Skip rotation passes only when every *plate-class* track is already locked.
        # Previously used all() on an empty iterable (when only vehicle tracks exist),
        # which returned True and incorrectly disabled rotation before any plate was found.
        plate_tracks = [t for t in self._tracker.tracks.values()
                        if t.class_name == "license_plate"]
        all_plates_locked = bool(plate_tracks) and all(t.ocr_done for t in plate_tracks)
        detections = detect_plates(img, try_rotation=not all_plates_locked)

        result = []
        for i, det in enumerate(detections):
            bbox = det["bbox"]
            result.append({
                "bbox": {
                    "x":      int(bbox["x"] * w),
                    "y":      int(bbox["y"] * h),
                    "width":  int(bbox["width"] * w),
                    "height": int(bbox["height"] * h),
                },
                "crop":            det.get("crop"),
                "confidence":      det["score"],
                "aspect_ratio":    det.get("aspect_ratio", 1.0),
                "class_name":      det.get("class_name", ""),
                "vehicle_type":    det.get("vehicle_type"),
                "detection_index": i,
            })
        return result

    # ── OCR (async task per track, with confidence accumulation) ──────────────

    async def _run_ocr_for_tracks(self, tracks_to_process: list):
        loop = asyncio.get_running_loop()

        for track_id, crop, aspect in tracks_to_process:
            if track_id in self._pending_ocr:
                continue

            state = self._ocr_state.get(track_id)
            if state and state["locked"]:
                continue

            self._pending_ocr[track_id] = True
            try:
                plate_text, conf = await loop.run_in_executor(None, _ocr_crop, crop, aspect)
                if conf is None:
                    conf = 0.0

                state = self._ocr_state.setdefault(track_id, {
                    "votes": {}, "attempts": 0, "locked": False,
                })
                state["attempts"] += 1

                if not plate_text or conf < _OCR_MIN_CONF:
                    # Low-quality read — force-lock if we've hit the attempt limit
                    if state["attempts"] >= _OCR_MAX_ATTEMPTS and state["votes"]:
                        best = max(state["votes"], key=state["votes"].get)
                        state["locked"] = True
                        state["verify_at"] = time.time()
                        self._tracker.set_plate_text(track_id, best)
                        logger.info("[WS] Max attempts (no lock) track %d → %s", track_id, best)
                        await self._finalize_plate(track_id, best, 0.0)
                    continue

                # Accumulate confidence-weighted votes across reads.
                # Reads matching a valid PH plate format get triple weight so a
                # correct read outvotes garbled partials when the track locks.
                normalized = plate_text.strip().upper().replace(' ', '')
                weight = conf * (3.0 if is_valid_ph_plate(normalized) else 1.0)
                state["votes"][plate_text] = state["votes"].get(plate_text, 0.0) + weight
                best = max(state["votes"], key=state["votes"].get)

                # Always push the current best to the overlay immediately
                await self.send_json({
                    "type":       "ocr_update",
                    "track_id":   track_id,
                    "plate_text": best,
                })
                logger.info("[WS] OCR track %d read=%d → %s (conf=%.2f)",
                            track_id, state["attempts"], best, conf)

                # Lock when confident or attempts exhausted
                if conf >= _OCR_LOCK_CONF or state["attempts"] >= _OCR_MAX_ATTEMPTS:
                    state["locked"] = True
                    state["verify_at"] = time.time()
                    self._tracker.set_plate_text(track_id, best)
                    logger.info("[WS] Locked track %d → %s (conf=%.2f, attempts=%d)",
                                track_id, best, conf, state["attempts"])
                    await self._finalize_plate(track_id, best, conf)

            except Exception as exc:
                logger.warning("[OCR] Failed for track %d: %s", track_id, exc)
            finally:
                self._pending_ocr.pop(track_id, None)

    async def _finalize_plate(self, track_id: int, plate_text: str, conf: float):
        """Process a freshly locked plate and broadcast the decision once."""
        result = await self._handle_plate_sighting(track_id, plate_text, 0.0, conf, None)
        if result:
            await self.send_json({"type": "result", "results": [result]})

    # ── locked-plate re-verification ───────────────────────────────────────────

    async def _reverify_locked_tracks(self, tracks_to_verify: list):
        """
        Quietly re-read plates on locked tracks (every _OCR_REVERIFY_SECONDS).

        A read matching the locked text changes nothing — no UI events, no DB
        writes. A different plate needs either one strong read or two agreeing
        reads to switch; the track then re-locks to the new text and the plate
        runs through the normal presence pipeline, so per-plate cooldowns
        (1-minute unknown/denied window etc.) still decide what gets reported.
        Catches physical plate swaps and corrects early misreads.
        """
        loop = asyncio.get_running_loop()

        for track_id, crop, aspect in tracks_to_verify:
            if track_id in self._pending_ocr:
                continue
            state = self._ocr_state.get(track_id)
            track = self._tracker.get_track(track_id)
            if not state or not state.get("locked") or track is None:
                continue

            self._pending_ocr[track_id] = True
            try:
                plate_text, conf = await loop.run_in_executor(None, _ocr_crop, crop, aspect)
                conf = conf or 0.0
                plate_norm = (plate_text or "").strip().upper().replace(" ", "")
                current    = (track.plate_text or "").strip().upper().replace(" ", "")

                if not plate_norm or conf < _OCR_MIN_CONF:
                    continue  # unreadable frame — keep the current lock

                if plate_norm == current:
                    state.pop("switch_reads", None)  # confirmed — drop any switch candidate
                    continue

                # Never re-lock a track onto an invalid plate format
                if not is_valid_ph_plate(plate_norm):
                    continue

                reads = state.setdefault("switch_reads", {})
                if len(reads) > 5:
                    reads = state["switch_reads"] = {}  # noisy garbage — start over
                reads[plate_norm] = reads.get(plate_norm, 0) + 1

                if conf >= _REVERIFY_STRONG_CONF or reads[plate_norm] >= 2:
                    state["switch_reads"] = {}
                    state["votes"] = {plate_norm: conf}
                    state["verify_at"] = time.time()
                    self._tracker.set_plate_text(track_id, plate_norm)
                    logger.info("[WS] Re-verify: track %d plate %s -> %s (conf=%.2f)",
                                track_id, current or "?", plate_norm, conf)
                    await self.send_json({
                        "type": "ocr_update", "track_id": track_id, "plate_text": plate_norm,
                    })
                    await self._finalize_plate(track_id, plate_norm, conf)
                else:
                    # One differing read — re-check shortly to confirm or dismiss
                    state["verify_at"] = time.time() - (_OCR_REVERIFY_SECONDS - 2.0)
            except Exception as exc:
                logger.warning("[OCR] Re-verify failed for track %d: %s", track_id, exc)
            finally:
                self._pending_ocr.pop(track_id, None)

    # ── presence-aware scan processing ─────────────────────────────────────────

    async def _handle_plate_sighting(self, track_id: int, plate_text: str,
                                     det_conf: float, ocr_conf: float, bbox):
        """
        Record a sighting of `plate_text` and return a result to announce, or None.

        While the plate stays in view, its `last_seen` slides forward and the
        existing decision is held — repeated reads of the current state are
        suppressed. The entry/exit state machine (_check_vehicle) runs again
        when the decision's hold time expires (even if the vehicle never left
        the frame) or when the plate re-appears after `scan_dedup_seconds` out
        of view — so the flow advances Entry → Exit → Entry both across genuine
        appearances and for a vehicle waiting at the gate.
        Each client is told about a given decision exactly once.
        """
        plate = plate_text.strip().upper().replace(' ', '')
        # OCR noise gate: only valid Philippine plate formats reach the lookup,
        # the access log, or the violation pipeline — partial/garbled reads
        # (e.g. "8946", "C946") are dropped here.
        if not plate or not is_valid_ph_plate(plate):
            return None

        now_ts = time.time()
        needs_decision = False
        with _PRESENCE_LOCK:
            entry = _PLATE_PRESENCE.get(plate)
            in_view = entry is not None and (now_ts - entry["last_seen"]) < self._dedup_seconds
            if in_view:
                entry["last_seen"] = now_ts   # sliding window — still in view
            hold_expired = (
                entry is None
                or (now_ts - entry["decided_at"]) >= self._result_hold_seconds(entry["result"])
            )
            if (not in_view or hold_expired) and plate not in _PLATES_IN_FLIGHT:
                _PLATES_IN_FLIGHT.add(plate)
                needs_decision = True

        if needs_decision:
            try:
                await sync_to_async(self._save_to_db)(
                    track_id, plate, det_conf, ocr_conf, bbox, None
                )
                enriched = await sync_to_async(
                    self._check_vehicle, thread_sensitive=True
                )(plate, bbox)
                enriched["plate_number"] = plate
                ts = time.time()
                with _PRESENCE_LOCK:
                    _PLATE_PRESENCE[plate] = {
                        "result": enriched, "decided_at": ts, "last_seen": ts,
                    }
                await sync_to_async(self._record_ml_sample)(None, [enriched])
                logger.info("[WS] Plate %s decided → %s", plate, enriched.get("status"))
            except Exception as exc:
                logger.error("[WS] Scan processing failed for %s: %s", plate, exc)
                # Remember the failure so we don't retry every frame and spam the log
                ts = time.time()
                with _PRESENCE_LOCK:
                    _PLATE_PRESENCE[plate] = {
                        "result": {"plate_number": plate, "error": True},
                        "decided_at": ts, "last_seen": ts,
                    }
            finally:
                with _PRESENCE_LOCK:
                    _PLATES_IN_FLIGHT.discard(plate)
                self._evict_presence()

        with _PRESENCE_LOCK:
            entry = _PLATE_PRESENCE.get(plate)
        if not entry or entry["result"].get("error"):
            return None
        if (time.time() - entry["last_seen"]) >= self._dedup_seconds:
            return None   # stale decision awaiting replacement — don't announce it
        if self._announced.get(plate) == entry["decided_at"]:
            return None   # this client already received this decision
        self._announced[plate] = entry["decided_at"]
        return entry["result"]

    # ── process tracks that already have plate text ────────────────────────────

    async def _process_scan_results(self, tracks_list: list[dict], now):
        results = []
        processed_ids: set[int] = set()

        for track_data in tracks_list:
            track_id     = track_data["track_id"]
            plate_number = track_data.get("plate_text", "")
            det_conf     = track_data.get("detection_conf", 0.0)

            if not plate_number.strip() or track_id in processed_ids:
                continue
            processed_ids.add(track_id)

            bbox = {
                "x": track_data["bbox"][0], "y": track_data["bbox"][1],
                "width": track_data["bbox"][2], "height": track_data["bbox"][3],
            }
            result = await self._handle_plate_sighting(
                track_id, plate_number, det_conf, 0.0, bbox
            )
            if result:
                results.append({**result, "bbox": bbox})

        if results:
            await self.send_json({"type": "result", "results": results})

    # ── presence maintenance ───────────────────────────────────────────────────

    def _result_hold_seconds(self, result: dict) -> float:
        """How long a decision is held before the plate is re-evaluated in view."""
        status = result.get("status")
        if result.get("error") or status == "duplicate":
            return self._dedup_seconds            # transient — retry soon
        if status in ("authorized", "open_entry", "exited"):
            return CAMERA_ENTRY_COOLDOWN_SECONDS  # breathing space before state can flip
        return NEGATIVE_SCAN_COOLDOWN_SECONDS     # unknown / denied / wrong_day etc.

    def _evict_presence(self):
        """Drop plates not seen for 2× the dedup window to bound memory."""
        cutoff = time.time() - self._dedup_seconds * 2
        with _PRESENCE_LOCK:
            for p in [p for p, e in _PLATE_PRESENCE.items() if e["last_seen"] < cutoff]:
                del _PLATE_PRESENCE[p]
            alive = set(_PLATE_PRESENCE)
        for p in [p for p in self._announced if p not in alive]:
            del self._announced[p]

    # ── sync helpers (run in executor / sync_to_async) ─────────────────────────

    def _save_snapshot(self, track_id: int, crop) -> str:
        import cv2
        from pathlib import Path
        snapshot_dir = Path(settings.MEDIA_ROOT) / SNAPSHOT_DIR
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        filename = f"plate_{track_id}_{int(datetime.now().timestamp())}.jpg"
        path = snapshot_dir / filename
        cv2.imwrite(str(path), crop)
        return f"{SNAPSHOT_DIR}/{filename}"

    def _save_to_db(self, track_id: int, plate_number: str, det_conf: float,
                    ocr_conf: float, bbox: dict, snapshot_path: str | None):
        from django.db import close_old_connections
        from .models import PlateRecognitionRecord
        close_old_connections()
        PlateRecognitionRecord.objects.create(
            track_id=track_id,
            plate_text=plate_number,
            detection_confidence=det_conf,
            ocr_confidence=ocr_conf,
            timestamp=timezone.now(),
            snapshot_path=snapshot_path or "",
        )

    def _check_vehicle(self, plate_number: str, bbox):
        from django.db import close_old_connections
        from vehicles.models import Vehicle, VehicleRegistration, SupplierPlate
        from .models import AccessLog
        from .entry_logic import check_entry
        from violations.models import Violation
        from vehicles.serializers import VehicleSerializer
        from accounts.models import AuditLog
        from .views import (_inside_state, _in_exit_cooldown, _already_inside,
                            _auto_log_violation, _close_active_pass, _gate_label,
                            _check_stay_limit, _log_status, _open_campus_unknown_result,
                            _is_standby_fetcher)
        from .entry_logic import is_open_campus
        close_old_connections()

        # Normalize so OCR output matches the stored plate (e.g. "ABC 123" → "ABC123")
        plate_number = plate_number.strip().upper().replace(' ', '')

        vehicle = Vehicle.objects.select_related("user").filter(
            plate_number=plate_number
        ).first()

        gate_id = getattr(self, '_gate_id', 'main')

        if not vehicle:
            # Supplier vehicles have no Vehicle/owner record — permitted by plate list
            supplier_plate = SupplierPlate.objects.select_related('supplier').filter(
                plate_number=plate_number, supplier__is_active=True
            ).first()
            if supplier_plate:
                return self._check_supplier(plate_number, supplier_plate, gate_id)

            # Open Campus Mode — unregistered plates are admitted with the full
            # entry/exit state machine and shown as "Open Entry".
            if is_open_campus():
                result = _open_campus_unknown_result(plate_number, gate_id, self._user)
                result.setdefault("registration", None)
                result.setdefault("has_violations", False)
                if result.get("allowed"):
                    try:
                        AuditLog.objects.create(
                            actor=self._user,
                            action="scan",
                            details=f"Open entry (camera) | Plate: {plate_number} | "
                                    f"Unregistered | Gate: {_gate_label(gate_id)}",
                        )
                    except Exception:
                        pass
                return result

            now = timezone.now()
            cutoff = now - timedelta(seconds=NEGATIVE_SCAN_COOLDOWN_SECONDS)
            recent_unknown = AccessLog.objects.filter(
                plate_number=plate_number,
                status="unknown",
                scanned_at__gte=cutoff,
                scanned_at__lte=now,  # future-dated rows (clock skew) must not wedge the gate
            ).exists()
            if recent_unknown:
                return {
                    "status":         "duplicate",
                    "allowed":        False,
                    "message":        "Duplicate scan — unregistered plate already logged within cooldown.",
                    "constraint":     None,
                    "vehicle":        None,
                    "registration":   None,
                    "has_violations": False,
                    "already_inside": False,
                }
            AccessLog.objects.create(
                plate_number=plate_number,
                status="unknown",
                gate_id=gate_id,
                scanned_by=self._user,
            )
            return {
                "status":         "unknown",
                "allowed":        False,
                "message":        "Plate not registered.",
                "constraint":     None,
                "vehicle":        None,
                "registration":   None,
                "has_violations": False,
            }

        inside_status, last_entry = _inside_state(plate_number)

        if inside_status == 'duplicate':
            return {
                "status":         "duplicate",
                "allowed":        False,
                "message":        "Duplicate scan — already processed within grace period.",
                "vehicle":        VehicleSerializer(vehicle).data,
                "has_violations": False,
                "already_inside": True,
            }

        if inside_status == 'inside':
            # Visitor vehicles exit by scanning the printed slip QR, never by plate.
            from .views import _active_visitor_pass
            if _active_visitor_pass(plate_number):
                return {
                    "status":         "visitor_pass_required",
                    "allowed":        False,
                    "message":        "Visitor is inside on an active pass. Scan the QR on the "
                                      "printed visitor slip to record the exit.",
                    "vehicle":        VehicleSerializer(vehicle).data,
                    "has_violations": False,
                    "already_inside": True,
                }
            seconds_since_entry = (timezone.now() - last_entry.scanned_at).total_seconds()
            if seconds_since_entry < CAMERA_ENTRY_COOLDOWN_SECONDS:
                # Within the 1-minute breathing space — ignore
                return {
                    "status":         "already_inside",
                    "allowed":        False,
                    "message":        "Vehicle just entered — within the 1-minute entry window.",
                    "vehicle":        VehicleSerializer(vehicle).data,
                    "has_violations": False,
                    "already_inside": True,
                }
            from django.db import transaction as _tx
            exit_log = None
            with _tx.atomic():
                locked_entry = AccessLog.objects.select_for_update().filter(
                    pk=last_entry.pk
                ).first()
                if not locked_entry or AccessLog.objects.filter(paired_entry=locked_entry).exists():
                    return {
                        "status":         "duplicate",
                        "allowed":        False,
                        "message":        "Duplicate scan — already processed.",
                        "vehicle":        VehicleSerializer(vehicle).data,
                        "has_violations": False,
                        "already_inside": False,
                    }
                exit_log = AccessLog.objects.create(
                    plate_number=plate_number,
                    vehicle=vehicle,
                    status=AccessLog.Status.EXITED,
                    gate_id=gate_id,
                    scanned_by=self._user,
                    paired_entry=locked_entry,
                )
            delta = exit_log.scanned_at - last_entry.scanned_at
            duration_minutes = int(delta.total_seconds() / 60)
            overstay_minutes = _close_active_pass(
                plate_number, gate_id,
                evidence_bytes=getattr(self, '_last_frame_jpeg', None))
            if vehicle.user and vehicle.user.owner_type == 'fetcher' and not _is_standby_fetcher(vehicle.user):
                overstay_minutes = max(overstay_minutes, _check_stay_limit(
                    plate_number, vehicle, 'fetcher', duration_minutes, gate_id,
                    evidence_bytes=getattr(self, '_last_frame_jpeg', None)))
            overstay_note = f" Overstayed by {overstay_minutes} min." if overstay_minutes else ""
            owner_name = vehicle.user.full_name if vehicle.user else 'Unknown'
            try:
                AuditLog.objects.create(
                    actor=self._user,
                    action="scan",
                    details=f"Auto-exit (camera) | Plate: {plate_number} | Owner: {owner_name} | "
                            f"Duration: {duration_minutes} min"
                            + (f" | OVERSTAYED by {overstay_minutes} min" if overstay_minutes else "")
                            + f" | Gate: {_gate_label(gate_id)}",
                )
            except Exception:
                pass
            return {
                "status":           "exited",
                "allowed":          False,
                "message":          f"{owner_name} — Exit recorded. Duration: {duration_minutes} min.{overstay_note}",
                "vehicle":          VehicleSerializer(vehicle).data,
                "has_violations":   False,
                "already_inside":   False,
                "duration_minutes": duration_minutes,
                "overstay_minutes": overstay_minutes,
            }

        if _in_exit_cooldown(plate_number):
            return {
                "status":         "duplicate",
                "allowed":        False,
                "message":        "Exit cooldown — entry suppressed for 1 minute after exit.",
                "vehicle":        VehicleSerializer(vehicle).data,
                "has_violations": False,
                "already_inside": False,
            }

        entry = check_entry(vehicle)

        # UI-only statuses (e.g. 'no_pass', 'open_entry') aren't valid AccessLog
        # statuses — store those rows as authorized/denied per the decision while
        # the client still sees the real status
        log_status = _log_status(entry)

        # Authorized entries are deduped by the grace-period / entry-window checks
        # above; denied/violation statuses get a 1-minute DB-backed cooldown so a
        # vehicle idling at the gate doesn't flood the log across WS reconnects.
        if not entry["allowed"]:
            now = timezone.now()
            cutoff = now - timedelta(seconds=NEGATIVE_SCAN_COOLDOWN_SECONDS)
            recent_same = AccessLog.objects.filter(
                plate_number=plate_number,
                status=log_status,
                scanned_at__gte=cutoff,
                scanned_at__lte=now,  # future-dated rows (clock skew) must not wedge the gate
            ).exists()
            if recent_same:
                return {
                    "status":         "duplicate",
                    "allowed":        False,
                    "message":        "Duplicate scan — result already logged within cooldown.",
                    "vehicle":        VehicleSerializer(vehicle).data,
                    "has_violations": False,
                    "already_inside": False,
                }

        has_violations = Violation.objects.filter(
            vehicle=vehicle, is_resolved=False
        ).exists()
        already_inside = _already_inside(plate_number)

        AccessLog.objects.create(
            plate_number=plate_number,
            vehicle=vehicle,
            status=log_status,
            denied_reason="" if entry["allowed"] else entry["message"],
            gate_id=gate_id,
            scanned_by=self._user,
        )
        try:
            AuditLog.objects.create(
                actor=self._user,
                action="scan",
                details=f"Plate: {plate_number} | Status: {entry['status']} | Gate: {_gate_label(gate_id)}",
            )
        except Exception:
            pass

        # A visitor waiting for a pass ('no_pass'/'unknown') isn't a violation —
        # only genuinely denied/wrong-day entries are auto-fined.
        if not entry["allowed"] and entry["status"] not in ("no_pass", "unknown"):
            try:
                _auto_log_violation(
                    vehicle, entry["message"], gate_id,
                    evidence_bytes=getattr(self, '_last_frame_jpeg', None))
            except Exception:
                pass

        # Fetch registration details for non-visitor plates
        registration_data = None
        owner_type = vehicle.user.owner_type if vehicle.user else None
        if owner_type and owner_type != 'visitor':
            try:
                reg = (
                    vehicle.registrations.filter(
                        status='accepted'
                    ).order_by('-reviewed_at').first()
                    or VehicleRegistration.objects.filter(
                        plate_number=vehicle.plate_number,
                        status='accepted',
                    ).order_by('-reviewed_at').first()
                )
                if reg:
                    registration_data = {
                        'registrant_type': reg.registrant_type,
                        'campus_days':     reg.campus_days,
                        'schedule':        reg.schedule,
                        'or_number':       reg.or_number,
                        'student_id':      reg.student_id,
                        'program_year':    reg.program_year,
                        'employee_id':     reg.employee_id,
                        'department_name': reg.department.name if reg.department else '',
                        'reviewed_at':     reg.reviewed_at.isoformat() if reg.reviewed_at else None,
                    }
            except Exception:
                pass

        try:
            from .entry_logic import get_organizer_event
            organizer_event = get_organizer_event(plate_number)
        except Exception:
            organizer_event = None

        return {
            "status":          entry["status"],
            "allowed":         entry["allowed"],
            "message":         entry["message"],
            "constraint":      entry.get("constraint"),
            "vehicle":         VehicleSerializer(vehicle).data,
            "registration":    registration_data,
            "has_violations":  has_violations,
            "already_inside":  already_inside,
            "organizer_event": organizer_event,
        }

    def _check_supplier(self, plate_number: str, supplier_plate, gate_id: str):
        """Entry/exit state machine for supplier plates (auto-permitted, no owner account).
        Mirrors the supplier branch of ManualEntryView so camera and manual paths agree."""
        from django.db import transaction as _tx
        from .models import AccessLog
        from accounts.models import AuditLog
        from .views import (_inside_state, _in_exit_cooldown, _gate_label,
                            _check_stay_limit, _supplier_rule_denial)

        supplier_name = supplier_plate.supplier.company_name
        inside_status, last_entry = _inside_state(plate_number)

        if inside_status == 'duplicate':
            return {
                "status":         "duplicate",
                "allowed":        False,
                "message":        "Duplicate scan — already processed within grace period.",
                "is_supplier":    True,
                "supplier_name":  supplier_name,
                "vehicle":        None,
                "registration":   None,
                "has_violations": False,
                "already_inside": True,
            }

        if inside_status == 'inside':
            seconds_since_entry = (timezone.now() - last_entry.scanned_at).total_seconds()
            if seconds_since_entry < CAMERA_ENTRY_COOLDOWN_SECONDS:
                return {
                    "status":         "already_inside",
                    "allowed":        False,
                    "message":        "Supplier vehicle just entered — within the 1-minute entry window.",
                    "is_supplier":    True,
                    "supplier_name":  supplier_name,
                    "vehicle":        None,
                    "registration":   None,
                    "has_violations": False,
                    "already_inside": True,
                }
            with _tx.atomic():
                locked_entry = AccessLog.objects.select_for_update().filter(pk=last_entry.pk).first()
                if not locked_entry or AccessLog.objects.filter(paired_entry=locked_entry).exists():
                    return {
                        "status":         "duplicate",
                        "allowed":        False,
                        "message":        "Duplicate scan — already processed.",
                        "is_supplier":    True,
                        "supplier_name":  supplier_name,
                        "vehicle":        None,
                        "registration":   None,
                        "has_violations": False,
                        "already_inside": False,
                    }
                exit_log = AccessLog.objects.create(
                    plate_number=plate_number,
                    status=AccessLog.Status.EXITED,
                    gate_id=gate_id,
                    scanned_by=self._user,
                    paired_entry=locked_entry,
                )
            delta = exit_log.scanned_at - last_entry.scanned_at
            duration_minutes = int(delta.total_seconds() / 60)
            overstay_minutes = _check_stay_limit(
                plate_number, None, 'supplier', duration_minutes, gate_id,
                evidence_bytes=getattr(self, '_last_frame_jpeg', None))
            overstay_note = f" Overstayed by {overstay_minutes} min — violation issued." if overstay_minutes else ""
            try:
                AuditLog.objects.create(
                    actor=self._user,
                    action="scan",
                    details=f"Auto-exit (camera) | Supplier plate: {plate_number} | {supplier_name} | "
                            f"Duration: {duration_minutes} min"
                            + (f" | OVERSTAYED by {overstay_minutes} min" if overstay_minutes else "")
                            + f" | Gate: {_gate_label(gate_id)}",
                )
            except Exception:
                pass
            return {
                "status":           "exited",
                "allowed":          False,
                "message":          f"Supplier vehicle — {supplier_name}. Exit recorded. Duration: {duration_minutes} min.{overstay_note}",
                "is_supplier":      True,
                "supplier_name":    supplier_name,
                "vehicle":          None,
                "registration":     None,
                "has_violations":   False,
                "already_inside":   False,
                "duration_minutes": duration_minutes,
                "overstay_minutes": overstay_minutes,
            }

        if _in_exit_cooldown(plate_number):
            return {
                "status":         "duplicate",
                "allowed":        False,
                "message":        "Exit cooldown — entry suppressed for 1 minute after exit.",
                "is_supplier":    True,
                "supplier_name":  supplier_name,
                "vehicle":        None,
                "registration":   None,
                "has_violations": False,
                "already_inside": False,
            }

        deny_msg = _supplier_rule_denial()
        if deny_msg:
            # DB-backed dedup so an idling supplier truck doesn't flood the log
            now = timezone.now()
            cutoff = now - timedelta(seconds=NEGATIVE_SCAN_COOLDOWN_SECONDS)
            recent_denied = AccessLog.objects.filter(
                plate_number=plate_number, status=AccessLog.Status.DENIED,
                scanned_at__gte=cutoff, scanned_at__lte=now,
            ).exists()
            if not recent_denied:
                AccessLog.objects.create(
                    plate_number=plate_number, status=AccessLog.Status.DENIED,
                    denied_reason=deny_msg, gate_id=gate_id, scanned_by=self._user,
                )
            return {
                "status":         "denied",
                "allowed":        False,
                "message":        deny_msg,
                "is_supplier":    True,
                "supplier_name":  supplier_name,
                "vehicle":        None,
                "registration":   None,
                "has_violations": False,
                "already_inside": False,
            }

        AccessLog.objects.create(
            plate_number=plate_number,
            status=AccessLog.Status.AUTHORIZED,
            gate_id=gate_id,
            scanned_by=self._user,
        )
        try:
            AuditLog.objects.create(
                actor=self._user,
                action="scan",
                details=f"Supplier entry (camera) | Plate: {plate_number} | {supplier_name} | Gate: {_gate_label(gate_id)}",
            )
        except Exception:
            pass
        from .entry_logic import is_open_campus
        open_campus = is_open_campus()
        return {
            "status":         "open_entry" if open_campus else "authorized",
            "allowed":        True,
            "message":        (f"Open Campus Mode active — Supplier vehicle {supplier_name}. Open entry granted."
                               if open_campus else
                               f"Supplier vehicle — {supplier_name}. Entry permitted."),
            "is_supplier":    True,
            "supplier_name":  supplier_name,
            "vehicle":        None,
            "registration":   None,
            "has_violations": False,
            "already_inside": False,
        }

    def _record_ml_sample(self, raw_bytes, results):
        from django.db import close_old_connections
        from .models import MLTrainingSample
        close_old_connections()
        try:
            plates = [r["plate_number"] for r in results if r.get("plate_number")]
            MLTrainingSample.objects.create(
                plate_number=";".join(plates) if plates else "",
                status="auto",
                source="stream",
            )
        except Exception as exc:
            logger.warning("ML sample failed: %s", exc)

    # ── auth ──────────────────────────────────────────────────────────────────

    @staticmethod
    async def _get_user_from_token(token_key):
        from django.contrib.auth import get_user_model
        from rest_framework_simplejwt.authentication import JWTAuthentication
        from rest_framework_simplejwt.exceptions import TokenError, InvalidToken

        User = get_user_model()
        try:
            # Validates signature, expiry, and token type using simplejwt + SECRET_KEY
            validated = await sync_to_async(JWTAuthentication().get_validated_token)(token_key)
            user_id = validated["user_id"]
            return await sync_to_async(User.objects.get)(pk=user_id)
        except (TokenError, InvalidToken, User.DoesNotExist, Exception):
            return None


# ── Shared RTSP stream worker ──────────────────────────────────────────────────
#
# Only ONE cv2.VideoCapture is opened per RTSP URL regardless of how many
# WebSocket consumers (admin, security, etc.) are watching the same camera.
# Each consumer subscribes to an asyncio.Queue; the worker thread broadcasts
# encoded JPEG frames to every live subscriber.

class _StreamWorker:
    """Manages a single RTSP capture thread shared across multiple consumers."""

    FRAME_INTERVAL = 1.0 / 20   # 20 fps cap for network/CPU budget
    MAX_RETRIES    = 5
    RETRY_DELAY    = 2.0

    def __init__(self, rtsp_url: str):
        self.rtsp_url   = rtsp_url
        # The subscriber dict IS the reference count. A separate counter drifted
        # out of step with it — an unsubscribe for an sid that had already been
        # replaced decremented the count for a subscriber that was still there.
        self._subs: dict[str, tuple['asyncio.Queue', 'asyncio.AbstractEventLoop']] = {}
        self._lock      = threading.Lock()
        self._thread: threading.Thread | None = None
        # One Event per thread generation, never reused: clearing a shared Event
        # let a new subscriber re-arm a thread that an outgoing one was stopping.
        self._stop      = threading.Event()

    def is_running(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    def subscribe(self, sid: str, loop: 'asyncio.AbstractEventLoop') -> 'asyncio.Queue':
        """Register a consumer and guarantee a capture thread is running for it.

        Call through _acquire_worker, which holds the pool lock across
        get-or-create + subscribe.
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=3)
        with self._lock:
            self._subs[sid] = (q, loop)
            # Start a thread whenever there is not a live one — not merely for
            # the first subscriber. A worker whose thread had already exited
            # (retries exhausted, or a stop that raced with this subscribe) was
            # still sitting in the pool, and everyone who joined it afterwards
            # waited on a queue nothing would ever push to: a black feed, no
            # error, forever.
            if not self.is_running():
                self._stop = threading.Event()
                self._thread = threading.Thread(
                    target=self._run, args=(self._stop,), daemon=True,
                    name=f'rtsp-worker-{sid[:6]}')
                self._thread.start()
        return q

    def unsubscribe(self, sid: str):
        # Pool lock first, matching _acquire_worker's order, so a subscribe
        # cannot slip in between "last subscriber left" and the worker leaving
        # the pool. It used to: the newcomer's thread was started, then killed
        # by this stop, and its worker evicted — the feed died on its own a
        # moment after opening.
        with _STREAM_POOL_LOCK:
            with self._lock:
                self._subs.pop(sid, None)
                if self._subs:
                    return
                self._stop.set()
                self._thread = None
            if _STREAM_POOL.get(self.rtsp_url) is self:
                del _STREAM_POOL[self.rtsp_url]

    def _push(self, msg: dict):
        with self._lock:
            items = list(self._subs.values())
        for q, loop in items:
            def _put(q=q, msg=msg):
                try:
                    q.put_nowait(msg)
                except asyncio.QueueFull:
                    try:  q.get_nowait()
                    except Exception: pass
                    try:  q.put_nowait(msg)
                    except Exception: pass
            loop.call_soon_threadsafe(_put)

    def _run(self, stop: threading.Event):
        # `stop` is this generation's Event, passed in rather than read off self:
        # a later subscribe swaps self._stop for a fresh one, and an older thread
        # reading self._stop would then never see its own stop signal.
        import cv2, base64 as _b64, time as _t
        retry = 0
        while not stop.is_set() and retry <= self.MAX_RETRIES:
            self._push({'type': 'status', 'connected': False,
                        'message': f'Connecting… (attempt {retry+1}/{self.MAX_RETRIES+1})'})

            cap = RtspStreamConsumer._open_cap(self.rtsp_url)
            if not cap or not cap.isOpened():
                if cap: cap.release()
                retry += 1
                _t.sleep(self.RETRY_DELAY)
                continue

            retry = 0
            self._push({'type': 'status', 'connected': True, 'message': 'Stream connected.'})
            logger.info('[StreamWorker] Opened %s', self.rtsp_url)

            # Drain thread so grab() never blocks the broadcast loop
            latest       = {'data': None, 'ok': False}
            drain_stop   = threading.Event()
            cap_released = threading.Event()

            def _drain():
                errs, grabs = 0, 0
                try:
                    while not drain_stop.is_set():
                        try:
                            if not cap.grab():
                                errs += 1
                                if errs > 20: latest['ok'] = False; break
                                _t.sleep(0.02); continue
                            errs = 0; grabs += 1
                            ok, frm = cap.retrieve()
                            if ok and frm is not None:
                                latest['data'] = frm; latest['ok'] = True
                        except Exception:
                            errs += 1
                            if errs > 20: break
                            _t.sleep(0.02)
                finally:
                    try: cap.release()
                    except Exception: pass
                    cap_released.set()

            dt = threading.Thread(target=_drain, daemon=True)
            dt.start()

            # Wait up to 15 s for the first frame
            for _ in range(300):
                if latest['ok'] or cap_released.is_set(): break
                _t.sleep(0.05)
            else:
                self._push({'type': 'status', 'connected': False,
                            'message': 'Stream timed out (no frames received).'})
                drain_stop.set()
                cap_released.wait(35)
                retry += 1
                continue

            try:
                while not stop.is_set() and not cap_released.is_set():
                    t0 = _t.monotonic()
                    frm = latest['data']
                    if not latest['ok'] or frm is None:
                        self._push({'type': 'status', 'connected': False,
                                    'message': 'Stream dropped. Reconnecting…'})
                        break
                    ok, buf = cv2.imencode('.jpg', frm, [cv2.IMWRITE_JPEG_QUALITY, 85])
                    if ok:
                        self._push({'type': 'frame',
                                    'image_b64': _b64.b64encode(buf.tobytes()).decode()})
                    wait = self.FRAME_INTERVAL - (_t.monotonic() - t0)
                    if wait > 0: _t.sleep(wait)
            finally:
                drain_stop.set()
                cap_released.wait(35)

        if retry > self.MAX_RETRIES:
            self._push({'type': 'error',
                        'message': 'Cannot connect to RTSP stream. '
                                   'Check the URL and ensure the backend has network access to the camera.'})

        # A worker whose capture thread has given up must not stay in the pool.
        # It used to, whenever a subscriber was still attached, and the next
        # viewer adopted the corpse: subscribe() saw a non-empty _subs, started
        # nothing, and handed back a queue with no producer.
        # `self._stop is stop` keeps an older generation's exit from evicting a
        # newer, live one.
        with _STREAM_POOL_LOCK:
            with self._lock:
                mine = self._stop is stop
            if mine and _STREAM_POOL.get(self.rtsp_url) is self:
                del _STREAM_POOL[self.rtsp_url]
        logger.info('[StreamWorker] Stopped for %s', self.rtsp_url)


_STREAM_POOL: dict[str, _StreamWorker] = {}
_STREAM_POOL_LOCK = threading.Lock()


def _acquire_worker(rtsp_url: str, sid: str, loop: 'asyncio.AbstractEventLoop'):
    """Get-or-create the worker for this URL and subscribe to it in one step.

    Looking the worker up and then subscribing to it had to become atomic:
    between the two, the last remaining subscriber could drop, which stopped the
    capture thread and pulled the worker out of the pool. The newcomer was left
    holding a worker nobody was feeding and nobody would ever restart.
    """
    with _STREAM_POOL_LOCK:
        worker = _STREAM_POOL.get(rtsp_url)
        if worker is None:
            worker = _STREAM_POOL[rtsp_url] = _StreamWorker(rtsp_url)
        q = worker.subscribe(sid, loop)
    return worker, q


class RtspStreamConsumer(AsyncJsonWebsocketConsumer):
    """
    WebSocket consumer: reads an RTSP IP-camera stream server-side (OpenCV + FFmpeg),
    pushes JPEG frames + plate-scan results back to the browser.

    Multiple consumers pointing at the same RTSP URL share ONE VideoCapture via
    _StreamWorker — the camera only receives a single connection regardless of how
    many browser tabs/users are watching.

    Client → Server:
        {"type": "start",  "rtsp_url": "rtsp://...", "gate_id": "gate1"}
        {"type": "stop"}

    Server → Client:
        {"type": "connected",  "message": "..."}
        {"type": "status",     "connected": bool, "message": "..."}
        {"type": "frame",      "image_b64": "<base64 JPEG>"}
        {"type": "tracks",     "tracks": [...], "frame_id": int}
        {"type": "ocr_update", "track_id": int,  "plate_text": "..."}
        {"type": "result",     "results": [...]}
        {"type": "error",      "message": "..."}
    """

    # ── lifecycle ──────────────────────────────────────────────────────────────

    async def connect(self):
        qs = self.scope["query_string"].decode()
        token_key = (
            qs.split("token=")[-1].split("&")[0]
            if "token=" in qs
            else None
        )
        if not token_key:
            await self.close(code=4001, reason="Authentication required")
            return
        self._user = await ScanLiveConsumer._get_user_from_token(token_key)
        if self._user is None:
            await self.close(code=4001, reason="Invalid token")
            return

        # detect=1 in the query string enables plate-scan ML.
        # Omit or set detect=0 for view-only connections (Device Management,
        # Operations Center) so they never run detection or OCR.
        self._scan_enabled = "detect=1" in qs

        # Shared scan state (mirrors ScanLiveConsumer.__init__ block)
        self._tracker               = ProximityTracker()
        self._frame_counter         = 0
        self._pending_ocr: dict     = {}
        self._ocr_state: dict       = {}
        self._announced: dict       = {}  # plate → decided_at last sent to this client
        self._detection_in_progress = False
        self._last_img_w            = 1280
        self._last_img_h            = 720
        self._stream_task           = None
        self._detect_tasks: set     = set()   # tracked so we can cancel on disconnect
        try:
            from vehicles.models import SystemSettings
            cfg = await sync_to_async(SystemSettings.get)()
            self._dedup_seconds = cfg.scan_dedup_seconds
        except Exception:
            self._dedup_seconds = _DEFAULT_DEDUP_SECONDS
        # Default to the guard's own gate; a camera-configured gate (sent in the
        # 'start' message) overrides this below.
        self._gate_id               = _resolve_gate('', self._user)
        self._loop                  = asyncio.get_running_loop()
        # uuid, not id(self): CPython reuses object addresses, so two consumers
        # could hold the same subscriber key and unsubscribe each other.
        self._worker_sid            = uuid.uuid4().hex

        await self.accept()
        logger.info("[RTSP] Connected: user=%s scan=%s", self._user, self._scan_enabled)

        # Register ML status listener — forwards loading stage events to this WS client
        from .ml.detection import add_ml_status_listener
        _loop = self._loop
        async def _send_ml_status(stage, message):
            try:
                await self.send_json({"type": "ml_status", "stage": stage, "message": message})
            except Exception:
                pass
        def _ml_status_listener(stage, message):
            asyncio.run_coroutine_threadsafe(_send_ml_status(stage, message), _loop)
        self._ml_status_listener = _ml_status_listener
        add_ml_status_listener(_ml_status_listener)  # immediately delivers current status
        await self.send_json({"type": "connected", "message": "RTSP consumer ready."})

    async def disconnect(self, code):
        logger.info("[RTSP] Disconnect code=%s", code)
        from .ml.detection import remove_ml_status_listener
        if hasattr(self, '_ml_status_listener'):
            remove_ml_status_listener(self._ml_status_listener)
        await self._cancel_stream()

    # ── receive ────────────────────────────────────────────────────────────────

    async def receive_json(self, content: dict):
        msg_type = content.get("type", "")

        if msg_type == "start":
            rtsp_url = content.get("rtsp_url", "").strip()
            if not rtsp_url or not rtsp_url.lower().startswith("rtsp://"):
                await self.send_json({"type": "error", "message": "Invalid or missing RTSP URL."})
                return
            if content.get("gate_id"):
                self._gate_id = _resolve_gate(content["gate_id"], self._user)
            await self._cancel_stream()
            # Reset tracker state for fresh stream
            self._tracker       = ProximityTracker()
            self._ocr_state     = {}
            self._pending_ocr   = {}
            self._announced     = {}
            self._frame_counter = 0
            self._stream_task   = asyncio.create_task(self._consume_stream(rtsp_url))

        elif msg_type == "stop":
            await self._cancel_stream()
            await self.send_json({"type": "status", "connected": False, "message": "Stream stopped."})

    # ── stream management ──────────────────────────────────────────────────────

    async def _cancel_stream(self):
        # Cancel all in-flight detection/OCR tasks first so they don't keep
        # logging after the stream stops (executor threads finish on their own
        # but the async wrappers — and their log calls — are stopped here).
        for t in list(getattr(self, '_detect_tasks', ())):
            t.cancel()
        self._detect_tasks = set()

        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass
        self._stream_task = None

    async def _consume_stream(self, rtsp_url: str):
        """Subscribe to the shared _StreamWorker for this URL and process frames."""
        import base64 as _b64
        worker, q = _acquire_worker(rtsp_url, self._worker_sid, self._loop)
        # Track whether we've told the frontend the stream is connected.
        # A late-joining subscriber won't receive the worker's initial status
        # broadcast, so we synthesise it on the first frame we see.
        sent_connected = False
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=45.0)
                except asyncio.TimeoutError:
                    # Nothing at all for 45 s. If the capture thread is gone,
                    # waiting longer cannot help — report it instead of holding
                    # a black canvas open in silence, which is how a dead worker
                    # used to present itself.
                    if not worker.is_running():
                        await self.send_json({
                            "type": "error",
                            "message": "The camera stream stopped and could not be restarted.",
                        })
                        await self.close()
                        return
                    logger.warning("[RTSP] No frames for 45 s — still waiting")
                    continue

                msg_type = msg.get("type")

                if msg_type == "frame":
                    if not sent_connected:
                        # Subscriber joined after the worker was already streaming;
                        # synthesise the connected status so the UI badge updates.
                        await self.send_json({
                            "type": "status", "connected": True,
                            "message": "Stream connected.",
                        })
                        sent_connected = True
                    await self.send_json({"type": "frame", "image_b64": msg["image_b64"]})
                    if self._scan_enabled and not self._detection_in_progress:
                        self._detection_in_progress = True
                        jpeg_bytes = _b64.b64decode(msg["image_b64"])
                        task = asyncio.create_task(self._detect_and_scan(jpeg_bytes))
                        self._detect_tasks.add(task)
                        task.add_done_callback(self._detect_tasks.discard)

                elif msg_type == "status":
                    await self.send_json(msg)
                    if msg.get("connected"):
                        sent_connected = True

                elif msg_type == "error":
                    await self.send_json(msg)
                    # Close the WebSocket so the frontend knows to reconnect;
                    # simply returning would leave the WS open with no stream.
                    await self.close()
                    return

        except asyncio.CancelledError:
            raise
        finally:
            worker.unsubscribe(self._worker_sid)

    # ── detection + scan pipeline ──────────────────────────────────────────────

    async def _detect_and_scan(self, jpeg_bytes: bytes):
        # Keep the latest frame — attached as evidence when a scan auto-issues a violation
        self._last_frame_jpeg = jpeg_bytes
        loop = asyncio.get_running_loop()
        try:
            detections = await loop.run_in_executor(None, self._run_detection, jpeg_bytes)
        except Exception as exc:
            logger.error("[RTSP] Detection error: %s", exc)
            detections = []
        finally:
            self._detection_in_progress = False

        now            = timezone.now()
        tracker_output = self._tracker.update(detections, img_w=self._last_img_w)
        det_by_idx     = {i: d for i, d in enumerate(detections)}

        # Evict OCR state for tracks the tracker has expired — prevents unbounded growth
        active_ids = set(self._tracker.tracks.keys())
        for stale_id in list(self._ocr_state.keys()):
            if stale_id not in active_ids:
                self._ocr_state.pop(stale_id, None)
                self._pending_ocr.pop(stale_id, None)

        active_tracks      = []
        tracks_needing_ocr = []
        tracks_to_reverify = []
        now_ts             = time.time()

        for t_out in tracker_output:
            track_id      = t_out["track_id"]
            bbox          = t_out["bbox"]
            x, y, bw, bh  = bbox["x"], bbox["y"], bbox["width"], bbox["height"]

            class_name   = t_out.get("class_name", "")
            vehicle_type = t_out.get("vehicle_type")
            plate_text   = t_out.get("plate_text", "")
            ocr_done     = t_out.get("ocr_done", False)

            d_idx = t_out.get("detection_index")
            det   = det_by_idx.get(d_idx) if d_idx is not None else None

            if (det and det.get("class_name") == "license_plate"
                    and det.get("crop") is not None):
                if not ocr_done:
                    tracks_needing_ocr.append(
                        (track_id, det["crop"], det.get("aspect_ratio", 1.0))
                    )
                else:
                    st = self._ocr_state.get(track_id)
                    if (st and st.get("locked")
                            and now_ts - st.get("verify_at", 0.0) >= _OCR_REVERIFY_SECONDS):
                        st["verify_at"] = now_ts  # claim before queueing
                        tracks_to_reverify.append(
                            (track_id, det["crop"], det.get("aspect_ratio", 1.0))
                        )

            w_img = self._last_img_w
            h_img = self._last_img_h
            active_tracks.append({
                "track_id":       track_id,
                "plate_text":     plate_text,
                "vehicle_type":   vehicle_type,
                "class_name":     class_name,
                "bbox":           [
                    x  / max(w_img, 1), y  / max(h_img, 1),
                    (x + bw) / max(w_img, 1), (y + bh) / max(h_img, 1),
                ],
                "detection_conf": det.get("confidence", 0.0) if det else 0.0,
            })

        await self.send_json({
            "type":     "tracks",
            "tracks":   active_tracks,
            "frame_id": self._frame_counter,
        })

        if tracks_needing_ocr:
            asyncio.create_task(self._run_ocr_for_tracks(tracks_needing_ocr))
        if tracks_to_reverify:
            asyncio.create_task(self._reverify_locked_tracks(tracks_to_reverify))

        if any(t.get("plate_text") for t in active_tracks):
            await self._process_scan_results(active_tracks, now)

    # ── sync helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _open_cap(rtsp_url: str):
        """Open OpenCV VideoCapture with robust FFmpeg RTSP options."""
        import cv2
        import os
        # _OPEN_CAP_LOCK ensures only one VideoCapture is being constructed at a
        # time.  os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] is read by FFmpeg
        # at VideoCapture() construction; concurrent writes + reads on Windows
        # (where putenv() is not thread-safe) can strip options for one camera
        # when two cameras connect simultaneously.
        with _OPEN_CAP_LOCK:
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
                "rtsp_transport;tcp"
                "|buffer_size;2097152"
                "|stimeout;10000000"
                "|threads;1"
                "|err_detect;ignore_err"
                "|fflags;discardcorrupt"
            )
            cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)              # hint: minimal decoded frame buffer
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 10000)   # 10 s open timeout
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000)    # 5 s read timeout
        return cap

    @staticmethod
    def _encode_frame(frame) -> "bytes | None":
        import cv2
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        return buf.tobytes() if ok else None

    def _run_detection(self, jpeg_bytes: bytes) -> list:
        import cv2
        import numpy as np
        nparr = np.frombuffer(jpeg_bytes, np.uint8)
        img   = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return []

        self._frame_counter += 1
        h, w = img.shape[:2]
        self._last_img_w = w
        self._last_img_h = h

        plate_tracks = [t for t in self._tracker.tracks.values()
                        if t.class_name == "license_plate"]
        all_plates_locked = bool(plate_tracks) and all(t.ocr_done for t in plate_tracks)
        detections = detect_plates(img, try_rotation=not all_plates_locked)

        out = []
        for i, det in enumerate(detections):
            bb = det["bbox"]
            out.append({
                "bbox": {
                    "x":      int(bb["x"]      * w),
                    "y":      int(bb["y"]      * h),
                    "width":  int(bb["width"]  * w),
                    "height": int(bb["height"] * h),
                },
                "crop":            det.get("crop"),
                "confidence":      det["score"],
                "aspect_ratio":    det.get("aspect_ratio", 1.0),
                "class_name":      det.get("class_name", ""),
                "vehicle_type":    det.get("vehicle_type"),
                "detection_index": i,
            })
        return out

    # Reuse async/sync helpers from ScanLiveConsumer (method assignment works in Python 3
    # because unbound functions become properly-bound methods when accessed on an instance)
    _run_ocr_for_tracks    = ScanLiveConsumer._run_ocr_for_tracks
    _reverify_locked_tracks = ScanLiveConsumer._reverify_locked_tracks
    _finalize_plate        = ScanLiveConsumer._finalize_plate
    _handle_plate_sighting = ScanLiveConsumer._handle_plate_sighting
    _process_scan_results  = ScanLiveConsumer._process_scan_results
    _result_hold_seconds   = ScanLiveConsumer._result_hold_seconds
    _evict_presence        = ScanLiveConsumer._evict_presence
    _save_snapshot         = ScanLiveConsumer._save_snapshot
    _save_to_db            = ScanLiveConsumer._save_to_db
    _check_vehicle         = ScanLiveConsumer._check_vehicle
    _check_supplier        = ScanLiveConsumer._check_supplier
    _record_ml_sample      = ScanLiveConsumer._record_ml_sample
