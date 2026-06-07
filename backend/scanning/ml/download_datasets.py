"""
──────────────────────────────────────────────────────────────────────
  download_datasets.py — Download & merge Roboflow license plate datasets
──────────────────────────────────────────────────────────────────────

Downloads two Philippine license plate datasets from Roboflow Universe,
merges them into the local dataset/ directory in YOLOv8 format, and
normalises class IDs so everything uses class 0 = license_plate.

Usage:
    cd backend
    python -m scanning.ml.download_datasets --api-key YOUR_ROBOFLOW_API_KEY

Datasets:
    1. philippine-license-plates / local_lpr-117y7
    2. university-of-southeastern-philippines-cnl9c / philippine-license-plates
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import tempfile
from pathlib import Path

# ── Paths ───────────────────────────────────────────────────────────

BASE_DIR    = Path(__file__).resolve().parent          # scanning/ml/
DATASET_DIR = BASE_DIR / "dataset"
IMAGES_DIR  = DATASET_DIR / "images"
LABELS_DIR  = DATASET_DIR / "labels"

# ── Roboflow dataset definitions ────────────────────────────────────

DATASETS = [
    {
        "name": "Dataset 1 — Local LPR",
        "prefix": "ds1",
        "workspace": "philippine-license-plates",
        "project": "local_lpr-117y7",
        "version": None,          # None → latest version
    },
    {
        "name": "Dataset 2 — USeP Philippine License Plates",
        "prefix": "ds2",
        "workspace": "university-of-southeastern-philippines-cnl9c",
        "project": "philippine-license-plates",
        "version": None,
    },
]


def _ensure_dirs():
    """Create train/val directories if they don't exist."""
    for split in ("train", "val"):
        (IMAGES_DIR / split).mkdir(parents=True, exist_ok=True)
        (LABELS_DIR / split).mkdir(parents=True, exist_ok=True)


def _normalise_label(label_path: Path):
    """
    Rewrite a YOLO label file so every line uses class_id = 0.
    This handles datasets that may use different class IDs or have
    multiple classes — we collapse everything to a single
    'license_plate' class (0).
    """
    lines = label_path.read_text(encoding="utf-8").strip().splitlines()
    normalised = []
    for line in lines:
        parts = line.strip().split()
        if len(parts) >= 5:
            # Replace class_id with 0, keep the rest (x_center y_center w h)
            parts[0] = "0"
            normalised.append(" ".join(parts))
    label_path.write_text("\n".join(normalised) + "\n", encoding="utf-8")


def _copy_split(
    src_images: Path,
    src_labels: Path,
    dst_images: Path,
    dst_labels: Path,
    prefix: str,
) -> int:
    """
    Copy images + labels from a downloaded split into the merged
    dataset directory, prefixing filenames to avoid collisions.

    Returns the number of image files copied.
    """
    count = 0
    image_exts = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

    if not src_images.exists():
        return 0

    for img_file in sorted(src_images.iterdir()):
        if img_file.suffix.lower() not in image_exts:
            continue

        # Build prefixed destination name
        new_name = f"{prefix}_{img_file.name}"
        dst_img = dst_images / new_name
        shutil.copy2(img_file, dst_img)

        # Copy matching label file (same stem, .txt extension)
        label_file = src_labels / f"{img_file.stem}.txt"
        if label_file.exists():
            dst_lbl = dst_labels / f"{prefix}_{img_file.stem}.txt"
            shutil.copy2(label_file, dst_lbl)
            _normalise_label(dst_lbl)

        count += 1

    return count


def _find_split_dirs(download_root: Path):
    """
    Locate images/ and labels/ directories inside a Roboflow download.
    Roboflow downloads often nest things like:
        <project>-<version>/train/images/
        <project>-<version>/valid/images/
    or
        <project>-<version>/images/train/
        <project>-<version>/labels/train/

    This function detects the actual layout.
    """

    # Common Roboflow YOLOv8 layouts:
    #   Layout A: root/train/images/  root/train/labels/
    #   Layout B: root/images/train/  root/labels/train/
    #   Layout C: root/<subdir>/train/images/  (nested one more level)

    # Try to find the actual root (might be nested in a subdirectory)
    candidates = [download_root]
    for child in download_root.iterdir():
        if child.is_dir() and child.name not in {"__pycache__", ".git"}:
            candidates.append(child)

    for root in candidates:
        # Layout A: root/train/images/ + root/train/labels/
        train_imgs_a = root / "train" / "images"
        val_imgs_a_1 = root / "valid" / "images"
        val_imgs_a_2 = root / "val" / "images"

        if train_imgs_a.exists():
            val_imgs_a = val_imgs_a_1 if val_imgs_a_1.exists() else val_imgs_a_2
            return {
                "train": {
                    "images": train_imgs_a,
                    "labels": root / "train" / "labels",
                },
                "val": {
                    "images": val_imgs_a if val_imgs_a.exists() else None,
                    "labels": root / ("valid" if val_imgs_a_1.exists() else "val") / "labels",
                },
            }

        # Layout B: root/images/train/ + root/labels/train/
        imgs_train_b = root / "images" / "train"
        if imgs_train_b.exists():
            val_name = "valid" if (root / "images" / "valid").exists() else "val"
            return {
                "train": {
                    "images": imgs_train_b,
                    "labels": root / "labels" / "train",
                },
                "val": {
                    "images": root / "images" / val_name,
                    "labels": root / "labels" / val_name,
                },
            }

    # Fallback: couldn't detect layout
    return None


def download_and_merge(api_key: str, version_overrides: dict[str, int] | None = None):
    """
    Download each Roboflow dataset into a temp directory, then merge
    the images/labels into the project's dataset/ directory.

    Args:
        api_key:            Your Roboflow API key.
        version_overrides:  Optional dict mapping project name to version number.
    """
    try:
        from roboflow import Roboflow
    except ImportError:
        print("=" * 60)
        print("  ERROR: The 'roboflow' package is not installed.")
        print("  Install it with:  pip install roboflow")
        print("=" * 60)
        return

    version_overrides = version_overrides or {}

    _ensure_dirs()

    rf = Roboflow(api_key=api_key)

    total_train = 0
    total_val = 0

    for ds in DATASETS:
        print()
        print("=" * 60)
        print(f"  [DOWNLOAD] Downloading: {ds['name']}")
        print(f"     Workspace:  {ds['workspace']}")
        print(f"     Project:    {ds['project']}")
        print("=" * 60)

        try:
            workspace = rf.workspace(ds["workspace"])
            project = workspace.project(ds["project"])

            # Determine version
            ver_num = version_overrides.get(ds["project"], ds["version"])
            if ver_num is None:
                # Get the latest version
                versions = project.versions()
                if not versions:
                    print(f"  [WARN] No versions found for {ds['project']}. Skipping.")
                    continue
                # versions is a list; the last one is typically the latest
                version = versions[-1]
                ver_num = version.version
                print(f"  [INFO] Using latest version: {ver_num}")
            else:
                version = project.version(ver_num)
                print(f"  [INFO] Using version: {ver_num}")

            print(f"  [DIR] Downloading...")
            dataset = version.download("yolov8")
            download_root = Path(dataset.location)
            
            print(f"  [DIR] Download location: {download_root}")

            # Detect directory layout
            splits = _find_split_dirs(download_root)
            if splits is None:
                print(f"  [WARN] Could not detect dataset layout in {download_root}")
                print(f"       Contents: {[p.name for p in download_root.iterdir()]}")
                print("       Skipping this dataset.")
                shutil.rmtree(download_root, ignore_errors=True)
                continue

            # Copy train split
            if splits["train"]["images"] and splits["train"]["images"].exists():
                n_train = _copy_split(
                    src_images=splits["train"]["images"],
                    src_labels=splits["train"]["labels"],
                    dst_images=IMAGES_DIR / "train",
                    dst_labels=LABELS_DIR / "train",
                    prefix=ds["prefix"],
                )
                total_train += n_train
                print(f"  [OK] Copied {n_train} training images")
            else:
                print("  [WARN] No training images found")

            # Copy val split
            if splits["val"]["images"] and splits["val"]["images"].exists():
                n_val = _copy_split(
                    src_images=splits["val"]["images"],
                    src_labels=splits["val"]["labels"],
                    dst_images=IMAGES_DIR / "val",
                    dst_labels=LABELS_DIR / "val",
                    prefix=ds["prefix"],
                )
                total_val += n_val
                print(f"  [OK] Copied {n_val} validation images")
            else:
                print("  [WARN] No validation images found")

            # Clean up temp directory
            shutil.rmtree(download_root, ignore_errors=True)
            print(f"  [CLEAN] Cleaned up downloaded files")

        except Exception as e:
            print(f"  [ERROR] Error downloading {ds['name']}: {e}")
            print(f"     Skipping this dataset.")
            continue

    # ── Summary ─────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print(f"  MERGE COMPLETE")
    print(f"     Training images:   {total_train}")
    print(f"     Validation images: {total_val}")
    print(f"     Total:             {total_train + total_val}")
    print()
    print(f"  Dataset location:  {DATASET_DIR}")
    print()
    if total_train > 0:
        print("  Ready to train! Run:")
        print("     python -m scanning.ml.train")
    else:
        print("  [WARN] No images were downloaded. Check your API key and")
        print("     ensure you have access to the datasets on Roboflow.")
    print("=" * 60)


# ── CLI ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Download Roboflow Philippine license plate datasets"
    )
    parser.add_argument(
        "--api-key",
        type=str,
        required=True,
        help="Your Roboflow API key (from Settings → API Key)",
    )
    parser.add_argument(
        "--ds1-version",
        type=int,
        default=None,
        help="Version number for Dataset 1 (local_lpr). Default: latest",
    )
    parser.add_argument(
        "--ds2-version",
        type=int,
        default=None,
        help="Version number for Dataset 2 (USeP). Default: latest",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove existing dataset images/labels before downloading",
    )

    args = parser.parse_args()

    # Optionally clean existing data
    if args.clean:
        print("[CLEAN] Cleaning existing dataset...")
        for split in ("train", "val"):
            img_dir = IMAGES_DIR / split
            lbl_dir = LABELS_DIR / split
            if img_dir.exists():
                shutil.rmtree(img_dir)
            if lbl_dir.exists():
                shutil.rmtree(lbl_dir)
        print("   Done.")

    # Build version overrides
    overrides = {}
    if args.ds1_version:
        overrides["local_lpr-117y7"] = args.ds1_version
    if args.ds2_version:
        overrides["philippine-license-plates"] = args.ds2_version

    download_and_merge(api_key=args.api_key, version_overrides=overrides)
