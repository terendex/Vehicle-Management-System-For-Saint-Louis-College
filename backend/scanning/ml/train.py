"""
scanning/ml/train.py — Train / fine-tune the multi-class YOLOv8 detector.

Supports:
  --freeze   : Freeze backbone layers and fine-tune the detection head only
               (recommended when starting from existing best.pt checkpoint)
  --download : Download + merge Roboflow unplated datasets before training
  --incremental : Append collected MLTrainingSample records from DB to dataset

Security-camera augmentation profile:
  - Gaussian blur (sigma 0.5–2.0) simulates soft-focus at distance
  - Motion blur (kernel 5–15 px) simulates vehicle movement
  - Lighting variations (brightness ±30%, contrast ±20%)
  - Modest geometric augmentation — NO heavy perspective warp
    (camera is fixed-mount, not handheld)
"""
from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

# Configure Django settings module environment variable
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

BASE_DIR    = Path(__file__).resolve().parent
UNPLATED_DIR = BASE_DIR.parent.parent / "unplated_ds"
# Use the unified 5-class dataset as the primary training source
DATA_YAML   = UNPLATED_DIR / "data.yaml"
WEIGHTS_DIR = BASE_DIR / "weights"

# Fallback: original license_plate-only dataset for reference
LEGACY_DATA_YAML = BASE_DIR / "dataset" / "data.yaml"


def _export_ml_samples(limit: int = 500) -> int:
    """Export verified MLTrainingSample DB records into the training dataset."""
    import django
    django.setup()

    from django.utils import timezone
    from scanning.models import MLTrainingSample

    dataset_img = UNPLATED_DIR / "images" / "train"
    dataset_lbl = UNPLATED_DIR / "labels" / "train"
    dataset_img.mkdir(parents=True, exist_ok=True)
    dataset_lbl.mkdir(parents=True, exist_ok=True)

    from django.conf import settings

    qs = MLTrainingSample.objects.filter(
        status__in=["auto_labeled", "verified"],
        used_in_training=False,
    ).exclude(bbox__isnull=True).exclude(plate_number="").order_by("created_at")[:limit]

    exported = 0
    for sample in qs:
        try:
            src = Path(settings.MEDIA_ROOT) / sample.image
            if not src.exists():
                continue

            stem = f"ml_{sample.pk}_{timezone.now().strftime('%Y%m%d%H%M%S')}"
            dst_img = dataset_img / f"{stem}.jpg"
            shutil.copy2(src, dst_img)

            bboxes = sample.bbox or []
            lines  = []
            for bb in bboxes:
                x  = bb.get("x", 0)
                y  = bb.get("y", 0)
                bw = bb.get("width", 0)
                bh = bb.get("height", 0)
                # license_plate = class id 0
                lines.append(f"0 {x} {y} {bw} {bh}")

            with open(dataset_lbl / f"{stem}.txt", "w") as lf:
                lf.write("\n".join(lines))

            sample.used_in_training = True
            sample.save(update_fields=["used_in_training"])
            exported += 1
        except Exception as exc:
            print(f"⚠️  Failed to export sample {sample.pk}: {exc}")

    print(f"📥 Exported {exported} ML samples into the YOLO dataset")
    return exported


def _fix_data_yaml_path(yaml_path: Path) -> None:
    """Ensure data.yaml uses an absolute path for the dataset root."""
    import yaml

    with open(yaml_path) as f:
        cfg = yaml.safe_load(f)

    cfg["path"] = str(yaml_path.parent)
    with open(yaml_path, "w") as f:
        yaml.dump(cfg, f, sort_keys=False, default_flow_style=False)


def _validate_labels(dataset_dir: Path) -> None:
    """Check all YOLO label files for invalid bounding boxes and remove bad ones."""
    bad_files = []
    for split in ("train", "val"):
        lbl_dir = dataset_dir / "labels" / split
        if not lbl_dir.exists():
            continue
        for lbl_file in lbl_dir.glob("*.txt"):
            lines = lbl_file.read_text().strip().splitlines()
            clean_lines = []
            has_bad = False
            for line in lines:
                parts = line.strip().split()
                if len(parts) != 5:
                    has_bad = True
                    continue
                try:
                    cls, cx, cy, w, h = int(parts[0]), float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4])
                except ValueError:
                    has_bad = True
                    continue
                if not (0.0 <= cx <= 1.0 and 0.0 <= cy <= 1.0 and 0.0 < w <= 1.0 and 0.0 < h <= 1.0):
                    has_bad = True
                    continue
                clean_lines.append(line)
            if has_bad:
                bad_files.append(lbl_file)
                lbl_file.write_text("\n".join(clean_lines))
    if bad_files:
        print(f"⚠️  Fixed {len(bad_files)} label files with invalid bounding boxes:")
        for f in bad_files[:10]:
            print(f"   {f.name}")
        if len(bad_files) > 10:
            print(f"   ... and {len(bad_files) - 10} more")
    else:
        print("✅ All label files validated — no invalid bounding boxes found")


def train(
    epochs:       int  = 100,
    batch:        int  = 8,
    imgsz:        int  = 640,
    model_size:   str  = "s",
    resume:       bool = False,
    incremental:  bool = False,
    freeze:       bool = True,
    freeze_layers: int = 10,
    data_yaml:    Path | None = None,
) -> None:
    """
    Train or fine-tune the multi-class vehicle detector.

    Args:
        freeze:        If True, freeze the first `freeze_layers` backbone layers.
                       Strongly recommended when fine-tuning from existing best.pt.
        freeze_layers: Number of backbone layers to freeze (YOLOv8n/s: 10 is safe).
        data_yaml:     Path to a custom data.yaml. Defaults to unplated_ds/data.yaml.
    """
    import django
    django.setup()

    if incremental:
        exported = _export_ml_samples()
        print(f"🧩 Incremental mode: appended {exported} new samples to the dataset")

    from ultralytics import YOLO

    # Resolve dataset
    dataset_dir = data_yaml.parent if data_yaml else UNPLATED_DIR
    global DATA_YAML
    if data_yaml:
        DATA_YAML = Path(data_yaml)

    # Validate dataset
    train_imgs = dataset_dir / "images" / "train"
    val_imgs   = dataset_dir / "images" / "val"
    train_count = len(list(train_imgs.glob("*"))) if train_imgs.exists() else 0
    val_count   = len(list(val_imgs.glob("*")))   if val_imgs.exists()   else 0

    _validate_labels(UNPLATED_DIR)

    if train_count == 0:
        print("=" * 60)
        print("  ERROR: No training images found!")
        print(f"  Expected images in: {train_imgs}")
        print()
        print("  Run first:")
        print("    python -m scanning.ml.train --download --api-key <KEY>")
        print("=" * 60)
        return

    print(f"📦 Dataset:  {train_count} training images, {val_count} validation images")

    # Select base model
    best_ckpt = WEIGHTS_DIR / "best.pt"
    last_ckpt = WEIGHTS_DIR / "last.pt"

    if resume and last_ckpt.exists():
        print("🔄 Resuming from last checkpoint…")
        model = YOLO(str(last_ckpt))
    elif best_ckpt.exists():
        print(f"🔁 Fine-tuning from existing checkpoint: {best_ckpt}")
        model = YOLO(str(best_ckpt))
        freeze_note = f" (backbone frozen: {freeze_layers} layers)" if freeze else ""
        print(f"   Strategy: fine-tune detection head{freeze_note}")
    else:
        print(f"🧠 Starting from pretrained YOLOv8{model_size} (no existing checkpoint)")
        model = YOLO(f"yolov8{model_size}.pt")
        freeze = False  # Nothing to freeze from scratch

    # Fix absolute path in data.yaml
    _fix_data_yaml_path(DATA_YAML)

    print(f"🧠 Model:    YOLOv8{model_size}  |  {epochs} epochs  |  batch {batch}  |  imgsz {imgsz}")
    if freeze:
        print(f"🧊 Freezing first {freeze_layers} backbone layers")
    print()

    # ── Security-camera augmentation profile ──────────────────────────────
    # Fixed-mount overhead/angled camera — avoid heavy geometric distortion.
    # Focus on blur, lighting, and mild colour shifts.
    augment_kwargs = {
        # Motion blur — simulates moving vehicles
        "mixup":           0.1,      # mild mixup helps minority class generalization
        # Colour/exposure variations — lighting changes throughout day
        "hsv_h":           0.015,    # hue shift ±1.5 %
        "hsv_s":           0.4,      # saturation ±40 %
        "hsv_v":           0.3,      # value (brightness) ±30 %
        # Geometric — mild only for fixed-mount camera
        "degrees":         5.0,      # rotation ±5° (slight tilt)
        "translate":       0.1,      # translation ±10%
        "scale":           0.3,      # scale ±30%
        "shear":           0.0,      # no shear (fixed vantage)
        "perspective":     0.0,      # no perspective warp (fixed mount)
        "flipud":          0.0,      # no vertical flip (cameras don't invert)
        "fliplr":          0.5,      # horizontal flip (vehicles go both ways)
        "mosaic":          1.0,      # full mosaic mixes rare class instances more often
        "copy_paste":      0.2,      # copies minority class instances into other images
    }

    train_kwargs = dict(
        data=str(DATA_YAML),
        epochs=epochs,
        imgsz=imgsz,
        batch=batch,
        workers=2,
        name="vehicle_unplated_detector",
        project=str(BASE_DIR / "runs"),
        exist_ok=True,
        patience=25,
        save=True,
        plots=True,
        amp=True,
        cache="ram",
        optimizer="Adam",        # Muon optimizer uses BF16 cuBLAS which fails on older GPUs
        **augment_kwargs,
    )

    if freeze and freeze_layers > 0:
        train_kwargs["freeze"] = freeze_layers

    results = model.train(**train_kwargs)

    # Copy best/last weights to canonical location
    best_src = Path(results.save_dir) / "weights" / "best.pt"
    last_src = Path(results.save_dir) / "weights" / "last.pt"
    WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)

    if best_src.exists():
        shutil.copy2(best_src, WEIGHTS_DIR / "best.pt")
        print(f"\n✅ Best weights saved to:  {WEIGHTS_DIR / 'best.pt'}")

    if last_src.exists():
        shutil.copy2(last_src, WEIGHTS_DIR / "last.pt")

    print("🎉 Training complete!")
    print(f"   View results in: {results.save_dir}")
    _print_class_eval(results)

    # Auto-push new weights to R2 so teammates get them on next docker compose up
    if os.getenv("R2_ACCESS_KEY_ID") and os.getenv("R2_BUCKET_NAME"):
        try:
            from django.core.management import call_command
            print("\n☁️  Pushing weights to R2...")
            call_command("sync_ml_weights", push=True, force=True)
        except Exception as exc:
            print(f"⚠️  Could not push weights to R2: {exc}")


def _print_class_eval(results) -> None:
    """Print per-class mAP@0.5 from the final validation run."""
    try:
        from ultralytics.utils.metrics import ClassifyMetrics
        maps = getattr(results, "maps", None)
        if maps is None:
            return
        from .detection import CLASS_NAMES
        print("\n📊 Per-class mAP@0.5 (final validation):")
        for i, cls in enumerate(CLASS_NAMES):
            val = maps[i] if i < len(maps) else float("nan")
            bar = "▓" * int(val * 20) + "░" * (20 - int(val * 20))
            print(f"   {cls:<20} {bar}  {val:.3f}")
    except Exception:
        pass  # eval printing is best-effort


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train multi-class vehicle detector (YOLOv8)")
    parser.add_argument("--data",          type=str,   default=None,
                        help="Path to data.yaml (default: unplated_ds/data.yaml)")
    parser.add_argument("--epochs",        type=int,   default=100)
    parser.add_argument("--batch",         type=int,   default=16)
    parser.add_argument("--imgsz",         type=int,   default=640)
    parser.add_argument("--model-size",    type=str,   default="s",
                        choices=["n", "s", "m", "l", "x"])
    parser.add_argument("--resume",        action="store_true",
                        help="Resume from last.pt checkpoint")
    parser.add_argument("--freeze",        action="store_true", default=True,
                        help="Freeze backbone layers (fine-tuning mode, recommended)")
    parser.add_argument("--no-freeze",     dest="freeze", action="store_false",
                        help="Disable backbone freezing (full retraining)")
    parser.add_argument("--freeze-layers", type=int,   default=10,
                        help="Number of backbone layers to freeze (default: 10)")
    parser.add_argument("--incremental",   action="store_true",
                        help="Append DB MLTrainingSample records to dataset before training")
    parser.add_argument("--download",      action="store_true",
                        help="Download Roboflow datasets before training")
    parser.add_argument("--api-key",       type=str,   default=None,
                        help="Roboflow API key (required with --download)")

    args = parser.parse_args()

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
        incremental=args.incremental,
        freeze=args.freeze,
        freeze_layers=args.freeze_layers,
        data_yaml=Path(args.data) if args.data else None,
    )
