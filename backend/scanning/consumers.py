import logging
import base64
import time
from datetime import datetime
from typing import Any
import asyncio
from asgiref.sync import sync_to_async
from django.utils import timezone
from django.conf import settings
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .ml.detection import detect_plates, is_gpu_available, VEHICLE_TYPE_CLASSES
from .ml.database import save_record as db_save_record
from .ml.proximity_tracker import ProximityTracker
from .ml.reader import _ocr_crop, requires_digital_id

logger = logging.getLogger(__name__)

SNAPSHOT_DIR = "snapshots"

FRAME_RATE_LIMIT_MS = 100
PLATE_DEDUP_SECONDS = 30

# Per-track OCR accumulation settings
_OCR_LOCK_CONF    = 0.70   # lock immediately if any single read reaches this
_OCR_MIN_CONF     = 0.20   # ignore reads below this threshold
_OCR_MAX_ATTEMPTS = 5      # force-lock after this many attempts (keeps best vote)


class ScanLiveConsumer(AsyncJsonWebsocketConsumer):

    # ── lifecycle ──────────────────────────────────────────────────────────────

    async def connect(self):
        logger.info("[WS] Connection attempt from %s", self.scope.get("REMOTE_ADDR", "unknown"))
        token_key = (
            self.scope["query_string"].decode().split("token=")[-1].split("&")[0]
            if "token=" in self.scope["query_string"].decode()
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

        self._tracker = ProximityTracker()
        self._frame_counter = 0
        self._pending_ocr: dict[int, bool] = {}
        # track_id → {votes, attempts, locked}
        self._ocr_state: dict[int, dict] = {}

        # plate_text → (processed_at_timestamp, cached_result_dict)
        self._plate_cache: dict[str, tuple[float, dict]] = {}

        self._fps = 0.0
        self._fps_counter = 0
        self._fps_start: float | None = None
        self._last_process_time: float = 0.0
        self._detection_in_progress = False

        await self.accept()
        logger.info("[WS] Connection accepted for user: %s", self._user)
        await self.send_json({"type": "connected", "message": "Stream ready.", "gpu": is_gpu_available()})

    async def disconnect(self, code):
        logger.info("[WS] Disconnecting with code %s", code)

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
        tracker_output = self._tracker.update(detections)
        det_by_idx = {i: d for i, d in enumerate(detections)}

        active_tracks = []
        tracks_needing_ocr = []
        tracks_needing_id = []

        for t_out in tracker_output:
            track_id    = t_out["track_id"]
            bbox        = t_out["bbox"]
            x, y, bw, bh = bbox["x"], bbox["y"], bbox["width"], bbox["height"]

            # class_name/vehicle_type live in t_out for both matched detections
            # and synthetic persisted-track entries from the tracker.
            class_name   = t_out.get("class_name", "")
            vehicle_type = t_out.get("vehicle_type")
            plate_text   = t_out.get("plate_text", "")
            ocr_done     = t_out.get("ocr_done", False)

            # Original detection dict (needed for crop/confidence); None for
            # persisted tracks that had no matching detection this frame.
            d_idx = t_out.get("detection_index")
            det   = det_by_idx.get(d_idx) if d_idx is not None else None

            if requires_digital_id(class_name) and vehicle_type:
                tracks_needing_id.append(
                    (track_id, vehicle_type, det["confidence"] if det else 0.0)
                )
                ocr_done = True
            elif (not ocr_done and det
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

        await self.send_json({
            "type":     "tracks",
            "tracks":   active_tracks,
            "frame_id": self._frame_counter,
            "fps":      round(self._fps, 1),
        })

        if tracks_needing_ocr:
            asyncio.create_task(self._run_ocr_for_tracks(tracks_needing_ocr))

        for track_id, vehicle_type, conf in tracks_needing_id:
            await self.send_json({
                "type":         "id_required",
                "track_id":     track_id,
                "vehicle_type": vehicle_type,
                "message":      f"Please show digital ID for {vehicle_type.replace('_', ' ')} entry",
            })

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

        detections = detect_plates(img)
        h, w = img.shape[:2]
        self._last_img_w = w
        self._last_img_h = h

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
        cached = self._plate_cache.get(plate_text)
        now_ts = time.time()
        if cached and (now_ts - cached[0]) < PLATE_DEDUP_SECONDS:
            logger.info("[WS] Plate %s in dedup window — skipping DB write", plate_text)
            await self.send_json({"type": "result", "results": [cached[1]]})
            return
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

    # ── process tracks that already have plate text ────────────────────────────

    async def _process_scan_results(self, tracks_list: list[dict], now):
        results = []
        processed_ids: set[int] = set()

        for track_data in tracks_list:
            track_id     = track_data["track_id"]
            plate_number = track_data.get("plate_text", "")
            det_conf     = track_data.get("detection_conf", 0.0)
            bbox = {
                "x": track_data["bbox"][0], "y": track_data["bbox"][1],
                "width": track_data["bbox"][2], "height": track_data["bbox"][3],
            }

            if not plate_number or track_id in processed_ids:
                continue
            processed_ids.add(track_id)

            # Dedup: re-use cached result within PLATE_DEDUP_SECONDS
            cached = self._plate_cache.get(plate_number)
            now_ts = time.time()
            if cached and (now_ts - cached[0]) < PLATE_DEDUP_SECONDS:
                results.append(cached[1])
                continue

            try:
                await sync_to_async(self._save_to_db)(
                    track_id, plate_number, det_conf, 0.0, bbox, None
                )
            except Exception as exc:
                logger.error("[WS] DB save failed for %s: %s", plate_number, exc)

            try:
                enriched = await sync_to_async(
                    self._check_vehicle, thread_sensitive=True
                )(plate_number, bbox)
                enriched["plate_number"] = plate_number
                enriched["bbox"]         = bbox

                self._plate_cache[plate_number] = (time.time(), enriched)
                self._evict_cache()

                results.append(enriched)
            except Exception as exc:
                logger.error("[WS] Vehicle check failed for %s: %s", plate_number, exc)

        if results:
            await sync_to_async(self._record_ml_sample)(None, results)
            await self.send_json({"type": "result", "results": results})

    # ── cache maintenance ──────────────────────────────────────────────────────

    def _evict_cache(self):
        """Remove entries older than 2× the dedup window to bound memory."""
        cutoff = time.time() - PLATE_DEDUP_SECONDS * 2
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
        from .models import PlateRecognitionRecord
        PlateRecognitionRecord.objects.create(
            track_id=track_id,
            plate_text=plate_number,
            detection_confidence=det_conf,
            ocr_confidence=ocr_conf,
            timestamp=timezone.now(),
            snapshot_path=snapshot_path or "",
        )

    def _check_vehicle(self, plate_number: str, bbox):
        from vehicles.models import Vehicle
        from .models import AccessLog
        from .entry_logic import check_entry
        from violations.models import Violation
        from vehicles.serializers import VehicleSerializer
        from accounts.models import AuditLog

        vehicle = Vehicle.objects.select_related("owner").filter(
            plate_number=plate_number
        ).first()

        if not vehicle:
            AccessLog.objects.create(
                plate_number=plate_number,
                status="unknown",
                scanned_by=self._user,
            )
            return {
                "status":         "unknown",
                "allowed":        False,
                "message":        "Plate not registered.",
                "constraint":     None,
                "vehicle":        None,
                "has_violations": False,
            }

        entry = check_entry(vehicle)
        has_violations = Violation.objects.filter(
            vehicle=vehicle, is_resolved=False
        ).exists()

        AccessLog.objects.create(
            plate_number=plate_number,
            vehicle=vehicle,
            status=entry["status"],
            denied_reason="" if entry["allowed"] else entry["message"],
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

        return {
            "status":         entry["status"],
            "allowed":        entry["allowed"],
            "message":        entry["message"],
            "constraint":     entry.get("constraint"),
            "vehicle":        VehicleSerializer(vehicle).data,
            "has_violations": has_violations,
        }

    def _record_ml_sample(self, raw_bytes, results):
        from .models import MLTrainingSample
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
        import jwt as _jwt

        User = get_user_model()
        try:
            decoded = _jwt.decode(token_key, options={"verify_signature": False})
            user = await sync_to_async(User.objects.get)(pk=decoded["user_id"])
            return user
        except (_jwt.ExpiredSignatureError, _jwt.InvalidTokenError, User.DoesNotExist):
            return None


class RtspStreamConsumer(AsyncJsonWebsocketConsumer):
    """
    WebSocket consumer: reads an RTSP IP-camera stream server-side (OpenCV + FFmpeg),
    pushes JPEG frames + plate-scan results back to the browser.

    Client → Server:
        {"type": "start",  "rtsp_url": "rtsp://..."}
        {"type": "stop"}

    Server → Client:
        {"type": "connected",  "message": "..."}
        {"type": "status",     "connected": bool, "message": "..."}
        {"type": "frame",      "image_b64": "<base64 JPEG>"}
        {"type": "tracks",     "tracks": [...], "frame_id": int}
        {"type": "ocr_update", "track_id": int,  "plate_text": "..."}
        {"type": "id_required","track_id": int,  "vehicle_type": "..."}
        {"type": "result",     "results": [...]}
        {"type": "error",      "message": "..."}
    """

    FRAME_RATE  = 20    # fps to send to the frontend (20 = 50ms per frame)
    MAX_RETRIES = 3     # reconnect attempts before giving up
    RETRY_DELAY = 2.0   # seconds between reconnect attempts

    # ── lifecycle ──────────────────────────────────────────────────────────────

    async def connect(self):
        token_key = (
            self.scope["query_string"].decode().split("token=")[-1].split("&")[0]
            if "token=" in self.scope["query_string"].decode()
            else None
        )
        if not token_key:
            await self.close(code=4001, reason="Authentication required")
            return
        self._user = await ScanLiveConsumer._get_user_from_token(token_key)
        if self._user is None:
            await self.close(code=4001, reason="Invalid token")
            return

        # Shared scan state (mirrors ScanLiveConsumer.__init__ block)
        self._tracker               = ProximityTracker()
        self._frame_counter         = 0
        self._pending_ocr: dict     = {}
        self._ocr_state: dict       = {}
        self._plate_cache: dict     = {}
        self._detection_in_progress = False
        self._last_img_w            = 1280
        self._last_img_h            = 720
        self._stream_task           = None

        await self.accept()
        logger.info("[RTSP] Connected: user=%s", self._user)
        await self.send_json({"type": "connected", "message": "RTSP consumer ready."})

    async def disconnect(self, code):
        logger.info("[RTSP] Disconnect code=%s", code)
        await self._cancel_stream()

    # ── receive ────────────────────────────────────────────────────────────────

    async def receive_json(self, content: dict):
        msg_type = content.get("type", "")

        if msg_type == "start":
            rtsp_url = content.get("rtsp_url", "").strip()
            if not rtsp_url or not rtsp_url.lower().startswith("rtsp://"):
                await self.send_json({"type": "error", "message": "Invalid or missing RTSP URL."})
                return
            await self._cancel_stream()
            # Reset tracker state for fresh stream
            self._tracker       = ProximityTracker()
            self._ocr_state     = {}
            self._pending_ocr   = {}
            self._plate_cache   = {}
            self._frame_counter = 0
            self._stream_task   = asyncio.create_task(self._capture_loop(rtsp_url))

        elif msg_type == "stop":
            await self._cancel_stream()
            await self.send_json({"type": "status", "connected": False, "message": "Stream stopped."})

    # ── stream management ──────────────────────────────────────────────────────

    async def _cancel_stream(self):
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass
        self._stream_task = None

    async def _capture_loop(self, rtsp_url: str):
        import cv2
        import threading
        loop     = asyncio.get_running_loop()
        retry    = 0
        interval = 1.0 / self.FRAME_RATE

        while True:
            # ── open RTSP capture ─────────────────────────────────────────────
            await self.send_json({
                "type": "status", "connected": False,
                "message": f"Connecting… (attempt {retry + 1}/{self.MAX_RETRIES + 1})",
            })

            cap = await loop.run_in_executor(None, self._open_cap, rtsp_url)
            if cap is None or not cap.isOpened():
                if cap is not None:
                    await loop.run_in_executor(None, cap.release)
                retry += 1
                if retry > self.MAX_RETRIES:
                    await self.send_json({
                        "type": "error",
                        "message": "Cannot connect to RTSP stream. Check the URL and ensure the backend has network access to the camera.",
                    })
                    return
                await asyncio.sleep(self.RETRY_DELAY)
                continue

            retry = 0
            logger.info("[RTSP] Stream opened: %s", rtsp_url)
            await self.send_json({"type": "status", "connected": True, "message": "Stream connected."})

            # ── start background drain thread ─────────────────────────────────
            # A dedicated thread continuously calls cap.grab() (decode-free read)
            # so the internal buffer never fills with stale frames. The main loop
            # calls cap.retrieve() only when it needs to actually display a frame.
            latest_frame     = {"data": None, "ok": False}
            drain_running     = threading.Event()
            drain_running.set()

            def _drain():
                """Continuously drain the RTSP buffer; keep only the latest frame."""
                while drain_running.is_set():
                    ret = cap.grab()  # fast — no decode
                    if not ret:
                        latest_frame["ok"] = False
                        break
                    # Only decode every N grabs so we always have a recent frame ready
                    ret2, frm = cap.retrieve()
                    if ret2 and frm is not None:
                        latest_frame["data"] = frm
                        latest_frame["ok"]   = True

            drain_thread = threading.Thread(target=_drain, daemon=True)
            drain_thread.start()

            # ── wait for first frame (up to 8 s) ───────────────────────────────
            # The drain thread starts with ok=False. Without this wait the send
            # loop below would immediately see a "failed" state and reconnect.
            for _ in range(160):   # 160 × 0.05 s = 8 s
                if latest_frame["ok"] or not drain_running.is_set():
                    break
                await asyncio.sleep(0.05)
            else:
                # Timed out — no frames in 8 s
                logger.warning("[RTSP] No frames in 8 s — reconnecting")
                await self.send_json({"type": "status", "connected": False,
                                      "message": "Stream timed out (no frames received)."})
                drain_running.clear()
                drain_thread.join(timeout=2)
                await loop.run_in_executor(None, cap.release)
                retry += 1
                if retry > self.MAX_RETRIES:
                    await self.send_json({"type": "error",
                                          "message": "Stream error — too many retries."})
                    await self.close()
                    return
                continue

            # ── frame send loop ───────────────────────────────────────────────
            try:
                while True:
                    t0 = loop.time()

                    # Get the frame the drain thread prepared (always the latest)
                    frame = latest_frame["data"]
                    if not latest_frame["ok"] or frame is None:
                        # Drain thread lost the stream
                        logger.warning("[RTSP] Stream read failed — reconnecting")
                        await self.send_json({
                            "type": "status", "connected": False,
                            "message": "Stream dropped. Reconnecting…",
                        })
                        break  # exit inner loop → retry connect

                    jpeg_bytes = await loop.run_in_executor(None, self._encode_frame, frame)
                    if jpeg_bytes:
                        await self.send_json({
                            "type":      "frame",
                            "image_b64": base64.b64encode(jpeg_bytes).decode("utf-8"),
                        })
                        if not self._detection_in_progress:
                            self._detection_in_progress = True
                            asyncio.create_task(self._detect_and_scan(jpeg_bytes))

                    # Pace ourselves to FRAME_RATE; sleep the remainder
                    elapsed = loop.time() - t0
                    wait    = max(0.0, interval - elapsed)
                    if wait > 0.001:
                        await asyncio.sleep(wait)

            except asyncio.CancelledError:
                logger.info("[RTSP] Capture task cancelled.")
                raise
            except Exception as exc:
                logger.error("[RTSP] Capture error: %s", exc)
                retry += 1
                if retry > self.MAX_RETRIES:
                    await self.send_json({"type": "error", "message": "Stream error — too many retries."})
                    await self.close()   # tell the frontend the WS is done
                    return
                await asyncio.sleep(self.RETRY_DELAY)
            finally:
                drain_running.clear()          # signal drain thread to stop
                drain_thread.join(timeout=2)   # wait for it to exit
                await loop.run_in_executor(None, cap.release)

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
        tracker_output = self._tracker.update(detections)
        det_by_idx     = {i: d for i, d in enumerate(detections)}

        active_tracks      = []
        tracks_needing_ocr = []
        tracks_needing_id  = []

        for t_out in tracker_output:
            track_id      = t_out["track_id"]
            bbox          = t_out["bbox"]
            x, y, bw, bh  = bbox["x"], bbox["y"], bbox["width"], bbox["height"]

            # class_name/vehicle_type live in t_out for both matched detections
            # and synthetic persisted-track entries from the tracker.
            class_name   = t_out.get("class_name", "")
            vehicle_type = t_out.get("vehicle_type")
            plate_text   = t_out.get("plate_text", "")
            ocr_done     = t_out.get("ocr_done", False)

            # Original detection dict (needed for crop/confidence); None for
            # persisted tracks that had no matching detection this frame.
            d_idx = t_out.get("detection_index")
            det   = det_by_idx.get(d_idx) if d_idx is not None else None

            if requires_digital_id(class_name) and vehicle_type:
                tracks_needing_id.append(
                    (track_id, vehicle_type, det["confidence"] if det else 0.0)
                )
                ocr_done = True
            elif (not ocr_done and det
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

        await self.send_json({
            "type":     "tracks",
            "tracks":   active_tracks,
            "frame_id": self._frame_counter,
        })

        if tracks_needing_ocr:
            asyncio.create_task(self._run_ocr_for_tracks(tracks_needing_ocr))

        for track_id, vehicle_type, conf in tracks_needing_id:
            await self.send_json({
                "type":         "id_required",
                "track_id":     track_id,
                "vehicle_type": vehicle_type,
                "message":      f"Please show digital ID for {vehicle_type.replace('_', ' ')} entry",
            })

        if any(t.get("plate_text") for t in active_tracks):
            await self._process_scan_results(active_tracks, now)

    # ── sync helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _open_cap(rtsp_url: str):
        """Open OpenCV VideoCapture with low-latency FFmpeg RTSP options."""
        import cv2
        import os
        # Force TCP transport — more reliable, avoids UDP reordering jitter.
        # Must be set before VideoCapture() opens the stream.
        os.environ.setdefault(
            "OPENCV_FFMPEG_CAPTURE_OPTIONS",
            "rtsp_transport;tcp|buffer_size;0|max_delay;0|stimeout;3000000"
        )
        cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)             # hint: minimal buffer
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 8000)   # 8 s open timeout
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000)   # 5 s read timeout
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
        detections = detect_plates(img)
        h, w = img.shape[:2]
        self._last_img_w = w
        self._last_img_h = h

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
