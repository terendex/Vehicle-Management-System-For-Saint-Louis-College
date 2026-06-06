"""
──────────────────────────────────────────────────────────────────────
  reader.py — License Plate Detection + OCR Pipeline
──────────────────────────────────────────────────────────────────────

  Two-stage pipeline:
    1. YOLO — locates the license plate region (bounding box)
    2. EasyOCR — reads the text from the cropped plate region

  If no trained YOLO model is found, falls back to running EasyOCR
  on the full preprocessed frame (legacy behaviour).
──────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np
import easyocr

from .validator import is_valid_ph_plate, normalize_plate

log = logging.getLogger(__name__)

# ── Lazy singletons ─────────────────────────────────────────────────

_ocr_reader: easyocr.Reader | None = None
_yolo_model = None                       # ultralytics.YOLO | None
_yolo_loaded = False                     # ensures we only try once

WEIGHTS_PATH = Path(__file__).resolve().parent / "weights" / "best.pt"


def _get_ocr():
    """Lazy-load EasyOCR (takes ~2 s on first call)."""
    global _ocr_reader
    if _ocr_reader is None:
        _ocr_reader = easyocr.Reader(["en"], gpu=False)
    return _ocr_reader


def _get_yolo():
    """
    Lazy-load the YOLO plate-detection model.
    Returns None if no trained weights exist yet.
    """
    global _yolo_model, _yolo_loaded
    if _yolo_loaded:
        return _yolo_model
    _yolo_loaded = True

    if WEIGHTS_PATH.exists():
        try:
            from ultralytics import YOLO
            _yolo_model = YOLO(str(WEIGHTS_PATH))
            log.info("✅ YOLO plate-detector loaded from %s", WEIGHTS_PATH)
        except Exception as exc:
            log.warning("⚠️  Failed to load YOLO model: %s", exc)
            _yolo_model = None
    else:
        log.info(
            "ℹ️  No YOLO weights found at %s — using EasyOCR-only fallback.",
            WEIGHTS_PATH,
        )
    return _yolo_model


# ── Image helpers ───────────────────────────────────────────────────

def _decode(image_bytes: bytes) -> np.ndarray:
    """Decode raw bytes into a BGR OpenCV image."""
    nparr = np.frombuffer(image_bytes, np.uint8)
    return cv2.imdecode(nparr, cv2.IMREAD_COLOR)


def _preprocess_for_ocr(img: np.ndarray) -> np.ndarray:
    """Convert a BGR image to a thresholded grayscale for OCR."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return thresh


# ── YOLO detection ──────────────────────────────────────────────────

def _detect_plates(img: np.ndarray, conf: float = 0.35):
    """
    Run the YOLO model on a BGR image.

    Returns a list of dicts, each with:
        crop  — the cropped plate region (BGR)
        bbox  — relative bounding box {x, y, width, height}  (0–1)
        score — detection confidence
    """
    model = _get_yolo()
    if model is None:
        return []

    h, w = img.shape[:2]
    results = model.predict(img, conf=conf, verbose=False)

    detections = []
    for r in results:
        for box in r.boxes:
            x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
            x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)

            # Add a small padding around the plate for OCR accuracy
            pad_x = int((x2 - x1) * 0.05)
            pad_y = int((y2 - y1) * 0.10)
            cx1 = max(0, x1 - pad_x)
            cy1 = max(0, y1 - pad_y)
            cx2 = min(w, x2 + pad_x)
            cy2 = min(h, y2 + pad_y)

            detections.append({
                "crop":  img[cy1:cy2, cx1:cx2],
                "bbox":  {
                    "x":      float(x1 / w),
                    "y":      float(y1 / h),
                    "width":  float((x2 - x1) / w),
                    "height": float((y2 - y1) / h),
                },
                "score": float(box.conf[0]),
            })

    # Sort by confidence descending
    detections.sort(key=lambda d: d["score"], reverse=True)
    return detections


# ── OCR on a single plate crop ──────────────────────────────────────

def _ocr_crop(crop: np.ndarray) -> tuple[str, float] | tuple[None, None]:
    """
    Run EasyOCR on a cropped plate image.
    Returns (normalized_plate, confidence) or (None, None).
    """
    processed = _preprocess_for_ocr(crop)
    ocr = _get_ocr()
    results = ocr.readtext(processed)

    candidates = []
    for (_bbox, text, confidence) in results:
        if confidence > 0.4 and is_valid_ph_plate(text):
            candidates.append((confidence, normalize_plate(text)))

    if not candidates:
        return None, None

    candidates.sort(reverse=True)
    return candidates[0][1], candidates[0][0]


# ── Public API ──────────────────────────────────────────────────────

def read_plate(image_bytes: bytes) -> tuple[str, dict] | tuple[None, None]:
    """
    Main entry point — detect + read a Philippine license plate.

    Returns:
        (plate_text, bbox_dict)  on success
        (None, None)             when no plate is found

    bbox_dict contains relative coordinates:
        {"x": 0.32, "y": 0.71, "width": 0.18, "height": 0.06}
    """
    img = _decode(image_bytes)
    if img is None:
        return None, None

    h, w = img.shape[:2]

    # ── Stage 1: Try YOLO detection first ───────────────────────────
    detections = _detect_plates(img)

    for det in detections:
        plate_text, _conf = _ocr_crop(det["crop"])
        if plate_text:
            return plate_text, det["bbox"]

    # ── Stage 2: Fallback — OCR on the full image ───────────────────
    log.debug("YOLO found no plates — falling back to full-frame OCR")
    processed = _preprocess_for_ocr(img)
    ocr = _get_ocr()
    results = ocr.readtext(processed)

    candidates = []
    for (bbox, text, confidence) in results:
        if confidence > 0.5 and is_valid_ph_plate(text):
            xs = [p[0] for p in bbox]
            ys = [p[1] for p in bbox]
            x_min, x_max = min(xs), max(xs)
            y_min, y_max = min(ys), max(ys)

            box_dict = {
                "x":      float(x_min / w),
                "y":      float(y_min / h),
                "width":  float((x_max - x_min) / w),
                "height": float((y_max - y_min) / h),
            }
            candidates.append((confidence, normalize_plate(text), box_dict))

    if not candidates:
        return None, None

    candidates.sort(key=lambda c: c[0], reverse=True)
    best = candidates[0]
    return best[1], best[2]