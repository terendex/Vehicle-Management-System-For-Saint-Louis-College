"""
lpr_pipeline.py — Non-blocking Multi-threaded Event-Driven License Plate Recognition Pipeline

Pipeline Structure:
1. Multithreaded Video Stream (background thread with frame dropping)
2. Continuous Bounding Box Tracking (IoU-based lightweight tracking)
3. One-Time Conditional OCR Gate (cached results via ocr_memory)
4. Philippine LTO Lexical Filtering & Correction
5. Smooth Visual Rendering
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Optional
import re

from pathlib import Path

import cv2
import numpy as np


import logging

log = logging.getLogger(__name__)


@dataclass
class Track:
    track_id: int
    bbox: tuple[int, int, int, int] = (0, 0, 0, 0)
    plate_text: str = ""
    missed_frames: int = 0
    is_active: bool = True


class VideoStreamThread(threading.Thread):
    def __init__(self, source, max_queue_size: int = 2, name: str = "VideoStream"):
        super().__init__(name=name, daemon=True)
        self.source = source
        self.max_queue_size = max_queue_size
        self.frame_queue: queue.Queue = queue.Queue(maxsize=max_queue_size)
        self._stop_event = threading.Event()
        self._cap = None
        self._last_freshness_time = 0.0

    def run(self):
        self._cap = cv2.VideoCapture(self.source)
        if not self._cap.isOpened():
            return
        while not self._stop_event.is_set():
            ret, frame = self._cap.read()
            if not ret:
                self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
            try:
                self.frame_queue.put_nowait((frame, time.time()))
            except queue.Full:
                try:
                    self.frame_queue.get_nowait()
                    self.frame_queue.put_nowait((frame, time.time()))
                except queue.Empty:
                    pass
        self._cap.release()

    def get_fresh_frame(self, timeout: float = 0.1) -> tuple[Optional[np.ndarray], float]:
        try:
            return self.frame_queue.get(timeout=timeout)
        except queue.Empty:
            return None, 0.0

    def stop(self):
        self._stop_event.set()


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


def compute_centroid_distance(boxA: tuple[int, int, int, int], boxB: tuple[int, int, int, int]) -> float:
    cx_a = boxA[0] + boxA[2] / 2
    cy_a = boxA[1] + boxA[3] / 2
    cx_b = boxB[0] + boxB[2] / 2
    cy_b = boxB[1] + boxB[3] / 2
    return np.sqrt((cx_a - cx_b) ** 2 + (cy_a - cy_b) ** 2)


class VehicleTracker:
    def __init__(self, iou_threshold: float = 0.3, centroid_threshold: float = 100.0, max_missed_frames: int = 30):
        self._tracks: dict[int, Track] = {}
        self._next_id: int = 1
        self.iou_threshold = iou_threshold
        self.centroid_threshold = centroid_threshold
        self.max_missed_frames = max_missed_frames

    def update(self, detections: list[tuple[tuple[int, int, int, int], float]]) -> list[Track]:
        if not self._tracks:
            for bbox, conf in detections:
                track = Track(self._next_id, bbox)
                self._tracks[self._next_id] = track
                self._next_id += 1
            return list(self._tracks.values())

        matches: dict[int, int] = {}
        used_tracks: set[int] = set()

        for d_idx, (bbox, conf) in enumerate(detections):
            best_iou, best_tid = 0.0, -1
            for tid, track in self._tracks.items():
                if tid in used_tracks:
                    continue
                iou = compute_iou(track.bbox, bbox)
                if iou > best_iou and iou >= self.iou_threshold:
                    best_iou, best_tid = iou, tid
            if best_tid >= 0:
                matches[d_idx] = best_tid
                used_tracks.add(best_tid)

        for d_idx, (bbox, conf) in enumerate(detections):
            if d_idx not in matches:
                track = Track(self._next_id, bbox)
                self._tracks[self._next_id] = track
                matches[d_idx] = self._next_id
                self._next_id += 1

        for d_idx, tid in matches.items():
            bbox, conf = detections[d_idx]
            self._tracks[tid].bbox = bbox
            self._tracks[tid].missed_frames = 0

        for tid in list(self._tracks.keys()):
            if tid not in matches:
                self._tracks[tid].missed_frames += 1
                if self._tracks[tid].missed_frames >= self.max_missed_frames:
                    self._tracks[tid].is_active = False

        self._tracks = {k: v for k, v in self._tracks.items() if v.is_active}
        return list(self._tracks.values())


_yolo_model = None


def get_yolo_model():
    global _yolo_model
    if _yolo_model is None:
        try:
            from ultralytics import YOLO
            model_path = Path(__file__).resolve().parent / "weights" / "best.pt"
            if model_path.exists():
                _yolo_model = YOLO(str(model_path))
        except ImportError:
            pass
    return _yolo_model


def detect_license_plates(img: np.ndarray, conf: float = 0.25) -> list[dict]:
    model = get_yolo_model()
    if model is None:
        return []
    h, w = img.shape[:2]
    try:
        results = model.predict(img, conf=conf, verbose=False, max_det=100)
    except Exception as e:
        log.error("[DETECT] YOLO error: %s", e)
        return []
    detections = []
    for r in results:
        for box in r.boxes:
            x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
            score = float(box.conf[0])
            x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
            box_w, box_h = x2 - x1, y2 - y1
            aspect_ratio = box_w / max(box_h, 1)
            if score < 0.25 or aspect_ratio < 0.5 or aspect_ratio > 6.0 or box_w < 30 or box_h < 10:
                continue
            detections.append({
                "bbox": (x1, y1, box_w, box_h),
                "score": score,
                "crop": img[y1:y2, x1:x2],
            })
    detections.sort(key=lambda d: d["score"], reverse=True)
    return detections


def get_easyocr_reader():
    global _ocr_reader
    if _ocr_reader is None:
        try:
            import easyocr
            try:
                import torch as _torch
                _use_gpu = _torch.cuda.is_available()
            except ImportError:
                _use_gpu = False
            _ocr_reader = easyocr.Reader(["en"], gpu=_use_gpu, allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-')
        except ImportError:
            pass
    return _ocr_reader


_ocr_reader = None


def sanitize_plate_text(text: str) -> str:
    text = re.sub(r'[^A-Z0-9-]', '', text.upper())
    if len(text) == 7 and text[3].isdigit() and text[4].isdigit() and text[5].isdigit() and text[6].isdigit():
        letters = text[:3]
        numbers = text[3:7]
        letters = letters.replace('0', 'O')
        numbers = numbers.replace('O', '0').replace('I', '1')
        return letters + numbers
    elif len(text) == 6:
        if text[0].isdigit() and text[1].isdigit() and text[2].isdigit():
            digits = text[:3]
            letters = text[3:6]
            digits = digits.replace('O', '0').replace('I', '1')
            return digits + letters
        elif text[0].isalpha() and text[1].isalpha() and text[2].isalpha():
            letters = text[:3]
            numbers = text[3:6]
            letters = letters.replace('0', 'O')
            numbers = numbers.replace('O', '0').replace('I', '1')
            return letters + numbers
    return text


def run_ocr_on_crop(crop: np.ndarray) -> str:
    reader = get_easyocr_reader()
    if reader is None or crop is None or crop.size == 0:
        return ""
    try:
        results = reader.readtext(crop, min_size=30)
        if not results:
            return ""
        best_text = max(results, key=lambda x: x[2] if len(x) > 2 else 0.0)[1]
        return sanitize_plate_text(best_text)
    except Exception as e:
        log.error("[OCR] Error: %s", e)
        return ""


ocr_memory: dict[int, str] = {}


class LPRPipeline:
    def __init__(self, source, iou_threshold: float = 0.3):
        self.stream = VideoStreamThread(source)
        self.tracker = VehicleTracker(iou_threshold=iou_threshold)
        self.ocr_memory = ocr_memory
        self.running = False

    def start(self):
        self.running = True
        self.stream.start()
        time.sleep(0.1)

    def process_frame(self, frame: np.ndarray) -> np.ndarray:
        detections = detect_license_plates(frame)
        tracks = self.tracker.update([(d["bbox"], d["score"]) for d in detections])
        for track in tracks:
            if track.track_id in self.ocr_memory:
                track.plate_text = self.ocr_memory[track.track_id]
            else:
                for d in detections:
                    if d["bbox"] == track.bbox:
                        plate_text = run_ocr_on_crop(d["crop"])
                        if plate_text:
                            self.ocr_memory[track.track_id] = plate_text
                            track.plate_text = plate_text
                        break
        return self._draw_overlay(frame, tracks)

    def _draw_overlay(self, frame: np.ndarray, tracks: list[Track]) -> np.ndarray:
        for track in tracks:
            x, y, w, h = track.bbox
            cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
            cv2.putText(frame, f"ID:{track.track_id}", (x, y - 40), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            if track.plate_text:
                cv2.putText(frame, track.plate_text, (x, y - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        return frame

    def stop(self):
        self.running = False
        self.stream.stop()


def run_pipeline(source, window_name: str = "LPR Pipeline"):
    pipeline = LPRPipeline(source)
    pipeline.start()
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    while True:
        frame, freshness = pipeline.stream.get_fresh_frame()
        if frame is None:
            continue
        result = pipeline.process_frame(frame)
        cv2.imshow(window_name, result)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
    pipeline.stop()
    cv2.destroyAllWindows()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Non-blocking LPR Pipeline")
    parser.add_argument("--source", type=str, default="0", help="Video source (camera index or path)")
    args = parser.parse_args()
    source = args.source
    if source.isdigit():
        source = int(source)
    run_pipeline(source)


if __name__ == "__main__":
    main()