"""Latest camera frame per gate, so any violation can carry a photo.

Evidence used to depend on where the violation happened to be raised: the
WebSocket scan path held the frame on the consumer instance, so REST scans and
the overstay sweeps had nothing to attach and their violations landed with no
photo at all. Those are exactly the ones somebody later has to judge — an
unattributed camera call that may need lifting as a false alarm.

The guard terminal publishes each frame here as it arrives; anything raising a
violation can ask for the newest one for that gate.

Deliberately in-memory and tiny:
  * one JPEG per gate, overwritten in place — bounded by gate count, not time;
  * a frame older than STALE_AFTER_SECONDS is refused rather than returned,
    because attaching a picture of an empty lane from ten minutes ago is worse
    than attaching nothing;
  * process-local, matching the campus/Railway split — the half that saw the
    vehicle is the half that raises the violation.
"""
import threading
import time

# A frame older than this is not evidence of anything current.
STALE_AFTER_SECONDS = 30.0

_lock = threading.Lock()
_frames: dict[str, tuple[bytes, float]] = {}


def set_latest_gate_frame(gate_id: str, jpeg_bytes: bytes) -> None:
    if not jpeg_bytes:
        return
    with _lock:
        _frames[gate_id or 'main'] = (jpeg_bytes, time.monotonic())


def latest_jpeg_for_gate(gate_id: str = 'main') -> "bytes | None":
    """Newest frame for this gate, or None if there is none or it is stale."""
    with _lock:
        entry = _frames.get(gate_id or 'main')
    if not entry:
        return None
    data, ts = entry
    if (time.monotonic() - ts) > STALE_AFTER_SECONDS:
        return None
    return data


def clear(gate_id: str | None = None) -> None:
    """Drop cached frames — used by tests and on guard sign-out."""
    with _lock:
        if gate_id is None:
            _frames.clear()
        else:
            _frames.pop(gate_id, None)
