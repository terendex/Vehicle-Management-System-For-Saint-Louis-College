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

Shadows, and the vote's blind spot
----------------------------------
All three signals above are tonal — they measure how much the bay's brightness
moved — and MAD alone is worth two of the three points needed. So MAD plus the
histogram is a claim, and a shadow moves exactly those two together. The vote's
stated safety ("the histogram and the edges both drift and reach only two")
never covered that pair. Measured on an empty bay, a shadow 30 grey levels deep
claims it outright. Compensation does not help: it subtracts a *frame*-wide
mean, and a shadow over one bay barely moves that.

What separates the two is not how much the bay changed but *what* changed. A
shadow is a change in illumination — it scales the light coming off the ground
and leaves the ground's own grain exactly where it was. A vehicle is a change of
surface: the grain is gone, covered by bodywork. Correlation against the
baseline is invariant to the first and destroyed by the second, so a fourth
measurement — the median per-block correlation, see `block_ncc` — reads the
difference directly where no tonal measure can.

It is a VETO rather than a fourth vote, because it is a necessary condition and
not evidence: if the bay's structure still matches the empty baseline then the
bay is empty, whatever its brightness is doing. That leaves the three-point vote
untouched. A bay whose ground has too little texture to correlate switches the
veto off and scores on tone alone, exactly as it did before.

Per-bay thresholds
------------------
The two thresholds that used to be global are now derived per bay from that
bay's own noise when empty. A bay under a tree, or one a gate sweeps a shadow
across, is far noisier than a sheltered bay on flat asphalt, and a single global
number has to be set for the worst of them — which is what made every quiet bay
needlessly deaf. Each bay's threshold is `mean + K*std` of its own empty
readings, clamped into a band whose CEILING is the old global value, so no bay
is ever *less* sensitive than it was and a quiet bay becomes much more so.

Live baseline
-------------
The captured baseline is never written to. Scoring instead runs against a *live*
baseline that starts as a copy of it and creeps toward the current scene, but
only while the bay is confidently empty — see `refresh_live`. That is what lets
a bay survive a season's worth of slow change (sun bleaching the paint, a puddle
drying, the camera's gain settling) without an admin re-capturing it, while the
conditions on the refresh make it impossible for a parked car to be absorbed.

Cost
----
Per frame the work is one greyscale conversion and one Canny pass over the whole
image, then per bay a slice of that result. Nothing rescans the full frame per
bay (which is why the crops happen before `calcHist`, not after), and the
expensive part — rasterising polygon masks and measuring the baseline — happens
once per layout change in `prepare_zone`, not per frame. Cost is flat in the
number of vehicles present, which is the main thing it buys over running a
detector: an empty lot and a full one cost the same. The refresh above adds one
more greyscale+Canny pass, but only on the rare frame where a bay is actually
due a blend (at most one per bay per REFRESH_INTERVAL_SECONDS).

The shadow veto is the one measurement `evaluate` does not already have in hand,
so it is taken only where it can change something: on a bay the vote has just
claimed, and on one clear frame in NCC_SAMPLE_EVERY to learn what the bay
correlates at when empty. `block_ncc` scores the whole grid in four array passes
rather than looping its 64 blocks — 0.37ms against 3.3ms, which is the
difference between the veto being free and being the most expensive thing here.
Measured at 1920x1080 with 24 bays: 13.6ms a frame against 12.8ms without the
veto at all, and 23.4ms in the worst case where every bay scores taken at once.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque

import cv2
import numpy as np

log = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────
# Starting points, not truths. Every zone response carries the raw signals so
# these can be tuned against a real camera instead of guessed at twice.

# Histogram correlation with the baseline crop, below which the bay is
# considered changed. 1.0 is identical. Corroboration, like edges: the readings
# below are the same on a taken bay and an empty one.
#
# Deliberately still a fixed global while the other two went per-bay. It is a
# *correlation* — already normalised by the bay's own tonal spread — so it does
# not carry the per-bay scale that makes a raw MAD or an edge density
# incomparable between a shaded bay and a sunlit one. Raised 0.70 → 0.80 as part
# of making the scorer more sensitive: it is one corroborating point and can
# never claim a bay on its own, so the cost of loosening it is bounded by the
# vote.
HIST_CORR_THR = 0.80

# ── Mean absolute greyscale difference from the baseline, 0-255 ───────────────
#
# The signal that actually answers the question — so it is the one that counts
# double. Measured after illumination compensation, so it is a local change and
# not the weather: the frame's global brightness shift is subtracted before it
# is taken.
#
# The CEILING is the old global value. The real bays read 19 empty against 67
# taken, and 40 sat in that gap; it is kept as the most a bay's threshold may
# ever be, so this change cannot make any bay less sensitive than it was. The
# FLOOR is what a bay may drop to when its own empty readings are very quiet —
# below it the scorer would be chasing sensor noise even on a still bay.
MAD_CEILING = 40.0
MAD_FLOOR   = 20.0
MAD_NOISE_K = 4.0
MAD_VOTE_WEIGHT = 2

# What a bay with a baseline but no collected noise stats uses, until the
# refresh process collects some. Midway between floor and ceiling: more
# sensitive than the old global, without claiming a quietness that has not been
# measured on that bay.
MAD_FALLBACK = 28.0

# ── Change in edge-pixel density against the empty baseline, either way ───────
#
# Two corrections live in this signal. It was a *rise* only, which cannot
# describe a bay floored with gravel or leaf litter: that ground is edge-dense
# when empty, and a vehicle parked on it covers the texture with smooth bodywork
# and drives the density down. And it used to count double, which made it
# decisive.
#
# It is neither reliable nor decisive. Measured on the real motorcycle bays,
# with one bay holding a bike and the other holding nothing:
#
#     M01, motorcycle parked : edge -0.0405   hist 0.626   mad 67.0
#     M02, empty             : edge -0.0514   hist 0.626   mad 19.0
#
# The histograms are identical and the *empty* bay shows the larger edge change.
# No threshold on either can tell these two apart; only the mean absolute
# difference does, and by a factor of three. So edges are corroboration — one
# point, never enough on their own.
EDGE_CEILING = 0.04
EDGE_FLOOR   = 0.015
EDGE_NOISE_K = 4.0
EDGE_VOTE_WEIGHT = 1
EDGE_FALLBACK = 0.025

# Backward-compatible names for the two thresholds that used to be global.
#
# They are the CEILINGS now, not the values in force: a bay scores against its
# own `mad_thr` / `edge_thr`. Kept because they are the documented meaning of
# "the old global value", which several tests assert against, and because a bare
# `score_votes(...)` still defaults to them — a call with no per-bay thresholds
# behaves exactly as it did before this change.
MAD_THR        = MAD_CEILING
EDGE_DELTA_THR = EDGE_CEILING

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

# ── Shadow veto ───────────────────────────────────────────────────────────────
#
# The vote's blind spot, and the reason this exists.
#
# Three points are needed and MAD is worth two, so MAD plus the histogram is a
# claim. Both of those are TONAL — they measure how much the bay's brightness
# moved — so a shadow moves them together and lands on exactly three points. The
# arithmetic's stated safety ("the histogram and the edges both drift and reach
# only two") never covered that pair. Measured on an empty bay: a shadow
# darkening it by 30 grey levels reads MAD 25 with the histogram inverted, and
# claims a bay with nothing in it. A hard shadow edge is also literally an edge,
# so it often takes the third point on its own merits too.
#
# What separates the two is not how much the bay changed but WHAT changed. A
# shadow is a change in illumination: it scales the light coming off the ground
# and leaves the ground's own grain exactly where it was. A vehicle is a change
# of surface: the grain is gone, replaced by bodywork. Correlation between the
# live crop and the baseline crop is invariant to the first and destroyed by the
# second, so it reads the difference directly where every tonal measure cannot.
#
# Measured, median over blocks, empty-bay correlation normalised to 1.0:
#
#     uniform shadow, any depth      0.99   of the bay's empty correlation
#     shadow across part of the bay  0.52 - 0.99
#     any vehicle, any colour        0.00 - 0.01
#
# It is a VETO, not a fourth vote, because it is a necessary condition rather
# than evidence: if the bay's structure still matches the empty baseline then
# the bay is empty, whatever its brightness is doing. That also leaves the
# three-point vote exactly as it was.

# Blocks per side. Per-block rather than whole-bay because a shadow edge cutting
# across a bay changes the bay's overall pattern while leaving each block
# internally, uniformly lit — whole-bay correlation drops to 0.18 on exactly the
# partial shadows this most needs to catch, and per-block stays at 0.90.
#
# Eight, not four, and that came from a real photograph. A real shadow edge is
# diagonal and slightly soft, not the clean vertical step a synthetic test
# produces, so at 4 blocks a side the edge runs THROUGH most blocks and none of
# them is uniformly lit any more. Measured on a real lot, a diagonal shadow over
# 55% of a bay: 0.06 at 4 blocks — indistinguishable from a car, and a false
# claim — against 0.53 at 8. Cars sit at 0.02 either way.
#
# The other half of the reason is amplitude. Deep shade scales the ground's
# texture down with everything else (0.27 on that photo) while sensor noise
# stays where it is, so correlation has to be taken over a patch small enough to
# be evenly lit but still large enough for the surviving texture to beat the
# noise. Eight is the coarsest setting that held on every real case.
NCC_BLOCKS = 8

# Fallbacks for bays too small to carve into NCC_BLOCKS: a 40px bay at 8 a side
# is 5px blocks, below NCC_MIN_BLOCK_PX, and every block would be skipped.
NCC_BLOCK_FALLBACKS = (8, 4, 2)

# A correlation needs this many blocks behind it to be a measurement rather than
# an accident of two or three.
NCC_MIN_BLOCKS = 6

# A block needs this many mask pixels to be worth correlating. Polygon bays
# clip blocks at their corners into slivers where correlation is noise.
NCC_MIN_BLOCK_PX = 25

# Veto when the bay's structure is still this much of what it is when empty.
#
# A fraction of the bay's OWN learned correlation rather than a fixed number,
# because the figure is a property of the ground: coarse asphalt correlates at
# 0.90 when empty and a smooth apron at 0.37, and one threshold cannot serve
# both. A quarter is far above any vehicle (which lands near zero on both) and
# far below the weakest shadow (0.52 of empty).
NCC_VETO_FRACTION = 0.25

# Ground whose empty correlation is below this has no structure to speak of —
# nothing for a shadow to preserve or a vehicle to cover — so the measure means
# nothing and the veto is switched off for that bay. Better to fall back to the
# tonal vote alone, which is what the bay had before, than to act on noise.
NCC_MIN_BASE = 0.15

# How often the empty-bay correlation is re-measured while a bay is clear.
#
# Unlike MAD and the edge delta, this one is not free — `evaluate` does not
# already have it — so it is sampled every Nth clear frame instead of every
# frame, and computed in full only when a bay is about to be claimed. A bay is
# clear for most of its life, and its ground does not change texture by the
# second.
NCC_SAMPLE_EVERY = 10

# ── Noise sampling ────────────────────────────────────────────────────────────
#
# How a bay learns its own threshold. The readings fed in are the very ones
# scoring produces — `evaluate` already computes MAD and the edge delta, so
# sampling costs no extra image work at all, which is why it can run for as long
# as it likes.
NOISE_SAMPLE_FRAMES  = 50     # target sample count for a fresh baseline
NOISE_SAMPLE_SECONDS = 10.0   # and the window it is gathered over
# Below this a standard deviation is not a measurement, it is an accident of
# three frames. A bay with fewer samples than this keeps the fallback.
NOISE_MIN_SAMPLES    = 12
# Rolling window for the top-ups the refresh process contributes. Bounded so a
# bay that has been empty all afternoon reflects this afternoon, not last week.
NOISE_WINDOW         = 200

# ── Live baseline refresh ─────────────────────────────────────────────────────
#
# Every condition here exists to make one outcome impossible: a parked car being
# absorbed into the bay's idea of "empty", which would free the bay under it and
# send the next driver into an occupied space. So the bar is deliberately
# extreme — five unbroken minutes of a bay that not one frame has called changed
# — and the blend that follows is small enough that even a sustained mistake
# would take many minutes to matter.
REFRESH_CLEAR_SECONDS    = 300.0   # unclaimed AND hitless for this long first
REFRESH_INTERVAL_SECONDS = 30.0    # at most one blend this often
REFRESH_ALPHA            = 0.02    # live = (1-a)*live + a*current
# Mean frame brightness below which nothing is refreshed. At night the scene is
# mostly sensor noise and headlights, and blending that into a daytime baseline
# corrupts it for the morning.
REFRESH_MIN_BRIGHTNESS   = 40.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class NoiseStats:
    """One bay's empty-scene readings, and the thresholds they imply.

    A rolling window rather than a running mean: a bay's noise is a property of
    the season and the weather, and a mean over all time would still carry last
    month's rain. `NOISE_WINDOW` bounds how far back the threshold can be
    arguing from.
    """

    __slots__ = ('mad', 'edge', 'ncc', 'updated_at', '_lock')

    def __init__(self, mad=None, edge=None, ncc=None, updated_at=None):
        self.mad  = deque(mad or (),  maxlen=NOISE_WINDOW)
        self.edge = deque(edge or (), maxlen=NOISE_WINDOW)
        # What this bay's structure correlates at while it is EMPTY — the
        # reference the shadow veto is taken as a fraction of. Sampled far less
        # often than the other two, so it keeps its own smaller window.
        # One series per candidate grid. Which grid a bay ends up judged on is
        # a property of its ground, not something a constant can decide: fine
        # blocks survive a diagonal shadow edge but need texture to correlate,
        # coarse blocks need less texture but are defeated by that same edge.
        # Measuring both and letting the bay pick is what makes one setting work
        # on coarse asphalt and on a smooth concrete apron.
        ncc = ncc or {}
        self.ncc = {n: deque(ncc.get(n) or (), maxlen=NOISE_WINDOW // 4)
                    for n in NCC_BLOCK_FALLBACKS}
        self.updated_at = updated_at
        # The worker thread appends to these every frame while the diagnostics
        # endpoint summarises them from a request thread. A bounded deque pops
        # as it appends, so an unguarded read can land mid-mutation and raise
        # "deque mutated during iteration" — a 500 on a debugging screen, at the
        # exact moment someone is using it to work out what a bay is doing.
        self._lock = threading.Lock()

    def add(self, mad: float, edge_delta: float) -> None:
        """Fold in one empty-bay reading.

        The edge figure is stored as a magnitude because that is how it is
        compared: the vote is on `abs(edge_delta)`, since a vehicle may either
        add detail to asphalt or cover the texture of gravel. Keeping the sign
        here would put the mean near zero on a bay that drifts both ways and
        make the threshold far tighter than the bay's actual noise.
        """
        with self._lock:
            self.mad.append(float(mad))
            self.edge.append(abs(float(edge_delta)))

    def add_ncc(self, readings: dict) -> None:
        """Record what this bay correlates at while empty, per grid."""
        with self._lock:
            for n, v in readings.items():
                if n in self.ncc:
                    self.ncc[n].append(float(v))

    @property
    def ncc_choice(self) -> tuple:
        """(blocks, base correlation) this bay should be judged on.

        The FINEST grid whose empty correlation is still worth trusting, because
        fine blocks are the ones a real shadow edge — diagonal and a little soft
        — cannot defeat. A bay whose ground is too plain for 8 falls back to 4,
        and one too plain for any of them gets (0, 0.0) and no veto at all.

        Median rather than mean: one frame where something crossed the bay
        before the scorer called it taken would drag a mean down and quietly
        weaken the veto for every frame after.
        """
        with self._lock:
            series = {n: list(d) for n, d in self.ncc.items()}
        for n in NCC_BLOCK_FALLBACKS:
            vals = series.get(n) or []
            if len(vals) < NOISE_MIN_SAMPLES:
                continue
            base = float(np.median(vals))
            if base >= NCC_MIN_BASE:
                return n, base
        return 0, 0.0

    @property
    def ncc_base(self) -> float:
        return self.ncc_choice[1]

    @property
    def ncc_exhausted(self) -> bool:
        """Every grid has had a full window and none of them correlates.

        The honest end of the search: this bay's ground carries no structure the
        veto can use, and repeating the measurement will not change that.
        """
        with self._lock:
            full = [len(d) >= d.maxlen for d in self.ncc.values()]
        return bool(full) and all(full) and not self.ncc_choice[0]

    @property
    def samples(self) -> int:
        return min(len(self.mad), len(self.edge))

    @property
    def usable(self) -> bool:
        return self.samples >= NOISE_MIN_SAMPLES

    def summary(self) -> dict:
        """The persistable form: means, deviations and a count.

        Stored rather than the raw readings — a few hundred floats per bay would
        be a lot of row for a number that only moves slowly.
        """
        with self._lock:
            mad  = np.asarray(self.mad,  dtype=np.float64)
            edge = np.asarray(self.edge, dtype=np.float64)
            ncc_n = {n: len(d) for n, d in self.ncc.items()}
        # The three series are filled independently — the tonal pair every
        # frame, the correlation every NCC_SAMPLE_EVERY'th — so this reports
        # whichever exist. Returning early on an empty tonal pair used to drop a
        # measured correlation on the floor, which would have cost a restarting
        # zone its shadow veto.
        ncc_blocks, ncc_base = self.ncc_choice
        n = min(mad.size, edge.size)
        mad, edge = mad[:n], edge[:n]
        out = {
            'samples': int(n),
            # The shadow veto's reference, and the grid it belongs to. None
            # until the bay has had enough clear frames to measure it, and the
            # veto stays off until then.
            'ncc_base':    (round(ncc_base, 4) if ncc_blocks else None),
            'ncc_blocks':  ncc_blocks or None,
            'ncc_samples': max(ncc_n.values()) if ncc_n else 0,
            'updated_at':  self.updated_at,
        }
        if n:
            out.update({
                'mad_mean':  round(float(mad.mean()), 3),
                'mad_std':   round(float(mad.std()), 3),
                'edge_mean': round(float(edge.mean()), 5),
                'edge_std':  round(float(edge.std()), 5),
            })
        return out


def thresholds_for(stats: "dict | None") -> tuple:
    """(mad_thr, edge_thr, adaptive) for one bay's stored noise summary.

    `mean + K*std` is the ordinary "this reading is not noise" bar: four
    deviations above what the bay does while empty. Clamped at both ends, and
    the ceiling is the old global value — so the worst case of this whole change
    is a bay that behaves exactly as it did before.

    A bay with no stats, or too few to have a meaningful deviation, gets the
    fallback pair and is reported as NOT adaptive, so the diagnostics can say
    which bays are still running on an assumption.
    """
    if not stats or int(stats.get('samples') or 0) < NOISE_MIN_SAMPLES:
        return MAD_FALLBACK, EDGE_FALLBACK, False

    mad = _clamp(float(stats.get('mad_mean', 0.0))
                 + MAD_NOISE_K * float(stats.get('mad_std', 0.0)),
                 MAD_FLOOR, MAD_CEILING)
    edge = _clamp(float(stats.get('edge_mean', 0.0))
                  + EDGE_NOISE_K * float(stats.get('edge_std', 0.0)),
                  EDGE_FLOOR, EDGE_CEILING)
    return mad, edge, True


class PreparedBay:
    """One bay's baseline, measured once so each frame only measures the live
    side.

    Two baselines, not one. `base_*` is the admin's captured reference and is
    never written to — it is what a reset returns to and what the drift can
    always be judged against. `live_*` is what scoring actually compares
    against; it starts as a copy and creeps, under the conditions in
    `refresh_live`.
    """

    __slots__ = ('space_id', 'space_number', 'rect', 'mask', 'mask_area',
                 'base_gray', 'base_edge_density', 'base_hist', 'usable',
                 'live_gray', 'live_edge_density', 'live_hist',
                 'mad_thr', 'edge_thr', 'adaptive', 'noise',
                 'clear_since', 'refreshed_at', 'blends',
                 'ncc_base', 'ncc_seen', 'ncc_blocks')

    def __init__(self, space, rect, mask, base_gray, base_edges, stats=None):
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

        # The live baseline starts as an independent copy of the captured one.
        # A copy, not a reference: the blend writes into it, and the captured
        # baseline has to stay byte-identical to what the admin approved.
        self.live_gray         = base_gray.copy()
        self.live_edge_density = self.base_edge_density
        self.live_hist         = None if self.base_hist is None else self.base_hist.copy()

        self.noise = NoiseStats(updated_at=(stats or {}).get('updated_at'))
        self.mad_thr, self.edge_thr, self.adaptive = thresholds_for(stats)

        # What this bay correlates at while empty, and therefore what the shadow
        # veto is measured against. Restored from stored stats so a restart does
        # not leave the veto off through a whole afternoon of shadows; 0.0 means
        # "not established yet", and the veto stays off until it is.
        self.ncc_base   = float((stats or {}).get('ncc_base') or 0.0)
        self.ncc_blocks = int((stats or {}).get('ncc_blocks') or 0)
        self.ncc_seen   = 0

        # Refresh bookkeeping. `clear_since` is when this bay's current unbroken
        # run of confidently-empty frames began, or None if it is not in one.
        self.clear_since  = None
        self.refreshed_at = None
        self.blends       = 0
        self.ncc_seen     = 0

    def apply_stats(self, stats: "dict | None") -> None:
        """Re-derive this bay's thresholds from a noise summary."""
        self.mad_thr, self.edge_thr, self.adaptive = thresholds_for(stats)
        base   = (stats or {}).get('ncc_base')
        blocks = (stats or {}).get('ncc_blocks')
        if base and blocks:
            self.ncc_base, self.ncc_blocks = float(base), int(blocks)

    @property
    def veto_thr(self) -> float:
        """Correlation at or above which this bay's change is illumination only.

        0.0 disables the veto, which is what a bay whose ground has too little
        structure to correlate gets — there the tonal vote stands alone, exactly
        as it did before the veto existed.
        """
        if not self.ncc_blocks or self.ncc_base < NCC_MIN_BASE:
            return 0.0
        return NCC_VETO_FRACTION * self.ncc_base

    def reset_live(self) -> None:
        """Throw away the drift and go back to the captured baseline."""
        self.live_gray         = self.base_gray.copy()
        self.live_edge_density = self.base_edge_density
        self.live_hist         = None if self.base_hist is None else self.base_hist.copy()
        self.clear_since  = None
        self.refreshed_at = None
        self.blends       = 0


class PreparedZone:
    """Every bay in a zone, prepared against one baseline frame."""

    __slots__ = ('bays', 'shape', 'signature', 'base_mean', 'live_mean')

    def __init__(self, bays, shape, signature, base_mean=0.0):
        self.bays      = bays
        self.shape     = shape
        self.signature = signature
        # Whole-frame mean brightness of the baseline. The live frame's shift
        # against this is what gets subtracted before any tonal comparison.
        self.base_mean = base_mean
        # ...and its live counterpart, blended alongside the per-bay crops. It
        # has to drift with them: illumination compensation is measured against
        # whatever the baseline's own brightness was, so leaving this pinned to
        # the captured frame while the crops move would feed every bay a
        # correction for a baseline no longer in use.
        self.live_mean = base_mean

    def bay(self, space_id: int) -> "PreparedBay | None":
        for b in self.bays:
            if b.space_id == space_id:
                return b
        return None


def block_ncc(live_crop, base_crop, mask, blocks: int = NCC_BLOCKS) -> float:
    """Median per-block correlation between the bay now and the bay when empty.

    Pearson correlation is invariant to `a*I + b`, which is what a shadow does
    to a patch of ground — so a shadowed bay still correlates with its own empty
    baseline, while a vehicle, having replaced the surface, does not.

    The median over blocks, not the mean: one block genuinely covered (a wing
    mirror, a bollard's shadow) should not drag the figure down, and the median
    is what makes a partial shadow read as intact rather than as half a vehicle.

    Returns 0.0 when there is nothing to correlate — a flat crop has no
    structure, and a flat LIVE crop over textured ground is a surface that has
    been covered, which is the vehicle case and must never veto.
    """
    live = live_crop.astype(np.float32)
    base = base_crop.astype(np.float32)
    h, w = base.shape[:2]

    # Coarsen until the blocks are big enough to mean anything. A small bay, or
    # a polygon one whose mask clips most of its bounding box, cannot carry 8 a
    # side; taking the finest grid that still yields real blocks keeps one
    # constant working for a motorcycle bay and a bus bay alike.
    candidates = ([blocks] if blocks not in NCC_BLOCK_FALLBACKS
                  else [n for n in NCC_BLOCK_FALLBACKS if n <= blocks])
    for n in candidates:
        bh, bw = h // n, w // n
        if bh < 1 or bw < 1:
            continue

        # All n*n blocks at once. The loop this replaces ran 64 times per bay
        # per call and spent most of itself in numpy call overhead on a few
        # hundred pixels at a time; folding the grid into the array shape does
        # the same arithmetic in four passes over the crop.
        sel = (slice(0, bh * n), slice(0, bw * n))
        def _blocks(arr):
            return (arr[sel].reshape(n, bh, n, bw)
                            .transpose(0, 2, 1, 3)
                            .reshape(n * n, bh * bw))

        m = _blocks(mask.astype(np.float32)) > 0
        cnt = m.sum(axis=1)
        keep = cnt >= NCC_MIN_BLOCK_PX
        if int(keep.sum()) < NCC_MIN_BLOCKS:
            continue

        a = _blocks(live)[keep]
        b = _blocks(base)[keep]
        mk = m[keep].astype(np.float32)
        cnt = cnt[keep].astype(np.float32)

        # Means over masked pixels only, then centre and zero the rest so the
        # sums below see nothing outside the bay's polygon.
        a = (a - ((a * mk).sum(1) / cnt)[:, None]) * mk
        b = (b - ((b * mk).sum(1) / cnt)[:, None]) * mk

        da = np.sqrt((a * a).sum(1))
        db = np.sqrt((b * b).sum(1))
        ok = (da > 1e-6) & (db > 1e-6)
        vals = np.zeros(a.shape[0], dtype=np.float32)
        vals[ok] = (a[ok] * b[ok]).sum(1) / (da[ok] * db[ok])
        return float(np.median(vals))

    # Nothing measurable. 0.0 never vetoes, so a bay that cannot be correlated
    # falls back to the tonal vote rather than being held free on no evidence.
    return 0.0


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
    the frame size invalidates the cached preparation.

    Note what is NOT in here: a bay's noise stats. They are re-derived in place
    by `apply_stats`, because rebuilding the zone would throw away every bay's
    live baseline and restart its refresh clock — and the stats are written
    during exactly the long quiet spells that the refresh needs to survive.
    """
    return (
        baseline_token,
        shape,
        tuple(sorted((s.id, str(s.updated_at)) for s in spaces)),
    )


def prepare_zone(baseline_bgr, spaces, shape, baseline_token='',
                 noise_stats=None) -> PreparedZone:
    """Measure the empty baseline once, per bay.

    `shape` is the live frame's (height, width). A baseline captured at a
    different resolution is resized to match rather than rejected — the geometry
    is normalised, so the two only need to agree on aspect, and refusing here
    would blind a zone over a camera profile change.

    `noise_stats` is {space_id: summary}; a space's own `noise_stats` attribute
    is used when the map does not carry one, so a caller holding model instances
    need pass nothing.
    """
    height, width = shape[:2]
    if baseline_bgr.shape[0] != height or baseline_bgr.shape[1] != width:
        baseline_bgr = cv2.resize(baseline_bgr, (width, height),
                                  interpolation=cv2.INTER_AREA)

    base_gray  = cv2.cvtColor(baseline_bgr, cv2.COLOR_BGR2GRAY)
    base_edges = cv2.Canny(base_gray, CANNY_LO, CANNY_HI)
    stats_map  = noise_stats or {}

    bays = []
    for space in spaces:
        rect = _rect_for(space, width, height)
        if rect is None:
            continue
        x1, y1, x2, y2 = rect
        mask = _mask_for(space, rect, width, height)
        stats = stats_map.get(space.id, getattr(space, 'noise_stats', None))
        bay = PreparedBay(space, rect, mask,
                          base_gray[y1:y2, x1:x2],
                          base_edges[y1:y2, x1:x2],
                          stats=stats)
        if bay.usable:
            bays.append(bay)

    return PreparedZone(bays, (height, width),
                        layout_signature(spaces, baseline_token, (height, width)),
                        base_mean=float(np.mean(base_gray)))


def score_votes(edge_delta: float, hist_corr: float, mad: float,
                mad_thr: float = MAD_CEILING,
                edge_thr: float = EDGE_CEILING) -> int:
    """Points for one bay's three readings — the whole verdict, in one place.

    Split out of `evaluate` so the rule can be checked against signals measured
    off a real camera. Reproducing a given trio of readings from a synthetic
    image is guesswork; asserting the arithmetic on the numbers a real bay
    actually produced is not.

    The two thresholds default to the old global values, so a call that passes
    only the three readings means exactly what it did before they went per-bay.
    """
    return ((MAD_VOTE_WEIGHT if mad >= mad_thr else 0)
            + (1 if hist_corr < HIST_CORR_THR else 0)
            + (EDGE_VOTE_WEIGHT if abs(edge_delta) >= edge_thr else 0))


def evaluate(prepared: PreparedZone, frame_bgr, now: "float | None" = None,
             blocked_ids=()) -> dict:
    """Score every prepared bay against one live frame.

    Returns ``{space_id: {occupied, edge_delta, hist_corr, mad, votes, ...}}``.
    The raw signals ride along deliberately — thresholds this cheap are only
    tunable if the numbers behind them are visible.

    This also advances each bay's refresh bookkeeping, which makes it stateful:
    it expects to be called once per frame, by the one thread that owns this
    PreparedZone. `blocked_ids` are bays something is known to be standing in
    or near (a tracked vehicle, a person); they can still be scored, but they
    can never count as confidently empty.
    """
    now   = time.monotonic() if now is None else now
    gray  = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    if gray.shape != prepared.shape:
        gray = cv2.resize(gray, (prepared.shape[1], prepared.shape[0]),
                          interpolation=cv2.INTER_AREA)
    edges = cv2.Canny(gray, CANNY_LO, CANNY_HI)

    # How much brighter or darker the whole scene is than when the baseline was
    # captured. Subtracting it makes the tonal signals measure a *local* change
    # instead of the weather. One mean over the frame, once — not per bay.
    frame_mean = float(np.mean(gray))
    illum      = frame_mean - prepared.live_mean

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
        edge_delta   = live_density - bay.live_edge_density
        hist_corr    = float(cv2.compareHist(_hist(compensated, bay.mask),
                                             bay.live_hist, cv2.HISTCMP_CORREL))
        mad          = float(cv2.mean(cv2.absdiff(compensated, bay.live_gray),
                                      mask=bay.mask)[0])

        votes    = score_votes(edge_delta, hist_corr, mad, bay.mad_thr, bay.edge_thr)
        occupied = votes >= VOTES_REQUIRED

        # The shadow veto. Structure is not free the way the three tonal
        # readings are, so it is measured only where it can change something:
        # on a bay the vote has just claimed, and — to learn what this bay
        # correlates at when nothing is on it — on every NCC_SAMPLE_EVERY'th
        # frame of a clear run. A bay is clear for almost all of its life, so
        # the steady-state cost is one correlation per bay per ten frames.
        ncc  = None
        veto = False
        thr  = bay.veto_thr
        if occupied and thr > 0.0:
            ncc = block_ncc(gray_crop, bay.live_gray, bay.mask, bay.ncc_blocks)
            if ncc >= thr:
                # The ground's own grain is still there, unmoved. Whatever
                # changed the brightness of this bay did not put anything in it.
                occupied = False
                veto     = True

        # Confidently empty means: nothing scored, nothing standing there, and
        # enough light to judge by. Anything else restarts the five-minute
        # clock — a single frame is enough to do it, which is the point.
        if occupied or bay.space_id in blocked_ids or frame_mean < REFRESH_MIN_BRIGHTNESS:
            bay.clear_since = None
        elif bay.clear_since is None:
            bay.clear_since = now

        # Learn the empty-structure reference, but only from frames where the
        # bay is genuinely clear — nothing scored, nothing standing in it. A
        # vetoed frame is NOT one of those: it is a shadowed bay, and folding
        # its correlation in would drag the reference toward the shadowed value
        # and slowly disarm the veto that produced it.
        if not veto and bay.clear_since is not None and not occupied:
            bay.ncc_seen += 1
            # Staggered by bay id, so a zone's bays sample on different frames.
            # Without the offset every bay counts in step and they all correlate
            # on the same tenth frame — one frame in ten costing the whole
            # zone's worth of it at once, which is a stutter in the frame pacing
            # rather than the flat cost this is supposed to be.
            if (bay.ncc_seen + bay.space_id) % NCC_SAMPLE_EVERY == 0:
                # Every candidate grid until the bay has settled on one, then
                # only that one. Measuring all three for the life of the zone
                # tripled the scoring cost for an answer that does not change:
                # which grid a bay's ground supports is a property of the
                # ground, decided once and re-decided only when the baseline is
                # re-captured or the bay is reset.
                if bay.ncc_blocks:
                    grids = (bay.ncc_blocks,)
                elif bay.noise.ncc_exhausted:
                    # Tried every grid over a full window and none of them
                    # correlates: this bay's ground has no usable structure, so
                    # the veto is off for good and there is nothing left to
                    # learn. Without this a bay like that would keep paying for
                    # three correlations a sample for the life of the zone.
                    grids = ()
                else:
                    grids = NCC_BLOCK_FALLBACKS
                if grids:
                    bay.noise.add_ncc({
                        n: block_ncc(gray_crop, bay.live_gray, bay.mask, n)
                        for n in grids})

                # Adopt a choice only once this run has actually measured one,
                # or once it has proved there is none to make. A bare
                # assignment would clobber a reference restored from the stored
                # stats on the first sample after a restart — the in-memory
                # window is empty then, so `ncc_choice` has nothing to go on and
                # says "no grid", and a zone coming back at noon would throw
                # away its veto and spend the afternoon relearning it.
                blocks, base = bay.noise.ncc_choice
                if blocks:
                    bay.ncc_blocks, bay.ncc_base = blocks, base
                elif bay.noise.ncc_exhausted:
                    bay.ncc_blocks, bay.ncc_base = 0, 0.0

        results[bay.space_id] = {
            'occupied':   occupied,
            'edge_delta': round(edge_delta, 4),
            'hist_corr':  round(hist_corr, 4),
            'mad':        round(mad, 2),
            'illum':      round(illum, 2),
            'votes':      votes,
            # What the reading was judged against, so a bay reading "free" with
            # a car in it can be argued about with numbers.
            'mad_thr':    round(bay.mad_thr, 2),
            'edge_thr':   round(bay.edge_thr, 4),
            'adaptive':   bay.adaptive,
            'brightness': round(frame_mean, 1),
            # Structure, and what it was judged against. `shadow_veto` true
            # means the tonal vote claimed this bay and the veto overruled it —
            # the single most useful line when a bay that should be free is not.
            'ncc':         None if ncc is None else round(ncc, 3),
            'ncc_base':    round(bay.ncc_base, 3),
            'ncc_blocks':  bay.ncc_blocks,
            'veto_thr':    round(thr, 3),
            'shadow_veto': veto,
        }
    return results


def sample_noise(prepared: PreparedZone, results: dict) -> None:
    """Feed one frame's readings into the noise accumulators of empty bays.

    Costs nothing: these are the numbers `evaluate` just produced. Only bays in
    a confidently-empty run contribute, so a bay someone happens to be standing
    in cannot teach itself that people-shaped changes are normal.
    """
    for bay in prepared.bays:
        reading = results.get(bay.space_id)
        if reading is None or bay.clear_since is None or reading['occupied']:
            continue
        bay.noise.add(reading['mad'], reading['edge_delta'])


def refresh_due(prepared: PreparedZone, now: "float | None" = None) -> list:
    """The bays whose live baseline may be blended on this frame.

    Every condition from the module docstring is checked here rather than at the
    call site, so there is exactly one place that decides a bay is safe to learn
    from.
    """
    now = time.monotonic() if now is None else now
    due = []
    for bay in prepared.bays:
        if bay.clear_since is None:
            continue
        if now - bay.clear_since < REFRESH_CLEAR_SECONDS:
            continue
        if bay.refreshed_at is not None and now - bay.refreshed_at < REFRESH_INTERVAL_SECONDS:
            continue
        due.append(bay)
    return due


def refresh_live(prepared: PreparedZone, frame_bgr, bays, now: "float | None" = None) -> int:
    """Blend the current scene into the live baseline of each bay in `bays`.

    Every stored quantity is an exponential average of the *same* quantity
    measured the same way — the crop's pixels, its histogram, its edge density,
    and the frame mean behind illumination compensation. Recomputing the
    histogram and the edges from the blended crop instead would be subtly wrong:
    Canny over a small crop sees borders the full-frame pass never had, so a
    bay's density would step the moment it was first refreshed.

    Returns how many bays were blended.
    """
    if not bays:
        return 0
    now  = time.monotonic() if now is None else now
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    if gray.shape != prepared.shape:
        gray = cv2.resize(gray, (prepared.shape[1], prepared.shape[0]),
                          interpolation=cv2.INTER_AREA)
    edges = cv2.Canny(gray, CANNY_LO, CANNY_HI)

    a, inv = REFRESH_ALPHA, 1.0 - REFRESH_ALPHA
    frame_mean = float(np.mean(gray))

    for bay in bays:
        x1, y1, x2, y2 = bay.rect
        gray_crop = gray[y1:y2, x1:x2]
        edge_crop = edges[y1:y2, x1:x2]

        bay.live_gray = np.clip(
            inv * bay.live_gray.astype(np.float32) + a * gray_crop.astype(np.float32),
            0, 255).astype(np.uint8)
        density = cv2.countNonZero(cv2.bitwise_and(edge_crop, bay.mask)) / bay.mask_area
        bay.live_edge_density = inv * bay.live_edge_density + a * density
        bay.live_hist = (inv * bay.live_hist + a * _hist(gray_crop, bay.mask)).astype(
            bay.live_hist.dtype)

        bay.refreshed_at = now
        bay.blends      += 1

    prepared.live_mean = inv * prepared.live_mean + a * frame_mean
    return len(bays)


def reset_live_baseline(prepared: PreparedZone, space_id: "int | None" = None) -> int:
    """Throw away drift: one bay's live baseline, or every bay's, back to the
    captured reference. Returns how many bays were reset."""
    targets = (prepared.bays if space_id is None
               else [b for b in prepared.bays if b.space_id == space_id])
    for bay in targets:
        bay.reset_live()
    if space_id is None:
        prepared.live_mean = prepared.base_mean
    return len(targets)
