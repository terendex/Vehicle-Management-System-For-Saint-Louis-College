import { useState, useEffect, useCallback, useRef } from 'react'
import {
  CheckCircle, XCircle, HelpCircle, AlertTriangle,
  ClipboardList, UserPlus, X, Shield, Search, LogOut, Video, Wifi, DoorOpen,
} from 'lucide-react'
import { toast } from 'sonner'
import { formatDistanceToNow } from 'date-fns'
import SecurityLayout from '../../components/Layout/SecurityLayout'
import {
  manualEntry, getAccessLogs, getOffices,
  createVisitorPass, overrideEntry, logExit,
} from '../../api/scanning'
import { camerasApi } from '../../api/cameras'
import { useCameraContext } from '../../context/CameraContext'
import useAuthStore from '../../stores/authStore'
import './SecurityEntryManagement.css'

// ─── OnDutyPanel ──────────────────────────────────────────────────────────────
function OnDutyPanel({ guard, gate }) {
  if (!guard) return null
  const gateLabel = GATE_LABELS[gate] || (gate ? `Gate ${gate}` : 'Main Gate')
  return (
    <div className="em-on-duty">
      <div className="em-on-duty-left">
        {guard.photo_url ? (
          <img src={guard.photo_url} alt={guard.full_name} className="em-on-duty-photo" />
        ) : (
          <div className="em-on-duty-avatar">
            {guard.full_name ? guard.full_name.charAt(0).toUpperCase() : 'G'}
          </div>
        )}
        <div className="em-on-duty-info">
          <span className="em-on-duty-label">On Duty</span>
          <span className="em-on-duty-name">{guard.full_name || 'Guard'}</span>
          <span className="em-on-duty-code">{guard.user_code || '—'}</span>
        </div>
      </div>
      <div className="em-on-duty-gate">
        <DoorOpen size={13} />
        <span>{gateLabel}</span>
      </div>
    </div>
  )
}

const STATUS_META = {
  authorized: { label: 'Approved for Entry',     Icon: CheckCircle,   cls: 'authorized', logCls: 'authorized' },
  wrong_day:  { label: 'Wrong Schedule Day',     Icon: XCircle,       cls: 'wrong_day',  logCls: 'wrong_day'  },
  denied:     { label: 'Entry Denied',           Icon: XCircle,       cls: 'denied',     logCls: 'denied'     },
  unknown:    { label: 'Visitor / Unregistered', Icon: HelpCircle,    cls: 'visitor',    logCls: 'visitor'    },
  no_pass:    { label: 'No Visitor Pass',        Icon: AlertTriangle, cls: 'visitor',    logCls: 'visitor'    },
  disabled:   { label: 'Access Disabled',        Icon: XCircle,       cls: 'denied',     logCls: 'denied'     },
  unreadable: { label: 'Unreadable Plate',       Icon: AlertTriangle, cls: 'visitor',    logCls: 'visitor'    },
  exited:     { label: 'Exited',                 Icon: CheckCircle,   cls: 'authorized', logCls: 'exited'     },
}
function getMeta(status) { return STATUS_META[status] ?? STATUS_META.unknown }

const GATE_LABELS = { gate1: 'Gate 1', gate4: 'Gate 4' }

function timeAgo(ts) {
  try { return formatDistanceToNow(new Date(ts), { addSuffix: true }) }
  catch { return '' }
}

function printVisitorSlip({ plate, purpose, officeName, guardName, issuedAt, expiresAt, duration }) {
  const w = window.open('', '_blank', 'width=320,height=520')
  if (!w) return
  const fmt = (d) => d ? new Date(d).toLocaleString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'
  w.document.write(`<!DOCTYPE html><html><head>
<meta charset="utf-8"/><title>Visitor Slip</title>
<style>
  @media print { @page { size: 80mm auto; margin: 0; } }
  body { font-family: 'Courier New', monospace; font-size: 11px; width: 72mm; margin: 0 auto; padding: 6px; }
  h2 { text-align: center; font-size: 13px; margin: 4px 0 2px; }
  .sub { text-align: center; font-size: 10px; color: #555; margin-bottom: 8px; }
  hr { border: none; border-top: 1px dashed #999; margin: 6px 0; }
  .row { display: flex; justify-content: space-between; margin: 3px 0; }
  .label { color: #555; }
  .plate { font-size: 20px; font-weight: bold; text-align: center; letter-spacing: 3px; margin: 8px 0; border: 2px solid #000; padding: 4px; }
  .footer { text-align: center; font-size: 9px; color: #888; margin-top: 10px; }
  .warn { text-align: center; font-size: 10px; font-weight: bold; margin: 6px 0; }
</style></head><body>
<h2>SAINT LOUIS COLLEGE</h2>
<div class="sub">Vehicle Management System</div>
<div class="sub">--- VISITOR SLIP ---</div>
<div class="plate">${plate}</div>
<hr/>
<div class="row"><span class="label">Office:</span><span>${officeName || 'N/A'}</span></div>
<div class="row"><span class="label">Purpose:</span><span>${purpose || 'N/A'}</span></div>
<div class="row"><span class="label">Duration:</span><span>${duration} min</span></div>
<hr/>
<div class="row"><span class="label">Issued:</span><span>${fmt(issuedAt)}</span></div>
<div class="row"><span class="label">Expires:</span><span>${fmt(expiresAt)}</span></div>
<div class="row"><span class="label">Guard:</span><span>${guardName || 'N/A'}</span></div>
<hr/>
<div class="warn">RETURN THIS SLIP UPON EXIT</div>
<div class="footer">Unauthorized possession is subject to penalty.</div>
</body></html>`)
  w.document.close(); w.focus()
  setTimeout(() => { w.print(); w.close() }, 400)
}

// ─── VisitorPassModal ──────────────────────────────────────────────────────────
function VisitorPassModal({ plate, offices, onClose, onCreated, guardName }) {
  const [officeId, setOfficeId] = useState('')
  const [purpose, setPurpose]   = useState('')
  const [duration, setDuration] = useState(60)
  const [loading, setLoading]   = useState(false)
  const [createdPass, setCreatedPass] = useState(null)

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!officeId || !purpose.trim()) { toast.error('Please fill in all fields.'); return }
    setLoading(true)
    try {
      const res = await createVisitorPass({ plate_number: plate, office: officeId, purpose, allowed_duration: duration })
      const pass = res.data
      setCreatedPass(pass)
      toast.success('Visitor pass created.')
      onCreated()
      const officeName = offices.find(o => String(o.id) === String(officeId))?.name
      printVisitorSlip({ plate, purpose, officeName, guardName, issuedAt: pass.entered_at, expiresAt: pass.expires_at, duration })
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Failed to create visitor pass.')
    } finally { setLoading(false) }
  }

  if (createdPass) return (
    <div className="em-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="em-modal">
        <div className="em-modal-head">
          <span className="em-modal-title"><CheckCircle size={17} style={{ color: '#10b981' }} /> Pass Created</span>
          <button className="em-modal-close" onClick={onClose}><X size={15} /></button>
        </div>
        <div className="em-modal-body">
          <p style={{ margin: 0, fontSize: 13, color: '#166534' }}>
            Visitor slip printed for <strong>{plate}</strong> — valid for <strong>{duration} min</strong>.
          </p>
          <p style={{ margin: '6px 0 0', fontSize: 12, color: '#6b7280' }}>
            Expires: {createdPass.expires_at ? new Date(createdPass.expires_at).toLocaleTimeString() : '—'}
          </p>
        </div>
        <div className="em-modal-foot">
          <button type="button" className="em-btn em-btn-primary" onClick={onClose}>Done</button>
        </div>
      </div>
    </div>
  )

  return (
    <div className="em-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="em-modal">
        <div className="em-modal-head">
          <span className="em-modal-title"><UserPlus size={17} /> Create Visitor Pass</span>
          <button className="em-modal-close" onClick={onClose}><X size={15} /></button>
        </div>
        <form onSubmit={handleSubmit}>
          <div className="em-modal-body">
            <div className="em-field">
              <label className="em-label">License Plate</label>
              <input className="em-input" value={plate} readOnly />
            </div>
            <div className="em-field">
              <label className="em-label">Destination Office</label>
              <select className="em-select" value={officeId} onChange={(e) => setOfficeId(e.target.value)} required>
                <option value="">Select office…</option>
                {offices.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
              </select>
            </div>
            <div className="em-field">
              <label className="em-label">Purpose of Visit</label>
              <textarea className="em-textarea" placeholder="e.g. Enrollment inquiry…" value={purpose}
                onChange={(e) => setPurpose(e.target.value)} required />
            </div>
            <div className="em-field">
              <label className="em-label">Allowed Duration (minutes)</label>
              <input className="em-input" type="number" min={1} max={480} value={duration}
                onChange={(e) => setDuration(Math.max(1, parseInt(e.target.value) || 60))} />
            </div>
          </div>
          <div className="em-modal-foot">
            <button type="button" className="em-btn em-btn-secondary" onClick={onClose}>Cancel</button>
            <button type="submit" className="em-btn em-btn-primary" disabled={loading}>
              {loading ? <><div className="em-spinner" /> Creating…</> : 'Create & Print Slip'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ─── OverrideModal ─────────────────────────────────────────────────────────────
function OverrideModal({ plate, onClose, onOverridden }) {
  const [reason, setReason]   = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!reason.trim()) { toast.error('Please provide a reason.'); return }
    setLoading(true)
    try {
      await overrideEntry({ plate_number: plate, reason })
      toast.success(`Entry override logged for ${plate}.`)
      onOverridden(); onClose()
    } catch (err) {
      toast.error(err?.response?.data?.error || 'Override failed.')
    } finally { setLoading(false) }
  }

  return (
    <div className="em-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="em-modal">
        <div className="em-modal-head">
          <span className="em-modal-title"><Shield size={17} /> Override Entry</span>
          <button className="em-modal-close" onClick={onClose}><X size={15} /></button>
        </div>
        <form onSubmit={handleSubmit}>
          <div className="em-modal-body">
            <div className="em-field">
              <label className="em-label">License Plate</label>
              <input className="em-input" value={plate} readOnly />
            </div>
            <div className="em-field">
              <label className="em-label">Override Reason</label>
              <textarea className="em-textarea" placeholder="e.g. Event day — general admission…"
                value={reason} onChange={(e) => setReason(e.target.value)} rows={3} required />
            </div>
            <p style={{ margin: 0, fontSize: 12, color: '#b45309', background: '#fffbeb', border: '1px solid #fde68a', borderRadius: 6, padding: '6px 10px' }}>
              This override will be logged in the audit trail.
            </p>
          </div>
          <div className="em-modal-foot">
            <button type="button" className="em-btn em-btn-secondary" onClick={onClose}>Cancel</button>
            <button type="submit" className="em-btn em-btn-primary" disabled={loading} style={{ background: '#d97706', borderColor: '#d97706' }}>
              {loading ? <><div className="em-spinner" /> Overriding…</> : 'Confirm Override'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ─── ResultCard ────────────────────────────────────────────────────────────────
function ResultCard({ result, offices, onPassCreated, onOverride, guardName }) {
  const [showVisitor,  setShowVisitor]  = useState(false)
  const [showOverride, setShowOverride] = useState(false)

  if (!result) return (
    <div className="em-card em-result">
      <div className="em-result-banner idle">
        <div className="em-result-icon idle"><Search size={20} /></div>
        <div className="em-result-text">
          <p className="em-result-status" style={{ color: '#9BA3BF' }}>Awaiting lookup</p>
          <p className="em-result-plate" style={{ color: '#C8CCDE', fontSize: 15, letterSpacing: 1 }}>— — — — —</p>
        </div>
      </div>
      <p className="em-idle-hint">Type a plate number above and press Check Entry.</p>
    </div>
  )

  const { Icon, label, cls } = getMeta(result.status)
  const owner     = result.vehicle?.user
  const vehicle   = result.vehicle
  const isVisitor = result.status === 'unknown' || result.status === 'no_pass'
  const isDeniable = ['denied', 'wrong_day', 'disabled'].includes(result.status)
  const todayName = new Date().toLocaleDateString('en-US', { weekday: 'long' })

  return (
    <>
      <div className={`em-card em-result ${cls}`}>
        <div className={`em-result-banner ${cls}`}>
          <div className="em-result-icon"><Icon size={20} /></div>
          <div className="em-result-text">
            <p className="em-result-status">{label}</p>
            <p className="em-result-plate">{result.plate_number || '—'}</p>
          </div>
        </div>
        <div className="em-result-body">
          <p className="em-result-msg">{result.message}</p>
          {result.constraint && (
            <div className="em-constraint-info">
              <AlertTriangle size={13} style={{ flexShrink: 0 }} />
              <span>Rule: <strong>{result.constraint}</strong></span>
            </div>
          )}
          {!isVisitor && owner && (
            <div className="em-result-rows">
              {owner.full_name && (
                <div className="em-result-row">
                  <span className="em-result-row-label">Owner</span>
                  <span className="em-result-row-value">{owner.full_name}</span>
                </div>
              )}
              {owner.owner_type && (
                <div className="em-result-row">
                  <span className="em-result-row-label">Type</span>
                  <span className="em-result-row-value" style={{ textTransform: 'capitalize' }}>
                    {owner.owner_type.replace('_', ' ')}
                  </span>
                </div>
              )}
              {vehicle && (vehicle.vehicle_type || vehicle.color) && (
                <div className="em-result-row">
                  <span className="em-result-row-label">Vehicle</span>
                  <span className="em-result-row-value">
                    {[vehicle.vehicle_type, vehicle.color].filter(Boolean).map(s => s.charAt(0).toUpperCase() + s.slice(1)).join(' · ')}
                  </span>
                </div>
              )}
              {result.has_violations && (
                <div className="em-result-row">
                  <span className="em-result-row-label">Violations</span>
                  <span className="em-violation-pill"><AlertTriangle size={10} /> Unresolved violations</span>
                </div>
              )}
              {result.already_inside && (
                <div className="em-result-row">
                  <span className="em-result-row-label">Warning</span>
                  <span className="em-violation-pill" style={{ background: '#fef9c3', border: '1px solid #fde68a', color: '#92400e' }}>
                    <AlertTriangle size={10} /> Already inside — no exit logged
                  </span>
                </div>
              )}
            </div>
          )}
          {isVisitor && owner?.full_name && (
            <div className="em-result-rows">
              <div className="em-result-row">
                <span className="em-result-row-label">Owner</span>
                <span className="em-result-row-value">{owner.full_name}</span>
              </div>
            </div>
          )}
          <div style={{ display: 'flex', gap: 6, marginTop: 8, flexDirection: 'column' }}>
            {isVisitor && (
              <button className="em-btn em-btn-secondary" style={{ width: '100%' }} onClick={() => setShowVisitor(true)}>
                <UserPlus size={14} /> Create Visitor Pass
              </button>
            )}
            {isDeniable && (
              <button className="em-btn" style={{ width: '100%', background: '#d97706', color: '#fff', border: 'none', justifyContent: 'center' }}
                onClick={() => setShowOverride(true)}>
                <Shield size={14} /> Override Entry
              </button>
            )}
          </div>
        </div>
      </div>

      {showVisitor && (
        <VisitorPassModal plate={result.plate_number} offices={offices}
          onClose={() => setShowVisitor(false)} onCreated={onPassCreated} guardName={guardName} />
      )}
      {showOverride && (
        <OverrideModal plate={result.plate_number}
          onClose={() => setShowOverride(false)}
          onOverridden={() => { onOverride?.(); setShowOverride(false) }} />
      )}
    </>
  )
}

// ─── Main Page ─────────────────────────────────────────────────────────────────
export default function SecurityEntryManagement() {
  const { user } = useAuthStore()
  const gateLabel = GATE_LABELS[user?.gate_assignment] || user?.gate_assignment || 'Main Gate'

  const [plateInput, setPlateInput] = useState('')
  const [loading, setLoading]       = useState(false)
  const [result, setResult]         = useState(null)
  const [logs, setLogs]             = useState([])
  const [offices, setOffices]       = useState([])
  const [exitPlate, setExitPlate]   = useState('')
  const [exitLoading, setExitLoading] = useState(false)
  const [exitResult, setExitResult]   = useState(null)

  const { cameras, results, addCamera, registerCanvas } = useCameraContext()
  const [rtspActiveCamId, setRtspActiveCam] = useState(null)
  const rtspCameras = cameras.filter(c => c.assignment === 'entry')
  const rtspActiveCam = rtspCameras.find(c => c.id === rtspActiveCamId) ?? rtspCameras[0] ?? null
  const rtspResults = results.filter(r => rtspCameras.some(c => c.id === r._camId))

  useEffect(() => {
    if (!rtspActiveCamId && rtspCameras.length > 0) setRtspActiveCam(rtspCameras[0].id)
  }) // intentionally no deps — runs after every render until activeCamId is set

  const isLive = rtspCameras.some(c => c.streamConnected)

  const scanCooldown = useRef(new Set())

  // Auto-process ML scan results from camera
  useEffect(() => {
    if (!rtspResults?.length) return
    rtspResults.forEach(r => {
      if (!r.plate_number || scanCooldown.current.has(r.plate_number)) return
      scanCooldown.current.add(r.plate_number)
      setTimeout(() => scanCooldown.current.delete(r.plate_number), 3000)
      setResult(r)
      const m = getMeta(r.status)
      if (r.allowed) {
        toast.success(`Entry approved: ${r.plate_number}`)
      } else {
        toast.error(`${m.label}: ${r.plate_number}`)
      }
      setLogs(prev => [{
        id: Date.now() + Math.random(),
        plate_number: r.plate_number,
        status: r.status,
        scanned_at: new Date().toISOString(),
        scanned_by_name: user?.full_name,
        gate_id: user?.gate_assignment,
      }, ...prev].slice(0, 20))
    })
  }, [rtspResults]) // eslint-disable-line react-hooks/exhaustive-deps

  const gateFilter = user?.gate_assignment ? { gate_id: user.gate_assignment } : {}

  useEffect(() => {
    getAccessLogs({ limit: 20, ...gateFilter }).then(r => setLogs(r.data?.results ?? r.data ?? [])).catch(() => {})
    getOffices().then(r => setOffices(r.data?.results ?? r.data ?? [])).catch(() => {})
    camerasApi.list({ assignment: 'entry' })
      .then(cams => cams.forEach(c => addCamera(c.name, c.rtsp_url, 'entry')))
      .catch(() => {})
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const refreshLogs = () =>
    getAccessLogs({ limit: 20, ...gateFilter }).then(r => setLogs(r.data?.results ?? r.data ?? [])).catch(() => {})

  const handleCheckEntry = async (e) => {
    e?.preventDefault()
    const plate = plateInput.trim().toUpperCase()
    if (!plate) return
    setLoading(true)
    setResult(null)
    try {
      const res = await manualEntry({ plate_number: plate })
      setResult(res.data)
      const m = getMeta(res.data.status)
      if (res.data.allowed) {
        toast.success(`Entry approved: ${plate}`)
      } else {
        toast.error(`${m.label}: ${plate}`)
      }
      setLogs(prev => [{
        id: Date.now(), plate_number: plate, status: res.data.status,
        scanned_at: new Date().toISOString(), scanned_by_name: user?.full_name,
        gate_id: user?.gate_assignment,
      }, ...prev].slice(0, 20))
    } catch (err) {
      toast.error(err?.response?.data?.error || 'Lookup failed.')
    } finally { setLoading(false) }
  }

  const handleRecordExit = async (e) => {
    e.preventDefault()
    const plate = exitPlate.trim().toUpperCase()
    if (!plate) return
    setExitLoading(true)
    try {
      const res = await logExit({ plate_number: plate })
      setExitResult(res.data)
      setExitPlate('')
      const dur = res.data.duration_minutes
      toast.success(dur != null ? `Exit recorded for ${plate} — inside for ${dur} min.` : `Exit recorded for ${plate}.`)
      refreshLogs()
    } catch (err) {
      toast.error(err?.response?.data?.error || 'Failed to record exit.')
    } finally { setExitLoading(false) }
  }

  return (
    <SecurityLayout fillHeight>
      <div className="em-page">

        {/* Main grid */}
        <div className="em-grid">

          {/* Left: CCTV + plate lookup */}
          <div className="em-card em-camera-card">
            <div className="em-card-head">
              <span className="em-card-label"><Video size={15} /> CCTV Monitor</span>
              <span className={`em-autoscan-status${isLive ? ' scanning' : ''}`} style={{ marginLeft: 'auto' }}>
                {rtspCameras.length === 0
                  ? <><Wifi size={12} /> No cameras</>
                  : isLive
                    ? <><Video size={12} /> {rtspCameras.filter(c => c.streamConnected).length}/{rtspCameras.length} live</>
                    : <><div className="em-spinner" style={{ borderTopColor: '#3b82f6', borderColor: 'rgba(59,130,246,.2)' }} /> Connecting…</>
                }
              </span>
            </div>

            {/* Viewport */}
            <div className="em-viewport" style={{ background: '#0d1117', minHeight: 280, position: 'relative' }}>
              {rtspCameras.length > 0 ? (
                <div style={{ position: 'relative', width: '100%', minHeight: 260 }}>
                  {rtspCameras.map((cam, idx) => (
                    <div
                      key={cam.id}
                      style={{ display: rtspActiveCamId === cam.id ? 'block' : 'none', width: '100%', ...(idx === 0 ? {} : { position: 'absolute', inset: 0 }) }}
                    >
                      <canvas
                        ref={el => registerCanvas(cam.id, el)}
                        style={{ width: '100%', display: 'block', background: '#000', minHeight: 260 }}
                      />
                    </div>
                  ))}
                  {rtspActiveCam && !rtspActiveCam.streamConnected && rtspActiveCam.wsActive && (
                    <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.7)', gap: 12, pointerEvents: 'none' }}>
                      <div className="em-spinner" style={{ width: 36, height: 36, borderWidth: 3, borderTopColor: '#60a5fa', borderColor: 'rgba(96,165,250,0.15)' }} />
                      <p style={{ color: '#93c5fd', fontSize: 13, margin: 0 }}>{rtspActiveCam.statusMsg || 'Connecting…'}</p>
                    </div>
                  )}
                  <div style={{ position: 'absolute', top: 10, left: 10, background: 'rgba(0,0,0,0.65)', color: '#fff', padding: '3px 9px', borderRadius: 6, fontSize: 11, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 5, pointerEvents: 'none' }}>
                    <span style={{ width: 6, height: 6, borderRadius: '50%', background: rtspActiveCam?.streamConnected ? '#22c55e' : '#f59e0b', display: 'inline-block' }} />
                    {rtspActiveCam?.name || 'Camera'}
                  </div>
                </div>
              ) : (
                <div className="em-cam-off">
                  <Wifi size={40} style={{ color: '#374151' }} />
                  <p>No entry cameras configured.</p>
                </div>
              )}
            </div>

            {/* Camera thumbnail strip */}
            {rtspCameras.length > 1 && (
              <div className="em-cam-thumbnails" style={{ borderTop: '1px solid #1e2235' }}>
                {rtspCameras.map(cam => (
                  <div
                    key={`thumb-${cam.id}`}
                    className={`em-cam-thumb ${rtspActiveCamId === cam.id ? 'active' : ''}`}
                    onClick={() => setRtspActiveCam(cam.id)}
                    style={{ position: 'relative' }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: rtspActiveCamId === cam.id ? '#60A5FA' : '#5A5F72' }}>
                      <Wifi size={18} />
                    </div>
                    <div className="em-cam-thumb-label">{cam.name}</div>
                    <span style={{ position: 'absolute', top: 4, left: 4, width: 6, height: 6, borderRadius: '50%', background: cam.streamConnected ? '#22c55e' : cam.wsActive ? '#f59e0b' : '#6b7280' }} />
                  </div>
                ))}
              </div>
            )}

            {/* Plate Lookup */}
            <div className="em-card-head" style={{ borderTop: '1px solid #f3f4f6' }}>
              <span className="em-card-label"><Search size={14} /> Plate Lookup</span>
            </div>
            <div style={{ padding: '12px 16px 8px' }}>
              <form onSubmit={handleCheckEntry} style={{ display: 'flex', gap: 8 }}>
                <input
                  className="em-plate-input"
                  value={plateInput}
                  onChange={e => setPlateInput(e.target.value.toUpperCase())}
                  placeholder="e.g. ABC 123"
                  style={{ flex: 1, padding: '10px 14px', border: '2px solid #E2E6EE', borderRadius: 10,
                    fontSize: 16, fontWeight: 700, letterSpacing: 2, textTransform: 'uppercase', outline: 'none',
                    fontFamily: 'monospace' }}
                  autoComplete="off"
                />
                <button type="submit" className="em-btn em-btn-primary em-btn-lg" disabled={loading || !plateInput.trim()}>
                  {loading ? <><div className="em-spinner" /> Checking…</> : <><Search size={15} /> Check Entry</>}
                </button>
              </form>
              <p style={{ fontSize: 11, color: '#9ca3af', marginTop: 6, marginBottom: 0 }}>
                Type the license plate and press Check Entry or hit Enter.
              </p>
            </div>

            {/* Record Exit */}
            <div style={{ padding: '8px 16px 16px' }}>
              <div className="em-card-head" style={{ marginBottom: 8 }}>
                <span className="em-card-label"><LogOut size={13} /> Record Exit</span>
              </div>
              <form onSubmit={handleRecordExit} style={{ display: 'flex', gap: 6 }}>
                <input
                  value={exitPlate}
                  onChange={e => setExitPlate(e.target.value.toUpperCase())}
                  placeholder="Plate number…"
                  style={{ flex: 1, padding: '8px 12px', border: '1.5px solid #E2E6EE', borderRadius: 8,
                    fontSize: 14, fontFamily: 'monospace', letterSpacing: 1, outline: 'none', textTransform: 'uppercase' }}
                />
                <button type="submit" disabled={exitLoading || !exitPlate.trim()}
                  className="em-btn em-btn-primary" style={{ padding: '8px 16px', fontSize: 12 }}>
                  {exitLoading ? '…' : 'Log Exit'}
                </button>
              </form>
              {exitResult && (
                <div style={{ marginTop: 8, padding: '7px 10px', background: '#f0fdf4', border: '1px solid #bbf7d0', borderRadius: 7, fontSize: 12, color: '#166534' }}>
                  <strong>{exitResult.plate_number}</strong> exited
                  {exitResult.duration_minutes != null && <span> · inside <strong>{exitResult.duration_minutes} min</strong></span>}
                </div>
              )}
            </div>
          </div>

          {/* On Duty guard panel — shown below camera card */}
          <OnDutyPanel guard={user} gate={user?.gate_assignment} />

          {/* Right panel */}
          <div className="em-right">
            <ResultCard
              result={result}
              offices={offices}
              onPassCreated={refreshLogs}
              onOverride={refreshLogs}
              guardName={user?.full_name}
            />

            {/* Recent scans */}
            <div className="em-card">
              <div className="em-card-head">
                <span className="em-card-label"><ClipboardList size={14} /> Recent Entries at {gateLabel}</span>
                <span className="em-logs-count">{logs.length}</span>
              </div>
              {logs.length === 0 ? (
                <p className="em-log-empty">No entries yet today.</p>
              ) : (
                <div className="em-log-list">
                  {logs.map((log, i) => {
                    const m = getMeta(log.status)
                    return (
                      <div key={log.id ?? i} className="em-log-item">
                        <span className={`em-log-dot ${m.logCls}`} />
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                            <span className="em-log-plate">{log.plate_number || '—'}</span>
                            <span className={`em-log-badge ${m.logCls}`}>{m.label}</span>
                          </div>
                          {(log.vehicle_owner_name || log.scanned_by_name) && (
                            <div style={{ fontSize: 10, color: '#9ca3af', marginTop: 1, display: 'flex', gap: 8 }}>
                              {log.vehicle_owner_name && <span>Owner: {log.vehicle_owner_name}</span>}
                              {log.scanned_by_name && <span>Guard: {log.scanned_by_name}</span>}
                              {log.gate_id && log.gate_id !== 'main' && (
                                <span style={{ color: '#6366f1' }}>{GATE_LABELS[log.gate_id] || log.gate_id}</span>
                              )}
                            </div>
                          )}
                        </div>
                        <span className="em-log-time">{timeAgo(log.scanned_at)}</span>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </SecurityLayout>
  )
}
