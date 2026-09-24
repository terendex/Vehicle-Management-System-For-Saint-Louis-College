# =============================================================================
# WHAT THIS FILE IS FOR
#
# The live camera pipeline. Two long-lived connections live here, both of them
# "consumers" — the Channels word for code that stays connected to a browser
# over a WebSocket instead of answering one request and stopping:
#
#   ScanLiveConsumer   (top half)    the gate. The browser sends camera frames;
#                                    this finds plates, reads them, decides
#                                    whether the vehicle may enter, and sends
#                                    the answer back.
#   _StreamWorker + RtspStreamConsumer (bottom half)
#                                    the IP-camera viewer: one worker thread per
#                                    camera, fanning its frames out to every
#                                    browser watching that camera.
#
# The hard part of the top half is not the reading; it is deciding WHEN to act.
# A camera sends many frames a second, and the same car sits in view for a long
# time, so the same plate is read over and over. Three mechanisms keep that from
# turning into a flood of database rows and gate decisions:
#
#   1. Tracks.    The tracker gives each vehicle/plate box a track_id that
#                 persists across frames, so repeated reads of one car can be
#                 gathered together instead of treated as new cars.
#   2. Voting.    Each track accumulates OCR reads, weighted by confidence,
#                 until one text wins and the track "locks".
#   3. Presence.  A locked plate's decision is remembered process-wide for a
#                 cooldown, so the gate decides once, not once per frame.
#
# Read the file in that order: lifecycle (connect/disconnect), frame intake
# (receive_json), detection, OCR, then the presence machinery.
# =============================================================================

import logging                                  # server-side diagnostic messages
import base64                                   # browser frames arrive as base64 text
import time                                     # monotonic-ish wall time for cooldowns and FPS
import threading                                # the presence registry is shared across threads
import uuid
from datetime import datetime, timedelta
from typing import Any
import asyncio                                  # this file is asynchronous: many sockets, one thread
from asgiref.sync import sync_to_async          # lets async code call ordinary (blocking) database code
from django.utils import timezone
from django.conf import settings
from channels.generic.websocket import AsyncJsonWebsocketConsumer   # base class for a JSON WebSocket endpoint

from .ml.detection import detect_plates, is_gpu_available   # the vehicle/plate detector
from vehicles.lens_layout import detect_across_lenses
from .ml.database import save_record as db_save_record
from .ml.proximity_tracker import ProximityTracker          # keeps one identity per vehicle across frames
from .ml.reader import _ocr_crop                            # reads the characters off a cropped plate
from .ml.validator import is_valid_ph_plate                 # "does this look like a real PH plate?"

logger = logging.getLogger(__name__)

SNAPSHOT_DIR = "snapshots"                      # sub-folder of MEDIA_ROOT where plate crops are written


# Works out which gate a scan should be filed under. Getting this wrong sends
# the record to a gate nobody looks at, so both sources are tried in order.
def _resolve_gate(raw, user) -> str:
    """Decide which gate a scan belongs to.

    A camera explicitly configured with a gate wins. Otherwise fall back to the
    scanning guard's own gate_assignment so the scan still lands in that gate's
    log instead of the orphan 'main' bucket (which shows in no gate's view).
    """
    gid = (raw or '').strip()                   # what the camera/browser said, if anything
    if gid and gid != 'main':
        return gid                              # an explicit gate always wins
    return getattr(user, 'gate_assignment', None) or 'main'   # else the guard's posted gate; 'main' is the last resort

FRAME_RATE_LIMIT_MS = 100                       # process at most one frame per 100ms (~10 per second)
_DEFAULT_DEDUP_SECONDS = 5  # fallback used if DB is unavailable at connect time
CAMERA_ENTRY_COOLDOWN_SECONDS = 60  # breathing space: camera won't exit a vehicle within this window after entry
NEGATIVE_SCAN_COOLDOWN_SECONDS = 60  # unregistered & denied/violation plates: same plate re-logged at most once per minute (DB-backed, survives reconnects)

# Per-track OCR accumulation settings
# These three decide when a track stops guessing and commits to a plate: a
# single confident read, or enough weaker reads agreeing.
_OCR_LOCK_CONF    = 0.50   # lock immediately if any single read reaches this
_OCR_MIN_CONF     = 0.08   # minimum confidence to count a read — low to handle noisy vehicle crops
_OCR_MAX_ATTEMPTS = 15     # more attempts before force-locking, helps accumulate votes

# Locked-plate re-verification: quietly re-read each locked track's plate so a
# physical plate swap (or an early misread) is noticed without restarting the
# stream. A same-text read changes nothing; a confirmed different plate re-locks
# the track and runs the normal presence pipeline (per-plate cooldowns apply).
_OCR_REVERIFY_SECONDS = 5.0   # how often a locked track's plate is re-read
_REVERIFY_STRONG_CONF = 0.60  # a single read at this confidence switches immediately;
                              # weaker reads need two agreeing reads to switch

# ── Shared plate-presence registry ─────────────────────────────────────────────
#
# Shared across ALL consumers in this process (multiple cameras, WS reconnects).
# Each decision is *held* for a status-dependent cooldown (see
# _result_hold_seconds): while the plate stays in view within the hold, repeat
# reads are suppressed — no log spam, no per-frame re-processing. Once the hold
# expires (or the plate re-appears after leaving view), the entry/exit state
# machine runs again, so a vehicle waiting at the gate is re-evaluated about
# once a minute and the flow advances Entry → Exit → Entry.
#
# plate → {"result": dict, "decided_at": float, "last_seen": float}
_PLATE_PRESENCE: dict[str, dict] = {}
# plates whose decision is currently being computed — prevents duplicate DB writes
_PLATES_IN_FLIGHT: set[str] = set()
_PRESENCE_LOCK = threading.Lock()   # both dictionaries above are shared, so every read/write takes this lock


# The gate itself: one of these exists per connected browser tab showing a
# camera. Frames come in, decisions go out.
class ScanLiveConsumer(AsyncJsonWebsocketConsumer):

    # ── lifecycle ──────────────────────────────────────────────────────────────

    # Runs once when a browser connects: check who they are, work out the gate,
    # and set up the per-connection state the frame loop will use.
    async def connect(self):
        logger.info("[WS] Connection attempt from %s", self.scope.get("REMOTE_ADDR", "unknown"))
        qs = self.scope["query_string"].decode()     # the ?token=...&gate=... part of the URL
        token_key = (                                # a WebSocket cannot send an auth header, so the token rides in the URL
            qs.split("token=")[-1].split("&")[0]
            if "token=" in qs
            else None
        )
        if not token_key:
            logger.warning("[WS] No token provided")
            await self.close(code=4001, reason="Authentication required")   # 4001: this app's "not signed in"
            return
        self._user = await self._get_user_from_token(token_key)   # look the token up in the database
        if self._user is None:
            logger.warning("[WS] Invalid token")
            await self.close(code=4001, reason="Invalid token")
            return

        # Read gate_id from query string, e.g. ?token=...&gate=gate1
        raw_gate = ''
        for part in qs.split('&'):
            if part.startswith('gate='):
                raw_gate = part[5:]                  # everything after "gate="
                break
        self._gate_id = _resolve_gate(raw_gate, self._user)   # camera's gate, else the guard's own

        self._tracker = ProximityTracker()           # one tracker per connection: identities are per camera view
        self._frame_counter = 0                      # only used to label frames sent back to the browser
        self._pending_ocr: dict[int, bool] = {}      # tracks whose plate is being read right now, so it is not read twice
        # track_id → {votes, attempts, locked}
        self._ocr_state: dict[int, dict] = {}

        # plate → decided_at of the presence decision this client last received
        self._announced: dict[str, float] = {}

        self._fps = 0.0                              # frames per second, shown in the browser overlay
        self._fps_counter = 0
        self._fps_start: float | None = None
        self._last_process_time: float = 0.0         # when the last frame was processed, for the rate limit
        self._detection_in_progress = False          # true while the detector is busy; extra frames are dropped
        self._loop = asyncio.get_running_loop()      # kept so background threads can hand work back to this connection

        try:
            from vehicles.models import SystemSettings
            cfg = await sync_to_async(SystemSettings.get)()   # database call, so it has to be wrapped for async code
            self._dedup_seconds = cfg.scan_dedup_seconds      # how long a plate counts as "still in view"
        except Exception:
            self._dedup_seconds = _DEFAULT_DEDUP_SECONDS      # settings unreachable: carry on with the fallback

        await self.accept()                          # from here on the browser may send frames
        logger.info("[WS] Connection accepted for user: %s", self._user)
        await self.send_json({"type": "connected", "message": "Stream ready.", "gpu": is_gpu_available()})

        # Loading the detector takes a while on first use, so the browser is
        # told what the model is doing ("loading", "ready") rather than facing a
        # silent pause. The listener is called from the detector's own thread,
        # which is why it hands the message back to this connection's loop.
        from .ml.detection import add_ml_status_listener
        _loop = self._loop
        async def _send_ml_status(stage, message):
            try:
                await self.send_json({"type": "ml_status", "stage": stage, "message": message})
            except Exception:
                pass                                 # the browser may have gone; a status note is not worth an error
        def _ml_status_listener(stage, message):
            asyncio.run_coroutine_threadsafe(_send_ml_status(stage, message), _loop)   # thread → event loop
        self._ml_status_listener = _ml_status_listener   # remembered so disconnect() can remove it
        add_ml_status_listener(_ml_status_listener)

    # Runs when the browser goes away. The listener must be removed or the
    # detector keeps a reference to a dead connection.
    async def disconnect(self, code):
        logger.info("[WS] Disconnecting with code %s", code)
        from .ml.detection import remove_ml_status_listener
        if hasattr(self, '_ml_status_listener'):     # connect() may have failed before setting it
            remove_ml_status_listener(self._ml_status_listener)

    # ── frame receive ──────────────────────────────────────────────────────────

    # The main loop: one camera frame in, tracked boxes out, and OCR or a gate
    # decision started when there is something worth deciding.
    async def receive_json(self, content):
        if content.get("type") != "frame":
            return                                   # other message types are not this consumer's business
        image_b64 = content.get("image_b64", "")
        if not image_b64:
            return                                   # an empty frame: nothing to look at

        try:
            image_bytes = base64.b64decode(image_b64)    # text back into the raw JPEG bytes
        except Exception as exc:
            await self.send_json({"type": "error", "message": str(exc)})   # malformed frame: tell the browser, stay connected
            return

        # FPS accounting
        # Measured over every 10 frames rather than each one, so the number the
        # guard sees does not jitter.
        self._frame_counter += 1
        self._fps_counter += 1
        now_ts = time.time()
        if self._fps_start is None or self._fps_counter >= 10:
            if self._fps_start:
                self._fps = 10.0 / (now_ts - self._fps_start)   # 10 frames divided by how long they took
            self._fps_start = now_ts
            self._fps_counter = 0

        # Rate-limit: drop frames that arrive faster than FRAME_RATE_LIMIT_MS
        current_ms = now_ts * 1000
        if current_ms - self._last_process_time < FRAME_RATE_LIMIT_MS:
            return                                   # too soon: skip this frame entirely
        self._last_process_time = current_ms

        # Guard: don't queue another detection while the previous one is running
        if self._detection_in_progress:
            return                                   # the detector is still busy; dropping a frame is better than a backlog
        self._detection_in_progress = True

        loop = asyncio.get_running_loop()
        try:
            # Detection is heavy and blocking, so it runs in a worker thread —
            # otherwise it would freeze every other connection this process serves.
            detections = await loop.run_in_executor(None, self._run_detection, image_bytes)
        except Exception as exc:
            logger.error("[WS] Detection error: %s", exc)
            detections = []                          # treat a failed frame as "nothing seen" and keep going
        finally:
            self._detection_in_progress = False      # always release the guard, success or not

        now = timezone.now()
        tracker_output = self._tracker.update(detections, img_w=getattr(self, "_last_img_w", 640))   # boxes → stable track ids
        det_by_idx = {i: d for i, d in enumerate(detections)}   # so a track can find the detection it came from

        # Evict OCR state for tracks the tracker has expired — prevents unbounded growth
        active_ids = set(self._tracker.tracks.keys())
        for stale_id in list(self._ocr_state.keys()):    # list(): the dictionary is edited inside the loop
            if stale_id not in active_ids:
                self._ocr_state.pop(stale_id, None)
                self._pending_ocr.pop(stale_id, None)

        active_tracks = []                           # what the browser will draw
        tracks_needing_ocr = []                      # plates not yet read
        tracks_to_reverify = []                      # plates already locked, due a quiet re-check

        # Sort every tracked box into those three buckets.
        for t_out in tracker_output:
            track_id    = t_out["track_id"]
            bbox        = t_out["bbox"]
            x, y, bw, bh = bbox["x"], bbox["y"], bbox["width"], bbox["height"]   # pixel box: left, top, width, height

            class_name   = t_out.get("class_name", "")     # "license_plate" or a vehicle class
            vehicle_type = t_out.get("vehicle_type")
            plate_text   = t_out.get("plate_text", "")     # filled once the track locks
            ocr_done     = t_out.get("ocr_done", False)

            d_idx = t_out.get("detection_index")           # which detection this track matched this frame
            det   = det_by_idx.get(d_idx) if d_idx is not None else None

            if (det and det.get("class_name") == "license_plate"
                    and det.get("crop") is not None):      # only a plate crop can be read
                if not ocr_done:
                    tracks_needing_ocr.append(             # never read: queue a first read
                        (track_id, det["crop"], det.get("aspect_ratio", 1.0))
                    )
                else:
                    st = self._ocr_state.get(track_id)
                    if (st and st.get("locked")
                            and now_ts - st.get("verify_at", 0.0) >= _OCR_REVERIFY_SECONDS):   # due another look
                        st["verify_at"] = now_ts  # claim before queueing
                        tracks_to_reverify.append(
                            (track_id, det["crop"], det.get("aspect_ratio", 1.0))
                        )

            w_img = getattr(self, "_last_img_w", 640)     # frame size, set by _run_detection
            h_img = getattr(self, "_last_img_h", 480)
            active_tracks.append({
                "track_id":      track_id,
                "plate_text":    plate_text,
                "vehicle_type":  vehicle_type,
                "class_name":    class_name,
                # Box as fractions of the frame (0-1), so the browser can draw it
                # at whatever size it displays the video; max(...,1) avoids a
                # divide-by-zero if the size is somehow unknown.
                "bbox":          [x / max(w_img, 1), y / max(h_img, 1),
                                  (x + bw) / max(w_img, 1), (y + bh) / max(h_img, 1)],
                "detection_conf": det.get("confidence", 0.0) if det else 0.0,
            })

        # Send the boxes straight away, so the overlay keeps up with the video
        # even while the slower OCR and gate work is still running.
        await self.send_json({
            "type":     "tracks",
            "tracks":   active_tracks,
            "frame_id": self._frame_counter,
            "fps":      round(self._fps, 1),
        })

        # Start the slow work in the background: create_task() means this frame
        # is finished with now, and the next one is not held up.
        if tracks_needing_ocr:
            asyncio.create_task(self._run_ocr_for_tracks(tracks_needing_ocr))
        if tracks_to_reverify:
            asyncio.create_task(self._reverify_locked_tracks(tracks_to_reverify))

        # Refresh presence for tracks whose plates are already known — keeps the
        # sliding dedup window open while the vehicle stays in view
        if any(t.get("plate_text") for t in active_tracks):
            await self._process_scan_results(active_tracks, now)

    # ── detection (sync, runs in executor) ────────────────────────────────────

    # Finds plates and vehicles in one frame. Ordinary blocking code: it is
    # called through run_in_executor, so it runs in a worker thread.
    def _run_detection(self, image_bytes: bytes) -> list[dict]:
        import cv2                                   # imported here to keep start-up light
        import numpy as np
        nparr = np.frombuffer(image_bytes, np.uint8)     # JPEG bytes as a number array
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)      # decode into an actual image
        if img is None:
            return []                                # not a readable image: nothing detected

        h, w = img.shape[:2]                         # height and width in pixels
        self._last_img_w = w                         # remembered so boxes can be turned into fractions
        self._last_img_h = h

        # Skip rotation passes only when every *plate-class* track is already locked.
        # Previously used all() on an empty iterable (when only vehicle tracks exist),
        # which returned True and incorrectly disabled rotation before any plate was found.
        plate_tracks = [t for t in self._tracker.tracks.values()
                        if t.class_name == "license_plate"]
        all_plates_locked = bool(plate_tracks) and all(t.ocr_done for t in plate_tracks)
        # Rotation passes cost time, so they are skipped only when every plate
        # on screen is already read.
        detections = detect_plates(img, try_rotation=not all_plates_locked)

        result = []
        for i, det in enumerate(detections):
            bbox = det["bbox"]
            result.append({
                # The detector returns fractions of the frame; convert to pixels,
                # which is what the tracker compares boxes in.
                "bbox": {
                    "x":      int(bbox["x"] * w),
                    "y":      int(bbox["y"] * h),
                    "width":  int(bbox["width"] * w),
                    "height": int(bbox["height"] * h),
                },
                "crop":            det.get("crop"),
                "confidence":      det["score"],
                "aspect_ratio":    det.get("aspect_ratio", 1.0),
                "class_name":      det.get("class_name", ""),
                "vehicle_type":    det.get("vehicle_type"),
                "detection_index": i,
            })
        return result

    # ── OCR (async task per track, with confidence accumulation) ──────────────

    # Reads the plate text for tracks that do not have one yet, gathering votes
    # across frames until one reading wins and the track locks.
    async def _run_ocr_for_tracks(self, tracks_to_process: list):
        loop = asyncio.get_running_loop()

        for track_id, crop, aspect in tracks_to_process:
            if track_id in self._pending_ocr:
                continue                             # a read for this track is already running

            state = self._ocr_state.get(track_id)
            if state and state["locked"]:
                continue                             # already decided; re-verification handles it from here

            self._pending_ocr[track_id] = True       # claim this track
            try:
                plate_text, conf = await loop.run_in_executor(None, _ocr_crop, crop, aspect)   # reading is blocking too
                if conf is None:
                    conf = 0.0                       # treat "no confidence given" as none at all

                state = self._ocr_state.setdefault(track_id, {   # first read for this track creates its record
                    "votes": {}, "attempts": 0, "locked": False,
                })
                state["attempts"] += 1

                if not plate_text or conf < _OCR_MIN_CONF:
                    # Low-quality read — force-lock if we've hit the attempt limit
                    if state["attempts"] >= _OCR_MAX_ATTEMPTS and state["votes"]:
                        best = max(state["votes"], key=state["votes"].get)   # the text with the most weight so far
                        state["locked"] = True
                        state["verify_at"] = time.time()   # start the re-verification clock
                        self._tracker.set_plate_text(track_id, best)
                        logger.info("[WS] Max attempts (no lock) track %d → %s", track_id, best)
                        await self._finalize_plate(track_id, best, 0.0)   # decide the gate on the best guess
                    continue                         # nothing usable this time; wait for another frame

                # Accumulate confidence-weighted votes across reads.
                # Reads matching a valid PH plate format get triple weight so a
                # correct read outvotes garbled partials when the track locks.
                normalized = plate_text.strip().upper().replace(' ', '')
                weight = conf * (3.0 if is_valid_ph_plate(normalized) else 1.0)   # plausible plates count triple
                state["votes"][plate_text] = state["votes"].get(plate_text, 0.0) + weight   # add this vote to the tally
                best = max(state["votes"], key=state["votes"].get)   # whichever text leads right now

                # Always push the current best to the overlay immediately
                await self.send_json({
                    "type":       "ocr_update",
                    "track_id":   track_id,
                    "plate_text": best,
                })
                logger.info("[WS] OCR track %d read=%d → %s (conf=%.2f)",
                            track_id, state["attempts"], best, conf)

                # Lock when confident or attempts exhausted
                if conf >= _OCR_LOCK_CONF or state["attempts"] >= _OCR_MAX_ATTEMPTS:
                    state["locked"] = True           # stop reading this track...
                    state["verify_at"] = time.time() # ...but start the quiet re-check clock
                    self._tracker.set_plate_text(track_id, best)   # the track now carries the plate
                    logger.info("[WS] Locked track %d → %s (conf=%.2f, attempts=%d)",
                                track_id, best, conf, state["attempts"])
                    await self._finalize_plate(track_id, best, conf)   # now the gate can decide

            except Exception as exc:
                logger.warning("[OCR] Failed for track %d: %s", track_id, exc)   # one bad read must not kill the loop
            finally:
                self._pending_ocr.pop(track_id, None)   # always release the claim

    # Takes a newly locked plate through the gate decision and tells this
    # browser the outcome, exactly once.
    async def _finalize_plate(self, track_id: int, plate_text: str, conf: float):
        """Process a freshly locked plate and broadcast the decision once."""
        result = await self._handle_plate_sighting(track_id, plate_text, 0.0, conf, None)
        if result:                                   # None means "already decided and announced"
            await self.send_json({"type": "result", "results": [result]})

    # ── locked-plate re-verification ───────────────────────────────────────────

    async def _reverify_locked_tracks(self, tracks_to_verify: list):
        """
        Quietly re-read plates on locked tracks (every _OCR_REVERIFY_SECONDS).

        A read matching the locked text changes nothing — no UI events, no DB
        writes. A different plate needs either one strong read or two agreeing
        reads to switch; the track then re-locks to the new text and the plate
        runs through the normal presence pipeline, so per-plate cooldowns
        (1-minute unknown/denied window etc.) still decide what gets reported.
        Catches physical plate swaps and corrects early misreads.
        """
        loop = asyncio.get_running_loop()

        for track_id, crop, aspect in tracks_to_verify:
            if track_id in self._pending_ocr:
                continue                             # a read is already in flight for this track
            state = self._ocr_state.get(track_id)
            track = self._tracker.get_track(track_id)
            if not state or not state.get("locked") or track is None:
                continue                             # the track went away, or was never locked

            self._pending_ocr[track_id] = True
            try:
                plate_text, conf = await loop.run_in_executor(None, _ocr_crop, crop, aspect)
                conf = conf or 0.0
                plate_norm = (plate_text or "").strip().upper().replace(" ", "")   # what was just read
                current    = (track.plate_text or "").strip().upper().replace(" ", "")   # what the track holds

                if not plate_norm or conf < _OCR_MIN_CONF:
                    continue  # unreadable frame — keep the current lock

                if plate_norm == current:
                    state.pop("switch_reads", None)  # confirmed — drop any switch candidate
                    continue

                # Never re-lock a track onto an invalid plate format
                if not is_valid_ph_plate(plate_norm):
                    continue

                # A different, plausible plate: count how many times it has been
                # read before believing it over the locked one.
                reads = state.setdefault("switch_reads", {})
                if len(reads) > 5:
                    reads = state["switch_reads"] = {}  # noisy garbage — start over
                reads[plate_norm] = reads.get(plate_norm, 0) + 1

                # Switch on one strong read, or two agreeing ordinary ones.
                if conf >= _REVERIFY_STRONG_CONF or reads[plate_norm] >= 2:
                    state["switch_reads"] = {}       # candidate accepted; clear the tally
                    state["votes"] = {plate_norm: conf}   # the new text starts as the only vote
                    state["verify_at"] = time.time()
                    self._tracker.set_plate_text(track_id, plate_norm)   # the track now means a different car
                    logger.info("[WS] Re-verify: track %d plate %s -> %s (conf=%.2f)",
                                track_id, current or "?", plate_norm, conf)
                    await self.send_json({
                        "type": "ocr_update", "track_id": track_id, "plate_text": plate_norm,
                    })
                    await self._finalize_plate(track_id, plate_norm, conf)   # decide the gate for the new plate
                else:
                    # One differing read — re-check shortly to confirm or dismiss
                    state["verify_at"] = time.time() - (_OCR_REVERIFY_SECONDS - 2.0)   # backdate the clock: due again in ~2s
            except Exception as exc:
                logger.warning("[OCR] Re-verify failed for track %d: %s", track_id, exc)
            finally:
                self._pending_ocr.pop(track_id, None)

    # ── presence-aware scan processing ─────────────────────────────────────────

    async def _handle_plate_sighting(self, track_id: int, plate_text: str,
                                     det_conf: float, ocr_conf: float, bbox):
        """
        Record a sighting of `plate_text` and return a result to announce, or None.

        While the plate stays in view, its `last_seen` slides forward and the
        existing decision is held — repeated reads of the current state are
        suppressed. The entry/exit state machine (_check_vehicle) runs again
        when the decision's hold time expires (even if the vehicle never left
        the frame) or when the plate re-appears after `scan_dedup_seconds` out
        of view — so the flow advances Entry → Exit → Entry both across genuine
        appearances and for a vehicle waiting at the gate.
        Each client is told about a given decision exactly once.
        """
        plate = plate_text.strip().upper().replace(' ', '')
        # OCR noise gate: only valid Philippine plate formats reach the lookup,
        # the access log, or the violation pipeline — partial/garbled reads
        # (e.g. "8946", "C946") are dropped here.
        if not plate or not is_valid_ph_plate(plate):
            return None

        now_ts = time.time()
        needs_decision = False
        # Everything inside this lock is about the SHARED registry, so decisions
        # are made once per plate even with several cameras connected.
        with _PRESENCE_LOCK:
            entry = _PLATE_PRESENCE.get(plate)       # what we already know about this plate, if anything
            in_view = entry is not None and (now_ts - entry["last_seen"]) < self._dedup_seconds
            if in_view:
                entry["last_seen"] = now_ts   # sliding window — still in view
            hold_expired = (                         # has the decision been held long enough to redo?
                entry is None
                or (now_ts - entry["decided_at"]) >= self._result_hold_seconds(entry["result"])
            )
            # Decide again when the car is newly here, or the hold ran out —
            # unless another task is already deciding this very plate.
            if (not in_view or hold_expired) and plate not in _PLATES_IN_FLIGHT:
                _PLATES_IN_FLIGHT.add(plate)         # claim it, so no duplicate database writes
                needs_decision = True

        if needs_decision:
            try:
                await sync_to_async(self._save_to_db)(   # keep the raw reading for diagnosis
                    track_id, plate, det_conf, ocr_conf, bbox, None
                )
                enriched = await sync_to_async(          # the actual gate decision (entry/exit, rules, violations)
                    self._check_vehicle, thread_sensitive=True
                )(plate, bbox)
                enriched["plate_number"] = plate
                ts = time.time()
                with _PRESENCE_LOCK:
                    _PLATE_PRESENCE[plate] = {           # publish it, so every camera holds the same answer
                        "result": enriched, "decided_at": ts, "last_seen": ts,
                    }
                await sync_to_async(self._record_ml_sample)(None, [enriched])   # keep the image for future training
                logger.info("[WS] Plate %s decided → %s", plate, enriched.get("status"))
            except Exception as exc:
                logger.error("[WS] Scan processing failed for %s: %s", plate, exc)
                # Remember the failure so we don't retry every frame and spam the log
                ts = time.time()
                with _PRESENCE_LOCK:
                    _PLATE_PRESENCE[plate] = {
                        "result": {"plate_number": plate, "error": True},   # marked as an error, so it is never announced
                        "decided_at": ts, "last_seen": ts,
                    }
            finally:
                with _PRESENCE_LOCK:
                    _PLATES_IN_FLIGHT.discard(plate)     # release the claim whatever happened
                self._evict_presence()                   # keep the shared registry from growing forever

        # Decide what, if anything, to tell THIS browser.
        with _PRESENCE_LOCK:
            entry = _PLATE_PRESENCE.get(plate)
        if not entry or entry["result"].get("error"):
            return None                                  # nothing usable to announce
        if (time.time() - entry["last_seen"]) >= self._dedup_seconds:
            return None   # stale decision awaiting replacement — don't announce it
        if self._announced.get(plate) == entry["decided_at"]:
            return None   # this client already received this decision
        self._announced[plate] = entry["decided_at"]     # remember what was sent, so it is sent once
        return entry["result"]

    # ── process tracks that already have plate text ────────────────────────────

    # Runs each frame for tracks that already carry a plate, which is what keeps
    # a waiting vehicle "present" and re-decided when its hold expires.
    async def _process_scan_results(self, tracks_list: list[dict], now):
        results = []
        processed_ids: set[int] = set()              # one sighting per track per frame

        for track_data in tracks_list:
            track_id     = track_data["track_id"]
            plate_number = track_data.get("plate_text", "")
            det_conf     = track_data.get("detection_conf", 0.0)

            if not plate_number.strip() or track_id in processed_ids:
                continue                             # no plate yet, or this track was handled already
            processed_ids.add(track_id)

            bbox = {                                 # rebuild the box as a dictionary for the decision code
                "x": track_data["bbox"][0], "y": track_data["bbox"][1],
                "width": track_data["bbox"][2], "height": track_data["bbox"][3],
            }
            result = await self._handle_plate_sighting(
                track_id, plate_number, det_conf, 0.0, bbox
            )
            if result:                               # None when there is nothing new to announce
                results.append({**result, "bbox": bbox})   # attach the box so the browser can point at the car

        if results:
            await self.send_json({"type": "result", "results": results})   # one message for all of them

    # ── presence maintenance ───────────────────────────────────────────────────

    # How long to sit on a decision before working it out again. The answer
    # depends on what was decided, which is why it is not a single constant.
    def _result_hold_seconds(self, result: dict) -> float:
        """How long a decision is held before the plate is re-evaluated in view."""
        status = result.get("status")
        if result.get("error") or status == "duplicate":
            return self._dedup_seconds            # transient — retry soon
        if status in ("authorized", "open_entry", "exited"):
            return CAMERA_ENTRY_COOLDOWN_SECONDS  # breathing space before state can flip
        return NEGATIVE_SCAN_COOLDOWN_SECONDS     # unknown / denied / wrong_day etc.

    # Housekeeping: the shared registry would otherwise keep every plate ever
    # seen for as long as the server runs.
    def _evict_presence(self):
        """Drop plates not seen for 2× the dedup window to bound memory."""
        cutoff = time.time() - self._dedup_seconds * 2   # anything older than this is long gone
        with _PRESENCE_LOCK:
            for p in [p for p, e in _PLATE_PRESENCE.items() if e["last_seen"] < cutoff]:   # list first: cannot delete while looping
                del _PLATE_PRESENCE[p]
            alive = set(_PLATE_PRESENCE)                  # what remains
        for p in [p for p in self._announced if p not in alive]:
            del self._announced[p]                        # forget what was announced for plates no longer tracked

    # ── sync helpers (run in executor / sync_to_async) ─────────────────────────

    # Writes a cropped plate image to disk and returns the path to store.
    def _save_snapshot(self, track_id: int, crop) -> str:
        import cv2
        from pathlib import Path
        snapshot_dir = Path(settings.MEDIA_ROOT) / SNAPSHOT_DIR
        snapshot_dir.mkdir(parents=True, exist_ok=True)   # create the folder the first time
        filename = f"plate_{track_id}_{int(datetime.now().timestamp())}.jpg"   # track + timestamp keeps names unique
        path = snapshot_dir / filename
        cv2.imwrite(str(path), crop)
        return f"{SNAPSHOT_DIR}/{filename}"          # a path relative to the media folder, not an absolute one

    # Records the raw reading (not the gate decision) for later diagnosis.
    def _save_to_db(self, track_id: int, plate_number: str, det_conf: float,
                    ocr_conf: float, bbox: dict, snapshot_path: str | None):
        from django.db import close_old_connections
        from .models import PlateRecognitionRecord
        close_old_connections()                      # this runs in a worker thread: drop any stale connection first
        PlateRecognitionRecord.objects.create(
            track_id=track_id,
            plate_text=plate_number,
            detection_confidence=det_conf,
            ocr_confidence=ocr_conf,
            timestamp=timezone.now(),
            snapshot_path=snapshot_path or "",
        )

    # =========================================================================
    # THE ENTRY / EXIT STATE MACHINE
    #
    # Everything above this point answers "which plate is it?". This answers
    # "so what happens?" — and it is the densest logic in the project, because
    # one plate in front of a camera can mean several different things.
    #
    # A vehicle is only ever in one of two states, and the state is not stored
    # anywhere: it is DERIVED from the access log by _inside_state(), which
    # looks at the most recent entry/exit rows for that plate.
    #
    #   OUTSIDE  → a scan means "asking to come in"  → decide, then log an entry
    #   INSIDE   → a scan means "leaving"            → log an exit, paired to the entry
    #
    # The branches below, in the order they are tried:
    #
    #   1. No Vehicle record at all
    #        a. an event organizer's plate, while the event is on   → admit
    #        b. a supplier's plate                                  → hand to _check_supplier
    #        c. Open Campus Mode                                    → admit as "open entry"
    #        d. otherwise                                           → log "unknown", refuse
    #   2. 'duplicate'  → the same scan seconds ago; ignore it
    #   3. 'inside'     → visitor on a pass? the guard handles the exit
    #                     just entered? stay quiet for the cooldown
    #                     otherwise                                 → record the EXIT
    #   4. just exited  → suppress the immediate re-entry
    #   5. outside      → ask the rules (check_entry), log it, maybe raise a violation
    #
    # Each branch returns a dictionary in the same shape, because the browser
    # renders whatever comes back without knowing which branch produced it.
    #
    # This runs in a worker thread (via sync_to_async), which is why it opens
    # with close_old_connections() and why every import is local.
    # =========================================================================
    def _check_vehicle(self, plate_number: str, bbox):
        from django.db import close_old_connections
        from vehicles.models import Vehicle, VehicleRegistration, SupplierPlate
        from .models import AccessLog
        from .entry_logic import check_entry           # the campus rules: may this owner enter now?
        from violations.models import Violation
        from vehicles.serializers import VehicleSerializer
        # These helpers are shared with the manual (typed) scan path in views.py,
        # so the camera and a guard typing a plate reach the same decisions.
        from .views import (_inside_state, _in_exit_cooldown, _already_inside,
                            _auto_log_violation, _close_active_pass, _gate_label,
                            _check_stay_limit, _log_status, _open_campus_unknown_result,
                            _is_standby_fetcher, _has_open_violations)
        from .entry_logic import is_open_campus
        close_old_connections()                        # worker thread: drop any connection left from a previous task

        # Normalize so OCR output matches the stored plate (e.g. "ABC 123" → "ABC123")
        plate_number = plate_number.strip().upper().replace(' ', '')

        vehicle = Vehicle.objects.select_related("user").filter(   # fetch the owner in the same query
            plate_number=plate_number
        ).first()                                      # None when this plate is not registered

        gate_id = getattr(self, '_gate_id', 'main')    # which gate this camera belongs to

        # ── BRANCH 1: no Vehicle record for this plate ────────────────────────
        # Three kinds of vehicle may still be admitted without one, tried in
        # this order because the earlier reason outranks the later.
        if not vehicle:
            # An organizer's unregistered plate during its event — the same
            # entry/exit and event slip the manual and image scans give. Ahead
            # of the supplier roster: a supplier listed as an organizer enters
            # for the event while it is on.
            from .views import _event_for_unregistered_plate, _event_plate_result
            event = _event_for_unregistered_plate(plate_number)
            if event:
                result = _event_plate_result(plate_number, event, gate_id, self._user)
                result.setdefault("registration", None)   # keep the shape every branch returns
                result.setdefault("constraint", None)
                return result

            # Supplier vehicles have no Vehicle/owner record — permitted by plate list
            supplier_plate = SupplierPlate.objects.select_related('supplier').filter(
                plate_number=plate_number, supplier__is_active=True   # only companies still in service
            ).first()
            if supplier_plate:
                return self._check_supplier(plate_number, supplier_plate, gate_id)   # its own state machine, below

            # Open Campus Mode — unregistered plates are admitted with the full
            # entry/exit state machine and shown as "Open Entry".
            if is_open_campus():
                result = _open_campus_unknown_result(plate_number, gate_id, self._user)
                result.setdefault("registration", None)
                result.setdefault("has_violations", False)
                return result

            # Nothing admits this plate. Before logging it as unknown, check we
            # have not just logged the same thing — a car idling in view would
            # otherwise write a row every time the hold expires.
            now = timezone.now()
            cutoff = now - timedelta(seconds=NEGATIVE_SCAN_COOLDOWN_SECONDS)   # one minute ago
            recent_unknown = AccessLog.objects.filter(
                plate_number=plate_number,
                status="unknown",
                scanned_at__gte=cutoff,
                scanned_at__lte=now,  # future-dated rows (clock skew) must not wedge the gate
            ).exists()
            if recent_unknown:
                return {
                    "status":         "duplicate",
                    "allowed":        False,
                    "message":        "Duplicate scan — unregistered plate already logged within cooldown.",
                    "constraint":     None,
                    "vehicle":        None,
                    "registration":   None,
                    "has_violations": False,
                    "already_inside": False,
                }
            AccessLog.objects.create(                  # record that an unknown plate was seen here
                plate_number=plate_number,
                status="unknown",
                gate_id=gate_id,
                scanned_by=self._user,
            )
            return {
                "status":         "unknown",
                "allowed":        False,
                "message":        "Plate not registered.",
                "constraint":     None,
                "vehicle":        None,               # there is no vehicle record to describe
                "registration":   None,
                "has_violations": False,
            }

        # ── The vehicle IS registered. Where is it now? ───────────────────────
        # The state is derived from the access log, not stored: 'inside',
        # 'duplicate' (a scan moments ago) or anything else meaning outside.
        # last_entry is the entry row an exit would be paired with.
        inside_status, last_entry = _inside_state(plate_number)

        # ── BRANCH 2: the same scan again, within the grace period ────────────
        if inside_status == 'duplicate':
            return {
                "status":         "duplicate",
                "allowed":        False,
                "message":        "Duplicate scan — already processed within grace period.",
                "vehicle":        VehicleSerializer(vehicle).data,
                "has_violations": False,
                "already_inside": True,
            }

        # ── BRANCH 3: the vehicle is inside, so this scan means "leaving" ─────
        if inside_status == 'inside':
            # The camera never logs a visitor out on its own. It hands the guard
            # the slip, and the guard records the exit (or reprints) from it.
            from .views import _active_visitor_pass
            from .slips import visitor_slip
            visitor_pass = _active_visitor_pass(plate_number)
            if visitor_pass:
                return {
                    "status":         "visitor_pass_required",
                    "allowed":        False,
                    "message":        "Visitor is inside on an active pass.",
                    "slip":           visitor_slip(visitor_pass),
                    "vehicle":        VehicleSerializer(vehicle).data,
                    "has_violations": False,
                    "already_inside": True,
                }
            # A car that just drove in is still in front of the camera. Without
            # this window the very next frame would be read as it leaving again.
            seconds_since_entry = (timezone.now() - last_entry.scanned_at).total_seconds()
            if seconds_since_entry < CAMERA_ENTRY_COOLDOWN_SECONDS:
                # Within the 1-minute breathing space — ignore
                return {
                    "status":         "already_inside",
                    "allowed":        False,
                    "message":        "Vehicle just entered — within the 1-minute entry window.",
                    "vehicle":        VehicleSerializer(vehicle).data,
                    "has_violations": False,
                    "already_inside": True,
                }
            # Recording the exit. Two cameras (or a camera and a guard) can read
            # the same plate at once, so the entry row is locked first and the
            # write happens inside a transaction — that is what stops one drive
            # out producing two exit rows.
            from django.db import transaction as _tx
            exit_log = None
            with _tx.atomic():                         # all-or-nothing: either the exit is written or nothing is
                locked_entry = AccessLog.objects.select_for_update().filter(   # hold this row until the block ends
                    pk=last_entry.pk
                ).first()
                # Either the entry vanished, or somebody else paired an exit to
                # it while we waited for the lock. Both mean: already handled.
                if not locked_entry or AccessLog.objects.filter(paired_entry=locked_entry).exists():
                    return {
                        "status":         "duplicate",
                        "allowed":        False,
                        "message":        "Duplicate scan — already processed.",
                        "vehicle":        VehicleSerializer(vehicle).data,
                        "has_violations": False,
                        "already_inside": False,
                    }
                exit_log = AccessLog.objects.create(
                    plate_number=plate_number,
                    vehicle=vehicle,
                    status=AccessLog.Status.EXITED,
                    gate_id=gate_id,
                    scanned_by=self._user,
                    paired_entry=locked_entry,         # the link that makes this pair a complete visit
                )
            delta = exit_log.scanned_at - last_entry.scanned_at
            duration_minutes = int(delta.total_seconds() / 60)   # how long they were inside, whole minutes
            # Closing any visitor pass may itself reveal an overstay.
            overstay_minutes = _close_active_pass(plate_number, gate_id)
            # Drop-and-go fetchers have a maximum stay; standby fetchers are
            # allowed to wait, so they are excluded from the check.
            if vehicle.user and vehicle.user.owner_type == 'fetcher' and not _is_standby_fetcher(vehicle.user):
                overstay_minutes = max(overstay_minutes, _check_stay_limit(   # keep whichever overstay is larger
                    plate_number, vehicle, 'fetcher', duration_minutes, gate_id))
            overstay_note = f" Overstayed by {overstay_minutes} min." if overstay_minutes else ""   # only mentioned when it happened
            owner_name = vehicle.user.full_name if vehicle.user else 'Unknown'   # the vehicle may have lost its owner
            return {
                "status":           "exited",
                "allowed":          False,
                "message":          f"{owner_name} — Exit recorded. Duration: {duration_minutes} min.{overstay_note}",
                "vehicle":          VehicleSerializer(vehicle).data,
                "has_violations":   False,
                "already_inside":   False,
                "duration_minutes": duration_minutes,
                "overstay_minutes": overstay_minutes,
            }

        # ── BRANCH 4: it just left, and is still in view ──────────────────────
        # The mirror image of the entry window above: without it, a car driving
        # away would immediately be read as arriving again.
        if _in_exit_cooldown(plate_number):
            return {
                "status":         "duplicate",
                "allowed":        False,
                "message":        "Exit cooldown — entry suppressed for 1 minute after exit.",
                "vehicle":        VehicleSerializer(vehicle).data,
                "has_violations": False,
                "already_inside": False,
            }

        # ── BRANCH 5: the vehicle is outside, so this is a request to enter ───
        # Everything up to here was about timing and state; this is where the
        # campus rules finally get asked (see scanning/entry_logic.py).
        entry = check_entry(vehicle)

        # UI-only statuses (e.g. 'no_pass', 'open_entry') aren't valid AccessLog
        # statuses — store those rows as authorized/denied per the decision while
        # the client still sees the real status
        log_status = _log_status(entry)                # the value that is valid to STORE, which may differ from what is shown

        # Authorized entries are deduped by the grace-period / entry-window checks
        # above; denied/violation statuses get a 1-minute DB-backed cooldown so a
        # vehicle idling at the gate doesn't flood the log across WS reconnects.
        if not entry["allowed"]:
            now = timezone.now()
            cutoff = now - timedelta(seconds=NEGATIVE_SCAN_COOLDOWN_SECONDS)
            recent_same = AccessLog.objects.filter(    # was this same refusal logged in the last minute?
                plate_number=plate_number,
                status=log_status,
                scanned_at__gte=cutoff,
                scanned_at__lte=now,  # future-dated rows (clock skew) must not wedge the gate
            ).exists()
            if recent_same:
                return {
                    "status":         "duplicate",
                    "allowed":        False,
                    "message":        "Duplicate scan — result already logged within cooldown.",
                    "vehicle":        VehicleSerializer(vehicle).data,
                    "has_violations": False,
                    "already_inside": False,
                }

        # Vehicle OR account — see _has_open_violations; the ladder is per account.
        has_violations = _has_open_violations(vehicle)
        already_inside = _already_inside(plate_number)  # shown to the guard; does not change the decision here

        # The scan is recorded whatever was decided: refusals matter as much as
        # entries, and this row is what the next scan's state is derived from.
        AccessLog.objects.create(
            plate_number=plate_number,
            vehicle=vehicle,
            status=log_status,
            denied_reason="" if entry["allowed"] else entry["message"],   # only a refusal carries a reason
            gate_id=gate_id,
            scanned_by=self._user,
        )

        # A visitor waiting for a pass ('no_pass'/'unknown') isn't a violation —
        # only genuinely denied/wrong-day entries are auto-fined.
        issued = None
        if not entry["allowed"] and entry["status"] not in ("no_pass", "unknown"):
            try:
                issued = _auto_log_violation(
                    vehicle, entry["message"], gate_id,
                    entry_status=entry["status"])
            except Exception:
                pass                                   # a violation that cannot be raised must not lose the scan record

        # Fetch registration details for non-visitor plates
        # This is for the guard's screen only — the decision is already made.
        registration_data = None
        owner_type = vehicle.user.owner_type if vehicle.user else None
        if owner_type and owner_type != 'visitor':     # visitors have a pass, not a registration
            try:
                reg = (
                    vehicle.registrations.filter(      # preferred: a registration linked to this vehicle
                        status='accepted'
                    ).order_by('-reviewed_at').first()
                    or VehicleRegistration.objects.filter(   # fallback: one matching the plate but never linked
                        plate_number=vehicle.plate_number,
                        status='accepted',
                    ).order_by('-reviewed_at').first()       # most recently approved wins
                )
                if reg:
                    registration_data = {
                        'registrant_type': reg.registrant_type,
                        'campus_days':     reg.campus_days,
                        'schedule':        reg.schedule,
                        'or_number':       reg.or_number,
                        'student_id':      reg.student_id,
                        'program_year':    reg.program_year,
                        'employee_id':     reg.employee_id,
                        'department_name': reg.department.name if reg.department else '',
                        'reviewed_at':     reg.reviewed_at.isoformat() if reg.reviewed_at else None,
                    }
            except Exception:
                pass                                   # extra detail is a nicety; never fail the scan over it

        # A registered vehicle may ALSO be listed as an event organizer, which
        # the guard's screen shows as a badge.
        try:
            from .entry_logic import get_organizer_event, vehicle_identifiers
            organizer_event = get_organizer_event(*vehicle_identifiers(vehicle, plate_number))   # match by plate, conduction or typed text
        except Exception:
            organizer_event = None                     # same reasoning: decoration must not break the decision

        # The single shape every branch of this method returns.
        return {
            "status":          entry["status"],        # what to show ('authorized', 'wrong_day', ...)
            "allowed":         entry["allowed"],       # whether the barrier should open
            "message":         entry["message"],       # the sentence the guard reads
            "constraint":      entry.get("constraint"),  # which rule decided it, when one did
            "vehicle":         VehicleSerializer(vehicle).data,
            "registration":    registration_data,
            "has_violations":  has_violations,
            "already_inside":  already_inside,
            "organizer_event": organizer_event,
            "violation":       issued,                 # what a refusal cost them, for the result card
        }

    # The same state machine as _check_vehicle, for a plate on a supplier's
    # roster. It is separate because a supplier has no account and no
    # registration: permission comes from the company being active, and the
    # only rule that can refuse is the supplier delivery window. The shape of
    # the returned dictionary matches, plus is_supplier/supplier_name.
    def _check_supplier(self, plate_number: str, supplier_plate, gate_id: str):
        """Entry/exit state machine for supplier plates (auto-permitted, no owner account).
        Mirrors the supplier branch of ManualEntryView so camera and manual paths agree."""
        from django.db import transaction as _tx
        from .models import AccessLog
        from .views import (_inside_state, _in_exit_cooldown, _gate_label,
                            _check_stay_limit, _supplier_rule_denial)

        supplier_name = supplier_plate.supplier.company_name   # named in every message the guard sees
        inside_status, last_entry = _inside_state(plate_number)   # same derived state as for a registered vehicle

        # Same first branch: the identical scan moments ago.
        if inside_status == 'duplicate':
            return {
                "status":         "duplicate",
                "allowed":        False,
                "message":        "Duplicate scan — already processed within grace period.",
                "is_supplier":    True,
                "supplier_name":  supplier_name,
                "vehicle":        None,
                "registration":   None,
                "has_violations": False,
                "already_inside": True,
            }

        # Inside → this scan is the truck leaving.
        if inside_status == 'inside':
            seconds_since_entry = (timezone.now() - last_entry.scanned_at).total_seconds()
            if seconds_since_entry < CAMERA_ENTRY_COOLDOWN_SECONDS:   # still the arrival, seen again
                return {
                    "status":         "already_inside",
                    "allowed":        False,
                    "message":        "Supplier vehicle just entered — within the 1-minute entry window.",
                    "is_supplier":    True,
                    "supplier_name":  supplier_name,
                    "vehicle":        None,
                    "registration":   None,
                    "has_violations": False,
                    "already_inside": True,
                }
            # Same locking as the registered-vehicle exit: one drive out must
            # not produce two exit rows.
            with _tx.atomic():
                locked_entry = AccessLog.objects.select_for_update().filter(pk=last_entry.pk).first()
                if not locked_entry or AccessLog.objects.filter(paired_entry=locked_entry).exists():
                    return {
                        "status":         "duplicate",
                        "allowed":        False,
                        "message":        "Duplicate scan — already processed.",
                        "is_supplier":    True,
                        "supplier_name":  supplier_name,
                        "vehicle":        None,
                        "registration":   None,
                        "has_violations": False,
                        "already_inside": False,
                    }
                exit_log = AccessLog.objects.create(
                    plate_number=plate_number,
                    status=AccessLog.Status.EXITED,
                    gate_id=gate_id,
                    scanned_by=self._user,
                    paired_entry=locked_entry,
                )
            delta = exit_log.scanned_at - last_entry.scanned_at
            duration_minutes = int(delta.total_seconds() / 60)
            # Suppliers have their own maximum stay; None is passed where a
            # Vehicle would go, because a supplier plate has no vehicle record.
            overstay_minutes = _check_stay_limit(
                plate_number, None, 'supplier', duration_minutes, gate_id)
            overstay_note = f" Overstayed by {overstay_minutes} min — violation issued." if overstay_minutes else ""
            return {
                "status":           "exited",
                "allowed":          False,
                "message":          f"Supplier vehicle — {supplier_name}. Exit recorded. Duration: {duration_minutes} min.{overstay_note}",
                "is_supplier":      True,
                "supplier_name":    supplier_name,
                "vehicle":          None,
                "registration":     None,
                "has_violations":   False,
                "already_inside":   False,
                "duration_minutes": duration_minutes,
                "overstay_minutes": overstay_minutes,
            }

        if _in_exit_cooldown(plate_number):
            return {
                "status":         "duplicate",
                "allowed":        False,
                "message":        "Exit cooldown — entry suppressed for 1 minute after exit.",
                "is_supplier":    True,
                "supplier_name":  supplier_name,
                "vehicle":        None,
                "registration":   None,
                "has_violations": False,
                "already_inside": False,
            }

        # Outside → a request to come in. The only rule that can refuse a
        # supplier is the delivery window; returns a sentence, or nothing.
        deny_msg = _supplier_rule_denial()
        if deny_msg:
            # DB-backed dedup so an idling supplier truck doesn't flood the log
            now = timezone.now()
            cutoff = now - timedelta(seconds=NEGATIVE_SCAN_COOLDOWN_SECONDS)
            recent_denied = AccessLog.objects.filter(
                plate_number=plate_number, status=AccessLog.Status.DENIED,
                scanned_at__gte=cutoff, scanned_at__lte=now,
            ).exists()
            if not recent_denied:                      # log the refusal once per minute, then just answer
                AccessLog.objects.create(
                    plate_number=plate_number, status=AccessLog.Status.DENIED,
                    denied_reason=deny_msg, gate_id=gate_id, scanned_by=self._user,
                )
            return {
                "status":         "denied",
                "allowed":        False,
                "message":        deny_msg,
                "is_supplier":    True,
                "supplier_name":  supplier_name,
                "vehicle":        None,
                "registration":   None,
                "has_violations": False,
                "already_inside": False,
            }

        # Permitted: record the entry. This row is what a later scan will read
        # as "inside", and what the exit will be paired to.
        entry_log = AccessLog.objects.create(
            plate_number=plate_number,
            status=AccessLog.Status.AUTHORIZED,
            gate_id=gate_id,
            scanned_by=self._user,
        )

        from .entry_logic import is_open_campus
        from .slips import supplier_slip
        open_campus = is_open_campus()                 # only changes the wording and the status shown
        return {
            "status":         "open_entry" if open_campus else "authorized",
            "allowed":        True,
            "message":        (f"Open Campus Mode active — Supplier vehicle {supplier_name}. Open entry granted."
                               if open_campus else
                               f"Supplier vehicle — {supplier_name}. Entry permitted."),
            "is_supplier":    True,
            "supplier_name":  supplier_name,
            "supplier_slip":  supplier_slip(entry_log),   # printed by the guard page
            "vehicle":        None,
            "registration":   None,
            "has_violations": False,
            "already_inside": False,
        }

    # Keeps a note of what this scan read, as material for improving the plate
    # model later.
    #
    # NOTE, factually (no code changed): the two values written here are not
    # among the ones MLTrainingSample declares. Its STATUS_CHOICES are
    # unlabeled / auto_labeled / verified / rejected, and its SOURCE_CHOICES are
    # scan / manual / imported — so "auto" and "stream" match neither, and a
    # screen filtering on the declared values will not show these rows. Django
    # only enforces `choices` in form validation, not in .create() or in the
    # database, so the row still saves. The `image` field is also left unset
    # (raw_bytes is accepted but never used), so these rows carry no picture.
    # Compare scanning/ml/collector.py, which writes samples with an image.
    def _record_ml_sample(self, raw_bytes, results):
        from django.db import close_old_connections
        from .models import MLTrainingSample
        close_old_connections()                        # worker thread: start from a fresh connection
        try:
            plates = [r["plate_number"] for r in results if r.get("plate_number")]   # the plates this scan decided
            MLTrainingSample.objects.create(
                plate_number=";".join(plates) if plates else "",   # several plates in one row, separated by ";"
                status="auto",
                source="stream",
            )
        except Exception as exc:
            logger.warning("ML sample failed: %s", exc)   # training material is optional; never fail a scan for it

    # ── auth ──────────────────────────────────────────────────────────────────

    # Turns the token from the connection URL into the signed-in user, or None.
    # Called by connect() before anything else is set up.
    @staticmethod
    async def _get_user_from_token(token_key):
        from django.contrib.auth import get_user_model
        from rest_framework_simplejwt.authentication import JWTAuthentication
        from rest_framework_simplejwt.exceptions import TokenError, InvalidToken

        User = get_user_model()
        try:
            # Validates signature, expiry, and token type using simplejwt + SECRET_KEY
            validated = await sync_to_async(JWTAuthentication().get_validated_token)(token_key)
            user_id = validated["user_id"]             # the account id carried inside the token
            return await sync_to_async(User.objects.get)(pk=user_id)   # database lookup, wrapped for async code
        except (TokenError, InvalidToken, User.DoesNotExist, Exception):
            return None                                # any failure means "not authenticated"; connect() then closes the socket


# ── Shared RTSP stream worker ──────────────────────────────────────────────────
#
# Only ONE cv2.VideoCapture is opened per RTSP URL regardless of how many
# WebSocket consumers (admin, security, etc.) are watching the same camera.
# Each consumer subscribes to an asyncio.Queue; the worker thread broadcasts
# encoded JPEG frames to every live subscriber.

# Cap the picture that goes on the wire, not just the one ffmpeg pipes.
#
# The OpenCV backend hands back the camera's native resolution — 2304x1296 on
# the Imou at Gate 1 — and a JPEG that size is ~1.08 MB of base64 per frame, 20
# times a second. Frames that big never reached the browser at all: the socket
# carried "Stream connected." and then nothing, which on screen is a green
# connected dot over a permanently black canvas. Proven by A/B against the same
# camera's substream, which is the same code path at 108 KB a frame and streams
# 168 frames in 18 s.
#
# `MAX_PIPE_PIXELS` is the budget the raw-pipe backend already uses, so one
# number now governs both backends instead of only the one that happened to
# need it first. Capping pixels rather than width keeps it meaningful for the
# stacked dual-lens frames too, which are taller than they are wide.
#
# Detection runs on this same JPEG, so the boxes it returns are already in the
# coordinates the browser draws in — the frontend sizes everything from the
# received image's naturalWidth.
def _fit_for_wire(frm):
    import cv2
    from vehicles.ffmpeg_capture import MAX_PIPE_PIXELS   # the pixel budget both backends share

    h, w = frm.shape[:2]
    if w * h <= MAX_PIPE_PIXELS:
        return frm                                   # already small enough: send it untouched
    # Shrink by area, not by width: the square root gives the factor to apply to
    # BOTH sides so that width x height lands on the budget.
    scale = (MAX_PIPE_PIXELS / float(w * h)) ** 0.5
    return cv2.resize(frm, (max(1, int(w * scale)), max(1, int(h * scale))),
                      interpolation=cv2.INTER_AREA)   # INTER_AREA is the right filter for shrinking


# =============================================================================
# ONE CAMERA, MANY VIEWERS
#
# An IP camera will only tolerate so many simultaneous connections, and each one
# costs bandwidth. So the server opens a camera ONCE, in a background thread,
# and every browser watching it subscribes to that one worker.
#
#   _StreamWorker    owns the connection to one camera and pushes frames out
#   _STREAM_POOL     url → worker, so the second viewer reuses the first's
#   _acquire_worker  get-or-create + subscribe, as one atomic step
#
# The worker runs in a plain thread while the consumers are asynchronous, which
# is why frames are handed over through per-subscriber queues and
# loop.call_soon_threadsafe: that is the safe way to cross from a thread into an
# event loop. The long comments already in this class record the failures each
# rule was written for; they are worth reading before changing any of it.
# =============================================================================
class _StreamWorker:
    """Manages a single RTSP capture thread shared across multiple consumers."""

    FRAME_INTERVAL = 1.0 / 20   # 20 fps cap for network/CPU budget
    MAX_RETRIES    = 5          # give up after this many failed reconnects in a row

    # Reconnect backoff, doubling from RETRY_DELAY up to RETRY_DELAY_CAP.
    #
    # A flat 2 s retry could not reconnect to the campus Yoosee at all. Cheap
    # firmware reboots when its RTSP server is pushed, and once it does, ICMP
    # comes back immediately while RTSP needs roughly 21 s more to bind. Six
    # attempts 2 s apart therefore spent every one of them inside the boot
    # window, gave up ~11 s before the camera was ready, and reported a URL or
    # network fault that did not exist. Worse, each attempt was several more
    # connections into a device mid-boot, which is what re-triggered the reset:
    # the retry loop was feeding the crash it existed to recover from.
    #
    # Doubling reaches ~60 s over the same five retries, so the last attempts
    # land well after the stream is available again.
    RETRY_DELAY     = 2.0
    RETRY_DELAY_CAP = 30.0

    # How long the newest frame may go without being replaced before the stream
    # counts as stalled rather than merely slow.
    #
    # Comfortably above any real gap: the campus camera's worst measured gap
    # over a clean minute was 135 ms, and even a keyframe hiccup is well under
    # a second. Anything past this is not a slow camera, it is a dead one.
    STALE_FRAME_SECONDS = 8.0

    # Sets up the bookkeeping for one camera. No connection is opened until
    # somebody subscribes.
    def __init__(self, rtsp_url: str):
        self.rtsp_url   = rtsp_url                   # which camera this worker owns
        # The subscriber dict IS the reference count. A separate counter drifted
        # out of step with it — an unsubscribe for an sid that had already been
        # replaced decremented the count for a subscriber that was still there.
        self._subs: dict[str, tuple['asyncio.Queue', 'asyncio.AbstractEventLoop']] = {}   # subscriber id → (queue, its event loop)
        self._lock      = threading.Lock()           # guards _subs and _thread across threads
        self._thread: threading.Thread | None = None   # the capture thread, while one is running
        # One Event per thread generation, never reused: clearing a shared Event
        # let a new subscriber re-arm a thread that an outgoing one was stopping.
        self._stop      = threading.Event()

    # Is there a live capture thread right now? Read into a local first, because
    # another thread may clear self._thread between the two uses.
    def is_running(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    # Adds one viewer and guarantees a thread is feeding it.
    def subscribe(self, sid: str, loop: 'asyncio.AbstractEventLoop') -> 'asyncio.Queue':
        """Register a consumer and guarantee a capture thread is running for it.

        Call through _acquire_worker, which holds the pool lock across
        get-or-create + subscribe.
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=3)   # short queue: a slow viewer drops frames rather than lagging further behind
        with self._lock:
            self._subs[sid] = (q, loop)              # from here the worker will push frames to this queue
            # Start a thread whenever there is not a live one — not merely for
            # the first subscriber. A worker whose thread had already exited
            # (retries exhausted, or a stop that raced with this subscribe) was
            # still sitting in the pool, and everyone who joined it afterwards
            # waited on a queue nothing would ever push to: a black feed, no
            # error, forever.
            if not self.is_running():
                self._stop = threading.Event()       # a fresh stop signal for this thread generation
                self._thread = threading.Thread(
                    target=self._run, args=(self._stop,), daemon=True,   # daemon: never blocks server shutdown
                    name=f'rtsp-worker-{sid[:6]}')   # a recognisable name in thread dumps
                self._thread.start()
        return q                                     # the caller waits on this queue for frames

    # Removes one viewer, and stops the camera when the last one leaves.
    def unsubscribe(self, sid: str):
        # Pool lock first, matching _acquire_worker's order, so a subscribe
        # cannot slip in between "last subscriber left" and the worker leaving
        # the pool. It used to: the newcomer's thread was started, then killed
        # by this stop, and its worker evicted — the feed died on its own a
        # moment after opening.
        with _STREAM_POOL_LOCK:
            with self._lock:
                self._subs.pop(sid, None)            # this viewer is gone
                if self._subs:
                    return                           # others are still watching: leave the camera open
                self._stop.set()                     # nobody left: tell the thread to finish
                self._thread = None
            if _STREAM_POOL.get(self.rtsp_url) is self:   # only evict ourselves, never a newer worker
                del _STREAM_POOL[self.rtsp_url]

    # Hands one message to every subscriber. Called from the capture thread, so
    # each queue is touched on its own event loop rather than directly.
    def _push(self, msg: dict):
        with self._lock:
            items = list(self._subs.values())        # copy under the lock; deliver outside it
        for q, loop in items:
            def _put(q=q, msg=msg):                  # default args bind this iteration's values
                try:
                    q.put_nowait(msg)
                except asyncio.QueueFull:
                    # This viewer is behind. Drop their oldest frame and keep the
                    # newest: for live video, being current beats being complete.
                    try:  q.get_nowait()
                    except Exception: pass
                    try:  q.put_nowait(msg)
                    except Exception: pass
            loop.call_soon_threadsafe(_put)          # the only safe way to touch a queue from another thread

    # Waiting time before reconnect attempt number `retry`: 2s, 4s, 8s… capped.
    @classmethod
    def _backoff(cls, retry: int) -> float:
        """Seconds to wait before reconnect attempt number `retry` (0-based)."""
        return min(cls.RETRY_DELAY * (2 ** retry), cls.RETRY_DELAY_CAP)   # double each time, never past the cap

    # The capture thread. Its life is one outer loop of "connect, stream until
    # something breaks, back off, try again", and inside each successful
    # connection two threads cooperate:
    #
    #   the drain thread  pulls frames off the camera as fast as they arrive and
    #                     keeps only the newest one, so the camera never stalls
    #                     waiting for us
    #   this loop         encodes that newest frame at a fixed 20 per second and
    #                     pushes it to the viewers
    #
    # Splitting them is what keeps a slow encode from backing up the camera, and
    # what makes it possible to tell a live picture from a frozen one.
    def _run(self, stop: threading.Event):
        # `stop` is this generation's Event, passed in rather than read off self:
        # a later subscribe swaps self._stop for a fresh one, and an older thread
        # reading self._stop would then never see its own stop signal.
        import cv2, base64 as _b64, time as _t
        retry = 0                                    # consecutive failed connection attempts
        while not stop.is_set() and retry <= self.MAX_RETRIES:
            self._push({'type': 'status', 'connected': False,
                        'message': f'Connecting… (attempt {retry+1}/{self.MAX_RETRIES+1})'})

            cap = RtspStreamConsumer._open_cap(self.rtsp_url)   # OpenCV first, system FFmpeg as fallback
            if not cap or not cap.isOpened():
                if cap: cap.release()                # always hand the handle back, even a useless one
                delay = self._backoff(retry)
                retry += 1
                # Interruptible: a subscriber leaving should not wait out a
                # 30 s sleep before the thread notices it has been stopped.
                if stop.wait(delay):
                    break
                continue

            retry = 0                                # connected: forget the failure count
            self._push({'type': 'status', 'connected': True, 'message': 'Stream connected.'})
            logger.info('[StreamWorker] Opened %s', self.rtsp_url)

            # Drain thread so grab() never blocks the broadcast loop
            # 'at' is when the newest frame arrived and 'seq' counts them, which
            # is how the loop below tells fresh from frozen and new from repeat.
            latest       = {'data': None, 'ok': False, 'at': _t.monotonic(), 'seq': 0}
            drain_stop   = threading.Event()         # tells the drain thread to finish
            cap_released = threading.Event()         # the drain thread sets this once it has released the camera

            def _drain():
                errs, grabs = 0, 0                   # consecutive failures, and total frames taken
                try:
                    while not drain_stop.is_set():
                        try:
                            if not cap.grab():       # fetch a frame without decoding it yet
                                errs += 1
                                if errs > 20: latest['ok'] = False; break   # 20 in a row: the camera is gone
                                _t.sleep(0.02); continue                    # brief pause, then try again
                            errs = 0; grabs += 1     # a good grab resets the failure run
                            ok, frm = cap.retrieve() # now decode the grabbed frame
                            if ok and frm is not None:
                                # `at` and `seq` are what let the broadcast loop
                                # tell a live picture from a frozen one. Without
                                # them the newest frame and a ten-minute-old
                                # frame are indistinguishable.
                                latest['data'] = frm
                                latest['ok'] = True
                                latest['at'] = _t.monotonic()
                                latest['seq'] += 1
                        except Exception:
                            errs += 1
                            if errs > 20: break
                            _t.sleep(0.02)
                finally:
                    # Whatever happened, give the camera back and say so — the
                    # loop below waits on this before reconnecting.
                    try: cap.release()
                    except Exception: pass
                    cap_released.set()

            dt = threading.Thread(target=_drain, daemon=True)
            dt.start()

            # Wait up to 15 s for the first frame
            for _ in range(300):                     # 300 x 0.05s = 15 seconds
                if latest['ok'] or cap_released.is_set(): break
                _t.sleep(0.05)
            else:                                    # for/else: runs only if the loop was never broken out of
                self._push({'type': 'status', 'connected': False,
                            'message': 'Stream timed out (no frames received).'})
                drain_stop.set()
                cap_released.wait(35)
                # Back off here too. A stream that opened and then stopped
                # producing frames is the *other* face of the firmware reset,
                # and reconnecting instantly walks straight back into a camera
                # that is still on its way down.
                delay = self._backoff(retry)
                retry += 1
                if stop.wait(delay):
                    break
                continue

            # Streaming. Each pass sends at most one frame, then sleeps just
            # long enough to hold the 20-per-second pace.
            try:
                sent_seq = 0                         # the sequence number of the last frame sent
                while not stop.is_set() and not cap_released.is_set():
                    t0 = _t.monotonic()              # start of this pass, for the pacing at the end
                    frm = latest['data']             # the newest frame the drain thread has
                    if not latest['ok'] or frm is None:
                        self._push({'type': 'status', 'connected': False,
                                    'message': 'Stream dropped. Reconnecting…'})
                        break

                    # A camera that goes quiet does not fail loudly: the drain
                    # thread just blocks in grab(), `ok` stays True, and this
                    # loop happily re-encoded the *same* frame 20 times a second
                    # forever. On screen that is a frozen picture over a
                    # "connected" badge, and nothing reconnected for up to 20
                    # failed grabs x a 10 s read timeout — over three minutes.
                    # Frame age is the honest signal, so use it.
                    if _t.monotonic() - latest['at'] > self.STALE_FRAME_SECONDS:
                        self._push({'type': 'status', 'connected': False,
                                    'message': 'Stream stalled. Reconnecting…'})
                        break

                    # Only encode what is actually new. Re-encoding an unchanged
                    # frame burns JPEG cycles per viewer to transmit a picture
                    # they already have.
                    if latest['seq'] != sent_seq:    # a different frame from the one last sent
                        sent_seq = latest['seq']
                        ok, buf = cv2.imencode('.jpg', _fit_for_wire(frm),   # shrink if needed, then compress
                                               [cv2.IMWRITE_JPEG_QUALITY, 85])
                        if ok:
                            self._push({'type': 'frame',
                                        'image_b64': _b64.b64encode(buf.tobytes()).decode()})   # JPEG → text for the socket
                    wait = self.FRAME_INTERVAL - (_t.monotonic() - t0)   # time left in this frame's slot
                    if wait > 0: _t.sleep(wait)      # sleep only the remainder, so slow encodes do not compound
            finally:
                drain_stop.set()                     # stop the drain thread...
                cap_released.wait(35)                # ...and wait for it to release the camera before reconnecting

        # Out of the outer loop: either stopped on purpose, or out of retries.
        if retry > self.MAX_RETRIES:
            self._push({'type': 'error',
                        'message': 'Cannot connect to RTSP stream. If the camera '
                                   'answers ping but not video, it may have reset — '
                                   'some units need up to a minute after a reboot '
                                   'before RTSP accepts connections again. '
                                   'Otherwise check the URL and the network route.'})

        # A worker whose capture thread has given up must not stay in the pool.
        # It used to, whenever a subscriber was still attached, and the next
        # viewer adopted the corpse: subscribe() saw a non-empty _subs, started
        # nothing, and handed back a queue with no producer.
        # `self._stop is stop` keeps an older generation's exit from evicting a
        # newer, live one.
        with _STREAM_POOL_LOCK:
            with self._lock:
                mine = self._stop is stop
            if mine and _STREAM_POOL.get(self.rtsp_url) is self:
                del _STREAM_POOL[self.rtsp_url]
        logger.info('[StreamWorker] Stopped for %s', self.rtsp_url)


_STREAM_POOL: dict[str, _StreamWorker] = {}   # rtsp url → the one worker for that camera
_STREAM_POOL_LOCK = threading.Lock()          # always taken BEFORE a worker's own lock (see unsubscribe)


# The only correct way to join a camera: look up or create the worker and
# subscribe to it without letting go of the pool lock in between.
def _acquire_worker(rtsp_url: str, sid: str, loop: 'asyncio.AbstractEventLoop'):
    """Get-or-create the worker for this URL and subscribe to it in one step.

    Looking the worker up and then subscribing to it had to become atomic:
    between the two, the last remaining subscriber could drop, which stopped the
    capture thread and pulled the worker out of the pool. The newcomer was left
    holding a worker nobody was feeding and nobody would ever restart.
    """
    with _STREAM_POOL_LOCK:
        worker = _STREAM_POOL.get(rtsp_url)          # is someone already watching this camera?
        if worker is None:
            worker = _STREAM_POOL[rtsp_url] = _StreamWorker(rtsp_url)   # first viewer: create the worker
        q = worker.subscribe(sid, loop)              # subscribing also starts the thread if needed
    return worker, q


class RtspStreamConsumer(AsyncJsonWebsocketConsumer):
    """
    WebSocket consumer: reads an RTSP IP-camera stream server-side (OpenCV + FFmpeg),
    pushes JPEG frames + plate-scan results back to the browser.

    Multiple consumers pointing at the same RTSP URL share ONE VideoCapture via
    _StreamWorker — the camera only receives a single connection regardless of how
    many browser tabs/users are watching.

    Client → Server:
        {"type": "start",  "rtsp_url": "rtsp://...", "gate_id": "gate1"}
        {"type": "stop"}

    Server → Client:
        {"type": "connected",  "message": "..."}
        {"type": "status",     "connected": bool, "message": "..."}
        {"type": "frame",      "image_b64": "<base64 JPEG>"}
        {"type": "tracks",     "tracks": [...], "frame_id": int}
        {"type": "ocr_update", "track_id": int,  "plate_text": "..."}
        {"type": "result",     "results": [...]}
        {"type": "error",      "message": "..."}
    """

    # ── lifecycle ──────────────────────────────────────────────────────────────

    # One browser joining a camera feed. Unlike ScanLiveConsumer, this one does
    # not receive frames — it fetches them from the shared worker and forwards
    # them, and only runs detection when the viewer asked for it.
    async def connect(self):
        qs = self.scope["query_string"].decode()
        token_key = (                                # same URL-token scheme as the scanning consumer
            qs.split("token=")[-1].split("&")[0]
            if "token=" in qs
            else None
        )
        if not token_key:
            await self.close(code=4001, reason="Authentication required")
            return
        self._user = await ScanLiveConsumer._get_user_from_token(token_key)   # reuse the same lookup
        if self._user is None:
            await self.close(code=4001, reason="Invalid token")
            return

        # detect=1 in the query string enables plate-scan ML.
        # Omit or set detect=0 for view-only connections (Device Management,
        # Operations Center) so they never run detection or OCR.
        self._scan_enabled = "detect=1" in qs        # view-only tabs cost no detection or OCR at all

        # Shared scan state (mirrors ScanLiveConsumer.__init__ block)
        # The same fields, because the scanning helpers borrowed at the bottom
        # of this class expect to find them.
        self._tracker               = ProximityTracker()
        self._frame_counter         = 0
        self._pending_ocr: dict     = {}
        self._ocr_state: dict       = {}
        self._announced: dict       = {}  # plate → decided_at last sent to this client
        self._detection_in_progress = False
        self._last_img_w            = 1280           # a sensible guess until the first frame is measured
        self._last_img_h            = 720
        self._stream_task           = None           # the task pumping frames from the worker
        self._detect_tasks: set     = set()   # tracked so we can cancel on disconnect
        try:
            from vehicles.models import SystemSettings
            cfg = await sync_to_async(SystemSettings.get)()
            self._dedup_seconds = cfg.scan_dedup_seconds
        except Exception:
            self._dedup_seconds = _DEFAULT_DEDUP_SECONDS
        # Default to the guard's own gate; a camera-configured gate (sent in the
        # 'start' message) overrides this below.
        self._gate_id               = _resolve_gate('', self._user)
        self._loop                  = asyncio.get_running_loop()
        # uuid, not id(self): CPython reuses object addresses, so two consumers
        # could hold the same subscriber key and unsubscribe each other.
        self._worker_sid            = uuid.uuid4().hex

        await self.accept()
        logger.info("[RTSP] Connected: user=%s scan=%s", self._user, self._scan_enabled)

        # Register ML status listener — forwards loading stage events to this WS client
        from .ml.detection import add_ml_status_listener
        _loop = self._loop
        async def _send_ml_status(stage, message):
            try:
                await self.send_json({"type": "ml_status", "stage": stage, "message": message})
            except Exception:
                pass
        def _ml_status_listener(stage, message):
            asyncio.run_coroutine_threadsafe(_send_ml_status(stage, message), _loop)
        self._ml_status_listener = _ml_status_listener
        add_ml_status_listener(_ml_status_listener)  # immediately delivers current status
        await self.send_json({"type": "connected", "message": "RTSP consumer ready."})

    # Leaving: drop the ML listener and stop the stream, which also
    # unsubscribes from the shared worker (see _consume_stream's finally).
    async def disconnect(self, code):
        logger.info("[RTSP] Disconnect code=%s", code)
        from .ml.detection import remove_ml_status_listener
        if hasattr(self, '_ml_status_listener'):
            remove_ml_status_listener(self._ml_status_listener)
        await self._cancel_stream()

    # ── receive ────────────────────────────────────────────────────────────────

    # This consumer takes commands rather than frames: "start" (watch this
    # camera) and "stop".
    async def receive_json(self, content: dict):
        msg_type = content.get("type", "")

        if msg_type == "start":
            rtsp_url = content.get("rtsp_url", "").strip()
            if not rtsp_url or not rtsp_url.lower().startswith("rtsp://"):   # refuse anything that is not a camera URL
                await self.send_json({"type": "error", "message": "Invalid or missing RTSP URL."})
                return
            if content.get("gate_id"):
                self._gate_id = _resolve_gate(content["gate_id"], self._user)   # a camera's own gate overrides the guard's
            await self._cancel_stream()              # switching cameras: stop the previous one first
            # Reset tracker state for fresh stream
            # Track ids and plate votes describe the old camera's scene; carrying
            # them over would attach one camera's readings to another's cars.
            self._tracker       = ProximityTracker()
            self._ocr_state     = {}
            self._pending_ocr   = {}
            self._announced     = {}
            self._frame_counter = 0
            self._stream_task   = asyncio.create_task(self._consume_stream(rtsp_url))   # runs until cancelled

        elif msg_type == "stop":
            await self._cancel_stream()
            await self.send_json({"type": "status", "connected": False, "message": "Stream stopped."})

    # ── stream management ──────────────────────────────────────────────────────

    # Stops everything this consumer started: the detection tasks first, then
    # the frame pump.
    async def _cancel_stream(self):
        # Cancel all in-flight detection/OCR tasks first so they don't keep
        # logging after the stream stops (executor threads finish on their own
        # but the async wrappers — and their log calls — are stopped here).
        for t in list(getattr(self, '_detect_tasks', ())):   # list(): cancelling removes entries from the set
            t.cancel()
        self._detect_tasks = set()

        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()
            try:
                await self._stream_task              # wait for it to actually finish unwinding
            except asyncio.CancelledError:
                pass                                 # expected: we are the ones who cancelled it
        self._stream_task = None

    async def _consume_stream(self, rtsp_url: str):
        """Subscribe to the shared _StreamWorker for this URL and process frames."""
        import base64 as _b64
        worker, q = _acquire_worker(rtsp_url, self._worker_sid, self._loop)   # join the camera; q is our private queue
        # Track whether we've told the frontend the stream is connected.
        # A late-joining subscriber won't receive the worker's initial status
        # broadcast, so we synthesise it on the first frame we see.
        sent_connected = False
        try:
            # Forward whatever the worker publishes until this task is cancelled.
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=45.0)   # wake up if nothing arrives for 45s
                except asyncio.TimeoutError:
                    # Nothing at all for 45 s. If the capture thread is gone,
                    # waiting longer cannot help — report it instead of holding
                    # a black canvas open in silence, which is how a dead worker
                    # used to present itself.
                    if not worker.is_running():          # the capture thread has given up
                        await self.send_json({
                            "type": "error",
                            "message": "The camera stream stopped and could not be restarted.",
                        })
                        await self.close()               # close the socket so the browser reconnects
                        return
                    logger.warning("[RTSP] No frames for 45 s — still waiting")   # thread alive: keep waiting
                    continue

                msg_type = msg.get("type")               # the worker sends 'frame', 'status' or 'error'

                if msg_type == "frame":
                    if not sent_connected:
                        # Subscriber joined after the worker was already streaming;
                        # synthesise the connected status so the UI badge updates.
                        await self.send_json({
                            "type": "status", "connected": True,
                            "message": "Stream connected.",
                        })
                        sent_connected = True
                    await self.send_json({"type": "frame", "image_b64": msg["image_b64"]})   # the picture, unchanged
                    # Scan only if this viewer asked for it and the previous
                    # detection has finished — otherwise just show the video.
                    if self._scan_enabled and not self._detection_in_progress:
                        self._detection_in_progress = True
                        jpeg_bytes = _b64.b64decode(msg["image_b64"])   # back to raw bytes for the detector
                        task = asyncio.create_task(self._detect_and_scan(jpeg_bytes))
                        self._detect_tasks.add(task)                   # remembered so disconnect can cancel it
                        task.add_done_callback(self._detect_tasks.discard)   # and forgotten once it finishes

                elif msg_type == "status":
                    await self.send_json(msg)                          # pass connection news straight through
                    if msg.get("connected"):
                        sent_connected = True                          # no need to synthesise it later

                elif msg_type == "error":
                    await self.send_json(msg)
                    # Close the WebSocket so the frontend knows to reconnect;
                    # simply returning would leave the WS open with no stream.
                    await self.close()
                    return

        except asyncio.CancelledError:
            raise                                    # cancellation is normal here; let it propagate
        finally:
            worker.unsubscribe(self._worker_sid)     # always leave the camera, however this ended

    # ── detection + scan pipeline ──────────────────────────────────────────────

    # The same pipeline as ScanLiveConsumer.receive_json, for one frame that
    # came from an IP camera rather than the browser: detect, track, queue OCR,
    # and let the shared presence code decide whether anything is announced.
    async def _detect_and_scan(self, jpeg_bytes: bytes):
        loop = asyncio.get_running_loop()
        try:
            detections = await loop.run_in_executor(None, self._run_detection, jpeg_bytes)   # heavy work off the event loop
        except Exception as exc:
            logger.error("[RTSP] Detection error: %s", exc)
            detections = []                          # a failed frame is treated as an empty one
        finally:
            self._detection_in_progress = False      # release the guard so the next frame may be scanned

        now            = timezone.now()
        tracker_output = self._tracker.update(detections, img_w=self._last_img_w)   # boxes → stable track ids
        det_by_idx     = {i: d for i, d in enumerate(detections)}   # so a track can find its detection again

        # Evict OCR state for tracks the tracker has expired — prevents unbounded growth
        active_ids = set(self._tracker.tracks.keys())
        for stale_id in list(self._ocr_state.keys()):
            if stale_id not in active_ids:
                self._ocr_state.pop(stale_id, None)
                self._pending_ocr.pop(stale_id, None)

        active_tracks      = []                      # what the browser draws
        tracks_needing_ocr = []                      # plates not read yet
        tracks_to_reverify = []                      # locked plates due a quiet re-check
        now_ts             = time.time()

        # Same sorting as the scanning consumer: read new plates, re-check old ones.
        for t_out in tracker_output:
            track_id      = t_out["track_id"]
            bbox          = t_out["bbox"]
            x, y, bw, bh  = bbox["x"], bbox["y"], bbox["width"], bbox["height"]

            class_name   = t_out.get("class_name", "")
            vehicle_type = t_out.get("vehicle_type")
            plate_text   = t_out.get("plate_text", "")
            ocr_done     = t_out.get("ocr_done", False)

            d_idx = t_out.get("detection_index")
            det   = det_by_idx.get(d_idx) if d_idx is not None else None

            if (det and det.get("class_name") == "license_plate"
                    and det.get("crop") is not None):
                if not ocr_done:
                    tracks_needing_ocr.append(
                        (track_id, det["crop"], det.get("aspect_ratio", 1.0))
                    )
                else:
                    st = self._ocr_state.get(track_id)
                    if (st and st.get("locked")
                            and now_ts - st.get("verify_at", 0.0) >= _OCR_REVERIFY_SECONDS):
                        st["verify_at"] = now_ts  # claim before queueing
                        tracks_to_reverify.append(
                            (track_id, det["crop"], det.get("aspect_ratio", 1.0))
                        )

            w_img = self._last_img_w
            h_img = self._last_img_h
            active_tracks.append({
                "track_id":       track_id,
                "plate_text":     plate_text,
                "vehicle_type":   vehicle_type,
                "class_name":     class_name,
                "bbox":           [
                    x  / max(w_img, 1), y  / max(h_img, 1),
                    (x + bw) / max(w_img, 1), (y + bh) / max(h_img, 1),
                ],
                "detection_conf": det.get("confidence", 0.0) if det else 0.0,
            })

        await self.send_json({                       # overlay first, so boxes keep pace with the video
            "type":     "tracks",
            "tracks":   active_tracks,
            "frame_id": self._frame_counter,
        })

        if tracks_needing_ocr:
            asyncio.create_task(self._run_ocr_for_tracks(tracks_needing_ocr))      # borrowed from ScanLiveConsumer
        if tracks_to_reverify:
            asyncio.create_task(self._reverify_locked_tracks(tracks_to_reverify))

        if any(t.get("plate_text") for t in active_tracks):
            await self._process_scan_results(active_tracks, now)   # keeps known plates "present", re-deciding when held long enough

    # ── sync helpers ───────────────────────────────────────────────────────────

    # Opens the camera. A static method because _StreamWorker calls it without
    # having a consumer instance — and because the tests replace it wholesale.
    @staticmethod
    def _open_cap(rtsp_url: str):
        """Open the stream with whichever backend can decode this camera.

        This used to try OpenCV over TCP then UDP and hand back whatever the
        last attempt produced. That covers most cameras and no more: OpenCV
        bundles a frozen FFmpeg 4.4, and a camera it cannot decode has no
        second chance. `open_capture` keeps OpenCV as the fast path and falls
        back to the system FFmpeg, which is where support for anything newer
        than 2021 lives. See vehicles/ffmpeg_capture.py.

        The env-var race that _OPEN_CAP_LOCK guarded is now handled inside
        that module, by a lock shared with the parking worker. The two modules
        previously held *separate* locks over the same process-wide
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"], so a scan camera and a
        parking camera opening at the same instant were never serialised
        against each other at all.
        """
        from vehicles.ffmpeg_capture import open_capture

        return open_capture(rtsp_url)

    # Compresses one frame to JPEG bytes.
    #
    # NOTE, factually (no code changed): nothing calls this. The worker encodes
    # inline in _run() instead, with quality 85 and after _fit_for_wire has
    # capped the size, whereas this uses quality 70 and no size cap. Searched
    # across the backend: the only occurrence of the name is this definition.
    @staticmethod
    def _encode_frame(frame) -> "bytes | None":
        import cv2
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        return buf.tobytes() if ok else None         # None signals "could not encode"

    # Detection for an IP-camera frame. Differs from the scanning consumer's
    # version in one way: it splits a multi-lens frame into its separate views
    # first (see the note below).
    def _run_detection(self, jpeg_bytes: bytes) -> list:
        import cv2
        import numpy as np
        nparr = np.frombuffer(jpeg_bytes, np.uint8)
        img   = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return []                                # undecodable frame: nothing detected

        self._frame_counter += 1                     # labels the overlay message for this frame
        h, w = img.shape[:2]
        self._last_img_w = w                         # remembered so boxes can be sent as fractions
        self._last_img_h = h

        plate_tracks = [t for t in self._tracker.tracks.values()
                        if t.class_name == "license_plate"]
        all_plates_locked = bool(plate_tracks) and all(t.ocr_done for t in plate_tracks)
        # Per lens on a multi-lens camera — a plate occupies a few dozen pixels
        # and halving its scene's height to stack a second view under it is
        # exactly the kind of framing OCR loses. Detections come back in
        # full-frame coordinates, so the tracker and the browser overlay below
        # are unaffected. Only this RTSP path splits; ScanLiveConsumer's frames
        # come from a phone or webcam, where "taller than wide" means someone
        # is holding the thing upright, not that there are two pictures in it.
        detections = detect_across_lenses(img, detect_plates,          # run the detector once per lens view
                                          try_rotation=not all_plates_locked)

        out = []
        for i, det in enumerate(detections):
            bb = det["bbox"]
            out.append({
                # Fractions of the frame back into pixels, as the tracker expects.
                "bbox": {
                    "x":      int(bb["x"]      * w),
                    "y":      int(bb["y"]      * h),
                    "width":  int(bb["width"]  * w),
                    "height": int(bb["height"] * h),
                },
                "crop":            det.get("crop"),
                "confidence":      det["score"],
                "aspect_ratio":    det.get("aspect_ratio", 1.0),
                "class_name":      det.get("class_name", ""),
                "vehicle_type":    det.get("vehicle_type"),
                "detection_index": i,
            })
        return out

    # Reuse async/sync helpers from ScanLiveConsumer (method assignment works in Python 3
    # because unbound functions become properly-bound methods when accessed on an instance)
    #
    # In other words: the OCR voting, the presence registry and the whole
    # entry/exit state machine are shared outright, not copied. Whichever
    # consumer a frame arrives through, a plate is decided by the same code —
    # which is why this class sets up the same attribute names in connect().
    # Anything changed in those methods changes both paths at once.
    _run_ocr_for_tracks    = ScanLiveConsumer._run_ocr_for_tracks
    _reverify_locked_tracks = ScanLiveConsumer._reverify_locked_tracks
    _finalize_plate        = ScanLiveConsumer._finalize_plate
    _handle_plate_sighting = ScanLiveConsumer._handle_plate_sighting
    _process_scan_results  = ScanLiveConsumer._process_scan_results
    _result_hold_seconds   = ScanLiveConsumer._result_hold_seconds
    _evict_presence        = ScanLiveConsumer._evict_presence
    _save_snapshot         = ScanLiveConsumer._save_snapshot
    _save_to_db            = ScanLiveConsumer._save_to_db
    _check_vehicle         = ScanLiveConsumer._check_vehicle
    _check_supplier        = ScanLiveConsumer._check_supplier
    _record_ml_sample      = ScanLiveConsumer._record_ml_sample
