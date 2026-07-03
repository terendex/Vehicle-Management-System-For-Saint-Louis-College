import { useState, useEffect, useCallback, useRef } from 'react'
import {
  CheckCircle, XCircle, HelpCircle, AlertTriangle,
  ClipboardList, UserPlus, X, Shield, Search, LogOut, Video, Wifi, Star, Clock,
} from 'lucide-react'
import { toast } from 'sonner'
import { formatDistanceToNow } from 'date-fns'
import SecurityLayout from '../../components/Layout/SecurityLayout'
import {
  manualEntry, getAccessLogs, getOffices,
  createVisitorPass, overrideEntry, logExit,
  getVisitorPasses, extendVisitorPass,
} from '../../api/scanning'
import { getSystemSettings } from '../../api/vehicles'
import { camerasApi } from '../../api/cameras'
import { useCameraContext } from '../../context/CameraContext'
import useAuthStore from '../../stores/authStore'
import './SecurityEntryManagement.css'


const STATUS_META = {
  authorized: { label: 'Approved for Entry',     Icon: CheckCircle,   cls: 'authorized', logCls: 'authorized' },
  wrong_day:  { label: 'Wrong Schedule Day',     Icon: XCircle,       cls: 'wrong_day',  logCls: 'wrong_day'  },
  denied:     { label: 'Entry Denied',           Icon: XCircle,       cls: 'denied',     logCls: 'denied'     },
  unknown:    { label: 'Visitor / Unregistered', Icon: HelpCircle,    cls: 'visitor',    logCls: 'visitor'    },
  no_pass:    { label: 'No Visitor Pass',        Icon: AlertTriangle, cls: 'visitor',    logCls: 'visitor'    },
  disabled:   { label: 'Access Disabled',        Icon: XCircle,       cls: 'denied',     logCls: 'denied'     },
  unreadable: { label: 'Unreadable Plate',       Icon: AlertTriangle, cls: 'visitor',    logCls: 'visitor'    },
  exited:     { label: 'Exited',                 Icon: LogOut,        cls: 'exited',     logCls: 'exited'     },
}
function getMeta(status) { return STATUS_META[status] ?? STATUS_META.unknown }

const GATE_LABELS = { gate1: 'Gate 1', gate4: 'Gate 4' }

function timeAgo(ts) {
  try { return formatDistanceToNow(new Date(ts), { addSuffix: true }) }
  catch { return '' }
}

// Time-left / overstay info for an active visitor pass
function passTimeInfo(p) {
  if (!p.expires_at) return { label: 'No limit', overdue: false, soon: false }
  const diffMin = Math.round((new Date(p.expires_at).getTime() - Date.now()) / 60000)
  if (diffMin >= 0) return { label: `${diffMin}m left`, overdue: false, soon: diffMin <= 10 }
  return { label: `OVERSTAY +${-diffMin}m`, overdue: true, soon: false }
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

// ─── CircleCountdown ───────────────────────────────────────────────────────────
function CircleCountdown({ duration = 5, onDismiss }) {
  const R = 18
  const circumference = +(2 * Math.PI * R).toFixed(2) // 113.1
  const [progress, setProgress] = useState(0) // 0 → 1
  const rafRef  = useRef(null)
  const startRef = useRef(null)

  useEffect(() => {
    startRef.current = performance.now()
    const tick = (now) => {
      const t = Math.min((now - startRef.current) / (duration * 1000), 1)
      setProgress(t)
      if (t < 1) rafRef.current = requestAnimationFrame(tick)
    }
    rafRef.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(rafRef.current)
  }, [duration])

  // green (#22c55e) → yellow (#f59e0b) → red (#ef4444)
  let r, g, b
  if (progress < 0.5) {
    const p = progress * 2
    r = Math.round(34  + (245 - 34)  * p)
    g = Math.round(197 + (158 - 197) * p)
    b = Math.round(94  + (11  - 94)  * p)
  } else {
    const p = (progress - 0.5) * 2
    r = Math.round(245 + (239 - 245) * p)
    g = Math.round(158 + (68  - 158) * p)
    b = Math.round(11  + (68  - 11)  * p)
  }
  const color = `rgb(${r},${g},${b})`
  const secsLeft = Math.ceil(duration * (1 - progress))

  return (
    <div
      title="Cooldown — click to dismiss"
      onClick={onDismiss}
      style={{ position: 'relative', width: 44, height: 44, cursor: 'pointer', flexShrink: 0 }}
    >
      <svg width="44" height="44" style={{ transform: 'rotate(-90deg)', display: 'block' }}>
        <circle cx="22" cy="22" r={R} fill="none" stroke="rgba(0,0,0,0.08)" strokeWidth="3.5" />
        <circle
          cx="22" cy="22" r={R}
          fill="none"
          stroke={color}
          strokeWidth="3.5"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * progress}
          strokeLinecap="round"
        />
      </svg>
      <div style={{
        position: 'absolute', inset: 0,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 11, fontWeight: 700, color,
        pointerEvents: 'none',
      }}>
        {secsLeft > 0 ? secsLeft : ''}
      </div>
    </div>
  )
}

// ─── ResultCard ────────────────────────────────────────────────────────────────
function ResultCard({ result, offices, onPassCreated, onOverride, guardName, cooldownKey, cooldownActive, dedupSeconds, onDismiss, onPause, onResume }) {
  const [showVisitor,  setShowVisitor]  = useState(false)
  const [showOverride, setShowOverride] = useState(false)

  // Opening a modal pauses the card's auto-dismiss so the form can't vanish
  const openVisitor   = () => { setShowVisitor(true);   onPause?.() }
  const closeVisitor  = () => { setShowVisitor(false);  onResume?.() }
  const openOverride  = () => { setShowOverride(true);  onPause?.() }
  const closeOverride = () => { setShowOverride(false); onResume?.() }

  if (!result) return (
    <div className="em-card em-result em-result-idle-compact">
      <div className="em-idle-compact-inner">
        <div className="em-result-icon" style={{ width: 36, height: 36, borderRadius: 8, background: '#F3F4F8', flexShrink: 0 }}>
          <Search size={16} style={{ color: '#9BA3BF' }} />
        </div>
        <div>
          <p className="em-result-status" style={{ color: '#9BA3BF', margin: 0 }}>AWAITING LOOKUP</p>
          <p style={{ margin: 0, fontSize: 11, color: '#C8CCDE' }}>Type a plate and press Check Entry</p>
        </div>
      </div>
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
        <div className={`em-result-banner ${cls}`} style={{ position: 'relative' }}>
          {cooldownActive && (
            <div style={{ position: 'absolute', top: 8, right: 8 }}>
              <CircleCountdown key={cooldownKey} duration={dedupSeconds} onDismiss={onDismiss} />
            </div>
          )}
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
              {result.organizer_event && (
                <div className="em-result-row">
                  <span className="em-result-row-label">Organizer</span>
                  <span className="em-violation-pill" style={{ background: '#eff6ff', border: '1px solid #bfdbfe', color: '#1d4ed8' }}>
                    <Star size={10} /> {result.organizer_event.name}
                  </span>
                </div>
              )}
            </div>
          )}
          {isVisitor && (owner?.full_name || result.organizer_event) && (
            <div className="em-result-rows">
              {owner?.full_name && (
                <div className="em-result-row">
                  <span className="em-result-row-label">Owner</span>
                  <span className="em-result-row-value">{owner.full_name}</span>
                </div>
              )}
              {result.organizer_event && (
                <div className="em-result-row">
                  <span className="em-result-row-label">Organizer</span>
                  <span className="em-violation-pill" style={{ background: '#eff6ff', border: '1px solid #bfdbfe', color: '#1d4ed8' }}>
                    <Star size={10} /> {result.organizer_event.name}
                  </span>
                </div>
              )}
            </div>
          )}
          <div style={{ display: 'flex', gap: 6, marginTop: 8, flexDirection: 'column' }}>
            {isVisitor && (
              <button className="em-btn em-btn-secondary" style={{ width: '100%' }} onClick={openVisitor}>
                <UserPlus size={14} /> Create Visitor Pass
              </button>
            )}
            {isDeniable && (
              <button className="em-btn" style={{ width: '100%', background: '#d97706', color: '#fff', border: 'none', justifyContent: 'center' }}
                onClick={openOverride}>
                <Shield size={14} /> Override Entry
              </button>
            )}
          </div>
        </div>
      </div>

      {showVisitor && (
        <VisitorPassModal plate={result.plate_number} offices={offices}
          onClose={closeVisitor} onCreated={onPassCreated} guardName={guardName} />
      )}
      {showOverride && (
        <OverrideModal plate={result.plate_number}
          onClose={closeOverride}
          onOverridden={() => onOverride?.()} />
      )}
    </>
  )
}

// ─── Main Page ─────────────────────────────────────────────────────────────────
export default function SecurityEntryManagement() {
  const { user } = useAuthStore()
  const gateLabel = GATE_LABELS[user?.gate_assignment] || user?.gate_assignment || 'Main Gate'

  const [plateInput, setPlateInput]   = useState('')
  const [loading, setLoading]         = useState(false)
  const [exitLoading, setExitLoading] = useState(false)
  const [scanQueue, setScanQueue]     = useState([]) // [{id, result, cooldownKey}]
  const [exitResult, setExitResult]   = useState(null)
  const [logs, setLogs]               = useState([])
  const [offices, setOffices]         = useState([])
  const [passes, setPasses]           = useState([]) // today's ACTIVE visitor passes
  const overstayToasted = useRef(new Set()) // pass ids already alerted for overstay
  const [dedupSeconds, setDedupSeconds] = useState(5)
  const queueTimers = useRef(new Map()) // queue entry id → auto-dismiss timeout

  const addToQueue = (r, secs = dedupSeconds) => {
    const id = Date.now() + Math.random()
    setScanQueue(prev => [{ id, result: r, cooldownKey: id, paused: false }, ...prev].slice(0, 4))
    queueTimers.current.set(id, setTimeout(() => removeFromQueue(id), secs * 1000))
  }

  const removeFromQueue = (id) => {
    clearTimeout(queueTimers.current.get(id))
    queueTimers.current.delete(id)
    setScanQueue(prev => prev.filter(e => e.id !== id))
  }

  // Pause the auto-dismiss while a modal (visitor pass / override) is open so
  // the card can't vanish mid-form; resume restarts the full countdown.
  const pauseQueueEntry = (id) => {
    clearTimeout(queueTimers.current.get(id))
    queueTimers.current.delete(id)
    setScanQueue(prev => prev.map(e => e.id === id ? { ...e, paused: true } : e))
  }

  const resumeQueueEntry = (id) => {
    clearTimeout(queueTimers.current.get(id))
    setScanQueue(prev => prev.map(e => e.id === id
      ? { ...e, paused: false, cooldownKey: Date.now() } : e))
    queueTimers.current.set(id, setTimeout(() => removeFromQueue(id), dedupSeconds * 1000))
  }

  const { cameras, results, addCamera, registerCanvas } = useCameraContext()
  const [rtspActiveCamId, setRtspActiveCam] = useState(null)
  const rtspCameras = cameras.filter(c => c.assignment === 'entry')
  const rtspActiveCam = rtspCameras.find(c => c.id === rtspActiveCamId) ?? rtspCameras[0] ?? null
  const rtspResults = results.filter(r => rtspCameras.some(c => c.id === r._camId))

  useEffect(() => {
    if (!rtspActiveCamId && rtspCameras.length > 0) setRtspActiveCam(rtspCameras[0].id)
  }) // intentionally no deps — runs after every render until activeCamId is set

  const isLive = rtspCameras.some(c => c.streamConnected)

  const scanCooldown = useRef(new Map()) // plate → { status, timeoutId }
  const processedRids = useRef(new Set()) // result _rid values already handled

  // Auto-process ML scan results from camera
  useEffect(() => {
    if (!rtspResults?.length) return
    rtspResults.forEach(r => {
      if (!r.plate_number) return
      if (r.status === 'duplicate') return
      // Each delivered result is handled exactly once — results linger in context
      // state, and this effect re-runs on every render, so without this guard the
      // same scan would re-spam the queue/toasts/log every time the cooldown lapses
      if (r._rid) {
        if (processedRids.current.has(r._rid)) return
        processedRids.current.add(r._rid)
        if (processedRids.current.size > 500) processedRids.current.clear()
      }
      if (r._at && Date.now() - r._at > 30000) return // stale result from before this page mounted
      const existing = scanCooldown.current.get(r.plate_number)
      // Skip only when the same status repeats within the dedup window
      if (existing && existing.status === r.status) return
      // Different status (entry→exit or exit→entry): reset the timer and log it
      if (existing) clearTimeout(existing.timeoutId)
      const timeoutId = setTimeout(() => scanCooldown.current.delete(r.plate_number), dedupSeconds * 1000)
      scanCooldown.current.set(r.plate_number, { status: r.status, timeoutId })
      addToQueue(r)
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
      .then(cams => cams.forEach(c => addCamera(c.name, c.rtsp_url, 'entry', { detect: true, gate: c.gate_id })))
      .catch(() => {})
    getSystemSettings()
      .then(({ data }) => { if (data?.scan_dedup_seconds) setDedupSeconds(data.scan_dedup_seconds) })
      .catch(() => {})
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const refreshLogs = () =>
    getAccessLogs({ limit: 20, ...gateFilter }).then(r => setLogs(r.data?.results ?? r.data ?? [])).catch(() => {})

  // Active visitor passes — alert once per pass when it crosses into overstay
  const refreshPasses = () =>
    getVisitorPasses().then(r => {
      const list = (r.data?.results ?? r.data ?? []).filter(p => p.status === 'active')
      setPasses(list)
      list.forEach(p => {
        if (p.expires_at && new Date(p.expires_at).getTime() < Date.now()
            && !overstayToasted.current.has(p.id)) {
          overstayToasted.current.add(p.id)
          toast.warning(`Visitor overstay: ${p.plate_number} exceeded the allowed ${p.allowed_duration} min.`, { duration: 8000 })
        }
      })
    }).catch(() => {})

  const refreshAll = () => { refreshLogs(); refreshPasses() }

  useEffect(() => {
    refreshPasses()
    const t = setInterval(refreshPasses, 30000)
    return () => clearInterval(t)
  }, [])

  const handleExtendPass = (p) => {
    extendVisitorPass(p.id, 30)
      .then(() => {
        toast.success(`Pass extended +30 min for ${p.plate_number}.`)
        overstayToasted.current.delete(p.id) // re-alert if it overstays again
        refreshPasses()
      })
      .catch(err => toast.error(err?.response?.data?.error || 'Failed to extend pass.'))
  }

  const handleCheckEntry = async (e) => {
    e?.preventDefault()
    const plate = plateInput.trim().toUpperCase()
    if (!plate) return
    setLoading(true)
    setExitResult(null)
    try {
      const res = await manualEntry({ plate_number: plate })
      addToQueue(res.data)
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

  const handleRecordExit = async () => {
    const plate = plateInput.trim().toUpperCase()
    if (!plate) return
    setExitLoading(true)
    setExitResult(null)
    try {
      const res = await logExit({ plate_number: plate })
      setExitResult(res.data)
      setPlateInput('')
      const dur = res.data.duration_minutes
      toast.success(dur != null ? `Exit recorded for ${plate} — inside for ${dur} min.` : `Exit recorded for ${plate}.`)
      if (res.data.overstay_minutes > 0) {
        toast.warning(`${plate} overstayed by ${res.data.overstay_minutes} min.`, { duration: 8000 })
      }
      refreshAll()
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
                  {(() => {
                    const s = rtspActiveCam?.mlStatus?.stage
                    const m = rtspActiveCam?.mlStatus?.message
                    if (!s || s === 'idle') return null
                    if (s === 'ready') return (
                      <div style={{
                        position: 'absolute', bottom: 10, left: 10,
                        background: 'rgba(16,185,129,0.18)', color: '#10b981',
                        border: '1px solid rgba(16,185,129,0.35)',
                        padding: '4px 9px', borderRadius: 7, fontSize: 11,
                        fontWeight: 600, display: 'flex', alignItems: 'center', gap: 5,
                        pointerEvents: 'none', backdropFilter: 'blur(4px)',
                      }}>
                        <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#10b981', display: 'inline-block', flexShrink: 0 }} />
                        Detection Ready
                      </div>
                    )
                    return (
                      <div style={{
                        position: 'absolute', bottom: 10, left: 10,
                        background: 'rgba(0,0,0,0.78)', color: '#fff',
                        padding: '5px 10px', borderRadius: 7, fontSize: 11,
                        fontWeight: 600, display: 'flex', alignItems: 'center', gap: 7,
                        pointerEvents: 'none', backdropFilter: 'blur(4px)',
                      }}>
                        <div className="em-spinner" style={{ width: 12, height: 12, borderWidth: 2, borderTopColor: '#60a5fa', borderColor: 'rgba(96,165,250,0.2)', flexShrink: 0 }} />
                        {m || 'Initializing…'}
                      </div>
                    )
                  })()}
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

            {/* Combined Plate Input */}
            <div className="em-card-head" style={{ borderTop: '1px solid #f3f4f6' }}>
              <span className="em-card-label"><Search size={14} /> Plate Number</span>
            </div>
            <div style={{ padding: '12px 16px 14px' }}>
              <form onSubmit={handleCheckEntry}>
                <input
                  className="em-plate-input"
                  value={plateInput}
                  onChange={e => { setPlateInput(e.target.value.toUpperCase()); setExitResult(null) }}
                  placeholder="E.G. ABC 123"
                  style={{
                    width: '100%', padding: '11px 14px', border: '2px solid #E2E6EE',
                    borderRadius: 10, fontSize: 16, fontWeight: 700, letterSpacing: 2,
                    textTransform: 'uppercase', outline: 'none', fontFamily: 'monospace',
                    boxSizing: 'border-box', marginBottom: 8,
                  }}
                  autoComplete="off"
                />
                <div style={{ display: 'flex', gap: 8 }}>
                  <button
                    type="submit"
                    className="em-btn em-btn-primary em-btn-lg"
                    style={{ flex: 1 }}
                    disabled={loading || exitLoading || !plateInput.trim()}
                  >
                    {loading ? <><div className="em-spinner" /> Checking…</> : <><Search size={15} /> Check Entry</>}
                  </button>
                  <button
                    type="button"
                    className="em-btn em-btn-lg"
                    style={{ flex: 1, background: '#f0fdf4', color: '#166534', border: '1.5px solid #bbf7d0' }}
                    disabled={loading || exitLoading || !plateInput.trim()}
                    onClick={handleRecordExit}
                  >
                    {exitLoading ? <><div className="em-spinner" style={{ borderTopColor: '#166534' }} /> Recording…</> : <><LogOut size={15} /> Log Exit</>}
                  </button>
                </div>
              </form>
              {exitResult && (
                <div style={{ marginTop: 8, padding: '7px 10px', background: '#f0fdf4', border: '1px solid #bbf7d0', borderRadius: 7, fontSize: 12, color: '#166534' }}>
                  <strong>{exitResult.plate_number}</strong> exited
                  {exitResult.duration_minutes != null && <> · inside <strong>{exitResult.duration_minutes} min</strong></>}
                  {exitResult.overstay_minutes > 0 && (
                    <> · <span style={{ color: '#dc2626', fontWeight: 700 }}>overstayed {exitResult.overstay_minutes} min</span></>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* Right panel */}
          <div className="em-right">
            {scanQueue.length === 0 ? (
              <ResultCard
                result={null}
                offices={offices}
                onPassCreated={refreshAll}
                onOverride={refreshAll}
                guardName={user?.full_name}
                cooldownKey={0}
                cooldownActive={false}
                dedupSeconds={dedupSeconds}
                onDismiss={() => {}}
              />
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {scanQueue.map(item => (
                  <ResultCard
                    key={item.id}
                    result={item.result}
                    offices={offices}
                    onPassCreated={refreshAll}
                    onOverride={refreshAll}
                    guardName={user?.full_name}
                    cooldownKey={item.cooldownKey}
                    cooldownActive={!item.paused}
                    dedupSeconds={dedupSeconds}
                    onDismiss={() => removeFromQueue(item.id)}
                    onPause={() => pauseQueueEntry(item.id)}
                    onResume={() => resumeQueueEntry(item.id)}
                  />
                ))}
              </div>
            )}

            {/* Recent scans */}
            <div className="em-card em-audit-card">
              <div className="em-card-head">
                <span className="em-card-label"><ClipboardList size={14} /> Recent Scans — {gateLabel}</span>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span className="em-logs-count">{logs.length}</span>
                  <button
                    onClick={refreshLogs}
                    title="Refresh"
                    style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 2, color: '#9ca3af', display: 'flex', alignItems: 'center' }}
                  >
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/>
                      <path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15"/>
                    </svg>
                  </button>
                </div>
              </div>
              {logs.length === 0 ? (
                <div className="em-audit-empty">
                  <ClipboardList size={22} style={{ color: '#d1d5db' }} />
                  <p>No entries recorded yet today.</p>
                </div>
              ) : (
                <div className="em-audit-list">
                  {logs.map((log, i) => {
                    const m = getMeta(log.status)
                    const { Icon } = m
                    return (
                      <div key={log.id ?? i} className={`em-audit-row ${m.logCls}`}>
                        <div className={`em-audit-icon ${m.logCls}`}>
                          <Icon size={13} />
                        </div>
                        <div className="em-audit-info">
                          <div className="em-audit-top">
                            <span className="em-audit-plate">{log.plate_number || '—'}</span>
                            <span className={`em-log-badge ${m.logCls}`}>{m.label}</span>
                          </div>
                          {(log.vehicle_owner_name || log.scanned_by_name || log.on_duty_guard_name) && (
                            <div className="em-audit-sub">
                              {log.vehicle_owner_name && <span>{log.vehicle_owner_name}</span>}
                              {log.on_duty_guard_name && <span>· On duty: {log.on_duty_guard_name}</span>}
                              {log.scanned_by_name && log.scanned_by_name !== log.on_duty_guard_name && (
                                <span>· {log.scanned_by_name}</span>
                              )}
                            </div>
                          )}
                        </div>
                        <span className="em-audit-time">{timeAgo(log.scanned_at)}</span>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>

            {/* Active visitors — time remaining / overstay */}
            <div className="em-card">
              <div className="em-card-head">
                <span className="em-card-label"><Clock size={14} /> Active Visitors</span>
                <span className="em-logs-count">{passes.length}</span>
              </div>
              {passes.length === 0 ? (
                <p style={{ margin: 0, padding: '10px 2px', fontSize: 12, color: '#9ca3af' }}>
                  No visitors currently inside.
                </p>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {passes.map(p => {
                    const t = passTimeInfo(p)
                    return (
                      <div
                        key={p.id}
                        style={{
                          display: 'flex', alignItems: 'center', gap: 8, padding: '6px 9px',
                          borderRadius: 8,
                          background: t.overdue ? '#fef2f2' : '#f8fafc',
                          border: `1px solid ${t.overdue ? '#fecaca' : '#e2e8f0'}`,
                        }}
                      >
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ fontWeight: 700, fontSize: 12.5, fontFamily: "'Courier New', monospace", letterSpacing: 0.5 }}>
                            {p.plate_number}
                          </div>
                          <div style={{ fontSize: 11, color: '#6b7280', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                            {p.office_name || 'No office'}{p.purpose ? ` · ${p.purpose}` : ''}
                          </div>
                        </div>
                        <span style={{
                          fontSize: 11, fontWeight: 700, whiteSpace: 'nowrap',
                          color: t.overdue ? '#dc2626' : t.soon ? '#d97706' : '#059669',
                        }}>
                          {t.overdue && <AlertTriangle size={11} style={{ verticalAlign: -1, marginRight: 3 }} />}
                          {t.label}
                        </span>
                        <button
                          onClick={() => handleExtendPass(p)}
                          title="Extend by 30 minutes"
                          style={{
                            fontSize: 11, fontWeight: 600, padding: '3px 8px', borderRadius: 6,
                            border: '1px solid #d1d5db', background: '#fff', cursor: 'pointer',
                            color: '#374151', whiteSpace: 'nowrap',
                          }}
                        >
                          +30m
                        </button>
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
