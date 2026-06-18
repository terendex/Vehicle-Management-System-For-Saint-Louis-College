"""
extract_frames.py — Extract frames from raw camera footage for YOLOv8 labeling.

Usage:
    python -m scanning.ml.extract_frames --video-dir <path> --output-dir <path> --fps 2.5
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

try:
    from tqdm import tqdm
except ImportError:
    tqdm = lambda x, **kwargs: x

VIDEOS = []


def extract_frames(video_path: Path, output_dir: Path, fps: float = 2.5) -> int:
    """Extract frames from a video at given FPS using ffmpeg."""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vf", f"fps={fps}",
        "-q:v", "2",
        str(output_dir / f"{video_path.stem}_%06d.jpg")
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[ERROR] ffmpeg failed for {video_path.name}: {result.stderr}")
        return 0
    
    return len(list(output_dir.glob(f"{video_path.stem}_*.jpg")))


def is_blurry(img_path: Path, threshold: float = 40.0) -> bool:
    """Check if image is too blurry using Laplacian variance."""
    try:
        import cv2
        img = cv2.imread(str(img_path), cv2.IMREAD_GRAYSCALE)
        if img is None:
            return True
        laplacian_var = cv2.Laplacian(img, cv2.CV_64F).var()
        return laplacian_var < threshold
    except Exception:
        return False


def deduplicate_frames(frames_dir: Path, similarity_threshold: float = 0.95) -> tuple[int, int]:
    """Remove near-duplicate frames using perceptual hashing."""
    try:
        from PIL import Image
        import imagehash
    except ImportError:
        print("[WARN] PIL/imagehash not installed - skipping deduplication")
        return len(list(frames_dir.glob("*.jpg"))), 0

    hashes = {}
    duplicates = 0
    
    for img_path in tqdm(frames_dir.glob("*.jpg"), desc="Deduplicating"):
        try:
            img = Image.open(img_path)
            phash = imagehash.average_hash(img)
            
            for existing_path, existing_hash in hashes.items():
                if phash - existing_hash <= 5:
                    img_path.unlink()
                    duplicates += 1
                    break
            else:
                hashes[img_path] = phash
        except Exception:
            pass
    
    return len(hashes), duplicates


def main():
    parser = argparse.ArgumentParser(description="Extract frames from raw camera footage")
    parser.add_argument("--video-dir", type=str, default=None,
                        help="Directory containing raw videos")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="Output directory for extracted frames")
    parser.add_argument("--fps", type=float, default=2.5,
                        help="Frames per second to extract (default: 2.5)")
    parser.add_argument("--skip-dedup", action="store_true",
                        help="Skip deduplication step")
    args = parser.parse_args()

    video_dir = Path(args.video_dir).resolve() if args.video_dir else Path("media/ml_video").resolve()
    output_dir = Path(args.output_dir).resolve() if args.output_dir else Path("scanning/ml/raw_frames").resolve()
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    total_extracted = 0
    video_files = list(video_dir.glob("*.MOV")) + list(video_dir.glob("*.mov")) + list(video_dir.glob("*.mp4"))
    
    print(f"\n[1/3] Extracting frames from {len(video_files)} videos at {args.fps} fps...")
    for video_path in tqdm(video_files, desc="Videos"):
        count = extract_frames(video_path, output_dir, args.fps)
        total_extracted += count
        print(f"  {video_path.name}: {count} frames")
    
    print(f"\n[EXTRACTED] Total frames: {total_extracted}")
    
    if not args.skip_dedup:
        print(f"\n[2/3] Deduplicating near-identical frames...")
        final_count, duplicates = deduplicate_frames(output_dir)
        print(f"[DEDUP] Kept: {final_count}, Removed: {duplicates} duplicates")
        
        print(f"\n[3/3] Filtering motion-blur frames...")
        blurry_count = 0
        for img_path in list(output_dir.glob("*.jpg")):
            if is_blurry(img_path):
                img_path.unlink()
                blurry_count += 1
        print(f"[BLURRY] Removed {blurry_count} overly blurry frames")
        print(f"[FINAL] {len(list(output_dir.glob('*.jpg')))} usable frames")
    else:
        print(f"[SKIP] Deduplication skipped")
        print(f"[FINAL] {len(list(output_dir.glob('*.jpg')))} frames")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())