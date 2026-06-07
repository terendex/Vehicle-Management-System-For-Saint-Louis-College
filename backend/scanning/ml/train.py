"""
──────────────────────────────────────────────────────────────────────
  train.py — Train a YOLOv8 model for Philippine License Plate Detection
──────────────────────────────────────────────────────────────────────

Usage:
    cd backend
    python -m scanning.ml.train                         # default settings
    python -m scanning.ml.train --epochs 200 --batch 8  # custom settings

The trained weights are saved to:
    scanning/ml/weights/best.pt

Prerequisites:
    1. Label your images using Roboflow or LabelImg.
    2. Place them into:
         scanning/ml/dataset/images/train/   +   scanning/ml/dataset/labels/train/
         scanning/ml/dataset/images/val/     +   scanning/ml/dataset/labels/val/
    3. Run this script.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from ultralytics import YOLO


# ── Paths ───────────────────────────────────────────────────────────

BASE_DIR    = Path(__file__).resolve().parent          # scanning/ml/
DATA_YAML   = BASE_DIR / "dataset" / "data.yaml"
WEIGHTS_DIR = BASE_DIR / "weights"


def train(
    epochs: int = 100,
    batch: int = 16,
    imgsz: int = 640,
    model_size: str = "n",          # n = nano, s = small, m = medium
    resume: bool = False,
):
    """
    Train or resume training a YOLOv8 plate-detection model.

    Args:
        epochs:     Number of training epochs.
        batch:      Batch size (lower if you run out of VRAM).
        imgsz:      Input image size (square).
        model_size: YOLOv8 variant — 'n' (nano/fast), 's', 'm', 'l', 'x'.
        resume:     If True, resume from the last checkpoint.
    """

    # ── Validate dataset ────────────────────────────────────────────
    train_imgs = BASE_DIR / "dataset" / "images" / "train"
    val_imgs   = BASE_DIR / "dataset" / "images" / "val"

    train_count = len(list(train_imgs.glob("*"))) if train_imgs.exists() else 0
    val_count   = len(list(val_imgs.glob("*")))   if val_imgs.exists()   else 0

    if train_count == 0:
        print("=" * 60)
        print("  ERROR: No training images found!")
        print(f"  Place labeled images in:  {train_imgs}")
        print(f"  And matching labels in:   {BASE_DIR / 'dataset' / 'labels' / 'train'}")
        print()
        print("  Labeling tools:")
        print("    • Roboflow   — https://roboflow.com  (recommended)")
        print("    • LabelImg   — pip install labelImg")
        print("    • CVAT       — https://cvat.ai")
        print("=" * 60)
        return

    print(f"📦 Dataset: {train_count} training images, {val_count} validation images")
    print(f"🧠 Model:   YOLOv8{model_size}  |  {epochs} epochs  |  batch {batch}  |  imgsz {imgsz}")
    print()

    # ── Load model ──────────────────────────────────────────────────
    if resume and (WEIGHTS_DIR / "last.pt").exists():
        print("🔄 Resuming from last checkpoint…")
        model = YOLO(str(WEIGHTS_DIR / "last.pt"))
    else:
        model = YOLO(f"yolov8{model_size}.pt")

    # ── Ensure data.yaml has absolute path ──────────────────────────
    import yaml
    with open(DATA_YAML, "r") as f:
        data_cfg = yaml.safe_load(f)
    
    # YOLOv8 sometimes struggles with relative paths in YAML on Windows.
    # We dynamically set the absolute path before training.
    data_cfg["path"] = str(BASE_DIR / "dataset")
    with open(DATA_YAML, "w") as f:
        yaml.dump(data_cfg, f, sort_keys=False)

    # ── Train ───────────────────────────────────────────────────────
    results = model.train(
        data=str(DATA_YAML),
        epochs=epochs,
        imgsz=imgsz,
        batch=batch,
        name="plate_detector",
        project=str(BASE_DIR / "runs"),
        exist_ok=True,
        patience=20,           # early stopping after 20 epochs without improvement
        save=True,
        plots=True,
    )

    # ── Copy best weights ───────────────────────────────────────────
    best_src = Path(results.save_dir) / "weights" / "best.pt"
    last_src = Path(results.save_dir) / "weights" / "last.pt"

    WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)

    if best_src.exists():
        shutil.copy2(best_src, WEIGHTS_DIR / "best.pt")
        print(f"\n✅ Best weights saved to:  {WEIGHTS_DIR / 'best.pt'}")

    if last_src.exists():
        shutil.copy2(last_src, WEIGHTS_DIR / "last.pt")

    print("🎉 Training complete!")
    print(f"   View training plots in: {results.save_dir}")


# ── CLI ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train YOLOv8 plate detector")
    parser.add_argument("--epochs",     type=int,  default=100)
    parser.add_argument("--batch",      type=int,  default=16)
    parser.add_argument("--imgsz",      type=int,  default=640)
    parser.add_argument("--model-size", type=str,  default="n",
                        choices=["n", "s", "m", "l", "x"])
    parser.add_argument("--resume",     action="store_true")
    parser.add_argument("--download",   action="store_true",
                        help="Download Roboflow datasets before training")
    parser.add_argument("--api-key",    type=str,  default=None,
                        help="Roboflow API key (required with --download)")

    args = parser.parse_args()

    # ── Optional: download datasets first ───────────────────────────
    if args.download:
        if not args.api_key:
            print("❌ --api-key is required when using --download")
            print("   Get your API key from: https://roboflow.com → Settings → API Key")
            exit(1)
        from scanning.ml.download_datasets import download_and_merge
        download_and_merge(api_key=args.api_key)

    train(
        epochs=args.epochs,
        batch=args.batch,
        imgsz=args.imgsz,
        model_size=args.model_size,
        resume=args.resume,
    )
