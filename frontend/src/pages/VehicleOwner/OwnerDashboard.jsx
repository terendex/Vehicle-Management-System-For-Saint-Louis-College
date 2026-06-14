import { useState, useEffect } from 'react'
import { QRCodeSVG } from 'qrcode.react'
import {
  User, Car, KeyRound, ShieldCheck, Eye, EyeOff, Check,
  Circle, AlertTriangle, Copy, LogOut, RefreshCw
} from 'lucide-react'
import OwnerLayout from '../../components/Layout/OwnerLayout'
import useAuthStore from '../../stores/authStore'
import { usersApi } from '../../api/users'
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

export default function OwnerDashboard() {
  const { user, logout, clearMustChangePassword } = useAuthStore()

  /* ── registration data ── */
  const [reg, setReg] = useState(null)
  const [loading, setLoading] = useState(true)
  const [fetchError, setFetchError] = useState(null)

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
  }, [])

  const fetchReg = async () => {
    setLoading(true)
    setFetchError(null)
    try {
      const data = await usersApi.getMyRegistration()
      setReg(data)
    } catch (err) {
      setFetchError(err.response?.data?.error || 'Failed to load registration data.')
    } finally {
      setLoading(false)
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
      setTimeout(() => {
        setPwModal(false)
        setPwSuccess(false)
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

  /* ── derived ── */
  const systemId = reg?.system_student_id || reg?.system_employee_id || '—'
  const qrPayload = reg ? `VEHICLE:${reg.plate_number}|ID:${reg.id}` : ''
  const strength  = pwStrength(pwForm.new)

  const handleLogout = () => {
    logout()
    window.location.href = '/login'
  }

  return (
    <OwnerLayout>
      {/* ──────────────────────────────────────────────────────
          FORCE PASSWORD CHANGE MODAL
          (blocks everything if must_change_password is true)
          ────────────────────────────────────────────────────── */}
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
                <div className="od-modal-icon warn">
                  <AlertTriangle size={26} />
                </div>
                <h2 className="od-modal-title">Change Your Password</h2>
                <p className="od-modal-subtitle">
                  {mustChange
                    ? 'You are using a temporary password. Please set a new password before continuing.'
                    : 'Update your account password below.'}
                </p>

                <form onSubmit={handlePwChange} className="od-pw-form">
                  {/* Current Password */}
                  <div className="od-form-group">
                    <label>Current (Temporary) Password</label>
                    <div className="od-pw-wrap">
                      <input
                        type={showCurrent ? 'text' : 'password'}
                        value={pwForm.current}
                        onChange={e => setPwForm({ ...pwForm, current: e.target.value })}
                        placeholder="Enter current password"
                        required
                        autoComplete="current-password"
                      />
                      <button type="button" className="od-pw-eye" onClick={() => setShowCurrent(v => !v)}>
                        {showCurrent ? <EyeOff size={16} /> : <Eye size={16} />}
                      </button>
                    </div>
                  </div>

                  {/* New Password */}
                  <div className="od-form-group">
                    <label>New Password</label>
                    <div className="od-pw-wrap">
                      <input
                        type={showNew ? 'text' : 'password'}
                        value={pwForm.new}
                        onChange={e => setPwForm({ ...pwForm, new: e.target.value })}
                        placeholder="Enter new password"
                        required
                        autoComplete="new-password"
                      />
                      <button type="button" className="od-pw-eye" onClick={() => setShowNew(v => !v)}>
                        {showNew ? <EyeOff size={16} /> : <Eye size={16} />}
                      </button>
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

                  {/* Confirm Password */}
                  <div className="od-form-group">
                    <label>Confirm New Password</label>
                    <div className="od-pw-wrap">
                      <input
                        type={showConfirm ? 'text' : 'password'}
                        value={pwForm.confirm}
                        onChange={e => setPwForm({ ...pwForm, confirm: e.target.value })}
                        placeholder="Re-enter new password"
                        required
                        autoComplete="new-password"
                      />
                      <button type="button" className="od-pw-eye" onClick={() => setShowConfirm(v => !v)}>
                        {showConfirm ? <EyeOff size={16} /> : <Eye size={16} />}
                      </button>
                    </div>
                    {pwForm.confirm && pwForm.new && pwForm.confirm !== pwForm.new && (
                      <p className="od-field-error">Passwords do not match.</p>
                    )}
                  </div>

                  {/* Backend errors */}
                  {pwError && <div className="od-error-banner">{pwError}</div>}
                  {pwErrors.length > 0 && (
                    <div className="od-error-banner">
                      {pwErrors.map((e, i) => <div key={i}>• {e}</div>)}
                    </div>
                  )}

                  <div className="od-pw-actions">
                    {!mustChange && (
                      <button type="button" className="od-btn-outline" onClick={() => setPwModal(false)}>
                        Cancel
                      </button>
                    )}
                    {mustChange && (
                      <button type="button" className="od-btn-logout" onClick={handleLogout}>
                        <LogOut size={15} /> Log Out
                      </button>
                    )}
                    <button
                      type="submit"
                      className="od-btn-primary"
                      disabled={pwSubmitting || strength.score < 5 || pwForm.new !== pwForm.confirm}
                    >
                      {pwSubmitting ? 'Saving…' : <><KeyRound size={15} /> Set New Password</>}
                    </button>
                  </div>
                </form>
              </>
            )}
          </div>
        </div>
      )}

      {/* ──────────────────────────────────────────────────────
          MAIN DASHBOARD CONTENT
          ────────────────────────────────────────────────────── */}
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
          <div className="od-loading">
            <div className="od-spinner" />
            <p>Loading your registration details…</p>
          </div>
        ) : fetchError ? (
          <div className="od-error-card">
            <AlertTriangle size={24} />
            <p>{fetchError}</p>
            <button className="od-btn-primary" onClick={fetchReg}><RefreshCw size={14} /> Retry</button>
          </div>
        ) : reg && (
          <div className="od-grid">

            {/* ── Left Column: Info ── */}
            <div className="od-info-col">

              {/* Personal Information */}
              <div className="od-card">
                <div className="od-card-head">
                  <User size={16} />
                  Personal Information
                </div>
                <div className="od-details-grid">
                  <div className="od-detail">
                    <span className="od-detail-label">Full Name</span>
                    <span className="od-detail-val">{reg.full_name}</span>
                  </div>
                  <div className="od-detail">
                    <span className="od-detail-label">Email</span>
                    <span className="od-detail-val">{reg.email}</span>
                  </div>
                  <div className="od-detail">
                    <span className="od-detail-label">Type</span>
                    <span className="od-detail-val od-capitalize">{reg.registrant_type}</span>
                  </div>
                  {reg.registrant_type === 'student' ? (
                    <>
                      <div className="od-detail">
                        <span className="od-detail-label">Student ID</span>
                        <span className="od-detail-val">{reg.student_id || '—'}</span>
                      </div>
                      <div className="od-detail" style={{ gridColumn: 'span 2' }}>
                        <span className="od-detail-label">Program &amp; Year</span>
                        <span className="od-detail-val">{reg.program_year || '—'}</span>
                      </div>
                    </>
                  ) : (
                    <>
                      <div className="od-detail">
                        <span className="od-detail-label">Employee ID</span>
                        <span className="od-detail-val">{reg.employee_id || '—'}</span>
                      </div>
                      <div className="od-detail" style={{ gridColumn: 'span 2' }}>
                        <span className="od-detail-label">Department</span>
                        <span className="od-detail-val">{reg.department || '—'}</span>
                      </div>
                    </>
                  )}
                  <div className="od-detail">
                    <span className="od-detail-label">Contact Number</span>
                    <span className="od-detail-val">{reg.contact_number || '—'}</span>
                  </div>
                  <div className="od-detail">
                    <span className="od-detail-label">Age</span>
                    <span className="od-detail-val">{reg.age || '—'}</span>
                  </div>
                  <div className="od-detail">
                    <span className="od-detail-label">Driver's License</span>
                    <span className="od-detail-val">{reg.drivers_license || '—'}</span>
                  </div>
                  <div className="od-detail" style={{ gridColumn: 'span 2' }}>
                    <span className="od-detail-label">Address</span>
                    <span className="od-detail-val">{reg.address || '—'}</span>
                  </div>
                  {reg.registrant_type === 'student' && reg.campus_days?.length > 0 && (
                    <div className="od-detail" style={{ gridColumn: 'span 2' }}>
                      <span className="od-detail-label">Campus Days</span>
                      <div className="od-day-badges">
                        {reg.campus_days.map(d => (
                          <span key={d} className="od-day-badge">{d}</span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </div>

              {/* Vehicle Information */}
              <div className="od-card">
                <div className="od-card-head">
                  <Car size={16} />
                  Vehicle Information
                </div>
                <div className="od-details-grid">
                  <div className="od-detail">
                    <span className="od-detail-label">Plate Number</span>
                    <span className="od-detail-val od-plate">{reg.plate_number}</span>
                  </div>
                  <div className="od-detail">
                    <span className="od-detail-label">Vehicle Type</span>
                    <span className="od-detail-val od-capitalize">{reg.vehicle_type}</span>
                  </div>
                  <div className="od-detail">
                    <span className="od-detail-label">Color</span>
                    <span className="od-detail-val">{reg.vehicle_color || '—'}</span>
                  </div>
                  <div className="od-detail">
                    <span className="od-detail-label">Conduction Number</span>
                    <span className="od-detail-val">{reg.conduction_number || '—'}</span>
                  </div>
                  {reg.body_number && (
                    <div className="od-detail" style={{ gridColumn: 'span 2' }}>
                      <span className="od-detail-label">Body Number</span>
                      <span className="od-detail-val">{reg.body_number}</span>
                    </div>
                  )}
                </div>
              </div>

            </div>

            {/* ── Right Column: QR + Status ── */}
            <div className="od-qr-col">
              <div className="od-card od-qr-card">
                <div className="od-card-head">
                  <ShieldCheck size={16} />
                  Vehicle Access QR Code
                </div>
                <p className="od-qr-hint">Present this code to security personnel upon entry.</p>
                <div className="od-qr-display">
                  <QRCodeSVG
                    value={qrPayload}
                    size={200}
                    level="H"
                    includeMargin={true}
                  />
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

              {/* Status card */}
              <div className="od-card od-status-card">
                <div className="od-card-head">
                  <ShieldCheck size={16} />
                  Registration Status
                </div>
                <div className="od-status-badge accepted">
                  <Check size={16} /> Accepted &amp; Authorized
                </div>
                <p className="od-status-note">
                  Your vehicle is authorized to enter the Saint Louis College campus premises.
                  Always carry your QR code for scanning at the gate.
                </p>
              </div>
            </div>

          </div>
        )}
      </div>
    </OwnerLayout>
  )
}
