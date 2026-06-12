"""
tracker.py — IoU-based Plate Tracker + Scan Cooldown Manager

Tracks physical license plates across consecutive frames by matching
YOLO bounding boxes via Intersection-over-Union (IoU).  Each tracked
object carries its own cooldown timer so a plate that moves across the
frame is only scanned once per cooldown window regardless of minor
bbox jitter or OCR mis-reads.

Bbox format (matches reader.py output):
    {"x": top_left_x, "y": top_left_y, "width": w, "height": h}   all 0-1 relative
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime

from django.utils import timezone

from .reader import is_valid_ph_plate, normalize_plate

log = logging.getLogger(__name__)

PLATE_COOLDOWN_SECONDS = 3.0
IOU_MATCH_THRESHOLD = 0.15
MAX_MISSED_FRAMES = 150
TRACK_TTL_SECONDS = 5.0
INITIAL_AVERAGE_FRAMES = 3
CAMERA_MOTION_CENTROID_THRESHOLD = 0.15
CAMERA_MOTION_SIZE_CHANGE = 0.20


def _top_left_to_center(box: dict[str, float]) -> dict[str, float]:
    return {
        "x": box["x"] + box["width"] / 2.0,
        "y": box["y"] + box["height"] / 2.0,
        "width": box["width"],
        "height": box["height"],
    }


def _center_to_top_left(box: dict[str, float]) -> dict[str, float]:
    return {
        "x": box["x"] - box["width"] / 2.0,
        "y": box["y"] - box["height"] / 2.0,
        "width": box["width"],
        "height": box["height"],
    }


def _compute_iou(box_a: dict[str, float], box_b: dict[str, float]) -> float:
    ax1 = box_a["x"]
    ay1 = box_a["y"]
    ax2 = box_a["x"] + box_a["width"]
    ay2 = box_a["y"] + box_a["height"]
    bx1 = box_b["x"]
    by1 = box_b["y"]
    bx2 = box_b["x"] + box_b["width"]
    by2 = box_b["y"] + box_b["height"]
    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    inter = max(0.0, inter_x2 - inter_x1) * max(0.0, inter_y2 - inter_y1)
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    if area_a <= 0.0 or area_b <= 0.0:
        return 0.0
    denom = area_a + area_b - inter
    if denom <= 0.0:
        return 0.0
    return inter / denom


def _compute_centroid(box: dict[str, float]) -> tuple[float, float]:
    return (box["x"] + box["width"] / 2.0, box["y"] + box["height"] / 2.0)


def _compute_euclidean(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _clamp_box(box: dict[str, float]) -> dict[str, float]:
    w = max(0.01, min(box["width"], 1.0))
    h = max(0.01, min(box["height"], 1.0))
    x = max(0.0, min(box["x"], 1.0 - w))
    y = max(0.0, min(box["y"], 1.0 - h))
    return {"x": x, "y": y, "width": w, "height": h}


@dataclass
class TrackedPlate:
    track_id: int
    bbox: dict[str, float] = field(default_factory=dict)
    plate_number: str = ""
    confidence_history: list[float] = field(default_factory=list)
    last_seen: datetime = field(default_factory=timezone.now)
    last_scanned: datetime | None = None
    missed_frames: int = 0
    plate_candidates: dict[str, int] = field(default_factory=dict)
    smoothed_bbox: dict[str, float] = field(default_factory=dict)
    centroid_history: list[tuple[float, float]] = field(default_factory=list)
    velocity: tuple[float, float] = (0.0, 0.0)
    is_stale: bool = False
    init_buffer: list[dict[str, float]] = field(default_factory=list)
    is_initializing: bool = True
    frame_count: int = 0
    last_ocr_frame: int = 0

    def record_detection(self, bbox: dict[str, float], plate_text: str, confidence: float, alpha: float = 0.5) -> str:
        raw_box = _clamp_box(bbox)
        self.frame_count += 1

        if self.is_initializing:
            self.init_buffer.append(raw_box)
            if len(self.init_buffer) >= INITIAL_AVERAGE_FRAMES:
                avg: dict[str, float] = {
                    "x": sum(b["x"] for b in self.init_buffer) / len(self.init_buffer),
                    "y": sum(b["y"] for b in self.init_buffer) / len(self.init_buffer),
                    "width": sum(b["width"] for b in self.init_buffer) / len(self.init_buffer),
                    "height": sum(b["height"] for b in self.init_buffer) / len(self.init_buffer),
                }
                self.init_buffer = []
                self.smoothed_bbox = _clamp_box(avg)
                self.bbox = dict(self.smoothed_bbox)
                self.is_initializing = False
            else:
                self.bbox = raw_box
                self.smoothed_bbox = dict(raw_box)
                self.last_seen = timezone.now()
                if confidence and confidence > 0.0:
                    self.confidence_history.append(confidence)
                self._update_plate_candidates(plate_text, confidence)
                return self.plate_number

        now = timezone.now()
        dt = (now - self.last_seen).total_seconds()
        if dt <= 0:
            dt = 0.016

        new_centroid = _compute_centroid(raw_box)
        prev_centroid = _compute_centroid(self.smoothed_bbox) if self.smoothed_bbox else new_centroid

        if dt > 0:
            vx = (new_centroid[0] - prev_centroid[0]) / dt
            vy = (new_centroid[1] - prev_centroid[1]) / dt

            centroid_dist = _compute_euclidean(new_centroid, prev_centroid)
            size_delta = max(
                abs(raw_box["width"] - self.smoothed_bbox.get("width", raw_box["width"])) / max(self.smoothed_bbox.get("width", raw_box["width"]), 0.01),
                abs(raw_box["height"] - self.smoothed_bbox.get("height", raw_box["height"])) / max(self.smoothed_bbox.get("height", raw_box["height"]), 0.01),
            )
            if centroid_dist > CAMERA_MOTION_CENTROID_THRESHOLD or size_delta > CAMERA_MOTION_SIZE_CHANGE:
                self.velocity = (0.0, 0.0)
                adaptive_alpha = 0.7
            else:
                k = 0.35
                self.velocity = (
                    self.velocity[0] * (1.0 - k) + vx * k,
                    self.velocity[1] * (1.0 - k) + vy * k,
                )
                adaptive_alpha = 1.0 - alpha ** dt
        else:
            adaptive_alpha = 0.5

        for key in ("x", "y", "width", "height"):
            self.smoothed_bbox[key] = self.smoothed_bbox.get(key, raw_box[key]) + adaptive_alpha * (raw_box[key] - self.smoothed_bbox.get(key, raw_box[key]))
        self.smoothed_bbox = _clamp_box(self.smoothed_bbox)

        self.bbox = raw_box
        self.centroid_history.append(new_centroid)
        max_history = 30
        if len(self.centroid_history) > max_history:
            self.centroid_history = self.centroid_history[-max_history:]

        self.last_seen = now
        self.missed_frames = 0
        self.is_stale = False

        if confidence and confidence > 0.0:
            self.confidence_history.append(confidence)
            if len(self.confidence_history) > 10:
                self.confidence_history = self.confidence_history[-10:]

        self._update_plate_candidates(plate_text, confidence)

        return self.plate_number

    def _update_plate_candidates(self, plate_text: str, confidence: float | None) -> None:
        if plate_text and is_valid_ph_plate(plate_text) and confidence and confidence > 0.3:
            normal = normalize_plate(plate_text)
            self.plate_candidates[normal] = self.plate_candidates.get(normal, 0) + 1
            sorted_cands = sorted(self.plate_candidates.items(), key=lambda x: x[1], reverse=True)
            if len(sorted_cands) >= 2:
                top, second = sorted_cands[0], sorted_cands[1]
                if top[1] >= 2 and top[1] > second[1]:
                    self.plate_number = top[0]
            elif sorted_cands and not self.plate_number:
                self.plate_number = sorted_cands[0][0]

    def predict_next_position(self, dt: float = 0.05) -> dict[str, float]:
        if not self.smoothed_bbox:
            return {"x": 0.0, "y": 0.0, "width": 0.01, "height": 0.01}
        cx = self.smoothed_bbox["x"] + self.smoothed_bbox["width"] / 2.0
        cy = self.smoothed_bbox["y"] + self.smoothed_bbox["height"] / 2.0
        next_cx = cx + self.velocity[0] * dt
        next_cy = cy + self.velocity[1] * dt
        return _clamp_box({
            "x": next_cx - self.smoothed_bbox["width"] / 2.0,
            "y": next_cy - self.smoothed_bbox["height"] / 2.0,
            "width": self.smoothed_bbox["width"],
            "height": self.smoothed_bbox["height"],
        })

    def average_confidence(self) -> float:
        if not self.confidence_history:
            return 0.0
        return sum(self.confidence_history) / len(self.confidence_history)

    def should_prune(self, now: datetime | None = None) -> bool:
        if now is None:
            now = timezone.now()
        age = (now - self.last_seen).total_seconds()
        return age > TRACK_TTL_SECONDS and self.missed_frames > MAX_MISSED_FRAMES

    def in_cooldown(self, now: datetime | None = None) -> bool:
        if self.last_scanned is None:
            return False
        if now is None:
            now = timezone.now()
        return (now - self.last_scanned).total_seconds() < PLATE_COOLDOWN_SECONDS

    def mark_scanned(self, when: datetime | None = None) -> None:
        if when is None:
            when = timezone.now()
        self.last_scanned = when

    def mark_missed(self) -> None:
        self.missed_frames += 1
        if self.missed_frames > MAX_MISSED_FRAMES:
            self.is_stale = True

    def should_run_ocr(self, ocr_interval_frames: int = 10) -> bool:
        if self.is_initializing:
            return True
        if not self.plate_number:
            return True
        return (self.frame_count - self.last_ocr_frame) >= ocr_interval_frames


class PlateTracker:
    def __init__(self) -> None:
        self._tracks: dict[int, TrackedPlate] = {}
        self._next_id: int = 1

    def update(self, detections: list[dict[str, Any]], now: datetime | None = None) -> list[dict[str, Any]]:
        if now is None:
            now = timezone.now()
        unmatched_dets = list(range(len(detections)))
        unmatched_tracks = list(self._tracks.keys())
        iou_matrix: list[list[tuple[float, int]]] = []

        for d_idx in unmatched_dets:
            det = detections[d_idx]
            row: list[tuple[float, int]] = []
            for t_id in unmatched_tracks:
                track = self._tracks[t_id]
                pred = track.predict_next_position()
                iou = _compute_iou(pred, det["bbox"])
                if iou < IOU_MATCH_THRESHOLD:
                    iou = 0.0
                row.append((iou, t_id))
            row.sort(key=lambda x: x[0], reverse=True)
            iou_matrix.append(row)

        greedy_matches: dict[int, int] = {}
        used_tracks: set[int] = set()
        for d_idx in unmatched_dets:
            row = iou_matrix[d_idx]
            for score, t_id in row:
                if score > 0.0 and t_id not in used_tracks:
                    greedy_matches[d_idx] = t_id
                    used_tracks.add(t_id)
                    break

        unmatched_det_indices = [i for i in unmatched_dets if i not in greedy_matches]
        for d_idx in unmatched_det_indices:
            det = detections[d_idx]
            det_centroid = _compute_centroid(det["bbox"])
            best_tid = None
            best_dist = CAMERA_MOTION_CENTROID_THRESHOLD * 2.5
            for t_id in unmatched_tracks:
                if t_id in used_tracks:
                    continue
                track = self._tracks[t_id]
                if track.is_initializing:
                    continue
                pred_bbox = track.predict_next_position()
                track_centroid = _compute_centroid(pred_bbox)
                dist = _compute_euclidean(det_centroid, track_centroid)
                if dist < best_dist:
                    best_dist = dist
                    best_tid = t_id
            if best_tid is not None:
                greedy_matches[d_idx] = best_tid
                used_tracks.add(best_tid)

        results: list[dict[str, Any]] = []
        for d_idx, det in enumerate(detections):
            det_bbox = det["bbox"]
            det_conf = det.get("confidence")
            raw_text = det.get("plate_text", "")

            if d_idx in greedy_matches:
                t_id = greedy_matches[d_idx]
                track = self._tracks[t_id]
                plate_text = track.record_detection(det_bbox, raw_text, det_conf)
                results.append({
                    "track_id": t_id,
                    "plate_text": plate_text,
                    "bbox": det_bbox,
                    "tracker_plate": plate_text,
                    "in_cooldown": track.in_cooldown(now),
                    "is_new_track": False,
                })
            else:
                new_track = self._create_track(det_bbox, raw_text, det_conf)
                results.append({
                    "track_id": new_track.track_id,
                    "plate_text": new_track.plate_number,
                    "bbox": det_bbox,
                    "tracker_plate": new_track.plate_number,
                    "in_cooldown": new_track.in_cooldown(now),
                    "is_new_track": True,
                })

        for t_id in list(self._tracks.keys()):
            matched = any(greedy_matches.get(i) == t_id for i in greedy_matches)
            if not matched:
                track = self._tracks[t_id]
                track.mark_missed()
                track.last_seen = now
                if track.should_prune(now):
                    del self._tracks[t_id]

        self._prune_stale(now)
        return results

    def _create_track(self, bbox: dict[str, float], plate_text: str, confidence: float | None) -> TrackedPlate:
        track_id = self._next_id
        self._next_id += 1
        track = TrackedPlate(track_id=track_id)
        track.record_detection(bbox, plate_text, confidence)
        self._tracks[track_id] = track
        return track

    def mark_scanned(self, track_id: int, when: datetime | None = None) -> None:
        track = self._tracks.get(track_id)
        if track:
            track.mark_scanned(when)

    def get_track(self, track_id: int) -> TrackedPlate | None:
        return self._tracks.get(track_id)

    def mark_all_missed(self) -> None:
        now = timezone.now()
        for track in self._tracks.values():
            track.mark_missed()
            track.last_seen = now

    def _prune_stale(self, now: datetime | None = None) -> None:
        if now is None:
            now = timezone.now()
        stale = [t_id for t_id, t in self._tracks.items() if t.should_prune(now)]
        for t_id in stale:
            del self._tracks[t_id]
            log.debug("[TRACKER] Pruned stale track_id=%d", t_id)

    def active_count(self) -> int:
        return len(self._tracks)

    def clear(self) -> None:
        self._tracks.clear()
