"""
scanning/ml/train.py — Train / resume a YOLOv8 plate-detector.

Optional --incremental flag augments the YOLO dataset with pseudo-labeled
MLTrainingSample records stored in the DB by the ML feedback loop before
training begins (same image + auto-generated bbox labels).
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from django.conf import settings

from ultralytics import YOLO


BASE_DIR    = Path(__file__).resolve().parent
DATA_YAML   = BASE_DIR / "dataset" / "data.yaml"
WEIGHTS_DIR = BASE_DIR / "weights"
DATASET_IMG = BASE_DIR / "dataset" / "images" / "train"
DATASET_LBL = BASE_DIR / "dataset" / "labels" / "train"


def _export_ml_samples(limit: int = 500) -> int:
    import django
    django.setup()

    from django.utils import timezone
    from scanning.models import MLTrainingSample

    if not DATASET_IMG.exists():
        DATASET_IMG.mkdir(parents=True, exist_ok=True)
    if not DATASET_LBL.exists():
        DATASET_LBL.mkdir(parents=True, exist_ok=True)

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
            dst_img = DATASET_IMG / f"{stem}.jpg"
            shutil.copy2(src, dst_img)

            bboxes = sample.bbox or []
            lines  = []
            for bb in bboxes:
                x      = bb.get("x", 0)
                y      = bb.get("y", 0)
                bw     = bb.get("width", 0)
                bh     = bb.get("height", 0)
                label  = f"0 {x} {y} {bw} {bh}"
                lines.append(label)

            with open(DATASET_LBL / f"{stem}.txt", "w") as lf:
                lf.write("\n".join(lines))

            sample.used_in_training = True
            sample.save(update_fields=["used_in_training"])
            exported += 1
        except Exception as exc:
            print(f"⚠️  Failed to export sample {sample.pk}: {exc}")

    print(f"📥 Exported {exported} ML samples into the YOLO dataset")
    return exported


def train(
    epochs: int = 100,
    batch: int = 16,
    imgsz: int = 640,
    model_size: str = "n",
    resume: bool = False,
    incremental: bool = False,
):
    import django
    django.setup()

    if incremental:
        exported = _export_ml_samples()
        print(f"🧩 Incremental mode: appended {exported} new samples to the dataset")

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

    if resume and (WEIGHTS_DIR / "last.pt").exists():
        print("🔄 Resuming from last checkpoint…")
        model = YOLO(str(WEIGHTS_DIR / "last.pt"))
    else:
        model = YOLO(f"yolov8{model_size}.pt")

    import yaml
    with open(DATA_YAML, "r") as f:
        data_cfg = yaml.safe_load(f)

    data_cfg["path"] = str(BASE_DIR / "dataset")
    with open(DATA_YAML, "w") as f:
        yaml.dump(data_cfg, f, sort_keys=False)

    results = model.train(
        data=str(DATA_YAML),
        epochs=epochs,
        imgsz=imgsz,
        batch=batch,
        name="plate_detector",
        project=str(BASE_DIR / "runs"),
        exist_ok=True,
        patience=20,
        save=True,
        plots=False,
        amp=False,
    )

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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train YOLOv8 plate detector")
    parser.add_argument("--epochs",     type=int,  default=100)
    parser.add_argument("--batch",      type=int,  default=16)
    parser.add_argument("--imgsz",      type=int,  default=640)
    parser.add_argument("--model-size", type=str,  default="n",
                        choices=["n", "s", "m", "l", "x"])
    parser.add_argument("--resume",     action="store_true")
    parser.add_argument("--incremental", action="store_true",
                        help="Append collected scan samples to the dataset before training")
    parser.add_argument("--download",   action="store_true",
                        help="Download Roboflow datasets before training")
    parser.add_argument("--api-key",    type=str,  default=None,
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
    )
