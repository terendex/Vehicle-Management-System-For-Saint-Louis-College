// =============================================================================
// EVERY CAMERA FEED IN THE APP — one provider, one socket per camera.
//
// A page does not open a camera. It registers a <canvas> and this provider
// paints into it, which is the whole architecture in one sentence. That
// indirection is deliberate: feeds have to survive a page navigating away and
// back, and reconnecting an RTSP camera is slow (and on the campus unit,
// actively harmful).
//
// Three loops run per camera, and keeping them separate is what makes it work:
//
//   1. the SOCKET     receives base64 JPEGs and detection boxes, pushes them
//                     into refs. Never touches the DOM.
//   2. the DECODE     turns the newest JPEG into an Image. At most ONE in
//                     flight per camera; a frame arriving mid-decode replaces
//                     the queued one rather than joining a backlog.
//   3. the rAF DRAW   paints whatever the refs currently hold, at screen rate,
//                     independently of how fast frames arrive.
//
// Almost all the state lives in refs rather than React state, because a
// 30-fps feed re-rendering the tree 30 times a second would be unusable. The
// two exceptions are marked where they are declared.
//
// The comments already in this file record the bugs that shaped it — a frozen
// tab, a hammered camera, a cropped dual-lens view. They are worth reading as
// history, not decoration.
// =============================================================================
import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { toast } from '../components/Feedback/notify'
import { WS_BASE } from '../api/wsBase'

// A flapping camera or scanner re-raises the same message on every retry.
// These dialogs stay dismissed for this long afterwards so a dead socket
// cannot bury the screen a guard is working on.
const STREAM_THROTTLE = 30000

const TRACK_COLORS = {
  license_plate: '#14A374',
  vehicle:       '#14A374',
  motorcycle:    '#2E8CCB',
  _default:      '#F6CE11',
}
const VEHICLE_TYPE_LABELS = { motorcycle: 'Motorcycle' }
// Smoothing factor for box movement: each frame the drawn box moves a quarter
// of the way to where detection says it is. Low enough to hide jitter between
// detections, high enough not to lag visibly behind a moving vehicle.
const LERP = 0.25

// Canvas key for "draw the entire frame", as opposed to a numbered slice of it.
const FULL_FRAME = 'full'

// How many pictures are packed into one frame. A dual-lens camera stacks two
// views vertically rather than opening a second stream.
//
// This must stay identical to lens_count() in backend/vehicles/lens_layout.py:
// the backend splits detection the same way and returns boxes in full-frame
// coordinates, so if the two disagree the overlays stop matching the picture.
//
//   1920x2160 -> two 1920x1080 views       (1.78)  split
//    864x976  -> two  864x488  views       (1.77)  split
//    960x1280 -> ambiguous, so left whole  (1.50)  leave alone
//   1080x1920 -> a portrait-mounted camera (1.13)  leave alone
//
// Deliberately biased against splitting: not splitting a stacked camera looks
// exactly like the old behaviour, while splitting an upright one silently
// hides half its picture.
const MIN_HALF_ASPECT = 1.6

function lensCount(w, h) {
  // A landscape or square frame is never a vertical stack, so it is answered
  // without arithmetic — and the guard also covers a frame whose dimensions
  // are not known yet (0), where dividing would give a nonsense ratio.
  if (!w || !h || h <= w) return 1
  // The test is on ONE HALF's aspect: if slicing the frame in two would give
  // two ordinary 16:9-ish pictures, it is a stack.
  return w / (h / 2) >= MIN_HALF_ASPECT ? 2 : 1
}

// Auto-reconnect backoff after an unexpected disconnect. A flat 3 s retry
// hammered a camera that was simply down — every attempt costs the backend an
// RTSP open (up to 10 s each) and popped another toast, so an unreachable
// camera degraded the ones that were working.
const RECONNECT_BASE_MS = 2000
const RECONNECT_MAX_MS  = 30000

function trackColor(t) {
  return TRACK_COLORS[t.vehicle_type] ?? TRACK_COLORS[t.class_name] ?? TRACK_COLORS._default
}

// Module-scope counter for camera ids. Pre-increment, so ids start at 1 and 0
// is never handed out — which matters because these are used in truthiness
// tests around the maps below.
let _seq = 0
const genId = () => ++_seq

const CameraContext = createContext(null)

export function CameraProvider({ children }) {
  // The three pieces of genuine React state. Everything else is a ref, for
  // the reason in the file header.
  const [cameras, setCameras] = useState([])    // the roster pages render
  const [results, setResults] = useState([])    // scan outcomes, which the UI lists
  const [flash,   setFlash]   = useState(false) // the brief visual confirmation of a scan

  // camId → how many views are packed into one frame (1 for an ordinary
  // camera, 2 for a dual-lens unit). State, not a ref, because pages render a
  // viewport per view and have to re-render when the answer changes.
  const [paneCounts, setPaneCounts] = useState({})

  // Mutable refs — never cause re-renders, survive page navigation
  // Sixteen ref maps, all keyed by camId. They are separate maps rather than
  // one object-per-camera because each is written by a different loop — the
  // socket writes frames, the draw loop writes smoothing, the reconnect logic
  // writes timers — and splitting them keeps those writes from colliding.
  const wsMap     = useRef({})
  const canvasMap = useRef({})   // id → { paneKey → <canvas> }; a camera may have several
  const frameMap  = useRef({})   // id → the newest *decoded* frame, safe to draw
  const decodeMap = useRef({})   // id → bool: a decode is in flight for this camera
  const queuedMap = useRef({})   // id → newest base64 arrived while decoding
  const trackMap  = useRef({})
  const smoothMap = useRef({})
  const rafMap    = useRef({})
  const loopErrMap = useRef({}) // id → already logged a render-loop error
  const paneCountMap = useRef({}) // id → pane count, mirrors paneCounts inside the RAF loop
  const detectMap = useRef({}) // id → bool: whether this connection runs ML detection
  const gateMap   = useRef({}) // id → gate_id the camera covers (used to tag scan logs)
  const retryMap  = useRef({}) // id → consecutive failed attempts (drives backoff)
  const timerMap  = useRef({}) // id → pending reconnect timeout

  // url → camId: tracks all known cameras by URL, used for dedup + reconnection
  // Using a Map instead of a Set so we can look up existing camId by URL
  const urlToIdMap = useRef({})

  // Mirrors `cameras` so syncCameras can read the current roster without being
  // rebuilt every time a frame flips a connection flag.
  const camerasRef = useRef([])

  // Ref that always holds the latest _connect function (needed for self-referential reconnect)
  // LOAD-BEARING, and ESLint flags it ("Cannot access refs during render").
  // scheduleReconnect is defined above _connect and has to call it; wiring
  // them directly would make two useCallbacks depend on each other. Do NOT
  // "fix" the lint error by removing this indirection — doing so reintroduces
  // the cycle and breaks reconnection.
  const connectRef = useRef(null)

  // ── Frame intake ─────────────────────────────────────────────────────────
  // Decode one frame at a time per camera, and only publish it once it has
  // actually decoded.
  //
  // Two bugs lived in the obvious version (`frameMap[id] = new Image()` right
  // after setting .src). The render loop only paints an image once
  // `complete && naturalWidth > 0`, so assigning an *undecoded* image makes the
  // newest frame unpaintable — and if frames keep arriving faster than they
  // decode, the slot is perpetually undecoded and the canvas sticks on the last
  // good picture forever. Background tabs are exactly that case: browsers
  // deprioritise image decoding for hidden documents while the socket keeps
  // delivering, so switching away and back froze the feed.
  //
  // Holding at most one decode in flight also stops a hidden tab from queueing
  // thousands of Image decodes it will never show. A frame that arrives mid
  // decode replaces the queued one rather than joining a backlog: for a live
  // feed only the newest picture is worth anything.
  // THE DECODE LOOP. Called by the socket for every frame that arrives.
  const acceptFrame = useCallback((camId, b64) => {
    // `start` recurses instead of acceptFrame calling itself: a useCallback
    // const cannot reference its own binding from inside its initialiser.
    const start = (payload) => {
      decodeMap.current[camId] = true

      const img = new Image()
      const done = () => {
        decodeMap.current[camId] = false
        const next = queuedMap.current[camId]
        if (next) {
          queuedMap.current[camId] = null
          start(next)
        }
      }
      img.onload = () => {
        // Publish only now: a stale but drawable frame beats a fresh blank one.
        frameMap.current[camId] = img
        done()
      }
      // A corrupt frame is dropped silently and the queue keeps moving. Without
      // this the decode flag would stay true forever and the feed would stop.
      img.onerror = done
      img.src = `data:image/jpeg;base64,${payload}`   // assigning src is what starts the decode
    }

    if (decodeMap.current[camId]) {
      queuedMap.current[camId] = b64        // newest wins; older one is dropped
      return
    }
    start(b64)                              // nothing decoding: begin immediately
  }, [])                                    // empty deps: the function closes over refs only, so it never needs rebuilding

  // ── Canvas registration (pages call this on mount/unmount) ───────────────
  // A camera may need more than one canvas. A dual-lens unit sends both of its
  // views stacked inside a single frame — one RTSP stream, one JPEG, two
  // pictures — so the split is a matter of which slice each canvas draws, not
  // of opening a second connection.
  //
  // Omitting `pane` means "the whole frame", and that is what every caller
  // that has not opted in gets. Defaulting it to 0 instead would have quietly
  // cropped every other page in the app to the top half of a dual-lens feed,
  // hiding the second view on screens that only ever register one canvas.
  const registerCanvas = useCallback((camId, el, pane) => {
    // `pane == null` catches both undefined and null — the two ways a caller
    // can decline to pick a slice. String() because object keys are strings
    // anyway, and being explicit stops 0 and '0' being different entries.
    const key = pane == null ? FULL_FRAME : String(pane)
    const panes = canvasMap.current[camId] ?? (canvasMap.current[camId] = {})   // create-on-first-use, in one expression
    if (el) {
      panes[key] = el                       // React passes the node on mount...
    } else {
      delete panes[key]                     // ...and null on unmount
      // Prune the empty parent too, so the draw loop's "any canvas?" test is a
      // simple key check rather than a walk.
      if (Object.keys(panes).length === 0) delete canvasMap.current[camId]
    }
  }, [])

  // ── 60-fps render loop (keeps running even when canvas is unregistered) ──
  // THE DRAW LOOP. One per camera, started on connect and never stopped while
  // the camera exists — it keeps running with no canvas registered, drawing
  // nothing, so navigating back to a page resumes instantly.
  const startRenderLoop = useCallback((camId) => {
    if (rafMap.current[camId]) return        // already looping; a second loop would double the paint rate
    if (!trackMap.current[camId]) trackMap.current[camId] = new Map()
    if (!smoothMap.current[camId]) smoothMap.current[camId] = new Map()

    // Every path out of this function must reschedule, so the body is wrapped
    // and the next frame is queued in `finally`.
    //
    // Before, the only `requestAnimationFrame(draw)` for the normal path was
    // the last statement, so *any* throw above it killed the chain for good —
    // and `startRenderLoop` refuses to restart a loop whose rafMap entry is
    // still set, so nothing ever revived it. A page reload was the only cure.
    // That is precisely the reported failure: switch browser tabs, come back,
    // frozen forever. Chrome discards a backgrounded tab's canvas backing
    // store under memory pressure, `getContext('2d')` then returns null, and
    // the first `ctx.clearRect` throws.
    const draw = () => {
      try {
      const panes = canvasMap.current[camId]
      // No canvas registered (page navigated away) — keep looping, draw when back
      if (!panes || Object.keys(panes).length === 0) {
        return
      }

      const img   = frameMap.current[camId]
      // All three checks: the Image may be absent, still decoding, or decoded
      // but empty. naturalWidth is the one that catches a failed decode that
      // nonetheless set `complete`.
      const ready = Boolean(img && img.complete && img.naturalWidth > 0)

      // How many pictures are inside this one frame — see lensCount above.
      const count = ready ? lensCount(img.naturalWidth, img.naturalHeight) : 1
      if (ready && paneCountMap.current[camId] !== count) {
        paneCountMap.current[camId] = count
        setPaneCounts(prev => ({ ...prev, [camId]: count }))
      }

      // Fallbacks so a camera that has not delivered a frame yet still gets a
      // sensibly-sized black canvas rather than a 0x0 one.
      const fw = img?.naturalWidth  || 1280           // full frame, both views
      const fh = img?.naturalHeight || 720
      // Floored: a canvas height attribute is an integer, and letting the
      // browser truncate it instead would drift the second view's offset.
      const sh = Math.floor(fh / count)                // one view's height

      const targets = trackMap.current[camId]  || new Map()
      const smooth  = smoothMap.current[camId] || new Map()

      // Drop smoothing state for tracks detection has stopped reporting.
      // Without this the map grows for the life of the page — every vehicle
      // that has ever passed the camera would keep an entry.
      for (const tid of smooth.keys()) if (!targets.has(tid)) smooth.delete(tid)

      // Smoothing is advanced once per frame, in full-frame pixels, before any
      // pane is drawn. Doing it inside the pane loop would step the same track
      // twice per frame on a split camera and make the boxes race ahead.
      for (const [tid, track] of targets) {
        const tx1 = track.bbox[0] * fw, ty1 = track.bbox[1] * fh
        const tx2 = track.bbox[2] * fw, ty2 = track.bbox[3] * fh
        // A brand-new track starts AT its target rather than at zero —
        // otherwise every box would fly in from the top-left corner.
        if (!smooth.has(tid)) smooth.set(tid, { x1: tx1, y1: ty1, x2: tx2, y2: ty2 })
        const s = smooth.get(tid)
        s.x1 += (tx1 - s.x1) * LERP; s.y1 += (ty1 - s.y1) * LERP
        s.x2 += (tx2 - s.x2) * LERP; s.y2 += (ty2 - s.y2) * LERP
      }

      for (const [key, canvas] of Object.entries(panes)) {
        // A viewport that asked for the whole frame gets it, stacked views and
        // all — only the ones that named a pane are cropped to it.
        const whole = key === FULL_FRAME
        // Clamped: a page may register pane 1 on a camera that turns out to
        // be single-lens, and without this it would slice past the bottom of
        // the frame and draw nothing.
        const pane  = whole ? 0 : Math.min(Number(key), count - 1)
        const ch    = whole ? fh : sh                  // this canvas's height
        const sy    = whole ? 0  : pane * sh           // top of this view

        // Guarded because ASSIGNING canvas.width clears the canvas, even to
        // the same value — doing it unconditionally would blank the picture
        // every frame and produce a flicker.
        if (canvas.width !== fw) canvas.width  = fw
        if (canvas.height !== ch) canvas.height = ch

        // null when the browser has thrown the backing store away, which it
        // does to hidden tabs. Skip this pane rather than throwing; the next
        // frame gets a fresh context once the tab is visible again.
        const ctx = canvas.getContext('2d')
        if (!ctx) continue
        ctx.clearRect(0, 0, fw, ch)

        if (ready) {
          // The nine-argument form: take the slice starting at sy from the
          // source, draw it filling the destination. This is where a stacked
          // dual-lens frame becomes two separate pictures.
          ctx.drawImage(img, 0, sy, fw, ch, 0, 0, fw, ch)
        } else {
          ctx.fillStyle = '#04121F'            // the dark placeholder, so a connecting feed reads as "not yet" rather than broken
          ctx.fillRect(0, 0, fw, ch)
        }

        if (targets.size === 0) continue       // nothing detected: skip the overlay setup entirely

        ctx.font = "12px 'Courier New', monospace"
        ctx.textBaseline = 'top'
        ctx.textAlign    = 'left'

        for (const [tid, track] of targets) {
          const s = smooth.get(tid)
          if (!s) continue

          // Track coordinates are relative to the whole frame; shift them into
          // this view and skip anything belonging to the other one.
          const py = s.y1 - sy, ph = s.y2 - s.y1
          // Entirely above or entirely below this pane — it belongs to the
          // other view. Both ends are tested so a box straddling the seam is
          // still drawn (clipped) on both.
          if (py + ph <= 0 || py >= ch) continue

          const px = s.x1, pw = s.x2 - s.x1
          const color = trackColor(track)

          ctx.strokeStyle = color
          // Vehicles get a thicker dashed box, plates a thin solid one — so
          // the two kinds of detection are distinguishable at a glance even
          // where they overlap.
          ctx.lineWidth   = track.vehicle_type ? 3 : 2
          ctx.setLineDash(track.vehicle_type ? [8, 4] : [])
          ctx.strokeRect(px, py, pw, ph)
          ctx.setLineDash([])                  // reset immediately: the dash pattern is context-wide and would leak to the next box

          const PAD = 6, TH = 21
          const labelText = track.vehicle_type
            ? (VEHICLE_TYPE_LABELS[track.vehicle_type] ?? track.vehicle_type)
            : `${track.plate_text || `T#${tid}`}${track.detection_conf ? ` ${(track.detection_conf * 100).toFixed(0)}%` : ''}`

          // Measured, not guessed: the label box has to fit its text, and the
          // text is a plate number of unpredictable length.
          const tw = ctx.measureText(labelText).width + PAD * 2
          // Three fills in order — dark plate, coloured spine, then the text.
          // Drawn ABOVE the box (py - TH), so a label never covers the vehicle
          // it belongs to.
          ctx.fillStyle = 'rgba(0,0,0,0.75)'; ctx.fillRect(px, py - TH, tw, TH)
          ctx.fillStyle = color;              ctx.fillRect(px, py - TH, 3, TH)
          ctx.fillStyle = '#fff';             ctx.fillText(labelText, px + PAD + 2, py - TH + 4)
        }
      }

      } catch (err) {
        // One bad frame must not end the feed. Logged once per camera so a
        // recurring fault is visible without flooding the console at 60 fps.
        // Note, factually: nothing ever clears loopErrMap — not
        // stopRenderLoop, not disconnectAll. Combined with disconnectCamera
        // KEEPING the urlToIdMap entry (so a reconnected camera reuses its
        // camId), this means "once per camera" is really "once per camera per
        // page load": after one error, a later and possibly different fault on
        // the same camera is never logged. Recorded, not changed.
        if (!loopErrMap.current[camId]) {
          loopErrMap.current[camId] = true
          console.error('[camera] render loop error (recovering)', err)
        }
      } finally {
        // THE line that keeps the feed alive. In `finally`, so every exit from
        // the try — a normal paint, an early `return` when no canvas is
        // registered, or a throw — still queues the next frame. This is the
        // fix for the freeze described at the top of the loop.
        rafMap.current[camId] = requestAnimationFrame(draw)
      }
    }
    rafMap.current[camId] = requestAnimationFrame(draw)   // the first tick; every later one comes from the finally above
  }, [])

  // The per-camera teardown. Note what it does NOT clear: detectMap, gateMap,
  // retryMap and urlToIdMap all survive, because a stopped camera is expected
  // to come back and should return with the same identity and settings.
  const stopRenderLoop = useCallback((camId) => {
    if (rafMap.current[camId]) {
      cancelAnimationFrame(rafMap.current[camId])
      delete rafMap.current[camId]
    }
    for (const canvas of Object.values(canvasMap.current[camId] ?? {})) {
      canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height)
    }
    delete frameMap.current[camId]
    // An in-flight decode's onload would otherwise repopulate frameMap for a
    // camera that has just been stopped, painting a ghost frame onto the next
    // feed to reuse the canvas.
    delete decodeMap.current[camId]
    delete queuedMap.current[camId]          // the pending base64 too, or a stopped camera keeps a whole JPEG alive
    delete trackMap.current[camId]
    delete smoothMap.current[camId]
    delete paneCountMap.current[camId]
    setPaneCounts(prev => {
      if (!(camId in prev)) return prev
      const next = { ...prev }
      delete next[camId]
      return next
    })
  }, [])

  // ── Reconnect with backoff ────────────────────────────────────────────────
  // One place decides when to retry, so the 'error' path and the 'close' path
  // can no longer schedule two overlapping reconnects for the same camera —
  // which opened two sockets and left one of them orphaned.
  // Backoff, and the counter that drives it. Reset to 0 whenever frames start
  // flowing again (see the status handler), so a camera that recovers does not
  // inherit a 30-second delay the next time it drops.
  const scheduleReconnect = useCallback((camId, rtspUrl) => {
    if (timerMap.current[camId]) return       // a retry is already pending; a second would double the attempt rate
    const attempt = (retryMap.current[camId] ?? 0) + 1
    retryMap.current[camId] = attempt
    // 2s, 4s, 8s, 16s, 30s, 30s… Each attempt costs the backend an RTSP open
    // of up to 10 seconds, which is why this is not a flat retry.
    const delay = Math.min(RECONNECT_BASE_MS * 2 ** (attempt - 1), RECONNECT_MAX_MS)

    // Announce an outage once, not on every attempt.
    if (attempt === 1) toast.error('Camera feed lost — reconnecting…', { title: 'Camera error', throttleMs: STREAM_THROTTLE })

    setCameras(p => p.map(c => c.id === camId
      ? { ...c, statusMsg: `Reconnecting in ${Math.round(delay / 1000)}s…` } : c))

    timerMap.current[camId] = setTimeout(() => {
      delete timerMap.current[camId]
      // Two guards before reconnecting, and both matter. The URL check
      // confirms this camera still exists under this id (it may have been
      // removed, or re-added with a new id, while the timer was pending); the
      // socket check confirms nothing else has already reconnected it.
      if (urlToIdMap.current[rtspUrl] === camId && !wsMap.current[camId]) {
        // Through the REF, not the binding: _connect is defined below this
        // function, and reaching it directly would be a cycle between two
        // useCallbacks. The ref is what breaks it.
        connectRef.current?.(camId, rtspUrl, detectMap.current[camId] ?? false)
      }
    }, delay)
  }, [])

  // ── Open WebSocket for a camera ───────────────────────────────────────────
  const _connect = useCallback((camId, rtspUrl, detect = false) => {
    // Read at connect time rather than captured: a reconnect after a token
    // refresh must carry the NEW token, and the socket authenticates by query
    // string because browsers cannot set headers on a WebSocket handshake.
    const token = localStorage.getItem('access_token') || ''
    if (!token) return                        // signed out — nothing to connect with

    // A pending retry is superseded by this attempt
    if (timerMap.current[camId]) {
      clearTimeout(timerMap.current[camId])
      delete timerMap.current[camId]
    }

    // Cancel any lingering WS before opening a new one for the same cam
    const stale = wsMap.current[camId]
    if (stale) {
      // onclose cleared FIRST. Closing a socket fires its close handler, and
      // that handler schedules a reconnect — so without this line, replacing a
      // socket would queue a retry for the one we are deliberately discarding.
      stale.onclose = null
      try { stale.close() } catch {}          // a socket already closing throws; nothing to do about it
      delete wsMap.current[camId]
    }

    const detectParam = detect ? '&detect=1' : ''
    const ws = new WebSocket(`${WS_BASE}/ws/scan/rtsp/?token=${token}${detectParam}`)
    wsMap.current[camId] = ws
    // Started here, not on first frame: the loop paints the dark placeholder
    // while connecting, so the viewport is never an empty white rectangle.
    startRenderLoop(camId)

    ws.onopen = () => {
      // Still "Connecting…" — the socket is up, but the backend has not yet
      // opened the RTSP stream behind it. Those are two separate connections
      // and the UI distinguishes them (wsActive vs streamConnected).
      setCameras(p => p.map(c => c.id === camId ? { ...c, wsActive: true, statusMsg: 'Connecting…' } : c))
      const gate = gateMap.current[camId]
      // The gate is sent with the start message and tags every scan this
      // camera produces. Omitted entirely when unknown, rather than sent
      // empty, so the backend falls back to the guard's own posting.
      ws.send(JSON.stringify({ type: 'start', rtsp_url: rtspUrl, ...(gate ? { gate_id: gate } : {}) }))
    }

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data)
        // Five message types, and `frame` is tested first because it is by far
        // the most common — everything else arrives occasionally.
        if (msg.type === 'frame') {
          acceptFrame(camId, msg.image_b64)   // straight into the decode queue; never drawn from here
          return
        }
        if (msg.type === 'status') {
          // Frames are flowing again — start the backoff over so the next
          // outage retries promptly instead of inheriting a 30 s delay.
          if (msg.connected) retryMap.current[camId] = 0
          setCameras(p => p.map(c => c.id === camId
            ? { ...c, streamConnected: !!msg.connected, statusMsg: msg.message || '' } : c))
          return
        }
        // A backend-reported failure — the RTSP source is unreachable or the
        // stream died. Torn down deliberately here rather than waiting for the
        // socket to drop, so the retry clock starts immediately.
        if (msg.type === 'error') {
          const wsErr = wsMap.current[camId]
          if (wsErr) {
            try { wsErr.onclose = null; wsErr.close() } catch {}   // onclose nulled first, same reason as above
            delete wsMap.current[camId]
          }
          stopRenderLoop(camId)               // stopped here, unlike a plain disconnect, because there is nothing left to paint
          setCameras(p => p.map(c => c.id === camId
            ? { ...c, wsActive: false, streamConnected: false, statusMsg: msg.message || 'Stream failed.' } : c))
          if (urlToIdMap.current[rtspUrl] === camId) scheduleReconnect(camId, rtspUrl)
          return
        }
        // Detections replace wholesale rather than merging: the backend sends
        // the complete current set every time, so anything absent has gone.
        // Rebuilt as a Map keyed by track_id, which is what the draw loop
        // needs to pair a detection with its smoothing state.
        if (msg.type === 'tracks' && msg.tracks) {
          const map = new Map()
          for (const t of msg.tracks) map.set(t.track_id, t)
          trackMap.current[camId] = map       // a ref, so 30 detections a second cost no re-renders
          return
        }
        if (msg.type === 'ocr_update') {
          const cur = trackMap.current[camId]?.get(msg.track_id)
          if (cur) trackMap.current[camId].set(msg.track_id, { ...cur, plate_text: msg.plate_text })
          return
        }
        if (msg.type === 'ml_status') {
          setCameras(p => p.map(c => c.id === camId
            ? { ...c, mlStatus: { stage: msg.stage, message: msg.message } } : c))
          return
        }
        if (msg.type === 'result' && msg.results) {
          setFlash(true)
          setTimeout(() => setFlash(false), 450)
          // _rid uniquely identifies this delivery — pages use it to process each
          // result exactly once (results stay in state, but must not be re-handled
          // on later re-renders). _at lets a freshly mounted page skip stale ones.
          const rid = genId()
          const at  = Date.now()
          setResults(msg.results.map((r, i) => ({ ...r, _camId: camId, _rid: `${rid}.${i}`, _at: at })))
        }
      } catch { /* ignore parse errors */ }
    }

    // onerror always arrives paired with onclose; toasting here as well meant
    // two notifications per drop, every drop. scheduleReconnect announces it.
    ws.onerror = () => {}

    ws.onclose = () => {
      // Only fires for UNEXPECTED closes — disconnectCamera sets ws.onclose = null
      // before calling ws.close(), so intentional disconnects won't reach here.
      delete wsMap.current[camId]
      setCameras(p => p.map(c => c.id === camId
        ? { ...c, wsActive: false, streamConnected: false } : c))

      // Auto-reconnect if camera is still tracked (not removed by the user)
      if (urlToIdMap.current[rtspUrl] === camId) scheduleReconnect(camId, rtspUrl)
    }
  }, [startRenderLoop, stopRenderLoop, scheduleReconnect, acceptFrame])

  // Keep ref in sync so ws.onclose callbacks always call the latest _connect
  connectRef.current = _connect

  // ── Close WebSocket for one camera (user-initiated) ──────────────────────
  const disconnectCamera = useCallback((camId) => {
    // Kill any pending retry first, or the camera reappears seconds after the
    // user closed it.
    if (timerMap.current[camId]) {
      clearTimeout(timerMap.current[camId])
      delete timerMap.current[camId]
    }
    retryMap.current[camId] = 0
    const ws = wsMap.current[camId]
    if (ws) {
      // Nullify onclose BEFORE close() so the auto-reconnect handler doesn't fire
      ws.onclose = null
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'stop' }))
      ws.close()
      delete wsMap.current[camId]
    }
    stopRenderLoop(camId)
    setCameras(p => p.map(c => c.id === camId
      ? { ...c, wsActive: false, streamConnected: false, statusMsg: '' } : c))
  }, [stopRenderLoop])

  // ── Add camera ────────────────────────────────────────────────────────────
  // detect=true   → backend runs plate-scan ML (Entry Management / Security)
  // detect=false  → view-only, no ML (Device Management, Operations Center)
  //
  // Safe to call multiple times for the same URL.  If the camera is already
  // tracked but disconnected, we reconnect it.  If a scan page calls with
  // detect=true and the existing connection has detect=false, we upgrade it
  // so ML starts running for that camera.
  const addCamera = useCallback((name, url, assignment = '', { detect = false, gate = '' } = {}) => {
    const trimUrl = (url || '').trim()
    if (!trimUrl.startsWith('rtsp://')) { toast.error('URL must start with rtsp://'); return null }

    const existingId = urlToIdMap.current[trimUrl]
    if (existingId !== undefined) {
      const ws             = wsMap.current[existingId]
      const currentDetect  = detectMap.current[existingId] ?? false
      // Detection is STICKY: a view-only page (Operations Center, Device
      // Management) must never downgrade a scanning connection — otherwise a
      // reconnect triggered from those pages would silently stop gate scanning.
      const effectiveDetect = detect || currentDetect
      const needsReconnect = !ws
        || ws.readyState === WebSocket.CLOSED
        || ws.readyState === WebSocket.CLOSING
      // Upgrade a view-only connection to scan mode when Entry Management arrives.
      // Also covers the CONNECTING race: if WS hasn't opened yet but detect mode
      // needs to change, cancel it and reopen with the correct detect flag.
      const needsUpgrade = detect && !currentDetect
      // A scan connection tagging logs with the wrong gate must be reopened too
      const gateChanged  = effectiveDetect && !!gate && gateMap.current[existingId] !== gate

      if (gate) {
        gateMap.current[existingId] = gate
        setCameras(p => p.map(c => c.id === existingId && c.gate !== gate ? { ...c, gate } : c))
      }
      if (needsReconnect || needsUpgrade || gateChanged) {
        detectMap.current[existingId] = effectiveDetect
        startRenderLoop(existingId)
        _connect(existingId, trimUrl, effectiveDetect)
      }
      return existingId
    }

    // Brand-new camera
    const id  = genId()
    urlToIdMap.current[trimUrl] = id
    detectMap.current[id]       = detect
    if (gate) gateMap.current[id] = gate
    const cam = {
      id,
      name: (name || '').trim() || `Camera ${id}`,
      url: trimUrl,
      assignment,
      gate: gate || '',
      wsActive: false,
      streamConnected: false,
      statusMsg: '',
    }
    setCameras(p => [...p, cam])
    _connect(id, trimUrl, detect)
    return id
  }, [_connect, startRenderLoop])

  // ── Remove camera (close WS + remove from list + stop tracking) ───────────
  // The difference between disconnect and REMOVE is one line — the urlToIdMap
  // entry. Disconnect keeps it, so reconnecting the same URL returns the same
  // camId with its settings intact; remove drops it, so the URL would come
  // back as a brand-new camera.
  const removeCamera = useCallback((camId) => {
    disconnectCamera(camId)
    setCameras(p => {
      const cam = p.find(c => c.id === camId)
      if (cam) delete urlToIdMap.current[cam.url]
      return p.filter(c => c.id !== camId)
    })
  }, [disconnectCamera])

  useEffect(() => { camerasRef.current = cameras }, [cameras])

  /**
   * Reconcile the cameras connected under one assignment against the server's
   * list — the half addCamera never had.
   *
   * Pages called addCamera once on mount, so the roster could only ever grow: a
   * camera deleted, reassigned or re-addressed in Device Management kept
   * streaming on the guard's screen until somebody reloaded the page. Anything
   * still connected under `assignment` that the server no longer lists is now
   * closed.
   *
   * Scoped to one assignment deliberately — every page shares this provider, so
   * syncing the entry roster must not tear down parking's feeds.
   *
   * `connect: false` prunes without opening anything, for pages that connect a
   * subset on purpose (the guard's parking view opens only the zone it is
   * showing, on hardware that reboots if you open every stream at once).
   */
  const syncCameras = useCallback((assignment, list, { detect = false, connect = true } = {}) => {
    const desired = (list || []).filter(c => (c.rtsp_url || '').trim().startsWith('rtsp://'))
    const wanted  = new Map(desired.map(c => [c.rtsp_url.trim(), c]))

    if (connect) {
      // A camera that moved between entry and parking has to move here too.
      //
      // addCamera dedups by URL and will not re-tag one it already knows, so a
      // camera connected while it was an entry camera goes on wearing that tag
      // for the life of the session: the parking page filters on
      // `assignment === 'parking'` and never sees it, and — worse — it keeps
      // running gate detection because detect is sticky. Dropping it first
      // makes the line below rebuild it with this page's assignment and its
      // detect flag.
      //
      // Nothing legitimate holds a camera under another assignment: every page
      // that connects one passes the assignment off the same Camera row.
      camerasRef.current
        .filter(c => wanted.has(c.url) && c.assignment !== assignment)
        .forEach(c => removeCamera(c.id))

      desired.forEach(c => addCamera(c.name, c.rtsp_url, assignment, { detect, gate: c.gate_id }))
    }

    camerasRef.current
      .filter(c => c.assignment === assignment && !wanted.has(c.url))
      .forEach(c => removeCamera(c.id))

    // A rename reaches the label too — the thumbnail strip and the camera
    // search are how a guard picks a feed, and they read this name.
    setCameras(prev => prev.map(c => {
      const want = c.assignment === assignment ? wanted.get(c.url) : null
      const name = (want?.name || '').trim()
      return name && name !== c.name ? { ...c, name } : c
    }))
  }, [addCamera, removeCamera])

  // ── Disconnect and clear all cameras ──────────────────────────────────────
  // Wholesale teardown, on logout. Written as direct ref clearing rather than
  // a loop over disconnectCamera — which is where the asymmetry noted below
  // comes from.
  const disconnectAll = useCallback(() => {
    Object.keys(wsMap.current).forEach(id => {
      const ws = wsMap.current[id]
      if (ws) {
        ws.onclose = null                     // before close(), so no reconnect is scheduled for a socket being torn down on purpose
        // A courtesy 'stop' so the backend releases the RTSP handle promptly
        // rather than waiting to notice the socket died — which matters on
        // the campus unit, where held sessions are what freeze the feeds.
        try { if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'stop' })); ws.close() } catch {}
      }
    })
    Object.keys(rafMap.current).forEach(id => cancelAnimationFrame(rafMap.current[id]))
    Object.values(timerMap.current).forEach(t => clearTimeout(t))
    timerMap.current   = {}
    retryMap.current   = {}
    wsMap.current      = {}
    rafMap.current     = {}
    frameMap.current   = {}
    trackMap.current   = {}
    smoothMap.current  = {}
    detectMap.current  = {}
    gateMap.current    = {}
    urlToIdMap.current = {}
    paneCountMap.current = {}
    setCameras([])
    setResults([])
    setFlash(false)
    setPaneCounts({})
    // Note, factually — TEARDOWN ASYMMETRY. This clears 11 of the 14 per-camera
    // ref maps. Three are missed, because this path rebuilds the maps directly
    // instead of going through stopRenderLoop (which does clear the first two):
    //
    //   decodeMap    a boolean per camera
    //   queuedMap    a pending base64 JPEG per camera — the one with real size
    //   loopErrMap   a boolean, and cleared by NO path anywhere in this file
    //
    // Not a misbehaviour: genId() only ever counts up and urlToIdMap IS
    // cleared here, so a camera added after this gets a fresh id and never
    // reads the stale entries. The cost is retention — one queued frame per
    // camera, held until the page unloads. Recorded, not changed.
  }, [])

  // Cleanup only on true app unmount (browser tab close / hard logout)
  // True unmount only — the empty dep array means this never re-runs, so
  // navigating between pages does NOT tear down feeds. That is the whole
  // reason the provider sits above the router: reconnecting these cameras is
  // slow, and on the campus unit actively harmful.
  //
  // Only the three things that would otherwise outlive the page are released
  // here (sockets, animation frames, timers). The ref maps are deliberately
  // left alone: the page is going away and they go with it.
  useEffect(() => () => {
    Object.values(wsMap.current).forEach(ws => { try { ws?.close() } catch {} })
    Object.values(rafMap.current).forEach(h => cancelAnimationFrame(h))
    Object.values(timerMap.current).forEach(t => clearTimeout(t))
  }, [])

  return (
    <CameraContext.Provider value={{
      cameras,
      addCamera,
      syncCameras,
      removeCamera,
      disconnectCamera,
      disconnectAll,
      results,
      flash,
      registerCanvas,
      paneCounts,
    }}>
      {children}
    </CameraContext.Provider>
  )
}

// Throws rather than returning null, so a component rendered outside the
// provider fails loudly at mount instead of silently never receiving frames —
// which would look like a broken camera rather than a wiring mistake.
export function useCameraContext() {
  const ctx = useContext(CameraContext)
  if (!ctx) throw new Error('useCameraContext must be used within CameraProvider')
  return ctx
}
