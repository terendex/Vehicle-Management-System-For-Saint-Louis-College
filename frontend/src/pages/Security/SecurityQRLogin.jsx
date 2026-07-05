import { useState, useEffect, useRef } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ScanLine, ShieldCheck, KeyRound, LogIn, AlertCircle, CheckCircle, ChevronLeft, Eye, EyeOff } from 'lucide-react'
import { toast } from 'sonner'
import jsQR from 'jsqr'
import useAuthStore from '../../stores/authStore'
import slcLogo from '../../assets/slclogo.jpg'
import './SecurityQRLogin.css'

const GATE_LABELS = { gate1: 'Gate 1', gate4: 'Gate 4' }

export default function SecurityQRLogin() {
  const navigate                                 = useNavigate()
  const { gateParam }                            = useParams()
  const { qrLogin, guardLogin, isLoading, user } = useAuthStore()

  // Kiosk mode: /security/guard-login/gate1 or /security/guard-login/gate4 locks the gate
  const kioskGate = GATE_LABELS[gateParam] ? gateParam : null

  const [selectedGate, setSelectedGate] = useState(kioskGate)
  const [prevKioskGate, setPrevKioskGate] = useState(kioskGate)
  const [method, setMethod]             = useState('credentials') // credentials | qr
  const [email, setEmail]               = useState('')
  const [password, setPassword]         = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [credError, setCredError]       = useState('')
  const [inputToken, setInputToken]     = useState('')
  const [showToken, setShowToken]       = useState(false)
  const [status, setStatus]             = useState('idle') // idle | scanning | success | error
  const [guardInfo, setGuardInfo]       = useState(null)
  const [errorMsg, setErrorMsg]         = useState('')
  const [useCamera, setUseCamera]       = useState(true)
  const [cameraErr, setCameraErr]       = useState('')

  const inputRef  = useRef(null)
  const videoRef  = useRef(null)
  const streamRef = useRef(null)

  // Trap back-button navigation — hard replace beats React Router's popstate handler.
  // In kiosk mode the trap pins the browser to the gate-specific URL.
  useEffect(() => {
    const trapUrl = kioskGate ? `/security/guard-login/${kioskGate}` : '/security/guard-login'
    window.history.pushState(null, '', window.location.href)
    const handlePop = () => window.location.replace(trapUrl)
    window.addEventListener('popstate', handlePop)
    return () => window.removeEventListener('popstate', handlePop)
  }, [kioskGate])

  // Invalid gate in URL (e.g. /security/guard-login/gate9) → fall back to gate selector
  useEffect(() => {
    if (gateParam && !kioskGate) navigate('/security/guard-login', { replace: true })
  }, [gateParam, kioskGate, navigate])

  // Kiosk URL is the source of truth for the gate. If the param changes while
  // mounted (/guard-login/gate1 → /guard-login/gate4, no remount), re-lock the
  // gate during render — cheaper than syncing in an effect.
  if (kioskGate && kioskGate !== prevKioskGate) {
    setPrevKioskGate(kioskGate)
    setSelectedGate(kioskGate)
  }

  useEffect(() => {
    if (selectedGate && method === 'qr' && !useCamera) inputRef.current?.focus()
  }, [selectedGate, method, useCamera])

  const handleGoToDashboard = () => navigate('/security/entries')

  // Camera-based QR detection using jsQR (works in all browsers).
  // Only starts once a gate is selected so the scan callback always has a valid gate.
  useEffect(() => {
    if (!useCamera || !selectedGate || method !== 'qr') return
    let animFrame
    const canvas = document.createElement('canvas')
    const ctx = canvas.getContext('2d', { willReadFrequently: true })

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
              handleQRScan(code.data)
              return
            }
          }
          animFrame = requestAnimationFrame(scan)
        }
        animFrame = requestAnimationFrame(scan)
      } catch (err) {
        setCameraErr(`Camera access denied: ${err.message}`)
        setUseCamera(false)
      }
    }

    startCamera()
    return () => {
      cancelAnimationFrame(animFrame)
      streamRef.current?.getTracks().forEach(t => t.stop())
    }
  }, [useCamera, selectedGate, method]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleQRScan = async (token) => {
    if (status === 'scanning') return
    const clean = token.trim()
    if (!clean) return
    setStatus('scanning')
    setErrorMsg('')
    try {
      const guard = await qrLogin(clean, selectedGate)
      setGuardInfo(guard)
      setStatus('success')
      toast.success(`Welcome, ${guard.full_name}! Clocked in at ${GATE_LABELS[guard.gate_assignment] || guard.gate_assignment}.`)
      setTimeout(() => navigate('/security/entries'), 1800)
    } catch (err) {
      setErrorMsg(err.message || 'QR scan failed.')
      setStatus('error')
      setInputToken('')
      setTimeout(() => { setStatus('idle'); inputRef.current?.focus() }, 3000)
    }
  }

  const handleCredentialLogin = async (e) => {
    e?.preventDefault()
    if (!email.trim() || !password || isLoading) return
    setCredError('')
    try {
      const guard = await guardLogin(email.trim(), password, selectedGate)
      setGuardInfo(guard)
      setStatus('success')
      toast.success(`Welcome, ${guard.full_name}! Clocked in at ${GATE_LABELS[guard.gate_assignment] || guard.gate_assignment}.`)
      setTimeout(() => navigate('/security/entries'), 1800)
    } catch (err) {
      setCredError(err.message || 'Login failed. Please check your credentials.')
      setPassword('')
    }
  }

  const switchMethod = (next) => {
    setMethod(next)
    setCredError('')
    setErrorMsg('')
    setStatus('idle')
    setInputToken('')
    if (next === 'qr') setUseCamera(true)
    else streamRef.current?.getTracks().forEach(t => t.stop())
  }

  const handleBackToGateSelect = () => {
    setSelectedGate(null)
    setStatus('idle')
    setInputToken('')
    setErrorMsg('')
    setCredError('')
    setEmail('')
    setPassword('')
    setMethod('credentials')
    setUseCamera(true)
    streamRef.current?.getTracks().forEach(t => t.stop())
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') { e.preventDefault(); handleQRScan(inputToken) }
  }

  return (
    <div className="sqr-page">
      {/* Header — same as login page */}
      <header className="sqr-header">
        <div className="sqr-header-content">
          <img src={slcLogo} alt="Saint Louis College Logo" className="sqr-header-logo" />
          <div className="sqr-header-text">
            <span className="sqr-header-title">SAINT LOUIS COLLEGE</span>
            <span className="sqr-header-subtitle">Smart Parking and Vehicle Verification System</span>
          </div>
        </div>
      </header>

      <main className="sqr-main">
        <div className="sqr-card">

          {/* ── STEP 1: Gate selector ── */}
          {!selectedGate ? (
            <>
              <div className="sqr-card-header">
                <ShieldCheck size={22} className="sqr-card-icon" />
                <div>
                  <h1 className="sqr-card-title">Guard Gate Login</h1>
                  <p className="sqr-card-subtitle">Select your assigned gate to begin your shift</p>
                </div>
              </div>

              <div className="sqr-gate-list">
                <button className="sqr-gate-item" onClick={() => setSelectedGate('gate1')}>
                  <div className="sqr-gate-icon">1</div>
                  <div className="sqr-gate-text">
                    <span className="sqr-gate-label">Gate 1</span>
                    <span className="sqr-gate-desc">Main Entrance</span>
                  </div>
                  <ChevronLeft size={16} className="sqr-gate-arrow" />
                </button>
                <button className="sqr-gate-item" onClick={() => setSelectedGate('gate4')}>
                  <div className="sqr-gate-icon">4</div>
                  <div className="sqr-gate-text">
                    <span className="sqr-gate-label">Gate 4</span>
                    <span className="sqr-gate-desc">Side Entrance</span>
                  </div>
                  <ChevronLeft size={16} className="sqr-gate-arrow" />
                </button>
              </div>

              {user?.role === 'security' && (
                <div className="sqr-active-guard">
                  <span className="sqr-active-dot" />
                  <span>
                    Active: <strong>{user.full_name}</strong>
                    {user.gate_assignment && ` — ${GATE_LABELS[user.gate_assignment] || user.gate_assignment}`}
                  </span>
                  <button className="sqr-dash-link" onClick={handleGoToDashboard}>
                    Go to dashboard →
                  </button>
                </div>
              )}
            </>
          ) : (
            /* ── STEP 2: Scan ── */
            <>
              {status === 'success' && guardInfo ? (
                <div className="sqr-state">
                  <div className="sqr-state-icon success"><CheckCircle size={32} /></div>
                  <h2 className="sqr-state-title">Welcome, {guardInfo.full_name}!</h2>
                  <p className="sqr-state-sub">
                    Clocked in at <strong>{GATE_LABELS[guardInfo.gate_assignment] || guardInfo.gate_assignment}</strong>
                  </p>
                  <p className="sqr-state-hint">Redirecting to dashboard…</p>
                </div>
              ) : status === 'error' ? (
                <div className="sqr-state">
                  <div className="sqr-state-icon error"><AlertCircle size={32} /></div>
                  <h2 className="sqr-state-title">Scan Failed</h2>
                  <p className="sqr-state-sub">{errorMsg}</p>
                  <p className="sqr-state-hint">Retrying in a moment…</p>
                </div>
              ) : status === 'scanning' ? (
                <div className="sqr-state">
                  <div className="sqr-spinner" />
                  <p className="sqr-state-sub">Verifying QR code…</p>
                </div>
              ) : (
                <>
                  <div className="sqr-scan-topbar">
                    {kioskGate ? (
                      <span className="sqr-kiosk-tag"><ShieldCheck size={14} /> Kiosk mode</span>
                    ) : (
                      <button className="sqr-back" onClick={handleBackToGateSelect}>
                        <ChevronLeft size={15} /> Change gate
                      </button>
                    )}
                    <span className="sqr-gate-pill">{GATE_LABELS[selectedGate]}</span>
                  </div>

                  <div className="sqr-method-tabs">
                    <button
                      className={`sqr-method-tab ${method === 'credentials' ? 'active' : ''}`}
                      onClick={() => switchMethod('credentials')}
                    >
                      <KeyRound size={15} /> Credentials
                    </button>
                    <button
                      className={`sqr-method-tab ${method === 'qr' ? 'active' : ''}`}
                      onClick={() => switchMethod('qr')}
                    >
                      <ScanLine size={15} /> QR Badge
                    </button>
                  </div>

                  {method === 'credentials' ? (
                    <>
                      <div className="sqr-card-header">
                        <ShieldCheck size={22} className="sqr-card-icon" />
                        <div>
                          <h1 className="sqr-card-title">Guard Login</h1>
                          <p className="sqr-card-subtitle">
                            Sign in with your guard credentials to clock in at <strong>{GATE_LABELS[selectedGate]}</strong>
                          </p>
                        </div>
                      </div>

                      <form className="sqr-cred-form" onSubmit={handleCredentialLogin}>
                        {credError && (
                          <div className="sqr-cred-error" role="alert">
                            <AlertCircle size={15} />
                            <span>{credError}</span>
                          </div>
                        )}
                        <div className="sqr-cred-group">
                          <label className="sqr-cred-label" htmlFor="guard-email">Email</label>
                          <input
                            id="guard-email"
                            className="sqr-input"
                            type="email"
                            value={email}
                            onChange={e => setEmail(e.target.value)}
                            placeholder="Enter your email address"
                            autoComplete="username"
                            required
                          />
                        </div>
                        <div className="sqr-cred-group">
                          <label className="sqr-cred-label" htmlFor="guard-password">Password</label>
                          <div className="sqr-input-wrap">
                            <input
                              id="guard-password"
                              className="sqr-input"
                              type={showPassword ? 'text' : 'password'}
                              value={password}
                              onChange={e => setPassword(e.target.value)}
                              placeholder="••••••••"
                              autoComplete="current-password"
                              required
                            />
                            <button type="button" className="sqr-eye" onClick={() => setShowPassword(v => !v)}>
                              {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                            </button>
                          </div>
                        </div>
                        <button
                          type="submit"
                          className="sqr-cred-submit"
                          disabled={!email.trim() || !password || isLoading}
                        >
                          {isLoading ? <span className="sqr-btn-spinner" /> : <LogIn size={17} />}
                          {isLoading ? 'Signing in…' : 'Login & Clock In'}
                        </button>
                      </form>
                    </>
                  ) : (
                    <>
                  <div className="sqr-card-header">
                    <ScanLine size={22} className="sqr-card-icon" />
                    <div>
                      <h1 className="sqr-card-title">Scan Your QR Badge</h1>
                      <p className="sqr-card-subtitle">
                        Hold your QR card to the scanner to clock in at <strong>{GATE_LABELS[selectedGate]}</strong>
                      </p>
                    </div>
                  </div>

                  {useCamera ? (
                    <div className="sqr-camera-wrap">
                      <video ref={videoRef} className="sqr-video" muted playsInline />
                      <p className="sqr-camera-hint">Point camera at your QR card</p>
                      <button className="sqr-toggle" onClick={() => setUseCamera(false)}>Use text input instead</button>
                    </div>
                  ) : (
                    <div className="sqr-input-section">
                      <div className="sqr-scan-pulse">
                        <ScanLine size={36} className="sqr-scan-anim" />
                      </div>
                      <p className="sqr-input-hint">USB scanner auto-fills and submits on Enter</p>
                      <div className="sqr-input-row">
                        <div className="sqr-input-wrap">
                          <input
                            ref={inputRef}
                            className="sqr-input"
                            type={showToken ? 'text' : 'password'}
                            value={inputToken}
                            onChange={e => setInputToken(e.target.value)}
                            onKeyDown={handleKeyDown}
                            placeholder="QR token…"
                            autoComplete="off"
                            spellCheck={false}
                          />
                          <button type="button" className="sqr-eye" onClick={() => setShowToken(v => !v)}>
                            {showToken ? <EyeOff size={16} /> : <Eye size={16} />}
                          </button>
                        </div>
                        <button
                          className="sqr-submit"
                          disabled={!inputToken.trim() || isLoading}
                          onClick={() => handleQRScan(inputToken)}
                        >
                          <LogIn size={18} />
                        </button>
                      </div>
                      {cameraErr && <p className="sqr-err-text">{cameraErr}</p>}
                      <button className="sqr-toggle" onClick={() => { setCameraErr(''); setUseCamera(true) }}>
                        Use camera instead
                      </button>
                    </div>
                  )}
                    </>
                  )}
                </>
              )}
            </>
          )}
        </div>

        <p className="sqr-footer">
          Guard credentials and QR badges are managed by the system administrator.
          Contact admin if you forgot your password or your badge is lost or damaged.
        </p>
      </main>
    </div>
  )
}
