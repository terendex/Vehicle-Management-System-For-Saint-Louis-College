"""
database.py — PostgreSQL storage for plate recognition records.

Stores:
- Track ID
- License plate text
- Detection confidence
- OCR confidence
- Timestamp
- Snapshot image path
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile

log = logging.getLogger(__name__)

DB_MODEL = None

def _get_model():
    global DB_MODEL
    if DB_MODEL is None:
        from .models import PlateRecognitionRecord
        DB_MODEL = PlateRecognitionRecord
    return DB_MODEL


def init_db():
    from django.db import connection
    try:
        with connection.schema_editor() as schema_editor:
            schema_editor.add_model(_get_model()) if hasattr(schema_editor, 'add_model') else None
        log.info("[DB] Database initialized")
    except Exception as exc:
        log.warning("[DB] Init failed: %s", exc)


def save_record(
    track_id: int,
    plate_text: str,
    detection_confidence: float,
    ocr_confidence: float,
    timestamp: datetime | None = None,
    snapshot_image: bytes | None = None,
    image_path: str | None = None,
) -> int:
    model = _get_model()
    if timestamp is None:
        timestamp = datetime.now()

    record_kwargs = {
        "track_id": track_id,
        "plate_text": plate_text,
        "detection_confidence": detection_confidence,
        "ocr_confidence": ocr_confidence,
        "timestamp": timestamp,
    }

    if snapshot_image:
        filename = f"plate_{track_id}_{timestamp.strftime('%Y%m%d_%H%M%S')}.jpg"
        snapshot_path = Path(settings.MEDIA_ROOT) / "snapshots" / filename
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        with open(snapshot_path, "wb") as f:
            f.write(snapshot_image)
        record_kwargs["snapshot_path"] = f"snapshots/{filename}"
    elif image_path:
        record_kwargs["snapshot_path"] = image_path

    record = model.objects.create(**record_kwargs)
    log.info("[DB] Saved record id=%d track_id=%d plate='%s'", record.id, track_id, plate_text)
    return record.id


def get_records_by_track(track_id: int):
    model = _get_model()
    return model.objects.filter(track_id=track_id).order_by("-timestamp")


def get_recent_records(limit: int = 100):
    model = _get_model()
    return model.objects.all().order_by("-timestamp")[:limit]


def get_records_by_plate(plate_text: str, limit: int = 50):
    model = _get_model()
    return model.objects.filter(plate_text__iexact=plate_text).order_by("-timestamp")[:limit]