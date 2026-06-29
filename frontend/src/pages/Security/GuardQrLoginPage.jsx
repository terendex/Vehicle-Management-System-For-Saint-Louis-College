import { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import jsQR from 'jsqr'
import { QrCode, User, ShieldCheck, AlertCircle, CheckCircle, Camera, CameraOff } from 'lucide-react'
import useAuthStore from '../../stores/authStore'
import slcLogo from '../../assets/slclogo.jpg'
import './GuardQrLoginPage.css'

const SCAN_INTERVAL_MS = 250

export default function GuardQrLoginPage() {
  const navigate     = useNavigate()
  const { guardQrLogin, isLoading, user, isAuthenticated } = useAuthStore()

  const [cameraOn, setCameraOn]   = useState(false)
  const [error, setError]         = useState('')
  const [success, setSuccess]     = useState(null)
  const [manualQr, setManualQr]   = useState('')
  const [scanning, setScanning]   = useState(false)

  const videoRef      = useRef(null)
  const canvasRef     = useRef(document.createElement('canvas'))
  const streamRef     = useRef(null)
  const intervalRef   = useRef(null)
  const processingRef = useRef(false)

  useEffect(() => {
    if (isAuthenticated && user?.role === 'security') {
      navigate('/security/entries', { replace: true })
    }
  }, [isAuthenticated, user, navigate])

  const handleQrData = useCallback(async (qr_data) => {
    if (processingRef.current) return
    if (!qr_data?.startsWith('SLC-GUARD:')) {
      setError('Invalid QR code. Please use your official guard badge.')
      return
    }
    processingRef.current = true
    setError('')
    try {
      const guard = await guardQrLogin(qr_data)
      setSuccess(guard)
      stopCamera()
      setTimeout(() => navigate('/security/entries', { replace: true }), 1800)
    } catch (err) {
      setError(err.message || 'QR login failed.')
    } finally {
      processingRef.current = false
    }
  }, [guardQrLogin, navigate])

  const startScanning = useCallback(() => {
    setScanning(true)
    const canvas = canvasRef.current
    const ctx = canvas.getContext('2d', { willReadFrequently: true })
    intervalRef.current = setInterval(() => {
      const video = videoRef.current
      if (!video || processingRef.current) return
      if (video.readyState < video.HAVE_ENOUGH_DATA) return
      canvas.width  = video.videoWidth
      canvas.height = video.videoHeight
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
      const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height)
      const code = jsQR(imageData.data, imageData.width, imageData.height, {
        inversionAttempts: 'dontInvert',
      })
      if (code) handleQrData(code.data)
    }, SCAN_INTERVAL_MS)
  }, [handleQrData])

  const startCamera = async () => {
    setError('')
    if (!navigator.mediaDevices?.getUserMedia) {
      setError('Camera not available. Make sure the page is served over HTTPS.')
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 } }
      })
      streamRef.current = stream
      videoRef.current.srcObject = stream
      videoRef.current.play().catch(e => console.error('[GuardQR] play() rejected:', e))
      setCameraOn(true)
      startScanning()
    } catch (err) {
      setError(`Camera error: ${err.name} — ${err.message}`)
    }
  }

  const stopCamera = () => {
    clearInterval(intervalRef.current)
    setScanning(false)
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(t => t.stop())
      streamRef.current = null
    }
    setCameraOn(false)
  }

  const handleManualSubmit = (e) => {
    e.preventDefault()
    handleQrData(manualQr.trim())
  }

  useEffect(() => () => stopCamera(), [])

  return (
    <div className="gqr-page">
      <header className="gqr-header">
        <img src={slcLogo} alt="SLC Logo" className="gqr-logo" />
        <div>
          <span className="gqr-header-title">SAINT LOUIS COLLEGE</span>
          <span className="gqr-header-sub">Guard Station Login</span>
        </div>
      </header>

      <main className="gqr-main">
        <div className="gqr-card">
          <div className="gqr-card-icon">
            <ShieldCheck size={32} color="#2A2B61" />
          </div>
          <h1 className="gqr-title">Guard QR Login</h1>
          <p className="gqr-subtitle">
            Scan your badge QR code to clock in. The previous guard will be automatically signed out.
          </p>

          {success ? (
            <div className="gqr-success">
              <CheckCircle size={28} color="#16a34a" />
              <div>
                <p className="gqr-success-name">{success.full_name}</p>
                <p className="gqr-success-code">{success.user_code}</p>
                <p className="gqr-success-msg">Logging you in…</p>
              </div>
            </div>
          ) : (
            <>
              {/* Camera QR Scanner — always shown */}
              <div className="gqr-cam-section">
                <div className="gqr-viewport">
                  {/* video always in DOM so srcObject can be set before cameraOn state updates */}
                  <video ref={videoRef} className="gqr-video" playsInline muted autoPlay />
                  {!cameraOn && (
                    <div className="gqr-cam-off" style={{ position: 'absolute', inset: 0 }}>
                      <QrCode size={48} color="#9CA3AF" />
                      <p>Camera is off</p>
                    </div>
                  )}
                  {cameraOn && (
                    <>
                      <div className="gqr-scan-frame" />
                      {scanning && <div className="gqr-scan-line" />}
                      <div className="gqr-cam-badge">
                        <span className="gqr-cam-dot" /> SCANNING
                      </div>
                    </>
                  )}
                </div>

                <button
                  className={`gqr-btn ${cameraOn ? 'gqr-btn-danger' : 'gqr-btn-primary'}`}
                  onClick={cameraOn ? stopCamera : startCamera}
                  disabled={isLoading || !!success}
                >
                  {cameraOn ? <><CameraOff size={16} /> Stop Camera</> : <><Camera size={16} /> Start Camera</>}
                </button>
              </div>

              {/* Divider */}
              <div className="gqr-divider">
                <span>or enter QR code manually</span>
              </div>

              {/* Manual fallback */}
              <form className="gqr-manual" onSubmit={handleManualSubmit}>
                <input
                  className="gqr-input"
                  value={manualQr}
                  onChange={e => setManualQr(e.target.value)}
                  placeholder="SLC-GUARD:SLC-SEC-000001:uuid…"
                  disabled={isLoading || !!success}
                />
                <button
                  type="submit"
                  className="gqr-btn gqr-btn-primary"
                  disabled={isLoading || !manualQr.trim() || !!success}
                >
                  {isLoading ? 'Logging in…' : <><User size={15} /> Login</>}
                </button>
              </form>

              {error && (
                <div className="gqr-error">
                  <AlertCircle size={15} />
                  <span>{error}</span>
                </div>
              )}
            </>
          )}
        </div>
      </main>
    </div>
  )
}
