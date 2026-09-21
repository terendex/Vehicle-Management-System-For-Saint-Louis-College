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
  const [fsKey, setFsKey] = useState(null)      // which tile is fullscreen, or null — state, because the UI reads it
  const refs = useRef({})                       // key -> DOM node; a ref, because changing it must not re-render

  useEffect(() => {
    // The browser is the source of truth, not this hook. Esc, the OS window
    // chrome and the browser's own controls all exit fullscreen without going
    // through toggle() — this listener is what keeps `fsKey` honest when they do.
    const onChange = () => {
      if (!document.fullscreenElement) setFsKey(null)
    }
    document.addEventListener('fullscreenchange', onChange)
    return () => document.removeEventListener('fullscreenchange', onChange)
  }, [])                                        // empty deps: one listener for the hook's lifetime

  // Stable per key, so it does not detach and reattach the ref every render.
  // Curried: setRef(key) returns the actual ref callback. useCallback with
  // empty deps keeps setRef itself stable, so `ref={fs.setRef(k)}` hands React
  // a fresh inner function each render — which React calls with null then the
  // node. The `delete` branch is that null, i.e. unmount.
  const setRef = useCallback(key => el => {
    if (el) refs.current[key] = el
    else delete refs.current[key]
  }, [])

  /** Enter, leave, or move fullscreen. Resolves false when the browser blocks it. */
  const toggle = useCallback(async key => {
    const el = refs.current[key]
    if (!el) return false                       // the tile is not mounted; nothing to make fullscreen
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
      // requestFullscreen rejects when the browser refuses — most often
      // because the call did not come from a user gesture. Reported as false
      // rather than thrown: a button that fails to expand is not an error
      // worth interrupting the operator for.
      return false
    }
  }, [fsKey])                                   // depends on fsKey, for the move-between-tiles branch above

  const isFullscreen = useCallback(key => fsKey === key, [fsKey])

  return { fsKey, setRef, toggle, isFullscreen }
}

export default useFullscreen
