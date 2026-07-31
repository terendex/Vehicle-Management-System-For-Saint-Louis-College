import { useState, useEffect, useCallback, useRef, Fragment } from 'react'
import { useLiveUpdates } from '../../realtime/useLiveUpdates'
import {
  ParkingCircle, Bike, Car, Camera, Plus, RefreshCw, Upload, Save,
  Pencil, Eye, Trash2, X, Loader2, CheckCircle2, Video, Wifi,
  AlertTriangle, CheckCircle, Square, PenTool, LayoutGrid,
} from 'lucide-react'
import { toast } from 'sonner'
import AdminLayout from '../../components/Layout/AdminLayout'
import { zoneApi } from '../../api/parking'
import { camerasApi } from '../../api/cameras'
import { useCameraContext } from '../../context/CameraContext'
import './ParkingManagement.css'

const CAT_OPTS = [
  { key: 'motorcycle', label: 'Motorcycle', Icon: Bike },
  { key: 'car',        label: 'Car',        Icon: Car  },
]

const DRAG_MIN = 0.02

let _tid = 0
const tid = () => `_n${++_tid}`

function svgPt(e, el) {
  const r = el.getBoundingClientRect()
  return {
    x: Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)),
    y: Math.max(0, Math.min(1, (e.clientY - r.top)  / r.height)),
  }
}

function pointInPolygon(pt, points) {
  let inside = false
  for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
    const [xi, yi] = points[i]
    const [xj, yj] = points[j]
    const intersect = ((yi > pt.y) !== (yj > pt.y)) &&
      (pt.x < (xj - xi) * (pt.y - yi) / (yj - yi) + xi)
    if (intersect) inside = !inside
  }
  return inside
}

function hitTest(pt, s) {
  if (s.points && s.points.length >= 3) return pointInPolygon(pt, s.points)
  return pt.x >= s.x1 && pt.x <= s.x2 && pt.y >= s.y1 && pt.y <= s.y2
}

function autoLabel(list, cat) {
  const pre  = cat === 'motorcycle' ? 'M' : 'C'
  const nums = list.map(s => parseInt(s.space_number.replace(/\D/g, ''), 10)).filter(n => !isNaN(n))
  const n    = nums.length ? Math.max(...nums) + 1 : 1
  return `${pre}${String(n).padStart(2, '0')}`
}

// `embedded` renders the content without its own AdminLayout so the page can
// live as a tab inside Parking Space Management.
export default function ParkingManagement({ embedded = false }) {
  const Wrapper = embedded ? Fragment : AdminLayout
  const [zones,        setZones]        = useState([])
  const [selId,        setSelId]        = useState(null)
  const [mode,         setMode]         = useState('live')
  const [drafts,       setDrafts]       = useState([])
  const [tool,         setTool]         = useState('box') // 'box' | 'pen' (Edit Layout only)
  const [penPoints,    setPenPoints]    = useState([])
  const [penCursor,    setPenCursor]    = useState(null)
  const [selDraft,     setSelDraft]     = useState(null)
  const [draftLabel,   setDraftLabel]   = useState('')
  const [rubberBand,   setRubberBand]   = useState(null)
  const [loading,      setLoading]      = useState(true)
  const [saving,       setSaving]       = useState(false)
  const [spaceOp,      setSpaceOp]      = useState(null)
  const [spaceOpPlate, setSpaceOpPlate] = useState('')
  const [showNew,      setShowNew]      = useState(false)
  const [newZone,      setNewZone]      = useState({ name: '', vehicle_category: 'motorcycle' })
  const [addingZone,   setAddingZone]   = useState(false)
  const [confirmModal, setConfirmModal] = useState(null) // { type: 'deleteZone' }
  const [resultModal,  setResultModal]  = useState(null) // { type: 'success'|'error', message }
  // Live-view RTSP cameras panel
  const [showCamPanel, setShowCamPanel] = useState(false)
  // Device Management cameras assignable to a zone, and per-zone detection status
  const [deviceCams,   setDeviceCams]   = useState([])
  const [camRunning,   setCamRunning]   = useState({})
  const [assigning,    setAssigning]    = useState(false)
  const [toggling,     setToggling]     = useState(false)
  const [capturing,    setCapturing]    = useState(false)

  const { cameras: allCameras, addCamera: addPkCamera, removeCamera: removePkCameraHook, registerCanvas: registerPkCanvas } = useCameraContext()
  const [pkActiveCamId, setPkActiveCam] = useState(null)
  const parkingCams = allCameras.filter(c => c.assignment === 'parking')
  const pkActiveCam = parkingCams.find(c => c.id === pkActiveCamId) ?? parkingCams[0] ?? null

  useEffect(() => {
    if (!pkActiveCamId && parkingCams.length > 0) setPkActiveCam(parkingCams[0].id)
  }) // intentionally no deps — runs after every render until activeCamId is set

  const svgEl       = useRef(null)
  const fileRef     = useRef(null)
  const dragStart   = useRef(null)
  const dragging    = useRef(false)
  const rbRef       = useRef(null)
  const draftsRef   = useRef([])
  const camCanvasRefs = useRef({})

  useEffect(() => { draftsRef.current = drafts }, [drafts])
  useEffect(() => { rbRef.current = rubberBand }, [rubberBand])

  const selZone = zones.find(z => z.id === selId) ?? null

  // ── Load zones ──────────────────────────────────────────────────
  const loadZones = useCallback(async () => {
    setLoading(true)
    try {
      const data = await zoneApi.listAll()
      setZones(data)
      setSelId(id => id ?? data[0]?.id ?? null)
    } finally { setLoading(false) }
  }, [])

  useEffect(() => { loadZones() }, [loadZones])

  // Live-refresh zones/occupancy on parking changes
  useLiveUpdates(loadZones, ['parkingzone', 'parkingspace'])

  // Load parking cameras once on mount — persists across navigation
  useEffect(() => {
    camerasApi.list({ assignment: 'parking' })
      .then(cams => {
        setDeviceCams(cams)
        cams.forEach(c => addPkCamera(c.name, c.rtsp_url, 'parking'))
      })
      .catch(() => {})
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Poll detection (auto-camera) status for all zones
  const refreshCamStatus = useCallback(async () => {
    try { setCamRunning(await zoneApi.getCameraStatus()) } catch { /* silent */ }
  }, [])

  useEffect(() => {
    refreshCamStatus()
    const t = setInterval(refreshCamStatus, 8000)
    return () => clearInterval(t)
  }, [refreshCamStatus])

  // Live occupancy polling (always on in live mode)
  const refreshZone = useCallback(async () => {
    if (!selId) return
    try {
      const z = await zoneApi.get(selId)
      setZones(p => p.map(x => x.id === z.id ? z : x))
    } catch { /* silent */ }
  }, [selId])

  useEffect(() => {
    if (mode !== 'live') return
    const t = setInterval(refreshZone, 8000)
    return () => clearInterval(t)
  }, [mode, refreshZone])

  // Copy live spaces into drafts when entering edit mode
  useEffect(() => {
    if (mode === 'edit' && selZone) {
      setDrafts(selZone.spaces.map(s => ({ ...s, _id: s.id })))
      setSelDraft(null)
    }
    setTool('box')
    setPenPoints([])
    setPenCursor(null)
  }, [mode, selId]) // eslint-disable-line react-hooks/exhaustive-deps

  // Escape cancels an in-progress pen shape
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape' && tool === 'pen' && penPoints.length > 0) setPenPoints([])
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [tool, penPoints])

  // ── Zone CRUD ───────────────────────────────────────────────────
  const handleAddZone = async (e) => {
    e.preventDefault()
    if (!newZone.name.trim()) return
    setAddingZone(true)
    try {
      const z = await zoneApi.create(newZone)
      setZones(p => [...p, { ...z, spaces: [] }])
      setSelId(z.id)
      setShowNew(false)
      setNewZone({ name: '', vehicle_category: 'motorcycle' })
      setMode('edit')
    } catch {
      setShowNew(false)
      setResultModal({ type: 'error', message: 'Failed to create zone. Please try again.' })
    } finally { setAddingZone(false) }
  }

  const handleDeleteZone = () => {
    if (!selZone) return
    setConfirmModal({ type: 'deleteZone' })
  }

  const executeDeleteZone = async () => {
    setConfirmModal(null)
    try {
      await zoneApi.remove(selZone.id)
      const rest = zones.filter(z => z.id !== selZone.id)
      setZones(rest)
      setSelId(rest[0]?.id ?? null)
      setMode('live')
      setResultModal({ type: 'success', message: `Zone "${selZone.name}" has been deleted.` })
    } catch {
      setResultModal({ type: 'error', message: 'Failed to delete zone. Please try again.' })
    }
  }

  // ── Camera assignment (Device Management) ──────────────────────
  const handleAssignCamera = async (e) => {
    const cameraId = e.target.value ? Number(e.target.value) : null
    if (!selId) return
    setAssigning(true)
    try {
      const z = await zoneApi.update(selId, { camera: cameraId })
      setZones(p => p.map(x => x.id === z.id ? { ...x, ...z } : x))
    } catch {
      setResultModal({ type: 'error', message: 'Failed to assign camera. Please try again.' })
    } finally { setAssigning(false) }
  }

  const toggleDetection = async () => {
    if (!selZone) return
    setToggling(true)
    try {
      if (camRunning[selZone.id]) {
        await zoneApi.stopCamera(selZone.id)
      } else {
        await zoneApi.startCamera(selZone.id)
      }
      await refreshCamStatus()
    } catch (err) {
      setResultModal({
        type: 'error',
        message: err?.response?.data?.error || 'Failed to toggle camera detection.',
      })
    } finally { setToggling(false) }
  }

  // ── Capture a still frame from the live feed as the reference image ─────
  const handleCapture = async () => {
    if (!selId || !pkActiveCam) return
    const canvas = camCanvasRefs.current[pkActiveCam.id]
    if (!canvas || !pkActiveCam.streamConnected) {
      setResultModal({ type: 'error', message: 'Camera is not connected yet — wait for the live feed then try again.' })
      return
    }
    setCapturing(true)
    try {
      const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.92))
      if (!blob) throw new Error('empty capture')
      const file = new File([blob], `zone-${selId}-capture.jpg`, { type: 'image/jpeg' })
      const z    = await zoneApi.uploadImage(selId, file)
      setZones(p => p.map(x => x.id === z.id ? { ...x, ...z } : x))
      toast.success('Captured frame set as reference image.')
    } catch {
      setResultModal({ type: 'error', message: 'Failed to capture frame. Please try again.' })
    } finally { setCapturing(false) }
  }

  // ── Image upload ────────────────────────────────────────────────
  const onImageFile = async (e) => {
    const f = e.target.files?.[0]
    if (!f || !selId) return
    try {
      const z = await zoneApi.uploadImage(selId, f)
      setZones(p => p.map(x => x.id === z.id ? { ...x, ...z } : x))
    } catch { toast.error('Image upload failed.') }
    finally { e.target.value = '' }
  }

  // ── SVG drawing (edit mode) ─────────────────────────────────────
  const onMouseDown = (e) => {
    if (mode !== 'edit' || tool !== 'box') return
    e.preventDefault()
    const pt = svgPt(e, svgEl.current)
    dragStart.current = pt
    dragging.current  = false
    setSelDraft(null)
  }

  const onMouseMove = (e) => {
    if (mode === 'edit' && tool === 'pen') {
      if (penPoints.length > 0) setPenCursor(svgPt(e, svgEl.current))
      return
    }
    if (!dragStart.current) return
    const pt = svgPt(e, svgEl.current)
    const dx = Math.abs(pt.x - dragStart.current.x)
    const dy = Math.abs(pt.y - dragStart.current.y)
    if (dx > DRAG_MIN || dy > DRAG_MIN) dragging.current = true
    if (dragging.current) {
      const rb = { x1: dragStart.current.x, y1: dragStart.current.y, x2: pt.x, y2: pt.y }
      setRubberBand(rb)
      rbRef.current = rb
    }
  }

  const onMouseUp = (e) => {
    if (tool !== 'box') return
    if (!dragStart.current) return
    const pt          = svgPt(e, svgEl.current)
    const wasDragging = dragging.current
    const rb          = rbRef.current

    dragStart.current = null
    dragging.current  = false
    setRubberBand(null)
    rbRef.current = null

    if (!wasDragging) {
      const hit = [...draftsRef.current].reverse().find(s => hitTest(pt, s))
      if (hit) { setSelDraft(hit._id); setDraftLabel(hit.space_number) }
      else      setSelDraft(null)
      return
    }

    if (!rb) return
    const nx1 = Math.min(rb.x1, rb.x2), nx2 = Math.max(rb.x1, rb.x2)
    const ny1 = Math.min(rb.y1, rb.y2), ny2 = Math.max(rb.y1, rb.y2)
    if (nx2 - nx1 < DRAG_MIN || ny2 - ny1 < DRAG_MIN) return

    const id    = tid()
    const label = autoLabel(draftsRef.current, selZone?.vehicle_category ?? 'motorcycle')
    setDrafts(p => [...p, {
      _id: id, id: null,
      space_number: label,
      vehicle_category: selZone?.vehicle_category,
      x1: nx1, y1: ny1, x2: nx2, y2: ny2,
      points: null,
      is_occupied: false, occupied_by: '',
    }])
    setSelDraft(id)
    setDraftLabel(label)
  }

  const onMouseLeave = () => {
    setPenCursor(null)
    if (!dragStart.current) return
    dragStart.current = null; dragging.current = false
    setRubberBand(null); rbRef.current = null
  }

  // ── Pen tool (freeform polygon spaces) ───────────────────────────
  const finalizePenShape = (points) => {
    if (points.length < 3) return
    const xs = points.map(p => p.x), ys = points.map(p => p.y)
    const id    = tid()
    const label = autoLabel(draftsRef.current, selZone?.vehicle_category ?? 'motorcycle')
    setDrafts(p => [...p, {
      _id: id, id: null,
      space_number: label,
      vehicle_category: selZone?.vehicle_category,
      x1: Math.min(...xs), y1: Math.min(...ys), x2: Math.max(...xs), y2: Math.max(...ys),
      points: points.map(p => [p.x, p.y]),
      is_occupied: false, occupied_by: '',
    }])
    setSelDraft(id)
    setDraftLabel(label)
  }

  const onSvgClick = (e) => {
    if (mode !== 'edit' || tool !== 'pen') return
    const pt = svgPt(e, svgEl.current)
    if (penPoints.length >= 3 && Math.hypot(pt.x - penPoints[0].x, pt.y - penPoints[0].y) < 0.02) {
      finalizePenShape(penPoints)
      setPenPoints([])
      setPenCursor(null)
      return
    }
    setPenPoints(prev => [...prev, pt])
  }

  const commitLabel = () => {
    if (!selDraft || !draftLabel.trim()) return
    setDrafts(p => p.map(s => s._id === selDraft ? { ...s, space_number: draftLabel.trim() } : s))
  }

  const deleteSelDraft = () => {
    setDrafts(p => p.filter(s => s._id !== selDraft))
    setSelDraft(null)
  }

  // ── Save layout ─────────────────────────────────────────────────
  const saveLayout = async () => {
    if (!selId) return
    setSaving(true)
    try {
      const payload = drafts.map(s => ({
        space_number: s.space_number, x1: s.x1, y1: s.y1, x2: s.x2, y2: s.y2,
        points: s.points ?? null,
      }))
      const saved   = await zoneApi.saveLayout(selId, payload)
      setZones(p => p.map(z => z.id === selId ? { ...z, spaces: saved } : z))
      setDrafts(saved.map(s => ({ ...s, _id: s.id })))
      setMode('live')
    } catch { setResultModal({ type: 'error', message: 'Failed to save layout. Please try again.' }) }
    finally { setSaving(false) }
  }

  // ── Space click (live mode) ─────────────────────────────────────
  const onSpaceClick = (sp) => {
    if (mode !== 'live') return
    if (sp.is_occupied) setSpaceOp({ type: 'free',   space: sp })
    else                { setSpaceOp({ type: 'occupy', space: sp }); setSpaceOpPlate('') }
  }

  const confirmOccupy = async () => {
    if (!spaceOpPlate.trim()) return
    try {
      const u = await zoneApi.markOccupied(spaceOp.space.id, spaceOpPlate)
      setZones(p => p.map(z => z.id === selId ? { ...z, spaces: z.spaces.map(s => s.id === u.id ? u : s) } : z))
      setSpaceOp(null)
    } catch { toast.error('Failed to mark space as occupied.') }
  }

  const confirmFree = async () => {
    try {
      const u = await zoneApi.markFree(spaceOp.space.id)
      setZones(p => p.map(z => z.id === selId ? { ...z, spaces: z.spaces.map(s => s.id === u.id ? u : s) } : z))
      setSpaceOp(null)
    } catch { toast.error('Failed to free space.') }
  }

  // ── Derived ─────────────────────────────────────────────────────
  const spaceList   = mode === 'edit' ? drafts : (selZone?.spaces ?? [])
  const selDraftSp  = drafts.find(s => s._id === selDraft)
  const liveSpaces  = selZone?.spaces ?? []
  const occ         = liveSpaces.filter(s => s.is_occupied).length
  const sumFr       = liveSpaces.length - occ

  // ════════════════════════════════════════════════════════════════
  return (
    <Wrapper>
      <div className="pm-page">

        {/* The visible page title and blurb are gone — the sidebar already says
            which page this is, and they cost a whole band of vertical space.
            The heading stays for screen readers, which have no sidebar context.
            Standalone (non-embedded) use keeps the visible header. */}
        {embedded ? (
          <h1 className="pm-sr-only">Parking Space Management</h1>
        ) : (
          <div className="pm-header">
            <div className="pm-header-left">
              <ParkingCircle size={22} className="pm-header-icon" />
              <div>
                <h1 className="pm-title">Parking</h1>
                <p className="pm-subtitle">
                  Draw space boxes in Edit Layout mode. Connect an IP CCTV camera via RTSP to
                  detect vehicles automatically — the backend updates occupancy in real time.
                </p>
              </div>
            </div>
          </div>
        )}

        {/* Occupancy at a glance — these numbers used to be a run of tiny text
            buried in the toolbar between the detection controls. */}
        {selZone && mode === 'live' && (
          <div className="pm-stats-row">
            <div className="pm-stat-card">
              <div className="pm-stat-icon green"><CheckCircle2 size={18} /></div>
              <div>
                <p className="pm-stat-val">{sumFr}</p>
                <p className="pm-stat-lbl">Free</p>
              </div>
            </div>
            <div className="pm-stat-card">
              <div className="pm-stat-icon red"><Car size={18} /></div>
              <div>
                <p className="pm-stat-val">{occ}</p>
                <p className="pm-stat-lbl">Occupied</p>
              </div>
            </div>
            <div className="pm-stat-card">
              <div className="pm-stat-icon blue"><ParkingCircle size={18} /></div>
              <div>
                <p className="pm-stat-val">{liveSpaces.length}</p>
                <p className="pm-stat-lbl">Total Spaces</p>
              </div>
            </div>
            <div className="pm-stat-card">
              <div className="pm-stat-icon purple"><LayoutGrid size={18} /></div>
              <div>
                <p className="pm-stat-val">{zones.length}</p>
                <p className="pm-stat-lbl">{zones.length === 1 ? 'Zone' : 'Zones'}</p>
              </div>
            </div>
          </div>
        )}

        {/* Zone bar — labelled tabs on the left, page actions on the right.
            The actions used to sit in their own header row beside the title;
            folding them in here removes a row and fills the bar's dead space. */}
        <div className="pm-zone-bar">
          <span className="pm-zone-bar-label">
            <LayoutGrid size={13} /> Zones
          </span>
          <div className="pm-zone-tabs">
            {zones.map(z => {
              const C = CAT_OPTS.find(c => c.key === z.vehicle_category)?.Icon ?? ParkingCircle
              return (
                <button
                  key={z.id}
                  className={`pm-zone-tab${z.id === selId ? ' pm-zone-tab--active' : ''}`}
                  onClick={() => { setSelId(z.id); setMode('live') }}
                >
                  <C size={13} /> {z.name}
                </button>
              )
            })}
            {!loading && zones.length === 0 && (
              <span className="pm-zone-empty">No zones yet — create one to start.</span>
            )}
          </div>

          <div className="pm-zone-bar-actions">
            <button className="pm-btn pm-btn--outline" onClick={loadZones} disabled={loading}>
              <RefreshCw size={14} className={loading ? 'pm-spin' : ''} /> Refresh
            </button>
            {mode === 'live' && (
              <button
                className={`pm-btn ${showCamPanel ? 'pm-btn--camera-on' : 'pm-btn--outline'}`}
                onClick={() => setShowCamPanel(p => !p)}
              >
                <Video size={14} /> Cameras {parkingCams.length > 0 && `(${parkingCams.length})`}
              </button>
            )}
            <button className="pm-btn pm-btn--primary" onClick={() => setShowNew(true)}>
              <Plus size={14} /> New Zone
            </button>
          </div>
        </div>

        {/* Main content row: parking map + optional camera sidebar */}
        {!selZone ? (
          <div className="pm-canvas-placeholder">
            <ParkingCircle size={36} />
            <span>{loading ? 'Loading…' : 'Select or create a parking zone.'}</span>
          </div>
        ) : (
          <div className="pm-content-row">
          <div className="pm-canvas-area" style={{ flex: 1, minWidth: 0 }}>

            {/* Toolbar */}
            <div className="pm-toolbar">
              <div className="pm-toolbar-left">
                <div className="pm-mode-toggle">
                  <button
                    className={`pm-mode-btn${mode === 'live' ? ' pm-mode-btn--active' : ''}`}
                    onClick={() => setMode('live')}
                  >
                    <Eye size={13} /> Live View
                  </button>
                  <button
                    className={`pm-mode-btn${mode === 'edit' ? ' pm-mode-btn--active' : ''}`}
                    onClick={() => setMode('edit')}
                  >
                    <Pencil size={13} /> Edit Layout
                  </button>
                </div>

                <div className="pm-cam-assign">
                  <Video size={13} />
                  <select
                    className="pm-cam-assign-select"
                    value={selZone.camera ?? ''}
                    onChange={handleAssignCamera}
                    disabled={assigning}
                  >
                    <option value="">No camera assigned</option>
                    {deviceCams.map(c => (
                      <option key={c.id} value={c.id}>{c.name}</option>
                    ))}
                  </select>
                  {deviceCams.length === 0 && (
                    <span style={{ color: '#9DB6C9' }}>None registered — add one in Device Management</span>
                  )}
                </div>

                {mode === 'live' && selZone.camera != null && (
                  <button
                    className="pm-btn pm-btn--outline"
                    onClick={toggleDetection}
                    disabled={toggling}
                  >
                    {toggling
                      ? <Loader2 size={13} className="pm-spin" />
                      : <Video size={13} />}
                    {camRunning[selZone.id] ? 'Stop Detection' : 'Start Detection'}
                  </button>
                )}

                {mode === 'live' && (
                  <span className={`pm-detect-badge ${camRunning[selZone.id] ? 'pm-detect-badge--on' : 'pm-detect-badge--off'}`}>
                    {camRunning[selZone.id] ? 'Auto-detect running' : 'Auto-detect off'}
                  </span>
                )}

                {/* Occupancy counts now live in the stats row above. */}

                {mode === 'edit' && (
                  <div className="pm-mode-toggle">
                    <button
                      className={`pm-mode-btn${tool === 'box' ? ' pm-mode-btn--active' : ''}`}
                      onClick={() => { setTool('box'); setPenPoints([]) }}
                    >
                      <Square size={13} /> Box
                    </button>
                    <button
                      className={`pm-mode-btn${tool === 'pen' ? ' pm-mode-btn--active' : ''}`}
                      onClick={() => setTool('pen')}
                    >
                      <PenTool size={13} /> Pen
                    </button>
                  </div>
                )}

                {mode === 'edit' && tool === 'box' && (
                  <span className="pm-edit-hint">Click-drag on the image to draw a space box</span>
                )}

                {mode === 'edit' && tool === 'pen' && (
                  <>
                    <span className="pm-edit-hint">
                      {penPoints.length === 0
                        ? 'Click to place points, then click the first (yellow) point to close the shape'
                        : `${penPoints.length} point${penPoints.length === 1 ? '' : 's'} placed — click the yellow point to close, or Esc to cancel`}
                    </span>
                    {penPoints.length >= 3 && (
                      <button
                        className="pm-btn pm-btn--outline"
                        onClick={() => { finalizePenShape(penPoints); setPenPoints([]) }}
                      >
                        <CheckCircle2 size={13} /> Finish Shape
                      </button>
                    )}
                    {penPoints.length > 0 && (
                      <button className="pm-btn pm-btn--outline" onClick={() => setPenPoints([])}>
                        <X size={13} /> Cancel
                      </button>
                    )}
                  </>
                )}
              </div>

              <div className="pm-toolbar-right">
                {mode === 'edit' && (
                  <>
                    <input ref={fileRef} type="file" accept="image/*" style={{ display: 'none' }} onChange={onImageFile} />
                    <button className="pm-btn pm-btn--outline" onClick={() => fileRef.current?.click()}>
                      <Upload size={13} /> Upload Image
                    </button>
                    <button className="pm-btn pm-btn--primary" onClick={saveLayout} disabled={saving}>
                      {saving ? <Loader2 size={13} className="pm-spin" /> : <Save size={13} />} Save Layout
                    </button>
                  </>
                )}
                {/* Separated from the working controls: deleting a zone is
                    irreversible and sat one button away from Start Detection. */}
                <span className="pm-toolbar-divider" />
                <button
                  className="pm-btn pm-btn--danger-outline"
                  onClick={handleDeleteZone}
                  title={`Delete the "${selZone.name}" zone and its spaces`}
                >
                  <Trash2 size={13} /> Delete Zone
                </button>
              </div>
            </div>

            {/* Canvas */}
            <div className="pm-canvas-wrapper">
              {/* Background: reference image OR placeholder */}
              {selZone.reference_image_url ? (
                <img src={selZone.reference_image_url} className="pm-canvas-img" draggable={false} alt="" />
              ) : (
                <div className="pm-canvas-no-img">
                  {mode === 'edit'
                    ? 'No image yet — draw boxes directly or upload a reference photo'
                    : 'No reference image. Switch to Edit Layout to upload one.'}
                </div>
              )}

              {/* SVG overlay */}
              <svg
                ref={svgEl}
                className="pm-canvas-svg"
                viewBox="0 0 1 1"
                preserveAspectRatio="none"
                style={{ cursor: mode === 'edit' ? 'crosshair' : 'default' }}
                onMouseDown={onMouseDown}
                onMouseMove={onMouseMove}
                onMouseUp={onMouseUp}
                onMouseLeave={onMouseLeave}
                onClick={onSvgClick}
              >
                {/* Parking space boxes */}
                {spaceList.map(s => {
                  const x  = Math.min(s.x1, s.x2), y = Math.min(s.y1, s.y2)
                  const w  = Math.abs(s.x2 - s.x1), h = Math.abs(s.y2 - s.y1)
                  const id = s._id ?? s.id
                  const sel    = id === selDraft
                  const color  = s.is_occupied ? '#D93B3B' : '#1BA968'
                  const fill   = s.is_occupied ? 'rgba(217, 59, 59,0.3)' : 'rgba(27, 169, 104,0.25)'
                  const stroke = sel ? '#F6CE11' : color
                  return (
                    <g
                      key={id}
                      onClick={() => mode === 'live'
                        ? onSpaceClick(s)
                        : (setSelDraft(id), setDraftLabel(s.space_number))
                      }
                      style={{ cursor: 'pointer' }}
                    >
                      {s.points && s.points.length >= 3 ? (
                        <polygon
                          points={s.points.map(p => p.join(',')).join(' ')}
                          fill={fill}
                          stroke={stroke}
                          strokeWidth={sel ? 0.006 : 0.003}
                          strokeDasharray={sel ? '0.015 0.007' : undefined}
                        />
                      ) : (
                        <rect
                          x={x} y={y} width={w} height={h}
                          fill={fill}
                          stroke={stroke}
                          strokeWidth={sel ? 0.006 : 0.003}
                          strokeDasharray={sel ? '0.015 0.007' : undefined}
                          rx={0.004}
                        />
                      )}
                      <text
                        x={x + w/2}
                        y={y + h/2 - (s.is_occupied && s.occupied_by ? 0.013 : 0)}
                        textAnchor="middle" dominantBaseline="middle"
                        fill="#fff" fontSize={0.028} fontWeight="bold"
                        style={{ paintOrder:'stroke', stroke:'rgba(0,0,0,0.55)', strokeWidth:'0.005' }}
                      >
                        {s.space_number}
                      </text>
                      {s.is_occupied && s.occupied_by && (
                        <text
                          x={x + w/2} y={y + h/2 + 0.023}
                          textAnchor="middle" dominantBaseline="middle"
                          fill="#F3C0C0" fontSize={0.02} fontWeight="600"
                          style={{ paintOrder:'stroke', stroke:'rgba(0,0,0,0.5)', strokeWidth:'0.004' }}
                        >
                          {s.occupied_by}
                        </text>
                      )}
                    </g>
                  )
                })}

                {/* Rubber band (edit mode) */}
                {rubberBand && (() => {
                  const { x1, y1, x2, y2 } = rubberBand
                  return (
                    <rect
                      x={Math.min(x1,x2)} y={Math.min(y1,y2)}
                      width={Math.abs(x2-x1)} height={Math.abs(y2-y1)}
                      fill="rgba(3, 57, 108,0.15)"
                      stroke="#03396C"
                      strokeWidth={0.004}
                      strokeDasharray="0.018 0.008"
                      rx={0.004}
                    />
                  )
                })()}

                {/* In-progress pen shape */}
                {mode === 'edit' && tool === 'pen' && penPoints.length > 0 && (
                  <g>
                    <polyline
                      points={
                        penPoints.map(p => `${p.x},${p.y}`).join(' ')
                        + (penCursor ? ` ${penCursor.x},${penCursor.y}` : '')
                      }
                      fill="none"
                      stroke="#03396C"
                      strokeWidth={0.003}
                      strokeDasharray="0.01 0.006"
                    />
                    {penPoints.map((p, i) => (
                      <circle
                        key={i}
                        cx={p.x} cy={p.y}
                        r={i === 0 ? 0.01 : 0.006}
                        fill={i === 0 ? '#F6CE11' : '#03396C'}
                        stroke="#fff"
                        strokeWidth={0.0015}
                      />
                    ))}
                  </g>
                )}
              </svg>

              {/* Space label popover (edit mode) */}
              {selDraftSp && mode === 'edit' && (
                <div
                  className="pm-popover"
                  style={{
                    left: `${(selDraftSp.x1 + selDraftSp.x2) / 2 * 100}%`,
                    top:  `${selDraftSp.y1 * 100}%`,
                  }}
                  onMouseDown={e => e.stopPropagation()}
                >
                  <input
                    className="pm-popover-input"
                    value={draftLabel}
                    onChange={e => setDraftLabel(e.target.value)}
                    onBlur={commitLabel}
                    onKeyDown={e => { if (e.key === 'Enter') commitLabel() }}
                    maxLength={10} autoFocus
                  />
                  <button className="pm-popover-del" onClick={deleteSelDraft} title="Delete space">
                    <Trash2 size={13} />
                  </button>
                </div>
              )}
            </div>

            {/* Legend */}
            <div className="pm-legend">
              <span className="pm-legend-item"><span className="pm-legend-dot pm-legend-dot--free" />Free</span>
              <span className="pm-legend-item"><span className="pm-legend-dot pm-legend-dot--occ" />Occupied</span>
              <span className="pm-legend-note">
                {mode === 'live'
                  ? 'Click a space to toggle manually · auto-refreshes every 8 s'
                  : tool === 'pen'
                    ? 'Click to trace a freeform shape · click a space to rename or delete'
                    : 'Click-drag to draw · click a box to rename or delete'}
              </span>
            </div>
          </div>{/* /pm-canvas-area */}

          {/* ── Camera Sidebar ── */}
          {showCamPanel && (
            <div className="pm-cam-sidebar">

              {/* Sidebar header */}
              <div className="pm-cam-sidebar-header">
                <span className="pm-cam-panel-title">
                  <Video size={14} /> {selZone.name}
                  {parkingCams.length > 0 && (
                    <span className="pm-cam-live-badge">
                      {parkingCams.filter(c => c.streamConnected).length}/{parkingCams.length} live
                    </span>
                  )}
                </span>
                <button
                  className="pm-btn pm-btn--outline"
                  style={{ padding: '3px 8px', fontSize: 11 }}
                  onClick={() => setShowCamPanel(false)}
                >
                  <X size={12} />
                </button>
              </div>

              {/* Main camera view — shows active cam at full size */}
              <div className="pm-cam-main-wrap">
                {parkingCams.length > 0 ? (
                  <>
                    {parkingCams.map((cam, idx) => (
                      <div
                        key={cam.id}
                        style={{ display: pkActiveCamId === cam.id ? 'block' : 'none', width: '100%', ...(idx === 0 ? {} : { position: 'absolute', inset: 0 }) }}
                      >
                        <canvas
                          ref={el => { registerPkCanvas(cam.id, el); camCanvasRefs.current[cam.id] = el }}
                          style={{ width: '100%', display: 'block', background: '#000', minHeight: 180 }}
                        />
                      </div>
                    ))}
                    {/* Connecting overlay */}
                    {pkActiveCam && !pkActiveCam.streamConnected && pkActiveCam.wsActive && (
                      <div className="pm-cam-overlay">
                        <div className="pm-spin-sm" />
                        <span>{pkActiveCam.statusMsg || 'Connecting…'}</span>
                      </div>
                    )}
                    {/* Name + status badge */}
                    <div className="pm-cam-main-badge">
                      <span
                        style={{ width: 7, height: 7, borderRadius: '50%', flexShrink: 0,
                          background: pkActiveCam?.streamConnected ? '#1BA968' : pkActiveCam?.wsActive ? '#E0B00C' : '#5C7B92' }}
                      />
                      {pkActiveCam?.name || 'Camera'}
                    </div>
                  </>
                ) : (
                  <div className="pm-cam-empty" style={{ minHeight: 160 }}>
                    <Video size={28} style={{ color: '#2E4C63' }} />
                    <p>Add a camera below</p>
                  </div>
                )}
              </div>

              {parkingCams.length > 0 && (
                <button
                  className="pm-btn pm-btn--primary"
                  style={{ width: '100%', justifyContent: 'center', marginTop: 8 }}
                  onClick={handleCapture}
                  disabled={capturing || !pkActiveCam?.streamConnected}
                  title={pkActiveCam?.streamConnected ? '' : 'Waiting for the live feed to connect…'}
                >
                  {capturing ? <Loader2 size={13} className="pm-spin" /> : <Camera size={13} />}
                  Use as Reference Image
                </button>
              )}

              {/* Thumbnail strip — only when 2+ cameras */}
              {parkingCams.length > 1 && (
                <div className="pm-cam-thumb-strip">
                  {parkingCams.map(cam => (
                    <div
                      key={`st-${cam.id}`}
                      className={`pm-cam-strip-thumb ${pkActiveCamId === cam.id ? 'active' : ''}`}
                      onClick={() => setPkActiveCam(cam.id)}
                    >
                      <span
                        className="pm-cam-strip-dot"
                        style={{ background: cam.streamConnected ? '#1BA968' : cam.wsActive ? '#E0B00C' : '#5C7B92' }}
                      />
                      <Wifi size={14} />
                      <span className="pm-cam-strip-label">{cam.name}</span>
                    </div>
                  ))}
                </div>
              )}

              {parkingCams.length === 0 && (
                <p style={{ fontSize: 12, color: '#6B8CA6', margin: '8px 0 0', textAlign: 'center' }}>
                  No parking cameras configured — add them in Device Management.
                </p>
              )}

            </div>
          )}

          </div>
        )}

      </div>

      {/* ── Modal: New Zone ── */}
      {showNew && (
        <div className="pm-overlay" onClick={() => setShowNew(false)}>
          <form className="pm-modal" onSubmit={handleAddZone} onClick={e => e.stopPropagation()}>
            <div className="pm-modal-header">
              <span>Create Parking Zone</span>
              <button type="button" className="pm-modal-close" onClick={() => setShowNew(false)}><X size={16} /></button>
            </div>
            <div className="pm-modal-body">
              <label className="pm-modal-label">Zone Name <span className="pm-req">*</span></label>
              <input
                className="pm-modal-input"
                value={newZone.name}
                onChange={e => setNewZone(p => ({ ...p, name: e.target.value }))}
                placeholder="e.g. Motorcycle Bay A"
                required autoFocus
              />
              <label className="pm-modal-label" style={{ marginTop: 16 }}>Vehicle Category</label>
              <div className="pm-cat-toggle">
                {CAT_OPTS.map(c => (
                  <button
                    key={c.key} type="button"
                    className={`pm-cat-btn${newZone.vehicle_category === c.key ? ' pm-cat-btn--active' : ''}`}
                    onClick={() => setNewZone(p => ({ ...p, vehicle_category: c.key }))}
                  >
                    <c.Icon size={13} /> {c.label}
                  </button>
                ))}
              </div>
            </div>
            <div className="pm-modal-footer">
              <button type="button" className="pm-btn pm-btn--outline" onClick={() => setShowNew(false)}>Cancel</button>
              <button type="submit" className="pm-btn pm-btn--primary" disabled={addingZone || !newZone.name.trim()}>
                {addingZone ? <Loader2 size={13} className="pm-spin" /> : <Plus size={13} />} Create Zone
              </button>
            </div>
          </form>
        </div>
      )}

      {/* ── Modal: Occupy ── */}
      {spaceOp?.type === 'occupy' && (
        <div className="pm-overlay" onClick={() => setSpaceOp(null)}>
          <div className="pm-modal" onClick={e => e.stopPropagation()}>
            <div className="pm-modal-header">
              <span>Mark Space {spaceOp.space.space_number} Occupied</span>
              <button className="pm-modal-close" onClick={() => setSpaceOp(null)}><X size={16} /></button>
            </div>
            <div className="pm-modal-body">
              <label className="pm-modal-label">Plate Number <span className="pm-req">*</span></label>
              <input
                className="pm-modal-input"
                value={spaceOpPlate}
                onChange={e => setSpaceOpPlate(e.target.value.toUpperCase())}
                placeholder="e.g. ABC 1234"
                maxLength={20} autoFocus
                onKeyDown={e => e.key === 'Enter' && confirmOccupy()}
              />
            </div>
            <div className="pm-modal-footer">
              <button className="pm-btn pm-btn--outline" onClick={() => setSpaceOp(null)}>Cancel</button>
              <button className="pm-btn pm-btn--primary" onClick={confirmOccupy} disabled={!spaceOpPlate.trim()}>
                <CheckCircle2 size={13} /> Confirm Occupied
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Modal: Free ── */}
      {spaceOp?.type === 'free' && (
        <div className="pm-overlay" onClick={() => setSpaceOp(null)}>
          <div className="pm-modal" onClick={e => e.stopPropagation()}>
            <div className="pm-modal-header">
              <span>Free Up Space {spaceOp.space.space_number}</span>
              <button className="pm-modal-close" onClick={() => setSpaceOp(null)}><X size={16} /></button>
            </div>
            <div className="pm-modal-body">
              <p className="pm-modal-msg">
                Occupied by <strong>{spaceOp.space.occupied_by}</strong>. Mark as free?
              </p>
            </div>
            <div className="pm-modal-footer">
              <button className="pm-btn pm-btn--outline" onClick={() => setSpaceOp(null)}>Cancel</button>
              <button className="pm-btn pm-btn--green" onClick={confirmFree}>
                <CheckCircle2 size={13} /> Mark Free
              </button>
            </div>
          </div>
        </div>
      )}
      {/* ── Confirmation Modal ── */}
      {confirmModal?.type === 'deleteZone' && (
        <div className="pm-overlay" onClick={() => setConfirmModal(null)}>
          <div className="pm-modal pm-modal--centered" onClick={e => e.stopPropagation()}>
            <button className="pm-modal-close" onClick={() => setConfirmModal(null)}><X size={16} /></button>
            <AlertTriangle size={32} className="pm-modal-warn-icon" />
            <h2 className="pm-modal-center-title">Delete Zone?</h2>
            <p className="pm-modal-center-body">
              This will permanently delete <strong>{selZone?.name}</strong> and all its spaces. This cannot be undone.
            </p>
            <div className="pm-modal-center-actions">
              <button className="pm-btn pm-btn--outline" onClick={() => setConfirmModal(null)}>Cancel</button>
              <button className="pm-btn pm-btn--danger" onClick={executeDeleteZone}>Delete Zone</button>
            </div>
          </div>
        </div>
      )}

      {/* ── Result Modal ── */}
      {resultModal && (
        <div className="pm-overlay" onClick={() => setResultModal(null)}>
          <div className="pm-modal pm-modal--centered" onClick={e => e.stopPropagation()}>
            <button className="pm-modal-close" onClick={() => setResultModal(null)}><X size={16} /></button>
            {resultModal.type === 'success'
              ? <CheckCircle size={32} className="pm-modal-success-icon" />
              : <AlertTriangle size={32} className="pm-modal-error-icon" />}
            <h2 className="pm-modal-center-title">{resultModal.type === 'success' ? 'Success' : 'Error'}</h2>
            <p className="pm-modal-center-body">{resultModal.message}</p>
            <div className="pm-modal-center-actions">
              <button className="pm-btn pm-btn--primary" onClick={() => setResultModal(null)}>OK</button>
            </div>
          </div>
        </div>
      )}

    </Wrapper>
  )
}
