import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { WS_BASE } from '../api/wsBase'

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
  const frameMap  = useRef({})
  const trackMap  = useRef({})
  const smoothMap = useRef({})
  const rafMap    = useRef({})
  const paneCountMap = useRef({}) // id → pane count, mirrors paneCounts inside the RAF loop
  const detectMap = useRef({}) // id → bool: whether this connection runs ML detection
  const gateMap   = useRef({}) // id → gate_id the camera covers (used to tag scan logs)
  const retryMap  = useRef({}) // id → consecutive failed attempts (drives backoff)
  const timerMap  = useRef({}) // id → pending reconnect timeout

  // url → camId: tracks all known cameras by URL, used for dedup + reconnection
  // Using a Map instead of a Set so we can look up existing camId by URL
  const urlToIdMap = useRef({})

  // Ref that always holds the latest _connect function (needed for self-referential reconnect)
  const connectRef = useRef(null)

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

    const draw = () => {
      const panes = canvasMap.current[camId]
      // No canvas registered (page navigated away) — keep looping, draw when back
      if (!panes || Object.keys(panes).length === 0) {
        rafMap.current[camId] = requestAnimationFrame(draw); return
      }

      const img   = frameMap.current[camId]
      const ready = Boolean(img && img.complete && img.naturalWidth > 0)

      // How many pictures are inside this one frame. A dual-lens unit stacks
      // its two views vertically, which makes the frame taller than it is wide
      // — 1920x2160 for the camera this was written for. No ordinary camera
      // sends a portrait frame, so the shape is the tell.
      const count = ready && img.naturalHeight > img.naturalWidth ? 2 : 1
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

        const ctx = canvas.getContext('2d')
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

      rafMap.current[camId] = requestAnimationFrame(draw)
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
    if (attempt === 1) toast.error('Camera feed lost — reconnecting…')

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
          const img = new Image()
          img.src = `data:image/jpeg;base64,${msg.image_b64}`
          frameMap.current[camId] = img
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
  }, [startRenderLoop, stopRenderLoop, scheduleReconnect])

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
