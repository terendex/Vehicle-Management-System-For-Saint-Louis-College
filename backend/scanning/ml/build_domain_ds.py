"""
build_domain_ds.py — Assemble the multi-domain single-class vehicle dataset.

The vehicle detector is trained on several *domains* of imagery, each from its
own Roboflow export:

    street    — ground-level gate view      (vehicle_ds/, already split)
    elevated  — elevated/angled campus view (_rf_downloads/elevated_view/)
    parking   — parking-lot overview        (_rf_downloads/parking_view/)

This script merges them into ONE YOLO dataset (default: all_domains_ds/) with
every image renamed `<domain>__<stem>`, so the domain mix of any split stays
readable at a glance:

    ls all_domains_ds/train/images | sed "s/_.*//" | sort | uniq -c

Labels are remapped to the single class 0 = "vehicle" via class_mapping (same
policy as train.py); plate/person/e-scooter labels are dropped, and images left
with no boxes are skipped.

Splits:
  - A source that already has a valid/ (or val/) split keeps it.
  - A train-only source is split locally, deterministically: the split is
    decided by a hash of the *source photo* name, so every Roboflow
    augmentation of one photo lands on the same side (no train/val leakage)
    and adding images later never reshuffles the existing ones.

Usage (from backend/):
    python -m scanning.ml.build_domain_ds \
        --domain street:scanning/ml/vehicle_ds \
        --domain elevated:scanning/ml/_rf_downloads/elevated_view \
        --domain parking:scanning/ml/_rf_downloads/parking_view \
        --out scanning/ml/all_domains_ds

    # preview only:
    python -m scanning.ml.build_domain_ds --domain ... --dry-run

Then train against it:
    python -m scanning.ml.train --data scanning/ml/all_domains_ds/data.yaml
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
from collections import defaultdict
from pathlib import Path

try:
    from .class_mapping import get_standard_class_name, is_excluded_label
except ImportError:  # run as a script: python scanning/ml/build_domain_ds.py
    from class_mapping import get_standard_class_name, is_excluded_label

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUT = BASE_DIR / "all_domains_ds"

VEHICLE_CLASS_ID = 0
IMG_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

# Roboflow appends `.rf.<32-hex>` to every exported (and augmented) copy of a
# photo. Stripping it groups all copies of one photo under one key.
_RF_HASH = re.compile(r"\.rf\.[0-9a-f]{6,}$", re.IGNORECASE)
# Frames extracted from a video: `VID20260712171133_mp4-0007`
_VIDEO_FRAME = re.compile(r"^(?P<vid>.+_mp4)-\d+$", re.IGNORECASE)


def _group_key(stem: str, group_videos: bool) -> str:
    """
    Collapse a filename to the thing that must not straddle the train/val line.

    Two augmented copies of one photo are near-identical; so are two frames a
    few hundredths of a second apart in one video clip. Either pair split
    across train and val turns validation into a memorisation test.
    """
    key = _RF_HASH.sub("", stem)
    if group_videos:
        m = _VIDEO_FRAME.match(key)
        if m:
            return m.group("vid")
    return key


def _is_val(key: str, val_frac: float, seed: int) -> bool:
    """Deterministic hash-based split — stable across runs and additions."""
    digest = hashlib.md5(f"{seed}:{key}".encode()).hexdigest()
    return (int(digest[:8], 16) % 10_000) < val_frac * 10_000


def _class_names(src: Path) -> list[str]:
    import yaml

    for cand in (src / "data.yaml", src / "dataset.yaml"):
        if cand.exists():
            with open(cand) as f:
                cfg = yaml.safe_load(f) or {}
            names = cfg.get("names", [])
            if isinstance(names, dict):
                return [names[k] for k in sorted(names)]
            return list(names)
    raise SystemExit(f"[ERROR] No data.yaml in {src} — is this a YOLOv8 export?")


def _split_dirs(src: Path, split: str) -> "tuple[Path, Path] | None":
    """Locate (images, labels) for a split across the two common layouts."""
    for img, lbl in ((src / split / "images", src / split / "labels"),
                     (src / "images" / split, src / "labels" / split)):
        if img.is_dir():
            return img, lbl
    return None


def _remap_lines(lbl_path: Path, names: list[str], dropped: dict[str, int],
                 unknown: dict[str, int]) -> list[str]:
    """Read one YOLO label file → single-class lines, dropping non-vehicles."""
    out: list[str] = []
    if not lbl_path.exists():
        return out
    for line in lbl_path.read_text().splitlines():
        parts = line.split()
        if len(parts) != 5:
            continue
        raw_id = int(parts[0])
        if raw_id >= len(names):
            continue
        raw = names[raw_id]
        canonical = get_standard_class_name(raw) or get_standard_class_name(str(raw).lower())
        if canonical == "vehicle":
            out.append(f"{VEHICLE_CLASS_ID} {' '.join(parts[1:])}")
            continue
        dropped[raw] += 1
        if canonical is None and not is_excluded_label(raw):
            unknown[raw] += 1
    return out


def _write_data_yaml(out: Path) -> None:
    import yaml

    cfg = {
        "train": "train/images",
        "val":   "valid/images",
        "nc":    1,
        "names": ["vehicle"],
        "path":  str(out),
    }
    with open(out / "data.yaml", "w") as f:
        yaml.dump(cfg, f, sort_keys=False, default_flow_style=False)
    print(f"[OK] data.yaml written -> {out / 'data.yaml'}")


def build(
    domains: list[tuple[str, Path]],
    out: Path,
    val_frac: float = 0.2,
    seed: int = 0,
    group_videos: bool = False,
    dry_run: bool = False,
) -> None:
    if not dry_run:
        if out.exists():
            print(f"[..] Removing previous dataset at {out}")
            shutil.rmtree(out)
        for split in ("train", "valid"):
            (out / split / "images").mkdir(parents=True)
            (out / split / "labels").mkdir(parents=True)

    dropped: dict[str, int] = defaultdict(int)
    unknown: dict[str, int] = defaultdict(int)
    totals: dict[str, dict[str, int]] = {}

    for domain, src in domains:
        names = _class_names(src)
        print(f"\n[{domain}] {src}")
        print(f"     raw classes: {names}")

        # A source with its own valid/ keeps that split; a train-only source is
        # split locally by hash below.
        found = {s: _split_dirs(src, s) for s in ("train", "valid", "val", "test")}
        has_val = any(found[s] for s in ("valid", "val"))

        counts = {"train": 0, "valid": 0, "boxes": 0, "empty": 0}
        for split, dirs in found.items():
            if not dirs:
                continue
            img_dir, lbl_dir = dirs
            if split == "train":
                forced = "train" if has_val else None   # None = hash-split
            else:
                forced = "valid"                        # valid/val/test → valid

            for img in sorted(img_dir.iterdir()):
                if img.suffix.lower() not in IMG_SUFFIXES:
                    continue
                lines = _remap_lines(lbl_dir / f"{img.stem}.txt", names, dropped, unknown)
                if not lines:
                    counts["empty"] += 1
                    continue
                dest_split = forced or (
                    "valid" if _is_val(_group_key(img.stem, group_videos), val_frac, seed)
                    else "train"
                )
                counts[dest_split] += 1
                counts["boxes"] += len(lines)
                if dry_run:
                    continue
                stem = f"{domain}__{img.stem}"
                shutil.copy2(img, out / dest_split / "images" / f"{stem}{img.suffix}")
                (out / dest_split / "labels" / f"{stem}.txt").write_text("\n".join(lines) + "\n")

        totals[domain] = counts
        kept = counts["train"] + counts["valid"]
        per_img = counts["boxes"] / kept if kept else 0
        print(f"     -> train {counts['train']}, valid {counts['valid']}, "
              f"{counts['boxes']} boxes ({per_img:.1f}/img), "
              f"{counts['empty']} skipped (no vehicles)")

    if not dry_run:
        _write_data_yaml(out)

    print("\n" + "=" * 66)
    print(f"  {'domain':<12}{'train':>8}{'valid':>8}{'val %':>8}{'boxes':>10}{'boxes/img':>12}")
    print("-" * 66)
    grand: dict[str, int] = defaultdict(int)
    for domain, c in totals.items():
        kept = c["train"] + c["valid"]
        print(f"  {domain:<12}{c['train']:>8}{c['valid']:>8}"
              f"{(c['valid'] / kept * 100 if kept else 0):>7.0f}%{c['boxes']:>10}"
              f"{(c['boxes'] / kept if kept else 0):>12.1f}")
        for k, v in c.items():
            grand[k] += v
    print("-" * 66)
    kept = grand["train"] + grand["valid"]
    print(f"  {'TOTAL':<12}{grand['train']:>8}{grand['valid']:>8}"
          f"{(grand['valid'] / kept * 100 if kept else 0):>7.0f}%{grand['boxes']:>10}"
          f"{(grand['boxes'] / kept if kept else 0):>12.1f}")
    print("=" * 66)
    if dry_run:
        print("  [DRY RUN] nothing written")
    else:
        print(f"  Dataset: {out}")
        print(f"  Train:   python -m scanning.ml.train --data {out / 'data.yaml'}")

    if dropped:
        print("\nDropped annotations by raw label (not vehicles):")
        for label, n in sorted(dropped.items(), key=lambda x: -x[1]):
            flag = "  <-- UNKNOWN, add to class_mapping.py if it is a vehicle" if label in unknown else ""
            print(f"  {label}: {n}{flag}")


def _parse_domain(spec: str) -> tuple[str, Path]:
    """Parse `name:path`. Windows paths carry a drive-letter colon of their own."""
    if ":" not in spec:
        raise SystemExit(f"[ERROR] Bad --domain '{spec}' — expected name:path")
    name, path = spec.split(":", 1)
    src = Path(path).expanduser()
    if not src.is_absolute():
        candidates = [(Path.cwd() / path), (BASE_DIR / path)]
        src = next((c.resolve() for c in candidates if c.exists()), candidates[0].resolve())
    if not src.exists():
        raise SystemExit(f"[ERROR] Domain '{name}': {src} not found")
    return name, src


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Merge per-domain YOLOv8 exports into one single-class vehicle dataset")
    parser.add_argument("--domain", action="append", default=[], required=True,
                        help="Domain as name:path to a YOLOv8 export (repeatable)")
    parser.add_argument("--out", type=str, default=str(DEFAULT_OUT),
                        help=f"Output dataset directory (default: {DEFAULT_OUT})")
    parser.add_argument("--val-frac", type=float, default=0.2,
                        help="Validation fraction for train-only sources (default: 0.2)")
    parser.add_argument("--seed", type=int, default=0,
                        help="Split seed — change to reshuffle a train-only source")
    parser.add_argument("--group-videos", action="store_true",
                        help="Keep every frame of one video clip on the same side of the split")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report the merge without writing anything")
    args = parser.parse_args()

    build(
        domains=[_parse_domain(s) for s in args.domain],
        out=Path(args.out).expanduser().resolve(),
        val_frac=args.val_frac,
        seed=args.seed,
        group_videos=args.group_videos,
        dry_run=args.dry_run,
    )
