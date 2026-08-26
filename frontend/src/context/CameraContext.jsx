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
  if (!w || !h || h <= w) return 1
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

let _seq = 0
const genId = () => ++_seq

const CameraContext = createContext(null)

export function CameraProvider({ children }) {
  const [cameras, setCameras] = useState([])
  const [results, setResults] = useState([])
  const [flash,   setFlash]   = useState(false)

  // camId → how many views are packed into one frame (1 for an ordinary
  // camera, 2 for a dual-lens unit). State, not a ref, because pages render a
  // viewport per view and have to re-render when the answer changes.
  const [paneCounts, setPaneCounts] = useState({})

  // Mutable refs — never cause re-renders, survive page navigation
  const wsMap     = useRef({})
  const canvasMap = useRef({})
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
      img.onerror = done
      img.src = `data:image/jpeg;base64,${payload}`
    }

    if (decodeMap.current[camId]) {
      queuedMap.current[camId] = b64        // newest wins; older one is dropped
      return
    }
    start(b64)
  }, [])

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
    const key = pane == null ? FULL_FRAME : String(pane)
    const panes = canvasMap.current[camId] ?? (canvasMap.current[camId] = {})
    if (el) {
      panes[key] = el
    } else {
      delete panes[key]
      if (Object.keys(panes).length === 0) delete canvasMap.current[camId]
    }
  }, [])

  // ── 60-fps render loop (keeps running even when canvas is unregistered) ──
  const startRenderLoop = useCallback((camId) => {
    if (rafMap.current[camId]) return
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
      const ready = Boolean(img && img.complete && img.naturalWidth > 0)

      // How many pictures are inside this one frame — see lensCount above.
      const count = ready ? lensCount(img.naturalWidth, img.naturalHeight) : 1
      if (ready && paneCountMap.current[camId] !== count) {
        paneCountMap.current[camId] = count
        setPaneCounts(prev => ({ ...prev, [camId]: count }))
      }

      const fw = img?.naturalWidth  || 1280           // full frame, both views
      const fh = img?.naturalHeight || 720
      // Floored: a canvas height attribute is an integer, and letting the
      // browser truncate it instead would drift the second view's offset.
      const sh = Math.floor(fh / count)                // one view's height

      const targets = trackMap.current[camId]  || new Map()
      const smooth  = smoothMap.current[camId] || new Map()

      for (const tid of smooth.keys()) if (!targets.has(tid)) smooth.delete(tid)

      // Smoothing is advanced once per frame, in full-frame pixels, before any
      // pane is drawn. Doing it inside the pane loop would step the same track
      // twice per frame on a split camera and make the boxes race ahead.
      for (const [tid, track] of targets) {
        const tx1 = track.bbox[0] * fw, ty1 = track.bbox[1] * fh
        const tx2 = track.bbox[2] * fw, ty2 = track.bbox[3] * fh
        if (!smooth.has(tid)) smooth.set(tid, { x1: tx1, y1: ty1, x2: tx2, y2: ty2 })
        const s = smooth.get(tid)
        s.x1 += (tx1 - s.x1) * LERP; s.y1 += (ty1 - s.y1) * LERP
        s.x2 += (tx2 - s.x2) * LERP; s.y2 += (ty2 - s.y2) * LERP
      }

      for (const [key, canvas] of Object.entries(panes)) {
        // A viewport that asked for the whole frame gets it, stacked views and
        // all — only the ones that named a pane are cropped to it.
        const whole = key === FULL_FRAME
        const pane  = whole ? 0 : Math.min(Number(key), count - 1)
        const ch    = whole ? fh : sh                  // this canvas's height
        const sy    = whole ? 0  : pane * sh           // top of this view

        if (canvas.width !== fw) canvas.width  = fw
        if (canvas.height !== ch) canvas.height = ch

        // null when the browser has thrown the backing store away, which it
        // does to hidden tabs. Skip this pane rather than throwing; the next
        // frame gets a fresh context once the tab is visible again.
        const ctx = canvas.getContext('2d')
        if (!ctx) continue
        ctx.clearRect(0, 0, fw, ch)

        if (ready) {
          ctx.drawImage(img, 0, sy, fw, ch, 0, 0, fw, ch)
        } else {
          ctx.fillStyle = '#04121F'
          ctx.fillRect(0, 0, fw, ch)
        }

        if (targets.size === 0) continue

        ctx.font = "12px 'Courier New', monospace"
        ctx.textBaseline = 'top'
        ctx.textAlign    = 'left'

        for (const [tid, track] of targets) {
          const s = smooth.get(tid)
          if (!s) continue

          // Track coordinates are relative to the whole frame; shift them into
          // this view and skip anything belonging to the other one.
          const py = s.y1 - sy, ph = s.y2 - s.y1
          if (py + ph <= 0 || py >= ch) continue

          const px = s.x1, pw = s.x2 - s.x1
          const color = trackColor(track)

          ctx.strokeStyle = color
          ctx.lineWidth   = track.vehicle_type ? 3 : 2
          ctx.setLineDash(track.vehicle_type ? [8, 4] : [])
          ctx.strokeRect(px, py, pw, ph)
          ctx.setLineDash([])

          const PAD = 6, TH = 21
          const labelText = track.vehicle_type
            ? (VEHICLE_TYPE_LABELS[track.vehicle_type] ?? track.vehicle_type)
            : `${track.plate_text || `T#${tid}`}${track.detection_conf ? ` ${(track.detection_conf * 100).toFixed(0)}%` : ''}`

          const tw = ctx.measureText(labelText).width + PAD * 2
          ctx.fillStyle = 'rgba(0,0,0,0.75)'; ctx.fillRect(px, py - TH, tw, TH)
          ctx.fillStyle = color;              ctx.fillRect(px, py - TH, 3, TH)
          ctx.fillStyle = '#fff';             ctx.fillText(labelText, px + PAD + 2, py - TH + 4)
        }
      }

      } catch (err) {
        // One bad frame must not end the feed. Logged once per camera so a
        // recurring fault is visible without flooding the console at 60 fps.
        if (!loopErrMap.current[camId]) {
          loopErrMap.current[camId] = true
          console.error('[camera] render loop error (recovering)', err)
        }
      } finally {
        rafMap.current[camId] = requestAnimationFrame(draw)
      }
    }
    rafMap.current[camId] = requestAnimationFrame(draw)
  }, [])

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
    delete queuedMap.current[camId]
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
  const scheduleReconnect = useCallback((camId, rtspUrl) => {
    if (timerMap.current[camId]) return
    const attempt = (retryMap.current[camId] ?? 0) + 1
    retryMap.current[camId] = attempt
    const delay = Math.min(RECONNECT_BASE_MS * 2 ** (attempt - 1), RECONNECT_MAX_MS)

    // Announce an outage once, not on every attempt.
    if (attempt === 1) toast.error('Camera feed lost — reconnecting…', { title: 'Camera error', throttleMs: STREAM_THROTTLE })

    setCameras(p => p.map(c => c.id === camId
      ? { ...c, statusMsg: `Reconnecting in ${Math.round(delay / 1000)}s…` } : c))

    timerMap.current[camId] = setTimeout(() => {
      delete timerMap.current[camId]
      if (urlToIdMap.current[rtspUrl] === camId && !wsMap.current[camId]) {
        connectRef.current?.(camId, rtspUrl, detectMap.current[camId] ?? false)
      }
    }, delay)
  }, [])

  // ── Open WebSocket for a camera ───────────────────────────────────────────
  const _connect = useCallback((camId, rtspUrl, detect = false) => {
    const token = localStorage.getItem('access_token') || ''
    if (!token) return

    // A pending retry is superseded by this attempt
    if (timerMap.current[camId]) {
      clearTimeout(timerMap.current[camId])
      delete timerMap.current[camId]
    }

    // Cancel any lingering WS before opening a new one for the same cam
    const stale = wsMap.current[camId]
    if (stale) {
      stale.onclose = null
      try { stale.close() } catch {}
      delete wsMap.current[camId]
    }

    const detectParam = detect ? '&detect=1' : ''
    const ws = new WebSocket(`${WS_BASE}/ws/scan/rtsp/?token=${token}${detectParam}`)
    wsMap.current[camId] = ws
    startRenderLoop(camId)

    ws.onopen = () => {
      setCameras(p => p.map(c => c.id === camId ? { ...c, wsActive: true, statusMsg: 'Connecting…' } : c))
      const gate = gateMap.current[camId]
      ws.send(JSON.stringify({ type: 'start', rtsp_url: rtspUrl, ...(gate ? { gate_id: gate } : {}) }))
    }

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data)
        if (msg.type === 'frame') {
          acceptFrame(camId, msg.image_b64)
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
        if (msg.type === 'error') {
          const wsErr = wsMap.current[camId]
          if (wsErr) {
            try { wsErr.onclose = null; wsErr.close() } catch {}
            delete wsMap.current[camId]
          }
          stopRenderLoop(camId)
          setCameras(p => p.map(c => c.id === camId
            ? { ...c, wsActive: false, streamConnected: false, statusMsg: msg.message || 'Stream failed.' } : c))
          if (urlToIdMap.current[rtspUrl] === camId) scheduleReconnect(camId, rtspUrl)
          return
        }
        if (msg.type === 'tracks' && msg.tracks) {
          const map = new Map()
          for (const t of msg.tracks) map.set(t.track_id, t)
          trackMap.current[camId] = map
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
  const disconnectAll = useCallback(() => {
    Object.keys(wsMap.current).forEach(id => {
      const ws = wsMap.current[id]
      if (ws) {
        ws.onclose = null
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
  }, [])

  // Cleanup only on true app unmount (browser tab close / hard logout)
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

export function useCameraContext() {
  const ctx = useContext(CameraContext)
  if (!ctx) throw new Error('useCameraContext must be used within CameraProvider')
  return ctx
}
