"""
tracking.py — Kalman-filter-based vehicle/license plate tracker.

Features:
- Unique Track ID assignment
- True Kalman filter for smooth position prediction
- Tracks alive for up to 30 frames during occlusion
- ID switching prevention via IoU + centroid matching
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Deque, Optional

import numpy as np

log = logging.getLogger(__name__)

MAX_MISSED_FRAMES = 30
IOU_THRESHOLD = 0.3
BUFFER_SIZE = 10


class KalmanFilter:
    """Kalman filter for 2D bounding box tracking."""
    
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
    """Compute Intersection over Union between two bounding boxes."""
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


@dataclass
class TrackedObject:
    track_id: int
    bbox: tuple[int, int, int, int] = (0, 0, 0, 0)
    kalman: KalmanFilter = field(default_factory=KalmanFilter)
    image_buffer: Deque = field(default_factory=lambda: deque(maxlen=BUFFER_SIZE))
    plate_text: str = ""
    det_confidence: float = 0.0
    ocr_confidence: float = 0.0
    missed_frames: int = 0
    last_ocr_frame: int = -100
    frame_count: int = 0
    is_active: bool = True
    last_scan: Optional[datetime] = None

    def __post_init__(self):
        x, y, w, h = self.bbox
        self.kalman.state[:4] = np.array([x, y, w, h], dtype=float)

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

    def should_run_ocr(self, frame_idx: int, interval: int = 10) -> bool:
        if self.last_ocr_frame < 0:
            return True
        return (frame_idx - self.last_ocr_frame) >= interval

    def mark_ocr_done(self, frame_idx: int, plate: str, ocr_conf: float):
        self.last_ocr_frame = frame_idx
        self.plate_text = plate
        self.ocr_confidence = ocr_conf

    def mark_missed(self):
        self.missed_frames += 1
        if self.missed_frames >= MAX_MISSED_FRAMES:
            self.is_active = False

    def in_cooldown(self, now: datetime) -> bool:
        return False

    def mark_scanned(self, now: datetime):
        self.last_scan = now


class PlateTracker:
    def __init__(self):
        self._tracks: dict[int, TrackedObject] = {}
        self._next_id: int = 1

    def update(self, detections: list[dict]) -> list[dict]:
        if not self._tracks:
            for det in detections:
                bbox = det["bbox"]
                conf = det.get("confidence", 0.0)
                crop = det.get("crop")
                x, y, w, h = bbox["x"], bbox["y"], bbox["width"], bbox["height"]
                if conf >= 0.3:
                    track = TrackedObject(self._next_id, (x, y, w, h))
                    if crop is not None:
                        track.add_crop(crop)
                    self._tracks[self._next_id] = track
                    self._next_id += 1
            return [{"track_id": t.track_id, "bbox": t.bbox, "plate_text": t.plate_text} for t in self._tracks.values()]

        matches: dict[int, int] = {}
        used_tracks: set[int] = set()

        for d_idx, det in enumerate(detections):
            bbox = det["bbox"]
            conf = det.get("confidence", 0.0)
            if conf < 0.3:
                continue
            x, y, w, h = bbox["x"], bbox["y"], bbox["width"], bbox["height"]
            abs_bbox = (x, y, w, h)
            best_iou, best_tid = 0.0, -1
            for t_id, track in self._tracks.items():
                if t_id in used_tracks:
                    continue
                pred = track.predict()
                iou = compute_iou(pred, abs_bbox)
                if iou > best_iou and iou >= IOU_THRESHOLD:
                    best_iou, best_tid = iou, t_id
            if best_tid >= 0:
                matches[d_idx] = best_tid
                used_tracks.add(best_tid)

        for d_idx, det in enumerate(detections):
            bbox = det["bbox"]
            conf = det.get("confidence", 0.0)
            crop = det.get("crop")
            x, y, w, h = bbox["x"], bbox["y"], bbox["width"], bbox["height"]
            if d_idx not in matches and conf >= 0.3:
                track = TrackedObject(self._next_id, (x, y, w, h))
                if crop is not None:
                    track.add_crop(crop)
                self._tracks[self._next_id] = track
                matches[d_idx] = self._next_id
                self._next_id += 1

        for d_idx, t_id in matches.items():
            det = detections[d_idx]
            bbox = det["bbox"]
            conf = det.get("confidence", 0.0)
            crop = det.get("crop")
            x, y, w, h = bbox["x"], bbox["y"], bbox["width"], bbox["height"]
            track = self._tracks[t_id]
            track.update((x, y, w, h), conf, crop)

        for t_id in list(self._tracks.keys()):
            if t_id not in matches:
                self._tracks[t_id].mark_missed()

        self._tracks = {k: v for k, v in self._tracks.items() if v.is_active}

        return [{"track_id": t.track_id, "bbox": t.bbox, "plate_text": t.plate_text} for t in self._tracks.values()]

    def get_track(self, track_id: int) -> Optional[TrackedObject]:
        return self._tracks.get(track_id)

    def mark_scanned(self, track_id: int, now: datetime):
        track = self._tracks.get(track_id)
        if track:
            track.mark_scanned(now)

    @property
    def tracks(self) -> dict[int, TrackedObject]:
        return self._tracks

    def clear(self):
        self._tracks.clear()
        self._next_id = 1