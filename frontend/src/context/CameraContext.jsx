import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'

const WS_BASE = import.meta.env.VITE_API_URL
  ? import.meta.env.VITE_API_URL.replace(/^http/, 'ws')
  : `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`

const TRACK_COLORS = {
  license_plate: '#00ff88',
  vehicle:       '#00ff88',
  motorcycle:    '#3b82f6',
  _default:      '#facc15',
}
const VEHICLE_TYPE_LABELS = { motorcycle: 'Motorcycle' }
const LERP = 0.25

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

  // Mutable refs — never cause re-renders, survive page navigation
  const wsMap     = useRef({})
  const canvasMap = useRef({})
  const frameMap  = useRef({})
  const trackMap  = useRef({})
  const smoothMap = useRef({})
  const rafMap    = useRef({})
  const urlSet    = useRef(new Set())

  // ── Canvas registration (pages call this on mount/unmount) ───────────────
  const registerCanvas = useCallback((camId, el) => {
    if (el) canvasMap.current[camId] = el
    else    delete canvasMap.current[camId]
  }, [])

  // ── 60-fps render loop (keeps running even when canvas is unregistered) ──
  const startRenderLoop = useCallback((camId) => {
    if (rafMap.current[camId]) return
    if (!trackMap.current[camId]) trackMap.current[camId] = new Map()
    if (!smoothMap.current[camId]) smoothMap.current[camId] = new Map()

    const draw = () => {
      const canvas = canvasMap.current[camId]
      // No canvas registered (page navigated away) — keep looping, draw when back
      if (!canvas) { rafMap.current[camId] = requestAnimationFrame(draw); return }

      const img = frameMap.current[camId]
      const vw  = img?.naturalWidth  || canvas.clientWidth  || 1280
      const vh  = img?.naturalHeight || canvas.clientHeight || 720
      if (canvas.width !== vw)  canvas.width  = vw
      if (canvas.height !== vh) canvas.height = vh

      const ctx = canvas.getContext('2d')
      ctx.clearRect(0, 0, vw, vh)

      if (img && img.complete && img.naturalWidth > 0) {
        ctx.drawImage(img, 0, 0, vw, vh)
      } else {
        ctx.fillStyle = '#0d1117'
        ctx.fillRect(0, 0, vw, vh)
      }

      const targets = trackMap.current[camId]  || new Map()
      const smooth  = smoothMap.current[camId] || new Map()

      for (const tid of smooth.keys()) if (!targets.has(tid)) smooth.delete(tid)

      if (targets.size > 0) {
        ctx.font = "12px 'Courier New', monospace"
        ctx.textBaseline = 'top'
        ctx.textAlign    = 'left'

        for (const [tid, track] of targets) {
          const tx1 = track.bbox[0] * vw, ty1 = track.bbox[1] * vh
          const tx2 = track.bbox[2] * vw, ty2 = track.bbox[3] * vh

          if (!smooth.has(tid)) smooth.set(tid, { x1: tx1, y1: ty1, x2: tx2, y2: ty2 })
          const s = smooth.get(tid)
          s.x1 += (tx1 - s.x1) * LERP; s.y1 += (ty1 - s.y1) * LERP
          s.x2 += (tx2 - s.x2) * LERP; s.y2 += (ty2 - s.y2) * LERP

          const px = s.x1, py = s.y1, pw = s.x2 - s.x1, ph = s.y2 - s.y1
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
    const canvas = canvasMap.current[camId]
    if (canvas) canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height)
    delete frameMap.current[camId]
    delete trackMap.current[camId]
    delete smoothMap.current[camId]
  }, [])

  // ── Open WebSocket ────────────────────────────────────────────────────────
  const _connect = useCallback((camId, rtspUrl) => {
    const token = localStorage.getItem('access_token') || ''
    if (!token) return
    const ws = new WebSocket(`${WS_BASE}/ws/scan/rtsp/?token=${token}`)
    wsMap.current[camId] = ws
    startRenderLoop(camId)

    ws.onopen = () => {
      setCameras(p => p.map(c => c.id === camId ? { ...c, wsActive: true, statusMsg: 'Connecting…' } : c))
      ws.send(JSON.stringify({ type: 'start', rtsp_url: rtspUrl }))
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
          setCameras(p => p.map(c => c.id === camId
            ? { ...c, streamConnected: !!msg.connected, statusMsg: msg.message || '' } : c))
          return
        }
        if (msg.type === 'error') {
          toast.error(`Camera error: ${msg.message}`)
          const wsErr = wsMap.current[camId]
          if (wsErr) { try { wsErr.onclose = null; wsErr.close() } catch {} delete wsMap.current[camId] }
          stopRenderLoop(camId)
          setCameras(p => p.map(c => c.id === camId
            ? { ...c, wsActive: false, streamConnected: false, statusMsg: 'Failed — check URL' } : c))
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
        if (msg.type === 'result' && msg.results) {
          setFlash(true)
          setTimeout(() => setFlash(false), 450)
          // Tag each result with the camera id so pages can filter to their own cameras
          setResults(msg.results.map(r => ({ ...r, _camId: camId })))
        }
      } catch { /* ignore parse errors */ }
    }

    ws.onerror = () => toast.error('Camera WebSocket connection error')
    ws.onclose = () => {
      setCameras(p => p.map(c => c.id === camId
        ? { ...c, wsActive: false, streamConnected: false } : c))
    }
  }, [startRenderLoop, stopRenderLoop])

  // ── Close WebSocket for one camera ────────────────────────────────────────
  const disconnectCamera = useCallback((camId) => {
    const ws = wsMap.current[camId]
    if (ws) {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'stop' }))
      ws.onclose = null
      ws.close()
      delete wsMap.current[camId]
    }
    stopRenderLoop(camId)
    setCameras(p => p.map(c => c.id === camId
      ? { ...c, wsActive: false, streamConnected: false, statusMsg: '' } : c))
  }, [stopRenderLoop])

  // ── Add camera — assignment tags which page the camera belongs to ─────────
  const addCamera = useCallback((name, url, assignment = '') => {
    const trimUrl = (url || '').trim()
    if (!trimUrl.startsWith('rtsp://')) { toast.error('URL must start with rtsp://'); return null }
    if (urlSet.current.has(trimUrl)) {
      // Already connected — return existing id so caller can use it
      return null
    }
    urlSet.current.add(trimUrl)
    const id  = genId()
    const cam = {
      id,
      name: (name || '').trim() || `Camera ${id}`,
      url: trimUrl,
      assignment,
      wsActive: false,
      streamConnected: false,
      statusMsg: '',
    }
    setCameras(p => [...p, cam])
    _connect(id, trimUrl)
    return id
  }, [_connect])

  // ── Remove camera (close WS + remove from list) ───────────────────────────
  const removeCamera = useCallback((camId) => {
    disconnectCamera(camId)
    setCameras(p => {
      const cam = p.find(c => c.id === camId)
      if (cam) urlSet.current.delete(cam.url)
      return p.filter(c => c.id !== camId)
    })
  }, [disconnectCamera])

  // ── Disconnect and clear all cameras ─────────────────────────────────────
  const disconnectAll = useCallback(() => {
    Object.keys(wsMap.current).forEach(id => {
      const ws = wsMap.current[id]
      if (ws) { try { if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'stop' })); ws.onclose = null; ws.close() } catch {} }
    })
    Object.keys(rafMap.current).forEach(id => cancelAnimationFrame(rafMap.current[id]))
    wsMap.current     = {}
    rafMap.current    = {}
    frameMap.current  = {}
    trackMap.current  = {}
    smoothMap.current = {}
    urlSet.current    = new Set()
    setCameras([])
    setResults([])
    setFlash(false)
  }, [])

  // Cleanup only on true app unmount (browser tab close / hard logout)
  useEffect(() => () => {
    Object.values(wsMap.current).forEach(ws => { try { ws?.close() } catch {} })
    Object.values(rafMap.current).forEach(h => cancelAnimationFrame(h))
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
