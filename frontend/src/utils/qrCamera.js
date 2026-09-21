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
// The shape to notice: `stop()` is created FIRST and `open()` is async, so the
// caller holds a working stop() before the camera has even been asked for.
// That ordering is the fix for the worst bug in the list above — a component
// unmounting mid-open used to leave the stream unowned and the webcam locked.
export function startQrCamera(video, onCode, onError) {
  let stream = null
  let stopped = false                           // set by stop(); every async path re-checks it
  let paused = false                            // set when onCode declines; cleared by resume()
  let frame = 0                                 // the rAF handle, so the loop can be cancelled
  const canvas = document.createElement('canvas')
  const ctx = canvas.getContext('2d', { willReadFrequently: true })

  const stop = () => {
    stopped = true                              // first, so an in-flight open() knows to discard its result
    cancelAnimationFrame(frame)
    // Each TRACK is stopped, not just the stream — a stream that is merely
    // dropped keeps its tracks live, and a live track holds the webcam. On
    // Windows the device is exclusive, so the next open fails outright.
    stream?.getTracks().forEach(t => t.stop())
    stream = null
    if (video && video.srcObject) video.srcObject = null   // detach, or the element keeps a reference the GC will not collect
  }

  const scan = () => {
    if (stopped) return
    try {
      // Four conditions before touching a pixel: not paused, a context
      // exists, the element exists, it has decoded metadata (readyState >= 2
      // = HAVE_CURRENT_DATA), and it actually has dimensions. drawImage on a
      // video that is not ready throws or silently draws nothing.
      if (!paused && ctx && video && video.readyState >= 2 && video.videoWidth > 0) {
        canvas.width  = video.videoWidth
        canvas.height = video.videoHeight
        ctx.drawImage(video, 0, 0)
        const img = ctx.getImageData(0, 0, canvas.width, canvas.height)
        const code = jsQR(img.data, img.width, img.height)
        // `!== true`, not `=== false`: a handler that returns nothing (the
        // common case) pauses, so a scanner stops after one code unless it
        // explicitly asks to keep going. Pausing rather than stopping keeps
        // the camera open, so resuming costs nothing.
        if (code?.data && onCode(code.data) !== true) paused = true
      }
    } catch {
      // One bad frame must not end the loop.
    } finally {
      // Rescheduled in `finally` — the same rule as the camera render loop in
      // CameraContext, and for the same reason: a throw above would otherwise
      // end scanning silently and forever.
      if (!stopped) frame = requestAnimationFrame(scan)
    }
  }

  const open = async () => {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error(window.isSecureContext
        ? 'This browser has no camera support.'
        : 'This browser blocks the camera on a plain http:// page. Use the secure (https://) address of this server.')
    }
    try {
      // `ideal`, not `exact`: a camera that cannot do 640x640 negotiates the
      // nearest size instead of rejecting the request.
      return await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'environment', width: { ideal: 640 }, height: { ideal: 640 } },
      })
    } catch (err) {
      // A permission refusal is final — retrying would only re-prompt, or
      // silently fail again. Anything else is a constraint the device did not
      // like, so drop them all and take whatever camera exists.
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
      // THE race this module exists to close. If stop() ran while open() was
      // still pending, the stream arrives with nobody to own it — so it is
      // stopped here and now. Without this the webcam stays held.
      if (stopped || !video) { s.getTracks().forEach(t => t.stop()); return }
      stream = s
      video.srcObject = s
      video.play().catch(() => {})   // "interrupted by a new load" is harmless
      frame = requestAnimationFrame(scan)
    })
    .catch(err => { if (!stopped) onError?.(describe(err)) })

  // Returned synchronously, before the camera is open — which is what lets a
  // caller stop() from a cleanup function that may run at any moment.
  return { stop, resume: () => { paused = false } }
}
