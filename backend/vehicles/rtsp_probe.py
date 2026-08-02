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
import threading
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
TOTAL_BUDGET_SECONDS = 45

# Reachability check before any candidate is tried: an unplugged camera would
# otherwise burn the full timeout once per candidate for no information.
CONNECT_TIMEOUT_SECONDS = 3

# Breathing room between probes. One real camera answered three connections and
# went quiet for the rest of the sweep, so this is deliberately unhurried —
# detection ends at the first working URL anyway, and a camera that stops
# talking costs far more than a few hundred milliseconds of politeness.
PROBE_PACING_SECONDS = 0.35

# When the device first goes quiet, give it one proper pause before concluding
# anything: several units simply need a moment to free the last session.
RECOVERY_AFTER_SILENT = 3
RECOVERY_PAUSE_SECONDS = 2.0

# Consecutive silent candidates after which the device is assumed to have had
# enough. Reporting what we know beats hammering it for another fifty.
DEAD_STREAK_LIMIT = 8

# How many candidates to try before falling back to asking the device over
# ONVIF. Two paths x three credential forms: the shapes that cover most units.
FAST_PATH_CANDIDATES = 6

# Pause after closing the probe connection and before opening the stream, so a
# camera with only a couple of connection slots has actually freed one.
SLOT_RELEASE_SECONDS = 0.6

# A path no camera can have. Used as a control: firmware that answers 200 to
# this answers 200 to anything, and its status codes cannot pick a stream.
BOGUS_PATH = '/slc-probe-no-such-path'

# How many URLs to open for real when the status codes turn out to be
# meaningless. Each costs the camera a connection and several seconds, and the
# devices that behave this way are the ones least able to spare either.
BLIND_DECODE_LIMIT = 4

# A candidate that has already authenticated deserves longer than a blind one:
# the live stream worker waits 10 s to open, so a 4 s probe could reject a URL
# that works perfectly well in the actual feed.
VERIFY_TIMEOUT_SECONDS = 8


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
        # They come *after* the vendor paths: /stream1 is a generic guess, and
        # putting it first meant firmware that accepts any path answered it
        # before its own documented URL was ever tried.
        paths = [
            *paths,
            ('generic',   '/stream1'),
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
    # Path-major, not credential-major. Grouping by credential put every
    # `admin` candidate behind all ten device-ID ones, and the probe's time
    # budget ran out long before it got there — which is exactly how an NVR
    # that wants `admin` ended up undetectable.
    out = []
    for fmt, path in paths_for(channel):
        for prefix in credential_prefixes(device_id, password):
            out.append({'format': fmt, 'url': f"rtsp://{prefix}{(ip or '').strip()}{path}"})
    return out


def credential_prefixes(device_id: str, password: str = '') -> list[str]:
    """The `user:pass@` forms to try, most likely first.

    Several vendors ignore the ID printed on the unit and expect "admin" for
    RTSP, so both are tried; the credential-less form covers a genuinely open
    camera and goes first when no password was given at all.
    """
    dev = quote((device_id or '').strip(), safe='')
    pw = quote((password or '').strip(), safe='')
    users = [u for u in dict.fromkeys([dev, 'admin']) if u]

    if pw:
        return [f"{u}:{pw}@" for u in users] + ['']
    return [''] + [f"{u}:@" for u in users]


def _port_of(host: str, default: int = RTSP_PORT) -> int:
    """Port out of a host string that may carry one."""
    if ':' in host:
        try:
            return int(host.rsplit(':', 1)[1])
        except ValueError:
            pass
    return default


def is_reachable(ip: str, port: int = RTSP_PORT,
                 timeout: float = CONNECT_TIMEOUT_SECONDS) -> bool:
    """TCP connect to the RTSP port — cheap proof the camera is even there."""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except Exception:
        return False


ONVIF_PORTS = (80, 8000, 8080, 8899)
ONVIF_PATHS = ('/onvif/device_service', '/onvif/media_service', '/onvif/Media',
               '/onvif/media', '/onvif/')
ONVIF_TIMEOUT_SECONDS = 3
# Ceiling on the whole ONVIF lookup. Ports x paths x usernames x content types
# is a big product, and on a host that accepts connections but does not speak
# ONVIF every one of them costs the full request timeout.
ONVIF_BUDGET_SECONDS = 8

# The SOAP envelope is rebuilt here rather than imported from views.py: that
# module imports this one, and the PTZ code it lives in is entangled with its
# own content-type caching. Not worth risking a working feature to share 20
# lines.
def _onvif_envelope(body_xml: str, username: str, password: str) -> bytes:
    import base64, datetime, os
    nonce_raw = os.urandom(16)
    created = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    digest = base64.b64encode(
        hashlib.sha1(nonce_raw + created.encode() + password.encode()).digest()).decode()
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Header>'
        '<wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"'
        ' xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">'
        '<wsse:UsernameToken>'
        f'<wsse:Username>{username}</wsse:Username>'
        '<wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd#PasswordDigest">'
        f'{digest}</wsse:Password>'
        '<wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">'
        f'{base64.b64encode(nonce_raw).decode()}</wsse:Nonce>'
        f'<wsu:Created>{created}</wsu:Created>'
        '</wsse:UsernameToken></wsse:Security></s:Header>'
        f'<s:Body>{body_xml}</s:Body></s:Envelope>'
    ).encode('utf-8')


def _onvif_call(endpoint: str, body_xml: str, username: str, password: str):
    """POST one ONVIF SOAP request. Returns the response text, or None."""
    import requests
    data = _onvif_envelope(body_xml, username, password)
    auths = ([(username, password)] if username else []) + [None]
    for ct in ('application/soap+xml; charset=utf-8', 'text/xml; charset=utf-8'):
        for auth in auths:
            try:
                r = requests.post(endpoint, data=data, timeout=ONVIF_TIMEOUT_SECONDS,
                                  headers={'Content-Type': ct}, auth=auth)
            except Exception:
                break                      # port/path dead — next one
            if r.status_code in (401, 403):
                continue                   # try the other auth style
            if r.status_code < 500 and r.text:
                return r.text
    return None


def onvif_stream_uri(ip: str, usernames, password: str,
                     channel: int = 1) -> str | None:
    """Ask the device for its own RTSP URL over ONVIF.

    Guessing vendor paths is a last resort: a device with ONVIF enabled will
    simply tell you the URL it publishes, which is both authoritative and one
    request instead of fifty. Cameras whose app says "works with ONVIF
    applications" — most consumer NVR-capable units — answer this.

    Returns the URI with credentials injected, or None if ONVIF is unavailable.
    """
    from xml.etree import ElementTree as ET

    ch = max(1, int(channel or 1))
    if isinstance(usernames, str):
        usernames = [usernames]
    users = [u for u in dict.fromkeys(usernames) if u] or ['admin']
    deadline = time.monotonic() + ONVIF_BUDGET_SECONDS

    for port in ONVIF_PORTS:
        if time.monotonic() >= deadline:
            break
        # One cheap TCP check per port, not one per username/path combination.
        if not is_reachable(ip, port, timeout=1.0):
            continue
        base = f'http://{ip}:{port}'
        for path in ONVIF_PATHS:
            if time.monotonic() >= deadline:
                break
            for username in users:
                if time.monotonic() >= deadline:
                    break
                text = _onvif_call(
                    f'{base}{path}',
                    '<trt:GetProfiles xmlns:trt="http://www.onvif.org/ver10/media/wsdl"/>',
                    username, password)
                if not text:
                    continue
                try:
                    tokens = [el.get('token') for el in ET.fromstring(text).iter()
                              if el.get('token')]
                except ET.ParseError:
                    continue
                if not tokens:
                    continue

                # Channel N means the Nth profile where the device offers that
                # many; on a single camera the extra profiles are sub-streams,
                # so asking for a channel it does not have is better refused
                # than silently answered with the wrong view.
                if ch > len(tokens):
                    log.info('[rtsp-probe] %s ONVIF offers %d profile(s), '
                             'channel %d asked', ip, len(tokens), ch)
                    return None
                token = tokens[ch - 1]

                uri_text = _onvif_call(
                    f'{base}{path}',
                    '<trt:GetStreamUri xmlns:trt="http://www.onvif.org/ver10/media/wsdl">'
                    '<trt:StreamSetup>'
                    '<tt:Stream xmlns:tt="http://www.onvif.org/ver10/schema">RTP-Unicast</tt:Stream>'
                    '<tt:Transport xmlns:tt="http://www.onvif.org/ver10/schema">'
                    '<tt:Protocol>RTSP</tt:Protocol></tt:Transport>'
                    '</trt:StreamSetup>'
                    f'<trt:ProfileToken>{token}</trt:ProfileToken>'
                    '</trt:GetStreamUri>', username, password)
                if not uri_text:
                    continue
                try:
                    uris = [el.text for el in ET.fromstring(uri_text).iter()
                            if el.tag.endswith('Uri')
                            and (el.text or '').startswith('rtsp://')]
                except ET.ParseError:
                    continue
                if uris:
                    log.info('[rtsp-probe] %s answered ONVIF GetStreamUri on %s%s',
                             ip, base, path)
                    return _with_credentials(uris[0], username, password)
    return None


def _with_credentials(url: str, username: str, password: str) -> str:
    """Put the login into a URL the device handed back without one."""
    if not username or '@' in url.split('://', 1)[-1].split('/', 1)[0]:
        return url
    scheme, rest = url.split('://', 1)
    return f'{scheme}://{quote(username, safe="")}:{quote(password, safe="")}@{rest}'


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


def _status_of(head: str) -> int | None:
    m = re.match(r'RTSP/\d\.\d\s+(\d+)', head)
    return int(m.group(1)) if m else None


def _read_response(sock) -> str:
    """Read one whole RTSP response, body included.

    The body has to be consumed even though nothing reads it: leaving an SDP
    payload in the socket buffer would desynchronise the next request on a
    reused connection, and every reply after it would be garbage.
    """
    buf = b''
    while b'\r\n\r\n' not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError('closed while reading headers')
        buf += chunk
    head, _, rest = buf.partition(b'\r\n\r\n')
    head_s = head.decode('utf-8', 'replace')

    m = re.search(r'Content-Length:\s*(\d+)', head_s, re.I)
    if m:
        remaining = int(m.group(1)) - len(rest)
        while remaining > 0:
            chunk = sock.recv(min(4096, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
    return head_s


class _RtspSession:
    """One TCP connection, many DESCRIBEs.

    A connection per candidate is what broke detection on a real camera: it
    accepts only about three at a time, so the sweep spent its whole allowance
    on questions and had nothing left for the stream it had just been told
    about. RTSP is happy to carry every DESCRIBE on one connection.
    """

    def __init__(self, host: str, port: int, timeout: float):
        self.host, self.port, self.timeout = host, port, timeout
        self.sock = None
        self.cseq = 0

    def _connect(self):
        if self.sock is None:
            self.sock = socket.create_connection((self.host, self.port),
                                                 timeout=self.timeout)
            self.sock.settimeout(self.timeout)
        return self.sock

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def _send(self, target: str, auth: str = '') -> str:
        self.cseq += 1
        lines = [f'DESCRIBE {target} RTSP/1.0',
                 f'CSeq: {self.cseq}',
                 'Accept: application/sdp',
                 'User-Agent: SLC-VMS']
        if auth:
            lines.append(f'Authorization: {auth}')
        sock = self._connect()
        sock.sendall(('\r\n'.join(lines) + '\r\n\r\n').encode())
        return _read_response(sock)

    def describe(self, target: str, user: str, pw: str) -> int | None:
        for attempt in (1, 2):
            try:
                head = self._send(target)
                code = _status_of(head)
                if code == 401 and (user or pw):
                    m = re.search(r'WWW-Authenticate:\s*(.+)', head, re.I)
                    if m:
                        auth = _auth_header('DESCRIBE', target, user, pw,
                                            m.group(1).strip())
                        code = _status_of(self._send(target, auth))
                return code
            except Exception as exc:
                # The camera closes an idle or completed connection routinely;
                # one silent reconnect keeps the sweep going. A second failure
                # means it has genuinely stopped talking.
                self.close()
                if attempt == 2:
                    log.debug('[rtsp-probe] DESCRIBE %s failed: %s', target, exc)
                    return None
        return None


def _describe(url: str, timeout: float = DESCRIBE_TIMEOUT_SECONDS,
              session: '_RtspSession | None' = None) -> int | None:
    """Send an RTSP DESCRIBE and return the status code the camera replies with.

    This is the search step. Opening a candidate with OpenCV costs seconds
    because FFmpeg waits for a decodable frame; DESCRIBE is one request on the
    port we already know is open, and it distinguishes the two failures that
    matter — 401 (credentials refused) from 404 (no such path or channel).

    Pass `session` to reuse one connection across candidates. Returns None when
    the socket itself failed.
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

    own = session is None
    if own:
        session = _RtspSession(host, port, timeout)
    try:
        return session.describe(target, user, pw)
    finally:
        if own:
            session.close()


# Transports to try, in order. TCP first because it survives packet loss, but
# it cannot be the only option: cameras that answer a TCP SETUP with a UDP
# transport make FFmpeg fail with "Nonmatching transport in server reply", and
# forcing TCP made those devices look like they had no working stream at all.
RTSP_TRANSPORTS = ('tcp', 'udp')


def _opens_once(url: str, transport: str, timeout_s: int) -> bool:
    import cv2
    import os

    os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = (
        f'rtsp_transport;{transport}'
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
        log.debug('[rtsp-probe] %s (%s) failed: %s', _redact(url), transport, exc)
        return False
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass


def _opens(url: str, timeout_s: int = PROBE_TIMEOUT_SECONDS) -> bool:
    """True when this URL yields an actual frame.

    A VideoCapture that merely `isOpened()` is not proof: FFmpeg reports open
    for URLs it will never decode. Reading a frame is the only honest test.

    Each attempt runs on its own thread with a hard wall-clock limit. The
    FFmpeg timeout options are advisory — one real camera sat in an open() for
    30 s despite a 4 s setting, which on its own exhausted the probe's entire
    budget and left every remaining candidate untried.
    """
    # A daemon thread, not a ThreadPoolExecutor: the executor's context manager
    # shuts down with wait=True on exit, which blocks on the very worker the
    # timeout was meant to escape.
    for transport in RTSP_TRANSPORTS:
        box = {}

        def work(t=transport):
            try:
                box['ok'] = _opens_once(url, t, timeout_s)
            except Exception:
                box['ok'] = False

        thread = threading.Thread(target=work, daemon=True,
                                  name=f'rtsp-open-{transport}')
        thread.start()
        thread.join(timeout_s + 2)

        if box.get('ok'):
            log.info('[rtsp-probe] %s decoded over %s', _redact(url), transport)
            return True
        if thread.is_alive():
            # Stuck inside FFmpeg. Abandon it — being a daemon it cannot hold
            # the process open — and move on rather than pay its full stall.
            log.debug('[rtsp-probe] %s (%s) exceeded the wall clock',
                      _redact(url), transport)
    return False


def _redact_prefix(prefix: str) -> str:
    """Mask the password inside a bare `user:pass@` prefix."""
    if ':' not in prefix:
        return prefix
    return f"{prefix.split(':', 1)[0]}:***@" if prefix.endswith('@') else prefix


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
    state     = {'dead': 0}  # consecutive silent candidates

    session = _RtspSession(ip.split(':')[0], _port_of(ip), DESCRIBE_TIMEOUT_SECONDS)

    def sweep(subset):
        """Try each candidate, confirming an acceptance the moment it happens.

        Every candidate costs the camera a connection, and they are not free:
        one real unit accepted on the second candidate and had stopped answering
        by the tenth. Collecting all the acceptances and verifying them at the
        end meant returning to a URL the camera would no longer serve, so a
        camera that had already said yes was reported as undetectable.

        Returns a success dict, or None.
        """
        for cand in subset:
            if time.monotonic() >= deadline:
                log.info('[rtsp-probe] %s gave up after %d attempts (budget reached)',
                         ip, len(attempts))
                return None
            code = _describe(cand['url'], session=session)
            codes.append(code)
            attempts.append(f"{_redact(cand['url'])} -> {code if code else 'no reply'}")

            if code == 200:
                state['dead'] = 0
                accepted.append(cand)
                # Hand the connection back before opening the stream. On a
                # camera with only a couple of slots, holding the probe socket
                # open is the difference between the stream connecting and the
                # camera refusing it.
                session.close()
                time.sleep(SLOT_RELEASE_SECONDS)
                # A few firmwares answer 200 to anything, so acceptance alone is
                # not proof there is video behind it. This one has authenticated,
                # so it earns a longer look than a blind candidate would get.
                if _opens(cand['url'], timeout_s=VERIFY_TIMEOUT_SECONDS):
                    log.info('[rtsp-probe] %s matched %s', ip, cand['format'])
                    return {'ok': True, 'rtsp_url': cand['url'],
                            'format': cand['format'], 'attempts': attempts}
            elif code is None:
                state['dead'] += 1
                # The device has stopped talking to us. Continuing only digs
                # deeper and makes the report misleading.
                if state['dead'] >= DEAD_STREAK_LIMIT:
                    log.info('[rtsp-probe] %s stopped responding after %d attempts',
                             ip, len(attempts))
                    return None
                if state['dead'] == RECOVERY_AFTER_SILENT:
                    # Let it free whatever session it is still holding before
                    # writing the rest of the list off.
                    time.sleep(RECOVERY_PAUSE_SECONDS)
            else:
                state['dead'] = 0
            time.sleep(PROBE_PACING_SECONDS)
        return None

    cands = candidate_urls(ip, device_id, password, channel)

    try:
        # Which credential does this camera accept, and do its answers mean
        # anything? Both questions are settled with one nonsense path.
        #
        # Some firmware returns 200 for every path it is asked about. On such a
        # device the search cannot use the status code at all — it "found" a
        # generic guess, spent the verification on it, and never reached the
        # camera's own documented URL.
        good_prefixes, status_meaningful = [], True
        for prefix in credential_prefixes(device_id, password):
            code = _describe(f'rtsp://{prefix}{ip}{BOGUS_PATH}', session=session)
            attempts.append(f'rtsp://{_redact_prefix(prefix)}{ip}{BOGUS_PATH} '
                            f'-> {code if code else "no reply"}  (control)')
            if code == 200:
                status_meaningful = False       # accepts anything
                good_prefixes.append(prefix)
            elif code == 404:
                good_prefixes.append(prefix)    # authenticated, path unknown
            time.sleep(PROBE_PACING_SECONDS)

        if not status_meaningful:
            # Fall back to trying the likeliest URLs for real. Restricting them
            # to the credential that authenticated keeps this within the handful
            # of connections such a camera tends to allow.
            log.info('[rtsp-probe] %s accepts any path — verifying by decode', ip)
            shortlist = [c for c in cands
                         if any(c['url'].startswith(f'rtsp://{p}{ip}')
                                for p in good_prefixes)][:BLIND_DECODE_LIMIT]
            session.close()
            for cand in shortlist:
                if time.monotonic() >= deadline:
                    break
                time.sleep(SLOT_RELEASE_SECONDS)
                ok = _opens(cand['url'], timeout_s=VERIFY_TIMEOUT_SECONDS)
                attempts.append(f"{_redact(cand['url'])} -> "
                                f"{'decoded' if ok else 'no video'}")
                if ok:
                    log.info('[rtsp-probe] %s matched %s by decode', ip, cand['format'])
                    return {'ok': True, 'rtsp_url': cand['url'],
                            'format': cand['format'], 'attempts': attempts}
            accepted = shortlist[:1]      # best guess for "Add Anyway"
            return {
                'ok': False,
                'error': ('This camera accepts any stream address you ask for, so '
                          'its answers cannot identify the right one, and none of '
                          'the likely URLs produced video. Close any app watching '
                          'the camera and try again — most units serve only one '
                          'stream at a time. Otherwise check the RTSP address in '
                          'the camera app and register it anyway.'),
                'suggestion': (accepted[0]['url'] if accepted
                               else suggestion_for(ip, device_id, password, channel)),
                'attempts': attempts,
            }

        # Stage 1 — the handful of shapes that cover most devices, both
        # usernames. Ordinary cameras resolve here in one or two connections,
        # which matters: asking ONVIF first meant several HTTP round trips
        # before any RTSP, and a connection-limited camera had nothing left by
        # the time its own working URL came up.
        hit = sweep(cands[:FAST_PATH_CANDIDATES])
        if hit:
            return hit

        # Stage 2 — ask the device for its own URL. Authoritative when it
        # answers, and worth the round trips now that the cheap guesses are
        # exhausted. Skipped entirely if the camera has already gone quiet:
        # piling HTTP requests onto a device that stopped answering RTSP only
        # pushes it under.
        if state['dead'] < RECOVERY_AFTER_SILENT:
            onvif_uri = None
            try:
                onvif_uri = onvif_stream_uri(ip, ['admin', device_id],
                                             password, channel)
            except Exception as exc:               # never let ONVIF break detect
                log.debug('[rtsp-probe] %s ONVIF lookup failed: %s', ip, exc)
            if onvif_uri:
                attempts.append(f'{_redact(onvif_uri)} -> ONVIF GetStreamUri')
                session.close()
                if _opens(onvif_uri, timeout_s=VERIFY_TIMEOUT_SECONDS):
                    return {'ok': True, 'rtsp_url': onvif_uri, 'format': 'onvif',
                            'attempts': attempts}

        # Stage 3 — the rest of the known vendor paths.
        if state['dead'] < DEAD_STREAK_LIMIT:
            hit = sweep(cands[FAST_PATH_CANDIDATES:])
            if hit:
                return hit
    finally:
        session.close()

    dead_streak = state['dead']

    # Nothing decoded. Say which wall we hit — "detection failed" sends an admin
    # to re-check a password that was never the problem.
    if dead_streak >= DEAD_STREAK_LIMIT:
        reason = ('The camera stopped answering partway through detection — it '
                  'likely limits how many connections it accepts at once. Wait '
                  'a few seconds and try again, or register it anyway.')
    elif accepted:
        reason = ('The camera accepted the stream address but no video could be '
                  'decoded from it over TCP or UDP. Another client may be '
                  'holding its only available connection — close the phone app '
                  'and try again. Registering it anyway is usually safe.')
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

    # A URL the camera actually accepted beats a vendor-shaped guess by a mile:
    # it is the one that authenticated, and "Add Anyway" saves whatever this
    # is. Falling back to the generic Dahua shape here handed the admin a URL
    # the camera had already refused.
    return {
        'ok': False,
        'error': reason,
        'suggestion': (accepted[0]['url'] if accepted
                       else suggestion_for(ip, device_id, password, channel)),
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
