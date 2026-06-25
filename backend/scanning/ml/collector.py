"""
scanning/ml/collector.py — Collect scan data for the ML feedback loop.

For each incoming scan, the system:
1. Saves the raw image to `MLTrainingSample.image`.
2. Re-uses the YOLO + OCR pipeline to auto-label the image (pseudo-labeling).
3. Stores the plate number + bounding box with confidence metadata.
4. When enough new high-confidence samples have accumulated since the
   last training run, enqueues an incremental YOLOv8 retrain via Celery.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import timedelta
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone

from ..models import MLTrainingSample
from .reader import _detect_plates, _ocr_crop, _decode

log = logging.getLogger(__name__)


def _save_raw_image(raw_bytes: bytes) -> str:
    MEDIA = str(settings.MEDIA_ROOT)
    Path(MEDIA, "ml_samples").mkdir(parents=True, exist_ok=True)
    filename = f"scan_{timezone.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.jpg"
    path = os.path.join(MEDIA, "ml_samples", filename)
    with open(path, "wb") as fh:
        fh.write(raw_bytes)
    return f"ml_samples/{filename}"


def _should_trigger_retrain() -> bool:
    cutoff   = timezone.now() - timedelta(minutes=30)
    new_count = MLTrainingSample.objects.filter(
        created_at__gte=cutoff,
        used_in_training=False,
    ).count()
    return new_count >= settings.ML_SAMPLE_BATCH_SIZE


def _enqueue_retrain() -> None:
    try:
        from .tasks import ml_retrain_task
        task = ml_retrain_task.delay()
        log.info("Retrain enqueued (task_id=%s)", task.id)
    except Exception as exc:
        log.error("Could not enqueue retrain task: %s", exc)


def record_scan(raw_bytes: bytes) -> dict | None:
    if raw_bytes is None or len(raw_bytes) == 0:
        return None

    img = _decode(raw_bytes)
    if img is None:
        return None

    plate_texts: list[str] = []
    confidences: list[float] = []
    bboxes: list[dict] = []
    status = "unlabeled"

    try:
        detections = _detect_plates(img)
        if detections:
            for det in detections:
                text, ocr_conf = _ocr_crop(det["crop"])
                combined_conf = (det["score"] + ocr_conf) / 2 if ocr_conf else det["score"]
                if text:
                    plate_texts.append(text)
                    confidences.append(combined_conf)
                    bboxes.append(det["bbox"])

            if plate_texts:
                status = "auto_labeled"

    except Exception as exc:
        log.error("ML collector error: %s", exc)

    media_path = _save_raw_image(raw_bytes)

    sample = MLTrainingSample.objects.create(
        image        = media_path,
        plate_number = ";".join(plate_texts) if plate_texts else "",
        bbox         = bboxes,
        confidence   = max(confidences) if confidences else None,
        source       = "scan",
        status       = status,
    )

    log.info("Saved MLTrainingSample id=%s plates=%s conf=%s", sample.pk, plate_texts, confidences)

    if _should_trigger_retrain() and settings.ML_AUTO_RETRAIN_ENABLED:
        _enqueue_retrain()
    else:
        log.debug("Sample stored — retrain threshold not yet reached")

    return {
        "sample_id": sample.pk,
        "plate":     plate_texts[0] if plate_texts else None,
        "plates":    plate_texts,
        "confidence": max(confidences) if confidences else None,
        "bbox":      bboxes,
    }
