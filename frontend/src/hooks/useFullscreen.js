import { useState, useEffect, useRef, useCallback } from 'react'

/**
 * Fullscreen for one of several elements, addressed by key.
 *
 * Driven through the browser's own Fullscreen API on the element that is
 * *already mounted*, rather than by re-rendering the feed into some overlay.
 * That distinction matters here: a camera canvas is registered with the stream
 * context and painted frame by frame, so remounting it elsewhere would drop the
 * registration and force a reconnect — and these cameras are slow, and in one
 * case actively hostile, to reconnect. Keeping the node in place means going
 * fullscreen costs nothing and never interrupts the picture.
 *
 * Usage:
 *   const fs = useFullscreen()
 *   <div ref={fs.setRef(key)}>…</div>
 *   <button onClick={() => fs.toggle(key)}>{fs.isFullscreen(key) ? …}</button>
 *
 * Listening for `fullscreenchange` rather than tracking our own state alone is
 * what keeps Esc, the OS chrome and the browser's own controls in sync.
 */
export function useFullscreen() {
  const [fsKey, setFsKey] = useState(null)
  const refs = useRef({})

  useEffect(() => {
    const onChange = () => {
      if (!document.fullscreenElement) setFsKey(null)
    }
    document.addEventListener('fullscreenchange', onChange)
    return () => document.removeEventListener('fullscreenchange', onChange)
  }, [])

  // Stable per key, so it does not detach and reattach the ref every render.
  const setRef = useCallback(key => el => {
    if (el) refs.current[key] = el
    else delete refs.current[key]
  }, [])

  /** Enter, leave, or move fullscreen. Resolves false when the browser blocks it. */
  const toggle = useCallback(async key => {
    const el = refs.current[key]
    if (!el) return false
    try {
      if (document.fullscreenElement) {
        await document.exitFullscreen()
        // Asking for a different tile while one is already fullscreen should
        // move, not just close: the change event clears fsKey, so only
        // re-enter when the request was for another element.
        if (fsKey !== key) {
          await el.requestFullscreen()
          setFsKey(key)
        }
        return true
      }
      await el.requestFullscreen()
      setFsKey(key)
      return true
    } catch {
      return false
    }
  }, [fsKey])

  const isFullscreen = useCallback(key => fsKey === key, [fsKey])

  return { fsKey, setRef, toggle, isFullscreen }
}

export default useFullscreen
