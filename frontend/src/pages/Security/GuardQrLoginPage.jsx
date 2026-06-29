import { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { QrCode, User, ShieldCheck, AlertCircle, CheckCircle, Camera, CameraOff } from 'lucide-react'
import useAuthStore from '../../stores/authStore'
import slcLogo from '../../assets/slclogo.jpg'
import './GuardQrLoginPage.css'

// Uses BarcodeDetector (Chromium) to decode QR codes from the camera.
// Falls back gracefully with a manual-paste field if not available.

const SCAN_INTERVAL_MS = 500

export default function GuardQrLoginPage() {
  const navigate     = useNavigate()
  const { guardQrLogin, isLoading, user, isAuthenticated } = useAuthStore()

  const [cameraOn, setCameraOn]   = useState(false)
  const [error, setError]         = useState('')
  const [success, setSuccess]     = useState(null)
  const [manualQr, setManualQr]   = useState('')
  const [scanning, setScanning]   = useState(false)
  const [hasBarcodeApi, setHasBarcodeApi] = useState(false)

  const videoRef     = useRef(null)
  const streamRef    = useRef(null)
  const intervalRef  = useRef(null)
  const detectorRef  = useRef(null)
  const processingRef = useRef(false)

  // If a guard is already logged in, redirect to their gate
  useEffect(() => {
    if (isAuthenticated && user?.role === 'security') {
      navigate('/security/entries', { replace: true })
    }
  }, [isAuthenticated, user, navigate])

  // Check BarcodeDetector API availability
  useEffect(() => {
    if ('BarcodeDetector' in window) {
      BarcodeDetector.getSupportedFormats().then(formats => {
        if (formats.includes('qr_code')) {
          detectorRef.current = new BarcodeDetector({ formats: ['qr_code'] })
          setHasBarcodeApi(true)
        }
      }).catch(() => {})
    }
  }, [])

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

  const startCamera = async () => {
    setError('')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 } }
      })
      streamRef.current = stream
      if (videoRef.current) {
        videoRef.current.srcObject = stream
        videoRef.current.play()
      }
      setCameraOn(true)
      startScanning()
    } catch {
      setError('Could not access camera. Please allow camera permission and try again.')
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

  const startScanning = () => {
    if (!detectorRef.current) return
    setScanning(true)
    intervalRef.current = setInterval(async () => {
      if (!videoRef.current || processingRef.current) return
      try {
        const barcodes = await detectorRef.current.detect(videoRef.current)
        if (barcodes.length > 0) {
          handleQrData(barcodes[0].rawValue)
        }
      } catch { /* ignore decode errors */ }
    }, SCAN_INTERVAL_MS)
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
              {/* Camera QR Scanner */}
              {hasBarcodeApi && (
                <div className="gqr-cam-section">
                  <div className="gqr-viewport">
                    {cameraOn ? (
                      <>
                        <video ref={videoRef} className="gqr-video" playsInline muted />
                        <div className="gqr-scan-frame" />
                        {scanning && <div className="gqr-scan-line" />}
                        <div className="gqr-cam-badge">
                          <span className="gqr-cam-dot" /> SCANNING
                        </div>
                      </>
                    ) : (
                      <div className="gqr-cam-off">
                        <QrCode size={48} color="#9CA3AF" />
                        <p>Camera is off</p>
                      </div>
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
              )}

              {/* Divider */}
              <div className="gqr-divider">
                <span>{hasBarcodeApi ? 'or enter QR code manually' : 'Enter QR code payload'}</span>
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
