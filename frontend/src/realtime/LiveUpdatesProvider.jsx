import { useCallback, useEffect, useRef } from 'react'
import useAuthStore from '../stores/authStore'
import { LiveUpdatesContext } from './LiveUpdatesContext'
import { WS_BASE } from '../api/wsBase'

/**
 * Maintains ONE WebSocket to /ws/updates/ for the whole app. The backend pushes
 * small {type:"data_changed", resource, action} messages whenever domain data
 * changes; pages subscribe via useLiveUpdates() and refetch.
 *
 * Reconnects automatically and follows the auth token (opens on login/refresh,
 * closes on logout).
 */
export function LiveUpdatesProvider({ children }) {
  const accessToken = useAuthStore((s) => s.accessToken)

  // Five refs, no state — deliberately. Every one of these changes during
  // the socket's life, and none should cause a re-render: this component
  // renders its children and nothing else.
  const wsRef = useRef(null)
  const subscribersRef = useRef(new Set())      // a Set, so unsubscribe is a delete rather than a filter
  const reconnectRef = useRef(null)             // the pending backoff timer
  const attemptsRef = useRef(0)                 // consecutive failures, reset on a successful open
  const closedByUsRef = useRef(false)           // "we meant it" — tells onclose not to reconnect

  // useCallback with an EMPTY dep array, so this identity never changes. That
  // matters: useLiveUpdates lists `subscribe` as an effect dependency, and an
  // unstable identity would make every consumer resubscribe on every render.
  const subscribe = useCallback((fn) => {
    subscribersRef.current.add(fn)
    return () => subscribersRef.current.delete(fn)   // the unsubscribe the hook calls on unmount
  }, [])

  useEffect(() => {
    // No token → make sure any existing socket is closed and stay idle.
    // Signed out (or not yet in). The socket authenticates with the token in
    // its query string, so there is nothing to connect with.
    if (!accessToken) {
      closedByUsRef.current = true              // set BEFORE closing, or onclose would schedule a reconnect
      if (reconnectRef.current) clearTimeout(reconnectRef.current)
      if (wsRef.current) { try { wsRef.current.close() } catch { /* ignore */ } }
      wsRef.current = null
      return
    }

    closedByUsRef.current = false
    let cancelled = false

    const connect = () => {
      if (cancelled) return
      const ws = new WebSocket(`${WS_BASE}/ws/updates/?token=${accessToken}`)
      wsRef.current = ws

      ws.onopen = () => { attemptsRef.current = 0 }

      ws.onmessage = (evt) => {
        let msg
        try { msg = JSON.parse(evt.data) } catch { return }   // unparseable frame: ignore rather than throw inside a socket handler
        if (msg.type !== 'data_changed') return  // the channel carries other message types; this provider only forwards one
        subscribersRef.current.forEach((fn) => {
          // Each subscriber is isolated: a page whose refetch throws must not
          // stop the other twenty pages from being told.
          try { fn(msg) } catch { /* one bad subscriber shouldn't break the rest */ }
        })
      }

      ws.onclose = () => {
        // Two ways this is intentional: the effect is tearing down
        // (`cancelled`), or we closed it on purpose (logout). Either way, do
        // not reconnect — that is what would resurrect a socket after logout.
        if (cancelled || closedByUsRef.current) return
        // Exponential backoff, capped at 15s.
        // 1s, 2s, 4s, 8s, 15s, 15s... The cap matters at a gate terminal left
        // running overnight: without it the delay would grow to hours and the
        // screen would still be dead at opening time.
        const delay = Math.min(1000 * 2 ** attemptsRef.current, 15000)
        attemptsRef.current += 1
        reconnectRef.current = setTimeout(connect, delay)
      }

      ws.onerror = () => { try { ws.close() } catch { /* ignore */ } }
    }

    connect()

    // Runs on unmount AND whenever the token changes — which is the mechanism
    // that makes the socket follow the session: the old one is torn down here
    // and the effect immediately reconnects with the new token.
    return () => {
      cancelled = true                          // closes over the running connect(), stopping a reconnect already in flight
      closedByUsRef.current = true
      if (reconnectRef.current) clearTimeout(reconnectRef.current)
      if (wsRef.current) { try { wsRef.current.close() } catch { /* ignore */ } }
      wsRef.current = null
    }
  }, [accessToken])

  return (
    <LiveUpdatesContext.Provider value={{ subscribe }}>
      {children}
    </LiveUpdatesContext.Provider>
  )
}
