"""
scanning/ml/train.py — Train / fine-tune the unified single-class vehicle
detector (YOLOv8).

The model detects ONE class: "vehicle" (cars, motorcycles, buses, trucks,
jeepneys… all unified).  License plates are handled by the separate
plate_detector model and are NOT part of this dataset.

Dataset: a Roboflow "YOLOv8" export, either
  - unzipped into scanning/ml/vehicle_ds/            (default location), or
  - ingested from a zip with:  python train.py --zip path/to/roboflow.zip

Whatever class names the Roboflow dataset uses (car, motorcycle, bus, …),
labels are automatically remapped to the single "vehicle" class (id 0) via
class_mapping.CLASS_MAPPING before training.  Excluded/unknown labels are
dropped.

Training output goes to runs/vehicle_detector/weights/best.pt — the exact
path detection.py loads from, so a finished run is live on next server start.

Supports:
  --freeze : Freeze backbone layers and fine-tune the detection head only
             (recommended when fine-tuning an existing checkpoint)

Security-camera augmentation profile:
  - Lighting variations (brightness ±30%, contrast ±20%)
  - Modest geometric augmentation — NO heavy perspective warp
    (camera is fixed-mount, not handheld)
"""
from __future__ import annotations

import argparse
import shutil
import zipfile
from pathlib import Path

try:
    from .class_mapping import CLASS_MAPPING
except ImportError:  # run as a script: python scanning/ml/train.py
    from class_mapping import CLASS_MAPPING

BASE_DIR       = Path(__file__).resolve().parent
VEHICLE_DS_DIR = BASE_DIR / "vehicle_ds"
DATA_YAML      = VEHICLE_DS_DIR / "data.yaml"
RUNS_DIR       = BASE_DIR / "runs"
RUN_NAME       = "vehicle_detector"

# Splits as named by Roboflow exports ("valid") and plain YOLO layouts ("val")
_SPLIT_NAMES = ("train", "val", "valid", "test")


def ingest_zip(zip_path: Path, dest: Path = VEHICLE_DS_DIR) -> None:
    """Extract a Roboflow YOLOv8 export zip into the dataset directory."""
    if not zip_path.exists():
        raise SystemExit(f"❌ Zip not found: {zip_path}")
    if dest.exists():
        print(f"🧹 Removing previous dataset at {dest}")
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest)
    # Roboflow zips sometimes nest everything inside a single top-level folder
    entries = list(dest.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        inner = entries[0]
        for item in inner.iterdir():
            shutil.move(str(item), dest / item.name)
        inner.rmdir()
    print(f"📦 Extracted {zip_path.name} → {dest}")


def _label_dirs(dataset_dir: Path) -> list[Path]:
    """Find label directories for both layouts: labels/<split> and <split>/labels."""
    dirs = []
    for split in _SPLIT_NAMES:
        for cand in (dataset_dir / "labels" / split, dataset_dir / split / "labels"):
            if cand.exists():
                dirs.append(cand)
    return dirs


def _build_remap(dataset_dir: Path) -> dict[int, "int | None"]:
    """
    Build old-class-id → new-class-id mapping from the dataset's data.yaml.
    Every name whose canonical class is "vehicle" → 0; everything else
    (license_plate, excluded, unknown) → None (label row removed).
    """
    import yaml

    yaml_path = dataset_dir / "data.yaml"
    if not yaml_path.exists():
        raise SystemExit(f"❌ data.yaml not found in {dataset_dir} — is this a YOLOv8 export?")

    with open(yaml_path) as f:
        cfg = yaml.safe_load(f)

    names = cfg.get("names", [])
    if isinstance(names, dict):  # names can be {id: name} or [name, ...]
        names = [names[k] for k in sorted(names)]

    remap: dict[int, int | None] = {}
    kept, dropped = [], []
    for i, name in enumerate(names):
        canonical = CLASS_MAPPING.get(name) or CLASS_MAPPING.get(str(name).lower())
        if canonical == "vehicle":
            remap[i] = 0
            kept.append(name)
        else:
            remap[i] = None
            dropped.append(name)

    print(f"🗺️  Class remap → 'vehicle': {kept or 'none'}")
    if dropped:
        print(f"🗑️  Dropped classes: {dropped}")
    if not kept:
        raise SystemExit(
            "❌ No dataset class maps to 'vehicle'. "
            f"Dataset classes: {names}. Add aliases to class_mapping.CLASS_MAPPING."
        )
    return remap


def _remap_labels(dataset_dir: Path, remap: dict[int, "int | None"]) -> None:
    """
    Rewrite YOLO label files in-place using `remap`:
      - Old class ID → new class ID (or None = delete that label row)
    Idempotent — a second run maps 0 → 0 and changes nothing.
    """
    changed = 0
    for lbl_dir in _label_dirs(dataset_dir):
        for lbl_file in lbl_dir.glob("*.txt"):
            lines = lbl_file.read_text().strip().splitlines()
            new_lines = []
            file_changed = False
            for line in lines:
                parts = line.strip().split()
                if len(parts) != 5:
                    continue
                old_cls = int(parts[0])
                new_cls = remap.get(old_cls, None if old_cls != 0 else 0)
                if new_cls is None:
                    file_changed = True
                    continue  # remove this label
                if new_cls != old_cls:
                    file_changed = True
                new_lines.append(f"{new_cls} {' '.join(parts[1:])}")
            if file_changed:
                lbl_file.write_text("\n".join(new_lines))
                changed += 1
    print(f"🔁 Label remap: updated {changed} label files in {dataset_dir.name}")


def _validate_labels(dataset_dir: Path) -> None:
    """Check all YOLO label files for invalid bounding boxes and remove bad ones."""
    bad_files = []
    for lbl_dir in _label_dirs(dataset_dir):
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


def _rewrite_data_yaml(yaml_path: Path) -> None:
    """Force single-class schema and absolute dataset path in data.yaml."""
    import yaml

    with open(yaml_path) as f:
        cfg = yaml.safe_load(f)

    cfg["path"]  = str(yaml_path.parent)
    cfg["nc"]    = 1
    cfg["names"] = ["vehicle"]

    # Roboflow writes relative paths like "../train/images" — normalise them
    # so they resolve under the absolute `path` set above.
    for split_key in ("train", "val", "test"):
        val = cfg.get(split_key)
        if isinstance(val, str) and val.startswith("../"):
            cfg[split_key] = val[3:]

    with open(yaml_path, "w") as f:
        yaml.dump(cfg, f, sort_keys=False, default_flow_style=False)
    print(f"📝 data.yaml normalised: nc=1, names=['vehicle'], path={cfg['path']}")


def _count_images(dataset_dir: Path, split_prefixes: tuple[str, ...]) -> int:
    count = 0
    for split in split_prefixes:
        for cand in (dataset_dir / "images" / split, dataset_dir / split / "images"):
            if cand.exists():
                count += len(list(cand.glob("*")))
    return count


def train(
    epochs:        int  = 100,
    batch:         int  = 16,
    imgsz:         int  = 640,
    model_size:    str  = "s",
    resume:        bool = False,
    freeze:        bool = True,
    freeze_layers: int  = 10,
    data_yaml:     Path | None = None,
) -> None:
    """
    Train or fine-tune the single-class vehicle detector.

    Args:
        freeze:        If True, freeze the first `freeze_layers` backbone layers.
                       Recommended when fine-tuning from an existing checkpoint.
        freeze_layers: Number of backbone layers to freeze (YOLOv8n/s: 10 is safe).
        data_yaml:     Path to a custom data.yaml. Defaults to vehicle_ds/data.yaml.
    """
    from ultralytics import YOLO

    data_yaml   = Path(data_yaml).resolve() if data_yaml else DATA_YAML
    dataset_dir = data_yaml.parent

    if not data_yaml.exists():
        print("=" * 60)
        print("  ERROR: No dataset found!")
        print(f"  Expected data.yaml at: {data_yaml}")
        print("  Drop your Roboflow YOLOv8 export into vehicle_ds/,")
        print("  or run:  python train.py --zip path/to/roboflow.zip")
        print("=" * 60)
        return

    # Remap all vehicle-ish classes → single class 0 (idempotent)
    _remap_labels(dataset_dir, _build_remap(dataset_dir))
    _rewrite_data_yaml(data_yaml)
    _validate_labels(dataset_dir)

    train_count = _count_images(dataset_dir, ("train",))
    val_count   = _count_images(dataset_dir, ("val", "valid"))
    if train_count == 0:
        print("=" * 60)
        print("  ERROR: No training images found!")
        print(f"  Expected images under: {dataset_dir}")
        print("=" * 60)
        return
    print(f"📦 Dataset:  {train_count} training images, {val_count} validation images")

    # Select base model
    best_ckpt = RUNS_DIR / RUN_NAME / "weights" / "best.pt"
    last_ckpt = RUNS_DIR / RUN_NAME / "weights" / "last.pt"

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

    print(f"🧠 Model:    YOLOv8{model_size}  |  {epochs} epochs  |  batch {batch}  |  imgsz {imgsz}")
    if freeze:
        print(f"🧊 Freezing first {freeze_layers} backbone layers")
    print()

    # ── Security-camera augmentation profile ──────────────────────────────
    # Fixed-mount overhead/angled camera — avoid heavy geometric distortion.
    # Focus on blur, lighting, and mild colour shifts.
    augment_kwargs = {
        # Colour/exposure variations — lighting changes throughout day
        "hsv_h":       0.015,    # hue shift ±1.5 %
        "hsv_s":       0.4,      # saturation ±40 %
        "hsv_v":       0.3,      # value (brightness) ±30 %
        # Geometric — mild only for fixed-mount camera
        "degrees":     5.0,      # rotation ±5° (slight tilt)
        "translate":   0.1,      # translation ±10%
        "scale":       0.3,      # scale ±30%
        "shear":       0.0,      # no shear (fixed vantage)
        "perspective": 0.0,      # no perspective warp (fixed mount)
        "flipud":      0.0,      # no vertical flip (cameras don't invert)
        "fliplr":      0.5,      # horizontal flip (vehicles go both ways)
        "mosaic":      1.0,      # mosaic mixes small/distant vehicles more often
    }

    train_kwargs = dict(
        data=str(data_yaml),
        epochs=epochs,
        imgsz=imgsz,
        batch=batch,
        workers=2,
        name=RUN_NAME,
        project=str(RUNS_DIR),
        exist_ok=True,
        patience=25,
        save=True,
        plots=True,
        amp=True,
        cache=False,
        optimizer="Adam",        # Muon optimizer uses BF16 cuBLAS which fails on older GPUs
        **augment_kwargs,
    )

    if freeze and freeze_layers > 0:
        train_kwargs["freeze"] = freeze_layers

    results = model.train(**train_kwargs)

    # project/name puts weights directly at runs/vehicle_detector/weights/best.pt —
    # the canonical path detection.py loads from.  No copy step needed.
    print("🎉 Training complete!")
    print(f"   View results in: {results.save_dir}")
    print(f"   Live weights:    {best_ckpt}")
    _print_eval(results)


def _print_eval(results) -> None:
    """Print mAP@0.5 from the final validation run."""
    try:
        maps = getattr(results, "maps", None)
        if maps is None or not len(maps):
            return
        val = float(maps[0])
        bar = "▓" * int(val * 20) + "░" * (20 - int(val * 20))
        print("\n📊 mAP@0.5 (final validation):")
        print(f"   {'vehicle':<20} {bar}  {val:.3f}")
    except Exception:
        pass  # eval printing is best-effort


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train the single-class vehicle detector (YOLOv8)")
    parser.add_argument("--zip",           type=str,   default=None,
                        help="Roboflow YOLOv8 export zip — extracts into vehicle_ds/ before training")
    parser.add_argument("--data",          type=str,   default=None,
                        help="Path to data.yaml (default: vehicle_ds/data.yaml)")
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

    args = parser.parse_args()

    if args.zip:
        ingest_zip(Path(args.zip))

    train(
        epochs=args.epochs,
        batch=args.batch,
        imgsz=args.imgsz,
        model_size=args.model_size,
        resume=args.resume,
        freeze=args.freeze,
        freeze_layers=args.freeze_layers,
        data_yaml=Path(args.data) if args.data else None,
    )
