import jsQR from 'jsqr'

/**
 * Start a camera into `video` and call `onCode(data)` once per QR it reads.
 * Returns `stop()`, which is safe to call at any time — including while the
 * camera is still being opened.
 *
 * Every QR scanner (guard scan modal, gate login, change shift) goes through
 * here because the hand-rolled copies each broke the webcam the same ways:
 *  - stop() ran before getUserMedia resolved, so the late stream was never
 *    stopped and held the webcam; the next open failed "Could not start video
 *    source" (Windows webcams are exclusive).
 *  - facingMode 'environment' is a phone idea; some USB webcams reject the
 *    constraint set outright, so we retry with a plain { video: true }.
 *  - On plain http:// from another PC (not localhost) the browser hides
 *    navigator.mediaDevices entirely, which read as a cryptic TypeError.
 *  - A throw inside the rAF loop silently ended scanning.
 *
 * `onCode` returning true keeps scanning; anything else pauses it until
 * `resume()` is called on the returned handle.
 */
export function startQrCamera(video, onCode, onError) {
  let stream = null
  let stopped = false
  let paused = false
  let frame = 0
  const canvas = document.createElement('canvas')
  const ctx = canvas.getContext('2d', { willReadFrequently: true })

  const stop = () => {
    stopped = true
    cancelAnimationFrame(frame)
    stream?.getTracks().forEach(t => t.stop())
    stream = null
    if (video && video.srcObject) video.srcObject = null
  }

  const scan = () => {
    if (stopped) return
    try {
      if (!paused && ctx && video && video.readyState >= 2 && video.videoWidth > 0) {
        canvas.width  = video.videoWidth
        canvas.height = video.videoHeight
        ctx.drawImage(video, 0, 0)
        const img = ctx.getImageData(0, 0, canvas.width, canvas.height)
        const code = jsQR(img.data, img.width, img.height)
        if (code?.data && onCode(code.data) !== true) paused = true
      }
    } catch {
      // One bad frame must not end the loop.
    } finally {
      if (!stopped) frame = requestAnimationFrame(scan)
    }
  }

  const open = async () => {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error(window.isSecureContext
        ? 'This browser has no camera support.'
        : 'The camera only works on https:// or http://localhost. Open this page from the gate PC itself, or over https.')
    }
    try {
      return await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'environment', width: { ideal: 640 }, height: { ideal: 640 } },
      })
    } catch (err) {
      if (err?.name === 'NotAllowedError' || err?.name === 'SecurityError') throw err
      return navigator.mediaDevices.getUserMedia({ video: true })
    }
  }

  const describe = (err) => {
    switch (err?.name) {
      case 'NotAllowedError':
      case 'SecurityError':    return 'Camera permission was blocked. Allow the camera in the browser address bar, then try again.'
      case 'NotFoundError':
      case 'OverconstrainedError': return 'No camera was found on this PC.'
      case 'NotReadableError':
      case 'AbortError':       return 'The camera is in use by another app or browser tab. Close it, then try again.'
      default:                 return err?.message || 'The camera could not be started.'
    }
  }

  open()
    .then(s => {
      if (stopped || !video) { s.getTracks().forEach(t => t.stop()); return }
      stream = s
      video.srcObject = s
      video.play().catch(() => {})   // "interrupted by a new load" is harmless
      frame = requestAnimationFrame(scan)
    })
    .catch(err => { if (!stopped) onError?.(describe(err)) })

  return { stop, resume: () => { paused = false } }
}
