"""
prepare_training.py — Merge raw footage labels with existing dataset for training.

Usage:
    python -m scanning.ml.prepare_training
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def build_dataset(raw_frames_dir: Path, raw_labels_dir: Path, output_dir: Path, flagged_dir: Path, split_ratio: float = 0.8):
    """Build YOLOv8 dataset structure with 80/20 split, excluding flagged tricycles."""
    images_train = output_dir / "images" / "train"
    images_val = output_dir / "images" / "val"
    labels_train = output_dir / "labels" / "train"
    labels_val = output_dir / "labels" / "val"
    
    for d in [images_train, images_val, labels_train, labels_val]:
        d.mkdir(parents=True, exist_ok=True)
    
    flagged_stems = set()
    if flagged_dir.exists():
        for f in flagged_dir.glob("*.txt"):
            flagged_stems.add(f.stem.replace("_tricycle_flag", ""))
    
    image_files = sorted(raw_frames_dir.glob("*.jpg"))
    total = len(image_files)
    split_idx = int(total * split_ratio)
    
    # Old labels used 9 classes (0-8). Remap to 6-class schema:
    #   jeep(6) → vehicle(1), truck(7) → vehicle(1), bus(8) → vehicle(1)
    #   All others stay at their original index (0-5 are identical).
    OLD_TO_NEW = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 1, 7: 1, 8: 1}

    def copy_files(files, img_dest, lbl_dest):
        copied = 0
        for img_path in files:
            lbl_path = raw_labels_dir / f"{img_path.stem}.txt"
            if lbl_path.exists():
                if img_path.stem in flagged_stems:
                    continue
                shutil.copy2(img_path, img_dest / img_path.name)
                # Strip confidence column and remap old 9-class IDs → 6-class IDs.
                # auto_label writes 6 cols (cls conf x y w h); YOLO training wants 5.
                with open(lbl_path) as f_in:
                    lines = f_in.readlines()
                with open(lbl_dest / lbl_path.name, "w") as f_out:
                    for line in lines:
                        parts = line.strip().split()
                        if len(parts) == 6:
                            old_cls = int(parts[0])
                            new_cls = OLD_TO_NEW.get(old_cls, old_cls)
                            f_out.write(f"{new_cls} {parts[2]} {parts[3]} {parts[4]} {parts[5]}\n")
                        elif len(parts) == 5:
                            old_cls = int(parts[0])
                            new_cls = OLD_TO_NEW.get(old_cls, old_cls)
                            f_out.write(f"{new_cls} {parts[1]} {parts[2]} {parts[3]} {parts[4]}\n")
                copied += 1
        return copied
    
    train_count = copy_files(image_files[:split_idx], images_train, labels_train)
    val_count = copy_files(image_files[split_idx:], images_val, labels_val)
    
    return train_count, val_count


def merge_existing_dataset(existing_dir: Path, output_dir: Path):
    """Merge existing unplated dataset (license_plate, car, bicycle, e_bike, electric_scooter)."""
    existing_images = existing_dir / "images"
    existing_labels = existing_dir / "labels"
    
    if not existing_images.exists():
        return 0
    
    merged = 0
    for split in ["train", "val"]:
        src_img = existing_images / split
        src_lbl = existing_labels / split
        dst_img = output_dir / "images" / split
        dst_lbl = output_dir / "labels" / split
        
        if not src_img.exists():
            continue
        
        for img_path in src_img.glob("*.jpg"):
            if "\n" in img_path.name or "\n" in img_path.stem:
                continue
            lbl_path = src_lbl / f"{img_path.stem}.txt"
            if lbl_path.exists():
                dst_img_path = dst_img / img_path.name
                dst_lbl_path = dst_lbl / lbl_path.name
                if dst_img_path.exists() and dst_lbl_path.exists():
                    continue
                dst_img.mkdir(parents=True, exist_ok=True)
                dst_lbl.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(img_path, dst_img_path)
                    shutil.copy2(lbl_path, dst_lbl_path)
                    merged += 1
                except (FileNotFoundError, OSError):
                    continue
    
    return merged


def write_data_yaml(output_dir: Path, num_classes: int = 6):
    """Write data.yaml for 6-class training (matches auto_label CLASS_NAMES order)."""
    import yaml

    cfg = {
        "path": str(output_dir),
        "train": "images/train",
        "val": "images/val",
        "nc": num_classes,
        "names": [
            "license_plate",    # 0
            "vehicle",          # 1 — car, jeep, truck, bus (same PH plate format)
            "bicycle",          # 2
            "e_bike",           # 3
            "electric_scooter", # 4
            "motorcycle",       # 5 — different PH plate format
        ],
    }
    
    yaml_path = output_dir / "data.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(cfg, f, sort_keys=False, default_flow_style=False)
    
    return yaml_path


def main():
    parser = argparse.ArgumentParser(description="Prepare training dataset")
    parser.add_argument("--raw-frames", type=str, default=None,
                        help="Directory with raw extracted frames")
    parser.add_argument("--raw-labels", type=str, default=None,
                        help="Directory with auto-generated labels")
    parser.add_argument("--flagged", type=str, default=None,
                        help="Directory with flagged tricycle detections")
    parser.add_argument("--output", type=str, default=None,
                        help="Output dataset directory")
    parser.add_argument("--merge-existing", action="store_true",
                        help="Also merge with existing unplated_ds")
    args = parser.parse_args()

    raw_frames = Path(args.raw_frames or "scanning/ml/raw_frames").resolve()
    raw_labels = Path(args.raw_labels or "scanning/ml/labeled/labels").resolve()
    flagged_dir = Path(args.flagged or "scanning/ml/labeled/flagged").resolve()
    output_dir = Path(args.output or "scanning/ml/motorcycle_ds").resolve()
    
    print(f"\n[1/3] Building dataset from raw footage (excluding flagged tricycles)...")
    train_count, val_count = build_dataset(raw_frames, raw_labels, output_dir, flagged_dir)
    print(f"  Train: {train_count} images, Val: {val_count} images")
    
    if args.merge_existing:
        print(f"\n[2/3] Merging with existing unplated_ds...")
        existing = Path("backend/unplated_ds").resolve()
        if existing.exists():
            merged = merge_existing_dataset(existing, output_dir)
            print(f"  Merged {merged} images from existing dataset")
    
    print(f"\n[3/3] Writing data.yaml...")
    yaml_path = write_data_yaml(output_dir)
    print(f"  {yaml_path}")
    
    print("\n" + "="*60)
    print("DATASET PREPARATION COMPLETE")
    print("="*60)
    print(f"Output: {output_dir}")
    print(f"data.yaml: {yaml_path}")
    print(f"Flagged tricycles excluded: {len(list(flagged_dir.glob('*.txt'))) if flagged_dir.exists() else 0}")
    print("\nRun training with:")
    print(f"  python -m scanning.ml.video_train --data {yaml_path} --epochs 100 --batch 8 --imgsz 640 --device 0")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())