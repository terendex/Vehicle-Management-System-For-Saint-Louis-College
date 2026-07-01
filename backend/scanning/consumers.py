import logging
import base64
import time
import threading
from datetime import datetime
from typing import Any
import asyncio
from asgiref.sync import sync_to_async
from django.utils import timezone
from django.conf import settings
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .ml.detection import detect_plates, is_gpu_available
from .ml.database import save_record as db_save_record
from .ml.proximity_tracker import ProximityTracker
from .ml.reader import _ocr_crop, find_plate_in_crop

logger = logging.getLogger(__name__)

SNAPSHOT_DIR = "snapshots"

# Serialise VideoCapture construction so concurrent camera connections don't
# race on os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"].  On Windows, putenv()
# is not guaranteed thread-safe, and one camera's options can clobber another's
# just before cv2.VideoCapture() reads them.
_OPEN_CAP_LOCK = threading.Lock()

FRAME_RATE_LIMIT_MS = 100
_DEFAULT_DEDUP_SECONDS = 10  # fallback used if DB is unavailable at connect time
CAMERA_ENTRY_COOLDOWN_SECONDS = 60  # breathing space: camera won't exit a vehicle within this window after entry

# Per-track OCR accumulation settings
_OCR_LOCK_CONF    = 0.50   # lock immediately if any single read reaches this
_OCR_MIN_CONF     = 0.08   # minimum confidence to count a read — low to handle noisy vehicle crops
_OCR_MAX_ATTEMPTS = 15     # more attempts before force-locking, helps accumulate votes


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
        self._gate_id = 'main'
        for part in qs.split('&'):
            if part.startswith('gate='):
                self._gate_id = part[5:] or 'main'
                break

        self._tracker = ProximityTracker()
        self._frame_counter = 0
        self._pending_ocr: dict[int, bool] = {}
        # track_id → {votes, attempts, locked}
        self._ocr_state: dict[int, dict] = {}

        # plate_text → (processed_at_timestamp, cached_result_dict)
        self._plate_cache: dict[str, tuple[float, dict]] = {}
        # plates currently being processed — prevents concurrent duplicate DB writes
        self._plates_in_flight: set[str] = set()

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

            if (not ocr_done and det
                  and det.get("class_name") == "license_plate"
                  and det.get("crop") is not None):
                tracks_needing_ocr.append(
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

        # Fallback: for MOTORCYCLE tracks with no plate detected, crop the lower half
        # of the motorcycle bounding box and run OCR directly on the plate area.
        # Restricted to motorcycle only — trucks/cars produce garbage OCR on their body panels.
        plate_class_ids = {t["track_id"] for t in active_tracks if t.get("class_name") == "license_plate"}
        for t_out in tracker_output:
            if t_out.get("vehicle_type") != "motorcycle":
                continue
            tid = t_out["track_id"]
            if tid in plate_class_ids:
                continue
            ocr_st = self._ocr_state.get(tid, {})
            if ocr_st.get("locked") or ocr_st.get("attempts", 0) >= _OCR_MAX_ATTEMPTS:
                continue
            d_idx = t_out.get("detection_index")
            det = det_by_idx.get(d_idx) if d_idx is not None else None
            if det and det.get("crop") is not None:
                crop = det["crop"]
                h = crop.shape[0]
                plate_region = crop[int(h * 0.30):, :]
                if plate_region.size > 0:
                    plate_region = find_plate_in_crop(plate_region)
                    tracks_needing_ocr.append((tid, plate_region, 2.0))

        await self.send_json({
            "type":     "tracks",
            "tracks":   active_tracks,
            "frame_id": self._frame_counter,
            "fps":      round(self._fps, 1),
        })

        if tracks_needing_ocr:
            asyncio.create_task(self._run_ocr_for_tracks(tracks_needing_ocr))

        # Re-broadcast cached results for tracks whose plates are already known
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
                        self._tracker.set_plate_text(track_id, best)
                        logger.info("[WS] Max attempts (no lock) track %d → %s", track_id, best)
                        await self._finalize_plate(track_id, best, 0.0)
                    continue

                # Accumulate confidence-weighted votes across reads
                state["votes"][plate_text] = state["votes"].get(plate_text, 0.0) + conf
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
                    self._tracker.set_plate_text(track_id, best)
                    logger.info("[WS] Locked track %d → %s (conf=%.2f, attempts=%d)",
                                track_id, best, conf, state["attempts"])
                    await self._finalize_plate(track_id, best, conf)

            except Exception as exc:
                logger.warning("[OCR] Failed for track %d: %s", track_id, exc)
            finally:
                self._pending_ocr.pop(track_id, None)

    async def _finalize_plate(self, track_id: int, plate_text: str, conf: float):
        """Write to DB and broadcast result once a plate is locked."""
        plate_text = plate_text.strip().upper().replace(' ', '')
        cached = self._plate_cache.get(plate_text)
        now_ts = time.time()
        if cached and (now_ts - cached[0]) < self._dedup_seconds:
            logger.info("[WS] Plate %s in dedup window — skipping DB write", plate_text)
            await self.send_json({"type": "result", "results": [cached[1]]})
            return
        _in_flight = getattr(self, '_plates_in_flight', None)
        if _in_flight is not None and plate_text in _in_flight:
            logger.info("[WS] Plate %s already in-flight — skipping finalize", plate_text)
            return
        if _in_flight is not None:
            _in_flight.add(plate_text)
        try:
            await sync_to_async(self._save_to_db)(
                track_id, plate_text, 0.0, conf, None, None
            )
            enriched = await sync_to_async(self._check_vehicle)(plate_text, None)
            enriched["plate_number"] = plate_text

            self._plate_cache[plate_text] = (time.time(), enriched)
            self._evict_cache()

            await sync_to_async(self._record_ml_sample)(None, [enriched])
            await self.send_json({"type": "result", "results": [enriched]})
        except Exception as db_exc:
            logger.error("[WS] DB error for plate %s: %s", plate_text, db_exc)
            self._plate_cache[plate_text] = (time.time(), {"plate_number": plate_text, "error": True})
        finally:
            if _in_flight is not None:
                _in_flight.discard(plate_text)

    # ── process tracks that already have plate text ────────────────────────────

    async def _process_scan_results(self, tracks_list: list[dict], now):
        results = []
        fresh_results = []
        processed_ids: set[int] = set()

        for track_data in tracks_list:
            track_id     = track_data["track_id"]
            plate_number = track_data.get("plate_text", "").strip().upper().replace(' ', '')
            det_conf     = track_data.get("detection_conf", 0.0)
            bbox = {
                "x": track_data["bbox"][0], "y": track_data["bbox"][1],
                "width": track_data["bbox"][2], "height": track_data["bbox"][3],
            }

            if not plate_number or track_id in processed_ids:
                continue
            processed_ids.add(track_id)

            # Dedup: re-use cached result within self._dedup_seconds
            cached = self._plate_cache.get(plate_number)
            now_ts = time.time()
            if cached and (now_ts - cached[0]) < self._dedup_seconds:
                # Already processed recently — skip DB and ML sample, just re-send UI result
                results.append(cached[1])
                continue

            try:
                await sync_to_async(self._save_to_db)(
                    track_id, plate_number, det_conf, 0.0, bbox, None
                )
            except Exception as exc:
                logger.error("[WS] DB save failed for %s: %s", plate_number, exc)

            _in_flight = getattr(self, '_plates_in_flight', None)
            if _in_flight is not None and plate_number in _in_flight:
                continue  # another coroutine is already processing this plate
            if _in_flight is not None:
                _in_flight.add(plate_number)
            try:
                enriched = await sync_to_async(
                    self._check_vehicle, thread_sensitive=True
                )(plate_number, bbox)
                enriched["plate_number"] = plate_number
                enriched["bbox"]         = bbox

                self._plate_cache[plate_number] = (time.time(), enriched)
                self._evict_cache()

                results.append(enriched)
                fresh_results.append(enriched)
            except Exception as exc:
                logger.error("[WS] Vehicle check failed for %s: %s", plate_number, exc)
                # Cache the failure so we don't retry every frame and spam the log
                self._plate_cache[plate_number] = (time.time(), {"plate_number": plate_number, "error": True})
            finally:
                if _in_flight is not None:
                    _in_flight.discard(plate_number)

        if fresh_results:
            # Only record ML sample for plates that were genuinely processed this call
            await sync_to_async(self._record_ml_sample)(None, fresh_results)

        if results:
            await self.send_json({"type": "result", "results": results})

    # ── cache maintenance ──────────────────────────────────────────────────────

    def _evict_cache(self):
        """Remove entries older than 2× the dedup window to bound memory."""
        cutoff = time.time() - self._dedup_seconds * 2
        stale = [k for k, (ts, _) in self._plate_cache.items() if ts < cutoff]
        for k in stale:
            del self._plate_cache[k]

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
        from vehicles.models import Vehicle, VehicleRegistration
        from .models import AccessLog
        from .entry_logic import check_entry
        from violations.models import Violation
        from vehicles.serializers import VehicleSerializer
        from accounts.models import AuditLog
        from .views import _inside_state, _in_exit_cooldown, _already_inside, _auto_log_violation
        close_old_connections()

        # Normalize so OCR output matches the stored plate (e.g. "ABC 123" → "ABC123")
        plate_number = plate_number.strip().upper().replace(' ', '')

        vehicle = Vehicle.objects.select_related("user").filter(
            plate_number=plate_number
        ).first()

        gate_id = getattr(self, '_gate_id', 'main')

        if not vehicle:
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
            seconds_since_entry = (timezone.now() - last_entry.scanned_at).total_seconds()
            if seconds_since_entry < CAMERA_ENTRY_COOLDOWN_SECONDS:
                # Within the 1-minute breathing space — ignore
                return {
                    "status":         "duplicate",
                    "allowed":        False,
                    "message":        "Within 1-minute entry window.",
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
            owner_name = vehicle.user.full_name if vehicle.user else 'Unknown'
            try:
                AuditLog.objects.create(
                    actor=self._user,
                    action="scan",
                    details=f"Auto-exit (camera) | Plate: {plate_number} | Owner: {owner_name} | Duration: {duration_minutes} min",
                )
            except Exception:
                pass
            return {
                "status":           "exited",
                "allowed":          False,
                "message":          f"{owner_name} — Exit recorded. Duration: {duration_minutes} min.",
                "vehicle":          VehicleSerializer(vehicle).data,
                "has_violations":   False,
                "already_inside":   False,
                "duration_minutes": duration_minutes,
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
        has_violations = Violation.objects.filter(
            vehicle=vehicle, is_resolved=False
        ).exists()
        already_inside = _already_inside(plate_number)

        AccessLog.objects.create(
            plate_number=plate_number,
            vehicle=vehicle,
            status=entry["status"],
            denied_reason="" if entry["allowed"] else entry["message"],
            gate_id=gate_id,
            scanned_by=self._user,
        )
        try:
            AuditLog.objects.create(
                actor=self._user,
                action="scan",
                details=f"Plate: {plate_number}, Status: {entry['status']}",
            )
        except Exception:
            pass

        if not entry["allowed"]:
            try:
                _auto_log_violation(vehicle, entry["message"])
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
        self._subs: dict[str, tuple['asyncio.Queue', 'asyncio.AbstractEventLoop']] = {}
        self._lock      = threading.Lock()
        self._ref_count = 0
        self._thread: threading.Thread | None = None
        self._stop      = threading.Event()

    def subscribe(self, sid: str, loop: 'asyncio.AbstractEventLoop') -> 'asyncio.Queue':
        q: asyncio.Queue = asyncio.Queue(maxsize=3)
        with self._lock:
            self._subs[sid] = (q, loop)
            self._ref_count += 1
            if self._ref_count == 1:
                self._stop.clear()
                self._thread = threading.Thread(target=self._run, daemon=True,
                                                name=f'rtsp-worker-{sid[:6]}')
                self._thread.start()
        return q

    def unsubscribe(self, sid: str):
        with self._lock:
            self._subs.pop(sid, None)
            self._ref_count -= 1
            last = self._ref_count == 0
        if last:
            self._stop.set()
            with _STREAM_POOL_LOCK:
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

    def _run(self):
        import cv2, base64 as _b64, time as _t
        retry = 0
        while not self._stop.is_set() and retry <= self.MAX_RETRIES:
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
                            if grabs % 2 == 0:
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
                while not self._stop.is_set() and not cap_released.is_set():
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
        logger.info('[StreamWorker] Stopped for %s', self.rtsp_url)


_STREAM_POOL: dict[str, _StreamWorker] = {}
_STREAM_POOL_LOCK = threading.Lock()


def _get_worker(rtsp_url: str) -> _StreamWorker:
    with _STREAM_POOL_LOCK:
        if rtsp_url not in _STREAM_POOL:
            _STREAM_POOL[rtsp_url] = _StreamWorker(rtsp_url)
        return _STREAM_POOL[rtsp_url]


class RtspStreamConsumer(AsyncJsonWebsocketConsumer):
    """
    WebSocket consumer: reads an RTSP IP-camera stream server-side (OpenCV + FFmpeg),
    pushes JPEG frames + plate-scan results back to the browser.

    Multiple consumers pointing at the same RTSP URL share ONE VideoCapture via
    _StreamWorker — the camera only receives a single connection regardless of how
    many browser tabs/users are watching.

    Client → Server:
        {"type": "start",  "rtsp_url": "rtsp://..."}
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
        self._plate_cache: dict     = {}
        self._plates_in_flight: set = set()  # prevents concurrent duplicate DB writes
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
        self._gate_id               = 'main'
        self._loop                  = asyncio.get_running_loop()
        self._worker_sid            = str(id(self))

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
                self._gate_id = content["gate_id"]
            await self._cancel_stream()
            # Reset tracker state for fresh stream
            self._tracker       = ProximityTracker()
            self._ocr_state     = {}
            self._pending_ocr   = {}
            self._plate_cache   = {}
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
        worker = _get_worker(rtsp_url)
        q = worker.subscribe(self._worker_sid, self._loop)
        # Track whether we've told the frontend the stream is connected.
        # A late-joining subscriber won't receive the worker's initial status
        # broadcast, so we synthesise it on the first frame we see.
        sent_connected = False
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=45.0)
                except asyncio.TimeoutError:
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

            if (not ocr_done and det
                  and det.get("class_name") == "license_plate"
                  and det.get("crop") is not None):
                tracks_needing_ocr.append(
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

        # Fallback: motorcycle detected but no plate class nearby → try OCR on lower crop.
        # Restricted to motorcycle only — trucks/cars produce garbage OCR on their body panels.
        plate_class_ids = {t["track_id"] for t in active_tracks if t.get("class_name") == "license_plate"}
        for t_out in tracker_output:
            if t_out.get("vehicle_type") != "motorcycle":
                continue
            tid = t_out["track_id"]
            if tid in plate_class_ids:
                continue
            ocr_st = self._ocr_state.get(tid, {})
            if ocr_st.get("locked") or ocr_st.get("attempts", 0) >= _OCR_MAX_ATTEMPTS:
                continue
            d_idx = t_out.get("detection_index")
            det = det_by_idx.get(d_idx) if d_idx is not None else None
            if det and det.get("crop") is not None:
                crop = det["crop"]
                h = crop.shape[0]
                plate_region = crop[int(h * 0.30):, :]
                if plate_region.size > 0:
                    plate_region = find_plate_in_crop(plate_region)
                    tracks_needing_ocr.append((tid, plate_region, 2.0))

        await self.send_json({
            "type":     "tracks",
            "tracks":   active_tracks,
            "frame_id": self._frame_counter,
        })

        if tracks_needing_ocr:
            asyncio.create_task(self._run_ocr_for_tracks(tracks_needing_ocr))

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
    _run_ocr_for_tracks   = ScanLiveConsumer._run_ocr_for_tracks
    _finalize_plate       = ScanLiveConsumer._finalize_plate
    _process_scan_results = ScanLiveConsumer._process_scan_results
    _evict_cache          = ScanLiveConsumer._evict_cache
    _save_snapshot        = ScanLiveConsumer._save_snapshot
    _save_to_db           = ScanLiveConsumer._save_to_db
    _check_vehicle        = ScanLiveConsumer._check_vehicle
    _record_ml_sample     = ScanLiveConsumer._record_ml_sample
