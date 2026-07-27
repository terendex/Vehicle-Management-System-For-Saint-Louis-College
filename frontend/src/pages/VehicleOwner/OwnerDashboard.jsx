import { useState, useEffect } from 'react'
import { QRCodeSVG } from 'qrcode.react'
import { useLiveUpdates } from '../../realtime/useLiveUpdates'
import {
  User, Car, KeyRound, ShieldCheck, Eye, EyeOff, Check,
  Circle, AlertTriangle, Copy, LogOut, RefreshCw, AlertCircle,
  ParkingCircle, Bike, Loader2, Megaphone, Image, X, ZoomIn
} from 'lucide-react'
import useAuthStore from '../../stores/authStore'
import { usersApi } from '../../api/users'
import { violationsApi } from '../../api/violations'
import { registrationApi } from '../../api/registration'
import { getNotices } from '../../api/vehicles'
import './OwnerDashboard.css'

/* ── password strength rules ── */
const PW_RULES = [
  { key: 'length',  label: 'At least 8 characters',         test: (p) => p.length >= 8 },
  { key: 'upper',   label: 'One uppercase letter',          test: (p) => /[A-Z]/.test(p) },
  { key: 'lower',   label: 'One lowercase letter',          test: (p) => /[a-z]/.test(p) },
  { key: 'number',  label: 'One number',                    test: (p) => /[0-9]/.test(p) },
  { key: 'special', label: 'One special character (!@#$…)', test: (p) => /[!@#$%^&*()_+\-=[\]{};':"\\|,.<>/?]/.test(p) },
]

function pwStrength(pw) {
  if (!pw) return { level: '', score: 0 }
  const passed = PW_RULES.filter(r => r.test(pw)).length
  if (passed <= 1) return { level: 'weak',      score: 1 }
  if (passed === 2) return { level: 'fair',      score: 2 }
  if (passed === 3) return { level: 'good',      score: 3 }
  if (passed === 4) return { level: 'strong',    score: 4 }
  return               { level: 'excellent',  score: 5 }
}

const STRENGTH_LABELS = { weak: 'Weak', fair: 'Fair', good: 'Good', strong: 'Strong', excellent: 'Excellent' }

const VIOLATION_TYPE_LABELS = {
  unauthorized_entry:   'Unauthorized Entry',
  double_parking:       'Double Parking',
  time_exceed:          'Time Exceed',
  no_sticker:           'No Sticker',
  expired_registration: 'Expired Registration',
  unauthorized:         'Unauthorized (Legacy)',
  other:                'Other',
}

const OFFENSE_LABELS = { 1: '1st offense', 2: '2nd offense', 3: '3rd offense' }

const MOTORCYCLE_TYPES = ['Motorcycle', 'motorcycle']
const isMotorcycle = (vtype) => MOTORCYCLE_TYPES.some(m => vtype?.toLowerCase().includes(m.toLowerCase()))

export default function OwnerDashboard() {
  const { user, logout, clearMustChangePassword } = useAuthStore()

  /* ── registration data ── */
  const [reg, setReg] = useState(null)
  const [loading, setLoading] = useState(true)
  const [fetchError, setFetchError] = useState(null)

  /* ── violations ── */
  const [violations, setViolations] = useState([])
  const [violationsLoading, setViolationsLoading] = useState(false)
  const [violationsError, setViolationsError] = useState(null)
  const [evidenceLightbox, setEvidenceLightbox] = useState(null)

  /* ── parking availability ── */
  const [parking, setParking] = useState(null)  // { spaces, summary, zones }
  const [parkingLoading, setParkingLoading] = useState(false)
  const [parkingError, setParkingError] = useState(null)
  const [parkingCategory, setParkingCategory] = useState(null)

  /* ── notices ── */
  const [notices, setNotices] = useState([])
  const [noticesLoading, setNoticesLoading] = useState(false)

  /* ── password change modal ── */
  const mustChange = user?.must_change_password === true
  const [pwModal, setPwModal] = useState(mustChange)
  const [pwForm, setPwForm] = useState({ current: '', new: '', confirm: '' })
  const [showCurrent, setShowCurrent] = useState(false)
  const [showNew, setShowNew] = useState(false)
  const [showConfirm, setShowConfirm] = useState(false)
  const [pwError, setPwError] = useState(null)
  const [pwErrors, setPwErrors] = useState([])
  const [pwSubmitting, setPwSubmitting] = useState(false)
  const [pwSuccess, setPwSuccess] = useState(false)

  /* ── qr copy ── */
  const [qrCopied, setQrCopied] = useState(false)

  useEffect(() => {
    fetchReg()
    fetchViolations()
    fetchNotices()
  }, [])

  // Live-refresh when the owner's registration, violations or notices change
  useLiveUpdates(
    () => { fetchReg(); fetchViolations(); fetchNotices() },
    ['vehicleregistration', 'violation', 'parkingnotice', 'vehicle'],
  )

  const fetchNotices = async () => {
    setNoticesLoading(true)
    try {
      const { data } = await getNotices()
      setNotices(data)
    } catch {
      // non-critical, fail silently
    } finally {
      setNoticesLoading(false)
    }
  }

  const fetchReg = async () => {
    setLoading(true)
    setFetchError(null)
    try {
      const data = await usersApi.getMyRegistration()
      setReg(data)
      // Determine parking category from vehicle type
      const cat = isMotorcycle(data?.vehicle_type) ? 'motorcycle' : 'car'
      setParkingCategory(cat)
      fetchParking(cat)
    } catch (err) {
      setFetchError(err.response?.data?.error || 'Failed to load registration data.')
    } finally {
      setLoading(false)
    }
  }

  const fetchViolations = async () => {
    setViolationsLoading(true)
    setViolationsError(null)
    try {
      const data = await violationsApi.getMyViolations()
      setViolations(data)
    } catch (err) {
      setViolationsError('Could not load violations.')
    } finally {
      setViolationsLoading(false)
    }
  }

  const fetchParking = async (category) => {
    setParkingLoading(true)
    setParkingError(null)
    try {
      const data = await registrationApi.getParkingAvailability(category)
      setParking(data)
    } catch {
      setParkingError('Could not load parking availability.')
    } finally {
      setParkingLoading(false)
    }
  }

  /* ── password change ── */
  const handlePwChange = async (e) => {
    e.preventDefault()
    setPwError(null)
    setPwErrors([])
    setPwSubmitting(true)
    try {
      await usersApi.changePassword(pwForm.current, pwForm.new, pwForm.confirm)
      clearMustChangePassword()
      setPwSuccess(true)
      setPwForm({ current: '', new: '', confirm: '' })
      // Force a fresh login with the new password instead of keeping the old session active.
      setTimeout(() => {
        logout()
        window.location.href = '/login?passwordChanged=1'
      }, 1800)
    } catch (err) {
      const data = err.response?.data
      if (data?.errors) {
        setPwErrors(data.errors)
      } else {
        setPwError(data?.error || 'Failed to change password.')
      }
    } finally {
      setPwSubmitting(false)
    }
  }

  const systemId = reg?.system_student_id || reg?.system_employee_id || '—'
  const qrPayload = reg ? `VEHICLE:${reg.plate_number}|ID:${reg.id}` : ''
  const strength  = pwStrength(pwForm.new)

  const handleLogout = () => {
    logout()
    window.location.href = '/login'
  }

  /* ── Parking grid helpers ── */
  const parkingSummary = parking?.summary?.[parkingCategory] || null
  const parkingSpaces  = (parking?.spaces || []).filter(s => s.vehicle_category === parkingCategory)
  const parkingZones   = (parking?.zones || []).filter(z => z.category === parkingCategory)

  return (
    <>
      {/* Force password change modal */}
      {pwModal && (
        <div className="od-modal-overlay">
          <div className="od-modal">
            {pwSuccess ? (
              <div className="od-pw-success">
                <div className="od-pw-success-icon"><ShieldCheck size={36} /></div>
                <h3>Password Changed!</h3>
                <p>Your password has been updated successfully. Welcome to the portal!</p>
              </div>
            ) : (
              <>
                <div className="od-modal-icon warn"><AlertTriangle size={26} /></div>
                <h2 className="od-modal-title">Change Your Password</h2>
                <p className="od-modal-subtitle">
                  {mustChange
                    ? 'You are using a temporary password. Please set a new password before continuing.'
                    : 'Update your account password below.'}
                </p>

                <form onSubmit={handlePwChange} className="od-pw-form">
                  <div className="od-form-group">
                    <label>Current (Temporary) Password</label>
                    <div className="od-pw-wrap">
                      <input type={showCurrent ? 'text' : 'password'} value={pwForm.current} onChange={e => setPwForm({ ...pwForm, current: e.target.value })} placeholder="Enter current password" required autoComplete="current-password" />
                      <button type="button" className="od-pw-eye" onClick={() => setShowCurrent(v => !v)}>{showCurrent ? <EyeOff size={16} /> : <Eye size={16} />}</button>
                    </div>
                  </div>

                  <div className="od-form-group">
                    <label>New Password</label>
                    <div className="od-pw-wrap">
                      <input type={showNew ? 'text' : 'password'} value={pwForm.new} onChange={e => setPwForm({ ...pwForm, new: e.target.value })} placeholder="Enter new password" required autoComplete="new-password" />
                      <button type="button" className="od-pw-eye" onClick={() => setShowNew(v => !v)}>{showNew ? <EyeOff size={16} /> : <Eye size={16} />}</button>
                    </div>
                    {pwForm.new && (
                      <div className="od-strength-wrap">
                        <div className="od-strength-bar-bg">
                          <div className={`od-strength-bar ${strength.level}`} style={{ width: `${strength.score * 20}%` }} />
                        </div>
                        <span className={`od-strength-label ${strength.level}`}>{STRENGTH_LABELS[strength.level]}</span>
                        <div className="od-pw-rules">
                          {PW_RULES.map(rule => (
                            <div key={rule.key} className={`od-pw-rule ${rule.test(pwForm.new) ? 'met' : ''}`}>
                              {rule.test(pwForm.new) ? <Check size={12} /> : <Circle size={12} />}
                              {rule.label}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>

                  <div className="od-form-group">
                    <label>Confirm New Password</label>
                    <div className="od-pw-wrap">
                      <input type={showConfirm ? 'text' : 'password'} value={pwForm.confirm} onChange={e => setPwForm({ ...pwForm, confirm: e.target.value })} placeholder="Re-enter new password" required autoComplete="new-password" />
                      <button type="button" className="od-pw-eye" onClick={() => setShowConfirm(v => !v)}>{showConfirm ? <EyeOff size={16} /> : <Eye size={16} />}</button>
                    </div>
                    {pwForm.confirm && pwForm.new && pwForm.confirm !== pwForm.new && (
                      <p className="od-field-error">Passwords do not match.</p>
                    )}
                  </div>

                  {pwError && <div className="od-error-banner">{pwError}</div>}
                  {pwErrors.length > 0 && (
                    <div className="od-error-banner">
                      {pwErrors.map((e, i) => <div key={i}>• {e}</div>)}
                    </div>
                  )}

                  <div className="od-pw-actions">
                    {!mustChange && <button type="button" className="od-btn-outline" onClick={() => setPwModal(false)}>Cancel</button>}
                    {mustChange && <button type="button" className="od-btn-logout" onClick={handleLogout}><LogOut size={15} /> Log Out</button>}
                    <button type="submit" className="od-btn-primary" disabled={pwSubmitting || strength.score < 5 || pwForm.new !== pwForm.confirm}>
                      {pwSubmitting ? 'Saving…' : <><KeyRound size={15} /> Set New Password</>}
                    </button>
                  </div>
                </form>
              </>
            )}
          </div>
        </div>
      )}

      {/* ── Main dashboard ── */}
      <div className="od-page">

        {/* Welcome Banner */}
        <div className="od-welcome-banner">
          <div className="od-welcome-avatar">
            {user?.full_name?.charAt(0)?.toUpperCase() || 'V'}
          </div>
          <div className="od-welcome-text">
            <h1>Welcome, {user?.full_name || 'Vehicle Owner'}!</h1>
            <p>Here is your registration summary and vehicle access details.</p>
          </div>
          <button className="od-change-pw-btn" onClick={() => setPwModal(true)} title="Change Password">
            <KeyRound size={15} />
            Change Password
          </button>
        </div>

        {/* ID Cards */}
        <div className="od-id-cards">
          <div className="od-id-card portal">
            <div className="od-id-card-label">Portal Account ID</div>
            <div className="od-id-card-value">{user?.user_code || '—'}</div>
            <div className="od-id-card-sub">Vehicle Owner Account</div>
          </div>
          <div className="od-id-card system">
            <div className="od-id-card-label">System Registration ID</div>
            <div className="od-id-card-value">{systemId}</div>
            <div className="od-id-card-sub">
              {reg?.registrant_type === 'student' ? 'Student Registration' : reg?.registrant_type === 'employee' ? 'Employee Registration' : 'Registration'}
            </div>
          </div>
        </div>

        {loading ? (
          <div className="od-loading"><div className="od-spinner" /><p>Loading your registration details…</p></div>
        ) : fetchError ? (
          <div className="od-error-card">
            <AlertTriangle size={24} />
            <p>{fetchError}</p>
            <button className="od-btn-primary" onClick={fetchReg}><RefreshCw size={14} /> Retry</button>
          </div>
        ) : reg && (
          <>
            {/* ── Main info + QR grid ── */}
            <div className="od-grid">

              {/* Left: Personal + Vehicle Info */}
              <div className="od-info-col">
                <div className="od-card">
                  <div className="od-card-head"><User size={16} /> Personal Information</div>
                  <div className="od-details-grid">
                    <div className="od-detail"><span className="od-detail-label">Full Name</span><span className="od-detail-val">{reg.full_name}</span></div>
                    <div className="od-detail"><span className="od-detail-label">Email</span><span className="od-detail-val">{reg.email}</span></div>
                    <div className="od-detail"><span className="od-detail-label">Type</span><span className="od-detail-val od-capitalize">{reg.registrant_type}</span></div>
                    {reg.registrant_type === 'student' ? (
                      <>
                        <div className="od-detail"><span className="od-detail-label">Student ID</span><span className="od-detail-val">{reg.student_id || '—'}</span></div>
                        <div className="od-detail" style={{ gridColumn: 'span 2' }}><span className="od-detail-label">Program &amp; Year</span><span className="od-detail-val">{reg.program_year || '—'}</span></div>
                      </>
                    ) : reg.registrant_type === 'employee' ? (
                      <>
                        <div className="od-detail"><span className="od-detail-label">Employee ID</span><span className="od-detail-val">{reg.employee_id || '—'}</span></div>
                        <div className="od-detail" style={{ gridColumn: 'span 2' }}><span className="od-detail-label">Department</span><span className="od-detail-val">{reg.department || '—'}</span></div>
                      </>
                    ) : null}
                    <div className="od-detail"><span className="od-detail-label">Contact Number</span><span className="od-detail-val">{reg.contact_number || '—'}</span></div>
                    <div className="od-detail"><span className="od-detail-label">Age</span><span className="od-detail-val">{reg.age || '—'}</span></div>
                    <div className="od-detail"><span className="od-detail-label">Driver's License</span><span className="od-detail-val">{reg.drivers_license || '—'}</span></div>
                    <div className="od-detail" style={{ gridColumn: 'span 2' }}><span className="od-detail-label">Address</span><span className="od-detail-val">{reg.address || '—'}</span></div>
                    {(reg.campus_days?.length > 0 || reg.schedule) && (
                      <div className="od-detail" style={{ gridColumn: 'span 2' }}>
                        <span className="od-detail-label">Assigned Schedule</span>
                        {reg.campus_days?.length > 0 ? (
                          <div className="od-day-badges">
                            {reg.campus_days.map(d => (
                              <span key={d} className="od-day-badge">{d}</span>
                            ))}
                          </div>
                        ) : (
                          <div className="od-day-badges">
                            <span className="od-day-badge">{reg.schedule}</span>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </div>

                <div className="od-card">
                  <div className="od-card-head"><Car size={16} /> Vehicle Information</div>
                  <div className="od-details-grid">
                    <div className="od-detail"><span className="od-detail-label">Plate Number</span><span className="od-detail-val od-plate">{reg.plate_number}</span></div>
                    <div className="od-detail"><span className="od-detail-label">Vehicle Type</span><span className="od-detail-val od-capitalize">{reg.vehicle_type}</span></div>
                    <div className="od-detail"><span className="od-detail-label">Color</span><span className="od-detail-val">{reg.vehicle_color || '—'}</span></div>
                    <div className="od-detail"><span className="od-detail-label">Conduction Number</span><span className="od-detail-val">{reg.conduction_number || '—'}</span></div>
                    {reg.body_number && (
                      <div className="od-detail" style={{ gridColumn: 'span 2' }}><span className="od-detail-label">Body Number</span><span className="od-detail-val">{reg.body_number}</span></div>
                    )}
                  </div>
                </div>
              </div>

              {/* Right: QR + Status */}
              <div className="od-qr-col">
                <div className="od-card od-qr-card">
                  <div className="od-card-head"><ShieldCheck size={16} /> Vehicle Access QR Code</div>
                  <p className="od-qr-hint">Present this code to security personnel upon entry.</p>
                  <div className="od-qr-display">
                    <QRCodeSVG value={qrPayload} size={200} level="H" includeMargin={true} />
                  </div>
                  <div className="od-qr-data-box">
                    <span className="od-qr-data-label">QR Data</span>
                    <code className="od-qr-data-code">{qrPayload}</code>
                  </div>
                  <button
                    className="od-copy-btn"
                    onClick={async () => {
                      await navigator.clipboard.writeText(qrPayload)
                      setQrCopied(true)
                      setTimeout(() => setQrCopied(false), 2000)
                    }}
                  >
                    {qrCopied ? <><Check size={14} /> Copied!</> : <><Copy size={14} /> Copy QR Data</>}
                  </button>
                </div>

                <div className="od-card od-status-card">
                  <div className="od-card-head"><ShieldCheck size={16} /> Registration Status</div>
                  <div className="od-status-badge accepted"><Check size={16} /> Accepted &amp; Authorized</div>
                  <p className="od-status-note">
                    Your vehicle is authorized to enter the Saint Louis College campus premises.
                    Always carry your QR code for scanning at the gate.
                  </p>
                </div>
              </div>
            </div>

            {/* ── Violations Section ── */}
            <div className="od-section-title-row">
              <AlertCircle size={17} />
              <h2 className="od-section-title">Violations</h2>
            </div>
            <div className="od-card od-violations-card">
              <div className="od-card-head"><AlertCircle size={16} /> My Violation Record</div>
              {violationsLoading ? (
                <div className="od-inner-loading"><Loader2 size={20} className="od-spin-icon" /> Loading violations…</div>
              ) : violationsError ? (
                <div className="od-inner-error">
                  <AlertTriangle size={16} /> {violationsError}
                  <button className="od-retry-link" onClick={fetchViolations}>Retry</button>
                </div>
              ) : violations.length === 0 ? (
                <div className="od-clean-record">
                  <div className="od-clean-icon"><ShieldCheck size={32} /></div>
                  <p className="od-clean-text">No violations on record.</p>
                  <span className="od-clean-sub">Keep up your good driving behavior on campus!</span>
                </div>
              ) : (
                <>
                  {evidenceLightbox && (
                    <div
                      style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)', zIndex: 9999, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}
                      onClick={() => setEvidenceLightbox(null)}
                    >
                      <div style={{ position: 'relative' }} onClick={e => e.stopPropagation()}>
                        <img src={evidenceLightbox} alt="violation evidence" style={{ maxWidth: '90vw', maxHeight: '85vh', borderRadius: 10, display: 'block', boxShadow: '0 20px 60px rgba(0,0,0,0.6)' }} />
                        <button
                          onClick={() => setEvidenceLightbox(null)}
                          style={{ position: 'absolute', top: -12, right: -12, width: 30, height: 30, borderRadius: '50%', border: 'none', background: '#fff', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: '0 2px 8px rgba(0,0,0,0.2)' }}
                        ><X size={14} /></button>
                      </div>
                    </div>
                  )}

                  {/* ── Active violations ── */}
                  {(() => {
                    const active = violations.filter(v => !v.is_resolved && v.status !== 'cleared')
                    const hasFee = active.some(v => v.status === 'fee_imposed')
                    const totalOutstanding = active.reduce((sum, v) => sum + parseFloat(v.fine_amount || 0), 0)
                    return (
                      <>
                        {hasFee && (
                          <div className="od-outstanding-banner od-outstanding-banner--blocked">
                            <AlertTriangle size={15} />
                            <span>
                              <strong>Entry Denied.</strong> You have an outstanding ₱{totalOutstanding.toFixed(2)} violation fee.
                              Report to the <strong>CDSO office</strong> to process payment and restore access.
                            </span>
                          </div>
                        )}
                        {!hasFee && totalOutstanding > 0 && (
                          <div className="od-outstanding-banner">
                            <AlertTriangle size={15} />
                            <span>Outstanding fines: <strong>₱{totalOutstanding.toFixed(2)}</strong> — please settle at the CDSO office.</span>
                          </div>
                        )}
                        {active.length > 0 && (
                          <div className="od-violations-table-wrap">
                            <table className="od-violations-table">
                              <thead>
                                <tr>
                                  <th>Violation</th>
                                  <th>Offense</th>
                                  <th>Notes</th>
                                  <th>Fine</th>
                                  <th>Evidence</th>
                                  <th>Date Issued</th>
                                  <th>Status</th>
                                </tr>
                              </thead>
                              <tbody>
                                {active.map(v => (
                                  <tr key={v.id} className={`od-viol-active${v.status === 'fee_imposed' ? ' od-viol-fee' : ''}`}>
                                    <td className="od-viol-type">{VIOLATION_TYPE_LABELS[v.violation_type] || v.violation_type}</td>
                                    <td className="od-viol-offense">
                                      {v.offense_number
                                        ? <span className={`od-offense-badge od-offense-${v.offense_number}`}>{OFFENSE_LABELS[v.offense_number] || `${v.offense_number}th offense`}</span>
                                        : '—'
                                      }
                                    </td>
                                    <td className="od-viol-notes">{v.notes || '—'}</td>
                                    <td className="od-viol-fine">
                                      {parseFloat(v.fine_amount) > 0 ? `₱${parseFloat(v.fine_amount).toFixed(2)}` : <span style={{ color: '#9CA3AF' }}>₱0</span>}
                                    </td>
                                    <td>
                                      {v.evidence_url ? (
                                        <button className="od-evidence-thumb-btn" onClick={() => setEvidenceLightbox(v.evidence_url)} title="View evidence">
                                          <img src={v.evidence_url} alt="evidence" className="od-evidence-thumb" />
                                          <ZoomIn size={11} className="od-evidence-zoom" />
                                        </button>
                                      ) : (
                                        <span className="od-no-evidence"><Image size={12} /></span>
                                      )}
                                    </td>
                                    <td className="od-viol-date">{new Date(v.issued_at).toLocaleDateString('en-PH', { year: 'numeric', month: 'short', day: 'numeric' })}</td>
                                    <td>
                                      {v.status === 'fee_imposed'
                                        ? <span className="od-viol-badge od-viol-badge--fee">Entry Denied · Pay ₱150</span>
                                        : v.status === 'warning'
                                        ? <span className="od-viol-badge od-viol-badge--warning">Warning</span>
                                        : <span className="od-viol-badge notified">Active</span>
                                      }
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        )}
                      </>
                    )
                  })()}

                  {/* ── Resolved / Cleared history ── */}
                  {(() => {
                    const resolved = violations.filter(v => v.is_resolved || v.status === 'cleared')
                    if (resolved.length === 0) return null
                    return (
                      <div className="od-history-section">
                        <div className="od-history-label">
                          <ShieldCheck size={13} /> Violation History — Resolved
                        </div>
                        <div className="od-violations-table-wrap">
                          <table className="od-violations-table od-violations-table--history">
                            <thead>
                              <tr>
                                <th>Violation</th>
                                <th>Notes</th>
                                <th>Fine</th>
                                <th>Evidence</th>
                                <th>Date Issued</th>
                                <th>Status</th>
                              </tr>
                            </thead>
                            <tbody>
                              {resolved.map(v => (
                                <tr key={v.id} className="od-viol-resolved">
                                  <td className="od-viol-type">{VIOLATION_TYPE_LABELS[v.violation_type] || v.violation_type}</td>
                                  <td className="od-viol-notes">{v.notes || '—'}</td>
                                  <td className="od-viol-fine">₱{parseFloat(v.fine_amount || 0).toFixed(2)}</td>
                                  <td>
                                    {v.evidence_url ? (
                                      <button className="od-evidence-thumb-btn" onClick={() => setEvidenceLightbox(v.evidence_url)} title="View evidence">
                                        <img src={v.evidence_url} alt="evidence" className="od-evidence-thumb" />
                                        <ZoomIn size={11} className="od-evidence-zoom" />
                                      </button>
                                    ) : (
                                      <span className="od-no-evidence"><Image size={12} /></span>
                                    )}
                                  </td>
                                  <td className="od-viol-date">{new Date(v.issued_at).toLocaleDateString('en-PH', { year: 'numeric', month: 'short', day: 'numeric' })}</td>
                                  <td>
                                    <span className="od-viol-badge resolved">Resolved</span>
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    )
                  })()}
                </>
              )}
            </div>

            {/* ── Announcements Section ── */}
            {(noticesLoading || notices.length > 0) && (
              <>
                <div className="od-section-title-row">
                  <Megaphone size={17} />
                  <h2 className="od-section-title">Announcements</h2>
                  {noticesLoading && <Loader2 size={15} className="od-spin-icon" />}
                </div>
                {!noticesLoading && (
                  <div className="od-notices-list">
                    {notices.map(n => (
                      <div key={n.id} className="od-notice-item">
                        <div className="od-notice-icon"><Megaphone size={15} /></div>
                        <div className="od-notice-content">
                          <span className="od-notice-title">{n.title}</span>
                          <p className="od-notice-body">{n.body}</p>
                          <span className="od-notice-date">
                            {new Date(n.created_at).toLocaleDateString('en-PH', { year: 'numeric', month: 'long', day: 'numeric' })}
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </>
            )}

            {/* ── Parking Availability Section ── */}
            <div className="od-section-title-row">
              {parkingCategory === 'motorcycle' ? <Bike size={17} /> : <ParkingCircle size={17} />}
              <h2 className="od-section-title">
                Live Parking Availability — {parkingCategory === 'motorcycle' ? 'Motorcycle' : 'Car'} Area
              </h2>
              <button className="od-refresh-btn" onClick={() => fetchParking(parkingCategory)} title="Refresh">
                <RefreshCw size={14} />
              </button>
            </div>
            <div className="od-card od-parking-card">
              <div className="od-card-head">
                {parkingCategory === 'motorcycle' ? <Bike size={16} /> : <ParkingCircle size={16} />}
                {parkingCategory === 'motorcycle' ? 'Motorcycle' : 'Car'} Parking Spaces
              </div>

              {parkingLoading ? (
                <div className="od-inner-loading"><Loader2 size={20} className="od-spin-icon" /> Loading parking data…</div>
              ) : parkingError ? (
                <div className="od-inner-error">
                  <AlertTriangle size={16} /> {parkingError}
                  <button className="od-retry-link" onClick={() => fetchParking(parkingCategory)}>Retry</button>
                </div>
              ) : (
                <>
                  {/* Summary counters */}
                  {parkingSummary ? (
                    <div className="od-parking-summary">
                      <div className="od-parking-stat available">
                        <span className="od-parking-stat-num">{parkingSummary.available}</span>
                        <span className="od-parking-stat-label">Available</span>
                      </div>
                      <div className="od-parking-stat occupied">
                        <span className="od-parking-stat-num">{parkingSummary.occupied}</span>
                        <span className="od-parking-stat-label">Occupied</span>
                      </div>
                      <div className="od-parking-stat total">
                        <span className="od-parking-stat-num">{parkingSummary.total}</span>
                        <span className="od-parking-stat-label">Total</span>
                      </div>
                    </div>
                  ) : null}

                  {/* Per-zone fill percentage */}
                  {parkingZones.length > 0 && (
                    <div className="od-zone-fill-grid">
                      {parkingZones.map(z => (
                        <div key={z.zone_id} className="od-zone-fill-card">
                          <div className="od-zone-fill-header">
                            <span className="od-zone-fill-name">{z.zone_name}</span>
                            <span className={`od-zone-fill-pct ${z.fill_pct >= 90 ? 'critical' : z.fill_pct >= 70 ? 'high' : 'normal'}`}>
                              {z.fill_pct}%
                            </span>
                          </div>
                          <div className="od-zone-fill-bar-bg">
                            <div
                              className={`od-zone-fill-bar ${z.fill_pct >= 90 ? 'critical' : z.fill_pct >= 70 ? 'high' : 'normal'}`}
                              style={{ width: `${z.fill_pct}%` }}
                            />
                          </div>
                          <div className="od-zone-fill-sub">
                            {z.available} of {z.total} spaces available
                          </div>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Parking grid */}
                  {parkingSpaces.length > 0 ? (
                    <div className="od-parking-grid">
                      {parkingSpaces.map(space => (
                        <div
                          key={space.id}
                          className={`od-parking-space ${space.is_occupied ? 'occupied' : 'free'}`}
                          title={space.is_occupied ? `Occupied${space.occupied_by ? ` by ${space.occupied_by}` : ''}` : 'Available'}
                        >
                          <span className="od-space-num">{space.space_number}</span>
                          {space.is_occupied && space.occupied_by && (
                            <span className="od-space-plate">{space.occupied_by}</span>
                          )}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="od-parking-empty">
                      <p>No parking spaces configured yet.</p>
                      <span className="od-parking-cctv-note">
                        Parking availability will be updated in real time once CCTV integration is enabled.
                        Each space is monitored via camera overview with designated zones.
                      </span>
                    </div>
                  )}

                  <div className="od-parking-legend">
                    <span className="od-legend-item free"><span className="od-legend-dot free" />Available</span>
                    <span className="od-legend-item occupied"><span className="od-legend-dot occupied" />Occupied</span>
                  </div>
                  <p className="od-parking-note">
                    Parking data is updated by security personnel and will be linked to CCTV camera zones.
                    Only {parkingCategory === 'motorcycle' ? 'motorcycle' : 'car/van/SUV'} spaces are shown for your vehicle type.
                  </p>
                </>
              )}
            </div>
          </>
        )}
      </div>
    </>
  )
}
