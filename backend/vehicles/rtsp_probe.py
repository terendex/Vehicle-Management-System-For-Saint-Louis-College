"""Work out a camera's RTSP URL by trying the known vendor paths against it.

Admins were being asked to pick the vendor (Generic / Dahua-IMOU / Hikvision /
Custom) from a form, which is knowledge about someone else's firmware that the
person mounting a camera should not need to have. The camera can answer the
question itself: open each candidate and keep the first that returns a frame.

Ordering matters — the list is cheapest-and-most-likely first, and probing
stops at the first success, so the common case costs one attempt rather than
four.
"""
import hashlib
import logging
import re
import socket
import time
from urllib.parse import quote, unquote, urlsplit

log = logging.getLogger(__name__)

RTSP_PORT = 554

# Seconds to wait for a single candidate before moving on. Only spent on the
# one URL that reached the decode check — the search itself uses DESCRIBE.
PROBE_TIMEOUT_SECONDS = 4

# An RTSP DESCRIBE is a single request/response on a socket already proven open,
# so it answers in milliseconds on a LAN.
DESCRIBE_TIMEOUT_SECONDS = 2.5

# Hard ceiling on the whole probe, so a page never appears to hang.
#
# This budget used to be spent opening each candidate with OpenCV at 4 s a go,
# which covered 6 of the 30 candidates for a multi-channel device — and the
# `admin` username that NVRs actually want for RTSP sat at candidate 10, so it
# was never reached at all. DESCRIBE walks the whole list in a couple of
# seconds; only a candidate the camera has already accepted costs a decode.
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
        ('hikvision', f'/h264/ch{ch}/main/av_stream'),  # older firmware
        ('hikvision', f'/h264/ch{ch}/sub/av_stream'),
        # Uniview and the many NVRs that copy it
        ('uniview',   f'/unicast/c{ch}/s0/live'),
        ('uniview',   f'/unicast/c{ch}/s1/live'),
        ('uniview',   f'/media/video{ch}'),
        # TVT / Provision-ISR and relabels
        ('tvt',       f'/profile{ch}/media.smp'),
        ('generic',   f'/live/ch{ch - 1}'),
        ('generic',   f'/live/ch{ch}'),
        ('generic',   f'/ch{ch:02d}/0'),
        ('generic',   f'/ch{ch:02d}/1'),
        ('generic',   f'/onvif{ch}'),
        ('generic',   f'/video{ch}'),
        ('generic',   f'/stream{ch}'),
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

    # /stream1 arrives from both the shortcut list and /stream{ch} at channel 1;
    # probing the same path twice is pure waste.
    seen, unique = set(), []
    for fmt, path in paths:
        if path not in seen:
            seen.add(path)
            unique.append((fmt, path))
    return unique


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

    # Path-major, not credential-major. Grouping by credential put every
    # `admin` candidate behind all ten device-ID ones, and the probe's time
    # budget ran out long before it got there — which is exactly how an NVR
    # that wants `admin` ended up undetectable.
    out = []
    for fmt, path in paths_for(channel):
        for prefix in prefixes:
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


def _auth_header(method: str, target: str, user: str, pw: str, challenge: str) -> str:
    """Answer a WWW-Authenticate challenge. Digest where offered, else Basic."""
    if challenge.lower().lstrip().startswith('digest'):
        fields = dict(re.findall(r'(\w+)\s*=\s*"([^"]*)"', challenge))
        realm  = fields.get('realm', '')
        nonce  = fields.get('nonce', '')
        md5    = lambda s: hashlib.md5(s.encode()).hexdigest()   # noqa: E731
        ha1    = md5(f'{user}:{realm}:{pw}')
        ha2    = md5(f'{method}:{target}')
        resp   = md5(f'{ha1}:{nonce}:{ha2}')
        return (f'Digest username="{user}", realm="{realm}", nonce="{nonce}", '
                f'uri="{target}", response="{resp}"')
    import base64
    token = base64.b64encode(f'{user}:{pw}'.encode()).decode()
    return f'Basic {token}'


def _describe(url: str, timeout: float = DESCRIBE_TIMEOUT_SECONDS) -> int | None:
    """Send an RTSP DESCRIBE and return the status code the camera replies with.

    This is the search step. Opening a candidate with OpenCV costs seconds
    because FFmpeg waits for a decodable frame; DESCRIBE is one request on the
    port we already know is open, and it distinguishes the two failures that
    matter — 401 (credentials refused) from 404 (no such path or channel).

    Returns None when the socket itself failed.
    """
    parsed = urlsplit(url)
    host = parsed.hostname
    if not host:
        return None
    port = parsed.port or RTSP_PORT
    user = unquote(parsed.username or '')
    pw   = unquote(parsed.password or '')

    path = parsed.path or '/'
    if parsed.query:
        path = f'{path}?{parsed.query}'
    target = f'rtsp://{host}:{port}{path}'

    def _request(cseq: int, auth: str = '') -> str:
        lines = [f'DESCRIBE {target} RTSP/1.0',
                 f'CSeq: {cseq}',
                 'Accept: application/sdp',
                 'User-Agent: SLC-VMS']
        if auth:
            lines.append(f'Authorization: {auth}')
        return '\r\n'.join(lines) + '\r\n\r\n'

    def _status(head: str) -> int | None:
        m = re.match(r'RTSP/\d\.\d\s+(\d+)', head)
        return int(m.group(1)) if m else None

    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(_request(1).encode())
            head = sock.recv(4096).decode('utf-8', 'replace')
            code = _status(head)

            # Answer the challenge on the same connection when we have creds.
            if code == 401 and (user or pw):
                m = re.search(r'WWW-Authenticate:\s*(.+)', head, re.I)
                if m:
                    auth = _auth_header('DESCRIBE', target, user, pw, m.group(1).strip())
                    sock.sendall(_request(2, auth).encode())
                    head = sock.recv(4096).decode('utf-8', 'replace')
                    code = _status(head)
            return code
    except Exception as exc:
        log.debug('[rtsp-probe] DESCRIBE %s failed: %s', _redact(url), exc)
        return None


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

    attempts  = []
    accepted  = []          # candidates the camera answered 200 to
    codes     = []          # every status seen, for the diagnosis below
    deadline  = time.monotonic() + TOTAL_BUDGET_SECONDS

    # Phase 1 — ask every candidate. Cheap enough to walk the whole list.
    for cand in candidate_urls(ip, device_id, password, channel):
        if time.monotonic() >= deadline:
            log.info('[rtsp-probe] %s gave up after %d attempts (budget reached)',
                     ip, len(attempts))
            break
        code = _describe(cand['url'])
        codes.append(code)
        attempts.append(f"{_redact(cand['url'])} -> {code if code else 'no reply'}")
        if code == 200:
            accepted.append(cand)

    # Phase 2 — confirm one of them actually decodes. A few firmwares answer 200
    # to anything, so acceptance alone is not proof there is video behind it.
    for cand in accepted:
        if time.monotonic() >= deadline:
            break
        if _opens(cand['url']):
            log.info('[rtsp-probe] %s matched %s', ip, cand['format'])
            return {
                'ok': True,
                'rtsp_url': cand['url'],
                'format': cand['format'],
                'attempts': attempts,
            }

    # Nothing decoded. Say which wall we hit — "detection failed" sends an admin
    # to re-check a password that was never the problem.
    if accepted:
        reason = ('The camera accepted the stream address but no video could be '
                  'decoded from it. It may be streaming a format this server '
                  'cannot read, or another client may be holding the only '
                  'available connection. Registering it anyway is usually safe.')
    elif 401 in codes or 403 in codes:
        reason = ('The camera rejected the username and password. On an NVR the '
                  'RTSP login is usually "admin" plus the NVR password, which is '
                  'not always the same as the device ID or the app password.')
    elif (channel or 1) > 1 and (404 in codes or 400 in codes):
        reason = (f'The camera answered, but it has no channel {channel} — or it '
                  f'numbers its channels differently. A single-lens camera only '
                  f'has channel 1; use 2 or higher only for an NVR or a '
                  f'multi-lens unit.')
    elif 404 in codes or 400 in codes:
        reason = ('The camera answered but does not recognise any stream path we '
                  'know. Check its manual or app for the RTSP URL it publishes.')
    else:
        reason = ('The camera is reachable but did not answer any stream request. '
                  'RTSP may be disabled on the device, or it may serve it on a '
                  'port other than 554.')

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
