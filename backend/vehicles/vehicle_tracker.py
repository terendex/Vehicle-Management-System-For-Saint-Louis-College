"""Frame-to-frame identity and stillness for vehicle boxes in a parking zone.

The parking loop saw each frame's detections as anonymous boxes, which left two
questions unanswerable:

  * Is this the same car as a moment ago, or a different one?
  * Has it come to rest, or is it still manoeuvring?

So everything downstream was phrased as "this *bay* looked taken for N
consecutive frames" — a statement about the bay, not the vehicle. That cannot
tell a parked car from one that has been inching across the line for six
seconds, which is exactly the case double-parking detection has to get right.
Tracking the box instead of the bay lets both occupancy and double parking wait
for the vehicle to actually stop.

Time, not frames. The old thresholds were counted in frames, and the loop reads
at ~10fps for detector scoring but only every couple of seconds when the classic
scorer is driving — so the same constant meant two different durations and had
to be converted between them. "Stationary for eight seconds" means the same
thing at any cadence.

Deliberately not the Kalman tracker in scanning/ml/tracking.py: that one is
built for vehicles crossing a gate at speed and carries plate crops, OCR
cooldowns and occlusion prediction, none of which a parking lot needs. Cars in
a lot barely move, so IoU matching is enough — and the one thing this has to
measure, how long a box has stayed put, is precisely what that tracker does not
keep. Its IoU helper is shared rather than reimplemented.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from scanning.ml.tracking import compute_iou

# Overlap between a new box and a known vehicle before they count as the same
# car. Parked cars barely move between observations, so this is generous.
IOU_MATCH_THRESHOLD = 0.3

# How far a box's centre may drift from where it settled, in fractions of the
# frame, before the vehicle counts as moving again. Detection boxes jitter by a
# few pixels even on a completely stationary car; on a 1920-wide frame this is
# roughly 38px of allowance, comfortably above that noise and well below the
# distance a car covers while parking.
STILL_RADIUS = 0.02

# A vehicle unseen for this long is gone, and a box appearing later is a new
# one. Long enough to ride out a detector that drops a frame or two, and it must
# stay comfortably above the interval the caller observes at, or every
# observation would start a fresh track with a fresh stillness clock.
TRACK_LOST_SECONDS = 6.0


def _centre(bbox: dict) -> tuple[float, float]:
    return bbox["x"] + bbox["width"] / 2.0, bbox["y"] + bbox["height"] / 2.0


def _as_tuple(bbox: dict) -> tuple[float, float, float, float]:
    """compute_iou's (x, y, w, h) form. It works in whatever units it is given,
    and everything here is normalised 0–1."""
    return bbox["x"], bbox["y"], bbox["width"], bbox["height"]


@dataclass
class VehicleTrack:
    """One vehicle, followed across observations."""
    track_id:    int
    bbox:        dict     # newest box, in the detector's normalised dict form
    first_seen:  float
    last_seen:   float
    # Where this vehicle settled, and when. Not the previous frame's box — see
    # observe() for why that distinction matters.
    anchor:      dict
    still_since: float

    def stationary_for(self, now: float) -> float:
        """Seconds this vehicle has been sitting still."""
        return max(0.0, now - self.still_since)

    def has_settled(self, now: float, seconds: float) -> bool:
        return self.stationary_for(now) >= seconds

    def observe(self, bbox: dict, now: float, still_radius: float) -> None:
        """Fold in this observation, restarting the clock if the vehicle moved.

        Displacement is measured from the anchor — the box the vehicle settled
        at — rather than from the previous observation. Comparing consecutive
        frames would let a car creeping forward a few pixels at a time never
        once exceed the threshold, so it would read as parked for the whole
        approach. Against a fixed anchor that drift accumulates and trips it.
        """
        self.bbox      = bbox
        self.last_seen = now

        ax, ay = _centre(self.anchor)
        cx, cy = _centre(bbox)
        if math.hypot(cx - ax, cy - ay) > still_radius:
            self.anchor      = bbox
            self.still_since = now


class VehicleTracker:
    """Keeps vehicle identity across observations and times how long each rests.

    Matching is greedy on IoU, best pair first, so the strongest overlap wins
    its track rather than whichever detection happened to be examined first.
    That costs O(detections × tracks) per update, which for a parking bay — a
    few dozen of each at worst — is far below the YOLO inference that produced
    the boxes.
    """

    def __init__(self,
                 lost_after:   float = TRACK_LOST_SECONDS,
                 still_radius: float = STILL_RADIUS,
                 iou_threshold: float = IOU_MATCH_THRESHOLD):
        self._lost_after   = lost_after
        self._still_radius = still_radius
        self._iou          = iou_threshold
        self._tracks: dict[int, VehicleTrack] = {}
        self._next_id = 1

    def update(self, detections: list, now: float) -> list[VehicleTrack]:
        """Fold one observation in; return the vehicles seen in it.

        Tracks not matched this time are kept — a detector that misses a car for
        a frame must not reset how long it has been parked — until they go stale.
        """
        # Each known track's box is converted once, not once per detection —
        # the comparison itself is unavoidably one per (detection, track) pair,
        # but rebuilding the same tuples inside that loop is not.
        known = [(tid, _as_tuple(t.bbox)) for tid, t in self._tracks.items()]

        pairs = []
        for d_idx, det in enumerate(detections):
            box = _as_tuple(det["bbox"])
            for tid, tbox in known:
                iou = compute_iou(box, tbox)
                if iou >= self._iou:
                    pairs.append((iou, d_idx, tid))
        pairs.sort(key=lambda p: p[0], reverse=True)

        matched: dict[int, int] = {}
        used_dets:   set[int] = set()
        used_tracks: set[int] = set()
        for _iou, d_idx, tid in pairs:
            if d_idx in used_dets or tid in used_tracks:
                continue
            used_dets.add(d_idx)
            used_tracks.add(tid)
            matched[d_idx] = tid

        seen: list[VehicleTrack] = []
        for d_idx, det in enumerate(detections):
            bbox = det["bbox"]
            tid  = matched.get(d_idx)
            if tid is None:
                track = VehicleTrack(
                    track_id=self._next_id, bbox=bbox,
                    first_seen=now, last_seen=now,
                    anchor=bbox, still_since=now,
                )
                self._tracks[track.track_id] = track
                self._next_id += 1
            else:
                track = self._tracks[tid]
                track.observe(bbox, now, self._still_radius)
            seen.append(track)

        for tid, track in list(self._tracks.items()):
            if now - track.last_seen > self._lost_after:
                del self._tracks[tid]

        return seen

    @property
    def tracks(self) -> dict[int, VehicleTrack]:
        return self._tracks

    def reset(self) -> None:
        """Forget everything. Used when the frame source changes underneath the
        tracker, where box positions are no longer comparable to the ones held."""
        self._tracks.clear()
