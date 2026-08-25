import { useState, useEffect, useCallback, useMemo } from 'react'
import { useLiveUpdates } from '../../realtime/useLiveUpdates'
import {
  ParkingCircle, Bike, Car, RefreshCw,
  Shield, AlertTriangle, X, CheckCircle2, LayoutGrid,
  Camera, VideoOff, Maximize2, Minimize2,
} from 'lucide-react'
import notify, { toast } from '../../components/Feedback/notify'
import { fieldProblems } from '../../components/Feedback/formProblems'
import DoubleParkingAlerts from '../../components/DoubleParkingAlerts'
import { zoneApi } from '../../api/parking'
import { camerasApi } from '../../api/cameras'
import { useCameraContext } from '../../context/CameraContext'
import useFullscreen from '../../hooks/useFullscreen'
import { overrideEntry } from '../../api/scanning'
import { createViolation } from '../../api/violations'
import '../Admin/ParkingManagement.css'

const CAT_OPTS = [
  { key: 'motorcycle', label: 'Motorcycle', Icon: Bike },
  { key: 'car',        label: 'Car',        Icon: Car  },
]

// ─── Parking Override Modal ───────────────────────────────────────────────────
function ParkingOverrideModal({ zoneName, onClose, onDone }) {
  const [plate,   setPlate]   = useState('')
  const [reason,  setReason]  = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    const problems = [...fieldProblems(e.currentTarget)]
    if (!plate.trim()) problems.push('Enter the plate number.')
    if (!reason.trim()) problems.push('Give a reason for the override.')
    if (await notify.validation(problems, { title: 'Override not logged' })) return
    setLoading(true)
    try {
      await overrideEntry({ plate_number: plate.trim().toUpperCase(), reason: `Parking override — ${zoneName}: ${reason}` })
      toast.success(`Parking override logged for ${plate.toUpperCase()}.`)
      onDone()
      onClose()
    } catch {
      toast.error('Override failed.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div style={{ background: '#fff', borderRadius: 14, width: 360, boxShadow: '0 20px 60px rgba(0,0,0,0.3)', overflow: 'hidden' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 18px', borderBottom: '1px solid #D3E1EC', background: '#FEF9E4' }}>
          <span style={{ fontWeight: 700, fontSize: 14, color: '#7A5C00', display: 'flex', alignItems: 'center', gap: 6 }}>
            <Shield size={15} /> Parking Override
          </span>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#5C7B92' }}><X size={15} /></button>
        </div>
        <form onSubmit={handleSubmit} noValidate style={{ padding: 18 }}>
          <p style={{ margin: '0 0 12px', fontSize: 12, color: '#8A6B00', background: '#FDF0BE', border: '1px solid #F7E08A', borderRadius: 6, padding: '6px 10px' }}>
            Allow a vehicle to park in <strong>{zoneName}</strong> even if the zone is full. This will be logged.
          </p>
          <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: '#2E4C63', marginBottom: 4 }}>License Plate</label>
          <input
            value={plate} onChange={e => setPlate(e.target.value)}
            placeholder="e.g. ABC 123"
            style={{ width: '100%', padding: '7px 10px', border: '1.5px solid #BDD4E5', borderRadius: 7, fontSize: 13, marginBottom: 10, boxSizing: 'border-box' }}
            required
          />
          <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: '#2E4C63', marginBottom: 4 }}>Reason</label>
          <textarea
            value={reason} onChange={e => setReason(e.target.value)}
            placeholder="e.g. Event day, special clearance…"
            rows={2}
            style={{ width: '100%', padding: '7px 10px', border: '1.5px solid #BDD4E5', borderRadius: 7, fontSize: 13, resize: 'vertical', boxSizing: 'border-box' }}
            required
          />
          <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
            <button type="button" onClick={onClose} style={{ flex: 1, padding: '8px', borderRadius: 7, border: '1.5px solid #BDD4E5', background: '#fff', cursor: 'pointer', fontSize: 13 }}>Cancel</button>
            <button type="submit" disabled={loading} style={{ flex: 1, padding: '8px', borderRadius: 7, border: 'none', background: '#8A6B00', color: '#fff', cursor: 'pointer', fontSize: 13, fontWeight: 700 }}>
              {loading ? 'Logging…' : 'Confirm Override'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ─── Issue Violation Modal ────────────────────────────────────────────────────
function IssueViolationModal({ onClose }) {
  const [plate, setPlate]   = useState('')
  const [type, setType]     = useState('no_sticker')
  const [notes, setNotes]   = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    const problems = [...fieldProblems(e.currentTarget)]
    if (!plate.trim()) problems.push('Enter the plate number.')
    if (await notify.validation(problems, { title: 'Violation not issued' })) return
    setLoading(true)
    try {
      await createViolation({ plate_number: plate.trim().toUpperCase(), violation_type: type, notes })
      toast.success(`Violation issued for ${plate.trim().toUpperCase()}.`)
      onClose()
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Failed to issue violation.')
    } finally { setLoading(false) }
  }

  return (
    <div
      style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div style={{ background: '#fff', borderRadius: 14, width: '100%', maxWidth: 400, boxShadow: '0 20px 60px rgba(0,0,0,0.25)', overflow: 'hidden' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 18px', borderBottom: '1px solid #D3E1EC', background: '#FCEDED' }}>
          <span style={{ fontWeight: 700, fontSize: 14, color: '#841818', display: 'flex', alignItems: 'center', gap: 6 }}>
            <AlertTriangle size={15} /> Issue Violation
          </span>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#5C7B92' }}><X size={15} /></button>
        </div>
        <form onSubmit={handleSubmit} noValidate style={{ padding: 18, display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: '#2E4C63', marginBottom: 4 }}>License Plate *</label>
            <input value={plate} onChange={e => setPlate(e.target.value)} placeholder="e.g. ABC 123" required
              style={{ width: '100%', padding: '7px 10px', border: '1.5px solid #BDD4E5', borderRadius: 7, fontSize: 13, boxSizing: 'border-box', fontFamily: 'inherit', textTransform: 'uppercase' }} />
          </div>
          <div>
            <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: '#2E4C63', marginBottom: 4 }}>Violation Type</label>
            <select value={type} onChange={e => setType(e.target.value)}
              style={{ width: '100%', padding: '7px 10px', border: '1.5px solid #BDD4E5', borderRadius: 7, fontSize: 13, boxSizing: 'border-box', fontFamily: 'inherit', background: '#fff' }}>
              <option value="no_sticker">No Sticker</option>
              <option value="expired_registration">Expired Registration</option>
              <option value="unauthorized">Unauthorized Entry</option>
              <option value="other">Other</option>
            </select>
          </div>
          <div>
            <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: '#2E4C63', marginBottom: 4 }}>Notes</label>
            <textarea value={notes} onChange={e => setNotes(e.target.value)} rows={3} placeholder="Optional additional details…"
              style={{ width: '100%', padding: '7px 10px', border: '1.5px solid #BDD4E5', borderRadius: 7, fontSize: 13, resize: 'vertical', boxSizing: 'border-box', fontFamily: 'inherit' }} />
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button type="button" onClick={onClose} style={{ flex: 1, padding: '8px', borderRadius: 7, border: '1.5px solid #BDD4E5', background: '#fff', cursor: 'pointer', fontSize: 13 }}>Cancel</button>
            <button type="submit" disabled={loading} style={{ flex: 1, padding: '8px', borderRadius: 7, border: 'none', background: '#C62828', color: '#fff', cursor: 'pointer', fontSize: 13, fontWeight: 700 }}>
              {loading ? 'Issuing…' : 'Issue Violation'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

export default function SecurityParkingView() {
  const [zones,         setZones]         = useState([])
  const [selId,         setSelId]         = useState(null)
  const [loading,       setLoading]       = useState(true)
  const [showOverride,  setShowOverride]  = useState(false)
  const [showViolation, setShowViolation] = useState(false)
  // Device Management camera rows, and which zones the detector is running for.
  const [deviceCams,    setDeviceCams]    = useState([])
  const [camStatus,     setCamStatus]     = useState({})
  // The reference image URL is signed and expires, so "it loaded an hour ago"
  // is no guarantee it loads now. Keyed by zone id: one zone's dead link must
  // not blank out the next zone's picture.
  const [imgFailedFor,  setImgFailedFor]  = useState(null)
  const [imgDimsFor,    setImgDimsFor]    = useState({})

  const { cameras: allCameras, addCamera, registerCanvas, paneCounts } = useCameraContext()
  const camFs = useFullscreen()

  const selZone    = zones.find(z => z.id === selId) ?? null
  const camRunning = !!camStatus[selId]

  // ── Load zones ──────────────────────────────────────────────────
  const loadZones = useCallback(async () => {
    setLoading(true)
    try {
      const data = await zoneApi.listAll()
      setZones(data)
      setSelId(id => id ?? data[0]?.id ?? null)
      // Refetched with the zones, not once on mount: a zone reassigned to a
      // different camera while a guard has this page open would otherwise keep
      // drawing bays over the old camera's picture.
      try {
        setDeviceCams(await camerasApi.list({ assignment: 'parking' }))
      } catch { /* keep the cameras already known */ }
    } finally { setLoading(false) }
  }, [])

  useEffect(() => { loadZones() }, [loadZones])

  // Live-refresh zones/occupancy on parking changes
  useLiveUpdates(loadZones, ['parkingzone', 'parkingspace'])

  // ── Live occupancy polling ──────────────────────────────────────
  const refreshZone = useCallback(async () => {
    if (!selId) return
    try {
      const z = await zoneApi.get(selId)
      setZones(p => p.map(x => x.id === z.id ? z : x))
    } catch { /* silent */ }
  }, [selId])

  useEffect(() => {
    const t = setInterval(refreshZone, 8000)
    return () => clearInterval(t)
  }, [refreshZone])

  // Whether the bay detector is running per zone. A GET, so guards may read it;
  // starting and stopping it stays admin-only.
  useEffect(() => {
    const pull = async () => {
      try { setCamStatus(await zoneApi.getCameraStatus()) } catch { /* silent */ }
    }
    pull()
    const t = setInterval(pull, 8000)
    return () => clearInterval(t)
  }, [])

  // ── Derived ─────────────────────────────────────────────────────
  // Two different questions, two different sources.
  //
  // Capacity/occupancy comes from the gate ledger and is per *category* — a
  // vehicle takes a slot when a guard scans it in and gives it back when one
  // scans it out. That is what decides whether another car can be let in.
  //
  // The bay numbers come from the camera and describe this zone's map only:
  // which specific slots are taken and whether anyone is parked across a line.
  const liveSpaces   = selZone?.spaces ?? []
  const baysOccupied = selZone?.bays_occupied ?? liveSpaces.filter(s => s.is_occupied).length
  const bayTotal     = liveSpaces.length
  const totalCap     = selZone?.category_capacity  ?? 0
  const occ          = selZone?.category_occupied  ?? 0
  const isFull       = selZone?.category_is_full   ?? false
  const sumFr        = selZone?.category_available ?? Math.max(0, totalCap - occ)
  const catLabel     = selZone?.vehicle_category === 'motorcycle' ? 'Motorcycle' : 'Car'

  // ── Which camera is this zone watched by? ───────────────────────
  //
  // Same three-way join the admin page does, minus the camera picker: a guard
  // does not choose a feed, the zone does. `zone.camera` is a Device Management
  // row id, while CameraContext keys its live feeds by a client-side counter,
  // so the RTSP URL is the only field the two share and it is the join key.
  // Getting this wrong would put camera B's picture under camera A's bays with
  // nothing on screen saying so.
  const deviceById = useMemo(
    () => new Map(deviceCams.map(d => [d.id, d])),
    [deviceCams],
  )
  const liveByUrl = useMemo(() => {
    const m = new Map()
    for (const c of allCameras) {
      if (c.assignment !== 'parking') continue
      const url = (c.url || '').trim()
      if (!m.has(url)) m.set(url, c)
    }
    return m
  }, [allCameras])

  const zoneDeviceCam = selZone?.camera != null ? deviceById.get(selZone.camera) ?? null : null
  const zoneCam       = zoneDeviceCam ? liveByUrl.get((zoneDeviceCam.rtsp_url || '').trim()) ?? null : null

  // Connect only the selected zone's camera, not every parking camera on
  // campus. Guards live on the entries screen; opening a stream per zone here
  // would multiply RTSP sessions on hardware that already reboots under load.
  // addCamera dedups by URL, so revisiting a zone reuses the open connection.
  const zoneRtsp = (zoneDeviceCam?.rtsp_url || '').trim()
  const zoneCamName = zoneDeviceCam?.name || selZone?.camera_name || ''
  useEffect(() => {
    if (zoneRtsp) addCamera(zoneCamName, zoneRtsp, 'parking')
  }, [zoneRtsp, zoneCamName, addCamera])

  // Same lens rule as the admin editor and lens_layout.lens_count() on the
  // backend. The live frame is the better witness — measured by the render loop
  // from the picture actually arriving — and it exists even for a zone that
  // never captured a reference image.
  const imgFailed  = !!selZone && imgFailedFor === selZone.id
  const imgDims    = selZone ? (imgDimsFor[selZone.id] ?? null) : null
  const livePanes  = zoneCam ? (paneCounts[zoneCam.id] ?? 1) : 1
  const lensCount  = (() => {
    if (livePanes > 1) return livePanes
    if (!imgDims) return 1
    const { w, h } = imgDims
    if (!w || !h || h <= w) return 1
    return w / (h / 2) >= 1.6 ? 2 : 1
  })()
  // Read-only: the zone already knows which view it covers, so there is nothing
  // to ask the guard. Bay geometry stays full-frame — the lens is a viewport,
  // applied by narrowing the SVG viewBox, never by rewriting a coordinate.
  const lensIdx    = lensCount > 1 ? (selZone?.lens_index ?? 0) : 0

  // Slots belong to one view of the camera, same filter the admin editor
  // applies. The viewBox already clips anything outside the band, but a bay
  // drawn before the lens split existed is tagged lens 0 with geometry spanning
  // the whole stacked frame — that one would survive the clip and land
  // somewhere plausible and wrong on the other lens's picture.
  // Stats deliberately keep counting every lens: bays_occupied is the zone's
  // map, not this viewport's.
  const spaceList = lensCount > 1
    ? liveSpaces.filter(s => (s.lens_index ?? 0) === lensIdx)
    : liveSpaces

  return (
    <>
      <div className="pm-page">

        {/* Title is screen-reader only, matching the admin page — the sidebar
            already names the page and the band it occupied is better spent on
            the numbers a guard actually reads. */}
        <h1 className="pm-sr-only">Parking Overview</h1>

        {/* Guards see the same live alert as admin — spotting a car
            across two bays is exactly their job. */}
        <DoubleParkingAlerts zoneId={selId} canAttribute />

        {/* Occupancy at a glance — same stat cards as the admin page. These
            were a run of tiny text inside the toolbar. */}
        {selZone && (
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
                <p className="pm-stat-val">{totalCap}</p>
                <p className="pm-stat-lbl">Capacity</p>
              </div>
            </div>
            <div className="pm-stat-card">
              <div className={`pm-stat-icon ${isFull ? 'red' : 'purple'}`}>
                {isFull ? <AlertTriangle size={18} /> : <LayoutGrid size={18} />}
              </div>
              <div>
                <p className="pm-stat-val">{isFull ? 'FULL' : `${baysOccupied}/${bayTotal}`}</p>
                <p className="pm-stat-lbl">{isFull ? `${catLabel} Parking` : 'Bays Taken'}</p>
              </div>
            </div>
          </div>
        )}

        {/* Says out loud where each number comes from. The counts above are
            campus-wide for this vehicle category and move on gate scans; the
            map below is this zone's camera reading. They are not expected to
            match, and a guard who thinks they should would distrust both. */}
        {selZone && (
          <p className="pm-stat-caption">
            Free / Occupied / Capacity count <strong>{catLabel.toLowerCase()}s on campus</strong> from
            gate entry and exit scans. <strong>Bays Taken</strong> is what the camera sees in {selZone.name}.
          </p>
        )}

        {/* Zone bar — labelled tabs left, actions right */}
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
                  onClick={() => setSelId(z.id)}
                >
                  <C size={13} /> {z.name}
                </button>
              )
            })}
            {!loading && zones.length === 0 && (
              <span className="pm-zone-empty">No parking zones configured yet.</span>
            )}
          </div>
          <div className="pm-zone-bar-actions">
            <button className="pm-btn pm-btn--outline" onClick={loadZones} disabled={loading}>
              <RefreshCw size={14} className={loading ? 'pm-spin' : ''} /> Refresh
            </button>
          </div>
        </div>

        {/* Main content */}
        {!selZone ? (
          <div className="pm-canvas-placeholder">
            <ParkingCircle size={36} />
            <span>{loading ? 'Loading…' : 'Select a parking zone.'}</span>
          </div>
        ) : (
          <div className="pm-content-row">
            <div className="pm-canvas-area" style={{ flex: 1, minWidth: 0 }}>

              {/* Toolbar — view-only summary */}
              <div className="pm-toolbar">
                <div className="pm-toolbar-left" style={{ flexWrap: 'wrap', gap: 8 }}>
                  {/* Counts moved up to the stat cards */}
                  {selZone?.capacity_override != null && (
                    <span style={{ fontSize: 11, color: '#7A5C00', background: '#FDF0BE', border: '1px solid #F7E08A', padding: '2px 8px', borderRadius: 20, fontWeight: 600 }}>
                      event capacity override
                    </span>
                  )}
                  {camRunning && (
                    <span className="pm-camera-badge">
                      <span className="pm-camera-dot" /> Camera active
                    </span>
                  )}
                </div>
                <div style={{ display: 'flex', gap: 6 }}>
                  <button
                    onClick={() => setShowViolation(true)}
                    style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '5px 12px', borderRadius: 7, border: 'none', background: '#C62828', color: '#fff', fontSize: 12, fontWeight: 700, cursor: 'pointer' }}
                    title="Issue a violation to a vehicle"
                  >
                    <AlertTriangle size={13} /> Issue Violation
                  </button>
                  <button
                    onClick={() => setShowOverride(true)}
                    style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '5px 12px', borderRadius: 7, border: 'none', background: '#8A6B00', color: '#fff', fontSize: 12, fontWeight: 700, cursor: 'pointer' }}
                    title="Allow a vehicle to park regardless of zone capacity"
                  >
                    <Shield size={13} /> Override Parking
                  </button>
                </div>
              </div>

              {/* Canvas.
                  Bays are drawn over the *live feed*, the same picture the
                  admin's Live View shows. The still reference image is only the
                  fallback for a zone with no camera, or one whose feed has not
                  arrived yet — a guard deciding whether a bay is really free
                  needs the car, not a photo of the car park taken last term. */}
              <div className="pm-canvas-wrapper" ref={camFs.setRef('parking')}>
                {zoneCam && (
                  <button
                    className="pm-cam-fs"
                    onClick={async () => {
                      if (!(await camFs.toggle('parking'))) toast.error('Fullscreen was blocked by the browser.')
                    }}
                    title={camFs.isFullscreen('parking') ? 'Exit fullscreen' : 'Fullscreen'}
                    aria-label={camFs.isFullscreen('parking') ? 'Exit fullscreen' : 'Fullscreen'}
                  >
                    {camFs.isFullscreen('parking') ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
                  </button>
                )}

                {zoneCam ? (
                  <canvas
                    className="pm-canvas-live"
                    /* Always an explicit pane, never undefined — `pane == null`
                       is the FULL_FRAME key. With one lens the render loop
                       draws pane 0 and the whole frame identically, so this
                       costs nothing and keeps a stacked dual-lens camera from
                       showing both scenes squeezed into one box. */
                    ref={el => registerCanvas(zoneCam.id, el, lensIdx)}
                  />
                ) : selZone.reference_image_url && !imgFailed ? (
                  <img
                    src={selZone.reference_image_url}
                    className="pm-canvas-img"
                    draggable={false}
                    alt=""
                    /* Stretch to lensCount x height and slide the wanted band
                       into the wrapper, which clips — matching the viewBox. */
                    style={lensCount > 1 ? {
                      height: `${lensCount * 100}%`,
                      top: `${-lensIdx * 100}%`,
                      bottom: 'auto',
                    } : undefined}
                    onError={() => setImgFailedFor(selZone.id)}
                    onLoad={e => {
                      setImgFailedFor(f => (f === selZone.id ? null : f))
                      setImgDimsFor(m => ({ ...m, [selZone.id]: { w: e.target.naturalWidth, h: e.target.naturalHeight } }))
                    }}
                  />
                ) : (
                  /* The signed image URL expires, so a page left open overnight
                     comes back to a dead link. Without this that renders as a
                     broken-image glyph with no hint that Refresh fixes it. */
                  <div className="pm-canvas-no-img">
                    {imgFailed ? (
                      <>
                        <AlertTriangle size={26} />
                        <p className="pm-canvas-no-img-title">Reference image could not be loaded</p>
                        <p className="pm-canvas-no-img-sub">The link may have expired. Refresh to get a fresh one.</p>
                        <button className="pm-btn pm-btn--outline" onClick={loadZones}>
                          <RefreshCw size={13} /> Refresh
                        </button>
                      </>
                    ) : zoneDeviceCam ? (
                      <>
                        <VideoOff size={26} />
                        <p className="pm-canvas-no-img-title">Connecting to {zoneDeviceCam.name}…</p>
                        <p className="pm-canvas-no-img-sub">
                          The bay numbers above are live — only the picture is still loading.
                        </p>
                      </>
                    ) : (
                      <>
                        <Camera size={26} />
                        <p className="pm-canvas-no-img-title">No camera on this zone</p>
                        <p className="pm-canvas-no-img-sub">
                          Ask an administrator to assign a camera to {selZone.name}.
                        </p>
                      </>
                    )}
                  </div>
                )}

                {/* SVG overlay — read-only (no event handlers).
                    One band of the full frame: every bay is stored in
                    full-frame coordinates, so narrowing the viewBox is all it
                    takes to show a single lens. */}
                <svg
                  className="pm-canvas-svg"
                  viewBox={lensCount > 1 ? `0 ${lensIdx / lensCount} 1 ${1 / lensCount}` : '0 0 1 1'}
                  preserveAspectRatio="none"
                >
                  {spaceList.map(s => {
                    const x     = Math.min(s.x1, s.x2), y = Math.min(s.y1, s.y2)
                    const w     = Math.abs(s.x2 - s.x1), h = Math.abs(s.y2 - s.y1)
                    const color = s.is_occupied ? '#D93B3B' : '#1BA968'
                    const fill  = s.is_occupied ? 'rgba(217, 59, 59,0.3)' : 'rgba(27, 169, 104,0.25)'
                    return (
                      <g key={s.id}>
                        <rect
                          x={x} y={y} width={w} height={h}
                          fill={fill} stroke={color} strokeWidth={0.003} rx={0.004}
                        />
                        <text
                          x={x + w / 2}
                          y={y + h / 2 - (s.is_occupied && s.occupied_by ? 0.013 : 0)}
                          textAnchor="middle" dominantBaseline="middle"
                          fill="#fff" fontSize={0.028} fontWeight="bold"
                          style={{ paintOrder: 'stroke', stroke: 'rgba(0,0,0,0.55)', strokeWidth: '0.005' }}
                        >
                          {s.space_number}
                        </text>
                        {s.is_occupied && s.occupied_by && (
                          <text
                            x={x + w / 2} y={y + h / 2 + 0.023}
                            textAnchor="middle" dominantBaseline="middle"
                            fill="#F3C0C0" fontSize={0.02} fontWeight="600"
                            style={{ paintOrder: 'stroke', stroke: 'rgba(0,0,0,0.5)', strokeWidth: '0.004' }}
                          >
                            {s.occupied_by}
                          </text>
                        )}
                      </g>
                    )
                  })}
                </svg>
              </div>

              {/* Legend */}
              <div className="pm-legend">
                <span className="pm-legend-item">
                  <span className="pm-legend-dot pm-legend-dot--free" />Free
                </span>
                <span className="pm-legend-item">
                  <span className="pm-legend-dot pm-legend-dot--occ" />Occupied
                </span>
                {/* Two different rates, and saying so stops a guard reading a
                    stale bay colour as a dead feed. The picture streams; the
                    bay verdicts come from the 8-second occupancy poll. */}
                <span className="pm-legend-note">
                  {zoneCam ? 'Live picture · bays refresh every 8 s' : 'Auto-refreshes every 8 s'}
                </span>
              </div>

            </div>{/* /pm-canvas-area */}

          </div>
        )}

      </div>

      {showOverride && selZone && (
        <ParkingOverrideModal
          zoneName={selZone.name}
          onClose={() => setShowOverride(false)}
          onDone={() => { setShowOverride(false); refreshZone() }}
        />
      )}

      {showViolation && (
        <IssueViolationModal onClose={() => setShowViolation(false)} />
      )}

    </>
  )
}
