import logging
import base64
from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.utils import timezone

logger = logging.getLogger(__name__)

PLATE_COOLDOWN_SECONDS = 3
FRAME_SKIP = 0


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
        if not hasattr(self, "_recent"):
            self._recent = {}
        await self.accept()
        await self.send_json({"type": "connected", "message": "Stream ready."})

    async def disconnect(self, code):
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

        now_ts = timezone.now()
        detections = self._process_frame(image_bytes)
        if not detections:
            await self.send_json({"type": "result", "results": []})
            return

        results = []
        for det in detections:
            plate_number = det["plate_text"]
            bbox = det["bbox"]

            if not plate_number:
                results.append({
                    "plate_number": "",
                    "bbox": bbox,
                    "status": "unreadable",
                    "allowed": False,
                    "message": "Plate detected but unreadable.",
                    "constraint": None,
                    "vehicle": None,
                    "has_violations": False,
                })
                continue

            key = f"{plate_number}:{now_ts.strftime('%H:%M')}"
            last_seen = self._recent.get(key)
            in_cooldown = last_seen and (now_ts - last_seen).total_seconds() < PLATE_COOLDOWN_SECONDS
            self._recent[key] = now_ts

            if in_cooldown:
                results.append({
                    "plate_number": plate_number,
                    "bbox": bbox,
                    "status": "recent",
                    "allowed": False,
                    "message": "Recently scanned — still in view.",
                    "constraint": None,
                    "vehicle": None,
                    "has_violations": False,
                })
                continue

            enriched = await sync_to_async(
                self._check_vehicle, thread_sensitive=True
            )(plate_number, bbox)
            enriched["plate_number"] = plate_number
            enriched["bbox"] = bbox
            results.append(enriched)

        await sync_to_async(
            self._record_ml_sample, thread_sensitive=True
        )(image_bytes, results)

        await self.send_json({"type": "result", "results": results})

    def _check_vehicle(self, plate_number, bbox):
        from rest_framework.authtoken.models import Token
        from vehicles.models import Vehicle
        from .models import AccessLog, MLTrainingSample
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
        import os, uuid

        try:
            plates = [r["plate_text"] for r in results if r.get("plate_text")]
            media_dir = os.path.join(str(settings.MEDIA_ROOT), "ml_samples")
            os.makedirs(media_dir, exist_ok=True)
            fn = f"scan_{timezone.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.jpg"
            path = os.path.join(media_dir, fn)
            with open(path, "wb") as f:
                f.write(raw_bytes)
            MLTrainingSample.objects.create(
                image=f"ml_samples/{fn}",
                plate_number=";".join(plates) if plates else "",
                status="unlabeled",
                source="scan",
            )
        except Exception as exc:
            logger.warning("ML sample failed: %s", exc)

    @staticmethod
    def _process_frame(image_bytes):
        from .ml.reader import read_plate
        import logging
        log = logging.getLogger(__name__)

        try:
            results = read_plate(image_bytes)
            log.info("[WS] read_plate returned %d results: %s", len(results), [r["plate_text"] for r in results])
            return [
                {
                    "plate_text": r["plate_text"],
                    "bbox": r["bbox"],
                }
                for r in results
            ]
        except Exception as exc:
            log.error("[WS] read_plate error: %s", exc)
            return []

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
