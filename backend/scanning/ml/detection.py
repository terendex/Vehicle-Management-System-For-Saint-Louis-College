"""
detection.py — YOLOv8 license plate detection for Philippine vehicles.

Detects:
- Cars: Standard rectangular plates
- Motorcycles: Two-row format plates

Returns bounding boxes with confidence scores above 0.5 threshold.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

log = logging.getLogger(__name__)

WEIGHTS_PATH = Path(__file__).resolve().parent / "weights" / "best.pt"
_model = None


def _get_yolo():
    """Lazy-load the YOLO plate-detection model."""
    global _model
    if _model is None:
        try:
            from ultralytics import YOLO
            if WEIGHTS_PATH.exists():
                _model = YOLO(str(WEIGHTS_PATH))
                log.info("[DETECT] YOLO loaded from %s", WEIGHTS_PATH)
            else:
                log.warning("[DETECT] No YOLO weights found at %s", WEIGHTS_PATH)
        except ImportError:
            log.error("[DETECT] ultralytics not installed")
    return _model


def detect_plates(img: np.ndarray, conf: float = 0.5) -> list[dict]:
    """
    Detect license plates in an image using custom-trained YOLOv8.
    
    Args:
        img: BGR image (OpenCV format)
        conf: Confidence threshold (default 0.5)
    
    Returns:
        List of dicts with:
        - crop: Cropped plate region (BGR)
        - bbox: Relative bounding box {x, y, width, height}
        - score: Detection confidence
        - aspect_ratio: Width/height ratio
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
            score = float(box.conf[0])
            cls_id = int(box.cls[0]) if hasattr(box, 'cls') else -1

            x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
            box_w, box_h = x2 - x1, y2 - y1
            aspect_ratio = box_w / max(box_h, 1)

            if score < 0.5 or aspect_ratio < 0.8 or aspect_ratio > 3.5:
                continue

            pad_x = int(box_w * 0.15)
            pad_y_top = int(box_h * 0.15)
            pad_y_bottom = int(box_h * 1.2) if aspect_ratio < 2.0 else int(box_h * 0.15)

            cx1, cy1 = max(0, x1 - pad_x), max(0, y1 - pad_y_top)
            cx2, cy2 = min(w, x2 + pad_x), min(h, y2 + pad_y_bottom)

            crop = img[cy1:cy2, cx1:cx2]

            detections.append({
                "crop": crop,
                "bbox": {
                    "x": float(cx1 / w),
                    "y": float(cy1 / h),
                    "width": float((cx2 - cx1) / w),
                    "height": float((cy2 - cy1) / h),
                },
                "score": score,
                "aspect_ratio": aspect_ratio,
            })

    detections.sort(key=lambda d: d["score"], reverse=True)
    return detections


def is_gpu_available() -> bool:
    """Check if CUDA/GPU is available for YOLO inference."""
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError:
        return False


def set_gpu_enabled(enabled: bool = True):
    """Enable or disable GPU for YOLO inference."""
    global _model
    if enabled and is_gpu_available():
        try:
            from ultralytics import YOLO
            if WEIGHTS_PATH.exists():
                _model = YOLO(str(WEIGHTS_PATH))
                _model.to('cuda')
                log.info("[DETECT] YOLO GPU enabled")
        except Exception as e:
            log.warning("[DETECT] GPU enable failed: %s", e)