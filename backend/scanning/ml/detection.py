"""
detection.py — YOLOv8 detection for Philippine vehicles.

Two independent models, each owned by one part of the system:
- Plate detector  (runs/plate_detector/weights/best.pt)  — license plates only,
  trained specifically for Philippine plates.  This is the *entire* entry-gate
  pipeline: detect_plates() loads nothing else.
- Vehicle detector (runs/vehicle_detector/weights/best.pt) — a single unified
  "vehicle" class (cars, motorcycles, buses, trucks… all one class).  Used only
  by parking occupancy, through detect_vehicles().

The gate decides on a plate; a vehicle box never contributed to that decision,
it only drew a second rectangle on the overlay and cost an extra inference (a
tiled one on high-res frames) on every gate frame.  Keeping the two models on
separate entry points means the gate never loads the vehicle weights and
parking never loads the plate weights.

Uses the model's own class names at runtime so a retrained model with a
different class list never silently misclassifies.  If weights are missing
or class names don't match, that model is disabled with a clear log message
rather than returning empty results silently.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)

PLATE_WEIGHTS_PATH   = Path(__file__).resolve().parent / "runs" / "plate_detector" / "weights" / "best.pt"
VEHICLE_WEIGHTS_PATH = Path(__file__).resolve().parent / "runs" / "vehicle_detector" / "weights" / "best.pt"

# The 2 detection targets for the campus entry system.
TARGET_CLASSES = ["license_plate", "vehicle"]

# Dynamic remap: any model class name → one of the 2 targets.
# Anything NOT in this map is silently ignored (person, helmet, etc.).
# Works with the single-class retrained weights, older multi-class weights,
# and even COCO pre-trained models (which use "car", "truck", "bus", etc.).
_CLASS_MAP: dict[str, str] = {
    # ── license plates ───────────────────────────────────────────────
    "license_plate":   "license_plate",
    "plate":           "license_plate",
    "licence_plate":   "license_plate",
    "number_plate":    "license_plate",
    # ── vehicles (everything motorized is normalised to "vehicle") ───
    "vehicle":         "vehicle",
    "vehicles":        "vehicle",
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
    "motorcycle":      "vehicle",
    "motorbike":       "vehicle",
    "moto":            "vehicle",
    "motor":           "vehicle",
    "tricycle":        "vehicle",
    "sidecar":         "vehicle",
}

_vehicle_model          = None   # unified vehicle model (runs/vehicle_detector/weights/best.pt)
_plate_model            = None   # dedicated plate detector (runs/plate_detector/weights/best.pt)
_vehicle_load_attempted = False
_plate_load_attempted   = False

# Serialises concurrent model.predict() calls from multiple camera streams.
# Ultralytics YOLO predict() is not thread-safe when the same model object is
# called from multiple threads simultaneously — internal inference buffers get
# corrupted, producing garbage results or crashes.  GPU inference is also
# serialised by CUDA, so this lock adds no throughput cost.
_INFER_LOCK = threading.Lock()

# ── ML loading status broadcast ────────────────────────────────────────────────
# stage values: "idle" | "loading_plate_yolo" | "warming_up" | "loading_ocr"
#               | "ready"
_ml_status: tuple[str, str] = ("idle", "")
_ml_listeners: list = []
_ml_listeners_lock = threading.Lock()


def add_ml_status_listener(cb) -> None:
    with _ml_listeners_lock:
        if cb not in _ml_listeners:
            _ml_listeners.append(cb)
    # Immediately deliver current status so late-joining consumers are in sync
    stage, msg = _ml_status
    try:
        cb(stage, msg)
    except Exception:
        pass


def remove_ml_status_listener(cb) -> None:
    with _ml_listeners_lock:
        try:
            _ml_listeners.remove(cb)
        except ValueError:
            pass


def _broadcast_status(stage: str, message: str) -> None:
    global _ml_status
    _ml_status = (stage, message)
    with _ml_listeners_lock:
        listeners = list(_ml_listeners)
    for cb in listeners:
        try:
            cb(stage, message)
        except Exception:
            pass


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


def _get_plate_yolo():
    """
    Lazy-load the plate-detector model — the primary model of the pipeline.
    Also chains the optional vehicle detector and OCR pre-loads so a single
    call brings the whole stack up.  Tries once; permanent None on failure.
    """
    global _plate_model, _plate_load_attempted
    if _plate_load_attempted:
        return _plate_model
    _plate_load_attempted = True

    if not PLATE_WEIGHTS_PATH.exists():
        log.error(
            "[DETECT] CRITICAL: No plate-detector weights at %s — plate detection disabled. "
            "Train a plate model and place best.pt at that path.",
            PLATE_WEIGHTS_PATH,
        )
        return None

    try:
        _broadcast_status("loading_plate_yolo", "Loading plate detector…")
        import torch
        from ultralytics import YOLO
        candidate = YOLO(str(PLATE_WEIGHTS_PATH))
        _plate_model = candidate
        if is_gpu_available():
            _plate_model.to("cuda")
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True
            torch.backends.cudnn.benchmark = True
            log.info("[DETECT] Plate-detector on GPU (CUDA): %s", PLATE_WEIGHTS_PATH)
        else:
            torch.set_num_threads(min(8, torch.get_num_threads()))
            log.info("[DETECT] Plate-detector on CPU (%d threads): %s",
                     torch.get_num_threads(), PLATE_WEIGHTS_PATH)

        _broadcast_status("warming_up", "Warming up plate detector…")
        log.info("[DETECT] Warming up plate detector (compiling JIT graph)…")
        dummy = np.zeros((480, 640, 3), np.uint8)
        _plate_model.predict(_preprocess_adaptive(dummy), imgsz=640, conf=_CONF_PLATE, verbose=False)
        log.info("[DETECT] Plate-detector warm-up complete.")

        # No vehicle-detector pre-load here.  This loader runs for the gate, and
        # the gate is plate-only; parking loads the vehicle weights lazily on its
        # first frame instead of the gate holding them in VRAM unused.

        _broadcast_status("loading_ocr", "Loading OCR engine…")
        try:
            from .reader import _get_ocr
            log.info("[DETECT] Pre-loading PaddleOCR…")
            _get_ocr()
            log.info("[DETECT] PaddleOCR pre-loaded.")
        except Exception as _ocr_exc:
            log.warning("[DETECT] PaddleOCR pre-load skipped: %s", _ocr_exc)

        if is_gpu_available():
            # Warm-up (cudnn benchmarking) bloats the torch allocator cache;
            # release it so training / other processes can use the VRAM.
            torch.cuda.empty_cache()

        _broadcast_status("ready", "Detection ready")

    except ImportError:
        log.error("[DETECT] ultralytics is not installed — cannot load YOLO model")
        _plate_model = None
        _broadcast_status("idle", "")
    except Exception as exc:
        log.error("[DETECT] Failed to load plate-detector: %s", exc)
        _plate_model = None
        _broadcast_status("idle", "")

    return _plate_model


def _get_vehicle_yolo():
    """
    Lazy-load the unified single-class vehicle detector.
    Optional — returns None (plate-only mode) until the model is trained.
    """
    global _vehicle_model, _vehicle_load_attempted
    if _vehicle_load_attempted:
        return _vehicle_model
    _vehicle_load_attempted = True

    if not VEHICLE_WEIGHTS_PATH.exists():
        log.warning(
            "[DETECT] Vehicle-detector weights not found at %s — running in plate-only mode. "
            "Train the model with scanning/ml/train.py to enable vehicle detection.",
            VEHICLE_WEIGHTS_PATH,
        )
        return None

    try:
        from ultralytics import YOLO
        candidate = YOLO(str(VEHICLE_WEIGHTS_PATH))
        if not _validate_model_classes(candidate):
            return None
        _vehicle_model = candidate
        if is_gpu_available():
            _vehicle_model.to("cuda")
            log.info("[DETECT] Vehicle-detector on GPU: %s", VEHICLE_WEIGHTS_PATH)
        else:
            log.info("[DETECT] Vehicle-detector on CPU: %s", VEHICLE_WEIGHTS_PATH)

        dummy = np.zeros((480, 640, 3), np.uint8)
        _vehicle_model.predict(_preprocess_adaptive(dummy), imgsz=960, conf=0.15, verbose=False)
        log.info("[DETECT] Vehicle-detector warm-up complete.")
        if is_gpu_available():
            import torch
            torch.cuda.empty_cache()
    except Exception as exc:
        log.error("[DETECT] Failed to load vehicle-detector: %s", exc)
        _vehicle_model = None

    return _vehicle_model


# Per-class confidence minimums
#
# _CONF_PLATE went 0.05 → 0.15 (false plates on truck bodies) → 0.40. The last
# step was measured by sweeping the plate detector over its own 515-image
# validation split at the settings this module runs (imgsz 640, half,
# `_preprocess_adaptive`), matching at IoU 0.5:
#
#     conf    P      R      F1     FP   missed plates
#     0.15  0.979  1.000  0.989   11         0
#     0.25  0.983  0.996  0.989    9         2
#     0.40  0.987  0.994  0.990    7         3      ← F1 peak
#     0.60  0.986  0.986  0.986    7         7
#
# Above 0.40 nothing more is gained: the remaining 7 false boxes score high and
# survive any threshold, so raising further only costs plates. The val set is
# plate close-ups and understates the gain — the failure that prompted this was
# a tricycle's diamond-plate bumper boxed at 20% on a live gate frame, which no
# image in the set resembles.
#
# The asymmetry is what justifies trading 0.6% recall for a third fewer false
# boxes: a plate missed on one frame is re-read on the next (the tracker votes
# across frames before locking a read), while a false box opens a track and
# runs OCR on a car's bodywork.
_CONF_PLATE   = 0.40
# Only detect_vehicles() reads this, and its one caller (parking occupancy)
# overrides it — see OCCUPANCY_CONF in vehicles/parking_camera.py for why a
# dense overview needs a stricter floor than this default.
_CONF_VEHICLE = 0.15   # raised from 0.10


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
    """Per-class NMS to remove duplicate boxes from tiled passes.

    Greedy NMS is inherently O(n²) in comparisons, but the suppression step is
    vectorised so those comparisons run inside NumPy rather than as one Python
    call per pair. A tiled GPU pass can emit a few hundred boxes, which meant
    tens of thousands of interpreted _box_iou calls per frame.

    Output is byte-identical to the scalar version: same boxes, same order.
    """
    n = len(dets)
    if n < 2:
        return list(dets)

    # Descending score, ties broken by original position — matches sorted().
    order = sorted(range(n), key=lambda i: (-dets[i]["score"], i))

    by_class: dict = {}
    for i in order:
        by_class.setdefault(dets[i]["class_name"], []).append(i)

    kept_idx = set()
    for idxs in by_class.values():
        boxes = np.asarray([dets[i]["_xyxy"] for i in idxs], dtype=np.float64)
        areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
        alive = np.ones(len(idxs), dtype=bool)

        for pos in range(len(idxs)):
            if not alive[pos]:
                continue
            kept_idx.add(idxs[pos])

            rest = np.nonzero(alive[pos + 1:])[0] + pos + 1
            if rest.size == 0:
                continue

            b, o = boxes[pos], boxes[rest]
            iw = np.minimum(b[2], o[:, 2]) - np.maximum(b[0], o[:, 0])
            ih = np.minimum(b[3], o[:, 3]) - np.maximum(b[1], o[:, 1])
            inter = np.maximum(iw, 0.0) * np.maximum(ih, 0.0)
            union = areas[pos] + areas[rest] - inter
            iou = np.divide(inter, union, out=np.zeros_like(inter),
                            where=union > 0)
            alive[rest[iou > iou_thresh]] = False

    # Emit in the same descending-score order the scalar version produced.
    return [dets[i] for i in order if i in kept_idx]


def _parse_boxes(results, img: np.ndarray, img_w: int, img_h: int,
                 model, offset_x: int = 0, offset_y: int = 0) -> list[dict]:
    """Convert YOLO result boxes into detection dicts with padding applied."""
    dets = []
    for r in results:
        boxes = getattr(r, "boxes", None)
        if boxes is None or len(boxes) == 0:
            continue

        # Pull the whole result off the GPU in three transfers instead of
        # three *per box*. With max_det up to 200 that was 600 device syncs
        # per pass, each one stalling the pipeline.
        xyxy_all  = boxes.xyxy.cpu().numpy()
        conf_all  = boxes.conf.cpu().numpy()
        cls_attr  = getattr(boxes, "cls", None)
        cls_all   = (cls_attr.cpu().numpy().astype(int) if cls_attr is not None
                     else np.full(len(xyxy_all), -1, dtype=int))

        for _i in range(len(xyxy_all)):
            x1, y1, x2, y2 = xyxy_all[_i]
            score  = float(conf_all[_i])
            cls_id = int(cls_all[_i])
            class_name = model.names.get(cls_id, f"class_{cls_id}")

            # Shift tile-relative coords back to full-image coords
            x1, y1, x2, y2 = (int(x1) + offset_x, int(y1) + offset_y,
                               int(x2) + offset_x, int(y2) + offset_y)

            # Map to one of 2 targets; skip anything not in the map (person, helmet, etc.)
            class_name = _CLASS_MAP.get(class_name.lower())
            if class_name is None:
                continue

            box_w, box_h = x2 - x1, y2 - y1
            aspect_ratio = box_w / max(box_h, 1)

            is_plate = class_name == "license_plate"
            min_conf = _CONF_PLATE if is_plate else _CONF_VEHICLE

            if score < min_conf or box_w < 30 or box_h < 12:
                continue
            if not is_plate:
                if aspect_ratio < 0.3 or aspect_ratio > 6.0:
                    continue

            vehicle_type = None
            if is_plate:
                # Small uniform padding — plate_detector gives tight boxes so
                # large padding just adds vehicle body that hurts OCR.
                pad = max(int(min(box_w, box_h) * 0.10), 6)
                cx1 = max(0,      x1 - pad)
                cy1 = max(0,      y1 - pad)
                cx2 = min(img_w,  x2 + pad)
                cy2 = min(img_h,  y2 + pad)
            else:
                cx1, cy1 = max(0, x1), max(0, y1)
                cx2, cy2 = min(img_w, x2), min(img_h, y2)
                vehicle_type = class_name

            dets.append({
                "crop": img[cy1:cy2, cx1:cx2],
                # tight bbox (YOLO output) — used for display on the frontend
                "bbox": {
                    "x":      float(x1 / img_w),
                    "y":      float(y1 / img_h),
                    "width":  float((x2 - x1) / img_w),
                    "height": float((y2 - y1) / img_h),
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
    Detect license plates in an image — the entry-gate detector.

    Plate weights only (runs/plate_detector/weights/best.pt), trained
    specifically for Philippine plates.  The vehicle detector is deliberately
    not consulted: the gate identifies a vehicle by its plate, so a car-body box
    changed no decision here while costing a second full inference — plus a
    tiled sweep on high-res frames — on every frame of every gate camera.
    Vehicle boxes live in detect_vehicles(), which parking occupancy calls.

    Handles all conditions via:
    - Adaptive preprocessing  (dark/night/glare/dim)
    - Multi-rotation fallback (tilted cameras / angled plates) — skip with try_rotation=False

    `conf` is accepted for call-site compatibility and ignored: it only ever
    reached the vehicle passes, and callers that raise it (reader.py passes
    0.25) were never asking for a stricter *plate* threshold.  Plates are held
    to _CONF_PLATE here and again in _parse_boxes.
    """
    plate_model = _get_plate_yolo()
    if plate_model is None:
        return []

    h, w = img.shape[:2]
    gpu = is_gpu_available()

    # Preprocessing is pure NumPy/OpenCV — fully thread-safe, runs outside the lock.
    img_proc = _preprocess_adaptive(img)
    all_dets: list[dict] = []

    # All model.predict() calls are serialised by _INFER_LOCK.
    # Ultralytics YOLO and PyTorch share internal inference buffers on the same
    # model object; concurrent calls from different camera threads corrupt those
    # buffers.  GPU inference is serialised by CUDA anyway, so this lock adds no
    # throughput penalty in practice.
    with _INFER_LOCK:
        # ── Plate pass ────────────────────────────────────────────────────────
        plate_res = plate_model.predict(
            img_proc, conf=_CONF_PLATE, verbose=False, max_det=100,
            half=gpu, imgsz=640,
        )
        plate_dets = _parse_boxes(plate_res, img, w, h, plate_model)
        all_dets.extend(d for d in plate_dets if d["class_name"] == "license_plate")

        # ── Rotation fallback ─────────────────────────────────────────────────
        if try_rotation and not all_dets:
            center = (w // 2, h // 2)
            for angle in [20, -20, 40, -40, 60, -60]:
                M = cv2.getRotationMatrix2D(center, angle, 1.0)
                img_rot = cv2.warpAffine(img, M, (w, h),
                                         flags=cv2.INTER_LINEAR,
                                         borderMode=cv2.BORDER_REFLECT_101)
                rot_res = plate_model.predict(_preprocess_adaptive(img_rot), conf=_CONF_PLATE,
                                              verbose=False, max_det=100,
                                              half=gpu, imgsz=640)
                rot_dets = [d for d in _parse_boxes(rot_res, img_rot, w, h, plate_model)
                            if d["class_name"] == "license_plate"]

                if rot_dets:
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
                    break

    all_dets = _nms(all_dets)

    for d in all_dets:
        d.pop("_xyxy", None)

    all_dets.sort(key=lambda d: d["score"], reverse=True)
    return all_dets


def detect_vehicles(img: np.ndarray, conf: float = _CONF_VEHICLE) -> list[dict]:
    """Vehicle boxes only — no plate model, no rotation fallback, no tiling.

    Parking occupancy is decided from where the *car body* sits, so the plate
    detector contributes nothing here. The parking loop used to call
    detect_plates() and feed every returned box into the occupancy test, which
    was wrong twice over: it paid for a plate inference on every frame of every
    zone, and it let a plate box (a small rectangle near the bumper) decide
    which space a car was in. A plate centroid can sit in the neighbouring bay
    while the car is parked correctly.

    Returns the same dicts as detect_plates(), filtered to class_name ==
    "vehicle", with the normalised "bbox" being the full vehicle body.
    """
    model = _get_vehicle_yolo()
    if model is None:
        return []

    h, w = img.shape[:2]
    gpu = is_gpu_available()
    img_proc = _preprocess_adaptive(img)

    with _INFER_LOCK:
        res = model.predict(img_proc, conf=conf, verbose=False, max_det=200,
                            half=gpu, imgsz=1280 if gpu else 960)
        dets = [d for d in _parse_boxes(res, img, w, h, model)
                if d["class_name"] != "license_plate"]

    dets = _nms(dets)
    for d in dets:
        d.pop("_xyxy", None)
    dets.sort(key=lambda d: d["score"], reverse=True)
    return dets


def is_gpu_available() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError:
        return False


def set_gpu_enabled(enabled: bool = True):
    """Move any already-loaded models to the GPU."""
    if not (enabled and is_gpu_available()):
        return
    for name, model in (("plate-detector", _plate_model),
                        ("vehicle-detector", _vehicle_model)):
        if model is None:
            continue
        try:
            model.to("cuda")
            log.info("[DETECT] %s GPU enabled", name)
        except Exception as exc:
            log.warning("[DETECT] GPU enable failed for %s: %s", name, exc)
