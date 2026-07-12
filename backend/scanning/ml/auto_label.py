"""
auto_label.py — Two-model auto-labeling for vehicle detection.

Uses the plate_detector model for license_plate detection (high-recall mode).
Uses COCO YOLO26n for vehicle detection: cars, motorcycles, buses, trucks…
are ALL labelled as the single unified "vehicle" class.

Output label schema: 0 = license_plate, 1 = vehicle.
(train.py's remap keeps only the vehicle rows when building the
single-class vehicle dataset, so this output can feed either model.)

Usage:
    python -m scanning.ml.auto_label --frames-dir <path> --output-dir <path>
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

BASE_DIR = Path(__file__).resolve().parent

CLASS_NAMES = [
    "license_plate",  # 0 — from the plate_detector model
    "vehicle",        # 1 — every motorized vehicle, unified
]

# COCO class IDs → our class name (everything collapses to "vehicle")
COCO_VEHICLE_MAP = {
    1: "vehicle",    # bicycle
    2: "vehicle",    # car
    3: "vehicle",    # motorcycle
    5: "vehicle",    # bus
    7: "vehicle",    # truck
}

CONF_THRESHOLD = 0.08   # license-plate pass — low to maximise recall
CONF_VEHICLE  = 0.20    # COCO vehicle pass — slightly higher, less noisy


def load_models():
    """Load the plate_detector model and COCO vehicle model (auto-downloads yolov8n)."""
    from ultralytics import YOLO

    plate_pt = BASE_DIR / "runs" / "plate_detector" / "weights" / "best.pt"
    model_best = YOLO(str(plate_pt)) if plate_pt.exists() else None

    try:
        # "yolo26n.pt" is downloaded automatically by Ultralytics if not cached
        model_coco = YOLO("yolo26n.pt")
    except Exception as exc:
        print(f"[WARN] Could not load COCO model: {exc}")
        model_coco = None

    return model_best, model_coco


def compute_iou(box1, box2):
    """IoU for two bounding boxes in normalised cx,cy,w,h format."""
    cx1, cy1, w1, h1 = box1
    cx2, cy2, w2, h2 = box2
    x1_1, y1_1 = cx1 - w1 / 2, cy1 - h1 / 2
    x2_1, y2_1 = cx1 + w1 / 2, cy1 + h1 / 2
    x1_2, y1_2 = cx2 - w2 / 2, cy2 - h2 / 2
    x2_2, y2_2 = cx2 + w2 / 2, cy2 + h2 / 2

    xi1, yi1 = max(x1_1, x1_2), max(y1_1, y1_2)
    xi2, yi2 = min(x2_1, x2_2), min(y2_1, y2_2)
    inter = max(0.0, xi2 - xi1) * max(0.0, yi2 - yi1)
    union = w1 * h1 + w2 * h2 - inter
    return 0.0 if union == 0 else inter / union


def _apply_clahe(img: np.ndarray) -> np.ndarray:
    """Boost contrast with CLAHE on L channel — helps distant/dim plates."""
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    l = clahe.apply(l)
    return cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)


def _bbox_norm(x1, y1, x2, y2, img_w, img_h):
    """Return normalised [cx, cy, bw, bh]."""
    return [
        (x1 + x2) / (2 * img_w),
        (y1 + y2) / (2 * img_h),
        (x2 - x1) / img_w,
        (y2 - y1) / img_h,
    ]


# ── license-plate detection (best.pt) ─────────────────────────────────────────

def _detect_plates_full(model, img: np.ndarray, img_w: int, img_h: int) -> list[dict]:
    """Full-frame pass at imgsz=1280 with TTA."""
    dets = []
    for r in model(img, verbose=False, conf=CONF_THRESHOLD,
                   max_det=200, imgsz=1280, augment=True):
        for box in r.boxes:
            if int(box.cls[0]) != 0:
                continue
            x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
            dets.append({
                "class": "license_plate",
                "conf": float(box.conf[0]),
                "bbox": _bbox_norm(x1, y1, x2, y2, img_w, img_h),
                "source": "plate_full",
            })
    return dets


def _detect_plates_tiled(model, img: np.ndarray, img_w: int, img_h: int,
                          tile: int = 640, overlap: float = 0.2) -> list[dict]:
    """Sliding-window pass to catch small/distant plates."""
    dets = []
    stride = int(tile * (1 - overlap))
    for y0 in range(0, img_h, stride):
        for x0 in range(0, img_w, stride):
            x1t, y1t = x0, y0
            x2t, y2t = min(x0 + tile, img_w), min(y0 + tile, img_h)
            patch = img[y1t:y2t, x1t:x2t]
            ph, pw = patch.shape[:2]
            if pw < 64 or ph < 64:
                continue
            for r in model(patch, verbose=False, conf=CONF_THRESHOLD, max_det=50, imgsz=640):
                for box in r.boxes:
                    if int(box.cls[0]) != 0:
                        continue
                    bx1, by1, bx2, by2 = box.xyxy[0].cpu().numpy()
                    ax1, ay1 = x1t + bx1, y1t + by1
                    ax2, ay2 = x1t + bx2, y1t + by2
                    dets.append({
                        "class": "license_plate",
                        "conf": float(box.conf[0]),
                        "bbox": _bbox_norm(ax1, ay1, ax2, ay2, img_w, img_h),
                        "source": "plate_tile",
                    })
    return dets


# ── vehicle detection (COCO yolov8n) ──────────────────────────────────────────

def _detect_vehicles_coco(model_coco, img: np.ndarray, img_w: int, img_h: int) -> list[dict]:
    """Detect vehicles using COCO — car/motorcycle/bus/truck/bicycle all → "vehicle"."""
    dets = []
    for r in model_coco(img, verbose=False, conf=CONF_VEHICLE, max_det=100, imgsz=1280):
        for box in r.boxes:
            coco_cls = int(box.cls[0])
            if coco_cls not in COCO_VEHICLE_MAP:
                continue
            x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
            dets.append({
                "class": COCO_VEHICLE_MAP[coco_cls],
                "conf": float(box.conf[0]),
                "bbox": _bbox_norm(x1, y1, x2, y2, img_w, img_h),
                "source": "coco",
            })
    return dets


# ── main pipeline ─────────────────────────────────────────────────────────────

def _nms(detections: list[dict], iou_thresh: float = 0.45) -> list[dict]:
    """Per-class NMS: drop lower-conf box when IoU > threshold."""
    detections.sort(key=lambda d: d["conf"], reverse=True)
    kept = []
    for det in detections:
        keep = True
        for existing in kept:
            if existing["class"] != det["class"]:
                continue
            if compute_iou(det["bbox"], existing["bbox"]) > iou_thresh:
                keep = False
                break
        if keep:
            kept.append(det)
    return kept


def run_auto_label(model_best, model_coco, frames_dir: Path, output_dir: Path):
    """Detect license plates (best.pt) and vehicles (COCO) in all frames."""
    labels_dir = output_dir / "labels"
    labels_dir.mkdir(parents=True, exist_ok=True)
    flagged_dir = output_dir / "flagged"
    flagged_dir.mkdir(parents=True, exist_ok=True)

    stats = {
        "total_frames": 0,
        "frames_with_detections": 0,
        "frames_no_detections": 0,
        "by_class": {cls: 0 for cls in CLASS_NAMES},
    }

    image_files = sorted(frames_dir.glob("*.jpg"))
    total = len(image_files)
    start_time = time.time()

    if tqdm is not None:
        iterator = tqdm(image_files, desc="Labeling", unit="frame",
                        dynamic_ncols=True,
                        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]")
    else:
        iterator = image_files

    for img_path in iterator:
        stats["total_frames"] += 1

        img = cv2.imread(str(img_path))
        if img is None:
            continue
        h, w = img.shape[:2]

        img_enhanced = _apply_clahe(img)
        all_dets = []

        # Plate detection — CLAHE helps for small/dim plates
        if model_best is not None:
            all_dets.extend(_detect_plates_full(model_best, img_enhanced, w, h))
            if w > 960 or h > 720:
                all_dets.extend(_detect_plates_tiled(model_best, img_enhanced, w, h))

        # Vehicle detection — use original image (CLAHE distorts large objects)
        if model_coco is not None:
            all_dets.extend(_detect_vehicles_coco(model_coco, img, w, h))

        final_labels = _nms(all_dets)

        if final_labels:
            stats["frames_with_detections"] += 1
            for lbl in final_labels:
                if lbl["class"] in stats["by_class"]:
                    stats["by_class"][lbl["class"]] += 1
        else:
            stats["frames_no_detections"] += 1

        label_path = labels_dir / f"{img_path.stem}.txt"
        with open(label_path, "w") as f:
            for lbl in final_labels:
                cls_id = CLASS_NAMES.index(lbl["class"]) if lbl["class"] in CLASS_NAMES else 0
                cx, cy, bw, bh = lbl["bbox"]
                f.write(f"{cls_id} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}\n")

        # Fallback progress when tqdm is not installed
        if tqdm is None and stats["total_frames"] % 100 == 0:
            elapsed = time.time() - start_time
            fps = stats["total_frames"] / max(elapsed, 1)
            remaining = (total - stats["total_frames"]) / max(fps, 0.001)
            print(f"  {stats['total_frames']}/{total} frames "
                  f"| {fps:.1f} fps "
                  f"| ~{int(remaining // 60)}m {int(remaining % 60)}s left "
                  f"| detections: {stats['frames_with_detections']}")

    return stats


def main():
    parser = argparse.ArgumentParser(description="Auto-label frames: plates (plate_detector) + vehicles (COCO)")
    parser.add_argument("--frames-dir", type=str, default=None)
    parser.add_argument("--output-dir", type=str, default=None)
    parser.add_argument("--visualize", type=int, default=0,
                        help="Visualisation samples per class (0=all, -1=skip)")
    args = parser.parse_args()

    frames_dir = Path(args.frames_dir or "scanning/ml/raw_frames").resolve()
    if not frames_dir.exists() and not args.frames_dir:
        frames_dir = Path("backend/scanning/ml/raw_frames").resolve()

    output_dir = Path(args.output_dir or "scanning/ml/labeled").resolve()
    if not output_dir.parent.exists() and not args.output_dir:
        output_dir = Path("backend/scanning/ml/labeled").resolve()

    print("Loading models...")
    model_best, model_coco = load_models()

    if model_best is None:
        print("[ERROR] plate_detector weights not found at runs/plate_detector/weights/best.pt")
        return 1
    if model_coco is None:
        print("[WARN] COCO model unavailable — vehicle detection disabled")

    n_frames = len(list(frames_dir.glob("*.jpg")))
    print(f"\nAuto-labeling {n_frames} frames")
    print(f"  Plates : conf>={CONF_THRESHOLD}, imgsz=1280, TTA=on, CLAHE=on, tiled")
    print(f"  Vehicles: conf>={CONF_VEHICLE}, imgsz=1280, classes={list(COCO_VEHICLE_MAP.values())}")

    stats = run_auto_label(model_best, model_coco, frames_dir, output_dir)

    print("\n" + "=" * 60)
    print("AUTO-LABELING RESULTS")
    print("=" * 60)
    print(f"Total frames          : {stats['total_frames']}")
    print(f"Frames with detections: {stats['frames_with_detections']}")
    print(f"Frames no detections  : {stats['frames_no_detections']}")
    print("\nPer-class detection counts:")
    for cls, count in stats["by_class"].items():
        print(f"  {cls}: {count}")

    print(f"\nLabels saved to: {output_dir / 'labels'}")
    print(f"Flagged samples: {output_dir / 'flagged'}")

    if args.visualize >= 0:
        label_word = "ALL" if args.visualize == 0 else str(args.visualize)
        print(f"\nGenerating visualizations ({label_word} per class, one folder per class)...")
        from .visualize_labels import visualize_dataset
        visualize_dataset(
            output_dir / "labels",
            frames_dir,
            output_dir.parent / "visualizations",
            args.visualize,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
