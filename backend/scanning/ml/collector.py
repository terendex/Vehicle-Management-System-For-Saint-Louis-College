"""
scanning/ml/collector.py — Collect scan data for later ML review.

For each incoming scan, the system:
1. Saves the raw image to `MLTrainingSample.image`.
2. Re-uses the YOLO + OCR pipeline to auto-label the image (pseudo-labeling).
3. Stores the plate number + bounding box with confidence metadata.

Samples are stored for review only. Automatic retraining has been removed —
the model is never retrained on its own from live scans.
"""

from __future__ import annotations

import logging
import os
import uuid
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


def record_scan(raw_bytes: bytes, results: list[dict] | None = None) -> dict | None:
    """Persist a training sample for a scan.

    `results` is the output of read_plate() for the same bytes. Pass it
    whenever the caller has already read the plate: detection and OCR are the
    two most expensive operations in the system, and re-running them here meant
    every scan paid for the whole pipeline twice. Left optional so callers that
    only want collection still work.
    """
    if raw_bytes is None or len(raw_bytes) == 0:
        return None

    plate_texts: list[str] = []
    confidences: list[float] = []
    bboxes: list[dict] = []
    status = "unlabeled"

    try:
        if results is None:
            img = _decode(raw_bytes)
            if img is None:
                return None
            for det in _detect_plates(img):
                text, ocr_conf = _ocr_crop(det["crop"], det.get("aspect_ratio", 1.0))
                if text:
                    plate_texts.append(text)
                    confidences.append(
                        (det["score"] + ocr_conf) / 2 if ocr_conf else det["score"]
                    )
                    bboxes.append(det["bbox"])
        else:
            for r in results:
                if not r.get("plate_text"):
                    continue
                det_score = r.get("detection_score") or 0.0
                ocr_conf  = r.get("confidence")
                plate_texts.append(r["plate_text"])
                confidences.append(
                    (det_score + ocr_conf) / 2 if ocr_conf else det_score
                )
                bboxes.append(r["bbox"])

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

    return {
        "sample_id": sample.pk,
        "plate":     plate_texts[0] if plate_texts else None,
        "plates":    plate_texts,
        "confidence": max(confidences) if confidences else None,
        "bbox":      bboxes,
    }
