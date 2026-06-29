import { useState, useEffect, useRef } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
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
  ScanLine,
  LogIn,
  Eye,
  EyeOff,
} from 'lucide-react'
import { toast } from 'sonner'
import jsQR from 'jsqr'
import slcLogo from '../../assets/slclogo.jpg'
import useAuthStore from '../../stores/authStore'
import { getCurrentShifts } from '../../api/scanning'
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
  const { qrLogin } = useAuthStore()

  const [status,     setStatus]     = useState('idle')  // idle | scanning | success | error
  const [guardInfo,  setGuardInfo]  = useState(null)
  const [errorMsg,   setErrorMsg]   = useState('')
  const [useCamera,  setUseCamera]  = useState(true)
  const [inputToken, setInputToken] = useState('')
  const [showToken,  setShowToken]  = useState(false)
  const [cameraErr,  setCameraErr]  = useState('')

  const videoRef  = useRef(null)
  const streamRef = useRef(null)
  const inputRef  = useRef(null)

  // QR camera scanning loop
  useEffect(() => {
    if (!useCamera || status !== 'idle') return
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
  }, [useCamera, status]) // eslint-disable-line react-hooks/exhaustive-deps

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

        ) : useCamera ? (
          <>
            <p className="cs-instruction">
              Scan the incoming guard's QR badge to hand over the shift at <strong>{gateLabel}</strong>
            </p>
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
            <p className="cs-instruction">
              Enter the incoming guard's QR token manually, or use a USB barcode scanner
            </p>
            <div className="cs-input-row">
              <input
                ref={inputRef}
                autoFocus
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
      </div>
    </div>
  )
}

// ── Layout ────────────────────────────────────────────────────────────────────
export default function SecurityLayout({ children, fillHeight = false }) {
  const { user, logout } = useAuthStore()
  const navigate = useNavigate()
  const gate      = user?.gate_assignment
  const gateLabel = GATE_LABELS[gate] || gate || 'Gate'
  const shift     = useCurrentShift(gate)

  const [showChangeShift, setShowChangeShift] = useState(false)

  const handleLogout = () => { logout('/security/qr-login') }

  const entryPath = gate ? `/security/gate/${gate}/entries` : '/security/entries'

  const navItems = [
    { name: 'Entry Management', path: entryPath,            icon: <ShieldCheck size={18} /> },
    { name: 'Parking',          path: '/security/parking',  icon: <ParkingCircle size={18} /> },
    { name: 'Audit Log',        path: '/security/audit',    icon: <ClipboardList size={18} /> },
  ]

  return (
    <div className="admin-layout security-layout">

      {/* Sidebar */}
      <aside className="admin-sidebar">
        <div className="sidebar-brand">
          <img src={slcLogo} alt="SLC Logo" className="brand-logo" />
          <div>
            <span className="brand-text">SLC Security</span>
            {gate && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginTop: 2 }}>
                <MapPin size={10} style={{ color: '#10b981', flexShrink: 0 }} />
                <span style={{ fontSize: 10, color: '#10b981', fontWeight: 600, letterSpacing: 0.3 }}>
                  {gateLabel}
                </span>
              </div>
            )}
          </div>
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
              <img src={user.photo_url} alt={user.full_name} style={{ width: 34, height: 34, borderRadius: '50%', objectFit: 'cover', border: '2px solid #E2E6EE' }} />
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
            <button className="logout-btn" onClick={handleLogout}>
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

      {/* Change Shift Overlay */}
      {showChangeShift && gate && (
        <ChangeShiftModal
          gate={gate}
          gateLabel={gateLabel}
          onClose={() => setShowChangeShift(false)}
          onSuccess={() => {}}
        />
      )}
    </div>
  )
}
