import logging
import base64
from datetime import datetime
from typing import Any
import asyncio
from asgiref.sync import sync_to_async
from django.utils import timezone
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .ml.tracker import PlateTracker
from .ml.reader import _detect_plates, _decode, _ocr_crop, normalize_plate

logger = logging.getLogger(__name__)

OCR_INTERVAL_FRAMES = 10

class ScanLiveConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        token_key = (
            self.scope["query_string"].decode().split("token=")[-1].split("&")[0]
            if "token=" in self.scope["query_string"].decode()
            else None
        )
        if not token_key:
            await self.close(code=4001, reason="Authentication required")
            return
        self._user = await self._get_user_from_token(token_key)
        if self._user is None:
            await self.close(code=4001, reason="Invalid token")
            return
        self._recent = {}
        self._tracker = PlateTracker()
        self._frame_counter = 0
        self._pending_ocr = {}
        await self.accept()
        await self.send_json({"type": "connected", "message": "Stream ready."})

    async def disconnect(self, code):
        if hasattr(self, '_ocr_tasks'):
            for task in self._ocr_tasks:
                task.cancel()
        logger.info("WS closed (code=%s)", code)

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
        loop = asyncio.get_running_loop()
        
        try:
            detections = await loop.run_in_executor(
                None, self._run_yolo_detection, image_bytes
            )
        except Exception as exc:
            logger.error("[WS] YOLO error: %s", exc)
            detections = []

        now = timezone.now()
        tracker_output = self._tracker.update(detections, now=now)
        
        det_by_idx = {i: d for i, d in enumerate(detections)}
        
        active_tracks = []
        tracks_needing_ocr = []
        
        for idx, t_out in enumerate(tracker_output):
            track_id = t_out["track_id"]
            bbox = t_out["bbox"]
            
            if not bbox:
                continue

            track = self._tracker.get_track(track_id)
            if track and track.should_run_ocr(OCR_INTERVAL_FRAMES):
                track.last_ocr_frame = track.frame_count
                
                if t_out.get("is_new_track", False):
                    det = det_by_idx.get(idx)
                    if det and det.get("crop") is not None:
                        tracks_needing_ocr.append((track_id, det["crop"], det.get("aspect_ratio", 1.0)))

            text = t_out.get("plate_text") or (track.plate_number if track else "")
            active_tracks.append({
                "track_id": track_id,
                "plate_text": text,
                "bbox": [bbox["x"], bbox["y"], bbox["width"], bbox["height"]],
            })

        await self.send_json({
            "type": "tracks",
            "tracks": active_tracks,
            "frame_id": self._frame_counter,
        })

        if tracks_needing_ocr:
            asyncio.create_task(self._run_ocr_for_tracks(tracks_needing_ocr))

        if any(t.get("plate_text") for t in active_tracks):
            await self._process_scan_results(active_tracks, now)

    def _run_yolo_detection(self, image_bytes: bytes) -> list[dict[str, Any]]:
        img = _decode(image_bytes)
        if img is None:
            return []
        
        detections = _detect_plates(img)
        return [
            {
                "bbox": det["bbox"],
                "crop": det["crop"],
                "confidence": det.get("score", 0.0),
                "aspect_ratio": det.get("aspect_ratio", 1.0),
            }
            for det in detections
        ]

    async def _run_ocr_for_tracks(self, tracks_to_process: list):
        loop = asyncio.get_running_loop()
        
        for track_id, crop, aspect in tracks_to_process:
            if track_id in self._pending_ocr:
                continue
            
            self._pending_ocr[track_id] = True
            try:
                plate_text, conf = await loop.run_in_executor(
                    None, _ocr_crop, crop, aspect
                )
                
                track = self._tracker.get_track(track_id)
                if track and plate_text:
                    if track.is_initializing:
                        track.plate_number = plate_text
                    else:
                        track.plate_candidates[normalize_plate(plate_text)] = track.plate_candidates.get(normalize_plate(plate_text), 0) + 1
                        if track.plate_candidates:
                            sorted_cands = sorted(track.plate_candidates.items(), key=lambda x: x[1], reverse=True)
                            if sorted_cands:
                                track.plate_number = sorted_cands[0][0]
                    
                    await self.send_json({
                        "type": "ocr_update",
                        "track_id": track_id,
                        "plate_text": track.plate_number,
                    })
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
            
            enriched = await sync_to_async(
                self._check_vehicle, thread_sensitive=True
            )(plate_number, bbox)
            enriched["plate_number"] = plate_number
            enriched["bbox"] = bbox
            results.append(enriched)

        if results:
            await sync_to_async(self._record_ml_sample)(None, results)
            await self.send_json({"type": "result", "results": results})

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
        from django.conf import settings
        from django.utils import timezone

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