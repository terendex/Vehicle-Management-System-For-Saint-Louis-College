"""Work out a camera's RTSP URL by trying the known vendor paths against it.

Admins were being asked to pick the vendor (Generic / Dahua-IMOU / Hikvision /
Custom) from a form, which is knowledge about someone else's firmware that the
person mounting a camera should not need to have. The camera can answer the
question itself: open each candidate and keep the first that returns a frame.

Ordering matters — the list is cheapest-and-most-likely first, and probing
stops at the first success, so the common case costs one attempt rather than
four.
"""
import logging
import socket
import time
from urllib.parse import quote

log = logging.getLogger(__name__)

RTSP_PORT = 554

# Seconds to wait for a single candidate before moving on. A camera that has
# already answered on port 554 responds to a good path quickly; the wait only
# matters for paths it will refuse.
PROBE_TIMEOUT_SECONDS = 4

# Hard ceiling on the whole probe. The candidate list is long enough that
# walking all of it at the per-candidate timeout would take minutes, and an
# admin will assume the page has hung. Stop at the budget and hand back the
# suggested URL instead — they can still save the camera.
TOTAL_BUDGET_SECONDS = 25

# Reachability check before any candidate is tried: an unplugged camera would
# otherwise burn the full timeout once per candidate for no information.
CONNECT_TIMEOUT_SECONDS = 3


def paths_for(channel: int = 1) -> list[tuple[str, str]]:
    """Stream paths to try for one channel of a device.

    A single IP can host several cameras — an NVR, or a multi-lens unit — and
    they are distinguished only by the channel number inside the path. Channel 1
    is an ordinary standalone camera; 2+ are the extra views behind the same
    address.
    """
    ch = max(1, int(channel or 1))

    paths = [
        ('dahua',     f'/cam/realmonitor?channel={ch}&subtype=0'),
        # Sub-stream. Several IMOU units only serve the main stream to one
        # client at a time, so subtype=1 succeeds where subtype=0 is refused
        # as busy.
        ('dahua',     f'/cam/realmonitor?channel={ch}&subtype=1'),
        # Hikvision encodes channel and stream as one number: 101, 201, 301…
        ('hikvision', f'/Streaming/Channels/{ch}01'),
        ('hikvision', f'/Streaming/Channels/{ch}02'),   # sub-stream
        ('generic',   f'/live/ch{ch - 1}'),
        ('generic',   f'/ch{ch:02d}/0'),
        ('generic',   f'/onvif{ch}'),
        ('generic',   f'/video{ch}'),
        ('generic',   f'/{ch}1'),
        ('generic',   f'/{ch}2'),
    ]

    if ch == 1:
        # Single-camera shortcuts, meaningless for channel 2+ because they
        # carry no channel number and would just return channel 1 again.
        paths = [
            ('generic',   '/stream1'),
            *paths,
            ('generic',   '/live'),
            ('generic',   '/h264'),
            ('generic',   '/h264_stream'),
            ('generic',   '/'),          # some serve the default track
        ]
    return paths


def candidate_urls(ip: str, device_id: str, password: str = '',
                   channel: int = 1) -> list[dict]:
    """Candidate RTSP URLs for this camera, most likely first.

    With a password, the credentialed forms come first — an IMOU/Dahua unit
    refuses everything else, and it is the common case here. `device_id` is
    whatever is printed on the unit; several vendors ignore it for RTSP and
    expect "admin", so both usernames are tried.

    The credential-less form is kept as a tail so a genuinely open camera still
    resolves, and is tried first when no password was given at all.
    """
    ip = (ip or '').strip()
    dev = quote((device_id or '').strip(), safe='')
    pw = quote((password or '').strip(), safe='')
    users = [u for u in dict.fromkeys([dev, 'admin']) if u]

    if pw:
        prefixes = [f"{u}:{pw}@" for u in users]
        prefixes += ['']                       # open camera, password ignored
    else:
        prefixes = ['']
        prefixes += [f"{u}:@" for u in users]  # username with an empty password

    out = []
    for prefix in prefixes:
        for fmt, path in paths_for(channel):
            out.append({'format': fmt, 'url': f"rtsp://{prefix}{ip}{path}"})
    return out


def is_reachable(ip: str, port: int = RTSP_PORT,
                 timeout: float = CONNECT_TIMEOUT_SECONDS) -> bool:
    """TCP connect to the RTSP port — cheap proof the camera is even there."""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except Exception:
        return False


def _opens(url: str, timeout_s: int = PROBE_TIMEOUT_SECONDS) -> bool:
    """True when this URL yields an actual frame.

    A VideoCapture that merely `isOpened()` is not proof: FFmpeg reports open
    for URLs it will never decode. Reading a frame is the only honest test.
    """
    import cv2
    import os

    os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = (
        'rtsp_transport;tcp'
        f'|stimeout;{timeout_s * 1_000_000}'
        f'|timeout;{timeout_s * 1_000_000}'
        '|threads;1'
    )
    cap = None
    try:
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, timeout_s * 1000)
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, timeout_s * 1000)
        if not cap.isOpened():
            return False
        ok, frame = cap.read()
        return bool(ok and frame is not None and getattr(frame, 'size', 0) > 0)
    except Exception as exc:
        log.debug('[rtsp-probe] %s failed: %s', _redact(url), exc)
        return False
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass


def _redact(url: str) -> str:
    """Hide the password before a URL reaches a log line."""
    if '@' not in url or '://' not in url:
        return url
    scheme, rest = url.split('://', 1)
    creds, host = rest.split('@', 1)
    user = creds.split(':', 1)[0]
    return f"{scheme}://{user}:***@{host}"


def detect(ip: str, device_id: str, password: str = '', channel: int = 1) -> dict:
    """Find the working RTSP URL for this camera.

    Returns {'ok': True, 'rtsp_url', 'format', 'attempts'} on success, or
    {'ok': False, 'error', 'attempts'} explaining what was tried — an admin
    facing a camera that will not connect needs to see that, not a bare
    "detection failed".
    """
    ip = (ip or '').strip()
    if not ip:
        return {'ok': False, 'error': 'No IP address given.', 'attempts': []}

    if not is_reachable(ip):
        return {
            'ok': False,
            'error': (f'Nothing is answering on {ip}:{RTSP_PORT}. Check the camera is '
                      f'powered on and that this machine is on the same network.'),
            'suggestion': suggestion_for(ip, device_id, password, channel),
            'attempts': [],
        }

    attempts = []
    deadline = time.monotonic() + TOTAL_BUDGET_SECONDS
    for cand in candidate_urls(ip, device_id, password, channel):
        if time.monotonic() >= deadline:
            log.info('[rtsp-probe] %s gave up after %d attempts (budget reached)',
                     ip, len(attempts))
            break
        if _opens(cand['url']):
            log.info('[rtsp-probe] %s matched %s', ip, cand['format'])
            return {
                'ok': True,
                'rtsp_url': cand['url'],
                'format': cand['format'],
                'attempts': attempts + [_redact(cand['url'])],
            }
        attempts.append(_redact(cand['url']))

    # The camera is there but speaks a path we do not know. Hand back the most
    # likely URL so the camera can still be registered: an admin who cannot add
    # it at all is worse off than one holding a good guess.
    if (channel or 1) > 1:
        # Asking for channel 2+ on a single-lens camera fails exactly like a bad
        # password does, and that is the likelier mistake of the two.
        reason = (f'The camera answered, but it has no channel {channel} — or this '
                  f'model numbers its channels differently. A single-lens camera '
                  f'only has channel 1; use 2 or higher only for an NVR or a '
                  f'multi-lens unit. Check the password too.')
    else:
        reason = ('The camera answered but none of the known stream paths worked. '
                  'The device ID or password may be wrong, or this model uses a '
                  'path we do not know yet.')
    return {
        'ok': False,
        'error': reason,
        'suggestion': suggestion_for(ip, device_id, password, channel),
        'attempts': attempts,
    }


def suggestion_for(ip: str, device_id: str, password: str = '',
                   channel: int = 1) -> str:
    """Best-guess RTSP URL, used to prefill the manual field when probing fails.

    Dahua/IMOU shape with the device ID as the username: those units are the
    common case here and their path is the one an admin is least likely to know
    off-hand. The real password is filled in when there is one, so the field
    arrives ready to save rather than needing a placeholder swapped out.
    """
    ip = (ip or '').strip()
    dev = quote((device_id or '').strip(), safe='') or 'admin'
    pw = quote((password or '').strip(), safe='') or 'PASSWORD'
    ch = max(1, int(channel or 1))
    return f"rtsp://{dev}:{pw}@{ip}/cam/realmonitor?channel={ch}&subtype=0"
