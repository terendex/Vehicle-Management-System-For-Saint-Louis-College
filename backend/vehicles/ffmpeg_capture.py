"""Read RTSP with the system FFmpeg when OpenCV's own copy cannot.

OpenCV ships a *frozen* FFmpeg inside its wheel — opencv-python 4.10 and 4.12
both carry avcodec 58.134.100, which is FFmpeg 4.4 from 2021. Upgrading
opencv-python does not move it, so every camera quirk fixed upstream in the last
four years is simply unavailable to `cv2.VideoCapture`.

That is not a hypothetical. A dual-lens Yoosee on this campus:

  * refuses RTSP-over-TCP outright ("Nonmatching transport in server reply"),
    so the TCP-first order every call site used left it permanently black;
  * advertises its audio track as `PCMA/16000`, but payload type 8 is
    statically 8000 Hz, and the old demuxer gives up on the whole stream over
    the malformed track;
  * answers `200 OK` to *every* path, including a deliberately bogus one, so
    RTSP status codes cannot identify its real stream.

FFmpeg 8 opens it on the first try, 3 times out of 3, in under four seconds.
FFmpeg 4.4 never opens it at all. So the fix is not another options string —
it is to stop asking the 2021 decoder.

Two rules do most of the work here, and both are deliberately *less* specific
than what the call sites used to do:

  * **Do not pin the transport.** FFmpeg's default is to negotiate — UDP, then
    TCP, then multicast. Every call site used to hardcode `rtsp_transport;tcp`,
    which is exactly the one thing this camera refuses. Letting FFmpeg choose
    is what makes an unknown camera work.
  * **Drop audio (`-an`).** Nothing here decodes audio, and a malformed audio
    track is a common way for cheap firmware to take the video down with it.

`open_capture()` is the entry point: it tries OpenCV first so nothing regresses
for the cameras already working, and falls back here only when OpenCV cannot
produce a frame.
"""
import logging
import os
import re
import shutil
import socket
import subprocess
import threading
import time

log = logging.getLogger(__name__)

# cv2 property ids, spelled out so this module need not import cv2 just to
# answer get(). They are stable public constants in OpenCV's C API.
CAP_PROP_FRAME_WIDTH  = 3
CAP_PROP_FRAME_HEIGHT = 4
CAP_PROP_FPS          = 5

# How long to wait for the first frame before calling the open a failure. The
# Yoosee takes ~4 s on its sub-stream and noticeably longer on the 1920x2160
# main one, so this is generous on purpose.
OPEN_TIMEOUT_SECONDS = 25

# OpenCV gets a much shorter leash than the fallback. It is only ever the fast
# path — when it can open a camera at all it does so in well under a second —
# so a long timeout here buys nothing and is paid twice, once per transport,
# before the backend that actually works is even tried.
CV2_OPEN_TIMEOUT_SECONDS = 6

# How long read() waits for a *fresh* frame before reporting failure.
READ_TIMEOUT_SECONDS = 10

# Grace period after ffmpeg exits, to drain frames already sitting in the pipe
# before declaring the open a failure.
EXIT_DRAIN_SECONDS = 0.75

# Ceiling on the pixels pushed through the raw pipe, per frame.
#
# This backend moves *uncompressed* bgr24, so a frame costs w*h*3 bytes on the
# pipe no matter how small the encoded stream was. The campus Yoosee sends
# 1920x2160 — 12.4 MB per frame, ~186 MB/s at its advertised 15 fps — and the
# pipe simply cannot carry that: measured end to end, the camera delivered
# 11.0 fps to a decode-only ffmpeg but only 0.6 fps through this class. The
# network was never the problem; the raw pipe was, by a factor of 18.
#
# Capping pixels rather than width keeps one number meaningful for both
# ordinary landscape cameras and the stacked dual-lens frames, which are taller
# than they are wide. The scale is proportional, so `lens_layout` still sees the
# aspect ratio it splits on.
MAX_PIPE_PIXELS = 1280 * 720

# Frames are dropped rather than queued: for a live feed the newest frame is
# the only one worth having, and a backlog just adds latency.
_SIZE_RE = re.compile(r'\b(\d{2,5})x(\d{2,5})\b')
_FPS_RE  = re.compile(r'([\d.]+)\s+fps')


def _scale_filter(max_pixels: int) -> str:
    """A scale filter that shrinks only oversized frames, preserving aspect.

    Expressed in ffmpeg's own filter arithmetic rather than computed here
    because the source size is not known until ffmpeg has read the stream
    banner — and asking first would cost a second RTSP session, which several
    of these cameras do not have to spare.

    `min(1, ...)` makes it a no-op for anything already under budget, so a
    normal 1280x720 camera is passed through untouched. Both axes are rounded
    to an even number: yuv420p chroma is subsampled 2x2 and an odd dimension
    makes the scaler fail outright.
    """
    factor = f'min(1\\,sqrt({max_pixels}/(iw*ih)))'
    return (f'scale=w=trunc(iw*{factor}/2)*2'
            f':h=trunc(ih*{factor}/2)*2')


def ffmpeg_binary() -> str | None:
    """Path to a usable ffmpeg, or None.

    `FFMPEG_BINARY` wins so a deployment can point at a specific build. The
    imageio-ffmpeg fallback matters on Windows boxes where nobody has installed
    ffmpeg system-wide but the wheel is already present as a transitive
    dependency.
    """
    explicit = (os.environ.get('FFMPEG_BINARY') or '').strip()
    if explicit and os.path.exists(explicit):
        return explicit

    found = shutil.which('ffmpeg')
    if found:
        return found

    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def is_available() -> bool:
    return ffmpeg_binary() is not None


def _no_window() -> dict:
    """Keep a console window from flashing up for every camera on Windows."""
    if os.name == 'nt':
        return {'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0)}
    return {}


class FFmpegCapture:
    """A `cv2.VideoCapture` work-alike backed by an ffmpeg subprocess.

    Implements the slice of the interface this codebase actually uses —
    `isOpened`, `read`, `release`, `set`, `get`, `grab`, `retrieve` — so it can
    be returned from `_open_cap()` without any caller knowing the difference.

    Unlike `cv2.VideoCapture`, `isOpened()` here is *honest*: construction waits
    for a real decoded frame, so it never reports open for a stream that will
    never produce video. That distinction is the entire bug this class exists
    to fix, and callers already treat a successful open as permission to stream.
    """

    def __init__(self, url: str, open_timeout: float = OPEN_TIMEOUT_SECONDS,
                 read_timeout: float = READ_TIMEOUT_SECONDS,
                 transport: str | None = None,
                 max_pixels: int | None = MAX_PIPE_PIXELS):
        self.url = url
        self.read_timeout = read_timeout
        self.open_timeout = open_timeout
        self.width = self.height = 0
        self.fps = 0.0

        self._proc = None
        self._frame = None
        self._seq = 0
        self._last_seq = 0
        self._dims = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._new = threading.Condition(self._lock)
        self._stderr_tail: list[str] = []

        binary = ffmpeg_binary()
        if not binary:
            log.warning('[ffmpeg-capture] no ffmpeg binary available')
            return

        # `-hide_banner` keeps the version/configuration dump out of stderr.
        # It is a dozen lines long, and `_stderr_tail` only keeps the last few —
        # so on a camera that fails before saying anything useful, the banner
        # was the whole "error message" a diagnosing admin got to see. The
        # stream lines this class parses for dimensions are unaffected.
        cmd = [binary, '-nostdin', '-hide_banner', '-loglevel', 'info']
        # Only pin the transport when a caller insists. The default — letting
        # FFmpeg negotiate — is what makes unknown cameras work.
        if transport:
            cmd += ['-rtsp_transport', transport]
        cmd += [
            # No `-fflags nobuffer` / `-flags low_delay` here, tempting as they
            # are for a live feed: on an H.264 source they make FFmpeg emit
            # *zero* frames — 20-frame clip in, nothing out, reproducible. They
            # would buy nothing anyway, because the reader thread below keeps
            # only the newest frame and drops the rest, which is where this
            # backend's low latency actually comes from.
            '-an',                      # audio never helps and often hurts
            '-i', url,
        ]
        # Shrink before the pipe, never after: the whole point is to not push
        # the bytes through it. A frame that is already within budget passes
        # through untouched.
        if max_pixels:
            cmd += ['-vf', _scale_filter(max_pixels)]
        cmd += [
            '-f', 'rawvideo',
            '-pix_fmt', 'bgr24',        # already OpenCV's channel order
            '-',
        ]

        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL, bufsize=0, **_no_window())
        except Exception as exc:
            log.warning('[ffmpeg-capture] could not start ffmpeg: %s', exc)
            self._proc = None
            return

        threading.Thread(target=self._pump_stderr, daemon=True,
                         name='ffmpeg-stderr').start()
        threading.Thread(target=self._pump_video, daemon=True,
                         name='ffmpeg-video').start()

        # Wait for a real frame, not merely for the process to be alive.
        #
        # Giving up the moment ffmpeg exits matters as much as the timeout does.
        # A camera that accepts any path answers DESCRIBE for a wrong one, so
        # ffmpeg prints the stream banner — dimensions and all — and only then
        # fails at PLAY. Waiting for dimensions therefore proves nothing, and
        # keying the early exit on them made every wrong path cost the full
        # timeout instead of the second it actually takes. That is the
        # difference between the probe reaching a camera's real path and
        # running out of budget seven guesses in.
        deadline = time.monotonic() + open_timeout
        exited_at = None
        with self._new:
            while self._seq == 0 and time.monotonic() < deadline:
                if self._proc.poll() is not None:
                    # Frames already in the pipe are still worth draining, but
                    # briefly — nothing new can arrive from a dead process.
                    if exited_at is None:
                        exited_at = time.monotonic()
                    elif time.monotonic() - exited_at >= EXIT_DRAIN_SECONDS:
                        break
                self._new.wait(0.1)

        if self._seq == 0:
            log.info('[ffmpeg-capture] %s produced no frame in %.0fs: %s',
                     _redact(url), open_timeout, self.last_error())
            self.release()

    # ── plumbing ────────────────────────────────────────────────────────────
    def _pump_stderr(self):
        """Parse the stream banner for dimensions, and keep the last few lines.

        The size has to come from here rather than a separate ffprobe run: many
        of these cameras allow only one or two sessions at a time, and spending
        one just to ask how big the picture is can cost the session that was
        meant to carry the video.

        The size that matters is the one in the **Output** banner, not the
        input's. They are the same until a scale filter is inserted, and then
        they are not — `_pump_video` sizes its reads as w*h*3, so taking the
        input's 1920x2160 while the pipe actually carries a scaled frame would
        misalign every read and shear the picture rather than shrink it.
        """
        stream = self._proc.stderr
        in_output = False
        try:
            for raw in iter(stream.readline, b''):
                if self._stop.is_set():
                    break
                line = raw.decode('utf-8', 'replace').rstrip()
                if not line:
                    continue
                self._stderr_tail = (self._stderr_tail + [line])[-8:]
                if line.startswith('Output #'):
                    in_output = True
                # fps is worth taking from either banner — the input states the
                # camera's real rate, and the output repeats it — but the
                # dimensions are only trustworthy once we are past `Output #`.
                if not self._dims.is_set() and 'Video:' in line:
                    f = _FPS_RE.search(line)
                    if f and not self.fps:
                        try:
                            self.fps = float(f.group(1))
                        except ValueError:
                            pass
                    if not in_output:
                        continue
                    m = _SIZE_RE.search(line)
                    if m:
                        self.width, self.height = int(m.group(1)), int(m.group(2))
                        self._dims.set()
        except Exception:
            # release() closes the pipe underneath this thread; a blocked
            # readline surfacing that as an exception is expected, not a fault.
            pass
        finally:
            # Never leave an opener waiting on a stream that has already died.
            self._dims.set()
            with self._new:
                self._new.notify_all()

    def _pump_video(self):
        """Read whole frames off the pipe, keeping only the newest."""
        if not self._dims.wait(self.open_timeout):
            return
        import numpy as np

        nbytes = self.width * self.height * 3
        if nbytes <= 0:
            return
        stdout = self._proc.stdout
        while not self._stop.is_set():
            buf = b''
            while len(buf) < nbytes and not self._stop.is_set():
                chunk = stdout.read(nbytes - len(buf))
                if not chunk:
                    return                      # ffmpeg exited
                buf += chunk
            if len(buf) < nbytes:
                return
            frame = np.frombuffer(buf, np.uint8).reshape(
                (self.height, self.width, 3)).copy()
            with self._new:
                self._frame = frame
                self._seq += 1
                self._new.notify_all()

    # ── VideoCapture surface ────────────────────────────────────────────────
    def isOpened(self) -> bool:
        return self._proc is not None and self._seq > 0 and not self._stop.is_set()

    def read(self):
        """Return the newest frame, waiting up to `read_timeout` for a fresh one."""
        if self._proc is None:
            return False, None
        deadline = time.monotonic() + self.read_timeout
        with self._new:
            while self._seq == self._last_seq:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self._stop.is_set():
                    return False, None
                self._new.wait(min(0.25, remaining))
            self._last_seq = self._seq
            frame = self._frame
        return (frame is not None), frame

    def grab(self) -> bool:
        ok, _ = self.read()
        return ok

    def retrieve(self):
        with self._lock:
            return (self._frame is not None), self._frame

    def set(self, prop, value) -> bool:      # noqa: ARG002 - parity with cv2
        """Accepted and ignored: buffering and timeouts are handled internally."""
        return True

    def get(self, prop) -> float:
        if prop == CAP_PROP_FRAME_WIDTH:
            return float(self.width)
        if prop == CAP_PROP_FRAME_HEIGHT:
            return float(self.height)
        if prop == CAP_PROP_FPS:
            return float(self.fps)
        return 0.0

    def last_error(self) -> str:
        return ' | '.join(self._stderr_tail[-3:]) or 'no output from ffmpeg'

    def release(self):
        self._stop.set()
        with self._new:
            self._new.notify_all()
        proc, self._proc = self._proc, None
        if proc is None:
            return
        for step in (proc.terminate, proc.kill):
            if proc.poll() is not None:
                break
            try:
                step()
                proc.wait(timeout=3)
            except Exception:
                pass
        # The pipes are deliberately NOT closed here. Both pump threads sit
        # blocked in read()/readline() on these handles, and closing a handle
        # out from under a blocked reader hangs the *closing* thread on
        # Windows — which is the caller, so releasing a camera could freeze the
        # worker that owned it. Killing the child gives the readers EOF and
        # they exit on their own; Popen closes the handles when it is collected.

    def __del__(self):
        try:
            self.release()
        except Exception:
            pass


class _PrimedCapture:
    """Wraps a `cv2.VideoCapture` whose first frame has already been read.

    Choosing a backend honestly means proving it can decode, and proving it
    means consuming a frame. Rather than throw that frame away — on a camera
    that took four seconds to hand it over — the first `read()` replays it and
    everything after delegates straight through.
    """

    def __init__(self, cap, first_frame):
        self._cap = cap
        self._pending = first_frame

    def read(self):
        if self._pending is not None:
            frame, self._pending = self._pending, None
            return True, frame
        return self._cap.read()

    def isOpened(self):
        return self._pending is not None or self._cap.isOpened()

    def __getattr__(self, name):
        return getattr(self._cap, name)


def _redact(url: str) -> str:
    """Hide the password before a URL reaches a log line."""
    if '@' not in url or '://' not in url:
        return url
    scheme, rest = url.split('://', 1)
    creds, host = rest.split('@', 1)
    return f"{scheme}://{creds.split(':', 1)[0]}:***@{host}"


# Guards the process-wide env var OpenCV reads at VideoCapture construction.
# Shared by every caller in the project, which the per-module locks it replaced
# were not: scanning and vehicles each held their own, over the same env var.
_ENV_LOCK = threading.Lock()

# Pause between giving up on OpenCV and starting ffmpeg. Cameras of this class
# allow only two or three sessions at a time and are slow to reap the ones
# OpenCV just abandoned; without the pause ffmpeg inherits a device with no free
# slot and sits there until its own timeout, turning a working camera into a
# failed open. Measured: the same stream that opens in 5 s standing alone
# produced nothing in 25 s when started immediately after two failed attempts.
SLOT_RELEASE_SECONDS = 2.0

# Transports to try with OpenCV, in order. TCP first because it survives packet
# loss on a campus network; UDP second because a fair number of cameras only
# ever answer UDP.
_CV2_TRANSPORTS = ('tcp', 'udp')


def _try_cv2(url: str, open_timeout_ms: int):
    """Open with OpenCV and prove it decodes. Returns a primed capture or None.

    The timeouts go through the constructor's parameter list, not `cap.set()`.
    Setting `CAP_PROP_OPEN_TIMEOUT_MSEC` afterwards is too late — the
    constructor has already done the opening, so the property it was meant to
    bound has been and gone. That is why a camera OpenCV cannot open used to
    cost 30 s per transport: its own default, not the 10 s being asked for.
    """
    import cv2

    for transport in _CV2_TRANSPORTS:
        with _ENV_LOCK:
            os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = (
                f'rtsp_transport;{transport}'
                '|buffer_size;2097152'
                '|stimeout;10000000'
                '|threads;1'
                '|err_detect;ignore_err'
                '|fflags;discardcorrupt'
            )
            cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG, [
                cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, open_timeout_ms,
                cv2.CAP_PROP_READ_TIMEOUT_MSEC, open_timeout_ms,
            ])
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        if cap.isOpened():
            ok, frame = cap.read()
            if ok and frame is not None and getattr(frame, 'size', 0) > 0:
                log.info('[capture] %s opened by OpenCV over %s',
                         _redact(url), transport)
                return _PrimedCapture(cap, frame)
        try:
            cap.release()
        except Exception:
            pass
    return None


# Hosts already known to need the subprocess backend.
#
# The OpenCV stage is meant to be a cheap fast path, but on fragile firmware it
# is not free: it costs two connection attempts (one per transport), and the
# campus Yoosee reboots under exactly that. The fallback then opened a camera
# that was already on its way down and failed, even though the same call on a
# rested camera succeeds in 2.6 s. Remembering the answer means each host pays
# that discovery at most once per process.
# Entries expire: a camera that is swapped, re-flashed or simply fixed should
# get the fast path back without a restart, and a permanent note would also
# make the decision depend on whatever this process happened to see first.
_PREFER_FFMPEG: dict[str, float] = {}
_MEMO_LOCK = threading.Lock()
MEMO_TTL_SECONDS = 3600.0


def _prefers_ffmpeg(key: str) -> bool:
    with _MEMO_LOCK:
        seen = _PREFER_FFMPEG.get(key)
        if seen is None:
            return False
        if time.monotonic() - seen > MEMO_TTL_SECONDS:
            del _PREFER_FFMPEG[key]
            return False
        return True


def _remember_ffmpeg(key: str) -> None:
    with _MEMO_LOCK:
        _PREFER_FFMPEG[key] = time.monotonic()


def reset_backend_memo() -> None:
    """Forget which backend each host needs. For tests and manual recovery."""
    with _MEMO_LOCK:
        _PREFER_FFMPEG.clear()

# How long to wait for a host that stopped listening during the OpenCV stage.
REBOOT_WAIT_SECONDS = 40

# Settling time after a rebooted camera starts accepting TCP again.
#
# The port binds a little before the media server behind it will negotiate, and
# a camera that just rebooted may still be holding the sessions that killed it.
# Opening the instant the port answers earns a 4XX instead of a stream.
POST_REBOOT_SETTLE_SECONDS = 8.0


def _host_key(url: str) -> str:
    """Host[:port] of an RTSP URL, without credentials — the memo key."""
    try:
        return url.split('@')[-1].split('/')[0].strip().lower()
    except Exception:
        return url


def _listening(hostport: str, timeout: float = 3.0) -> bool:
    """Cheap check that something still accepts TCP on the RTSP port."""
    host, _, port = hostport.partition(':')
    try:
        with socket.create_connection((host, int(port or 554)), timeout=timeout):
            return True
    except Exception:
        return False


def open_capture(url: str, open_timeout: float = OPEN_TIMEOUT_SECONDS):
    """Open `url` with whichever backend can actually decode it.

    OpenCV is tried first so nothing changes for the cameras already working;
    its bundled FFmpeg is old but it is in-process and cheap. The subprocess
    backend is the fallback that covers everything else.

    Always returns an object with the `VideoCapture` surface. Callers check
    `isOpened()` exactly as before — a returned object that is not open means
    no backend could decode the stream.
    """
    key = _host_key(url)
    skip_cv2 = _prefers_ffmpeg(key)

    if not skip_cv2:
        cap = _try_cv2(url, int(CV2_OPEN_TIMEOUT_SECONDS * 1000))
        if cap is not None:
            return cap

    if not is_available():
        log.warning('[capture] OpenCV could not open %s and no system ffmpeg is '
                    'installed to fall back on', _redact(url))
        import cv2
        return cv2.VideoCapture('')          # closed, so isOpened() is False

    if not skip_cv2:
        log.info('[capture] OpenCV could not open %s — falling back to system ffmpeg',
                 _redact(url))
        time.sleep(SLOT_RELEASE_SECONDS)     # let the camera reap the dead sessions

        # If the OpenCV attempts knocked the camera over, opening now just
        # fails against a device that is booting. Wait for it rather than
        # spending the fallback — this is the only chance to get the stream,
        # and a wrong verdict here is what made a working camera look dead.
        if not _listening(key):
            # Record this *now*, on the crash itself rather than on a later
            # success. The fallback is very likely to fail on this pass — the
            # camera is rebooting and still holding the sessions OpenCV left,
            # so it answers 4XX — and memoising only on success would mean
            # never learning, re-crashing the camera on every single open.
            _remember_ffmpeg(key)
            log.info('[capture] %s stopped listening during the OpenCV probe — '
                     'it will use the ffmpeg backend directly from now on', key)

            log.info('[capture] waiting up to %ss for %s to restart',
                     REBOOT_WAIT_SECONDS, key)
            waited = 0.0
            while waited < REBOOT_WAIT_SECONDS and not _listening(key):
                time.sleep(2.0)
                waited += 2.0
            if _listening(key):
                log.info('[capture] %s back after ~%.0fs', key, waited)
                time.sleep(POST_REBOOT_SETTLE_SECONDS)

    cap = FFmpegCapture(url, open_timeout=open_timeout)

    # Letting FFmpeg negotiate is right for an unknown camera, but negotiation
    # can still land on TCP — and some units answer "Nonmatching transport in
    # server reply" and serve nothing at all. One explicit UDP attempt covers
    # them. It is only ever paid on a camera that already failed, so it costs
    # the working ones nothing.
    if not cap.isOpened() and 'nonmatching transport' in cap.last_error().lower():
        log.info('[capture] %s refused the negotiated transport — retrying over UDP',
                 key)
        cap.release()
        time.sleep(SLOT_RELEASE_SECONDS)
        cap = FFmpegCapture(url, open_timeout=open_timeout, transport='udp')

    if cap.isOpened() and not skip_cv2:
        # Only the subprocess backend can drive this host. Skip the OpenCV
        # stage next time so we stop paying for a reset to learn it again.
        _remember_ffmpeg(key)
        log.info('[capture] %s will use the ffmpeg backend directly from now on', key)
    return cap
