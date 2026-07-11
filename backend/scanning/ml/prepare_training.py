"""
prepare_training.py — Build the single-class vehicle dataset from auto-labeled
camera footage.

Takes raw frames + auto_label output (0 = license_plate, 1 = vehicle) and
writes a vehicle-only YOLO dataset into vehicle_ds/ using the same layout as
a Roboflow export (train/images, train/labels, valid/…), so footage-derived
data and Roboflow data merge cleanly.

Per the vehicle/plate policy:
  - Every vehicle-ish label (including legacy bicycle/motorcycle/jeep/truck/bus
    ids from old label files) is rewritten to class 0 = "vehicle".
  - license_plate labels are DROPPED — plates belong to the separate
    plate_detector dataset (dataset/), which this script never touches.
  - Frames left with no vehicle labels are skipped (an unlabeled vehicle in a
    "background" image would hurt training).

NOTE: train.py --zip wipes vehicle_ds/ — ingest the main Roboflow export
first, then run this to add footage-derived images on top.

Usage:
    python -m scanning.ml.prepare_training
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
VEHICLE_DS_DIR = BASE_DIR / "vehicle_ds"

# Label remap → single-class schema (None = drop the label row).
# Covers both current auto_label output (0=plate, 1=vehicle) and legacy
# 9-class label files (0=plate 1=vehicle 2=bicycle 3=e_bike 4=escooter
# 5=motorcycle 6=jeep 7=truck 8=bus).
OLD_TO_NEW: dict[int, int | None] = {
    0: None,  # license_plate    → dropped (plate dataset is separate)
    1: 0,     # vehicle          → vehicle
    2: 0,     # bicycle          → vehicle
    3: None,  # e_bike           → dropped (unplated, excluded)
    4: None,  # electric_scooter → dropped (unplated, excluded)
    5: 0,     # motorcycle       → vehicle
    6: 0,     # jeep             → vehicle
    7: 0,     # truck            → vehicle
    8: 0,     # bus              → vehicle
}


def _remap_lines(lbl_path: Path) -> list[str]:
    """Read a label file and return vehicle-only lines in single-class schema.

    Handles both 5-column YOLO format (cls x y w h) and 6-column
    auto-label format (cls conf x y w h) — confidence is stripped.
    """
    lines_out: list[str] = []
    for line in lbl_path.read_text().strip().splitlines():
        parts = line.strip().split()
        if len(parts) == 6:
            cls, coords = int(parts[0]), parts[2:]
        elif len(parts) == 5:
            cls, coords = int(parts[0]), parts[1:]
        else:
            continue
        new_cls = OLD_TO_NEW.get(cls)
        if new_cls is None:
            continue
        lines_out.append(f"{new_cls} {' '.join(coords)}")
    return lines_out


def build_dataset(raw_frames_dir: Path, raw_labels_dir: Path, output_dir: Path,
                  flagged_dir: Path, split_ratio: float = 0.8) -> tuple[int, int, int]:
    """Build the vehicle_ds structure with an 80/20 split.

    Returns (train_count, val_count, skipped_no_vehicle).
    """
    images_train = output_dir / "train" / "images"
    labels_train = output_dir / "train" / "labels"
    images_val   = output_dir / "valid" / "images"
    labels_val   = output_dir / "valid" / "labels"

    for d in [images_train, images_val, labels_train, labels_val]:
        d.mkdir(parents=True, exist_ok=True)

    flagged_stems = set()
    if flagged_dir.exists():
        for f in flagged_dir.glob("*.txt"):
            flagged_stems.add(f.stem.replace("_tricycle_flag", ""))

    image_files = sorted(raw_frames_dir.glob("*.jpg"))
    total = len(image_files)
    split_idx = int(total * split_ratio)

    skipped_no_vehicle = 0

    def copy_files(files, img_dest, lbl_dest):
        nonlocal skipped_no_vehicle
        copied = 0
        for img_path in files:
            lbl_path = raw_labels_dir / f"{img_path.stem}.txt"
            if not lbl_path.exists() or img_path.stem in flagged_stems:
                continue
            lines = _remap_lines(lbl_path)
            if not lines:
                skipped_no_vehicle += 1
                continue
            shutil.copy2(img_path, img_dest / img_path.name)
            (lbl_dest / lbl_path.name).write_text("\n".join(lines) + "\n")
            copied += 1
        return copied

    train_count = copy_files(image_files[:split_idx], images_train, labels_train)
    val_count   = copy_files(image_files[split_idx:], images_val,   labels_val)

    return train_count, val_count, skipped_no_vehicle


def write_data_yaml(output_dir: Path) -> Path:
    """Write the single-class data.yaml — only if one doesn't already exist
    (a Roboflow export ingested by train.py --zip brings its own)."""
    import yaml

    yaml_path = output_dir / "data.yaml"
    if yaml_path.exists():
        print(f"  data.yaml already present — left untouched: {yaml_path}")
        return yaml_path

    cfg = {
        "path":  str(output_dir),
        "train": "train/images",
        "val":   "valid/images",
        "nc":    1,
        "names": ["vehicle"],
    }
    with open(yaml_path, "w") as f:
        yaml.dump(cfg, f, sort_keys=False, default_flow_style=False)
    return yaml_path


def main():
    parser = argparse.ArgumentParser(
        description="Build the single-class vehicle dataset from auto-labeled footage"
    )
    parser.add_argument("--raw-frames", type=str, default=None,
                        help="Directory with raw extracted frames")
    parser.add_argument("--raw-labels", type=str, default=None,
                        help="Directory with auto-generated labels")
    parser.add_argument("--flagged", type=str, default=None,
                        help="Directory with flagged detections to exclude")
    parser.add_argument("--output", type=str, default=None,
                        help="Output dataset directory (default: scanning/ml/vehicle_ds)")
    args = parser.parse_args()

    raw_frames  = Path(args.raw_frames or "scanning/ml/raw_frames").resolve()
    raw_labels  = Path(args.raw_labels or "scanning/ml/labeled/labels").resolve()
    flagged_dir = Path(args.flagged or "scanning/ml/labeled/flagged").resolve()
    output_dir  = Path(args.output).resolve() if args.output else VEHICLE_DS_DIR

    print("\n[1/2] Building single-class vehicle dataset from raw footage...")
    train_count, val_count, skipped = build_dataset(raw_frames, raw_labels,
                                                    output_dir, flagged_dir)
    print(f"  Train: {train_count} images, Val: {val_count} images")
    if skipped:
        print(f"  Skipped {skipped} frames with no vehicle labels")

    print("\n[2/2] Writing data.yaml...")
    yaml_path = write_data_yaml(output_dir)
    print(f"  {yaml_path}")

    print("\n" + "=" * 60)
    print("DATASET PREPARATION COMPLETE")
    print("=" * 60)
    print(f"Output: {output_dir}")
    print("\nRun training with:")
    print("  python -m scanning.ml.train")

    return 0


if __name__ == "__main__":
    sys.exit(main())
