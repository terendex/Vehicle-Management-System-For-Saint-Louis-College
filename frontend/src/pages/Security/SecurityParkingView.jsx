import { useState, useEffect, useCallback, useMemo } from 'react'
import { useLiveUpdates } from '../../realtime/useLiveUpdates'
import {
  ParkingCircle, Bike, Car, RefreshCw,
  Shield, AlertTriangle, X, CheckCircle2, LayoutGrid,
  Camera, VideoOff, Maximize2, Minimize2, CalendarDays,
} from 'lucide-react'
import notify, { toast } from '../../components/Feedback/notify'
import { fieldProblems } from '../../components/Feedback/formProblems'
import DoubleParkingAlerts from '../../components/DoubleParkingAlerts'
import BayOccupantModal from '../../components/BayOccupant'
import { zoneApi } from '../../api/parking'
import { camerasApi } from '../../api/cameras'
import { useCameraContext } from '../../context/CameraContext'
import useFullscreen from '../../hooks/useFullscreen'
import { overrideEntry } from '../../api/scanning'
import { createViolation } from '../../api/violations'
import ConfiscatedAccounts from '../../components/ConfiscatedAccounts'
import { feedState, FEED_DOT } from '../../utils/feedState'
import '../Admin/ParkingManagement.css'
import '../../styles/camera-monitor.css'
import './SecurityParkingView.css'

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
  // Which occupied bay the guard asked about, if any.
  const [bayLookup,     setBayLookup]     = useState(null)
  // What the detector last saw in the selected zone. Occupancy is a verdict;
  // these are the boxes behind it, so a bay staying green can be told apart
  // from a detector seeing nothing at all.
  const [detections,    setDetections]    = useState(null)
  const [zones,         setZones]         = useState([])
  const [selId,         setSelId]         = useState(null)
  const [loading,       setLoading]       = useState(true)
  const [showOverride,  setShowOverride]  = useState(false)
  const [showViolation, setShowViolation] = useState(false)
  // Device Management camera rows, and which zones the detector is running for.
  const [deviceCams,    setDeviceCams]    = useState([])
  const [camStatus,     setCamStatus]     = useState({})
  // Whether the camera list has been asked for at least once. Until it has, a
  // zone whose camera is not in the list yet is loading, not missing.
  const [camsLoaded,    setCamsLoaded]    = useState(false)

  const { cameras: allCameras, addCamera, syncCameras, registerCanvas, paneCounts } = useCameraContext()
  const camFs = useFullscreen()

  const selZone    = zones.find(z => z.id === selId) ?? null
  const camRunning = !!camStatus[selId]
  // Bays are only watched once the zone has an empty baseline to compare against.
  const monitored  = camRunning && !!selZone?.has_baseline

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
      setCamsLoaded(true)
    } finally { setLoading(false) }
  }, [])

  useEffect(() => { loadZones() }, [loadZones])

  // Polled at the rate the worker re-detects (DETECT_INTERVAL_SECONDS = 2).
  // Faster would redraw the same rectangles; slower would lag the picture.
  useEffect(() => {
    if (!selId) return undefined
    let alive = true
    const tick = () => zoneApi.getDetections()
      .then(all => { if (alive) setDetections(all?.[selId] ?? null) })
      .catch(() => { if (alive) setDetections(null) })
    tick()
    const timer = setInterval(tick, 2000)
    return () => { alive = false; clearInterval(timer) }
  }, [selId])

  // Camera changes reach this screen too. Pruning only: the effect below opens
  // just the selected zone's feed on purpose, so this must not connect every
  // parking camera on campus — it exists so a camera deleted in Device
  // Management stops playing here rather than streaming on unattached.
  const refreshCameras = useCallback(() => {
    camerasApi.list({ assignment: 'parking' })
      .then(cams => {
        setDeviceCams(cams)
        syncCameras('parking', cams, { connect: false })
      })
      .catch(() => {})
  }, [syncCameras])

  // Deleting a camera nulls ParkingZone.camera through the collector's bulk
  // UPDATE, which fires no parkingzone signal — so the zones have to be
  // refetched from the *camera* event or they go on pointing at a device that
  // no longer exists.
  const onCameraChange = useCallback(() => { refreshCameras(); loadZones() },
                                     [refreshCameras, loadZones])
  useLiveUpdates(onCameraChange, 'camera')

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
  // Free / parked / capacity are per *category*, across every zone of that
  // kind: parked is the bays the cameras read as taken, free is what that
  // leaves. On campus is the gate ledger — vehicles scanned in and not yet out,
  // parked or not — shown beside them and never subtracted from capacity.
  //
  // The bay numbers in the second panel describe this zone's map only.
  const liveSpaces   = selZone?.spaces ?? []
  const baysOccupied = selZone?.bays_occupied ?? liveSpaces.filter(s => s.is_occupied).length
  const bayTotal     = liveSpaces.length
  const totalCap     = selZone?.category_capacity  ?? 0
  const occ          = selZone?.category_occupied  ?? 0
  const onCampus     = selZone?.category_on_campus ?? 0
  const unmonitored  = selZone?.category_unmonitored ?? 0
  const isFull       = selZone?.category_is_full   ?? false
  const sumFr        = selZone?.category_available ?? Math.max(0, totalCap - occ)
  const catLabel     = selZone?.vehicle_category === 'motorcycle' ? 'Motorcycle' : 'Car'
  // Bays an event under way has spoken for. Already taken out of `sumFr`, so
  // without naming it here the free count simply drops and the guard is left
  // to decide whether the number is wrong.
  const reserved     = selZone?.category_reserved ?? 0
  const capEvent     = selZone?.category_event ?? null

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

  // Camera → the zones drawn against it. A dual-lens unit watches two places,
  // so this is a list, not a single zone.
  const zonesByCamera = useMemo(() => {
    const m = new Map()
    for (const z of zones) {
      if (z.camera == null) continue
      if (!m.has(z.camera)) m.set(z.camera, [])
      m.get(z.camera).push(z)
    }
    return m
  }, [zones])
  // Not filtered by assignment: CameraContext dedups feeds by URL, so a camera
  // another screen opened first keeps that screen's assignment, and filtering
  // it out here left the zone with "no feed" for as long as it stayed open.
  const liveByUrl = useMemo(() => {
    const m = new Map()
    for (const c of allCameras) {
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

  // How many views the live frame carries, measured by the render loop from
  // the picture actually arriving. The bays are only drawn over a live picture,
  // so there is no still to measure instead.
  const lensCount  = zoneCam ? (paneCounts[zoneCam.id] ?? 1) : 1
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

  // What the stage shows. Never the zone's reference photo.
  //
  // It used to fall back to that still whenever the live feed was not matched
  // yet — while the camera list was loading, while the stream was connecting,
  // and for good when the camera was down. The still is the picture the bays
  // were drawn on, taken whenever an admin set the zone up, and with the bay
  // colours painted over it it reads as the car park right now: a guard would
  // be judging today's bays off a photo of some other day's cars. No live
  // picture has to look like no live picture.
  const stage = (() => {
    if (!selZone) return loading ? 'loading' : 'no-zone'
    if (selZone.camera == null) return 'no-camera'
    if (!zoneDeviceCam) return camsLoaded ? 'missing' : 'loading'
    if (!zoneRtsp.startsWith('rtsp://')) return 'missing'
    const st = feedState(zoneCam)
    // addCamera runs in an effect, so the render before it has no feed yet.
    return st === 'none' ? 'connecting' : st
  })()
  const isLive = stage === 'live'
  const [pillCls, pillText] = {
    live:        ['live',       'Live'],
    connecting:  ['connecting', 'Connecting…'],
    loading:     ['connecting', 'Loading…'],
    offline:     ['offline',    'Offline'],
    missing:     ['offline',    'Unavailable'],
    'no-camera': ['none',       'No camera'],
    'no-zone':   ['none',       'No zone'],
  }[stage]
  const zoneMsg = (zoneCam?.statusMsg || '').trim()
  const zoneDetail = zoneMsg && !/^connecting…?$/i.test(zoneMsg) ? zoneMsg : ''
  const camName = zoneCamName || 'the camera'
  // Occupancy as a share of capacity, for the bar. Held bays are drawn after
  // the occupied ones so the bar's empty part is exactly the free count.
  const pct = (n) => (totalCap > 0 ? Math.min(100, (n / totalCap) * 100) : 0)

  return (
    <>
      <div className="cm-page pm-guard">

        {/* Title is screen-reader only — the sidebar already names the page and
            the band it occupied is better spent on the numbers a guard reads. */}
        <h1 className="pm-sr-only">Parking Overview</h1>

        {/* Guards see the same live alert as admin — spotting a car across two
            bays is exactly their job. Full width, above everything: it is the
            one thing on this screen that needs acting on now. */}
        <DoubleParkingAlerts zoneId={selId} canAttribute />

        <div className="cm-layout">

          {/* Left: the zone's live camera with its bays drawn over it. */}
          <section className="cm-card">
            <div className="cm-head">
              <span className="cm-title"><ParkingCircle size={15} /> Parking Monitor</span>
              {selZone && (
                <span className="cm-head-note">
                  {selZone.name}{zoneCamName ? ` · ${zoneCamName}` : ''}
                </span>
              )}
              <div className="cm-head-end">
                <span className={`cm-pill ${pillCls}`}>
                  <span className="cm-pill-dot" /> {pillText}
                </span>
                <button
                  type="button"
                  className="cm-icon-btn"
                  onClick={loadZones}
                  disabled={loading}
                  title="Refresh"
                  aria-label="Refresh parking zones"
                >
                  <RefreshCw size={13} className={loading ? 'pm-spin' : ''} />
                </button>
              </div>
            </div>

            {/* Zones, and — once there is more than one — cameras.
                A guard thinks in cameras ("the one over the north lot") while
                the bays belong to zones. Choosing a camera jumps to the zone
                drawn against it; the zone chips stay for the dual-lens case,
                where one camera carries two zones. */}
            {(zones.length > 0 || !loading) && (
              <div className="cm-toolbar">
                <span className="cm-toolbar-label"><LayoutGrid size={12} /> Zones</span>
                <div className="cm-chips">
                  {zones.map(z => {
                    const C = CAT_OPTS.find(c => c.key === z.vehicle_category)?.Icon ?? ParkingCircle
                    return (
                      <button
                        key={z.id}
                        type="button"
                        className={`cm-chip${z.id === selId ? ' active' : ''}`}
                        onClick={() => setSelId(z.id)}
                        aria-pressed={z.id === selId}
                      >
                        <C size={13} /> {z.name}
                      </button>
                    )
                  })}
                  {!loading && zones.length === 0 && (
                    <span className="pm-zone-empty">No parking zones configured yet.</span>
                  )}
                </div>
                {deviceCams.length > 1 && (
                  <>
                    <span className="cm-toolbar-label pm-guard-cams-label"><Camera size={12} /> Cameras</span>
                    <div className="cm-chips">
                      {deviceCams.map(dev => {
                        const zonesHere = zonesByCamera.get(dev.id) ?? []
                        const st        = feedState(liveByUrl.get((dev.rtsp_url || '').trim()))
                        const active    = zonesHere.some(z => z.id === selId)
                        return (
                          <button
                            key={`cam-${dev.id}`}
                            type="button"
                            className={`cm-chip${active ? ' active' : ''}`}
                            onClick={() => zonesHere[0] && setSelId(zonesHere[0].id)}
                            disabled={!zonesHere.length}
                            aria-pressed={active}
                            title={zonesHere.length
                              ? `${dev.name} — ${zonesHere.map(z => z.name).join(', ')}`
                              : `${dev.name} — no zone drawn for it yet`}
                          >
                            <span className={`cm-dot ${FEED_DOT[st]}`} />
                            {dev.name}
                            {!zonesHere.length && <span className="cm-pick-sub">No zone</span>}
                          </button>
                        )
                      })}
                    </div>
                  </>
                )}
              </div>
            )}

            {/* The standard stage — same size as the entry screen and the
                Operations Center. Bays are drawn over the live feed only. */}
            <div className="cm-well" ref={camFs.setRef('parking')}>
              <div className="cm-stage">
                {zoneCam && (
                  <canvas
                    key={`${zoneCam.id}:${lensIdx}`}
                    className="cm-canvas pm-guard-canvas"
                    /* Always an explicit pane, never undefined — `pane == null`
                       is the FULL_FRAME key. With one lens the render loop
                       draws pane 0 and the whole frame identically, so this
                       costs nothing and keeps a stacked dual-lens camera from
                       showing both scenes squeezed into one box. */
                    ref={el => registerCanvas(zoneCam.id, el, lensIdx)}
                  />
                )}

                {/* SVG overlay, live picture only — bays over a spinner or a
                    blank stage would claim a reading there is no picture for.
                    Bays are not editable here (the geometry belongs to Parking
                    Management), but an occupied one answers who is in it.
                    Every bay is stored in full-frame coordinates, so narrowing
                    the viewBox is all it takes to show a single lens. */}
                {isLive && (
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
                      const known = s.is_occupied && !!s.occupied_by
                      return (
                        <g
                          key={s.id}
                          onClick={known ? () => setBayLookup(s) : undefined}
                          style={known ? { cursor: 'pointer' } : undefined}
                        >
                          {known && <title>{`${s.occupied_by} — click for details`}</title>}
                          {/* Draw the bay the shape it was drawn in. The pen
                              tool stores freeform vertices in `points`; x1..y2
                              is only the bounding box kept for overlap maths. */}
                          {s.points && s.points.length >= 3 ? (
                            <polygon
                              points={s.points.map(pt => pt.join(',')).join(' ')}
                              fill={fill} stroke={color} strokeWidth={0.003}
                            />
                          ) : (
                            <rect
                              x={x} y={y} width={w} height={h}
                              fill={fill} stroke={color} strokeWidth={0.003} rx={0.004}
                            />
                          )}
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

                    {/* Only vehicles lying across two bays. Bays are scored
                        against their baseline, so every other detector box is
                        noise here. Dashed while the double-park wait runs. */}
                    {(detections?.vehicles ?? [])
                      .filter(v => v.double_parking)
                      .map(v => (
                        <rect
                          key={`veh-${v.id}`}
                          x={v.bbox.x} y={v.bbox.y}
                          width={v.bbox.width} height={v.bbox.height}
                          fill="rgba(217, 59, 59, 0.12)"
                          stroke="#D93B3B"
                          strokeWidth={0.0025}
                          strokeDasharray={v.double_parking === 'flagged' ? undefined : '0.012 0.008'}
                        />
                      ))}
                  </svg>
                )}

                {!isLive && (
                  <div className={`cm-state${zoneCam ? ' cm-state--over' : ''}${stage === 'offline' || stage === 'missing' ? ' cm-state--offline' : ''}`}>
                    {stage === 'loading' || stage === 'connecting' ? (
                      <div className="cm-spinner" />
                    ) : stage === 'offline' || stage === 'missing' ? (
                      <VideoOff size={30} />
                    ) : stage === 'no-camera' ? (
                      <Camera size={30} />
                    ) : (
                      <ParkingCircle size={30} />
                    )}
                    <p className="cm-state-title">
                      {{
                        loading:     selZone ? 'Loading the camera…' : 'Loading parking zones…',
                        connecting:  `Connecting to ${camName}…`,
                        offline:     `${camName} is offline`,
                        missing:     'This zone’s camera is unavailable',
                        'no-camera': 'No camera on this zone',
                        'no-zone':   zones.length ? 'Select a parking zone' : 'No parking zones yet',
                      }[stage]}
                    </p>
                    <p className="cm-state-sub">
                      {{
                        loading:     'The picture appears as soon as the feed is ready.',
                        connecting:  zoneDetail || 'Bay counts on the right are live — only the picture is still loading.',
                        offline:     `${zoneDetail ? `${zoneDetail} ` : ''}Bay counts on the right keep updating while it reconnects.`,
                        missing:     'It may have been removed or switched off in Device Management.',
                        'no-camera': selZone ? `Ask an administrator to assign a camera to ${selZone.name}.` : '',
                        'no-zone':   zones.length ? 'Pick a zone above.' : 'An administrator sets zones up in Parking Management.',
                      }[stage]}
                    </p>
                    {stage === 'missing' && (
                      <button type="button" className="cm-state-btn" onClick={loadZones}>
                        <RefreshCw size={13} /> Refresh
                      </button>
                    )}
                  </div>
                )}

                {zoneCam && (
                  <>
                    <div className="cm-tag">
                      <span className={`cm-dot ${FEED_DOT[feedState(zoneCam)]}`} />
                      {zoneCamName}
                      {lensCount > 1 && ` · Lens ${lensIdx + 1}`}
                    </div>
                    <button
                      className="cm-fs"
                      onClick={async () => {
                        if (!(await camFs.toggle('parking'))) toast.error('Fullscreen was blocked by the browser.')
                      }}
                      title={camFs.isFullscreen('parking') ? 'Exit fullscreen' : 'Fullscreen'}
                      aria-label={camFs.isFullscreen('parking') ? 'Exit fullscreen' : 'Fullscreen'}
                    >
                      {camFs.isFullscreen('parking') ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
                    </button>
                  </>
                )}
              </div>
            </div>

            {/* Legend. Two different rates, and saying so stops a guard reading
                a stale bay colour as a dead feed: the picture streams, the bay
                verdicts come from the 8-second occupancy poll. */}
            <div className="cm-foot pm-legend-strip">
              <span className="pm-legend-item"><span className="pm-legend-dot pm-legend-dot--free" />Free</span>
              <span className="pm-legend-item"><span className="pm-legend-dot pm-legend-dot--occ" />Occupied</span>
              <span className="pm-legend-item"><span className="pm-legend-dot pm-legend-dot--dbl" />Double parking</span>
              <span className="pm-legend-note">Live picture · bays refresh every 8 s</span>
            </div>
          </section>

          {/* Right: the numbers, then what a guard can do about them. */}
          <aside className="cm-side">

            {selZone && (
              <section className="cm-panel">
                <div className="cm-panel-head">
                  <span className="cm-panel-title">
                    {selZone.vehicle_category === 'motorcycle' ? <Bike size={14} /> : <Car size={14} />}
                    {catLabel} Parking
                  </span>
                  <div className="cm-panel-end"><span className="pm-guard-scope">Campus-wide</span></div>
                </div>
                <div className="pm-guard-occ">
                  {/* An event is holding part of the lot. First in the panel,
                      because it explains the numbers under it — the guard is
                      the one who has to tell a driver why the lot closed early. */}
                  {capEvent && reserved > 0 && (
                    <div className="pm-event-banner">
                      <CalendarDays size={15} />
                      <span>
                        <strong>{capEvent.name}</strong>
                        {capEvent.time_display !== 'All day' ? ` (${capEvent.time_display})` : ' today'}
                        {' — '}{reserved} space{reserved === 1 ? '' : 's'} held
                        ({capEvent.share_label.replace(/^About /, '').replace(' of parking', ' of the lot')}).
                      </span>
                    </div>
                  )}

                  <div className={`pm-guard-free${isFull ? ' full' : ''}`}>
                    {isFull ? <AlertTriangle size={22} /> : <CheckCircle2 size={22} />}
                    <div>
                      <p className="pm-guard-free-val">{isFull ? 'FULL' : sumFr}</p>
                      <p className="pm-guard-free-lbl">
                        {isFull
                          ? `No ${catLabel.toLowerCase()} spaces left`
                          : `${catLabel.toLowerCase()} space${sumFr === 1 ? '' : 's'} free`}
                      </p>
                    </div>
                  </div>

                  <div className="pm-guard-bar" aria-hidden="true">
                    <span className="pm-guard-bar-occ" style={{ width: `${pct(occ)}%` }} />
                    <span className="pm-guard-bar-held" style={{ width: `${pct(reserved)}%` }} />
                  </div>

                  <dl className="pm-guard-figs">
                    <div><dt>Parked</dt><dd>{occ}</dd></div>
                    <div><dt>Capacity</dt><dd>{totalCap}</dd></div>
                    {reserved > 0 && <div><dt>Held</dt><dd>{reserved}</dd></div>}
                    <div><dt>On campus</dt><dd>{onCampus}</dd></div>
                  </dl>

                  {/* Where these numbers come from. On campus will usually be
                      higher than parked — drop-offs, cars still circling, cars
                      parked where no camera watches — and a guard who expects
                      the two to match would distrust both. */}
                  <p className="pm-guard-note">
                    Free and parked come from the parking cameras in every {catLabel.toLowerCase()} zone.
                    On campus counts gate entry and exit scans, parked or not.
                  </p>
                  {unmonitored > 0 && (
                    <p className="pm-guard-override-tag pm-guard-unmonitored">
                      {unmonitored} {catLabel.toLowerCase()} zone{unmonitored === 1 ? ' is' : 's are'} not
                      monitored yet, so the free count may be too high.
                    </p>
                  )}
                </div>
              </section>
            )}

            {selZone && (
              <section className="cm-panel">
                <div className="cm-panel-head">
                  <span className="cm-panel-title"><LayoutGrid size={14} /> Bays in {selZone.name}</span>
                  <div className="cm-panel-end">
                    <span className={`pm-guard-detector${monitored ? ' on' : ''}${!selZone.has_baseline ? ' warn' : ''}`}>
                      <span className="cm-dot" />{' '}
                      {!selZone.has_baseline ? 'Not set up' : monitored ? 'Monitoring' : 'Camera off'}
                    </span>
                  </div>
                </div>
                <div className="pm-guard-bays">
                  <p className="pm-guard-bays-val">
                    {baysOccupied}<span>/{bayTotal}</span>
                  </p>
                  <div>
                    <p className="pm-guard-bays-lbl">bays taken</p>
                    {/* A zone with no baseline is not scored at all, so its bay
                        colours are whatever they last were. A guard has to know
                        that before sending anyone to a "free" bay. */}
                    <p className="pm-guard-note">
                      {selZone.has_baseline
                        ? 'What the camera sees in this zone.'
                        : 'Not monitored yet. An admin needs to finish this zone’s setup, so these bays may be out of date.'}
                    </p>
                  </div>
                </div>
                {selZone.capacity_override != null && (
                  <p className="pm-guard-override-tag">Event capacity override in effect</p>
                )}
              </section>
            )}

            <section className="cm-panel">
              <div className="cm-panel-head">
                <span className="cm-panel-title"><Shield size={14} /> Actions</span>
              </div>
              <div className="pm-guard-actions">
                <button
                  type="button"
                  className="pm-btn pm-btn--danger"
                  onClick={() => setShowViolation(true)}
                  title="Issue a violation to a vehicle"
                >
                  <AlertTriangle size={14} /> Issue Violation
                </button>
                <button
                  type="button"
                  className="pm-btn pm-guard-override"
                  onClick={() => setShowOverride(true)}
                  disabled={!selZone}
                  title="Allow a vehicle to park regardless of zone capacity"
                >
                  <Shield size={14} /> Override Parking
                </button>
              </div>
            </section>

            {/* Confiscated owners may not park either, and a guard walking the
                lot is who would spot one. */}
            <ConfiscatedAccounts compact />
          </aside>
        </div>

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

      {bayLookup && (
        <BayOccupantModal
          space={bayLookup}
          zoneName={selZone?.name}
          onClose={() => setBayLookup(null)}
        />
      )}

    </>
  )
}
