"""
download_datasets.py — Merge extra datasets into the single-class vehicle dataset.

Downloads Roboflow datasets (YOLOv8 format) and/or merges local YOLOv8
dataset folders into vehicle_ds/.  Every label whose class maps to a
vehicle in class_mapping.CLASS_MAPPING (car, motorcycle, bus, truck…) is
rewritten to the single class 0 = "vehicle".  License-plate and excluded
labels are dropped — plates belong to the separate plate_detector dataset,
which this script never touches.

If vehicle_ds/ already holds a multi-class Roboflow export, it is
normalised to the single-class schema first (via train.py's remap) so the
merged labels line up.

NOTE: run `train.py --zip <export.zip>` BEFORE merging extras — ingest_zip
wipes vehicle_ds/ and would delete anything merged earlier.

Usage (from backend/):
    python -m scanning.ml.download_datasets --api-key <KEY> \
        --dataset workspace/project/1 [--dataset ws2/proj2/3 ...]
    python -m scanning.ml.download_datasets --local path/to/yolo_dataset
    (both flags can be combined; add --dry-run to preview)
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from collections import defaultdict

try:
    from .class_mapping import get_standard_class_name, is_excluded_label
    from .train import VEHICLE_DS_DIR, _build_remap, _remap_labels, _rewrite_data_yaml
except ImportError:  # run as a script: python scanning/ml/download_datasets.py
    from class_mapping import get_standard_class_name, is_excluded_label
    from train import VEHICLE_DS_DIR, _build_remap, _remap_labels, _rewrite_data_yaml

BASE_DIR = Path(__file__).resolve().parent

VEHICLE_CLASS_ID = 0  # the one and only class


def _download_rf(api_key: str, workspace: str, project: str, version: int, dest: Path) -> Path:
    """Download a single Roboflow dataset in YOLOv8 format."""
    try:
        from roboflow import Roboflow
    except ImportError:
        print("[ERROR] roboflow package not installed. Run: pip install roboflow")
        sys.exit(1)

    rf = Roboflow(api_key=api_key)
    proj = rf.workspace(workspace).project(project)
    dataset = proj.version(version).download("yolov8", location=str(dest))
    return Path(dataset.location)


def _parse_raw_class_names(rf_root: Path) -> list[str]:
    """Read class names from data.yaml inside a dataset folder."""
    import yaml
    for candidate in [rf_root / "data.yaml", rf_root / "dataset.yaml"]:
        if candidate.exists():
            with open(candidate) as f:
                cfg = yaml.safe_load(f)
            names = cfg.get("names", [])
            if isinstance(names, dict):
                # YOLO sometimes writes {0: "cls", 1: "cls2"}
                return [names[i] for i in sorted(names.keys())]
            return list(names)
    return []


def _remap_label_file(
    label_path: Path,
    raw_class_names: list[str],
    ambiguity_log: dict[str, list[str]],
    dropped_counts: dict[str, int],
    dest_path: Path,
    dry_run: bool,
) -> bool:
    """
    Remap one YOLO label file: vehicle-ish classes → 0, everything else dropped.

    Returns True if at least one vehicle annotation was written.
    """
    lines_out: list[str] = []
    with open(label_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 5:
                continue
            raw_id = int(parts[0])
            if raw_id >= len(raw_class_names):
                continue
            raw_label = raw_class_names[raw_id]

            canonical = (get_standard_class_name(raw_label)
                         or get_standard_class_name(str(raw_label).lower()))
            if canonical == "vehicle":
                lines_out.append(f"{VEHICLE_CLASS_ID} {' '.join(parts[1:])}")
                continue

            dropped_counts[raw_label] += 1
            if canonical is None and not is_excluded_label(raw_label):
                # Unknown label — log for review (plates/excluded drop silently)
                ambiguity_log[raw_label].append(str(label_path))

    if not lines_out:
        return False

    if not dry_run:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "w") as f:
            f.write("\n".join(lines_out) + "\n")
    return True


def _merge_split(
    src_root: Path,
    split: str,
    dest_split: str,
    raw_class_names: list[str],
    ambiguity_log: dict[str, list[str]],
    dropped_counts: dict[str, int],
    dry_run: bool,
    prefix: str,
) -> tuple[int, int]:
    """Merge one split from a source dataset into vehicle_ds/<dest_split>/."""
    src_img = src_root / split / "images"
    src_lbl = src_root / split / "labels"

    if not src_img.exists():
        # Some datasets use the labels/<split> layout or a flat structure
        src_img = src_root / "images" / split
        src_lbl = src_root / "labels" / split
    if not src_img.exists() and split == "train":
        src_img = src_root / "images"
        src_lbl = src_root / "labels"
    if not src_img.exists():
        return 0, 0

    dest_img_dir = VEHICLE_DS_DIR / dest_split / "images"
    dest_lbl_dir = VEHICLE_DS_DIR / dest_split / "labels"

    copied = 0
    skipped = 0
    for img_path in src_img.glob("*.*"):
        if img_path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}:
            continue
        lbl_path = src_lbl / (img_path.stem + ".txt")
        if not lbl_path.exists():
            skipped += 1
            continue

        dest_stem = f"{prefix}_{img_path.stem}"
        ok = _remap_label_file(
            lbl_path, raw_class_names, ambiguity_log, dropped_counts,
            dest_lbl_dir / (dest_stem + ".txt"), dry_run,
        )
        if ok:
            if not dry_run:
                dest_img_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(img_path, dest_img_dir / (dest_stem + img_path.suffix))
            copied += 1
        else:
            skipped += 1

    return copied, skipped


def _merge_dataset(
    src_root: Path,
    prefix: str,
    ambiguity_log: dict[str, list[str]],
    dropped_counts: dict[str, int],
    dry_run: bool,
) -> tuple[int, int]:
    """Merge a whole YOLOv8 dataset folder into vehicle_ds (all splits)."""
    raw_names = _parse_raw_class_names(src_root)
    if not raw_names:
        print("     [WARN] Could not parse class names from data.yaml -- skipping")
        return 0, 0
    print(f"     Raw classes: {raw_names}")

    total_copied = total_skipped = 0
    for split, dest_split in [
        ("train", "train"),
        ("valid", "valid"),
        ("val",   "valid"),
        ("test",  "valid"),
    ]:
        copied, skipped = _merge_split(
            src_root, split, dest_split, raw_names,
            ambiguity_log, dropped_counts, dry_run, prefix,
        )
        if copied or skipped:
            print(f"     [{split}] OK: {copied} merged, {skipped} skipped")
            total_copied += copied
            total_skipped += skipped
    return total_copied, total_skipped


def _normalise_existing_dataset(dry_run: bool) -> None:
    """
    If vehicle_ds already holds a (possibly multi-class) export, remap it to
    the single-class schema first so merged class-0 labels line up.
    Creates a fresh single-class data.yaml when the dataset doesn't exist yet.
    """
    import yaml

    data_yaml = VEHICLE_DS_DIR / "data.yaml"
    if data_yaml.exists():
        if dry_run:
            print(f"[DRY RUN] Would normalise existing dataset at {VEHICLE_DS_DIR}")
            return
        _remap_labels(VEHICLE_DS_DIR, _build_remap(VEHICLE_DS_DIR))
        _rewrite_data_yaml(data_yaml)
        return

    if dry_run:
        print(f"[DRY RUN] Would create fresh dataset at {VEHICLE_DS_DIR}")
        return
    VEHICLE_DS_DIR.mkdir(parents=True, exist_ok=True)
    cfg = {
        "path":  str(VEHICLE_DS_DIR),
        "train": "train/images",
        "val":   "valid/images",
        "nc":    1,
        "names": ["vehicle"],
    }
    with open(data_yaml, "w") as f:
        yaml.dump(cfg, f, sort_keys=False, default_flow_style=False)
    print(f"[OK] Fresh single-class data.yaml written -> {data_yaml}")


def _print_report(ambiguity_log: dict[str, list[str]], dropped_counts: dict[str, int]):
    if dropped_counts:
        print("\nDropped annotations by raw label (not vehicles):")
        for label, count in sorted(dropped_counts.items(), key=lambda x: -x[1]):
            print(f"  {label}: {count}")

    if not ambiguity_log:
        print("\n[OK] No ambiguous labels -- all raw labels mapped or deliberately dropped.")
        return

    print("\n" + "=" * 60)
    print("  !! AMBIGUITY REPORT -- Labels requiring review")
    print("  These labels were NOT in CLASS_MAPPING and were SKIPPED.")
    print("  Add them to class_mapping.py if they should count as vehicles.")
    print("=" * 60)
    for label, sources in sorted(ambiguity_log.items()):
        print(f"\n  Raw label: '{label}'  ({len(sources)} annotation file(s))")
        for src in sources[:3]:
            print(f"    - {src}")
        if len(sources) > 3:
            print(f"    ... and {len(sources) - 3} more")
    print()


def download_and_merge(
    api_key: str | None = None,
    datasets: list[str] | None = None,
    local_dirs: list[str] | None = None,
    dry_run: bool = False,
    tmp_dir: Path | None = None,
) -> None:
    """
    Main entry point: download the requested Roboflow datasets and/or take
    local dataset folders, remap every vehicle-ish label to class 0, and
    merge into vehicle_ds/.
    """
    datasets   = datasets or []
    local_dirs = local_dirs or []
    if not datasets and not local_dirs:
        print("[ERROR] Nothing to merge — pass --dataset workspace/project/version and/or --local <dir>")
        sys.exit(1)
    if datasets and not api_key:
        print("[ERROR] --api-key is required to download Roboflow datasets")
        sys.exit(1)

    if tmp_dir is None:
        tmp_dir = BASE_DIR.parent.parent / "dl_temp"
    if datasets and not dry_run:
        tmp_dir.mkdir(parents=True, exist_ok=True)

    _normalise_existing_dataset(dry_run)

    ambiguity_log: dict[str, list[str]] = defaultdict(list)
    dropped_counts: dict[str, int] = defaultdict(int)
    total_copied = 0
    total_skipped = 0

    print("\n" + "=" * 60)
    print(f"  Merging {len(datasets)} Roboflow + {len(local_dirs)} local dataset(s) -> vehicle_ds")
    if dry_run:
        print("  [DRY RUN] -- no files will be written")
    print("=" * 60 + "\n")

    for spec in datasets:
        try:
            workspace, project, version = spec.split("/")
            version = int(version)
        except ValueError:
            print(f"[SKIP] Bad --dataset spec '{spec}' — expected workspace/project/version")
            continue

        print(f"[DL] {workspace}/{project} v{version}")
        rf_dest = tmp_dir / f"{workspace}__{project}__v{version}"

        if rf_dest.exists() and any(rf_dest.iterdir()):
            print(f"     -> Already downloaded at {rf_dest}")
        else:
            try:
                _download_rf(api_key, workspace, project, version, rf_dest)
            except Exception as exc:
                print(f"     [FAILED] Download failed: {exc}")
                continue

        prefix = f"{workspace}__{project}__v{version}"
        copied, skipped = _merge_dataset(rf_dest, prefix, ambiguity_log,
                                         dropped_counts, dry_run)
        total_copied += copied
        total_skipped += skipped

    for local in local_dirs:
        src_root = Path(local).resolve()
        print(f"[LOCAL] {src_root}")
        if not src_root.exists():
            print("     [FAILED] Directory not found")
            continue
        copied, skipped = _merge_dataset(src_root, src_root.name, ambiguity_log,
                                         dropped_counts, dry_run)
        total_copied += copied
        total_skipped += skipped

    print("\n" + "=" * 60)
    print("  DONE")
    print(f"     Total new images merged:   {total_copied}")
    print(f"     Total images skipped:      {total_skipped} (no vehicle annotations)")
    print(f"     Destination: {VEHICLE_DS_DIR}")
    print("=" * 60)

    _print_report(ambiguity_log, dropped_counts)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Merge extra Roboflow/local datasets into vehicle_ds as single-class 'vehicle'"
    )
    parser.add_argument("--api-key",  default=None, help="Roboflow API key (needed with --dataset)")
    parser.add_argument("--dataset",  action="append", default=[],
                        help="Roboflow dataset as workspace/project/version (repeatable)")
    parser.add_argument("--local",    action="append", default=[],
                        help="Local YOLOv8 dataset folder to merge (repeatable)")
    parser.add_argument("--dry-run",  action="store_true",
                        help="Show what would happen without writing files")
    parser.add_argument("--tmp-dir",  type=str, default=None,
                        help="Directory for raw Roboflow downloads")
    args = parser.parse_args()

    download_and_merge(
        api_key=args.api_key,
        datasets=args.dataset,
        local_dirs=args.local,
        dry_run=args.dry_run,
        tmp_dir=Path(args.tmp_dir) if args.tmp_dir else None,
    )
