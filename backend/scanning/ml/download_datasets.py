"""
download_datasets.py — Roboflow dataset downloader + label merger.

Downloads unplated-vehicle datasets from Roboflow in YOLOv8 format,
applies the canonical class mapping defined in class_mapping.py,
merges images + labels into unplated_dataset/, and prints an
ambiguity report for any raw labels not in CLASS_MAPPING.

Usage (from backend/):
    python -m scanning.ml.download_datasets --api-key <KEY> [--dry-run]

Or via train.py:
    python -m scanning.ml.train --download --api-key <KEY>
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from collections import defaultdict

BASE_DIR = Path(__file__).resolve().parent
UNPLATED_DIR = BASE_DIR.parent.parent / "unplated_ds"
PLATE_DIR    = BASE_DIR / "dataset"          # existing license_plate-only set

# ── Canonical class ordering (MUST match detection.py CLASS_NAMES) ─────────────
CANONICAL_CLASSES = ["license_plate", "car", "bicycle", "e_bike", "electric_scooter"]
CLASS_TO_ID: dict[str, int] = {c: i for i, c in enumerate(CANONICAL_CLASSES)}

# ── Roboflow dataset specs ─────────────────────────────────────────────────────
# Each entry: (workspace, project, version, suggested_label_notes)
ROBOFLOW_DATASETS = [
    # Electric scooters — confirmed YOLOv8 object detection datasets
    ("etg-ik2gp",       "electric_scooter",       1, "electric_scooter"),
    ("ajou-univ-xsz0v", "electric-scooter-kdl9p", 1, "electric_scooter"),
    # REMOVED: scooter-9weqt/scooter-ptbxe — multilabel classification, not detection
    # REMOVED: personal-mtwk7/helmet-bike-and-scooter-detection — no valid version found
]

# Bicycle-only datasets (add your chosen 1-2 here after searching roboflow.com)
# Format: (workspace, project, version, "bicycle")
BICYCLE_DATASETS: list[tuple[str, str, int, str]] = [
    ("daffodil-international-university-kbooi", "object-detection-yolov8", 1, "bicycle"),
    ("yolov8-druip", "bicycle-zrn08", 1, "bicycle"),
    ("project", "cis-515-final-project", 1, "bicycle"),
    ("theone-f4xcb", "bicycle5904", 1, "bicycle"),
]


def _load_class_mapping():
    from .class_mapping import CLASS_MAPPING, EXCLUDED_LABELS, get_standard_class_name, is_excluded_label
    return CLASS_MAPPING, EXCLUDED_LABELS, get_standard_class_name, is_excluded_label


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


def _remap_label_file(
    label_path: Path,
    raw_class_names: list[str],
    get_standard_class_name,
    is_excluded_label,
    ambiguity_log: dict[str, list[str]],
    dest_path: Path,
    dry_run: bool,
) -> bool:
    """
    Remap a single YOLO label file from raw class ids → canonical class ids.

    Returns True if at least one valid annotation was written.
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

            if is_excluded_label(raw_label):
                continue  # silently drop excluded classes (person, car, motorcycle…)

            canonical = get_standard_class_name(raw_label)
            if canonical is None:
                # Unknown label — log for review, skip
                ambiguity_log[raw_label].append(str(label_path))
                continue

            new_id = CLASS_TO_ID.get(canonical)
            if new_id is None:
                continue

            coords = " ".join(parts[1:])
            lines_out.append(f"{new_id} {coords}")

    if not lines_out:
        return False

    if not dry_run:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "w") as f:
            f.write("\n".join(lines_out) + "\n")
    return True


def _merge_split(
    rf_root: Path,
    split: str,
    raw_class_names: list[str],
    get_standard_class_name,
    is_excluded_label,
    dest_img_dir: Path,
    dest_lbl_dir: Path,
    ambiguity_log: dict[str, list[str]],
    dry_run: bool,
    prefix: str,
) -> tuple[int, int]:
    """Merge one split (train/val) from a downloaded dataset into unplated_dataset/."""
    src_img = rf_root / split / "images"
    src_lbl = rf_root / split / "labels"

    if not src_img.exists():
        # Some datasets use flat structure
        src_img = rf_root / "images"
        src_lbl = rf_root / "labels"
    if not src_img.exists():
        return 0, 0

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
        dest_lbl_path = dest_lbl_dir / (dest_stem + ".txt")

        ok = _remap_label_file(
            lbl_path, raw_class_names, get_standard_class_name, is_excluded_label,
            ambiguity_log, dest_lbl_path, dry_run,
        )
        if ok:
            if not dry_run:
                dest_img_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(img_path, dest_img_dir / (dest_stem + img_path.suffix))
            copied += 1
        else:
            skipped += 1

    return copied, skipped


def _parse_raw_class_names(rf_root: Path) -> list[str]:
    """Read class names from data.yaml inside the downloaded dataset."""
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


def _merge_existing_plate_dataset(
    dest_img_train: Path,
    dest_lbl_train: Path,
    dest_img_val: Path,
    dest_lbl_val: Path,
    dry_run: bool,
) -> int:
    """
    Copy existing license_plate-only dataset into the unified dataset,
    remapping class id 0 (license_plate) → 0 (still license_plate, no change).
    """
    merged = 0
    for split, dst_img, dst_lbl in [
        ("train", dest_img_train, dest_lbl_train),
        ("val",   dest_img_val,   dest_lbl_val),
    ]:
        src_img = PLATE_DIR / "images" / split
        src_lbl = PLATE_DIR / "labels" / split
        if not src_img.exists():
            continue
        for img_path in src_img.glob("*.*"):
            if img_path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}:
                continue
            lbl_path = src_lbl / (img_path.stem + ".txt")
            if not lbl_path.exists():
                continue
            # Labels already use id=0 for license_plate, which is correct
            if not dry_run:
                dst_img.mkdir(parents=True, exist_ok=True)
                dst_lbl.mkdir(parents=True, exist_ok=True)
                shutil.copy2(img_path, dst_img / img_path.name)
                shutil.copy2(lbl_path, dst_lbl / lbl_path.name)
            merged += 1
    return merged


def _write_data_yaml(dest_dir: Path, dry_run: bool):
    """Write the unified data.yaml for the combined 5-class dataset."""
    import yaml
    cfg = {
        "path":  str(dest_dir),
        "train": "images/train",
        "val":   "images/val",
        "nc":    len(CANONICAL_CLASSES),
        "names": CANONICAL_CLASSES,
    }
    yaml_path = dest_dir / "data.yaml"
    if not dry_run:
        with open(yaml_path, "w") as f:
            yaml.dump(cfg, f, sort_keys=False, default_flow_style=False)
    print(f"[OK] data.yaml written -> {yaml_path}")


def _print_ambiguity_report(ambiguity_log: dict[str, list[str]]):
    if not ambiguity_log:
        print("\n[OK] No ambiguous labels -- all raw labels mapped cleanly.")
        return

    print("\n" + "=" * 60)
    print("  !! AMBIGUITY REPORT -- Labels requiring review")
    print("  These labels were NOT in CLASS_MAPPING and were SKIPPED.")
    print("  Add them to class_mapping.py if they should be included.")
    print("=" * 60)
    for label, sources in sorted(ambiguity_log.items()):
        print(f"\n  Raw label: '{label}'  ({len(sources)} annotation file(s))")
        for src in sources[:3]:
            print(f"    - {src}")
        if len(sources) > 3:
            print(f"    ... and {len(sources) - 3} more")
    print()


def download_and_merge(
    api_key: str,
    dry_run: bool = False,
    tmp_dir: Path = None,
) -> None:
    """
    Main entry point: download all datasets, remap labels, merge into
    unplated_dataset/.
    """
    import yaml

    try:
        import yaml as _yaml_check  # noqa: F401
    except ImportError:
        print("[ERROR] pyyaml not installed. Run: pip install pyyaml")
        sys.exit(1)

    CLASS_MAPPING, EXCLUDED_LABELS, get_standard_class_name, is_excluded_label = _load_class_mapping()

    if tmp_dir is None:
        tmp_dir = BASE_DIR.parent.parent / "dl_temp"
    if not dry_run:
        tmp_dir.mkdir(parents=True, exist_ok=True)

    dest_img_train = UNPLATED_DIR / "images" / "train"
    dest_lbl_train = UNPLATED_DIR / "labels" / "train"
    dest_img_val   = UNPLATED_DIR / "images" / "val"
    dest_lbl_val   = UNPLATED_DIR / "labels" / "val"

    ambiguity_log: dict[str, list[str]] = defaultdict(list)
    total_copied = 0
    total_skipped = 0

    all_datasets = ROBOFLOW_DATASETS + BICYCLE_DATASETS

    print("\n" + "=" * 60)
    print(f"  Downloading {len(all_datasets)} Roboflow dataset(s)")
    if dry_run:
        print("  [DRY RUN] -- no files will be written")
    print("=" * 60 + "\n")

    for workspace, project, version, hint in all_datasets:
        print(f"[DL] {workspace}/{project} v{version} (hint: {hint})")
        rf_dest = tmp_dir / f"{workspace}__{project}__v{version}"

        if rf_dest.exists() and any(rf_dest.iterdir()):
            print(f"     -> Already downloaded at {rf_dest}")
        else:
            try:
                rf_root = _download_rf(api_key, workspace, project, version, rf_dest)
            except Exception as exc:
                print(f"     [FAILED] Download failed: {exc}")
                continue

        rf_root = rf_dest
        raw_names = _parse_raw_class_names(rf_root)
        if not raw_names:
            print("     [WARN] Could not parse class names from data.yaml -- skipping")
            continue

        print(f"     Raw classes: {raw_names}")
        prefix = f"{workspace}__{project}__v{version}"

        for split, dst_img, dst_lbl in [
            ("train", dest_img_train, dest_lbl_train),
            ("valid", dest_img_val,   dest_lbl_val),
            ("val",   dest_img_val,   dest_lbl_val),
        ]:
            copied, skipped = _merge_split(
                rf_root, split, raw_names,
                get_standard_class_name, is_excluded_label,
                dst_img, dst_lbl, ambiguity_log,
                dry_run, prefix,
            )
            if copied or skipped:
                print(f"     [{split}] OK: {copied} merged, {skipped} skipped")
                total_copied += copied
                total_skipped += skipped

    # Merge existing plate dataset
    print(f"\n[MERGE] Existing license_plate dataset from: {PLATE_DIR}")
    plate_merged = _merge_existing_plate_dataset(
        dest_img_train, dest_lbl_train, dest_img_val, dest_lbl_val, dry_run
    )
    print(f"     OK: {plate_merged} plate images merged")

    # Write unified data.yaml
    _write_data_yaml(UNPLATED_DIR, dry_run)

    # Summary
    print("\n" + "=" * 60)
    print("  DONE")
    print(f"     Total new images merged:   {total_copied}")
    print(f"     Total annotations skipped: {total_skipped}")
    print(f"     Destination: {UNPLATED_DIR}")
    print("=" * 60)

    _print_ambiguity_report(ambiguity_log)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Download Roboflow datasets and merge into unified unplated_dataset/"
    )
    parser.add_argument("--api-key",  required=True, help="Roboflow API key")
    parser.add_argument("--dry-run",  action="store_true",
                        help="Show what would happen without writing files")
    parser.add_argument("--tmp-dir",  type=str, default=None,
                        help="Directory for raw Roboflow downloads")
    args = parser.parse_args()

    download_and_merge(
        api_key=args.api_key,
        dry_run=args.dry_run,
        tmp_dir=Path(args.tmp_dir) if args.tmp_dir else None,
    )
