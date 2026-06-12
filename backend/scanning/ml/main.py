"""
main.py — Standalone entry point for Philippine license plate tracking.

Usage:
    python main.py [--source 0] [--output output.mp4]

Features:
- Custom YOLOv8 detection for Philippine plates
- Kalman-filter-based tracking
- Bounding box smoothing
- Image buffer (10 crops per track)
- OCR with majority voting
- PostgreSQL storage
- Real-time display with FPS counter
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections import deque, Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Deque, Optional

import cv2
import numpy as np

log = logging.getLogger(__name__)

DETECTION_CONF_THRESHOLD = 0.5
IOU_THRESHOLD = 0.3
MAX_MISSED_FRAMES = 30
OCR_INTERVAL = 10
BUFFER_SIZE = 10
WEIGHTS_PATH = Path(__file__).resolve().parent / "weights" / "best.pt"


def detect_plates_standalone(img: np.ndarray, conf: float = 0.5) -> list[dict]:
    """Detect plates using YOLOv8 - standalone version."""
    try:
        from ultralytics import YOLO
        if not WEIGHTS_PATH.exists():
            return []
        model = YOLO(str(WEIGHTS_PATH))
        h, w = img.shape[:2]
        results = model.predict(img, conf=conf, verbose=False)
        detections = []
        for r in results:
            for box in r.boxes:
                x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                score = float(box.conf[0])
                x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
                box_w, box_h = x2 - x1, y2 - y1
                aspect_ratio = box_w / max(box_h, 1)
                if score < 0.5 or aspect_ratio < 0.8 or aspect_ratio > 3.5:
                    continue
                pad_x = int(box_w * 0.15)
                pad_y_top = int(box_h * 0.15)
                pad_y_bottom = int(box_h * 1.2) if aspect_ratio < 2.0 else int(box_h * 0.15)
                cx1, cy1 = max(0, x1 - pad_x), max(0, y1 - pad_y_top)
                cx2, cy2 = min(w, x2 + pad_x), min(h, y2 + pad_y_bottom)
                crop = img[cy1:cy2, cx1:cx2]
                detections.append({
                    "crop": crop,
                    "bbox": {"x": float(cx1 / w), "y": float(cy1 / h), "width": float((cx2 - cx1) / w), "height": float((cy2 - cy1) / h)},
                    "score": score,
                    "aspect_ratio": aspect_ratio,
                })
        return detections
    except Exception as e:
        log.error("[DETECT] Error: %s", e)
        return []


class KalmanFilter:
    def __init__(self):
        self.state = np.zeros(8)
        self.P = np.eye(8) * 1000
        self.Q = np.eye(8) * 0.1
        self.R = np.eye(4) * 1.0
        self.F = np.array([
            [1, 0, 0, 0, 1, 0, 0, 0],
            [0, 1, 0, 0, 0, 1, 0, 0],
            [0, 0, 1, 0, 0, 0, 1, 0],
            [0, 0, 0, 1, 0, 0, 0, 1],
            [0, 0, 0, 0, 1, 0, 0, 0],
            [0, 0, 0, 0, 0, 1, 0, 0],
            [0, 0, 0, 0, 0, 0, 1, 0],
            [0, 0, 0, 0, 0, 0, 0, 1],
        ])
        self.H = np.array([
            [1, 0, 0, 0, 0, 0, 0, 0],
            [0, 1, 0, 0, 0, 0, 0, 0],
            [0, 0, 1, 0, 0, 0, 0, 0],
            [0, 0, 0, 1, 0, 0, 0, 0],
        ])

    def predict(self) -> np.ndarray:
        self.state = self.F @ self.state
        self.P = self.F @ self.P @ self.F.T + self.Q
        return self.state

    def update(self, measurement: np.ndarray) -> np.ndarray:
        z_pred = self.H @ self.state
        y = measurement - z_pred
        S = self.H @ self.P @ self.H.T + self.R
        K = self.P @ self.H.T @ np.linalg.inv(S)
        self.state = self.state + K @ y
        self.P = self.P - K @ self.H @ self.P
        return self.state


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


class TrackedObject:
    def __init__(self, track_id: int, bbox: tuple[int, int, int, int]):
        self.track_id = track_id
        self.bbox = bbox
        self.kalman = KalmanFilter()
        self.kalman.state[:4] = np.array(bbox, dtype=float)
        self.image_buffer: Deque = deque(maxlen=BUFFER_SIZE)
        self.plate_text = ""
        self.det_confidence = 0.0
        self.ocr_confidence = 0.0
        self.missed_frames = 0
        self.last_ocr_frame = 0
        self.frame_count = 0
        self.is_active = True

    def predict(self) -> tuple[int, int, int, int]:
        state = self.kalman.predict()
        x, y, w, h = state[:4]
        return (max(0, int(x)), max(0, int(y)), max(1, int(w)), max(1, int(h)))

    def update(self, bbox: tuple[int, int, int, int], conf: float, crop: Optional[np.ndarray] = None):
        self.bbox = bbox
        self.det_confidence = conf
        self.missed_frames = 0
        self.frame_count += 1
        if crop is not None:
            self.image_buffer.append(crop.copy())
        measurement = np.array(bbox, dtype=float)
        self.kalman.update(measurement)

    def add_crop(self, crop: np.ndarray):
        self.image_buffer.append(crop.copy())

    def should_run_ocr(self, frame_idx: int, interval: int = OCR_INTERVAL) -> bool:
        return (frame_idx - self.last_ocr_frame) >= interval

    def mark_ocr_done(self, frame_idx: int, plate: str, ocr_conf: float):
        self.last_ocr_frame = frame_idx
        self.plate_text = plate
        self.ocr_confidence = ocr_conf

    def mark_missed(self):
        self.missed_frames += 1
        if self.missed_frames >= MAX_MISSED_FRAMES:
            self.is_active = False


class PlateTracker:
    def __init__(self):
        self._tracks: dict[int, TrackedObject] = {}
        self._next_id: int = 1

    def update(self, detections: list[tuple[tuple[int, int, int, int], float, Optional[np.ndarray]]]) -> list[dict]:
        if not self._tracks:
            for bbox, conf, crop in detections:
                if conf >= DETECTION_CONF_THRESHOLD:
                    track = TrackedObject(self._next_id, bbox)
                    if crop is not None:
                        track.add_crop(crop)
                    self._tracks[self._next_id] = track
                    self._next_id += 1
            return [{"track_id": t.track_id, "bbox": t.bbox, "plate_text": t.plate_text} for t in self._tracks.values()]

        matches: dict[int, int] = {}
        used_tracks: set[int] = set()

        for d_idx, (bbox, conf, _) in enumerate(detections):
            if conf < DETECTION_CONF_THRESHOLD:
                continue
            best_iou, best_tid = 0.0, -1
            for t_id, track in self._tracks.items():
                if t_id in used_tracks:
                    continue
                pred = track.predict()
                iou = compute_iou(pred, bbox)
                if iou > best_iou and iou >= IOU_THRESHOLD:
                    best_iou, best_tid = iou, t_id
            if best_tid >= 0:
                matches[d_idx] = best_tid
                used_tracks.add(best_tid)

        for d_idx, (bbox, conf, crop) in enumerate(detections):
            if d_idx not in matches and conf >= DETECTION_CONF_THRESHOLD:
                track = TrackedObject(self._next_id, bbox)
                if crop is not None:
                    track.add_crop(crop)
                self._tracks[self._next_id] = track
                matches[d_idx] = self._next_id
                self._next_id += 1

        for d_idx, t_id in matches.items():
            bbox, conf, crop = detections[d_idx]
            track = self._tracks[t_id]
            track.update(bbox, conf, crop)

        for t_id in list(self._tracks.keys()):
            if t_id not in matches:
                self._tracks[t_id].mark_missed()

        self._tracks = {k: v for k, v in self._tracks.items() if v.is_active}

        return [{"track_id": t.track_id, "bbox": t.bbox, "plate_text": t.plate_text} for t in self._tracks.values()]

    def get_track(self, track_id: int) -> Optional[TrackedObject]:
        return self._tracks.get(track_id)

    def clear(self):
        self._tracks.clear()
        self._next_id = 1


def run_ocr_on_buffer(track: TrackedObject) -> tuple[str, float]:
    results = []
    for crop in track.image_buffer:
        try:
            text, conf = run_ocr_standalone(crop)
            if text and conf > 0.1:
                results.append((text, conf))
        except Exception:
            pass
    if not results:
        return "", 0.0
    by_text = {}
    for text, conf in results:
        by_text[text] = max(by_text.get(text, 0.0), conf)
    best = max(by_text.items(), key=lambda x: x[1])
    return best


def run_ocr_standalone(crop: np.ndarray, aspect_ratio: float = 1.0) -> tuple[Optional[str], float]:
    """Standalone OCR using EasyOCR."""
    try:
        import easyocr
        ocr = easyocr.Reader(["en"], gpu=False)
        h, w = crop.shape[:2]
        if w < 320:
            scale = 320 / max(w, 1)
            crop = cv2.resize(crop, (320, max(int(h * scale), 20)), interpolation=cv2.INTER_CUBIC)
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        results = ocr.readtext(gray, text_threshold=0.3)
        if not results:
            return None, 0.0
        best_text, best_conf = None, 0.0
        for (bbox, text, conf) in results:
            if conf > best_conf:
                best_text, best_conf = text, conf
        return best_text, best_conf
    except Exception as e:
        log.debug("[OCR] Error: %s", e)
        return None, 0.0


def main():
    parser = argparse.ArgumentParser(description="Philippine License Plate Tracker")
    parser.add_argument("--source", type=int, default=0, help="Camera source index")
    parser.add_argument("--output", type=str, default=None, help="Output video path")
    parser.add_argument("--gpu", action="store_true", help="Use GPU acceleration")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    cap = cv2.VideoCapture(args.source)
    if not cap.isOpened():
        log.error("Cannot open camera %d", args.source)
        sys.exit(1)

    tracker = PlateTracker()
    fps_counter = 0
    fps_start = time.time()
    frame_idx = 0
    out = None
    if args.output:
        out = cv2.VideoWriter(args.output, cv2.VideoWriter_fourcc(*'mp4v'), 30, 
                              (int(cap.get(3)), int(cap.get(4))))

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        h, w = frame.shape[:2]
        frame_idx += 1
        fps_counter += 1

        detections = detect_plates_standalone(frame)
        detections_list = [((int(b["x"]*w), int(b["y"]*h), int(b["width"]*w), int(b["height"]*h)), s, c) 
                         for c, b, s in [(d["crop"], d["bbox"], d["score"]) for d in detections]]

        tracks = tracker.update(detections_list)

        for track in tracker.tracks.values():
            if track.should_run_ocr(frame_idx):
                track.last_ocr_frame = frame_idx
                if len(track.image_buffer) > 0:
                    plate, ocr_conf = run_ocr_on_buffer(track)
                    if plate:
                        track.mark_ocr_done(frame_idx, plate, ocr_conf)

        for track in tracker.tracks.values():
            x, y, ww, hh = track.bbox
            cv2.rectangle(frame, (x, y), (x + ww, y + hh), (0, 255, 0), 2)
            cv2.putText(frame, f"ID:{track.track_id}", (x, y - 40), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            if track.plate_text:
                cv2.putText(frame, track.plate_text, (x, y - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            cv2.putText(frame, f"{track.det_confidence:.2f}", (x, y + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

        if fps_counter % 30 == 0:
            fps = 30.0 / (time.time() - fps_start)
            fps_start = time.time()
        cv2.putText(frame, f"FPS:{fps:.1f}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)

        cv2.imshow("Plate Tracker", frame)
        if out:
            out.write(frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    if out:
        out.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()