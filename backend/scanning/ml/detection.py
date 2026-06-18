"""
detection.py — YOLOv8 vehicle detection for Philippine vehicles.

Detects:
- License plates
- Bicycles, e-bikes, electric scooters (unplated — require digital ID)

Uses the model's own class names at runtime so a retrained model with a
different class list never silently misclassifies.  If weights are missing
or class names don't match, detection is disabled with a clear ERROR log
rather than returning empty results silently.
"""

from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)

WEIGHTS_PATH = Path(__file__).resolve().parent / "weights" / "best.pt"

# Expected class names in the order the model was trained.
# Used only for validation — actual inference uses model.names at runtime.
CLASS_NAMES = ["license_plate", "vehicle", "bicycle", "e_bike", "electric_scooter", "motorcycle"]

VEHICLE_TYPE_CLASSES = {"bicycle", "e_bike", "electric_scooter"}

_model = None
_load_attempted = False  # only try once; avoids re-loading on every frame after failure


def _validate_model_classes(model) -> bool:
    """
    Validate loaded model class names against CLASS_NAMES.
    Returns True if OK (or validation can't run), False if mismatch.
    A mismatch means the weights were trained with a different class order —
    inference would silently produce wrong labels so we refuse to run it.
    """
    try:
        model_names: dict[int, str] = model.names
        model_class_list = [model_names.get(i, f"<unknown_{i}>") for i in range(len(model_names))]
        if model_class_list != CLASS_NAMES:
            log.error(
                "[DETECT] CLASS MISMATCH — inference disabled to prevent silent misclassification.\n"
                "  Model classes : %s\n"
                "  Code expects  : %s\n"
                "  Fix: retrain with the same class list, or update CLASS_NAMES in detection.py.",
                model_class_list,
                CLASS_NAMES,
            )
            return False
        log.info("[DETECT] Class names OK: %s", CLASS_NAMES)
        return True
    except Exception as exc:
        log.warning("[DETECT] Class validation skipped (non-fatal): %s", exc)
        return True  # don't block if validation itself errors


def _get_yolo():
    """Lazy-load the YOLO model. Tries once; permanent None on any failure."""
    global _model, _load_attempted
    if _load_attempted:
        return _model
    _load_attempted = True

    if not WEIGHTS_PATH.exists():
        log.error(
            "[DETECT] CRITICAL: No YOLO weights at %s — all detections disabled. "
            "Train a model and place best.pt at that path.",
            WEIGHTS_PATH,
        )
        return None

    try:
        from ultralytics import YOLO
        candidate = YOLO(str(WEIGHTS_PATH))
        if not _validate_model_classes(candidate):
            return None
        _model = candidate
        if is_gpu_available():
            import torch
            _model.to("cuda")
            # Ampere (RTX 30xx) and later: allow TF32 for matmuls — free ~10% speedup
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True
            torch.backends.cudnn.benchmark = True
            log.info("[DETECT] YOLO on GPU (CUDA) — TF32+cuDNN benchmark enabled: %s", WEIGHTS_PATH)
        else:
            log.info("[DETECT] YOLO on CPU — no CUDA GPU found: %s", WEIGHTS_PATH)
    except ImportError:
        log.error("[DETECT] ultralytics is not installed — cannot load YOLO model")
    except Exception as exc:
        log.error("[DETECT] Failed to load YOLO model: %s", exc)

    return _model


def detect_plates(img: np.ndarray, conf: float = 0.25) -> list[dict]:
    """
    Detect vehicles and license plates in an image using custom-trained YOLOv8.

    Args:
        img:  BGR image (OpenCV format)
        conf: Confidence threshold (default 0.25)

    Returns:
        List of dicts:
          crop        — cropped region (BGR)
          bbox        — relative {x, y, width, height} (0-1)
          score       — detection confidence
          aspect_ratio
          class_name  — from model.names (runtime class list)
          vehicle_type — set for unplated classes, else None
    """
    model = _get_yolo()
    if model is None:
        return []

    h, w = img.shape[:2]
    # half=True uses FP16 Tensor Cores on RTX GPUs — ~2x faster, no accuracy loss for detection
    results = model.predict(img, conf=conf, verbose=False, max_det=100,
                            half=is_gpu_available())

    detections = []
    for r in results:
        for box in r.boxes:
            x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
            score = float(box.conf[0])
            cls_id = int(box.cls[0]) if hasattr(box, "cls") else -1

            # Use the model's own names — immune to CLASS_NAMES ordering bugs
            class_name = model.names.get(cls_id, f"class_{cls_id}")

            x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
            box_w, box_h = x2 - x1, y2 - y1
            aspect_ratio = box_w / max(box_h, 1)

            is_unplated = class_name in VEHICLE_TYPE_CLASSES

            if is_unplated:
                if score < 0.25 or box_w < 30 or box_h < 10:
                    continue
            else:
                if score < 0.25 or aspect_ratio < 0.5 or aspect_ratio > 6.0 or box_w < 30 or box_h < 10:
                    continue

            vehicle_type = None

            if class_name == "license_plate":
                # Contextual padding: motorcycle plates are square/small, car plates are wide
                if aspect_ratio < 2.0 and (box_w < 60 or box_h < 40):
                    pad_x      = max(int(box_w * 0.6), 20)
                    pad_y_top  = max(int(box_h * 0.5), 15)
                    pad_y_bot  = max(int(box_h * 2.5), 60)
                elif aspect_ratio < 2.0:
                    pad_x      = int(box_w * 0.3)
                    pad_y_top  = int(box_h * 0.3)
                    pad_y_bot  = int(box_h * 1.5)
                else:
                    pad_x      = int(box_w * 0.25)
                    pad_y_top  = int(box_h * 0.25)
                    pad_y_bot  = int(box_h * 0.3)

                cx1 = max(0, x1 - pad_x)
                cy1 = max(0, y1 - pad_y_top)
                cx2 = min(w, x2 + pad_x)
                cy2 = min(h, y2 + pad_y_bot)
            else:
                cx1, cy1 = max(0, x1), max(0, y1)
                cx2, cy2 = min(w, x2), min(h, y2)
                vehicle_type = class_name

            crop = img[cy1:cy2, cx1:cx2]

            detections.append({
                "crop": crop,
                "bbox": {
                    "x":      float(cx1 / w),
                    "y":      float(cy1 / h),
                    "width":  float((cx2 - cx1) / w),
                    "height": float((cy2 - cy1) / h),
                },
                "score":        score,
                "aspect_ratio": aspect_ratio,
                "class_name":   class_name,
                "vehicle_type": vehicle_type,
            })

    detections.sort(key=lambda d: d["score"], reverse=True)
    return detections


def is_gpu_available() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError:
        return False


def set_gpu_enabled(enabled: bool = True):
    global _model, _load_attempted
    if enabled and is_gpu_available():
        try:
            from ultralytics import YOLO
            if WEIGHTS_PATH.exists():
                candidate = YOLO(str(WEIGHTS_PATH))
                if _validate_model_classes(candidate):
                    _model = candidate
                    _model.to("cuda")
                    _load_attempted = True
                    log.info("[DETECT] YOLO GPU enabled")
        except Exception as exc:
            log.warning("[DETECT] GPU enable failed: %s", exc)
