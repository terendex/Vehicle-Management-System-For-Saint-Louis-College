"""
visualize_labels.py — Draw bounding boxes on images for label review.

Each class gets its own sub-folder under output_dir:
    output_dir/license_plate/
    output_dir/car/
    output_dir/motorcycle/
    output_dir/jeep/
    output_dir/truck/
    output_dir/bus/
    ...

Usage:
    python -m scanning.ml.visualize_labels --labels-dir <path> --images-dir <path> --output-dir <path>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

CLASS_NAMES = [
    "license_plate",    # 0
    "car",              # 1
    "bicycle",          # 2
    "e_bike",           # 3
    "electric_scooter", # 4
    "motorcycle",       # 5
    "jeep",             # 6
    "truck",            # 7
    "bus",              # 8
]

# BGR colours — one per class
COLORS = [
    (0,   255,   0),   # license_plate — green
    (0,     0, 255),   # car           — red
    (255,   0,   0),   # bicycle       — blue
    (255, 255,   0),   # e_bike        — cyan
    (0,   255, 255),   # electric_scooter — yellow
    (255,   0, 255),   # motorcycle    — magenta
    (128,   0, 255),   # jeep          — purple
    (0,   165, 255),   # truck         — orange
    (0,   255, 128),   # bus           — lime
]


def draw_boxes(img_path: Path, label_path: Path, output_path: Path) -> int:
    """Draw bounding boxes on an image and save it.

    Supports both 5-column YOLO format (cls x y w h) and
    6-column auto-label format (cls conf x y w h).
    Returns number of boxes drawn.
    """
    if not CV2_AVAILABLE:
        return 0

    img = cv2.imread(str(img_path))
    if img is None:
        return 0
    if not label_path.exists():
        return 0

    h, w = img.shape[:2]
    count = 0

    with open(label_path) as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) < 5:
                continue

            cls_id = int(parts[0])
            if len(parts) >= 6:
                conf = float(parts[1])
                cx, cy, bw, bh = float(parts[2]), float(parts[3]), float(parts[4]), float(parts[5])
                cls_name = CLASS_NAMES[cls_id] if cls_id < len(CLASS_NAMES) else f"cls{cls_id}"
                label_text = f"{cls_name} {conf:.2f}"
            else:
                cx, cy, bw, bh = float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4])
                cls_name = CLASS_NAMES[cls_id] if cls_id < len(CLASS_NAMES) else f"cls{cls_id}"
                label_text = cls_name

            x1 = max(0, int((cx - bw / 2) * w))
            y1 = max(0, int((cy - bh / 2) * h))
            x2 = min(w - 1, int((cx + bw / 2) * w))
            y2 = min(h - 1, int((cy + bh / 2) * h))

            color = COLORS[cls_id % len(COLORS)]
            cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
            cv2.putText(img, label_text, (x1, max(y1 - 5, 10)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
            count += 1

    if count > 0:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(output_path), img)
    return count


def visualize_dataset(labels_dir: Path, images_dir: Path, output_dir: Path, per_class: int = 0):
    """Draw bounding boxes and save images grouped into per-class sub-folders.

    Args:
        labels_dir:  directory containing *.txt label files
        images_dir:  directory containing the corresponding *.jpg frames
        output_dir:  root output directory; class sub-folders are created inside
        per_class:   max images to save per class (0 = all)
    """
    if not CV2_AVAILABLE:
        print("[ERROR] OpenCV not installed.")
        return 1

    labels_dir = Path(labels_dir).resolve()
    images_dir = Path(images_dir).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    all_labels = list(labels_dir.glob("*.txt"))

    # ── count class distribution ───────────────────────────────────────────────
    class_counts = {cls: 0 for cls in CLASS_NAMES}
    for lbl_path in all_labels:
        with open(lbl_path) as f:
            for line in f:
                parts = line.strip().split()
                if parts:
                    cls_id = int(parts[0])
                    if cls_id < len(CLASS_NAMES):
                        class_counts[CLASS_NAMES[cls_id]] += 1

    print("Class distribution in labels:")
    for cls, count in class_counts.items():
        print(f"  {cls}: {count}")

    # ── render per-class subfolders ────────────────────────────────────────────
    # Track how many images we've saved for each class
    visualized = {cls: 0 for cls in CLASS_NAMES}

    for lbl_path in all_labels:
        img_path = images_dir / f"{lbl_path.stem}.jpg"
        if not img_path.exists():
            continue

        with open(lbl_path) as f:
            lines = f.readlines()

        # Collect all classes present in this label file
        present = []
        for line in lines:
            parts = line.strip().split()
            if parts:
                cls_id = int(parts[0])
                if cls_id < len(CLASS_NAMES):
                    present.append(CLASS_NAMES[cls_id])

        if not present:
            continue

        # For each class that appears in this frame, save it to that class's folder
        rendered = False
        for cls_name in set(present):
            if per_class > 0 and visualized[cls_name] >= per_class:
                continue
            out_path = output_dir / cls_name / f"{lbl_path.stem}.jpg"
            if out_path.exists():
                visualized[cls_name] += 1
                rendered = True
                continue
            drawn = draw_boxes(img_path, lbl_path, out_path)
            if drawn > 0:
                visualized[cls_name] += 1
                rendered = True

    print("\nVisualizations generated per class:")
    for cls, count in visualized.items():
        folder = output_dir / cls
        print(f"  {cls}: {count}  →  {folder}")

    return 0


def main():
    parser = argparse.ArgumentParser(description="Visualize YOLO labels on images, one folder per class")
    parser.add_argument("--labels-dir", type=str, default=None)
    parser.add_argument("--images-dir", type=str, default=None)
    parser.add_argument("--output-dir", type=str, default=None)
    parser.add_argument("--per-class", type=int, default=0,
                        help="Max images per class (0 = all)")
    args = parser.parse_args()

    labels_dir = Path(args.labels_dir or "scanning/ml/labeled/labels").resolve()
    images_dir = Path(args.images_dir or "scanning/ml/raw_frames").resolve()
    output_dir = Path(args.output_dir or "scanning/ml/visualizations").resolve()

    return visualize_dataset(labels_dir, images_dir, output_dir, args.per_class)


if __name__ == "__main__":
    sys.exit(main())
