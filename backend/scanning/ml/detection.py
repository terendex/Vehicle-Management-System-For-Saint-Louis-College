"""
detection.py — YOLOv8 vehicle detection for Philippine vehicles.

Detects:
- Cars with license plates
- Bicycles
- E-bikes  
- Electric scooters

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

CLASS_NAMES = ["license_plate", "car", "bicycle", "e_bike", "electric_scooter"]

VEHICLE_TYPE_CLASSES = {"bicycle", "e_bike", "electric_scooter"}

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
                _validate_model_classes(_model)
            else:
                log.warning("[DETECT] No YOLO weights found at %s", WEIGHTS_PATH)
        except ImportError:
            log.error("[DETECT] ultralytics not installed")
    return _model


def _validate_model_classes(model) -> None:
    """
    Validate that the loaded model's class names match CLASS_NAMES.

    A mismatch means the model was trained with a different class ordering
    than the code expects — detections will be silently misclassified.
    Logs a clear warning with the mismatch details so the operator can
    retrain or update CLASS_NAMES accordingly.
    """
    try:
        model_names: dict[int, str] = model.names  # {0: "cls", 1: "cls2", …}
        model_class_list = [model_names.get(i, f"<unknown_{i}>") for i in range(len(model_names))]

        if model_class_list != CLASS_NAMES:
            log.warning(
                "[DETECT] ⚠️  CLASS NAME MISMATCH between model weights and code!\n"
                "  Model classes : %s\n"
                "  Code expects  : %s\n"
                "  This will cause silent misclassification. "
                "Retrain or update CLASS_NAMES in detection.py.",
                model_class_list,
                CLASS_NAMES,
            )
        else:
            log.info("[DETECT] ✅ Model class names validated: %s", CLASS_NAMES)
    except Exception as exc:
        log.debug("[DETECT] Class validation skipped: %s", exc)


def detect_plates(img: np.ndarray, conf: float = 0.5) -> list[dict]:
    """
    Detect vehicles and license plates in an image using custom-trained YOLOv8.
    
    Args:
        img: BGR image (OpenCV format)
        conf: Confidence threshold (default 0.5)
    
    Returns:
        List of dicts with:
        - crop: Cropped plate region (BGR) - only for license_plate class
        - bbox: Relative bounding box {x, y, width, height}
        - score: Detection confidence
        - aspect_ratio: Width/height ratio
        - class_name: The detected class name
        - vehicle_type: For unplated vehicles (bicycle, e_bike, electric_scooter)
    """
    model = _get_yolo()
    if model is None:
        log.warning("[DETECT] No YOLO model loaded — returning empty detections")
        return []

    h, w = img.shape[:2]
    log.info("[DETECT] Running YOLO on %dx%d image, conf=%.2f", w, h, conf)
    results = model.predict(img, conf=conf, verbose=False)

    detections = []
    for r in results:
        log.info("[DETECT] YOLO returned %d boxes", len(r.boxes))
        for box in r.boxes:
            x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
            score = float(box.conf[0])
            cls_id = int(box.cls[0]) if hasattr(box, 'cls') else -1
            class_name = CLASS_NAMES[cls_id] if 0 <= cls_id < len(CLASS_NAMES) else f"class_{cls_id}"

            x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
            box_w, box_h = x2 - x1, y2 - y1
            aspect_ratio = box_w / max(box_h, 1)
            
            log.info("[DETECT] Box: (%.0f,%.0f)-(%.0f,%.0f) conf=%.3f class=%s aspect=%.2f size=%dx%d", 
                     x1, y1, x2, y2, score, class_name, aspect_ratio, box_w, box_h)
            
            is_unplated = class_name in VEHICLE_TYPE_CLASSES
            
            if is_unplated:
                if score < 0.5 or box_w < 30 or box_h < 10:
                    log.info("[DETECT] Dropping unplated vehicle: conf=%.3f size=%dx%d", score, box_w, box_h)
                    continue
            else:
                if score < 0.5 or aspect_ratio < 0.5 or aspect_ratio > 6.0 or box_w < 30 or box_h < 10:
                    log.info("[DETECT] Dropped: conf=%.3f aspect=%.2f size=%dx%d", score, aspect_ratio, box_w, box_h)
                    continue

            crop = None
            vehicle_type = None
            
            if class_name == "license_plate":
                if aspect_ratio < 2.0 and (box_w < 60 or box_h < 40):
                    pad_x = max(int(box_w * 0.6), 20)
                    pad_y_top = max(int(box_h * 0.5), 15)
                    pad_y_bottom = max(int(box_h * 2.5), 60)
                elif aspect_ratio < 2.0:
                    pad_x = int(box_w * 0.3)
                    pad_y_top = int(box_h * 0.3)
                    pad_y_bottom = int(box_h * 1.5)
                else:
                    pad_x = int(box_w * 0.25)
                    pad_y_top = int(box_h * 0.25)
                    pad_y_bottom = int(box_h * 0.3)

                cx1, cy1 = max(0, x1 - pad_x), max(0, y1 - pad_y_top)
                cx2, cy2 = min(w, x2 + pad_x), min(h, y2 + pad_y_bottom)
                crop = img[cy1:cy2, cx1:cx2]
            else:
                cx1, cy1 = max(0, x1), max(0, y1)
                cx2, cy2 = min(w, x2), min(h, y2)
                crop = img[cy1:cy2, cx1:cx2]
                vehicle_type = class_name

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
                "class_name": class_name,
                "vehicle_type": vehicle_type,
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