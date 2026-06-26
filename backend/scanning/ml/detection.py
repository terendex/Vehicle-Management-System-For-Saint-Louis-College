"""
detection.py — YOLOv8 vehicle detection for Philippine vehicles.

Detects:
- License plates
- Vehicles and motorcycles

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

# The 3 detection targets for the campus entry system.
TARGET_CLASSES = ["license_plate", "vehicle", "motorcycle"]

# Dynamic remap: any model class name → one of the 3 targets.
# Anything NOT in this map is silently ignored (bicycle, e_bike, scooters, etc.).
# Works with the current 6-class weights, future 3-class retrained weights,
# and even COCO pre-trained models (which use "car", "truck", "bus", etc.).
_CLASS_MAP: dict[str, str] = {
    # ── license plates ───────────────────────────────────────────────
    "license_plate":   "license_plate",
    "plate":           "license_plate",
    "licence_plate":   "license_plate",
    "number_plate":    "license_plate",
    # ── 4-wheel vehicles (all normalised to "vehicle") ───────────────
    "vehicle":         "vehicle",
    "car":             "vehicle",
    "truck":           "vehicle",
    "bus":             "vehicle",
    "van":             "vehicle",
    "suv":             "vehicle",
    "pickup":          "vehicle",
    "jeep":            "vehicle",
    "jeepney":         "vehicle",
    "taxi":            "vehicle",
    "ambulance":       "vehicle",
    # ── motorcycles ──────────────────────────────────────────────────
    "motorcycle":      "motorcycle",
    "motorbike":       "motorcycle",
    "moto":            "motorcycle",
    "tricycle":        "motorcycle",
    "sidecar":         "motorcycle",
}

_model = None
_load_attempted = False  # only try once; avoids re-loading on every frame after failure


def _validate_model_classes(model) -> bool:
    """
    Check the loaded model's class names against _CLASS_MAP.
    Logs a warning for unknown classes (they are ignored at inference time).
    Returns False only if the model has NO recognised classes at all.
    """
    try:
        model_names = list(model.names.values())
        known   = [n for n in model_names if n.lower() in _CLASS_MAP]
        unknown = [n for n in model_names if n.lower() not in _CLASS_MAP]
        if unknown:
            log.info("[DETECT] Ignoring model classes not in target set: %s", unknown)
        if not known:
            log.error(
                "[DETECT] Model has NO recognised classes — detection disabled.\n"
                "  Model classes : %s\n"
                "  Expected any of: %s",
                model_names, list(_CLASS_MAP.keys()),
            )
            return False
        log.info("[DETECT] Active classes: %s → mapped to %s",
                 known, sorted({_CLASS_MAP[n.lower()] for n in known}))
        return True
    except Exception as exc:
        log.warning("[DETECT] Class check skipped (non-fatal): %s", exc)
        return True


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
        import torch
        from ultralytics import YOLO
        candidate = YOLO(str(WEIGHTS_PATH))
        if not _validate_model_classes(candidate):
            return None
        _model = candidate
        if is_gpu_available():
            _model.to("cuda")
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True
            torch.backends.cudnn.benchmark = True
            log.info("[DETECT] YOLO on GPU (CUDA): %s", WEIGHTS_PATH)
        else:
            # Use all available CPU threads
            torch.set_num_threads(min(8, torch.get_num_threads()))
            log.info("[DETECT] YOLO on CPU (%d threads): %s",
                     torch.get_num_threads(), WEIGHTS_PATH)

        # Warm-up: run a dummy inference so the first real frame is fast
        log.info("[DETECT] Warming up model (compiling JIT graph)…")
        dummy = np.zeros((480, 640, 3), np.uint8)
        _model.predict(_preprocess_adaptive(dummy), imgsz=640, conf=0.10, verbose=False)
        log.info("[DETECT] Warm-up complete — model ready.")

        # Pre-load EasyOCR now so the first real plate read is instant
        try:
            from .reader import _get_ocr
            log.info("[DETECT] Pre-loading EasyOCR…")
            _get_ocr()
            log.info("[DETECT] EasyOCR pre-loaded.")
        except Exception as _ocr_exc:
            log.warning("[DETECT] EasyOCR pre-load skipped: %s", _ocr_exc)

    except ImportError:
        log.error("[DETECT] ultralytics is not installed — cannot load YOLO model")
    except Exception as exc:
        log.error("[DETECT] Failed to load YOLO model: %s", exc)

    return _model


# Per-class confidence minimums — low thresholds; size/aspect filters catch false positives
_CONF_PLATE   = 0.05   # very permissive — OCR vote accumulation handles noise
_CONF_VEHICLE = 0.10   # low — catches motorcycles the model is less confident about


def _apply_gamma(img: np.ndarray, gamma: float) -> np.ndarray:
    inv = 1.0 / gamma
    table = np.array([((i / 255.0) ** inv) * 255 for i in range(256)], dtype=np.uint8)
    return cv2.LUT(img, table)


def _preprocess_adaptive(img: np.ndarray) -> np.ndarray:
    """CLAHE with gamma correction tuned to frame brightness."""
    brightness = float(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).mean())
    if brightness < 50:       # very dark / night
        img = _apply_gamma(img, 2.2)
        clip = 4.0
    elif brightness < 90:     # dim / indoor / dusk
        img = _apply_gamma(img, 1.5)
        clip = 3.5
    elif brightness > 200:    # glare / overexposed
        clip = 2.0
    else:
        clip = 3.0
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = cv2.createCLAHE(clipLimit=clip, tileGridSize=(8, 8)).apply(l)
    return cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)


def _box_iou(b1, b2) -> float:
    """IoU for two (x1,y1,x2,y2) boxes."""
    xi1, yi1 = max(b1[0], b2[0]), max(b1[1], b2[1])
    xi2, yi2 = min(b1[2], b2[2]), min(b1[3], b2[3])
    inter = max(0, xi2 - xi1) * max(0, yi2 - yi1)
    a1 = (b1[2] - b1[0]) * (b1[3] - b1[1])
    a2 = (b2[2] - b2[0]) * (b2[3] - b2[1])
    union = a1 + a2 - inter
    return inter / union if union > 0 else 0.0


def _nms(dets: list[dict], iou_thresh: float = 0.45) -> list[dict]:
    """Per-class NMS to remove duplicate boxes from tiled passes."""
    dets = sorted(dets, key=lambda d: d["score"], reverse=True)
    kept = []
    for d in dets:
        suppress = False
        for k in kept:
            if k["class_name"] != d["class_name"]:
                continue
            if _box_iou(d["_xyxy"], k["_xyxy"]) > iou_thresh:
                suppress = True
                break
        if not suppress:
            kept.append(d)
    return kept


def _parse_boxes(results, img: np.ndarray, img_w: int, img_h: int,
                 model, offset_x: int = 0, offset_y: int = 0) -> list[dict]:
    """Convert YOLO result boxes into detection dicts with padding applied."""
    dets = []
    for r in results:
        for box in r.boxes:
            x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
            score = float(box.conf[0])
            cls_id = int(box.cls[0]) if hasattr(box, "cls") else -1
            class_name = model.names.get(cls_id, f"class_{cls_id}")

            # Shift tile-relative coords back to full-image coords
            x1, y1, x2, y2 = (int(x1) + offset_x, int(y1) + offset_y,
                               int(x2) + offset_x, int(y2) + offset_y)

            # Map to one of 3 targets; skip anything not in the map (bicycle, e_bike, etc.)
            class_name = _CLASS_MAP.get(class_name.lower())
            if class_name is None:
                continue

            box_w, box_h = x2 - x1, y2 - y1
            aspect_ratio = box_w / max(box_h, 1)

            is_plate = class_name == "license_plate"
            min_conf = _CONF_PLATE if is_plate else _CONF_VEHICLE

            if score < min_conf or box_w < 20 or box_h < 8:
                continue
            if not is_plate:
                if aspect_ratio < 0.3 or aspect_ratio > 8.0:
                    continue

            vehicle_type = None
            if is_plate:
                if aspect_ratio < 2.0 and (box_w < 60 or box_h < 40):
                    pad_x, pad_y_top, pad_y_bot = (max(int(box_w * 0.6), 20),
                                                    max(int(box_h * 0.5), 15),
                                                    max(int(box_h * 2.5), 60))
                elif aspect_ratio < 2.0:
                    pad_x, pad_y_top, pad_y_bot = (int(box_w * 0.3),
                                                    int(box_h * 0.3),
                                                    int(box_h * 1.5))
                else:
                    pad_x, pad_y_top, pad_y_bot = (int(box_w * 0.25),
                                                    int(box_h * 0.25),
                                                    int(box_h * 0.3))
                cx1 = max(0, x1 - pad_x)
                cy1 = max(0, y1 - pad_y_top)
                cx2 = min(img_w, x2 + pad_x)
                cy2 = min(img_h, y2 + pad_y_bot)
            else:
                cx1, cy1 = max(0, x1), max(0, y1)
                cx2, cy2 = min(img_w, x2), min(img_h, y2)
                vehicle_type = class_name

            dets.append({
                "crop": img[cy1:cy2, cx1:cx2],
                "bbox": {
                    "x":      float(cx1 / img_w),
                    "y":      float(cy1 / img_h),
                    "width":  float((cx2 - cx1) / img_w),
                    "height": float((cy2 - cy1) / img_h),
                },
                "score":        score,
                "aspect_ratio": aspect_ratio,
                "class_name":   class_name,
                "vehicle_type": vehicle_type,
                "_xyxy":        (x1, y1, x2, y2),  # kept for NMS, stripped before return
            })
    return dets


def detect_plates(img: np.ndarray, conf: float = _CONF_PLATE,
                  try_rotation: bool = True) -> list[dict]:
    """
    Detect vehicles and license plates in an image using custom-trained YOLOv8.

    Handles all conditions via:
    - Adaptive preprocessing  (dark/night/glare/dim)
    - Multi-rotation fallback (tilted cameras / angled plates) — skip with try_rotation=False
    - GPU tiled pass          (high-res frames, GPU only)

    Pass try_rotation=False when the scene already has locked plate tracks to
    avoid up to 6 redundant YOLO passes on frames where no new plate is needed.
    """
    model = _get_yolo()
    if model is None:
        return []

    h, w = img.shape[:2]
    gpu = is_gpu_available()
    imgsz = 960 if gpu else 640

    # Pass 0 — normal orientation
    img_proc = _preprocess_adaptive(img)
    results = model.predict(img_proc, conf=conf, verbose=False, max_det=200,
                            half=gpu, imgsz=imgsz)
    all_dets = _parse_boxes(results, img, w, h, model)

    # GPU: tiled pass for high-res frames
    if gpu and (w > 1280 or h > 960):
        tile, overlap = 640, 0.2
        stride = int(tile * (1 - overlap))
        for y0 in range(0, h, stride):
            for x0 in range(0, w, stride):
                x2t, y2t = min(x0 + tile, w), min(y0 + tile, h)
                patch = img_proc[y0:y2t, x0:x2t]
                ph, pw = patch.shape[:2]
                if pw < 64 or ph < 64:
                    continue
                tile_res = model.predict(patch, conf=conf, verbose=False,
                                         max_det=50, half=True, imgsz=640)
                all_dets.extend(_parse_boxes(tile_res, img, w, h, model,
                                             offset_x=x0, offset_y=y0))

    # Rotation fallback — fires when no plate found at 0°.
    # Handles cameras mounted at an angle and tilted plates (up to ±60°).
    # Each pass takes ~0.17 s on CPU; skipped entirely when try_rotation=False
    # (caller sets this once all active tracks have a locked plate).
    if try_rotation and not any(d["class_name"] == "license_plate" for d in all_dets):
        center = (w // 2, h // 2)
        for angle in [20, -20, 40, -40, 60, -60]:
            M = cv2.getRotationMatrix2D(center, angle, 1.0)
            img_rot = cv2.warpAffine(img, M, (w, h),
                                     flags=cv2.INTER_LINEAR,
                                     borderMode=cv2.BORDER_REFLECT_101)
            rot_res = model.predict(_preprocess_adaptive(img_rot), conf=conf,
                                    verbose=False, max_det=100,
                                    half=gpu, imgsz=imgsz)
            rot_dets = _parse_boxes(rot_res, img_rot, w, h, model)

            if rot_dets:
                # Map bboxes back to original (unrotated) coordinate space
                M_inv = cv2.getRotationMatrix2D(center, -angle, 1.0)
                for d in rot_dets:
                    bx = d["_xyxy"]
                    corners = np.array([
                        [bx[0], bx[1], 1], [bx[2], bx[1], 1],
                        [bx[2], bx[3], 1], [bx[0], bx[3], 1],
                    ], dtype=np.float32)
                    t = (M_inv @ corners.T).T
                    nx1 = max(0, int(t[:, 0].min()))
                    ny1 = max(0, int(t[:, 1].min()))
                    nx2 = min(w, int(t[:, 0].max()))
                    ny2 = min(h, int(t[:, 1].max()))
                    d["bbox"] = {
                        "x": nx1 / w, "y": ny1 / h,
                        "width": (nx2 - nx1) / w,
                        "height": (ny2 - ny1) / h,
                    }
                    d["_xyxy"] = (nx1, ny1, nx2, ny2)

                all_dets.extend(rot_dets)
                if any(d["class_name"] == "license_plate" for d in rot_dets):
                    break

    all_dets = _nms(all_dets)

    for d in all_dets:
        d.pop("_xyxy", None)

    all_dets.sort(key=lambda d: d["score"], reverse=True)
    return all_dets


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
