"""
proximity_tracker.py — Lightweight 2 FPS-friendly plate tracker.

Uses proximity matching instead of motion vectors (too slow at 2 FPS).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PlateTrack:
    track_id: int
    bbox: list[int]
    plate_text: str = ""
    last_seen: float = 0.0
    ocr_done: bool = False


class ProximityTracker:
    MATCH_RADIUS = 100
    EXPIRY_SECONDS = 1.5
    next_id: int = 1

    def __init__(self):
        self.tracks: dict[int, PlateTrack] = {}

    def _center(self, bbox: list[int]) -> tuple[float, float]:
        x1, y1, x2, y2 = bbox
        return ((x1 + x2) / 2, (y1 + y2) / 2)

    def _distance(self, b1: list[int], b2: list[int]) -> float:
        cx1, cy1 = self._center(b1)
        cx2, cy2 = self._center(b2)
        return ((cx1 - cx2) ** 2 + (cy1 - cy2) ** 2) ** 0.5

    def update(self, detections: list[dict]) -> list[dict]:
        now = time.time()
        matched_track_ids = set()

        for det in detections:
            bbox = det["bbox"]
            x, y, w, h = bbox["x"], bbox["y"], bbox["width"], bbox["height"]
            abs_bbox = [x, y, x + w, y + h]

            best_track_id = None
            best_dist = float("inf")

            for tid, track in self.tracks.items():
                if (now - track.last_seen) < self.EXPIRY_SECONDS:
                    dist = self._distance(abs_bbox, track.bbox)
                    if dist < best_dist and dist < self.MATCH_RADIUS:
                        best_dist = dist
                        best_track_id = tid

            if best_track_id is not None:
                track = self.tracks[best_track_id]
                track.bbox = abs_bbox
                track.last_seen = now
                matched_track_ids.add(best_track_id)
                det["track_id"] = best_track_id
                det["plate_text"] = track.plate_text
                det["ocr_done"] = track.ocr_done
            else:
                track_id = self.next_id
                self.next_id += 1
                track = PlateTrack(
                    track_id=track_id,
                    bbox=abs_bbox,
                    last_seen=now,
                    ocr_done=False,
                )
                self.tracks[track_id] = track
                matched_track_ids.add(track_id)
                det["track_id"] = track_id
                det["plate_text"] = ""
                det["ocr_done"] = False

        expired = [
            tid for tid, t in self.tracks.items()
            if now - t.last_seen >= self.EXPIRY_SECONDS
        ]
        for tid in expired:
            del self.tracks[tid]

        return detections

    def set_plate_text(self, track_id: int, plate_text: str):
        if track_id in self.tracks:
            self.tracks[track_id].plate_text = plate_text
            self.tracks[track_id].ocr_done = True

    def get_track(self, track_id: int) -> Optional[PlateTrack]:
        return self.tracks.get(track_id)