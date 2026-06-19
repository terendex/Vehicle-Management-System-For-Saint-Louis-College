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
from dataclasses import dataclass, field
from typing import Optional

from pathlib import Path

import cv2
import numpy as np


import logging

log = logging.getLogger(__name__)

_MIN_CONF_STORE = 0.20   # minimum confidence to record a read
_LOCK_CONF      = 0.70   # confidence above which we stop re-reading
_MAX_OCR_ATTEMPTS = 5    # fall back to best-so-far after this many reads


@dataclass
class Track:
    track_id: int
    bbox: tuple[int, int, int, int] = (0, 0, 0, 0)
    plate_text: str = ""
    plate_conf: float = 0.0
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


def detect_license_plates(img: np.ndarray) -> list[dict]:
    """Detect plates and vehicles — delegates to detection.py for CLAHE, tiling, and NMS."""
    from .detection import detect_plates
    try:
        raw = detect_plates(img)
    except Exception as e:
        log.error("[DETECT] YOLO error: %s", e)
        return []
    # Normalise bbox to (x, y, w, h) pixel tuple expected by the tracker
    out = []
    for d in raw:
        bx = d["bbox"]
        h, w = img.shape[:2]
        px = int(bx["x"] * w)
        py = int(bx["y"] * h)
        pw = int(bx["width"] * w)
        ph = int(bx["height"] * h)
        out.append({
            "bbox":  (px, py, pw, ph),
            "score": d["score"],
            "crop":  d["crop"],
        })
    return out


from .reader import _ocr_crop


class LPRPipeline:
    def __init__(self, source, iou_threshold: float = 0.3):
        self.stream = VideoStreamThread(source)
        self.tracker = VehicleTracker(iou_threshold=iou_threshold)
        # track_id → {text, conf, votes, attempts, locked}
        self.ocr_memory: dict[int, dict] = {}
        self.running = False

    def start(self):
        self.running = True
        self.stream.start()
        time.sleep(0.1)

    def process_frame(self, frame: np.ndarray) -> np.ndarray:
        detections = detect_license_plates(frame)
        tracks = self.tracker.update([(d["bbox"], d["score"]) for d in detections])

        for track in tracks:
            entry = self.ocr_memory.get(track.track_id)

            if entry and entry["locked"]:
                track.plate_text = entry["text"]
                track.plate_conf = entry["conf"]
                continue

            # Find matching detection crop for this track
            crop = None
            aspect_ratio = 1.0
            for d in detections:
                if d["bbox"] == track.bbox:
                    bw, bh = d["bbox"][2], d["bbox"][3]
                    aspect_ratio = bw / max(bh, 1)
                    crop = d["crop"]
                    break

            if crop is None or crop.size == 0:
                if entry:
                    track.plate_text = entry.get("text", "")
                    track.plate_conf = entry.get("conf", 0.0)
                continue

            text, conf = _ocr_crop(crop, aspect_ratio)
            if conf is None:
                conf = 0.0

            if entry is None:
                entry = {"text": "", "conf": 0.0, "votes": {}, "attempts": 0, "locked": False}
                self.ocr_memory[track.track_id] = entry

            entry["attempts"] += 1

            if text and conf >= _MIN_CONF_STORE:
                # Accumulate confidence-weighted votes across reads
                entry["votes"][text] = entry["votes"].get(text, 0.0) + conf
                best_text = max(entry["votes"], key=entry["votes"].get)
                entry["text"] = best_text
                entry["conf"] = conf

                if conf >= _LOCK_CONF:
                    entry["locked"] = True
                    log.info("[LPR] Locked plate %s (conf=%.2f) for track %d",
                             best_text, conf, track.track_id)

            if entry["attempts"] >= _MAX_OCR_ATTEMPTS and not entry["locked"]:
                entry["locked"] = True
                log.info("[LPR] Max attempts reached for track %d — locking '%s'",
                         track.track_id, entry.get("text", ""))

            track.plate_text = entry.get("text", "")
            track.plate_conf = entry.get("conf", 0.0)

        return self._draw_overlay(frame, tracks)

    def _draw_overlay(self, frame: np.ndarray, tracks: list[Track]) -> np.ndarray:
        for track in tracks:
            x, y, w, h = track.bbox
            cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
            cv2.putText(frame, f"ID:{track.track_id}", (x, y - 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            if track.plate_text:
                label = f"{track.plate_text} ({track.plate_conf:.0%})"
                cv2.putText(frame, label, (x, y - 20),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
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