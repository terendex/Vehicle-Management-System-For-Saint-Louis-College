import { useState, useEffect, useCallback, useRef } from 'react'
import {
  ParkingCircle, Bike, Car, Plus, RefreshCw, Upload, Save,
  Pencil, Eye, Trash2, X, Loader2, CheckCircle2, Video, VideoOff, Settings, Wifi,
} from 'lucide-react'
import AdminLayout from '../../components/Layout/AdminLayout'
import { zoneApi } from '../../api/parking'
import { useMultiRtspStream } from '../../hooks/useMultiRtspStream'
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

function inside(pt, s) {
  return pt.x >= s.x1 && pt.x <= s.x2 && pt.y >= s.y1 && pt.y <= s.y2
}

function autoLabel(list, cat) {
  const pre  = cat === 'motorcycle' ? 'M' : 'C'
  const nums = list.map(s => parseInt(s.space_number.replace(/\D/g, ''), 10)).filter(n => !isNaN(n))
  const n    = nums.length ? Math.max(...nums) + 1 : 1
  return `${pre}${String(n).padStart(2, '0')}`
}

export default function ParkingManagement() {
  const [zones,        setZones]        = useState([])
  const [selId,        setSelId]        = useState(null)
  const [mode,         setMode]         = useState('live')
  const [drafts,       setDrafts]       = useState([])
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
  // IP Camera state (backend AI detection stream)
  const [camStatus,    setCamStatus]    = useState({})   // { zone_id: bool }
  const [camLoading,   setCamLoading]   = useState(false)
  const [rtspModal,    setRtspModal]    = useState(false)
  const [rtspInput,    setRtspInput]    = useState('')
  const [savingRtsp,   setSavingRtsp]   = useState(false)

  // Live-view RTSP cameras panel
  const [showCamPanel, setShowCamPanel] = useState(false)
  const [camAddName,   setCamAddName]   = useState('')
  const [camAddUrl,    setCamAddUrl]    = useState('')

  const token = typeof localStorage !== 'undefined' ? localStorage.getItem('access_token') || '' : ''
  const {
    cameras:        parkingCams,
    activeCamId:    pkActiveCamId,
    setActiveCamId: setPkActiveCam,
    activeCam:      pkActiveCam,
    addCamera:      addPkCamera,
    removeCamera:   removePkCameraHook,
    disconnectAll:  disconnectAllPkCams,
    registerCanvas: registerPkCanvas,
  } = useMultiRtspStream(token)

  const svgEl     = useRef(null)
  const fileRef   = useRef(null)
  const dragStart = useRef(null)
  const dragging  = useRef(false)
  const rbRef     = useRef(null)
  const draftsRef = useRef([])

  useEffect(() => { draftsRef.current = drafts }, [drafts])
  useEffect(() => { rbRef.current = rubberBand }, [rubberBand])

  const selZone     = zones.find(z => z.id === selId) ?? null
  const camRunning  = selId != null && !!camStatus[selId]
  // Relative URL → Vite proxy → Django (no CORS issues)
  const streamUrl   = (selId && token)
    ? `/api/vehicles/parking-zones/${selId}/stream/?token=${token}`
    : null

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

  // Load view-cameras from localStorage when zone changes
  useEffect(() => {
    disconnectAllPkCams()
    if (!selId) return
    const saved = JSON.parse(localStorage.getItem(`rtsp_cams_${selId}`) || '[]')
    saved.forEach(c => addPkCamera(c.name, c.url))
  }, [selId]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Camera status polling ───────────────────────────────────────
  const refreshCamStatus = useCallback(async () => {
    try {
      const s = await zoneApi.getCameraStatus()
      // API returns {zone_id_str: bool} — normalise keys to int
      const normalised = {}
      Object.entries(s).forEach(([k, v]) => { normalised[parseInt(k, 10)] = v })
      setCamStatus(normalised)
    } catch { /* silent */ }
  }, [])

  useEffect(() => {
    refreshCamStatus()
    const t = setInterval(refreshCamStatus, 5000)
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
  }, [mode, selId]) // eslint-disable-line react-hooks/exhaustive-deps

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
    } catch { alert('Failed to create zone.') }
    finally { setAddingZone(false) }
  }

  const handleDeleteZone = async () => {
    if (!selZone || !confirm(`Delete zone "${selZone.name}" and all its spaces?`)) return
    if (camRunning) await zoneApi.stopCamera(selId).catch(() => {})
    try {
      await zoneApi.remove(selZone.id)
      const rest = zones.filter(z => z.id !== selZone.id)
      setZones(rest)
      setSelId(rest[0]?.id ?? null)
      setMode('live')
    } catch { alert('Failed to delete zone.') }
  }

  // ── Image upload ────────────────────────────────────────────────
  const onImageFile = async (e) => {
    const f = e.target.files?.[0]
    if (!f || !selId) return
    try {
      const z = await zoneApi.uploadImage(selId, f)
      setZones(p => p.map(x => x.id === z.id ? { ...x, ...z } : x))
    } catch { alert('Image upload failed.') }
    finally { e.target.value = '' }
  }

  // ── SVG drawing (edit mode) ─────────────────────────────────────
  const onMouseDown = (e) => {
    if (mode !== 'edit') return
    e.preventDefault()
    const pt = svgPt(e, svgEl.current)
    dragStart.current = pt
    dragging.current  = false
    setSelDraft(null)
  }

  const onMouseMove = (e) => {
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
    if (!dragStart.current) return
    const pt          = svgPt(e, svgEl.current)
    const wasDragging = dragging.current
    const rb          = rbRef.current

    dragStart.current = null
    dragging.current  = false
    setRubberBand(null)
    rbRef.current = null

    if (!wasDragging) {
      const hit = [...draftsRef.current].reverse().find(s => inside(pt, s))
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
      is_occupied: false, occupied_by: '',
    }])
    setSelDraft(id)
    setDraftLabel(label)
  }

  const onMouseLeave = () => {
    if (!dragStart.current) return
    dragStart.current = null; dragging.current = false
    setRubberBand(null); rbRef.current = null
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
      const payload = drafts.map(s => ({ space_number: s.space_number, x1: s.x1, y1: s.y1, x2: s.x2, y2: s.y2 }))
      const saved   = await zoneApi.saveLayout(selId, payload)
      setZones(p => p.map(z => z.id === selId ? { ...z, spaces: saved } : z))
      setDrafts(saved.map(s => ({ ...s, _id: s.id })))
      setMode('live')
    } catch { alert('Save failed.') }
    finally { setSaving(false) }
  }

  // ── Space click (live mode) ─────────────────────────────────────
  const onSpaceClick = (sp) => {
    if (mode !== 'live' || camRunning) return
    if (sp.is_occupied) setSpaceOp({ type: 'free',   space: sp })
    else                { setSpaceOp({ type: 'occupy', space: sp }); setSpaceOpPlate('') }
  }

  const confirmOccupy = async () => {
    if (!spaceOpPlate.trim()) return
    try {
      const u = await zoneApi.markOccupied(spaceOp.space.id, spaceOpPlate)
      setZones(p => p.map(z => z.id === selId ? { ...z, spaces: z.spaces.map(s => s.id === u.id ? u : s) } : z))
      setSpaceOp(null)
    } catch { alert('Failed.') }
  }

  const confirmFree = async () => {
    try {
      const u = await zoneApi.markFree(spaceOp.space.id)
      setZones(p => p.map(z => z.id === selId ? { ...z, spaces: z.spaces.map(s => s.id === u.id ? u : s) } : z))
      setSpaceOp(null)
    } catch { alert('Failed.') }
  }

  // ── Parking live-cam add/remove ─────────────────────────────────────────────
  const handleAddPkCam = () => {
    if (!camAddUrl.trim() || !selId) return
    addPkCamera(camAddName, camAddUrl.trim())
    const saved = JSON.parse(localStorage.getItem(`rtsp_cams_${selId}`) || '[]')
    saved.push({ name: camAddName.trim() || `Camera ${saved.length + 1}`, url: camAddUrl.trim() })
    localStorage.setItem(`rtsp_cams_${selId}`, JSON.stringify(saved))
    setCamAddName('')
    setCamAddUrl('')
  }

  const handleRemovePkCam = (camId) => {
    const cam = parkingCams.find(c => c.id === camId)
    removePkCameraHook(camId)
    if (cam && selId) {
      const saved = JSON.parse(localStorage.getItem(`rtsp_cams_${selId}`) || '[]')
      localStorage.setItem(`rtsp_cams_${selId}`, JSON.stringify(saved.filter(c => c.url !== cam.url)))
    }
  }

  // ── IP Camera controls ──────────────────────────────────────────
  const handleStartCamera = async () => {
    if (!selId) return
    setCamLoading(true)
    try {
      await zoneApi.startCamera(selId)
      await refreshCamStatus()
    } catch (err) {
      const msg = err?.response?.data?.error || 'Failed to start camera.'
      alert(msg)
    } finally { setCamLoading(false) }
  }

  const handleStopCamera = async () => {
    if (!selId) return
    setCamLoading(true)
    try {
      await zoneApi.stopCamera(selId)
      await refreshCamStatus()
    } catch { alert('Failed to stop camera.') }
    finally { setCamLoading(false) }
  }

  const handleOpenRtsp = () => {
    setRtspInput(selZone?.rtsp_url || '')
    setRtspModal(true)
  }

  const handleSaveRtsp = async (e) => {
    e.preventDefault()
    setSavingRtsp(true)
    try {
      const z = await zoneApi.update(selId, { rtsp_url: rtspInput.trim() })
      setZones(p => p.map(x => x.id === z.id ? { ...x, rtsp_url: z.rtsp_url } : x))
      setRtspModal(false)
    } catch { alert('Failed to save RTSP URL.') }
    finally { setSavingRtsp(false) }
  }

  // ── Derived ─────────────────────────────────────────────────────
  const spaceList   = mode === 'edit' ? drafts : (selZone?.spaces ?? [])
  const selDraftSp  = drafts.find(s => s._id === selDraft)
  const liveSpaces  = selZone?.spaces ?? []
  const occ         = liveSpaces.filter(s => s.is_occupied).length
  const sumFr       = liveSpaces.length - occ

  // ════════════════════════════════════════════════════════════════
  return (
    <AdminLayout>
      <div className="pm-page">

        {/* Header */}
        <div className="pm-header">
          <div className="pm-header-left">
            <ParkingCircle size={22} className="pm-header-icon" />
            <div>
              <h1 className="pm-title">Live Parking Management</h1>
              <p className="pm-subtitle">
                Draw space boxes in Edit Layout mode. Connect an IP CCTV camera via RTSP to
                detect vehicles automatically — the backend updates occupancy in real time.
              </p>
            </div>
          </div>
          <div className="pm-header-actions">
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

        {/* Zone tabs */}
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

                {mode === 'live' && (
                  <>
                    {/* Camera start / stop */}
                    {camRunning ? (
                      <button
                        className="pm-btn pm-btn--camera-on"
                        onClick={handleStopCamera}
                        disabled={camLoading}
                      >
                        {camLoading
                          ? <Loader2 size={13} className="pm-spin" />
                          : <VideoOff size={13} />
                        } Stop Camera
                      </button>
                    ) : (
                      <button
                        className="pm-btn pm-btn--outline"
                        onClick={handleStartCamera}
                        disabled={camLoading || !selZone?.rtsp_url}
                        title={!selZone?.rtsp_url ? 'Configure an RTSP URL first' : ''}
                      >
                        {camLoading
                          ? <Loader2 size={13} className="pm-spin" />
                          : <Video size={13} />
                        } Start Camera
                      </button>
                    )}

                    <div className="pm-summary">
                      <span className="pm-sum-free">{sumFr} free</span>
                      <span className="pm-sum-sep">·</span>
                      <span className="pm-sum-occ">{occ} occupied</span>
                      <span className="pm-sum-sep">·</span>
                      <span className="pm-sum-total">{liveSpaces.length} total</span>
                    </div>

                    {camRunning && (
                      <span className="pm-camera-badge">
                        <span className="pm-camera-dot" /> Camera active
                      </span>
                    )}
                  </>
                )}

                {mode === 'edit' && (
                  <span className="pm-edit-hint">Click-drag on the image to draw a space box</span>
                )}
              </div>

              <div className="pm-toolbar-right">
                {mode === 'live' && (
                  <button className="pm-btn pm-btn--outline" onClick={handleOpenRtsp} title="Configure RTSP URL">
                    <Settings size={13} /> RTSP
                  </button>
                )}
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
                <button className="pm-btn pm-btn--danger-outline" onClick={handleDeleteZone}>
                  <Trash2 size={13} /> Delete Zone
                </button>
              </div>
            </div>

            {/* Canvas */}
            <div className="pm-canvas-wrapper">
              {/* Background: MJPEG stream OR reference image OR placeholder */}
              {camRunning && streamUrl ? (
                <img
                  key={`stream-${selId}`}
                  src={streamUrl}
                  className="pm-canvas-img"
                  draggable={false}
                  alt="Camera stream"
                />
              ) : selZone.reference_image_url ? (
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
              >
                {/* Parking space boxes */}
                {spaceList.map(s => {
                  const x  = Math.min(s.x1, s.x2), y = Math.min(s.y1, s.y2)
                  const w  = Math.abs(s.x2 - s.x1), h = Math.abs(s.y2 - s.y1)
                  const id = s._id ?? s.id
                  const sel    = id === selDraft
                  const color  = s.is_occupied ? '#EF4444' : '#22C55E'
                  const fill   = s.is_occupied ? 'rgba(239,68,68,0.3)' : 'rgba(34,197,94,0.25)'
                  const stroke = sel ? '#FBBF24' : color
                  return (
                    <g
                      key={id}
                      onClick={() => mode === 'live'
                        ? onSpaceClick(s)
                        : (setSelDraft(id), setDraftLabel(s.space_number))
                      }
                      style={{ cursor: 'pointer' }}
                    >
                      <rect
                        x={x} y={y} width={w} height={h}
                        fill={fill}
                        stroke={stroke}
                        strokeWidth={sel ? 0.006 : 0.003}
                        strokeDasharray={sel ? '0.015 0.007' : undefined}
                        rx={0.004}
                      />
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
                          fill="#FECACA" fontSize={0.02} fontWeight="600"
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
                      fill="rgba(42,43,97,0.15)"
                      stroke="#2A2B61"
                      strokeWidth={0.004}
                      strokeDasharray="0.018 0.008"
                      rx={0.004}
                    />
                  )
                })()}
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
                {camRunning
                  ? 'IP camera active — backend detects vehicles and updates occupancy automatically'
                  : mode === 'live'
                    ? 'Click a space to toggle manually · auto-refreshes every 8 s'
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
                          ref={el => registerPkCanvas(cam.id, el)}
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
                          background: pkActiveCam?.streamConnected ? '#22c55e' : pkActiveCam?.wsActive ? '#f59e0b' : '#6b7280' }}
                      />
                      {pkActiveCam?.name || 'Camera'}
                    </div>
                  </>
                ) : (
                  <div className="pm-cam-empty" style={{ minHeight: 160 }}>
                    <Video size={28} style={{ color: '#374151' }} />
                    <p>Add a camera below</p>
                  </div>
                )}
              </div>

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
                        style={{ background: cam.streamConnected ? '#22c55e' : cam.wsActive ? '#f59e0b' : '#6b7280' }}
                      />
                      <Wifi size={14} />
                      <span className="pm-cam-strip-label">{cam.name}</span>
                      <button
                        className="pm-cam-remove"
                        style={{ marginLeft: 'auto', flexShrink: 0 }}
                        onClick={e => { e.stopPropagation(); handleRemovePkCam(cam.id) }}
                        title="Remove"
                      >
                        <X size={11} />
                      </button>
                    </div>
                  ))}
                </div>
              )}

              {/* Remove button when only 1 camera */}
              {parkingCams.length === 1 && (
                <div style={{ padding: '0 12px 4px', display: 'flex', justifyContent: 'flex-end' }}>
                  <button
                    className="pm-btn pm-btn--danger-outline"
                    style={{ padding: '4px 10px', fontSize: 11 }}
                    onClick={() => handleRemovePkCam(parkingCams[0].id)}
                  >
                    <X size={12} /> Remove
                  </button>
                </div>
              )}

              {/* Add camera form */}
              <div className="pm-cam-add-col">
                <input
                  className="pm-modal-input"
                  placeholder="Name (optional)"
                  value={camAddName}
                  onChange={e => setCamAddName(e.target.value)}
                />
                <input
                  className="pm-modal-input"
                  style={{ fontFamily: 'monospace', fontSize: 12 }}
                  placeholder="rtsp://user:pass@ip:port/stream"
                  value={camAddUrl}
                  onChange={e => setCamAddUrl(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') handleAddPkCam() }}
                />
                <button
                  className="pm-btn pm-btn--primary"
                  style={{ width: '100%', justifyContent: 'center' }}
                  onClick={handleAddPkCam}
                  disabled={!camAddUrl.trim()}
                >
                  <Plus size={13} /> Add Camera
                </button>
              </div>

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

      {/* ── Modal: Configure RTSP ── */}
      {rtspModal && (
        <div className="pm-overlay" onClick={() => setRtspModal(false)}>
          <form className="pm-modal" onSubmit={handleSaveRtsp} onClick={e => e.stopPropagation()}>
            <div className="pm-modal-header">
              <span>Configure IP Camera — {selZone?.name}</span>
              <button type="button" className="pm-modal-close" onClick={() => setRtspModal(false)}><X size={16} /></button>
            </div>
            <div className="pm-modal-body">
              <label className="pm-modal-label">RTSP URL</label>
              <input
                className="pm-modal-input"
                value={rtspInput}
                onChange={e => setRtspInput(e.target.value)}
                placeholder="rtsp://<device-id>:<password>@192.168.1.x:554/stream0"
                autoFocus
              />
              <p style={{ fontSize: 12, color: '#7C80A3', marginTop: 8, lineHeight: 1.5 }}>
                <strong>V380 Pro format:</strong> <code>rtsp://&lt;device-id&gt;:&lt;password&gt;@&lt;camera-IP&gt;:554/stream0</code>
                <br />The device ID and IP are in the V380 app → Device Info.
                <br />Use <code>stream1</code> instead of <code>stream0</code> if the feed doesn't appear (stream0 is H.265; stream1 is H.264).
              </p>
            </div>
            <div className="pm-modal-footer">
              <button type="button" className="pm-btn pm-btn--outline" onClick={() => setRtspModal(false)}>Cancel</button>
              <button type="submit" className="pm-btn pm-btn--primary" disabled={savingRtsp}>
                {savingRtsp ? <Loader2 size={13} className="pm-spin" /> : <Save size={13} />} Save
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
    </AdminLayout>
  )
}
