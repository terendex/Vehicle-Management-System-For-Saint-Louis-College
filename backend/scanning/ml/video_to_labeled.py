"""
video_to_labeled.py — Extract frames from ml_video, then auto-label them.

Pipeline:
  1. Scan media/ml_video for .mp4 / .mov files
  2. Extract frames with OpenCV (no ffmpeg required)
  3. Drop blurry frames
  4. Auto-label with the plate_detector model (plates) + YOLOv8n-COCO (vehicles,
     all unified into the single "vehicle" class)
  5. Write YOLO .txt labels to scanning/ml/labeled/labels/

Usage (from backend/):
    python -m scanning.ml.video_to_labeled
    python -m scanning.ml.video_to_labeled --fps 3 --video-dir media/ml_video
    python -m scanning.ml.video_to_labeled --skip-label   # frames only
    python -m scanning.ml.video_to_labeled --skip-extract # label existing frames
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2

try:
    from tqdm import tqdm
    _HAS_TQDM = True
except ImportError:
    _HAS_TQDM = False

VIDEO_EXTENSIONS = {".mp4", ".MP4"}


# ── Frame extraction ──────────────────────────────────────────────────────────

def _is_blurry(frame, threshold: float = 40.0) -> bool:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.Laplacian(gray, cv2.CV_64F).var() < threshold


def extract_frames(video_path: Path, output_dir: Path, fps_rate: float = 2.0,
                   blur_threshold: float = 40.0) -> tuple[int, int]:
    """Extract frames from one video at `fps_rate` frames/sec using OpenCV.

    Returns (saved, skipped_blurry).
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        print(f"  [WARN] Cannot open {video_path.name}")
        return 0, 0

    video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    frame_interval = max(1, round(video_fps / fps_rate))

    saved = blurry = frame_idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_idx % frame_interval == 0:
            if blur_threshold > 0 and _is_blurry(frame, blur_threshold):
                blurry += 1
            else:
                out_path = output_dir / f"{video_path.stem}_{frame_idx:06d}.jpg"
                cv2.imwrite(str(out_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
                saved += 1
        frame_idx += 1

    cap.release()
    return saved, blurry


def run_extraction(video_dir: Path, frames_dir: Path, fps_rate: float,
                   blur_threshold: float) -> int:
    video_files = sorted(
        f for f in video_dir.iterdir()
        if f.is_file() and f.suffix in VIDEO_EXTENSIONS
    )

    if not video_files:
        print(f"[ERROR] No video files found in {video_dir}")
        sys.exit(1)

    frames_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*55}")
    print(f"  STEP 1 — FRAME EXTRACTION")
    print(f"  Videos : {video_dir}")
    print(f"  Frames : {frames_dir}")
    print(f"  Rate   : {fps_rate} fps  |  blur filter: {blur_threshold > 0}")
    print(f"{'='*55}")

    total_saved = total_blurry = 0
    iterator = tqdm(video_files, desc="Extracting") if _HAS_TQDM else video_files

    for vf in iterator:
        saved, blurry = extract_frames(vf, frames_dir, fps_rate, blur_threshold)
        total_saved += saved
        total_blurry += blurry
        msg = f"  {vf.name}: {saved} frames"
        if blurry:
            msg += f"  ({blurry} blurry skipped)"
        print(msg)

    print(f"\n  Saved {total_saved} frames  |  Skipped {total_blurry} blurry")
    return total_saved


# ── Auto-labeling ─────────────────────────────────────────────────────────────

def run_labeling(frames_dir: Path, output_dir: Path) -> dict:
    from scanning.ml.auto_label import load_models, run_auto_label, CONF_THRESHOLD, CONF_VEHICLE

    n_frames = len(list(frames_dir.glob("*.jpg")))
    if n_frames == 0:
        print(f"[ERROR] No .jpg frames in {frames_dir}")
        sys.exit(1)

    print(f"\n{'='*55}")
    print(f"  STEP 2 — AUTO-LABELING")
    print(f"  Frames : {frames_dir}  ({n_frames} images)")
    print(f"  Labels : {output_dir / 'labels'}")
    print(f"{'='*55}")

    print("Loading models…")
    model_best, model_coco = load_models()

    if model_best is None:
        print("[ERROR] plate_detector weights not found at scanning/ml/runs/plate_detector/weights/best.pt")
        sys.exit(1)
    if model_coco is None:
        print("[WARN] COCO model unavailable — vehicle boxes will be skipped")

    print(f"  Plate confidence : >= {CONF_THRESHOLD}")
    print(f"  Vehicle confidence: >= {CONF_VEHICLE}")
    print()

    t0 = time.time()
    stats = run_auto_label(model_best, model_coco, frames_dir, output_dir)
    elapsed = time.time() - t0

    return stats, elapsed


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Extract frames from ml_video and auto-label them for YOLO training"
    )
    parser.add_argument("--video-dir", default="media/ml_video",
                        help="Folder containing raw .mp4/.mov files")
    parser.add_argument("--frames-dir", default="scanning/ml/raw_frames",
                        help="Where extracted frames are saved")
    parser.add_argument("--output-dir", default="scanning/ml/labeled",
                        help="Where labeled outputs (labels/) are written")
    parser.add_argument("--fps", type=float, default=5.0,
                        help="Frames to capture per second of video (default: 5.0)")
    parser.add_argument("--no-blur-filter", action="store_true",
                        help="Keep blurry frames instead of skipping them")
    parser.add_argument("--skip-extract", action="store_true",
                        help="Skip extraction; label frames already in --frames-dir")
    parser.add_argument("--skip-label", action="store_true",
                        help="Only extract frames, skip auto-labeling")
    args = parser.parse_args()

    video_dir  = Path(args.video_dir).resolve()
    frames_dir = Path(args.frames_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    blur_threshold = 0.0 if args.no_blur_filter else 20.0

    # ── Step 1: Extract ──────────────────────────────────────────
    if not args.skip_extract:
        run_extraction(video_dir, frames_dir, args.fps, blur_threshold)
    else:
        print(f"[SKIP] Extraction skipped — using frames in {frames_dir}")

    # ── Step 2: Label ────────────────────────────────────────────
    if not args.skip_label:
        stats, elapsed = run_labeling(frames_dir, output_dir)

        print(f"\n{'='*55}")
        print("  RESULTS")
        print(f"{'='*55}")
        print(f"  Total frames       : {stats['total_frames']}")
        print(f"  With detections    : {stats['frames_with_detections']}")
        print(f"  No detections      : {stats['frames_no_detections']}")
        print()
        print("  Per-class counts:")
        for cls, n in stats["by_class"].items():
            bar = "█" * min(n, 40)
            print(f"    {cls:<16} {n:>5}  {bar}")
        mins, secs = divmod(int(elapsed), 60)
        print(f"\n  Time elapsed : {mins}m {secs}s")
        print(f"  Labels saved : {output_dir / 'labels'}")
        print(f"  Frames folder: {frames_dir}")
    else:
        print(f"\n[SKIP] Labeling skipped.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
