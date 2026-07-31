"""
scanning/ml/train.py — Train / fine-tune the unified single-class vehicle
detector (YOLO26, size m by default).

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
import os
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


def _setup_device(gpu_mem_gb: float) -> tuple[str, "float | None"]:
    """
    Pick the training device.  On CUDA, hard-cap this process's VRAM usage at
    `gpu_mem_gb` (allocations beyond it raise OOM instead of eating the whole
    card) and return an auto-batch fraction targeting ~90% of the cap, so
    Ultralytics computes the largest batch size that fits inside it.

    Returns (device, batch_fraction) — batch_fraction is None on CPU.
    """
    # Reduces fragmentation so the capped allocator is used efficiently.
    # Must be set before the first CUDA allocation.
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    try:
        import torch
        if torch.cuda.is_available():
            props = torch.cuda.get_device_properties(0)
            total_gb = props.total_memory / 1024**3
            frac = min(1.0, gpu_mem_gb / total_gb)
            torch.cuda.set_per_process_memory_fraction(frac, 0)
            # TF32 tensor cores — free speedup on Ampere+ (RTX 3060)
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True
            torch.backends.cudnn.benchmark = True
            print(f"⚡ GPU: {props.name} ({total_gb:.1f} GB) — hard-capped at "
                  f"{gpu_mem_gb:.1f} GB ({frac:.0%} of VRAM)")
            return "0", round(frac * 0.9, 2)
    except Exception as exc:
        print(f"⚠️  GPU setup failed ({exc}) — falling back to CPU")
    print("🐢 No CUDA GPU available — training on CPU (slow for size m)")
    return "cpu", None


def train(
    epochs:        int  = 100,
    batch:         "int | float | None" = None,
    imgsz:         int  = 640,
    model_size:    str  = "m",
    resume:        bool = False,
    freeze:        bool = True,
    freeze_layers: int  = 10,
    data_yaml:     Path | None = None,
    gpu_mem_gb:    float = 3.0,
    lr0:           float = 0.001,
) -> None:
    """
    Train or fine-tune the single-class vehicle detector.

    Args:
        batch:         Explicit batch size. Default None = auto: on GPU the
                       batch is sized to fit the gpu_mem_gb cap; on CPU it's 8.
        freeze:        If True, freeze the first `freeze_layers` backbone layers.
                       Recommended when fine-tuning from an existing checkpoint.
        freeze_layers: Number of backbone layers to freeze (10 covers the
                       YOLO26 backbone stages, same as YOLOv8).
        data_yaml:     Path to a custom data.yaml. Defaults to vehicle_ds/data.yaml.
        gpu_mem_gb:    Hard VRAM budget for this training process.
        lr0:           Initial learning rate. Default 0.001 because we force the
                       Adam optimizer below — YOLO's stock 0.01 is an SGD-scale
                       rate and is ~10x too high for Adam. Training at 0.01 blows
                       the EMA up to NaN/Inf, which makes Ultralytics skip the
                       checkpoint save ("EMA contains NaN/Inf") and silently
                       leaves best.pt stuck at an early epoch.
    """
    from ultralytics import YOLO

    device, batch_frac = _setup_device(gpu_mem_gb)
    if batch is None:
        # Float in (0,1) → Ultralytics auto-batch targeting that VRAM fraction
        batch = batch_frac if batch_frac is not None else 8

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
        print(f"🧠 Starting from pretrained YOLO26{model_size} (no existing checkpoint)")
        model = YOLO(f"yolo26{model_size}.pt")
        freeze = False  # Nothing to freeze from scratch

    batch_desc = (f"auto (fits {gpu_mem_gb:.1f} GB VRAM cap)"
                  if isinstance(batch, float) and batch < 1 else str(batch))
    print(f"🧠 Model:    YOLO26{model_size}  |  {epochs} epochs  |  batch {batch_desc}  |  imgsz {imgsz}")
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
        device=device,
        workers=4 if device != "cpu" else 2,
        name=RUN_NAME,
        project=str(RUNS_DIR),
        exist_ok=True,
        patience=25,
        save=True,
        plots=True,
        amp=True,                # FP16 tensor cores on the RTX 3060 — ~2× speed, ~half VRAM
        cache=False,
        optimizer="Adam",        # YOLO26's default MuSGD/Muon uses BF16 cuBLAS which fails on older GPUs
        lr0=lr0,                 # must be Adam-scale (~1e-3); 0.01 diverges the EMA to NaN
        # ── Adam-safe warmup ──
        # Ultralytics' stock warmup_bias_lr=0.1 is tuned for SGD. It is applied
        # to the bias param group for the first `warmup_epochs` (3) and with
        # Adam that is ~100x too hot: the biases blow up during warmup, the EMA
        # goes NaN around epoch 3, every later checkpoint save is skipped, and
        # training eventually dies with a CUDA illegal memory access.
        # Ultralytics zeroes this itself, but ONLY under optimizer="auto"
        # (trainer.py: `self.args.warmup_bias_lr = 0.0  # no higher than 0.01
        # for Adam`) — forcing Adam by name skips that safety, so we set it here.
        warmup_bias_lr=0.0,
        momentum=0.9,            # becomes Adam beta1; 0.937 is the SGD-tuned default
        **augment_kwargs,
    )

    if freeze and freeze_layers > 0:
        train_kwargs["freeze"] = freeze_layers

    _attach_divergence_guard(model)

    results = model.train(**train_kwargs)

    # project/name puts weights directly at runs/vehicle_detector/weights/best.pt —
    # the canonical path detection.py loads from.  No copy step needed.
    print("🎉 Training complete!")
    print(f"   View results in: {results.save_dir}")
    print(f"   Live weights:    {best_ckpt}")
    _print_eval(results)


def _attach_divergence_guard(model) -> None:
    """
    Stop training the moment the weights go non-finite.

    Without this, a divergence is nearly invisible: Ultralytics only warns
    "EMA contains NaN/Inf" and skips the checkpoint save, then keeps training
    for many more epochs on corrupt weights before CUDA finally dies with an
    illegal memory access — a traceback that points at conv2d and says nothing
    about the real cause. Failing at the first bad epoch keeps the diagnosis
    attached to the symptom.
    """
    import torch

    def _check(trainer):
        ema = getattr(getattr(trainer, "ema", None), "ema", None)
        target, label = (ema, "EMA") if ema is not None else (trainer.model, "model")
        bad = [n for n, p in target.named_parameters()
               if p.dtype.is_floating_point and not torch.isfinite(p).all()]
        if not bad:
            return
        # Plain ASCII on purpose: SystemExit text is printed by the interpreter
        # to stderr, which is cp1252 on a stock Windows console — an emoji here
        # would raise UnicodeEncodeError and swallow the diagnosis.
        raise SystemExit(
            f"\nTraining diverged at epoch {trainer.epoch}: "
            f"{len(bad)} {label} tensors contain NaN/Inf (first: {bad[0]}).\n"
            f"   Checkpoints stop updating from here, so best.pt would silently\n"
            f"   stay at an earlier epoch. Lower --lr0 (currently "
            f"{trainer.args.lr0}) and re-run; if it persists, retry with amp=False."
        )

    model.add_callback("on_fit_epoch_end", _check)


def _print_eval(results) -> None:
    """
    Print the final validation metrics.

    NOTE: `results.maps` is mAP50-95 per class, NOT mAP@0.5 — reporting it as
    mAP@0.5 understates the model badly (e.g. 0.463 vs the true 0.782).
    Read mAP@0.5 from `results.box.map50` instead.
    """
    try:
        box = getattr(results, "box", None)
        if box is None:
            return

        def _bar(v: float) -> str:
            filled = int(v * 20)
            return "▓" * filled + "░" * (20 - filled)

        print("\n📊 Final validation:")
        for label, val in (
            ("mAP@0.5",      float(box.map50)),
            ("mAP@0.5:0.95", float(box.map)),
            ("precision",    float(box.mp)),
            ("recall",       float(box.mr)),
        ):
            print(f"   {label:<14} {_bar(val)}  {val:.3f}")
    except Exception:
        pass  # eval printing is best-effort


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train the single-class vehicle detector (YOLO26)")
    parser.add_argument("--zip",           type=str,   default=None,
                        help="Roboflow YOLOv8 export zip — extracts into vehicle_ds/ before training")
    parser.add_argument("--data",          type=str,   default=None,
                        help="Path to data.yaml (default: vehicle_ds/data.yaml)")
    parser.add_argument("--epochs",        type=int,   default=100)
    parser.add_argument("--batch",         type=float, default=None,
                        help="Batch size (default: auto-sized to fit --gpu-mem-gb on GPU, 8 on CPU)")
    parser.add_argument("--gpu-mem-gb",    type=float, default=3.0,
                        help="Hard VRAM budget for training (default: 3.0 GB)")
    parser.add_argument("--lr0",           type=float, default=0.001,
                        help="Initial learning rate (default: 0.001, correct for the "
                             "Adam optimizer this script forces; 0.01 diverges to NaN)")
    parser.add_argument("--imgsz",         type=int,   default=640)
    parser.add_argument("--model-size",    type=str,   default="m",
                        choices=["n", "s", "m", "l", "x"],
                        help="YOLO26 size (default: m)")
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

    # --batch accepts floats for CLI parsing; whole numbers are real batch sizes
    batch = args.batch
    if batch is not None and batch >= 1:
        batch = int(batch)

    train(
        epochs=args.epochs,
        batch=batch,
        imgsz=args.imgsz,
        model_size=args.model_size,
        resume=args.resume,
        freeze=args.freeze,
        freeze_layers=args.freeze_layers,
        data_yaml=Path(args.data) if args.data else None,
        gpu_mem_gb=args.gpu_mem_gb,
        lr0=args.lr0,
    )
