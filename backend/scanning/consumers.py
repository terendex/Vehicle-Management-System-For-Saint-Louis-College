import logging
import base64
import os
from datetime import datetime
from typing import Any
import asyncio
from collections import Counter
from asgiref.sync import sync_to_async
from django.utils import timezone
from django.conf import settings
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .ml.tracking import PlateTracker
from .ml.detection import detect_plates, is_gpu_available
from .ml.ocr import run_ocr, majority_vote_ocr
from .ml.database import save_record as db_save_record

logger = logging.getLogger(__name__)

OCR_INTERVAL_FRAMES = 10
SNAPSHOT_DIR = "snapshots"
FRAME_RATE_LIMIT_MS = 2000


class ScanLiveConsumer(AsyncJsonWebsocketConsumer):
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
        self._recent = {}
        self._tracker = PlateTracker()
        self._frame_counter = 0
        self._pending_ocr = {}
        self._fps = 0.0
        self._fps_counter = 0
        self._fps_start = None
        self._last_process_time = 0
        await self.accept()
        logger.info("[WS] Connection accepted for user: %s", self._user)
        await self.send_json({"type": "connected", "message": "Stream ready.", "gpu": is_gpu_available()})

    async def disconnect(self, code):
        logger.info("[WS] Disconnecting with code %s", code)
        if hasattr(self, '_ocr_tasks'):
            for task in self._ocr_tasks:
                task.cancel()
        logger.info("[WS] WS closed")

    async def receive_json(self, content):
        msg_type = content.get("type")
        if msg_type != "frame":
            return
        image_b64 = content.get("image_b64", "")
        if not image_b64:
            return

        try:
            image_bytes = base64.b64decode(image_b64)
        except Exception as exc:
            await self.send_json({"type": "error", "message": str(exc)})
            return

        self._frame_counter += 1
        self._fps_counter += 1
        if self._fps_start is None or self._fps_counter >= 10:
            import time
            now = time.time()
            if self._fps_start:
                self._fps = 10.0 / (now - self._fps_start)
            self._fps_start = now
            self._fps_counter = 0

        import time
        current_time = time.time() * 1000
        if current_time - self._last_process_time < FRAME_RATE_LIMIT_MS:
            return
        self._last_process_time = current_time
        
        logger.info("[WS] Frame %d received, running detection...", self._frame_counter)

        loop = asyncio.get_running_loop()
        
        try:
            detections = await loop.run_in_executor(
                None, self._run_detection, image_bytes
            )
            logger.info("[WS] Detection returned %d results", len(detections))
        except Exception as exc:
            logger.error("[WS] Detection error: %s", exc)
            detections = []

        now = timezone.now()
        tracker_output = self._tracker.update(detections)
        det_by_idx = {i: d for i, d in enumerate(detections)}
        
        logger.info("[WS] Tracks after update: %d", len(tracker_output))
        
        logger.info("[WS] Detections: %d, Tracks: %d", len(detections), len(tracker_output))
        
        active_tracks = []
        tracks_needing_ocr = []
        
        for t_out in tracker_output:
            track_id = t_out["track_id"]
            bbox = t_out["bbox"]
            if isinstance(bbox, dict):
                bbox_list = [bbox["x"], bbox["y"], bbox["width"], bbox["height"]]
            else:
                bbox_list = list(bbox)
            
            track = self._tracker.get_track(track_id)
            d_idx = t_out.get("detection_index")
            det = det_by_idx.get(d_idx) if d_idx is not None else None
            
            if track:
                if track.should_run_ocr(self._frame_counter, OCR_INTERVAL_FRAMES):
                    track.last_ocr_frame = self._frame_counter
                    
                    if det and det.get("crop") is not None:
                        track.add_crop(det["crop"])
                        tracks_needing_ocr.append((track_id, det["crop"], det.get("aspect_ratio", 1.0)))

                text = t_out.get("plate_text") or (track.plate_text if track else "")
                
                # Scale absolute tracking coordinates to relative coordinates for the frontend
                w_img = getattr(self, "_last_img_w", 640)
                h_img = getattr(self, "_last_img_h", 480)
                rel_bbox = [
                    bbox_list[0] / max(w_img, 1),
                    bbox_list[1] / max(h_img, 1),
                    bbox_list[2] / max(w_img, 1),
                    bbox_list[3] / max(h_img, 1)
                ]
                
                active_tracks.append({
                    "track_id": track_id,
                    "plate_text": text,
                    "bbox": rel_bbox,
                    "detection_conf": det.get("confidence", 0.0) if det else track.det_confidence,
                })

        await self.send_json({
            "type": "tracks",
            "tracks": active_tracks,
            "frame_id": self._frame_counter,
            "fps": round(self._fps, 1),
        })

        if tracks_needing_ocr:
            asyncio.create_task(self._run_ocr_for_tracks(tracks_needing_ocr))

        if any(t.get("plate_text") for t in active_tracks):
            await self._process_scan_results(active_tracks, now)

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
        logger.info("[WS] Detection returned %d plates", len(detections))
        result = []
        for det in detections:
            bbox = det["bbox"]
            abs_x = int(bbox["x"] * w)
            abs_y = int(bbox["y"] * h)
            abs_w = int(bbox["width"] * w)
            abs_h = int(bbox["height"] * h)
            result.append({
                "bbox": {
                    "x": abs_x,
                    "y": abs_y,
                    "width": abs_w,
                    "height": abs_h,
                },
                "crop": det["crop"],
                "confidence": det["score"],
                "aspect_ratio": det["aspect_ratio"],
            })
        return result

    async def _run_ocr_for_tracks(self, tracks_to_process: list):
        loop = asyncio.get_running_loop()
        
        for track_id, crop, aspect in tracks_to_process:
            if track_id in self._pending_ocr:
                continue
            
            self._pending_ocr[track_id] = True
            try:
                track = self._tracker.get_track(track_id)
                if track and len(track.image_buffer) > 0:
                    crops = list(track.image_buffer)
                    aspects = [aspect] * len(crops)
                    plate_text, conf = await loop.run_in_executor(
                        None, majority_vote_ocr, crops, aspects
                    )
                else:
                    plate_text, conf = await loop.run_in_executor(
                        None, run_ocr, crop, aspect
                    )
                
                if track and plate_text:
                    logger.info("[WS] OCR result for track %d: %s (conf=%.2f)", track_id, plate_text, conf)
                    
                    track.mark_ocr_done(track.frame_count, plate_text, conf or 0.0)
                    
                    track_bbox = track.bbox
                    bbox_dict = {"x": track_bbox[0], "y": track_bbox[1], "width": track_bbox[2], "height": track_bbox[3]}
                    
                    await self.send_json({
                        "type": "ocr_update",
                        "track_id": track_id,
                        "plate_text": track.plate_text,
                    })
                    logger.info("[WS] Sent OCR update for track %d", track_id)
                    
                    now = timezone.now()
                    try:
                        self._tracker.mark_scanned(track_id, now)
                        await sync_to_async(self._save_to_db)(track_id, plate_text, 0.0, conf or 0.0, bbox_dict, None)
                        enriched = await sync_to_async(self._check_vehicle)(plate_text, bbox_dict)
                        enriched["plate_number"] = plate_text
                        results = [enriched]
                        if results:
                            await sync_to_async(self._record_ml_sample)(None, results)
                            await self.send_json({"type": "result", "results": results})
                            logger.info("[WS] Sent result for plate: %s", plate_text)
                    except Exception as db_exc:
                        logger.error("[WS] Database error for plate %s: %s", plate_text, db_exc)
            except Exception as exc:
                logger.warning("[OCR] Failed for track %d: %s", track_id, exc)
            finally:
                self._pending_ocr.pop(track_id, None)

    async def _process_scan_results(self, tracks_list: list[dict], now: datetime):
        results = []
        processed_track_ids = set()
        
        for track_data in tracks_list:
            track_id = track_data["track_id"]
            plate_number = track_data["plate_text"]
            bbox = {"x": track_data["bbox"][0], "y": track_data["bbox"][1], 
                    "width": track_data["bbox"][2], "height": track_data["bbox"][3]}
            det_conf = track_data.get("detection_conf", 0.0)

            if not plate_number or track_id in processed_track_ids:
                continue

            track = self._tracker.get_track(track_id)
            if track and track.in_cooldown(now):
                results.append({
                    "plate_number": plate_number,
                    "bbox": bbox,
                    "status": "cooldown",
                    "allowed": False,
                    "message": "Recently scanned.",
                    "constraint": None,
                    "vehicle": None,
                    "has_violations": False,
                })
                processed_track_ids.add(track_id)
                continue

            self._tracker.mark_scanned(track_id, now)
            processed_track_ids.add(track_id)
            
            ocr_conf = track.ocr_confidence if track else 0.0
            
            snapshot_path = None
            if track and len(track.image_buffer) > 0 and ocr_conf > 0.5:
                snapshot_path = await sync_to_async(self._save_snapshot)(track_id, list(track.image_buffer)[-1])
            
            try:
                await sync_to_async(self._save_to_db)(track_id, plate_number, det_conf, ocr_conf, bbox, snapshot_path)
            except Exception as e:
                logger.error("[WS] DB save failed for %s: %s", plate_number, e)
            
            try:
                enriched = await sync_to_async(
                    self._check_vehicle, thread_sensitive=True
                )(plate_number, bbox)
                enriched["plate_number"] = plate_number
                enriched["bbox"] = bbox
                results.append(enriched)
            except Exception as e:
                logger.error("[WS] Vehicle check failed for %s: %s", plate_number, e)

        if results:
            await sync_to_async(self._record_ml_sample)(None, results)
            await self.send_json({"type": "result", "results": results})

    def _save_snapshot(self, track_id: int, crop) -> str:
        import cv2
        from pathlib import Path
        snapshot_dir = Path(settings.MEDIA_ROOT) / SNAPSHOT_DIR
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        filename = f"plate_{track_id}_{int(datetime.now().timestamp())}.jpg"
        path = snapshot_dir / filename
        cv2.imwrite(str(path), crop)
        return f"{SNAPSHOT_DIR}/{filename}"

    def _save_to_db(self, track_id: int, plate_number: str, det_conf: float, ocr_conf: float, bbox: dict, snapshot_path: str = None):
        from .models import PlateRecognitionRecord
        PlateRecognitionRecord.objects.create(
            track_id=track_id,
            plate_text=plate_number,
            detection_confidence=det_conf,
            ocr_confidence=ocr_conf,
            timestamp=timezone.now(),
            snapshot_path=snapshot_path or "",
        )

    def _check_vehicle(self, plate_number, bbox):
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
                "status": "unknown",
                "allowed": False,
                "message": "Plate not registered.",
                "constraint": None,
                "vehicle": None,
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
            "status": entry["status"],
            "allowed": entry["allowed"],
            "message": entry["message"],
            "constraint": entry.get("constraint"),
            "vehicle": VehicleSerializer(vehicle).data,
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