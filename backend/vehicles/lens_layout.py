"""Multi-lens cameras: one RTSP stream carrying more than one picture.

A dual-lens unit does not open two streams. It packs both of its views into a
single frame, stacked vertically — the campus Yoosee sends 1920x2160, which is
two 1920x1080 pictures one above the other. Everything downstream that treats
that as *one* scene is being lied to: a detector sees two half-height views
squashed together, and a model trained on ordinary camera framing has to make
sense of a picture no camera ever produced.

So detection runs per lens, and the boxes are mapped back into full-frame
coordinates before anyone else sees them. That last part is what keeps this
change small: parking-space geometry was placed against the whole frame, and
the browser draws overlays against the whole frame, so both keep working
untouched while the model gets a normally-shaped scene.

The frontend applies the same rule in CameraContext.jsx to decide how many
viewports a camera needs. **The two heuristics must agree** — if they drift,
the boxes drawn on screen stop matching the picture under them.
"""
import logging

log = logging.getLogger(__name__)

# A stacked frame is taller than it is wide, and splits into halves that are
# themselves *widescreen* pictures. Both halves of the test matter:
#
#   1920x2160  two stacked 1080p views -> halves are 1920x1080 (1.78)  SPLIT
#    864x976   the same camera's sub-stream -> 864x488         (1.77)  SPLIT
#    960x1280  ambiguous: 4:3 portrait, or two 3:2 views?      (1.50)  LEAVE
#   1080x1920  a genuinely portrait-mounted camera -> 1080x960 (1.13)  LEAVE
#   1920x1080  an ordinary camera, not even taller than wide          LEAVE
#
# The threshold sits at 1.6 rather than somewhere looser because the two
# mistakes are not equally bad. Failing to split a stacked camera costs
# nothing new — it is exactly how the system behaved before any of this, and
# the picture is still all there. Splitting a camera that was merely mounted
# upright throws half its scene away with no error anywhere. So the rule only
# fires when the halves are unmistakably widescreen, and anything genuinely
# ambiguous is left whole.
_MIN_HALF_ASPECT = 1.6


def lens_count(frame) -> int:
    """How many pictures are packed into this frame."""
    if frame is None or getattr(frame, 'ndim', 0) < 2:
        return 1
    h, w = frame.shape[:2]
    if h <= w or h < 2:
        return 1
    return 2 if (w / (h / 2)) >= _MIN_HALF_ASPECT else 1


def lenses(frame):
    """Yield (index, sub-frame) for each picture in the frame.

    Row slices of a C-contiguous frame stay contiguous, so this costs nothing
    beyond the view itself.
    """
    count = lens_count(frame)
    if count == 1:
        yield 0, frame
        return
    lens_h = frame.shape[0] // count
    for i in range(count):
        yield i, frame[i * lens_h:(i + 1) * lens_h]


def _to_full_frame(bbox: dict, index: int, count: int) -> dict:
    """Rewrite a lens-relative normalised bbox as a full-frame one.

    Only the vertical axis moves: the lenses are stacked, so x and width are
    already whole-frame values.
    """
    return {
        'x':      bbox['x'],
        'width':  bbox['width'],
        'y':      (index + bbox['y']) / count,
        'height': bbox['height'] / count,
    }


def detect_across_lenses(frame, detector, *args, **kwargs) -> list[dict]:
    """Run `detector` on each lens and return detections in full-frame coords.

    A single-lens frame is passed straight through, so an ordinary camera pays
    nothing at all — not even a copy. A dual-lens frame costs two inferences
    instead of one, which is the price of the model seeing a real scene rather
    than two of them stacked.

    `crop` is left exactly as the detector produced it: it is cut from the lens
    the detection was found in, so its pixels are already right.
    """
    count = lens_count(frame)
    if count == 1:
        return detector(frame, *args, **kwargs)

    out: list[dict] = []
    for index, sub in lenses(frame):
        try:
            dets = detector(sub, *args, **kwargs)
        except Exception:
            log.exception('[lens] detector failed on lens %d of %d', index + 1, count)
            continue
        for det in dets:
            bbox = det.get('bbox')
            if bbox:
                det['bbox'] = _to_full_frame(bbox, index, count)
            det['lens'] = index          # which picture it was found in
            out.append(det)

    out.sort(key=lambda d: d.get('score', 0), reverse=True)
    return out
