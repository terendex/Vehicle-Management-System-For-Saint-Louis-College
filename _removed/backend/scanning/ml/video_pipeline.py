"""
video_pipeline.py — Video processing pipeline for vehicle/license plate detection.

Pipeline:
1. Load model
2. Load video
3. Read frames
4. Detect vehicles
5. Track vehicles
6. Detect license plates
7. Assign license plates to vehicles
8. Crop license plates
9. Process via B&W filter for OCR
10. Train / results
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

log = logging.getLogger(__name__)

VEHICLE_MODEL_PATH = Path(__file__).resolve().parent / "runs" / "vehicle_detector" / "weights" / "best.pt"
PLATE_MODEL_PATH = Path(__file__).resolve().parent / "runs" / "plate_detector" / "weights" / "best.pt"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"

_ocr_reader = None


def _get_ocr_reader():
    global _ocr_reader
    if _ocr_reader is None:
        try:
            from paddleocr import PaddleOCR
            try:
                import torch as _torch
                _use_gpu = _torch.cuda.is_available()
            except ImportError:
                _use_gpu = False
            _ocr_reader = PaddleOCR(use_angle_cls=True, lang='en', use_gpu=_use_gpu, show_log=False)
        except ImportError:
            pass
    return _ocr_reader


@dataclass
class Vehicle:
    track_id: int
    bbox: tuple[int, int, int, int] = (0, 0, 0, 0)
    missed_frames: int = 0
    is_active: bool = True
    plate_text: str = ""
    plate_crop: Optional[np.ndarray] = None


def compute_iou(boxA: tuple[int, int, int, int], boxB: tuple[int, int, int, int]) -> float:
    xA = max(boxA[0], boxB[0])
    yA = max(boxA[1], boxB[1])
    xB = min(boxA[0] + boxA[2], boxB[0] + boxB[2])
    yB = min(boxA[1] + boxA[3], boxB[1] + boxB[3])
    interArea = max(0, xB - xA) * max(0, yB - yA)
    boxAArea = boxA[2] * boxA[3]
    boxBArea = boxB[2] * boxB[3]
    if boxAArea == 0 or boxBArea == 0:
        return 0.0
    return interArea / float(boxAArea + boxBArea - interArea)


class VehicleTracker:
    def __init__(self, max_missed_frames: int = 30, iou_threshold: float = 0.3):
        self._tracks: dict[int, Vehicle] = {}
        self._next_id: int = 1
        self.max_missed_frames = max_missed_frames
        self.iou_threshold = iou_threshold

    def update(self, detections: list[tuple[tuple[int, int, int, int], float]]) -> list[dict]:
        if not self._tracks:
            for bbox, conf in detections:
                vehicle = Vehicle(self._next_id, bbox)
                self._tracks[self._next_id] = vehicle
                self._next_id += 1
            return [{"track_id": t.track_id, "bbox": t.bbox, "plate_text": t.plate_text} for t in self._tracks.values()]

        matches: dict[int, int] = {}
        used_tracks: set[int] = set()

        for d_idx, (bbox, conf) in enumerate(detections):
            best_iou, best_tid = 0.0, -1
            for t_id, track in self._tracks.items():
                if t_id in used_tracks:
                    continue
                pred = track.bbox
                iou = compute_iou(pred, bbox)
                if iou > best_iou and iou >= self.iou_threshold:
                    best_iou, best_tid = iou, t_id
            if best_tid >= 0:
                matches[d_idx] = best_tid
                used_tracks.add(best_tid)

        for d_idx, (bbox, conf) in enumerate(detections):
            if d_idx not in matches:
                vehicle = Vehicle(self._next_id, bbox)
                self._tracks[self._next_id] = vehicle
                matches[d_idx] = self._next_id
                self._next_id += 1

        for d_idx, t_id in matches.items():
            bbox, conf = detections[d_idx]
            track = self._tracks[t_id]
            track.bbox = bbox
            track.missed_frames = 0

        for t_id in list(self._tracks.keys()):
            if t_id not in matches:
                self._tracks[t_id].missed_frames += 1
                if self._tracks[t_id].missed_frames >= self.max_missed_frames:
                    self._tracks[t_id].is_active = False

        self._tracks = {k: v for k, v in self._tracks.items() if v.is_active}
        return [{"track_id": t.track_id, "bbox": t.bbox, "plate_text": t.plate_text} for t in self._tracks.values()]

    def assign_plate(self, track_id: int, plate_text: str, plate_crop: Optional[np.ndarray] = None):
        if track_id in self._tracks:
            self._tracks[track_id].plate_text = plate_text
            self._tracks[track_id].plate_crop = plate_crop

    @property
    def tracks(self) -> dict[int, Vehicle]:
        return self._tracks


def detect_license_plates(img: np.ndarray, conf: float = 0.25) -> list[dict]:
    try:
        from .detection import detect_plates
        detections = detect_plates(img, conf)
        for d in detections:
            d["bbox"] = (
                int(d["bbox"]["x"] * img.shape[1]),
                int(d["bbox"]["y"] * img.shape[0]),
                int(d["bbox"]["width"] * img.shape[1]),
                int(d["bbox"]["height"] * img.shape[0]),
            )
        return detections
    except Exception as e:
        log.error("[PLATE] Detection error: %s", e)
        return []


def assign_plates_to_vehicles(vehicle_tracks: dict[int, Vehicle], plate_detections: list[dict]) -> dict[int, np.ndarray]:
    assignments = {}
    for plate in plate_detections:
        plate_bbox = plate["bbox"]
        best_iou, best_tid = 0.0, -1
        for tid, vehicle in vehicle_tracks.items():
            iou = compute_iou(vehicle.bbox, plate_bbox)
            if iou > best_iou and iou >= 0.3:
                best_iou, best_tid = iou, tid
        if best_tid >= 0:
            assignments[best_tid] = plate["crop"]
    return assignments


def preprocess_for_ocr(crop: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=4.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    _, binary = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return binary


def run_ocr_on_crop(crop: np.ndarray) -> tuple[Optional[str], float]:
    try:
        ocr = _get_ocr_reader()
        if ocr is None:
            return None, 0.0
        gray = preprocess_for_ocr(crop)
        if gray.shape[1] < 400:
            scale = 400 / max(gray.shape[1], 1)
            gray = cv2.resize(gray, (400, int(gray.shape[0] * scale)), interpolation=cv2.INTER_CUBIC)
        img_bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR) if len(gray.shape) == 2 else gray
        raw = ocr.ocr(img_bgr, cls=True)
        page = raw[0] if raw else []
        if not page:
            return None, 0.0
        best_text, best_conf = None, 0.0
        for item in page:
            if not item or len(item) != 2:
                continue
            text, conf = item[1][0], item[1][1]
            if conf > best_conf:
                best_text, best_conf = text, conf
        return best_text, best_conf
    except Exception as e:
        log.error("[OCR] Error: %s", e)
        return None, 0.0


def process_video(video_path: str, output_path: Optional[str] = None, process_every: int = 1):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        log.error("[VIDEO] Cannot open video: %s", video_path)
        return []
    
    fps = cap.get(cv2.CAP_PROP_FPS)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    
    out = None
    if output_path:
        out = cv2.VideoWriter(output_path, cv2.VideoWriter_fourcc(*'mp4v'), fps, (width, height))
    
    vehicle_tracker = VehicleTracker()
    frame_idx = 0
    results = []
    
    log.info("[VIDEO] Processing: %s (%dx%d @ %.2f fps)", video_path, width, height, fps)
    
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        
        frame_idx += 1
        if frame_idx % process_every != 0:
            continue
        
        h, w = frame.shape[:2]
        
        plate_detections = detect_license_plates(frame, conf=0.25)
        
        if not plate_detections:
            if out:
                out.write(frame)
            continue
        
        detections_for_tracker = [(d["bbox"], d["score"]) for d in plate_detections]
        
        tracks = vehicle_tracker.update(detections_for_tracker)
        
        assignments = assign_plates_to_vehicles(vehicle_tracker.tracks, plate_detections)
        
        for tid, crop in assignments.items():
            plate_text, conf = run_ocr_on_crop(crop)
            if plate_text:
                vehicle_tracker.assign_plate(tid, plate_text, crop)
                log.info("[FRAME %d] Track %d: %s (conf=%.2f)", frame_idx, tid, plate_text, conf)
        
        for track in tracks:
            tid = track["track_id"]
            x, y, ww, hh = track["bbox"]
            cv2.rectangle(frame, (x, y), (x + ww, y + hh), (0, 255, 0), 2)
            cv2.putText(frame, f"ID:{tid}", (x, y - 40), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            if track["plate_text"]:
                cv2.putText(frame, track["plate_text"], (x, y - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        
        results.append({
            "frame": frame_idx,
            "vehicles": len(tracks),
            "plates": sum(1 for t in tracks if t["plate_text"]),
        })
        
        if out:
            out.write(frame)
    
    cap.release()
    if out:
        out.release()
    
    log.info("[VIDEO] Processing complete")
    return results


def main():
    parser = argparse.ArgumentParser(description="Video processing pipeline for vehicle/license plate detection")
    parser.add_argument("--source", type=str, required=True, help="Video file or camera index")
    parser.add_argument("--output", type=str, default=None, help="Output video path")
    parser.add_argument("--process-every", type=int, default=1, help="Process every N frames")
    args = parser.parse_args()
    
    logging.basicConfig(level=logging.INFO)
    
    video_source = args.source
    if video_source.isdigit():
        video_source = int(video_source)
    
    results = process_video(
        video_path=video_source,
        output_path=args.output,
        process_every=args.process_every,
    )
    
    if results:
        total_vehicles = sum(r["vehicles"] for r in results)
        total_plates = sum(r["plates"] for r in results)
        print(f"\nResults:")
        print(f"  Total frames processed: {len(results)}")
        print(f"  Total vehicle detections: {total_vehicles}")
        print(f"  Total plates recognized: {total_plates}")


if __name__ == "__main__":
    main()