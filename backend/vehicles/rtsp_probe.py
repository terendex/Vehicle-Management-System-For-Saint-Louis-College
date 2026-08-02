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
from urllib.parse import quote

log = logging.getLogger(__name__)

RTSP_PORT = 554

# Seconds to wait for a single candidate before moving on. Long enough for a
# camera on a busy LAN to answer, short enough that four dead candidates do not
# leave the admin staring at a spinner.
PROBE_TIMEOUT_SECONDS = 6

# Reachability check before any candidate is tried: an unplugged camera would
# otherwise burn the full timeout once per candidate for no information.
CONNECT_TIMEOUT_SECONDS = 3


def candidate_urls(ip: str, device_id: str, password: str) -> list[dict]:
    """Candidate RTSP URLs for this camera, most likely first.

    `device_id` is whatever is printed on the unit. Several vendors ignore it
    for RTSP and authenticate as "admin" regardless, so both are tried.
    """
    ip = (ip or '').strip()
    dev = quote((device_id or '').strip(), safe='')
    pw = quote((password or '').strip(), safe='')

    def url(user, path):
        return f"rtsp://{user}:{pw}@{ip}{path}"

    users = [u for u in dict.fromkeys([dev, 'admin']) if u]   # dedupe, keep order

    out = []
    for user in users:
        out.append({'format': 'generic',   'url': url(user, '/stream1')})
        out.append({'format': 'dahua',     'url': url(user, '/cam/realmonitor?channel=1&subtype=0')})
        out.append({'format': 'hikvision', 'url': url(user, '/Streaming/Channels/101')})
        # Seen on ONVIF-generic and several budget units.
        out.append({'format': 'generic',   'url': url(user, '/live')})
        out.append({'format': 'generic',   'url': url(user, '/h264')})
        out.append({'format': 'generic',   'url': url(user, '/11')})
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


def detect(ip: str, device_id: str, password: str) -> dict:
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
            'attempts': [],
        }

    attempts = []
    for cand in candidate_urls(ip, device_id, password):
        if _opens(cand['url']):
            log.info('[rtsp-probe] %s matched %s', ip, cand['format'])
            return {
                'ok': True,
                'rtsp_url': cand['url'],
                'format': cand['format'],
                'attempts': attempts + [_redact(cand['url'])],
            }
        attempts.append(_redact(cand['url']))

    return {
        'ok': False,
        'error': ('The camera answered but none of the known stream paths worked. '
                  'The device ID or password may be wrong, or this model needs a '
                  'custom URL.'),
        'attempts': attempts,
    }
