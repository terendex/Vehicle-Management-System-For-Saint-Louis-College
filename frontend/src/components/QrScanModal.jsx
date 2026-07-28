import { useEffect, useRef, useState } from 'react'
import jsQR from 'jsqr'
import { X, ScanLine, Camera } from 'lucide-react'

/**
 * Camera-based QR scanner modal (jsQR — works in all browsers).
 * Calls `onDetected(data)` once with the decoded string, then the parent
 * decides what to do and closes the modal.
 *
 * Props:
 *   onClose()          — dismiss without a scan
 *   onDetected(data)   — a QR code was read
 *   title, hint        — copy shown in the modal
 *   busy               — parent is processing the last scan (freezes detection)
 */
export default function QrScanModal({ onClose, onDetected, title = 'Scan QR Code', hint, busy = false }) {
  const videoRef  = useRef(null)
  const streamRef = useRef(null)
  const detectedRef = useRef(false)          // one-shot guard so we don't fire repeatedly
  const [cameraErr, setCameraErr] = useState('')

  useEffect(() => {
    let animFrame
    const canvas = document.createElement('canvas')
    const ctx = canvas.getContext('2d', { willReadFrequently: true })

    const start = async () => {
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
          if (!detectedRef.current && video && video.readyState >= 2 && video.videoWidth > 0) {
            canvas.width  = video.videoWidth
            canvas.height = video.videoHeight
            ctx.drawImage(video, 0, 0)
            const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height)
            const code = jsQR(imageData.data, imageData.width, imageData.height)
            if (code?.data) {
              detectedRef.current = true
              onDetected(code.data)
              return
            }
          }
          animFrame = requestAnimationFrame(scan)
        }
        animFrame = requestAnimationFrame(scan)
      } catch (err) {
        setCameraErr(`Camera access denied: ${err.message}`)
      }
    }

    start()
    return () => {
      cancelAnimationFrame(animFrame)
      streamRef.current?.getTracks().forEach(t => t.stop())
    }
  }, [onDetected])

  // Re-arm the one-shot guard when the parent finishes processing, so the guard
  // can immediately scan the next slip without reopening the modal.
  useEffect(() => { if (!busy) detectedRef.current = false }, [busy])

  return (
    <div className="em-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="em-modal" style={{ maxWidth: 380 }}>
        <div className="em-modal-head">
          <span className="em-modal-title"><ScanLine size={17} /> {title}</span>
          <button className="em-modal-close" onClick={onClose}><X size={15} /></button>
        </div>
        <div className="em-modal-body" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12 }}>
          {cameraErr ? (
            <div style={{ textAlign: 'center', color: '#C62828', fontSize: 13, padding: '20px 8px' }}>
              <Camera size={28} style={{ marginBottom: 8, opacity: 0.7 }} />
              <p style={{ margin: 0 }}>{cameraErr}</p>
            </div>
          ) : (
            <>
              <div style={{ position: 'relative', width: '100%', aspectRatio: '1 / 1', background: '#04121F', borderRadius: 12, overflow: 'hidden' }}>
                <video ref={videoRef} muted playsInline
                  style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} />
                <div style={{
                  position: 'absolute', inset: '18%',
                  border: '3px solid rgba(92, 169, 220,0.9)', borderRadius: 12,
                  boxShadow: '0 0 0 100vmax rgba(0,0,0,0.35)',
                }} />
                {busy && (
                  <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.55)', color: '#fff', fontSize: 13, gap: 8 }}>
                    <div className="em-spinner" /> Processing…
                  </div>
                )}
              </div>
              <p style={{ margin: 0, fontSize: 12, color: '#5C7B92', textAlign: 'center' }}>
                {hint || 'Hold the QR code steady inside the frame.'}
              </p>
            </>
          )}
        </div>
        <div className="em-modal-foot">
          <button type="button" className="em-btn em-btn-secondary" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  )
}
