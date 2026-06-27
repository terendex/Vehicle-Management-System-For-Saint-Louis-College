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
    class_name: str = ""
    vehicle_type: Optional[str] = None


class ProximityTracker:
    # Match radius as a fraction of image width; minimum 80px floor.
    # 25% of 1280px = 320px — large enough to re-match a close-up motorcycle
    # whose centre shifts significantly between CPU-speed detection frames.
    MATCH_RADIUS_FRACTION = 0.25
    MATCH_RADIUS_MIN      = 80

    # At ~500ms/frame on CPU, 3 s only gives ~6 frames before a track drops.
    # 6 s keeps the track alive through brief detection gaps (passing occlusion,
    # model miss on one angle) without accumulating stale ghost tracks.
    EXPIRY_SECONDS = 6.0

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

    def update(self, detections: list[dict], img_w: int = 640) -> list[dict]:
        now = time.time()
        matched_track_ids = set()
        match_radius = max(self.MATCH_RADIUS_MIN, int(img_w * self.MATCH_RADIUS_FRACTION))

        for det in detections:
            bbox = det["bbox"]
            x, y, w, h = bbox["x"], bbox["y"], bbox["width"], bbox["height"]
            abs_bbox = [x, y, x + w, y + h]
            class_name = det.get("class_name", "")
            vehicle_type = det.get("vehicle_type")

            best_track_id = None
            best_dist = float("inf")

            for tid, track in self.tracks.items():
                if (now - track.last_seen) < self.EXPIRY_SECONDS:
                    dist = self._distance(abs_bbox, track.bbox)
                    if dist < best_dist and dist < match_radius:
                        best_dist = dist
                        best_track_id = tid

            if best_track_id is not None:
                track = self.tracks[best_track_id]
                track.bbox = abs_bbox
                track.last_seen = now
                track.class_name = class_name
                track.vehicle_type = vehicle_type
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
                    class_name=class_name,
                    vehicle_type=vehicle_type,
                )
                self.tracks[track_id] = track
                matched_track_ids.add(track_id)
                det["track_id"] = track_id
                det["plate_text"] = ""
                det["ocr_done"] = False

        # Expire old tracks
        expired = [
            tid for tid, t in self.tracks.items()
            if now - t.last_seen >= self.EXPIRY_SECONDS
        ]
        for tid in expired:
            del self.tracks[tid]

        # Include active tracks not seen this frame so the frontend keeps
        # showing their bboxes through momentary detection gaps.
        result = list(detections)
        for tid, track in self.tracks.items():
            if tid not in matched_track_ids:
                x1, y1, x2, y2 = track.bbox
                result.append({
                    "track_id":    tid,
                    "bbox":        {"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1},
                    "plate_text":  track.plate_text,
                    "ocr_done":    track.ocr_done,
                    "class_name":  track.class_name,
                    "vehicle_type": track.vehicle_type,
                })

        return result

    def set_plate_text(self, track_id: int, plate_text: str):
        if track_id in self.tracks:
            self.tracks[track_id].plate_text = plate_text
            self.tracks[track_id].ocr_done = True

    def get_track(self, track_id: int) -> Optional[PlateTrack]:
        return self.tracks.get(track_id)