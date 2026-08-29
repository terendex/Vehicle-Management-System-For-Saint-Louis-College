import { useState, useEffect, useRef } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { ShieldCheck, LogIn, ChevronLeft, Eye, EyeOff } from 'lucide-react'
import notify from '../../components/Feedback/notify'
import { fieldProblems } from '../../components/Feedback/formProblems'
import jsQR from 'jsqr'
import useAuthStore from '../../stores/authStore'
import { authApi } from '../../api/auth'
import { useGates } from '../../hooks/useGates'
import slcLogo from '../../assets/slclogo.jpg'
import './SecurityQRLogin.css'

export default function SecurityQRLogin() {
  const navigate                                 = useNavigate()
  const { gateParam }                            = useParams()
  const [searchParams]                           = useSearchParams()
  const { qrLogin, guardLogin, isLoading, user } = useAuthStore()

  // Set by the forced password change, which signs the guard out on purpose
  const passwordChanged = searchParams.get('passwordChanged') === '1'
  useEffect(() => {
    if (!passwordChanged) return
    notify.success('Password changed. Please sign in with your new password.', {
      title: 'Password updated',
    })
  }, [passwordChanged])

  // Kiosk mode: /security/guard-login/gate1, /security/guard-login/gate4, … locks the gate.
  // Gates are dynamic (admin can add more), so accept any gateN slug.
  const kioskGate = /^gate\d{1,3}$/.test(gateParam || '') ? gateParam : null

  const [selectedGate, setSelectedGate] = useState(kioskGate)
  const [prevKioskGate, setPrevKioskGate] = useState(kioskGate)
  const [email, setEmail]               = useState('')
  const [password, setPassword]         = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [inputToken, setInputToken]     = useState('')
  const [showToken, setShowToken]       = useState(false)
  const [status, setStatus]             = useState('idle') // idle | scanning | success | error
  const [useCamera, setUseCamera]       = useState(true)
  const [cameraErr, setCameraErr]       = useState('')
  // The QR Badge tab appears once the typed email belongs to a guard whose
  // badge is usable (first credentials login + password change completed).
  const [qrAvailable, setQrAvailable]   = useState(false)
  // Dynamic gate list (admin can add gates in System Settings)
  const { gates, gateLabel: gateName } = useGates()

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

  // Debounced per-guard check: as the guard types their email, ask the backend
  // whether that account's QR badge is usable and reveal the scanner if so.
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

  const handleGoToDashboard = () => navigate('/security/entries')

  // Camera-based QR detection using jsQR (works in all browsers).
  // Only starts once a gate is selected (valid gate for the scan callback)
  // and the typed email unlocked the scanner.
  useEffect(() => {
    if (!useCamera || !selectedGate || !qrAvailable) return
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
  }, [useCamera, selectedGate, qrAvailable]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleQRScan = async (token) => {
    if (status === 'scanning') return
    const clean = token.trim()
    if (!clean) {
      await notify.error('Enter or scan your QR token.', { title: 'Nothing to verify' })
      return
    }
    setStatus('scanning')
    try {
      const guard = await qrLogin(clean, selectedGate)
      await notify.success(`Clocked in at ${gateName(guard.gate_assignment)}.`, {
        title: `Welcome, ${guard.full_name}`,
      })
      navigate('/security/entries')
    } catch (err) {
      setInputToken('')
      await notify.error(err.message || 'QR scan failed.', {
        title: 'Scan Failed', confirmLabel: 'Try Again',
      })
      setStatus('idle')
      inputRef.current?.focus()
    }
  }

  const handleCredentialLogin = async (e) => {
    e?.preventDefault()
    if (isLoading) return
    const problems = [...fieldProblems(e?.currentTarget)]
    if (!email.trim()) problems.push('Enter your email address.')
    if (!password) problems.push('Enter your password.')
    if (await notify.validation(problems, { title: 'Cannot sign in' })) return
    try {
      const guard = await guardLogin(email.trim(), password, selectedGate)
      await notify.success(`Clocked in at ${gateName(guard.gate_assignment)}.`, {
        title: `Welcome, ${guard.full_name}`,
      })
      navigate('/security/entries')
    } catch (err) {
      notify.error(err.message || 'Login failed. Please check your credentials.', { title: 'Login Failed', confirmLabel: 'Try Again' })
      setPassword('')
    }
  }

  const handleBackToGateSelect = () => {
    setSelectedGate(null)
    setStatus('idle')
    setInputToken('')
    setEmail('')
    setPassword('')
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
        <div className="header-logo-group">
          <img src={slcLogo} alt="Saint Louis College Logo" className="header-logo" />
          <div className="header-text">
            <span className="header-title">SAINT LOUIS COLLEGE</span>
            <span className="header-subtitle">Smart Parking and Vehicle Verification System</span>
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
                {gates.map(g => {
                  const [main, desc] = g.label.split('—').map(s => s.trim())
                  return (
                    <button key={g.gate_id} className="sqr-gate-item" onClick={() => setSelectedGate(g.gate_id)}>
                      <div className="sqr-gate-icon">{g.gate_id.replace(/\D/g, '')}</div>
                      <div className="sqr-gate-text">
                        <span className="sqr-gate-label">{main || g.gate_id}</span>
                        <span className="sqr-gate-desc">{desc || 'Campus gate'}</span>
                      </div>
                      <ChevronLeft size={16} className="sqr-gate-arrow" />
                    </button>
                  )
                })}
              </div>

              {user?.role === 'security' && (
                <div className="sqr-active-guard">
                  <span className="sqr-active-dot" />
                  <span>
                    Active: <strong>{user.full_name}</strong>
                    {user.gate_assignment && ` — ${gateName(user.gate_assignment)}`}
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
              {status === 'scanning' ? (
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
                    <span className="sqr-gate-pill">{gateName(selectedGate)}</span>
                  </div>

                  <div className="sqr-card-header">
                    <ShieldCheck size={22} className="sqr-card-icon" />
                    <div>
                      <h1 className="sqr-card-title">Guard Login</h1>
                      <p className="sqr-card-subtitle">
                        Sign in with your guard credentials to clock in at <strong>{gateName(selectedGate)}</strong>
                      </p>
                    </div>
                  </div>

                  <form className="sqr-cred-form" onSubmit={handleCredentialLogin} noValidate>
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
                      disabled={isLoading}
                    >
                      {isLoading ? <span className="sqr-btn-spinner" /> : <LogIn size={17} />}
                      {isLoading ? 'Signing in…' : 'Login & Clock In'}
                    </button>
                    <button
                      type="button"
                      className="sqr-forgot-link"
                      onClick={() => navigate('/forgot-password', { state: { from: 'security' } })}
                    >
                      Forgot password?
                    </button>
                  </form>

                  {/* QR scanner appears once the typed email matches a badge-eligible guard */}
                  {qrAvailable && (
                    <>
                      <div className="sqr-divider"><span>or scan your QR badge</span></div>
                      {useCamera ? (
                        <div className="sqr-camera-wrap">
                          <video ref={videoRef} className="sqr-video" muted playsInline />
                          <p className="sqr-camera-hint">Point camera at your QR card</p>
                          <button className="sqr-toggle" onClick={() => setUseCamera(false)}>Use text input instead</button>
                        </div>
                      ) : (
                        <div className="sqr-input-section">
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
                              disabled={isLoading}
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
          Forgot your password? Use the link above to reset it.
          Contact admin if your badge is lost or damaged.
        </p>
      </main>
    </div>
  )
}
