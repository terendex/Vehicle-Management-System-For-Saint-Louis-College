import { useState, useRef, useCallback, useEffect } from 'react'
import {
  Camera,
  CheckCircle, XCircle, Clock, HelpCircle, AlertTriangle,
  ClipboardList, UserPlus, X, Zap, Video, Wifi, LogOut,
} from 'lucide-react'
import { toast } from 'sonner'
import { formatDistanceToNow } from 'date-fns'
import AdminLayout from '../../components/Layout/AdminLayout'
import { getAccessLogs, getOffices, createVisitorPass } from '../../api/scanning'
import useAuthStore from '../../stores/authStore'
import { camerasApi } from '../../api/cameras'
import { useCameraContext } from '../../context/CameraContext'
import './EntryManagement.css'

// ─── Constants ────────────────────────────────────────────────────────────────

const PLATE_COOLDOWN_MS = 3000
const LOG_LIMIT = 50

const STATUS_META = {
  authorized: { label: 'Approved for Entry', Icon: CheckCircle, cls: 'authorized', logCls: 'authorized' },
  wrong_day:  { label: 'Wrong Schedule Day', Icon: XCircle,    cls: 'wrong_day',  logCls: 'wrong_day' },
  denied:     { label: 'Entry Denied',        Icon: XCircle,    cls: 'denied',     logCls: 'denied' },
  pending:    { label: 'Awaiting Approval',   Icon: Clock,      cls: 'pending',    logCls: 'pending' },
  unknown:    { label: 'Visitor / Unregistered', Icon: HelpCircle, cls: 'visitor', logCls: 'visitor' },
  no_pass:    { label: 'No Visitor Pass',     Icon: AlertTriangle, cls: 'visitor', logCls: 'visitor' },
  disabled:   { label: 'Access Disabled',     Icon: XCircle,    cls: 'denied',     logCls: 'denied' },
  unreadable: { label: 'Unreadable Plate',    Icon: AlertTriangle, cls: 'visitor', logCls: 'visitor' },
  cooldown:   { label: 'Recently Scanned',    Icon: Clock,      cls: 'pending',    logCls: 'pending' },
  exited:     { label: 'Exited',              Icon: LogOut,     cls: 'exited',     logCls: 'exited' },
}

function getMeta(status) {
  return STATUS_META[status] ?? STATUS_META.unknown
}

function timeAgo(ts) {
  try { return formatDistanceToNow(new Date(ts), { addSuffix: true }) } catch { return '' }
}

// ─── Visitor Pass Modal ────────────────────────────────────────────────────────

function VisitorPassModal({ plate, offices, onClose, onCreated }) {
  const [officeId, setOfficeId] = useState('')
  const [purpose, setPurpose] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!officeId || !purpose.trim()) { toast.error('Please fill in all fields.'); return }
    setLoading(true)
    try {
      await createVisitorPass({ plate_number: plate, office: officeId, purpose })
      toast.success('Visitor pass created — awaiting office confirmation.')
      onCreated()
      onClose()
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Failed to create visitor pass.')
    } finally {
      setLoading(false)
    }
  }

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
              <textarea
                className="em-textarea"
                placeholder="e.g. Enrollment inquiry, document pick-up…"
                value={purpose}
                onChange={(e) => setPurpose(e.target.value)}
                required
              />
            </div>
          </div>
          <div className="em-modal-foot">
            <button type="button" className="em-btn em-btn-secondary" onClick={onClose}>Cancel</button>
            <button type="submit" className="em-btn em-btn-primary" disabled={loading}>
              {loading ? <><div className="em-spinner" /> Creating…</> : 'Create Pass'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ─── Result Card ──────────────────────────────────────────────────────────────

function ResultCard({ result, offices, onPassCreated }) {
  const [showModal, setShowModal] = useState(false)

  if (!result) {
    return (
      <div className="em-card em-result">
        <div className="em-result-banner idle">
          <div className="em-result-icon idle"><Camera size={20} /></div>
          <div className="em-result-text">
            <p className="em-result-status" style={{ color: '#9BA3BF' }}>Awaiting scan</p>
            <p className="em-result-plate" style={{ color: '#C8CCDE', fontSize: 15, letterSpacing: 1 }}>— — — — —</p>
          </div>
        </div>
        <p className="em-idle-hint">Point the CCTV camera at a license plate to begin scanning.</p>
      </div>
    )
  }

  const { Icon, label, cls } = getMeta(result.status)
  const owner     = result.vehicle?.user
  const reg       = result.registration
  const vehicle   = result.vehicle
  const isVisitor = result.status === 'unknown' || result.status === 'no_pass'
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
              <span>Rule blocked: <strong>{result.constraint}</strong></span>
            </div>
          )}

          {/* Registered non-visitor: full details */}
          {!isVisitor && owner && (
            <>
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
                {owner.contact && (
                  <div className="em-result-row">
                    <span className="em-result-row-label">Contact</span>
                    <span className="em-result-row-value">{owner.contact}</span>
                  </div>
                )}
              </div>

              {reg?.campus_days?.length > 0 && (
                <div className="em-campus-days">
                  <span className="em-campus-days-label">Campus Days</span>
                  <div className="em-campus-days-list">
                    {reg.campus_days.map(day => (
                      <span key={day} className={`em-day-chip${day === todayName ? ' today' : ''}`}>
                        {day.slice(0, 3)}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              <div className="em-result-rows">
                {reg?.registrant_type === 'student' && (
                  <>
                    {reg.student_id && (
                      <div className="em-result-row">
                        <span className="em-result-row-label">Student ID</span>
                        <span className="em-result-row-value">{reg.student_id}</span>
                      </div>
                    )}
                    {reg.program_year && (
                      <div className="em-result-row">
                        <span className="em-result-row-label">Program</span>
                        <span className="em-result-row-value">{reg.program_year}</span>
                      </div>
                    )}
                  </>
                )}
                {reg?.registrant_type === 'employee' && (
                  <>
                    {reg.employee_id && (
                      <div className="em-result-row">
                        <span className="em-result-row-label">Employee ID</span>
                        <span className="em-result-row-value">{reg.employee_id}</span>
                      </div>
                    )}
                    {reg.department_name && (
                      <div className="em-result-row">
                        <span className="em-result-row-label">Department</span>
                        <span className="em-result-row-value">{reg.department_name}</span>
                      </div>
                    )}
                  </>
                )}
                {vehicle && (vehicle.vehicle_type || vehicle.color) && (
                  <div className="em-result-row">
                    <span className="em-result-row-label">Vehicle</span>
                    <span className="em-result-row-value">
                      {[
                        vehicle.vehicle_type && vehicle.vehicle_type.charAt(0).toUpperCase() + vehicle.vehicle_type.slice(1),
                        vehicle.color && vehicle.color.charAt(0).toUpperCase() + vehicle.color.slice(1),
                      ].filter(Boolean).join(' · ')}
                    </span>
                  </div>
                )}
                {result.has_violations && (
                  <div className="em-result-row">
                    <span className="em-result-row-label">Violations</span>
                    <span className="em-violation-pill">
                      <AlertTriangle size={10} /> Unresolved violations
                    </span>
                  </div>
                )}
              </div>
            </>
          )}

          {/* Visitor / unknown: minimal info */}
          {isVisitor && owner?.full_name && (
            <div className="em-result-rows">
              <div className="em-result-row">
                <span className="em-result-row-label">Owner</span>
                <span className="em-result-row-value">{owner.full_name}</span>
              </div>
            </div>
          )}

          {isVisitor && (
            <button
              className="em-btn em-btn-secondary"
              style={{ width: '100%', marginTop: 4 }}
              onClick={() => setShowModal(true)}
            >
              <UserPlus size={14} /> Create Visitor Pass
            </button>
          )}
        </div>
      </div>

      {showModal && (
        <VisitorPassModal
          plate={result.plate_number}
          offices={offices}
          onClose={() => setShowModal(false)}
          onCreated={onPassCreated}
        />
      )}
    </>
  )
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function EntryManagement() {
  const { user } = useAuthStore()
  const [result, setResult] = useState(null)
  const [logs, setLogs] = useState([])
  const [offices, setOffices] = useState([])
  const plateCooldownRef = useRef(new Set())
  const processedRidsRef = useRef(new Set()) // result _rid values already handled

  const { cameras, results, addCamera, registerCanvas } = useCameraContext()
  const [rtspActiveCamId, setRtspActiveCam] = useState(null)
  const rtspCameras = cameras.filter(c => c.assignment === 'entry')
  const rtspActiveCam = rtspCameras.find(c => c.id === rtspActiveCamId) ?? rtspCameras[0] ?? null
  const rtspResults = results.filter(r => rtspCameras.some(c => c.id === r._camId))

  useEffect(() => {
    if (!rtspActiveCamId && rtspCameras.length > 0) setRtspActiveCam(rtspCameras[0].id)
  }) // intentionally no deps — runs after every render until activeCamId is set

  const handleScanSuccess = useCallback((results) => {
    // Handle each delivered result exactly once — results linger in context state
    // and this callback re-fires on every render, so without the _rid guard the
    // same scan re-spams the card and log whenever the plate cooldown lapses
    const fresh = (results ?? []).filter((r) => {
      if (r.status === 'duplicate') return false
      if (r._rid) {
        if (processedRidsRef.current.has(r._rid)) return false
        processedRidsRef.current.add(r._rid)
        if (processedRidsRef.current.size > 500) processedRidsRef.current.clear()
      }
      if (r._at && Date.now() - r._at > 30000) return false // stale result from before this page mounted
      return true
    })
    if (fresh.length === 0) return
    setResult(fresh)

    const cooldowns = plateCooldownRef.current
    const now = Date.now()
    const newLogs = fresh
      .filter((r) => {
        if (cooldowns.has(r.plate_number)) return false
        cooldowns.add(r.plate_number)
        setTimeout(() => cooldowns.delete(r.plate_number), PLATE_COOLDOWN_MS)
        return true
      })
      .map((r) => ({
        id: now + Math.random(),
        plate_number: r.plate_number,
        status: r.status,
        scanned_at: new Date().toISOString(),
        scanned_by_name: user?.full_name || null,
      }))
    if (newLogs.length > 0) {
      setLogs((prev) => [...newLogs, ...prev].slice(0, LOG_LIMIT))
    }
  }, [user])

  useEffect(() => {
    if (rtspResults?.length > 0) handleScanSuccess(rtspResults)
  }, [rtspResults, handleScanSuccess])

  // Load entry cameras — detect=true enables plate-scan ML for this page
  useEffect(() => {
    camerasApi.list({ assignment: 'entry' })
      .then(cams => cams.forEach(c => addCamera(c.name, c.rtsp_url, 'entry', { detect: true, gate: c.gate_id })))
      .catch(() => {})
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const handlePassCreated = () => {
    getAccessLogs({ limit: 20 }).then((r) => setLogs(r.data?.results ?? r.data ?? [])).catch(() => {})
  }

  useEffect(() => {
    getAccessLogs({ limit: 20 }).then((r) => setLogs(r.data?.results ?? r.data ?? [])).catch(() => {})
    getOffices().then((r) => setOffices(r.data?.results ?? r.data ?? [])).catch(() => {})
  }, [])

  const isLive = rtspCameras.some(c => c.streamConnected)

  return (
    <AdminLayout fillHeight>
      <div className="em-page">

        {/* Header */}
        <div className="em-header">
          <div>
            <h1 className="em-title">Vehicle Entry Management</h1>
            <p className="em-subtitle">
              CCTV cameras scan license plates automatically — entry is decided based on registration and schedule.
            </p>
          </div>
          <div className={`em-live-badge ${isLive ? '' : rtspCameras.length === 0 ? 'offline' : 'connecting'}`}>
            <span className="em-live-dot" />
            {rtspCameras.length === 0 ? 'NO CAMERAS' : isLive ? 'LIVE' : 'CONNECTING…'}
          </div>
        </div>

        {/* Main grid */}
        <div className="em-grid">

          {/* CCTV Camera card */}
          <div className="em-card em-camera-card">
            <div className="em-card-head">
              <span className="em-card-label">
                <Video size={15} /> IP Camera (CCTV)
              </span>
            </div>

            {/* Viewport */}
            <div className="em-viewport" style={{ background: '#0d1117', minHeight: 340, position: 'relative' }}>
              {rtspCameras.length > 0 ? (
                <div style={{ position: 'relative', width: '100%', minHeight: 300 }}>
                  {rtspCameras.map((cam, idx) => (
                    <div
                      key={cam.id}
                      style={{ display: rtspActiveCamId === cam.id ? 'block' : 'none', width: '100%', ...(idx === 0 ? {} : { position: 'absolute', inset: 0 }) }}
                    >
                      <canvas
                        ref={el => registerCanvas(cam.id, el)}
                        style={{ width: '100%', display: 'block', background: '#000', minHeight: 300 }}
                      />
                    </div>
                  ))}
                  {rtspActiveCam && !rtspActiveCam.streamConnected && rtspActiveCam.wsActive && (
                    <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.7)', gap: 12, pointerEvents: 'none' }}>
                      <div className="em-spinner" style={{ width: 36, height: 36, borderWidth: 3, borderTopColor: '#60a5fa', borderColor: 'rgba(96,165,250,0.15)' }} />
                      <p style={{ color: '#93c5fd', fontSize: 13, margin: 0 }}>{rtspActiveCam.statusMsg || 'Connecting…'}</p>
                    </div>
                  )}
                  <div style={{ position: 'absolute', top: 12, left: 12, background: 'rgba(0,0,0,0.65)', color: '#fff', padding: '4px 10px', borderRadius: 6, fontSize: 11, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 5, pointerEvents: 'none' }}>
                    <span style={{ width: 7, height: 7, borderRadius: '50%', background: rtspActiveCam?.streamConnected ? '#22c55e' : '#f59e0b', display: 'inline-block' }} />
                    {rtspActiveCam?.name || 'IP Camera'}
                  </div>
                  {rtspCameras.length > 1 && (
                    <div style={{ position: 'absolute', top: 12, right: 12, background: 'rgba(0,0,0,0.65)', color: '#60a5fa', padding: '4px 10px', borderRadius: 6, fontSize: 11, fontWeight: 600, pointerEvents: 'none' }}>
                      {rtspCameras.filter(c => c.streamConnected).length}/{rtspCameras.length} live
                    </div>
                  )}
                </div>
              ) : (
                <div className="em-cam-off">
                  <Wifi size={52} style={{ color: '#374151' }} />
                  <p>No entry cameras configured. Add cameras in Device Management.</p>
                </div>
              )}
            </div>

            {/* Camera thumbnail strip */}
            {rtspCameras.length > 0 && (
              <div className="em-cam-thumbnails" style={{ marginTop: 0, borderTop: '1px solid #1e2235' }}>
                {rtspCameras.map(cam => (
                  <div
                    key={`rthumb-${cam.id}`}
                    className={`em-cam-thumb ${rtspActiveCamId === cam.id ? 'active' : ''}`}
                    onClick={() => setRtspActiveCam(cam.id)}
                    style={{ position: 'relative' }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: rtspActiveCamId === cam.id ? '#60A5FA' : '#5A5F72' }}>
                      <Wifi size={20} />
                    </div>
                    <div className="em-cam-thumb-label">{cam.name}</div>
                    <span style={{ position: 'absolute', top: 4, left: 4, width: 6, height: 6, borderRadius: '50%', background: cam.streamConnected ? '#22c55e' : cam.wsActive ? '#f59e0b' : '#6b7280', display: 'inline-block' }} />
                  </div>
                ))}
              </div>
            )}

            {/* Controls */}
            <div className="em-controls">
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', width: '100%' }}>
                <div className={`em-autoscan-status ${isLive ? 'scanning' : ''}`} style={{ flex: 1 }}>
                  {rtspCameras.length === 0
                    ? <><Video size={13} /> No entry cameras configured — add them in Device Management</>
                    : isLive
                      ? <><Zap size={13} /> {rtspCameras.filter(c => c.streamConnected).length}/{rtspCameras.length} camera{rtspCameras.length !== 1 ? 's' : ''} live</>
                      : <><div className="em-spinner" style={{ borderTopColor: '#3b82f6', borderColor: 'rgba(59,130,246,.2)' }} /> Connecting…</>
                  }
                </div>
              </div>
            </div>
          </div>

          {/* Right panel */}
          <div className="em-right">
            {(result && result.length > 0) ? (
              <div className="em-results-stack">
                {result.map((r, idx) => (
                  <ResultCard key={`result-${idx}-${r.plate_number}`} result={r} offices={offices} onPassCreated={handlePassCreated} />
                ))}
              </div>
            ) : (
              <ResultCard result={null} offices={offices} onPassCreated={handlePassCreated} />
            )}

            {/* Recent scans */}
            <div className="em-card">
              <div className="em-card-head">
                <span className="em-card-label"><ClipboardList size={14} /> Recent Scans</span>
                <span className="em-logs-count">{logs.length}</span>
              </div>
              {logs.length === 0 ? (
                <p className="em-log-empty">No scans yet today.</p>
              ) : (
                <div className="em-log-list">
                  {logs.map((log, i) => {
                    const m = getMeta(log.status)
                    return (
                      <div key={log.id ?? i} className="em-log-item">
                        <span className={`em-log-dot ${m.logCls}`} />
                        <span className="em-log-plate">{log.plate_number || '—'}</span>
                        <span className={`em-log-badge ${m.logCls}`}>{m.label}</span>
                        {(log.on_duty_guard_name || log.scanned_by_name) && (
                          <span
                            className="em-log-guard"
                            title={log.on_duty_guard_name ? 'Guard on duty' : 'Scanned by'}
                          >
                            {log.on_duty_guard_name || log.scanned_by_name}
                          </span>
                        )}
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
    </AdminLayout>
  )
}
