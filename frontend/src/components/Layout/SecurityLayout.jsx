import { useState, useEffect, useRef } from 'react'
import { NavLink, useNavigate, useLocation } from 'react-router-dom'
import {
  ShieldCheck,
  ParkingCircle,
  ClipboardList,
  LogOut,
  MapPin,
  Clock,
  RefreshCw,
  X,
  CheckCircle,
  AlertCircle,
  LogIn,
  Eye,
  EyeOff,
  KeyRound,
  Menu,
  HelpCircle,
  Shield,
} from 'lucide-react'
import { toast } from 'sonner'
import jsQR from 'jsqr'
import slcLogo from '../../assets/slclogo.jpg'
import useAuthStore from '../../stores/authStore'
import { getCurrentShifts } from '../../api/scanning'
import { usersApi } from '../../api/users'
import { authApi } from '../../api/auth'
import './AdminLayout.css'
import './SecurityLayout.css'

const GATE_LABELS = { gate1: 'Gate 1', gate4: 'Gate 4' }

function useCurrentShift(gate) {
  const [shift, setShift] = useState(null)
  useEffect(() => {
    if (!gate) return
    getCurrentShifts()
      .then(r => setShift(r.data?.[gate] ?? null))
      .catch(() => {})
  }, [gate])
  return shift
}

function shiftDuration(clockedInAt) {
  if (!clockedInAt) return null
  const mins = Math.floor((Date.now() - new Date(clockedInAt).getTime()) / 60000)
  if (mins < 60) return `${mins}m`
  return `${Math.floor(mins / 60)}h ${mins % 60}m`
}

// ── Change Shift Overlay ──────────────────────────────────────────────────────
function ChangeShiftModal({ gate, gateLabel, onClose, onSuccess }) {
  const { qrLogin, guardLogin, isLoading } = useAuthStore()

  const [status,     setStatus]     = useState('idle')  // idle | scanning | success | error
  const [guardInfo,  setGuardInfo]  = useState(null)
  const [errorMsg,   setErrorMsg]   = useState('')
  const [useCamera,  setUseCamera]  = useState(true)
  const [inputToken, setInputToken] = useState('')
  const [showToken,  setShowToken]  = useState(false)
  const [cameraErr,  setCameraErr]  = useState('')

  // Incoming guard signs in with credentials; the QR scanner appears below the
  // form once their typed email belongs to a guard whose badge is usable.
  const [qrAvailable, setQrAvailable]   = useState(false)
  const [email, setEmail]               = useState('')
  const [password, setPassword]         = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [credError, setCredError]       = useState('')

  const videoRef  = useRef(null)
  const streamRef = useRef(null)
  const inputRef  = useRef(null)

  // Same rule as the gate login page: debounced per-guard badge check on the typed email
  useEffect(() => {
    const clean = email.trim()
    if (!clean || !clean.includes('@')) {
      setQrAvailable(false)
      return
    }
    let cancelled = false
    const timer = setTimeout(() => {
      authApi.guardQrAvailable(clean)
        .then(ok => { if (!cancelled) setQrAvailable(ok) })
        .catch(() => { if (!cancelled) setQrAvailable(false) })
    }, 400)
    return () => { cancelled = true; clearTimeout(timer) }
  }, [email])

  // QR camera scanning loop
  useEffect(() => {
    if (!useCamera || status !== 'idle' || !qrAvailable) return
    let animFrame
    const canvas = document.createElement('canvas')
    const ctx    = canvas.getContext('2d', { willReadFrequently: true })

    const startCamera = async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: 'environment', width: { ideal: 640 }, height: { ideal: 640 } },
        })
        streamRef.current = stream
        if (videoRef.current) {
          videoRef.current.srcObject = stream
          videoRef.current.play()
        }
        const scan = () => {
          const video = videoRef.current
          if (video && video.readyState >= 2 && video.videoWidth > 0) {
            canvas.width  = video.videoWidth
            canvas.height = video.videoHeight
            ctx.drawImage(video, 0, 0)
            const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height)
            const code = jsQR(imageData.data, imageData.width, imageData.height)
            if (code?.data) {
              handleScan(code.data)
              return
            }
          }
          animFrame = requestAnimationFrame(scan)
        }
        animFrame = requestAnimationFrame(scan)
      } catch (err) {
        setCameraErr(`Camera unavailable: ${err.message}`)
        setUseCamera(false)
      }
    }

    startCamera()
    return () => {
      cancelAnimationFrame(animFrame)
      streamRef.current?.getTracks().forEach(t => t.stop())
    }
  }, [useCamera, status, qrAvailable]) // eslint-disable-line react-hooks/exhaustive-deps

  const stopCamera = () => streamRef.current?.getTracks().forEach(t => t.stop())

  const handleClose = () => {
    stopCamera()
    onClose()
  }

  const handleScan = async (token) => {
    if (status === 'scanning') return
    const clean = token.trim()
    if (!clean) return
    stopCamera()
    setStatus('scanning')
    setErrorMsg('')

    try {
      const guard = await qrLogin(clean, gate)
      setGuardInfo(guard)
      setStatus('success')
      toast.success(`Shift handed over to ${guard.full_name} at ${gateLabel}.`)
      onSuccess?.()
      setTimeout(() => { onClose() }, 2200)
    } catch (err) {
      setErrorMsg(err.message || 'QR scan failed. Please try again.')
      setStatus('error')
      setInputToken('')
      setTimeout(() => {
        setStatus('idle')
        // Re-init camera after error
        if (useCamera) {
          setUseCamera(false)
          setTimeout(() => setUseCamera(true), 80)
        } else {
          inputRef.current?.focus()
        }
      }, 3000)
    }
  }

  const handleCredentialLogin = async (e) => {
    e?.preventDefault()
    if (!email.trim() || !password || isLoading) return
    setCredError('')
    try {
      const guard = await guardLogin(email.trim(), password, gate)
      setGuardInfo(guard)
      setStatus('success')
      toast.success(`Shift handed over to ${guard.full_name} at ${gateLabel}.`)
      onSuccess?.()
      setTimeout(() => { onClose() }, 2200)
    } catch (err) {
      setCredError(err.message || 'Login failed. Please check your credentials.')
      setPassword('')
    }
  }

  return (
    <div className="cs-overlay" onClick={e => e.target === e.currentTarget && handleClose()}>
      <div className="cs-modal">

        {/* Header */}
        <div className="cs-modal-head">
          <div className="cs-modal-head-left">
            <RefreshCw size={15} />
            <span className="cs-modal-title">Change Shift</span>
            <span className="cs-gate-pill">{gateLabel}</span>
          </div>
          <button className="cs-close" onClick={handleClose}><X size={17} /></button>
        </div>

        {/* States */}
        {status === 'success' ? (
          <div className="cs-state cs-state-success">
            <CheckCircle size={42} />
            <h3>Shift Handed Over</h3>
            <p>Welcome, <strong>{guardInfo?.full_name}</strong></p>
            <p className="cs-hint">Now on duty at {gateLabel}</p>
          </div>

        ) : status === 'error' ? (
          <div className="cs-state cs-state-error">
            <AlertCircle size={42} />
            <h3>Scan Failed</h3>
            <p>{errorMsg}</p>
            <p className="cs-hint">Retrying in a moment…</p>
          </div>

        ) : status === 'scanning' ? (
          <div className="cs-state">
            <div className="cs-spinner" />
            <p>Verifying QR badge…</p>
          </div>

        ) : (
          <>
            <p className="cs-instruction">
              Incoming guard: sign in with your credentials to take over the shift at <strong>{gateLabel}</strong>
            </p>
            <form onSubmit={handleCredentialLogin} style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {credError && <p className="cs-err-text" role="alert">{credError}</p>}
              <input
                className="cs-input"
                style={{ fontFamily: 'inherit' }}
                type="email"
                value={email}
                onChange={e => setEmail(e.target.value)}
                placeholder="Email address"
                autoComplete="username"
                required
              />
              <div className="cs-input-row">
                <input
                  className="cs-input"
                  style={{ fontFamily: 'inherit' }}
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  placeholder="Password"
                  autoComplete="current-password"
                  required
                />
                <button type="button" className="cs-eye" onClick={() => setShowPassword(v => !v)}>
                  {showPassword ? <EyeOff size={15} /> : <Eye size={15} />}
                </button>
                <button
                  type="submit"
                  className="cs-submit"
                  disabled={!email.trim() || !password || isLoading}
                >
                  <LogIn size={16} />
                </button>
              </div>
            </form>

            {/* QR scanner appears once the typed email matches a badge-eligible guard */}
            {qrAvailable && (
              <>
                <div className="cs-divider"><span>or scan your QR badge</span></div>
                {useCamera ? (
                  <>
                    <div className="cs-camera-wrap">
                      <video ref={videoRef} className="cs-video" muted playsInline />
                      <div className="cs-scan-frame" />
                      <p className="cs-camera-hint">Point camera at QR badge</p>
                    </div>
                    <button className="cs-toggle-btn" onClick={() => { stopCamera(); setUseCamera(false) }}>
                      Use text input instead
                    </button>
                  </>
                ) : (
                  <>
                    <div className="cs-input-row">
                      <input
                        ref={inputRef}
                        className="cs-input"
                        type={showToken ? 'text' : 'password'}
                        value={inputToken}
                        onChange={e => setInputToken(e.target.value)}
                        onKeyDown={e => e.key === 'Enter' && handleScan(inputToken)}
                        placeholder="QR token…"
                        autoComplete="off"
                        spellCheck={false}
                      />
                      <button type="button" className="cs-eye" onClick={() => setShowToken(v => !v)}>
                        {showToken ? <EyeOff size={15} /> : <Eye size={15} />}
                      </button>
                      <button
                        className="cs-submit"
                        disabled={!inputToken.trim()}
                        onClick={() => handleScan(inputToken)}
                      >
                        <LogIn size={16} />
                      </button>
                    </div>
                    {cameraErr && <p className="cs-err-text">{cameraErr}</p>}
                    <button className="cs-toggle-btn" onClick={() => { setCameraErr(''); setUseCamera(true) }}>
                      Use camera instead
                    </button>
                  </>
                )}
              </>
            )}
          </>
        )}
      </div>
    </div>
  )
}

// ── Forced Password Change ────────────────────────────────────────────────────
// Shown on a guard's first credentials login (must_change_password is set).
// The guard cannot use the dashboard — and their QR badge stays locked — until
// the temporary password is replaced.
function ForcePasswordModal({ onLogout }) {
  const { clearMustChangePassword } = useAuthStore()

  const [form, setForm]             = useState({ current: '', new: '', confirm: '' })
  const [showPw, setShowPw]         = useState(false)
  const [error, setError]           = useState(null)
  const [errors, setErrors]         = useState([])
  const [submitting, setSubmitting] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (submitting) return
    setError(null)
    setErrors([])
    if (form.new !== form.confirm) {
      setError('New passwords do not match.')
      return
    }
    setSubmitting(true)
    try {
      await usersApi.changePassword(form.current, form.new, form.confirm)
      clearMustChangePassword()
      toast.success('Password changed! Please log in again with your new password.')
      // Force a fresh login with the new password instead of keeping the old session active.
      setTimeout(() => onLogout({ passwordChanged: true }), 1200)
    } catch (err) {
      const data = err.response?.data
      if (data?.errors) setErrors(data.errors)
      else setError(data?.error || 'Failed to change password.')
    } finally {
      setSubmitting(false)
    }
  }

  const fieldStyle = { display: 'flex', flexDirection: 'column', gap: 4 }
  const labelStyle = { fontSize: 12, fontWeight: 600, color: '#4A6B85' }

  return (
    <div className="cs-overlay">
      <div className="cs-modal" style={{ maxWidth: 380 }}>
        <div className="cs-modal-head">
          <div className="cs-modal-head-left">
            <KeyRound size={15} />
            <span className="cs-modal-title">Change Your Password</span>
          </div>
        </div>

        <p style={{ margin: 0, fontSize: 13, color: '#2E4C63', lineHeight: 1.5 }}>
          You are using a temporary password. Set a new one to continue — your QR badge
          stays locked until you do.
        </p>

        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 8 }}>
          <div style={fieldStyle}>
            <label style={labelStyle} htmlFor="fp-current">Current (Temporary) Password</label>
            <input
              id="fp-current"
              className="cs-input"
              type={showPw ? 'text' : 'password'}
              value={form.current}
              onChange={e => setForm({ ...form, current: e.target.value })}
              autoComplete="current-password"
              required
            />
          </div>
          <div style={fieldStyle}>
            <label style={labelStyle} htmlFor="fp-new">New Password</label>
            <input
              id="fp-new"
              className="cs-input"
              type={showPw ? 'text' : 'password'}
              value={form.new}
              onChange={e => setForm({ ...form, new: e.target.value })}
              autoComplete="new-password"
              required
            />
          </div>
          <div style={fieldStyle}>
            <label style={labelStyle} htmlFor="fp-confirm">Confirm New Password</label>
            <input
              id="fp-confirm"
              className="cs-input"
              type={showPw ? 'text' : 'password'}
              value={form.confirm}
              onChange={e => setForm({ ...form, confirm: e.target.value })}
              autoComplete="new-password"
              required
            />
          </div>

          <button
            type="button"
            className="cs-toggle-btn"
            style={{ alignSelf: 'flex-start', display: 'flex', alignItems: 'center', gap: 5 }}
            onClick={() => setShowPw(v => !v)}
          >
            {showPw ? <EyeOff size={13} /> : <Eye size={13} />}
            {showPw ? 'Hide passwords' : 'Show passwords'}
          </button>

          <p style={{ margin: 0, fontSize: 11.5, color: '#64839C', lineHeight: 1.45 }}>
            At least 8 characters with an uppercase letter, lowercase letter, number and special character.
          </p>

          {error && <p className="cs-err-text" style={{ margin: 0 }}>{error}</p>}
          {errors.length > 0 && (
            <div className="cs-err-text" style={{ margin: 0 }}>
              {errors.map((msg, i) => <div key={i}>• {msg}</div>)}
            </div>
          )}

          <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
            <button
              type="button"
              style={{
                flex: 1, height: 38, border: '1.5px solid #D3E1EC', borderRadius: 8,
                background: '#fff', color: '#2E4C63', fontSize: 13, fontWeight: 600,
                cursor: 'pointer', fontFamily: 'inherit', display: 'flex',
                alignItems: 'center', justifyContent: 'center', gap: 6,
              }}
              onClick={onLogout}
            >
              <LogOut size={14} /> Log Out
            </button>
            <button
              type="submit"
              disabled={submitting}
              style={{
                flex: 1.4, height: 38, border: 'none', borderRadius: 8,
                background: '#03396C', color: '#fff', fontSize: 13, fontWeight: 600,
                cursor: submitting ? 'not-allowed' : 'pointer', opacity: submitting ? 0.7 : 1,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                gap: 6, fontFamily: 'inherit',
              }}
            >
              <KeyRound size={14} />
              {submitting ? 'Saving…' : 'Set New Password'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ── Layout ────────────────────────────────────────────────────────────────────
export default function SecurityLayout({ children, fillHeight = false }) {
  const { user, logout } = useAuthStore()
  const navigate = useNavigate()
  const location = useLocation()
  const gate      = user?.gate_assignment
  const gateLabel = GATE_LABELS[gate] || gate || 'Gate'
  const shift     = useCurrentShift(gate)

  const [showChangeShift,  setShowChangeShift]  = useState(false)
  const [showLogoutConfirm, setShowLogoutConfirm] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)

  useEffect(() => {
    setSidebarOpen(false)
  }, [location.pathname])

  // Return kiosks to their own gate's login URL after logout
  const handleLogout = (opts) => {
    const base = gate ? `/security/guard-login/${gate}` : '/security/guard-login'
    // logout() hard-navigates, so a toast wouldn't survive — pass the notice in the URL
    logout(opts?.passwordChanged ? `${base}?passwordChanged=1` : base)
  }

  const entryPath = gate ? `/security/gate/${gate}/entries` : '/security/entries'

  const navItems = [
    { name: 'Entry Management', path: entryPath,            icon: <ShieldCheck size={18} /> },
    { name: 'Parking',          path: '/security/parking',  icon: <ParkingCircle size={18} /> },
    { name: 'Vehicle Log',      path: '/security/audit',    icon: <ClipboardList size={18} /> },
  ]

  return (
    <div className="admin-layout security-layout">

      {/* Mobile menu toggle */}
      <button
        className="admin-menu-toggle"
        onClick={() => setSidebarOpen(true)}
        aria-label="Open menu"
      >
        <Menu size={22} />
      </button>

      {/* Backdrop (mobile only, shown while sidebar is open) */}
      {sidebarOpen && (
        <div className="admin-sidebar-backdrop" onClick={() => setSidebarOpen(false)} />
      )}

      {/* Sidebar */}
      <aside className={`admin-sidebar${sidebarOpen ? ' open' : ''}`}>
        <div className="sidebar-brand">
          <img src={slcLogo} alt="SLC Logo" className="brand-logo" />
          <div>
            <span className="brand-text">SLC Security</span>
            {gate && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginTop: 2 }}>
                <MapPin size={10} style={{ color: '#14A374', flexShrink: 0 }} />
                <span style={{ fontSize: 10, color: '#14A374', fontWeight: 600, letterSpacing: 0.3 }}>
                  {gateLabel}
                </span>
              </div>
            )}
          </div>
          <button
            className="admin-sidebar-close"
            onClick={() => setSidebarOpen(false)}
            aria-label="Close menu"
          >
            <X size={20} />
          </button>
        </div>

        <nav className="sidebar-nav">
          {navItems.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
              end={item.path === '/security'}
            >
              {item.icon}
              <span>{item.name}</span>
            </NavLink>
          ))}
        </nav>

        <div className="sidebar-footer">
          <div className="user-profile">
            {user?.photo_url ? (
              <img src={user.photo_url} alt={user.full_name} style={{ width: 34, height: 34, borderRadius: '50%', objectFit: 'cover', border: '2px solid #D3E1EC' }} />
            ) : (
              <div className="user-avatar">
                {user?.full_name ? user.full_name.charAt(0).toUpperCase() : 'S'}
              </div>
            )}
            <div className="user-info">
              <span className="user-name">{user?.full_name || 'Security Guard'}</span>
              {shift?.clocked_in_at ? (
                <span className="user-role" style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
                  <Clock size={9} />
                  On duty · {shiftDuration(shift.clocked_in_at)}
                </span>
              ) : (
                <span className="user-role">{gateLabel} · Security</span>
              )}
            </div>
          </div>

          {/* Change Shift button */}
          <button
            className="cs-trigger-btn"
            onClick={() => setShowChangeShift(true)}
            title="Hand over shift to incoming guard"
          >
            <RefreshCw size={13} />
            <span>Change Shift</span>
          </button>

          <div className="footer-actions">
            <button className="action-btn" title="Help & User Manual" onClick={() => navigate('/help')}>
              <HelpCircle size={18} />
            </button>
            <button className="action-btn" title="Privacy Policy & Terms" onClick={() => navigate('/policy')}>
              <Shield size={18} />
            </button>
            <button className="logout-btn" onClick={() => setShowLogoutConfirm(true)}>
              <LogOut size={16} />
              <span>Log Out</span>
            </button>
          </div>
        </div>
      </aside>

      {/* Main Column */}
      <main className="admin-main">
        <div className={`admin-content${fillHeight ? ' admin-content--fill' : ''}`}>
          {children}
        </div>
      </main>

      {/* Forced password change — first credentials login with a temporary password */}
      {user?.role === 'security' && user?.must_change_password && (
        <ForcePasswordModal onLogout={handleLogout} />
      )}

      {/* Change Shift Overlay */}
      {showChangeShift && gate && (
        <ChangeShiftModal
          gate={gate}
          gateLabel={gateLabel}
          onClose={() => setShowChangeShift(false)}
          onSuccess={() => {}}
        />
      )}

      {/* Logout Confirmation Modal */}
      {showLogoutConfirm && (
        <div className="cs-overlay" onClick={e => e.target === e.currentTarget && setShowLogoutConfirm(false)}>
          <div className="cs-modal" style={{ maxWidth: 340 }}>
            <div className="cs-modal-head">
              <div className="cs-modal-head-left">
                <LogOut size={15} style={{ color: '#C62828' }} />
                <span className="cs-modal-title">Log Out</span>
              </div>
              <button className="cs-close" onClick={() => setShowLogoutConfirm(false)}><X size={17} /></button>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <p style={{ margin: 0, fontSize: 13.5, color: '#2E4C63', lineHeight: 1.5 }}>
                Are you sure you want to log out?
              </p>
              <p style={{ margin: 0, fontSize: 12, color: '#64839C' }}>
                Your shift at <strong style={{ color: '#4A6B85' }}>{gateLabel}</strong> will be ended.
              </p>
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
              <button
                style={{
                  flex: 1, height: 38, border: '1.5px solid #D3E1EC', borderRadius: 8,
                  background: '#fff', color: '#2E4C63', fontSize: 13, fontWeight: 600,
                  cursor: 'pointer', fontFamily: 'inherit',
                }}
                onClick={() => setShowLogoutConfirm(false)}
              >
                Cancel
              </button>
              <button
                style={{
                  flex: 1, height: 38, border: 'none', borderRadius: 8,
                  background: '#C62828', color: '#fff', fontSize: 13, fontWeight: 600,
                  cursor: 'pointer', display: 'flex', alignItems: 'center',
                  justifyContent: 'center', gap: 6, fontFamily: 'inherit',
                }}
                onClick={handleLogout}
              >
                <LogOut size={14} />
                Log Out
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
