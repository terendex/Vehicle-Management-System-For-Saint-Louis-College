import { useState, useRef, useEffect } from 'react'
import { useParams } from 'react-router-dom'
import Webcam from 'react-webcam'
import {
  Camera, CameraOff, ScanLine, Upload, RotateCcw,
  CheckCircle, XCircle, Clock, HelpCircle, AlertTriangle,
  ClipboardList, UserPlus, X, Video, Plus,
  Shield, Wifi, Zap, User as UserIcon, DoorOpen
} from 'lucide-react'
import { toast } from "sonner"
import { formatDistanceToNow } from 'date-fns'
import SecurityLayout from '../../components/Layout/SecurityLayout'
import { getAccessLogs, getOffices, createVisitorPass, scanPlate, overrideEntry, logExit } from '../../api/scanning'
import { camerasApi } from '../../api/cameras'
import { getSystemSettings } from '../../api/vehicles'
import { createViolation } from '../../api/violations'
import useAuthStore from '../../stores/authStore'
import { useScanStream } from '../../hooks/useScanStream'
import { useMultiRtspStream } from '../../hooks/useMultiRtspStream'
import './SecurityEntryManagement.css'

// ─── OnDutyPanel ──────────────────────────────────────────────────────────────
function OnDutyPanel({ guard, gate }) {
  if (!guard) return null
  const gateLabel = gate ? `Gate ${gate}` : 'Main Gate'
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
  pending:    { label: 'Awaiting Approval',      Icon: Clock,         cls: 'pending',    logCls: 'pending'    },
  unknown:    { label: 'Visitor / Unregistered', Icon: HelpCircle,    cls: 'visitor',    logCls: 'visitor'    },
  no_pass:    { label: 'No Visitor Pass',        Icon: AlertTriangle, cls: 'visitor',    logCls: 'visitor'    },
  disabled:   { label: 'Access Disabled',        Icon: XCircle,       cls: 'denied',     logCls: 'denied'     },
  unreadable: { label: 'Unreadable Plate',       Icon: AlertTriangle, cls: 'visitor',    logCls: 'visitor'    },
  cooldown:   { label: 'Recently Scanned',       Icon: Clock,         cls: 'pending',    logCls: 'pending'    },
  exited:     { label: 'Exited',                 Icon: CheckCircle,   cls: 'authorized', logCls: 'exited'     },
}

function getMeta(status) {
  return STATUS_META[status] ?? STATUS_META.unknown
}

function timeAgo(ts) {
  try { return formatDistanceToNow(new Date(ts), { addSuffix: true }) }
  catch { return '' }
}

// ─── printVisitorSlip ─────────────────────────────────────────────────────────
function printVisitorSlip({ plate, purpose, officeName, guardName, issuedAt, expiresAt, duration }) {
  const w = window.open('', '_blank', 'width=320,height=520')
  if (!w) { return }
  const fmt = (d) => d ? new Date(d).toLocaleString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'
  w.document.write(`<!DOCTYPE html><html><head>
<meta charset="utf-8"/>
<title>Visitor Slip</title>
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
  w.document.close()
  w.focus()
  setTimeout(() => { w.print(); w.close() }, 400)
}

// ─── VisitorPassModal ─────────────────────────────────────────────────────────
function VisitorPassModal({ plate, offices, onClose, onCreated, guardName }) {
  const [officeId, setOfficeId]       = useState('')
  const [purpose, setPurpose]         = useState('')
  const [duration, setDuration]       = useState(60)
  const [loading, setLoading]         = useState(false)
  const [createdPass, setCreatedPass] = useState(null)

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!officeId || !purpose.trim()) { toast.error('Please fill in all fields.'); return }
    setLoading(true)
    try {
      const res = await createVisitorPass({
        plate_number: plate,
        office: officeId,
        purpose,
        allowed_duration: duration,
      })
      const pass = res.data
      setCreatedPass(pass)
      toast.success('Visitor pass created.')
      onCreated()
      const officeName = offices.find(o => String(o.id) === String(officeId))?.name
      printVisitorSlip({
        plate,
        purpose,
        officeName,
        guardName,
        issuedAt: pass.entered_at,
        expiresAt: pass.expires_at,
        duration,
      })
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Failed to create visitor pass.')
    } finally {
      setLoading(false)
    }
  }

  if (createdPass) {
    return (
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
            <button
              type="button" className="em-btn em-btn-secondary"
              onClick={() => {
                const officeName = offices.find(o => String(o.id) === String(officeId))?.name
                printVisitorSlip({ plate, purpose, officeName, guardName, issuedAt: createdPass.entered_at, expiresAt: createdPass.expires_at, duration })
              }}
            >
              Print Again
            </button>
            <button type="button" className="em-btn em-btn-primary" onClick={onClose}>Done</button>
          </div>
        </div>
      </div>
    )
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
            <div className="em-field">
              <label className="em-label">Allowed Duration (minutes)</label>
              <input
                className="em-input"
                type="number"
                min={1}
                max={480}
                value={duration}
                onChange={(e) => setDuration(Math.max(1, parseInt(e.target.value) || 60))}
              />
              <span style={{ fontSize: 11, color: '#6b7280', marginTop: 3, display: 'block' }}>
                Default is 60 min. Guard can extend after issuance.
              </span>
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

// ─── OverrideModal ────────────────────────────────────────────────────────────
function OverrideModal({ plate, onClose, onOverridden }) {
  const [reason, setReason]   = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!reason.trim()) { toast.error('Please provide a reason for the override.'); return }
    setLoading(true)
    try {
      await overrideEntry({ plate_number: plate, reason })
      toast.success(`Entry override logged for ${plate}.`)
      onOverridden()
      onClose()
    } catch (err) {
      toast.error(err?.response?.data?.error || 'Override failed.')
    } finally {
      setLoading(false)
    }
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
              <textarea
                className="em-textarea"
                placeholder="e.g. Event day — general admission, special clearance…"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                rows={3}
                required
              />
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

// ─── IssueViolationModal ──────────────────────────────────────────────────────
function IssueViolationModal({ initialPlate = '', onClose }) {
  const [plate, setPlate]       = useState(initialPlate)
  const [type, setType]         = useState('no_sticker')
  const [notes, setNotes]       = useState('')
  const [evidence, setEvidence] = useState(null)
  const [preview, setPreview]   = useState(null)
  const [loading, setLoading]   = useState(false)
  const fileRef                 = useRef(null)

  const handleFile = (file) => {
    if (!file || !file.type.startsWith('image/')) { toast.error('Please select an image file.'); return }
    setEvidence(file)
    setPreview(URL.createObjectURL(file))
  }

  const removeEvidence = () => {
    if (preview) URL.revokeObjectURL(preview)
    setEvidence(null); setPreview(null)
    if (fileRef.current) fileRef.current.value = ''
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!plate.trim()) { toast.error('Plate number is required.'); return }
    setLoading(true)
    try {
      await createViolation({
        plate_number: plate.trim().toUpperCase(),
        violation_type: type,
        notes,
        ...(evidence ? { evidence } : {}),
      })
      toast.success(`Violation issued for ${plate.trim().toUpperCase()}.`)
      onClose()
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Failed to issue violation.')
    } finally {
      setLoading(false)
    }
  }

  const inputStyle = { width: '100%', padding: '7px 10px', border: '1.5px solid #d1d5db', borderRadius: 7, fontSize: 13, boxSizing: 'border-box', fontFamily: 'inherit' }

  return (
    <div className="em-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="em-modal">
        <div className="em-modal-head">
          <span className="em-modal-title"><AlertTriangle size={17} /> Issue Violation</span>
          <button className="em-modal-close" onClick={onClose}><X size={15} /></button>
        </div>
        <form onSubmit={handleSubmit}>
          <div className="em-modal-body">
            <div className="em-field">
              <label className="em-label">License Plate *</label>
              <input className="em-input" value={plate} onChange={e => setPlate(e.target.value)} placeholder="e.g. ABC 123" required />
            </div>
            <div className="em-field">
              <label className="em-label">Violation Type</label>
              <select className="em-select" value={type} onChange={e => setType(e.target.value)}>
                <option value="no_sticker">No Sticker</option>
                <option value="expired_registration">Expired Registration</option>
                <option value="unauthorized">Unauthorized Entry</option>
                <option value="other">Other</option>
              </select>
            </div>
            <div className="em-field">
              <label className="em-label">Notes</label>
              <textarea className="em-textarea" placeholder="Optional additional details…" value={notes} onChange={e => setNotes(e.target.value)} rows={2} />
            </div>
            <div className="em-field">
              <label className="em-label">Screenshot Evidence</label>
              {preview ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  <img src={preview} alt="preview" style={{ maxWidth: '100%', maxHeight: 140, borderRadius: 8, border: '1.5px solid #E2E6EE', objectFit: 'contain' }} />
                  <button type="button" onClick={removeEvidence} style={{ alignSelf: 'flex-start', display: 'flex', alignItems: 'center', gap: 4, padding: '3px 9px', borderRadius: 6, border: '1.5px solid #FECACA', background: '#FEF2F2', color: '#DC2626', fontSize: 11, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit' }}>
                    <X size={11} /> Remove
                  </button>
                </div>
              ) : (
                <div
                  onClick={() => fileRef.current?.click()}
                  onDrop={(e) => { e.preventDefault(); handleFile(e.dataTransfer.files?.[0]) }}
                  onDragOver={e => e.preventDefault()}
                  style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, padding: 16, border: '2px dashed #C7CAD9', borderRadius: 10, background: '#F8F9FC', cursor: 'pointer' }}
                >
                  <Upload size={20} style={{ color: '#9ca3af' }} />
                  <p style={{ margin: 0, fontSize: 12, color: '#6b7280' }}>Drop image or <span style={{ color: '#2A2B61', fontWeight: 600, textDecoration: 'underline' }}>click to browse</span></p>
                  <input ref={fileRef} type="file" accept="image/*" style={{ display: 'none' }} onChange={e => handleFile(e.target.files?.[0])} />
                </div>
              )}
            </div>
          </div>
          <div className="em-modal-foot">
            <button type="button" className="em-btn em-btn-secondary" onClick={onClose}>Cancel</button>
            <button type="submit" className="em-btn em-btn-primary" disabled={loading} style={{ background: '#dc2626', borderColor: '#dc2626' }}>
              {loading ? <><div className="em-spinner" /> Issuing…</> : 'Issue Violation'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ─── ResultCard ───────────────────────────────────────────────────────────────
function ResultCard({ result, offices, onPassCreated, onOverride, guardName }) {
  const [showModal, setShowModal]         = useState(false)
  const [showOverride, setShowOverride]   = useState(false)
  const [showViolation, setShowViolation] = useState(false)

  if (!result) {
    return (
      <div className="em-card em-result">
        <div className="em-result-banner idle">
          <div className="em-result-icon idle"><ScanLine size={20} /></div>
          <div className="em-result-text">
            <p className="em-result-status" style={{ color: '#9BA3BF' }}>Awaiting scan</p>
            <p className="em-result-plate" style={{ color: '#C8CCDE', fontSize: 15, letterSpacing: 1 }}>— — — — —</p>
          </div>
        </div>
        <p className="em-idle-hint">Point the camera at a license plate to start live scanning.</p>
      </div>
    )
  }

  const { Icon, label, cls } = getMeta(result.status)
  const owner      = result.vehicle?.user
  const reg        = result.registration
  const vehicle    = result.vehicle
  const isVisitor  = result.status === 'unknown' || result.status === 'no_pass'
  const isDeniable = result.status === 'denied' || result.status === 'wrong_day' || result.status === 'disabled'
  const todayName  = new Date().toLocaleDateString('en-US', { weekday: 'long' })

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
                {result.already_inside && (
                  <div className="em-result-row">
                    <span className="em-result-row-label">Warning</span>
                    <span className="em-violation-pill" style={{ background: '#fef9c3', border: '1px solid #fde68a', color: '#92400e' }}>
                      <AlertTriangle size={10} /> Already inside — no exit logged
                    </span>
                  </div>
                )}
              </div>
            </>
          )}

          {/* Visitor / unknown: minimal info */}
          {isVisitor && (
            <>
              {owner?.full_name && (
                <div className="em-result-rows">
                  <div className="em-result-row">
                    <span className="em-result-row-label">Owner</span>
                    <span className="em-result-row-value">{owner.full_name}</span>
                  </div>
                </div>
              )}
              {result.already_inside && (
                <div className="em-result-rows">
                  <div className="em-result-row">
                    <span className="em-result-row-label">Warning</span>
                    <span className="em-violation-pill" style={{ background: '#fef9c3', border: '1px solid #fde68a', color: '#92400e' }}>
                      <AlertTriangle size={10} /> Already inside — no exit logged
                    </span>
                  </div>
                </div>
              )}
            </>
          )}

          <div style={{ display: 'flex', gap: 6, marginTop: 4, flexDirection: 'column' }}>
            {isVisitor && (
              <button className="em-btn em-btn-secondary" style={{ width: '100%' }} onClick={() => setShowModal(true)}>
                <UserPlus size={14} /> Create Visitor Pass
              </button>
            )}
            {isDeniable && (
              <button
                className="em-btn"
                style={{ width: '100%', background: '#d97706', color: '#fff', border: 'none', justifyContent: 'center' }}
                onClick={() => setShowOverride(true)}
              >
                <Shield size={14} /> Override Entry
              </button>
            )}
            <button
              className="em-btn"
              style={{ width: '100%', background: '#dc2626', color: '#fff', border: 'none', justifyContent: 'center' }}
              onClick={() => setShowViolation(true)}
            >
              <AlertTriangle size={14} /> Issue Violation
            </button>
          </div>
        </div>
      </div>

      {showModal && (
        <VisitorPassModal
          plate={result.plate_number}
          offices={offices}
          onClose={() => setShowModal(false)}
          onCreated={onPassCreated}
          guardName={guardName}
        />
      )}

      {showOverride && (
        <OverrideModal
          plate={result.plate_number}
          onClose={() => setShowOverride(false)}
          onOverridden={() => { onOverride?.(); setShowOverride(false) }}
        />
      )}

      {showViolation && (
        <IssueViolationModal
          initialPlate={result.plate_number || ''}
          onClose={() => setShowViolation(false)}
        />
      )}
    </>
  )
}

// ─── BBoxLegend ───────────────────────────────────────────────────────────────
const BBOX_LEGEND = [
  { color: '#00ff88', label: 'Plate / Vehicle' },
  { color: '#3b82f6', label: 'Motor'           },
]

function BBoxLegend() {
  return (
    <div className="em-bbox-legend">
      {BBOX_LEGEND.map(({ color, label }) => (
        <div key={label} className="em-bbox-legend-item">
          <span className="em-bbox-legend-swatch" style={{ background: color }} />
          <span className="em-bbox-legend-label">{label}</span>
        </div>
      ))}
    </div>
  )
}

// ─── SecurityEntryManagement (page) ──────────────────────────────────────────
export default function SecurityEntryManagement() {
  const { user } = useAuthStore()
  const { gate } = useParams()          // e.g. "1" from /security/gate/1/entries
  const gateId = gate ? `gate${gate}` : 'main'  // stored as "gate1", "gate2", etc.

  // Use the same JWT access token as the admin page — no prompt needed
  const token = localStorage.getItem('access_token') || ''

  const [mode, setMode]               = useState('camera')
  const [cameraOn, setCameraOn]       = useState(false)
  const [uploadFile, setUploadFile]   = useState(null)
  const [dragOver, setDragOver]       = useState(false)
  const [cooldown, setCooldown]       = useState(false)
  const [displayResult, setDisplayResult] = useState(null)
  const [logs, setLogs]               = useState([])
  const [offices, setOffices]         = useState([])
const [webcams, setWebcams]           = useState([{ id: 1, name: 'Main Gate - Front' }])
  const [activeCamId, setActiveCamId]   = useState(1)
  const [eventMode, setEventMode]       = useState(false)  // read-only, fetched from backend
  const [exitPlate, setExitPlate]       = useState('')
  const [exitResult, setExitResult]     = useState(null)
  const [exitLoading, setExitLoading]   = useState(false)

  const DENIED_STATUSES = ['denied', 'wrong_day', 'disabled']

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
      toast.success(
        dur != null
          ? `Exit recorded for ${plate} — inside for ${dur} min.`
          : `Exit recorded for ${plate}.`
      )
      setLogs((prev) => [{
        id: Date.now(),
        plate_number:    plate,
        status:          'exited',
        scanned_at:      new Date().toISOString(),
        scanned_by_name: user?.full_name || null,
      }, ...prev].slice(0, 20))
      getAccessLogs({ limit: 20 }).then(r => setLogs(r.data?.results ?? r.data ?? [])).catch(() => {})
    } catch (err) {
      toast.error(err?.response?.data?.error || 'Failed to record exit.')
    } finally {
      setExitLoading(false)
    }
  }

  const autoOverride = async (plate) => {
    try {
      await overrideEntry({ plate_number: plate, reason: 'Event mode — automatic override' })
    } catch {
      // silent; event mode overrides are best-effort
    }
  }


  const webcamRefs = useRef({})
  const fileInputRef = useRef(null)

  const {
    scanning,
    connected,
    results: wsResults,
    flash,
    activeTracks,
    videoRef,
    canvasRef,
  } = useScanStream(token, cameraOn, gateId)

  const {
    cameras:         rtspCameras,
    activeCamId:     rtspActiveCamId,
    setActiveCamId:  setRtspActiveCam,
    activeCam:       rtspActiveCam,
    addCamera:       addRtspCamera,
    disconnectAll:   disconnectAllRtsp,
    results:         rtspResults,
    flash:           rtspFlash,
    registerCanvas:  registerRtspCanvas,
  } = useMultiRtspStream(token)

  // ── Data fetching ───────────────────────────────────────────────────────────
  useEffect(() => {
    getAccessLogs({ limit: 20 }).then((r) => setLogs(r.data?.results ?? r.data ?? [])).catch(() => {})
    getOffices().then((r) => setOffices(r.data?.results ?? r.data ?? [])).catch(() => {})
getSystemSettings().then(r => setEventMode(!!r.data.event_mode_entry)).catch(() => {})
    const settingsPoll = setInterval(() => {
      getSystemSettings().then(r => setEventMode(!!r.data.event_mode_entry)).catch(() => {})
    }, 15000)
    return () => clearInterval(settingsPoll)
  }, [])

  // ── WS plate results → display + log ───────────────────────────────────────
  useEffect(() => {
    if (!wsResults?.length) return
    setDisplayResult(wsResults)
    setCooldown(true)
    setTimeout(() => setCooldown(false), 5000)
    if (eventMode) {
      wsResults.forEach(r => { if (DENIED_STATUSES.includes(r.status)) autoOverride(r.plate_number) })
    }
    setLogs((prev) => {
      const newLogs = wsResults.map(r => ({
        id: Date.now() + Math.random(),
        plate_number:      r.plate_number,
        status:            r.status,
        scanned_at:        new Date().toISOString(),
        scanned_by_name:   user?.full_name || null,
        vehicle_owner_name: r.vehicle?.user?.full_name || null,
      }))
      return [...newLogs, ...prev].slice(0, 20)
    })
  }, [wsResults]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── RTSP results (any camera) → display + log ──────────────────────────────
  useEffect(() => {
    if (!rtspResults?.length) return
    setDisplayResult(rtspResults)
    setCooldown(true)
    setTimeout(() => setCooldown(false), 5000)
    if (eventMode) {
      rtspResults.forEach(r => { if (DENIED_STATUSES.includes(r.status)) autoOverride(r.plate_number) })
    }
    setLogs((prev) => {
      const newLogs = rtspResults.map(r => ({
        id: Date.now() + Math.random(),
        plate_number:      r.plate_number,
        status:            r.status,
        scanned_at:        new Date().toISOString(),
        scanned_by_name:   user?.full_name || null,
        vehicle_owner_name: r.vehicle?.user?.full_name || null,
      }))
      return [...newLogs, ...prev].slice(0, 20)
    })
  }, [rtspResults]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Load entry cameras from Device Management (DB) when RTSP mode is opened ──
  useEffect(() => {
    if (mode !== 'rtsp') return
    if (rtspCameras.length > 0) return
    camerasApi.list({ assignment: 'entry' })
      .then(cams => cams.forEach(c => addRtspCamera(c.name, c.rtsp_url)))
      .catch(() => {})
  }, [mode]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Handlers ───────────────────────────────────────────────────────────────────
  const handleRtspDisconnectAll = () => {
    disconnectAllRtsp()
    setCooldown(false)
    setDisplayResult(null)
  }

  const addWebcam = () => {
    if (webcams.length >= 4) { toast.error('Maximum of 4 cameras allowed.'); return }
    const id = Date.now()
    setWebcams(prev => [...prev, { id, name: `Angle ${prev.length + 1}` }])
  }

  const removeWebcam = (id) => {
    setWebcams(prev => {
      const next = prev.filter(c => c.id !== id)
      if (activeCamId === id && next.length > 0) setActiveCamId(next[0].id)
      return next
    })
  }

  const stopCamera = () => {
    setCameraOn(false)
    setCooldown(false)
    setDisplayResult(null)
  }

  const stopAllAndSwitchMode = (nextMode) => {
    if (nextMode !== 'camera') { setCameraOn(false); setCooldown(false) }
    if (nextMode !== 'rtsp') { disconnectAllRtsp(); setDisplayResult(null) }
    if (nextMode !== 'upload') { /* nothing */ }
  }

  const handleFileChange = (file) => {
    if (!file || !file.type.startsWith('image/')) { toast.error('Please select an image file.'); return }
    setUploadFile({ file, url: URL.createObjectURL(file) })
    setDisplayResult(null)
  }

  const handleDrop = (e) => {
    e.preventDefault()
    setDragOver(false)
    handleFileChange(e.dataTransfer.files?.[0])
  }

  const resetUpload = () => {
    if (uploadFile?.url) URL.revokeObjectURL(uploadFile.url)
    setUploadFile(null)
    setDisplayResult(null)
  }

  const handleUploadScan = async () => {
    if (!uploadFile) return
    try {
      const res = await scanPlate(uploadFile.file, gateId)
      const data = res.data?.results ?? res.data ?? []
      if (data.length) {
        setDisplayResult(data)
        setCooldown(true)
        setTimeout(() => setCooldown(false), 5000)
        setLogs((prev) => {
          const newLogs = data.map(r => ({
            id: Date.now() + Math.random(),
            plate_number:      r.plate_number,
            status:            r.status,
            scanned_at:        new Date().toISOString(),
            scanned_by_name:   user?.full_name || null,
            vehicle_owner_name: r.vehicle?.user?.full_name || null,
          }))
          return [...newLogs, ...prev].slice(0, 20)
        })
      } else {
        toast.info('No plate detected in this image.')
      }
    } catch {
      toast.error('Upload scan failed')
    }
  }

  const handlePassCreated = () => {
    getAccessLogs({ limit: 20 }).then((r) => setLogs(r.data?.results ?? r.data ?? [])).catch(() => {})
  }

  // ── Render ──────────────────────────────────────────────────────────────────
  return (
    <SecurityLayout fillHeight>
      <div className="em-page">

        {/* Header */}
        <div className="em-header">
          <div>
            <h1 className="em-title">
              Vehicle Entry Management
              {gate && <span className="em-gate-badge">Gate {gate}</span>}
            </h1>
            <p className="em-subtitle">
              Scan license plates — entry is decided automatically based on registration and schedule.
            </p>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <span
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 6,
                padding: '5px 13px', borderRadius: 20, border: '1.5px solid',
                fontSize: 11.5, fontWeight: 700, userSelect: 'none',
                background: eventMode ? '#16a34a' : '#f3f4f6',
                color:      eventMode ? '#fff'    : '#9ca3af',
                borderColor: eventMode ? '#16a34a' : '#e5e7eb',
                letterSpacing: 0.3,
              }}
              title={eventMode ? 'Event mode is ON — denied scans are auto-overridden. Controlled by admin.' : 'Event mode is OFF. Controlled by admin.'}
            >
              <Shield size={13} />
              {eventMode ? 'EVENT MODE ON' : 'EVENT MODE OFF'}
            </span>
            {cameraOn ? (
              <div className={`em-live-badge ${connected ? '' : 'connecting'}`}>
                <span className="em-live-dot" />
                {connected ? 'LIVE' : 'CONNECTING…'}
              </div>
            ) : (
              <div className="em-live-badge offline">
                <span className="em-live-dot" /> OFFLINE
              </div>
            )}
          </div>
        </div>

        {/* Main grid */}
        <div className="em-grid">

          {/* Camera / Upload card */}
          <div className="em-card em-camera-card">
            <div className="em-card-head">
              <span className="em-card-label">
                <Camera size={15} />
                {mode === 'camera' ? `Live Camera Feed` : mode === 'rtsp' ? `IP Cameras (${rtspCameras.length})` : 'Upload Plate Image'}
              </span>
              <div className="em-mode-toggle">
                <button
                  className={`em-mode-btn ${mode === 'camera' ? 'active' : ''}`}
                  onClick={() => { stopAllAndSwitchMode('camera'); setMode('camera') }}
                >Camera</button>
                <button
                  className={`em-mode-btn ${mode === 'rtsp' ? 'active' : ''}`}
                  onClick={() => { stopAllAndSwitchMode('rtsp'); setMode('rtsp') }}
                ><Wifi size={12} style={{ marginRight: 4 }} />RTSP</button>
                <button
                  className={`em-mode-btn ${mode === 'upload' ? 'active' : ''}`}
                  onClick={() => { stopAllAndSwitchMode('upload'); resetUpload(); setMode('upload') }}
                >Upload</button>
              </div>
            </div>

            {/* Viewport */}
            {mode === 'camera' ? (
              <div className="em-viewport" style={{ background: cameraOn ? '#1A1D2E' : '#08090F', padding: cameraOn ? '10px 1.25rem' : 0, minHeight: cameraOn ? 340 : undefined }}>
                {cameraOn ? (
                  <div className="em-multi-cam-container">
                    <div className="em-primary-cam">
                      {webcams.map(cam => (
                        <div key={`primary-${cam.id}`} style={{ display: activeCamId === cam.id ? 'flex' : 'none', width: '100%', height: '100%', flex: 1 }}>
                          <Webcam
                            ref={(el) => {
                              webcamRefs.current[cam.id] = el
                              if (cam.id === activeCamId) videoRef.current = el
                            }}
                            audio={false}
                            screenshotFormat="image/jpeg"
                            screenshotQuality={0.95}
                            className="em-video"
                            videoConstraints={{ facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } }}
                            onUserMediaError={(err) => toast.error(`Camera error: ${err?.message || err?.name || 'Could not access camera'}`)}
                            style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
                          />
                        </div>
                      ))}
                      {cameraOn && <BBoxLegend />}

                      <canvas
                        ref={canvasRef}
                        style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', pointerEvents: 'none' }}
                      />
                      <div style={{ position: 'absolute', top: 12, left: 12, background: 'rgba(0,0,0,0.6)', color: 'white', padding: '4px 10px', borderRadius: 6, fontSize: 11, fontWeight: 600 }}>
                        {webcams.find(c => c.id === activeCamId)?.name}
                      </div>
                    </div>

                    <div className="em-cam-thumbnails">
                      {webcams.map(cam => (
                        <div
                          key={`thumb-${cam.id}`}
                          className={`em-cam-thumb ${activeCamId === cam.id ? 'active' : ''}`}
                          onClick={() => setActiveCamId(cam.id)}
                        >
                          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: activeCamId === cam.id ? '#60A5FA' : '#5A5F72' }}>
                            <Video size={24} />
                          </div>
                          <div className="em-cam-thumb-label">{cam.name}</div>
                          {webcams.length > 1 && (
                            <div className="em-cam-thumb-actions">
                              <button className="em-cam-delete" onClick={(e) => { e.stopPropagation(); removeWebcam(cam.id) }} title="Remove angle">
                                <X size={12} />
                              </button>
                            </div>
                          )}
                        </div>
                      ))}
                      {webcams.length < 4 && (
                        <div
                          className="em-cam-thumb"
                          style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#2A304D', border: '1px dashed #4A5070', cursor: 'pointer' }}
                          onClick={addWebcam}
                          title="Add another angle"
                        >
                          <Plus size={20} color="#9BA3BF" />
                        </div>
                      )}
                    </div>
                  </div>
                ) : (
                  <div className="em-cam-off">
                    <CameraOff size={52} />
                    <p>Camera is off — press Start Camera to begin</p>
                  </div>
                )}
              </div>
            ) : mode === 'rtsp' ? (
              <>
                {/* Main canvas area */}
                <div className="em-viewport" style={{ background: '#0d1117', minHeight: 340, position: 'relative' }}>
                  {rtspCameras.length > 0 ? (
                    <div style={{ position: 'relative', width: '100%', minHeight: 300 }}>
                      {/* Stacked canvases — only the active camera is visible */}
                      {rtspCameras.map((cam, idx) => (
                        <div
                          key={cam.id}
                          style={{ display: rtspActiveCamId === cam.id ? 'block' : 'none', width: '100%', ...(idx === 0 ? {} : { position: 'absolute', inset: 0 }) }}
                        >
                          <canvas
                            ref={el => registerRtspCanvas(cam.id, el)}
                            style={{ width: '100%', display: 'block', background: '#000', minHeight: 300 }}
                          />
                        </div>
                      ))}
                      {/* Connecting overlay */}
                      {rtspActiveCam && !rtspActiveCam.streamConnected && rtspActiveCam.wsActive && (
                        <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.7)', gap: 12, pointerEvents: 'none' }}>
                          <div className="em-spinner" style={{ width: 36, height: 36, borderWidth: 3, borderTopColor: '#60a5fa', borderColor: 'rgba(96,165,250,0.15)' }} />
                          <p style={{ color: '#93c5fd', fontSize: 13, margin: 0 }}>{rtspActiveCam.statusMsg || 'Connecting…'}</p>
                        </div>
                      )}
                      {/* Camera name badge */}
                      <div style={{ position: 'absolute', top: 12, left: 12, background: 'rgba(0,0,0,0.65)', color: '#fff', padding: '4px 10px', borderRadius: 6, fontSize: 11, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 5, pointerEvents: 'none' }}>
                        <span style={{ width: 7, height: 7, borderRadius: '50%', background: rtspActiveCam?.streamConnected ? '#22c55e' : '#f59e0b', display: 'inline-block' }} />
                        {rtspActiveCam?.name || 'IP Camera'}
                      </div>
                      {/* Camera count badge */}
                      {rtspCameras.length > 1 && (
                        <div style={{ position: 'absolute', top: 12, right: 12, background: 'rgba(0,0,0,0.65)', color: '#60a5fa', padding: '4px 10px', borderRadius: 6, fontSize: 11, fontWeight: 600, pointerEvents: 'none' }}>
                          {rtspCameras.filter(c => c.streamConnected).length}/{rtspCameras.length} live
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="em-cam-off">
                      <Wifi size={52} style={{ color: '#374151' }} />
                      <p>Add RTSP cameras below to begin monitoring</p>
                    </div>
                  )}
                </div>

                {/* Camera thumbnail strip (when 2+ cameras) */}
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
                        {/* Status dot */}
                        <span style={{ position: 'absolute', top: 4, left: 4, width: 6, height: 6, borderRadius: '50%', background: cam.streamConnected ? '#22c55e' : cam.wsActive ? '#f59e0b' : '#6b7280', display: 'inline-block' }} />
                      </div>
                    ))}
                  </div>
                )}
              </>
            ) : (
              uploadFile ? (
                <div className="em-upload-preview">
                  <img src={uploadFile.url} alt="Plate capture" style={{ width: '100%', height: '100%', objectFit: 'contain' }} />
                </div>
              ) : (
                <div
                  className={`em-upload-zone ${dragOver ? 'drag-over' : ''}`}
                  onClick={() => fileInputRef.current?.click()}
                  onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
                  onDragLeave={() => setDragOver(false)}
                  onDrop={handleDrop}
                >
                  <Upload size={38} />
                  <p>Click to upload or drag &amp; drop</p>
                  <span>JPG · PNG · WEBP</span>
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept="image/*"
                    style={{ display: 'none' }}
                    onChange={(e) => handleFileChange(e.target.files?.[0])}
                  />
                </div>
              )
            )}

            {/* Controls */}
            <div className="em-controls">
              {mode === 'camera' ? (
                cameraOn ? (
                  <>
                    <div className={`em-autoscan-status ${cooldown ? 'cooldown' : ''}`}>
                      {cooldown
                        ? <><Clock size={13} /> Cooldown…</>
                        : scanning
                          ? <><div className="em-spinner" style={{ borderTopColor: '#065F46', borderColor: 'rgba(6,95,70,.2)' }} /> Scanning…</>
                          : <><Zap size={13} /> Auto-scanning</>
                      }
                    </div>
                    <button className="em-btn em-btn-danger" onClick={stopCamera}>
                      <CameraOff size={15} /> Stop
                    </button>
                  </>
                ) : (
                  <button className="em-btn em-btn-primary em-btn-lg" onClick={() => setCameraOn(true)}>
                    <Camera size={17} /> Start Camera
                  </button>
                )
              ) : mode === 'rtsp' ? (
                <div style={{ display: 'flex', gap: 8, width: '100%', alignItems: 'center' }}>
                  {rtspCameras.length === 0 ? (
                    <div className="em-autoscan-status" style={{ flex: 1, justifyContent: 'center', color: '#6b7280' }}>
                      <Video size={13} /> No entry cameras configured — add them in Device Management
                    </div>
                  ) : (
                    <>
                      <div className={`em-autoscan-status ${rtspCameras.some(c => c.streamConnected) ? 'scanning' : ''}`} style={{ flex: 1 }}>
                        {rtspCameras.some(c => c.streamConnected)
                          ? <><Zap size={13} /> {rtspCameras.filter(c => c.streamConnected).length}/{rtspCameras.length} camera{rtspCameras.length !== 1 ? 's' : ''} live</>
                          : <><div className="em-spinner" style={{ borderTopColor: '#3b82f6', borderColor: 'rgba(59,130,246,.2)' }} /> Connecting…</>}
                      </div>
                      <button className="em-btn em-btn-danger" onClick={handleRtspDisconnectAll}>
                        <CameraOff size={14} /> Disconnect All
                      </button>
                    </>
                  )}
                </div>
              ) : (
                uploadFile ? (
                  <>
                    <button className="em-btn em-btn-primary em-btn-lg" onClick={handleUploadScan}>
                      <ScanLine size={17} /> Scan Plate
                    </button>
                    <button className="em-btn em-btn-secondary em-btn-lg" onClick={() => fileInputRef.current?.click()}>
                      <Upload size={15} /> Choose Different
                    </button>
                    <button className="em-btn em-btn-secondary em-btn-icon" onClick={resetUpload} title="Reset">
                      <RotateCcw size={15} />
                    </button>
                  </>
                ) : (
                  <button className="em-btn em-btn-secondary em-btn-lg" onClick={() => fileInputRef.current?.click()}>
                    <Upload size={15} /> Choose Image
                  </button>
                )
              )}
            </div>
          </div>

          {/* On Duty guard panel — shown below camera card */}
          <OnDutyPanel guard={user} gate={gate} />

          {/* Right panel */}
          <div className="em-right">
            {displayResult?.length > 0 ? (
              <div className="em-results-stack">
                {displayResult.map((r, idx) => (
                  <ResultCard
                    key={`result-${idx}-${r.plate_number}`}
                    result={r}
                    offices={offices}
                    onPassCreated={handlePassCreated}
                    onOverride={handlePassCreated}
                    guardName={user?.full_name}
                  />
                ))}
              </div>
            ) : (
              <ResultCard result={null} offices={offices} onPassCreated={handlePassCreated} onOverride={handlePassCreated} guardName={user?.full_name} />
            )}

            {/* Record Exit */}
            <div className="em-card">
              <div className="em-card-head">
                <span className="em-card-label"><CheckCircle size={14} /> Record Exit</span>
              </div>
              <div style={{ padding: '10px 14px' }}>
                <form onSubmit={handleRecordExit} style={{ display: 'flex', gap: 6 }}>
                  <input
                    value={exitPlate}
                    onChange={e => setExitPlate(e.target.value)}
                    placeholder="Plate number…"
                    style={{ flex: 1, padding: '6px 10px', border: '1.5px solid #E2E6EE', borderRadius: 7, fontSize: 13, outline: 'none' }}
                  />
                  <button
                    type="submit"
                    disabled={exitLoading || !exitPlate.trim()}
                    className="em-btn em-btn-primary"
                    style={{ padding: '6px 14px', fontSize: 12, whiteSpace: 'nowrap' }}
                  >
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
                    const ownerName = log.vehicle_owner_name
                    return (
                      <div key={log.id ?? i} className="em-log-item">
                        <span className={`em-log-dot ${m.logCls}`} />
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                            <span className="em-log-plate">{log.plate_number || '—'}</span>
                            <span className={`em-log-badge ${m.logCls}`}>{m.label}</span>
                          </div>
                          {(ownerName || log.scanned_by_name) && (
                            <div style={{ fontSize: 10, color: '#9ca3af', marginTop: 1, display: 'flex', gap: 8 }}>
                              {ownerName && <span>Owner: {ownerName}</span>}
                              {log.scanned_by_name && <span>Guard: {log.scanned_by_name}</span>}
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
