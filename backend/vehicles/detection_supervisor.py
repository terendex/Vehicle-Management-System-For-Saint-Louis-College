"""Keeps a detector running for every parking zone that should have one.

Detection used to exist only as a button on Parking Space Management, and a
zone watched nothing until somebody pressed it. That is the wrong default for
the thing the whole feature rests on: a zone drawn on a Friday counted no bays
all weekend, a camera assigned after the fact never started one, and every
server restart switched every zone off again while the guard's screen went on
showing bays free — with nothing on screen saying the detector was not running.

So this reconciles rather than starts. Every RECONCILE_SECONDS it compares the
zones that *should* be watching — a camera assigned, that camera active, and
detection not paused by hand — against the threads actually running, and starts
or stops the difference. Written as a loop rather than a one-shot sweep at boot
because the interesting changes all happen later: a camera assigned to a zone,
an RTSP URL corrected in Device Management, a zone deleted, a worker thread
dying on a camera that dropped off the network.

The manual switch still wins. `ParkingZone.detection_enabled` is what Stop
Detection clears, and a zone with it off is left alone — otherwise the button
would appear to work and then undo itself a few seconds later.

Threads are keyed by zone, but the RTSP readers underneath are shared per URL
(see parking_camera._acquire_reader), so two zones on one dual-lens camera cost
one stream, not two.
"""
from __future__ import annotations

import logging
import os
import sys
import threading

from django.db import close_old_connections

log = logging.getLogger(__name__)

# How often the roster is re-checked. Short enough that assigning a camera feels
# immediate, long enough that it costs one cheap query a few times a minute.
RECONCILE_SECONDS = 20

# The first pass waits a little: at boot the database may still be coming up,
# and there is no value in racing it.
FIRST_DELAY_SECONDS = 8

_started = False
_lock = threading.Lock()
_stop = threading.Event()


def _desired() -> dict[int, str]:
    """zone_id → rtsp_url for every zone whose detector should be running."""
    from .models import ParkingZone

    rows = (
        ParkingZone.objects
        .filter(detection_enabled=True, camera__isnull=False, camera__is_active=True)
        .values_list('id', 'camera__rtsp_url')
    )
    return {zid: (url or '').strip() for zid, url in rows if (url or '').strip()}


def reconcile() -> None:
    """Start what should be running, stop what should not. Safe to call anytime."""
    from . import parking_camera

    desired = _desired()
    running = parking_camera.all_threads()

    # Stop first, so a zone whose camera changed frees its old stream before the
    # replacement opens — the campus cameras drop sessions under load.
    for zone_id, thread in running.items():
        wanted_url = desired.get(zone_id)
        if wanted_url is None:
            log.info("[autodetect] zone %s no longer eligible — stopping", zone_id)
            parking_camera.stop(zone_id)
        elif wanted_url != thread.rtsp_url:
            log.info("[autodetect] zone %s camera changed — restarting", zone_id)
            parking_camera.stop(zone_id)

    for zone_id, url in desired.items():
        thread = parking_camera.get_thread(zone_id)
        if thread is None or not thread.is_alive():
            log.info("[autodetect] starting detector for zone %s", zone_id)
            parking_camera.start(zone_id, url)


def _loop() -> None:
    delay = FIRST_DELAY_SECONDS
    while not _stop.wait(delay):
        delay = RECONCILE_SECONDS
        try:
            close_old_connections()
            reconcile()
        except Exception:
            # A bad pass must not take the supervisor down with it: the next one
            # is twenty seconds away and may well succeed.
            log.exception("[autodetect] reconcile pass failed")


def _autodetect_disabled() -> bool:
    """Whether to skip the supervisor entirely on this host.

    Off by default anywhere the cameras are unreachable, which in practice means
    the cloud half. The cameras live on the campus LAN at 192.168.x.x; a Railway
    container cannot route to them, so every reconcile pass there was starting a
    detector that could only fail — and failing is not cheap. One open attempt
    costs CV2_OPEN_TIMEOUT_SECONDS then OPEN_TIMEOUT_SECONDS (about half a
    minute), spawns an ffmpeg child, and holds a worker thread the whole time.
    Passes are 20s apart, so the attempts overlapped and piled up, and the
    single Daphne process serving the site was left starving: every request,
    including a small JSON POST, was waiting tens of seconds behind them.

    Explicit beats inferred in both directions — set DISABLE_PARKING_AUTODETECT
    to false on a cloud host that really can reach the cameras (a VPN, a tunnel),
    or to true on campus to keep it off.
    """
    raw = os.getenv('DISABLE_PARKING_AUTODETECT', '').strip().lower()
    if raw in ('1', 'true', 'yes'):
        return True
    if raw in ('0', 'false', 'no'):
        return False
    # Unset: default off wherever Railway sets its own environment markers.
    return bool(os.getenv('RAILWAY_ENVIRONMENT') or os.getenv('RAILWAY_PUBLIC_DOMAIN'))


def start() -> None:
    """Start the supervisor once per process. No-op outside the server."""
    global _started

    if _autodetect_disabled():
        log.info(
            "[autodetect] disabled on this host — the parking cameras are not "
            "reachable from here. Set DISABLE_PARKING_AUTODETECT=false to override."
        )
        return

    # Same rule as vehicles/scheduler.py: migrations, tests, shells and one-shot
    # commands must not open camera streams.
    argv = ' '.join(sys.argv)
    is_server = (
        'daphne' in argv or 'uvicorn' in argv or 'gunicorn' in argv
        or 'runserver' in argv
    )
    if not is_server:
        return
    if 'runserver' in argv and os.environ.get('RUN_MAIN') != 'true':
        return

    with _lock:
        if _started:
            return
        _started = True

    _stop.clear()
    threading.Thread(target=_loop, name='parking-autodetect', daemon=True).start()
    log.info("[autodetect] started — reconciling zone detectors every %ds", RECONCILE_SECONDS)


def stop() -> None:
    """Stop the supervisor thread (used by tests)."""
    global _started
    _stop.set()
    with _lock:
        _started = False
