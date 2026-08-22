import { useState, useEffect, useCallback } from 'react'
import { useLiveUpdates } from '../../realtime/useLiveUpdates'
import {
  ParkingCircle, Bike, Car, RefreshCw,
  Shield, AlertTriangle, X, CheckCircle2, LayoutGrid,
} from 'lucide-react'
import notify, { toast } from '../../components/Feedback/notify'
import { fieldProblems } from '../../components/Feedback/formProblems'
import DoubleParkingAlerts from '../../components/DoubleParkingAlerts'
import { zoneApi } from '../../api/parking'
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

  const selZone   = zones.find(z => z.id === selId) ?? null
  const camRunning = false

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

              {/* Canvas */}
              <div className="pm-canvas-wrapper">
                {selZone.reference_image_url ? (
                  <img
                    src={selZone.reference_image_url}
                    className="pm-canvas-img"
                    draggable={false}
                    alt=""
                  />
                ) : (
                  <div className="pm-canvas-no-img">No reference image for this zone.</div>
                )}

                {/* SVG overlay — read-only (no event handlers) */}
                <svg
                  className="pm-canvas-svg"
                  viewBox="0 0 1 1"
                  preserveAspectRatio="none"
                >
                  {liveSpaces.map(s => {
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
                <span className="pm-legend-note">Auto-refreshes every 8 s</span>
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
