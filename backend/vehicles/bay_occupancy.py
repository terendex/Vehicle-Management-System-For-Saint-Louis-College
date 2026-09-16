"""Bay occupancy without a neural network.

The camera is bolted down and the bays are drawn once, which means the most
informative thing available is *what this exact bay looks like empty*. An admin
captures that reference when the lot is clear, and from then on a bay is judged
by how far it has moved from its own baseline — no model, no inference, just
arithmetic on a crop.

Three signals, because no single one survives a campus lot all day:

  mean abs diff  How far the bay's pixels have moved from the baseline's, on
                 average. Blunt, and the one that actually answers the question:
                 on the real bays it read 67 with a motorcycle parked and 19
                 empty, while the other two read the same on both. It counts
                 double.
  histogram      Correlation against the baseline crop. Falls when something
                 with a different tonal distribution is parked there — but also
                 when the ground itself changes colour, which is why it only
                 corroborates.
  edge density   How much fine detail the bay holds, against what it held empty.
                 On asphalt a vehicle adds panel lines, glass and shadow; on
                 gravel or leaf litter the ground is the detailed thing and a
                 vehicle's smooth bodywork covers it, so the density falls
                 instead. Either direction counts, but weakly: measured on a real
                 camera the *empty* bay showed the larger change.

The vote is weighted, and the weighting is the whole design. Three points are
required, the decisive signal scores two and the two corroborating ones score
one each. So nothing claims a bay by itself, and — the case this arithmetic
exists for — an empty bay whose ground has merely changed, where the histogram
and the edges both drift, reaches two and loses.

On top of that, both tonal signals are illumination-compensated: the frame's
global brightness shift against the baseline is subtracted before the bay is
compared, so a cloud cancels out and only a *local* change survives.

Cost
----
Per frame the work is one greyscale conversion and one Canny pass over the whole
image, then per bay a slice of that result. Nothing rescans the full frame per
bay (which is why the crops happen before `calcHist`, not after), and the
expensive part — rasterising polygon masks and measuring the baseline — happens
once per layout change in `prepare_zone`, not per frame. Cost is flat in the
number of vehicles present, which is the main thing it buys over running a
detector: an empty lot and a full one cost the same.
"""
from __future__ import annotations

import logging

import cv2
import numpy as np

log = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────
# Starting points, not truths. Every zone response carries the raw signals so
# these can be tuned against a real camera instead of guessed at twice.

# Change in edge-pixel density against the empty baseline, either way.
#
# Two corrections live here. It was a *rise* only, which cannot describe a bay
# floored with gravel or leaf litter: that ground is edge-dense when empty, and
# a vehicle parked on it covers the texture with smooth bodywork and drives the
# density down. And it used to count double, which made it decisive.
#
# It is neither reliable nor decisive. Measured on the real motorcycle bays,
# with one bay holding a bike and the other holding nothing:
#
#     M01, motorcycle parked : edge -0.0405   hist 0.626   mad 67.0
#     M02, empty             : edge -0.0514   hist 0.626   mad 19.0
#
# The histograms are identical and the *empty* bay shows the larger edge change.
# No threshold on either can tell these two apart; only the mean absolute
# difference does, and by a factor of three. So edges are corroboration now —
# one point, never enough on their own.
EDGE_DELTA_THR = 0.04
EDGE_VOTE_WEIGHT = 1

# Histogram correlation with the baseline crop, below which the bay is
# considered changed. 1.0 is identical. Corroboration, like edges: the readings
# above are the same on a taken bay and an empty one.
HIST_CORR_THR = 0.70

# Mean absolute greyscale difference from the baseline, 0-255, and the signal
# that actually answers the question — so it is the one that counts double.
#
# Measured after illumination compensation, so it is a local change and not the
# weather: the frame's global brightness shift is subtracted before it is taken.
# The threshold sits in the gap the real bays showed, 19 empty against 67 taken.
# It was 14.0, which every damp patch and shifting shadow cleared — that, plus
# the double weight then sitting on edges, is what let an empty bay read taken.
#
# Deliberately far above what a compensated lighting change leaves behind: a
# cloud leaves single digits, not forties (the lighting tests hold that line).
MAD_THR = 40.0
MAD_VOTE_WEIGHT = 2

# Points required. No signal claims a bay alone — the decisive one scores 2 and
# still needs corroboration — and the two corroborating signals together score 2
# and cannot claim one either. That last pairing is the case this arithmetic
# exists for: an empty bay whose ground has changed colour, where the histogram
# and the edges both drift but the bay plainly holds nothing.
VOTES_REQUIRED = 3

CANNY_LO, CANNY_HI = 60, 160
HIST_BINS = 32

# A bay smaller than this many pixels either way is not measurable — a bad
# baseline or a mis-drawn box. Reported as free rather than guessed at.
MIN_BAY_PX = 6


class PreparedBay:
    """One bay's baseline, measured once so each frame only measures the live
    side."""

    __slots__ = ('space_id', 'space_number', 'rect', 'mask', 'mask_area',
                 'base_gray', 'base_edge_density', 'base_hist', 'usable')

    def __init__(self, space, rect, mask, base_gray, base_edges):
        self.space_id     = space.id
        self.space_number = space.space_number
        self.rect         = rect
        self.mask         = mask
        self.mask_area    = int(cv2.countNonZero(mask))
        self.base_gray    = base_gray
        self.usable       = self.mask_area > 0

        if self.usable:
            self.base_edge_density = (
                cv2.countNonZero(cv2.bitwise_and(base_edges, mask)) / self.mask_area
            )
            self.base_hist = _hist(base_gray, mask)
        else:
            self.base_edge_density = 0.0
            self.base_hist = None


class PreparedZone:
    """Every bay in a zone, prepared against one baseline frame."""

    __slots__ = ('bays', 'shape', 'signature', 'base_mean')

    def __init__(self, bays, shape, signature, base_mean=0.0):
        self.bays      = bays
        self.shape     = shape
        self.signature = signature
        # Whole-frame mean brightness of the baseline. The live frame's shift
        # against this is what gets subtracted before any tonal comparison.
        self.base_mean = base_mean


def _hist(gray, mask):
    h = cv2.calcHist([gray], [0], mask, [HIST_BINS], [0, 256])
    cv2.normalize(h, h, 0, 1, cv2.NORM_MINMAX)
    return h


def _rect_for(space, width: int, height: int):
    """The bay's pixel bounding box, clamped to the frame.

    Geometry is stored normalised 0-1 against the full frame, so this is the
    only place pixels enter the picture.
    """
    if space.points:
        xs = [p[0] for p in space.points]
        ys = [p[1] for p in space.points]
        nx1, nx2, ny1, ny2 = min(xs), max(xs), min(ys), max(ys)
    else:
        if space.x1 is None or space.x2 is None:
            return None
        nx1, nx2 = min(space.x1, space.x2), max(space.x1, space.x2)
        ny1, ny2 = min(space.y1, space.y2), max(space.y1, space.y2)

    x1 = max(0, min(width - 1, int(nx1 * width)))
    x2 = max(0, min(width, int(round(nx2 * width))))
    y1 = max(0, min(height - 1, int(ny1 * height)))
    y2 = max(0, min(height, int(round(ny2 * height))))

    if (x2 - x1) < MIN_BAY_PX or (y2 - y1) < MIN_BAY_PX:
        return None
    return (x1, y1, x2, y2)


def _mask_for(space, rect, width: int, height: int):
    """A mask local to `rect`: the pen tool's polygon filled in, or the whole
    rectangle for a box-drawn bay.

    The polygon matters. Without it the denominator would include asphalt
    outside the bay the admin actually drew, which on angled layouts is most of
    the bounding box.
    """
    x1, y1, x2, y2 = rect
    mask = np.zeros((y2 - y1, x2 - x1), dtype=np.uint8)

    if space.points:
        poly = np.array(
            [[int(px * width) - x1, int(py * height) - y1] for px, py in space.points],
            dtype=np.int32,
        )
        cv2.fillPoly(mask, [poly], 255)
    else:
        mask[:] = 255
    return mask


def layout_signature(spaces, baseline_token, shape) -> tuple:
    """Identity of a prepared zone. Any change to the bays, the baseline image or
    the frame size invalidates the cached preparation."""
    return (
        baseline_token,
        shape,
        tuple(sorted((s.id, str(s.updated_at)) for s in spaces)),
    )


def prepare_zone(baseline_bgr, spaces, shape, baseline_token='') -> PreparedZone:
    """Measure the empty baseline once, per bay.

    `shape` is the live frame's (height, width). A baseline captured at a
    different resolution is resized to match rather than rejected — the geometry
    is normalised, so the two only need to agree on aspect, and refusing here
    would blind a zone over a camera profile change.
    """
    height, width = shape[:2]
    if baseline_bgr.shape[0] != height or baseline_bgr.shape[1] != width:
        baseline_bgr = cv2.resize(baseline_bgr, (width, height),
                                  interpolation=cv2.INTER_AREA)

    base_gray  = cv2.cvtColor(baseline_bgr, cv2.COLOR_BGR2GRAY)
    base_edges = cv2.Canny(base_gray, CANNY_LO, CANNY_HI)

    bays = []
    for space in spaces:
        rect = _rect_for(space, width, height)
        if rect is None:
            continue
        x1, y1, x2, y2 = rect
        mask = _mask_for(space, rect, width, height)
        bay = PreparedBay(space, rect, mask,
                          base_gray[y1:y2, x1:x2],
                          base_edges[y1:y2, x1:x2])
        if bay.usable:
            bays.append(bay)

    return PreparedZone(bays, (height, width),
                        layout_signature(spaces, baseline_token, (height, width)),
                        base_mean=float(np.mean(base_gray)))


def score_votes(edge_delta: float, hist_corr: float, mad: float) -> int:
    """Points for one bay's three readings — the whole verdict, in one place.

    Split out of `evaluate` so the rule can be checked against signals measured
    off a real camera. Reproducing a given trio of readings from a synthetic
    image is guesswork; asserting the arithmetic on the numbers a real bay
    actually produced is not.
    """
    return ((MAD_VOTE_WEIGHT if mad >= MAD_THR else 0)
            + (1 if hist_corr < HIST_CORR_THR else 0)
            + (EDGE_VOTE_WEIGHT if abs(edge_delta) >= EDGE_DELTA_THR else 0))


def evaluate(prepared: PreparedZone, frame_bgr) -> dict:
    """Score every prepared bay against one live frame.

    Returns ``{space_id: {occupied, edge_delta, hist_corr, mad, votes}}``. The
    raw signals ride along deliberately — thresholds this cheap are only
    tunable if the numbers behind them are visible.
    """
    gray  = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    if gray.shape != prepared.shape:
        gray = cv2.resize(gray, (prepared.shape[1], prepared.shape[0]),
                          interpolation=cv2.INTER_AREA)
    edges = cv2.Canny(gray, CANNY_LO, CANNY_HI)

    # How much brighter or darker the whole scene is than when the baseline was
    # captured. Subtracting it makes the tonal signals measure a *local* change
    # instead of the weather. One mean over the frame, once — not per bay.
    illum = float(np.mean(gray)) - prepared.base_mean

    results = {}
    for bay in prepared.bays:
        x1, y1, x2, y2 = bay.rect
        gray_crop = gray[y1:y2, x1:x2]
        edge_crop = edges[y1:y2, x1:x2]

        if abs(illum) >= 1.0:
            compensated = np.clip(gray_crop.astype(np.int16) - illum,
                                  0, 255).astype(np.uint8)
        else:
            compensated = gray_crop

        live_density = cv2.countNonZero(cv2.bitwise_and(edge_crop, bay.mask)) / bay.mask_area
        edge_delta   = live_density - bay.base_edge_density
        hist_corr    = float(cv2.compareHist(_hist(compensated, bay.mask),
                                             bay.base_hist, cv2.HISTCMP_CORREL))
        mad          = float(cv2.mean(cv2.absdiff(compensated, bay.base_gray),
                                      mask=bay.mask)[0])

        votes = score_votes(edge_delta, hist_corr, mad)

        results[bay.space_id] = {
            'occupied':   votes >= VOTES_REQUIRED,
            'edge_delta': round(edge_delta, 4),
            'hist_corr':  round(hist_corr, 4),
            'mad':        round(mad, 2),
            'illum':      round(illum, 2),
            'votes':      votes,
        }
    return results
