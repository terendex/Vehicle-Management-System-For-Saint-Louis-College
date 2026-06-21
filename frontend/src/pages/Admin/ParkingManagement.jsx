import { useState, useEffect, useCallback, useRef } from 'react'
import Webcam from 'react-webcam'
import {
  ParkingCircle, Bike, Car, Plus, RefreshCw, Upload, Save,
  Pencil, Eye, Trash2, X, Loader2, CheckCircle2, Video, VideoOff,
} from 'lucide-react'
import AdminLayout from '../../components/Layout/AdminLayout'
import { zoneApi } from '../../api/parking'
import { useScanStream } from '../../hooks/useScanStream'
import './ParkingManagement.css'

const CAT_OPTS = [
  { key: 'motorcycle', label: 'Motorcycle', Icon: Bike },
  { key: 'car',        label: 'Car',        Icon: Car  },
]

const DRAG_MIN    = 0.02
const OCCUPY_THR  = 4    // frames of vehicle inside before marking occupied
const FREE_THR    = 20   // frames of absence before marking free

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

// Centre of a normalized vehicle track bbox falls inside a parking space
function vehicleInSpace(track, sp) {
  if (sp.x1 == null) return false
  const cx = (track.bbox[0] + track.bbox[2]) / 2
  const cy = (track.bbox[1] + track.bbox[3]) / 2
  return cx >= sp.x1 && cx <= sp.x2 && cy >= sp.y1 && cy <= sp.y2
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
  const [cameraOn,     setCameraOn]     = useState(false)

  const svgEl     = useRef(null)
  const fileRef   = useRef(null)
  const dragStart = useRef(null)
  const dragging  = useRef(false)
  const rbRef     = useRef(null)
  const draftsRef = useRef([])
  // Hysteresis: positive = consecutive occupied frames, negative = consecutive free frames
  const hyst      = useRef({})
  const patching  = useRef(new Set())

  useEffect(() => { draftsRef.current = drafts }, [drafts])
  useEffect(() => { rbRef.current = rubberBand }, [rubberBand])

  const selZone = zones.find(z => z.id === selId) ?? null

  // ── Camera / scan stream ────────────────────────────────────────
  const token = localStorage.getItem('access_token') ?? ''
  const { videoRef: webcamRef, activeTracks } = useScanStream(
    token,
    cameraOn && mode === 'live',
  )

  // ── Parking overlap detection + hysteresis ──────────────────────
  useEffect(() => {
    if (!cameraOn || mode !== 'live' || !selZone) return
    const spaces = selZone.spaces.filter(s => s.x1 != null)

    spaces.forEach(sp => {
      const hit = activeTracks.some(t => vehicleInSpace(t, sp))
      const prev = hyst.current[sp.id] ?? 0

      if (hit) {
        const next = Math.min(prev + 1, OCCUPY_THR)
        hyst.current[sp.id] = next
        if (!sp.is_occupied && next >= OCCUPY_THR && !patching.current.has(sp.id)) {
          // Find best plate text from vehicles in this space
          const match  = activeTracks.find(t => vehicleInSpace(t, sp))
          const plate  = match?.plate_text || 'CAMERA'
          patching.current.add(sp.id)
          hyst.current[sp.id] = 0
          zoneApi.markOccupied(sp.id, plate)
            .then(u => setZones(p => p.map(z =>
              z.id === selId ? { ...z, spaces: z.spaces.map(s => s.id === u.id ? u : s) } : z
            )))
            .catch(() => {})
            .finally(() => patching.current.delete(sp.id))
        }
      } else {
        const next = Math.max(prev - 1, -FREE_THR)
        hyst.current[sp.id] = next
        if (sp.is_occupied && next <= -FREE_THR && !patching.current.has(sp.id)) {
          patching.current.add(sp.id)
          hyst.current[sp.id] = 0
          zoneApi.markFree(sp.id)
            .then(u => setZones(p => p.map(z =>
              z.id === selId ? { ...z, spaces: z.spaces.map(s => s.id === u.id ? u : s) } : z
            )))
            .catch(() => {})
            .finally(() => patching.current.delete(sp.id))
        }
      }
    })
  }, [activeTracks, cameraOn, mode, selId]) // eslint-disable-line react-hooks/exhaustive-deps

  // Reset hysteresis when zone changes
  useEffect(() => { hyst.current = {} }, [selId])

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

  // Live polling (when camera is off)
  const refreshZone = useCallback(async () => {
    if (!selId) return
    try {
      const z = await zoneApi.get(selId)
      setZones(p => p.map(x => x.id === z.id ? z : x))
    } catch { /* silent */ }
  }, [selId])

  useEffect(() => {
    if (mode !== 'live' || cameraOn) return
    const t = setInterval(refreshZone, 8000)
    return () => clearInterval(t)
  }, [mode, cameraOn, refreshZone])

  // Copy live spaces into drafts when entering edit mode
  useEffect(() => {
    if (mode === 'edit' && selZone) {
      setDrafts(selZone.spaces.map(s => ({ ...s, _id: s.id })))
      setSelDraft(null)
      setCameraOn(false)
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
    setCameraOn(false)
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

  // ── Space click (live mode, camera off) ────────────────────────
  const onSpaceClick = (sp) => {
    if (mode !== 'live' || cameraOn) return
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
                Draw space boxes in Edit Layout mode. Enable the camera to detect vehicles automatically —
                a space is marked occupied when a vehicle center falls inside its box.
              </p>
            </div>
          </div>
          <div className="pm-header-actions">
            <button className="pm-btn pm-btn--outline" onClick={loadZones} disabled={loading}>
              <RefreshCw size={14} className={loading ? 'pm-spin' : ''} /> Refresh
            </button>
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
                onClick={() => { setSelId(z.id); setMode('live'); setCameraOn(false) }}
              >
                <C size={13} /> {z.name}
              </button>
            )
          })}
          {!loading && zones.length === 0 && (
            <span className="pm-zone-empty">No zones yet — create one to start.</span>
          )}
        </div>

        {/* Main canvas area */}
        {!selZone ? (
          <div className="pm-canvas-placeholder">
            <ParkingCircle size={36} />
            <span>{loading ? 'Loading…' : 'Select or create a parking zone.'}</span>
          </div>
        ) : (
          <div className="pm-canvas-area">

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
                    {/* Camera toggle */}
                    <button
                      className={`pm-btn ${cameraOn ? 'pm-btn--camera-on' : 'pm-btn--outline'}`}
                      onClick={() => setCameraOn(v => !v)}
                    >
                      {cameraOn ? <><VideoOff size={13} /> Stop Camera</> : <><Video size={13} /> Use Camera</>}
                    </button>

                    <div className="pm-summary-row">
                      <span className="pm-sum-free">{sumFr} free</span>
                      <span className="pm-sum-sep">·</span>
                      <span className="pm-sum-occ">{occ} occupied</span>
                      <span className="pm-sum-sep">·</span>
                      <span className="pm-sum-total">{liveSpaces.length} total</span>
                    </div>

                    {cameraOn && (
                      <span className="pm-camera-badge">
                        <span className="pm-camera-dot" /> ML detection active
                      </span>
                    )}
                  </>
                )}

                {mode === 'edit' && (
                  <span className="pm-edit-hint">Click-drag on the image to draw a space box</span>
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
                <button className="pm-btn pm-btn--danger-outline" onClick={handleDeleteZone}>
                  <Trash2 size={13} /> Delete Zone
                </button>
              </div>
            </div>

            {/* Canvas */}
            <div className="pm-canvas-wrapper">
              {/* Background: camera feed OR reference image OR placeholder */}
              {cameraOn && mode === 'live' ? (
                <Webcam
                  ref={webcamRef}
                  className="pm-canvas-img"
                  screenshotFormat="image/jpeg"
                  screenshotQuality={0.85}
                  videoConstraints={{ width: 1280, height: 720, facingMode: 'environment' }}
                  mirrored={false}
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

                {/* Vehicle detection boxes (camera mode) */}
                {cameraOn && activeTracks.map(t => {
                  // bbox is [x1,y1,x2,y2] normalised 0-1
                  const [bx1, by1, bx2, by2] = t.bbox
                  const bx = Math.min(bx1, bx2), by = Math.min(by1, by2)
                  const bw = Math.abs(bx2 - bx1), bh = Math.abs(by2 - by1)
                  return (
                    <g key={t.track_id}>
                      <rect
                        x={bx} y={by} width={bw} height={bh}
                        fill="rgba(251,191,36,0.08)"
                        stroke="#FBBF24"
                        strokeWidth={0.003}
                        strokeDasharray="0.012 0.005"
                        rx={0.003}
                      />
                      {t.plate_text && (
                        <text
                          x={bx + bw/2} y={by - 0.012}
                          textAnchor="middle" dominantBaseline="middle"
                          fill="#FBBF24" fontSize={0.02} fontWeight="600"
                          style={{ paintOrder:'stroke', stroke:'rgba(0,0,0,0.7)', strokeWidth:'0.005' }}
                        >
                          {t.plate_text}
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
              {cameraOn && <span className="pm-legend-item"><span className="pm-legend-dot pm-legend-dot--det" />Detected vehicle</span>}
              <span className="pm-legend-note">
                {cameraOn
                  ? `ML auto-detects vehicles · ${OCCUPY_THR} frames to mark occupied · ${FREE_THR} frames to mark free`
                  : mode === 'live'
                    ? 'Click a space to toggle manually · auto-refreshes every 8 s'
                    : 'Click-drag to draw · click a box to rename or delete'}
              </span>
            </div>
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
    </AdminLayout>
  )
}
