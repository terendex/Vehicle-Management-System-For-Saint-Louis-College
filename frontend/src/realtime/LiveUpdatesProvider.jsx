import { useCallback, useEffect, useRef } from 'react'
import useAuthStore from '../stores/authStore'
import { LiveUpdatesContext } from './LiveUpdatesContext'

const WS_BASE =
  (import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000').replace('http', 'ws')

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

  const wsRef = useRef(null)
  const subscribersRef = useRef(new Set())
  const reconnectRef = useRef(null)
  const attemptsRef = useRef(0)
  const closedByUsRef = useRef(false)

  const subscribe = useCallback((fn) => {
    subscribersRef.current.add(fn)
    return () => subscribersRef.current.delete(fn)
  }, [])

  useEffect(() => {
    // No token → make sure any existing socket is closed and stay idle.
    if (!accessToken) {
      closedByUsRef.current = true
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
        try { msg = JSON.parse(evt.data) } catch { return }
        if (msg.type !== 'data_changed') return
        subscribersRef.current.forEach((fn) => {
          try { fn(msg) } catch { /* one bad subscriber shouldn't break the rest */ }
        })
      }

      ws.onclose = () => {
        if (cancelled || closedByUsRef.current) return
        // Exponential backoff, capped at 15s.
        const delay = Math.min(1000 * 2 ** attemptsRef.current, 15000)
        attemptsRef.current += 1
        reconnectRef.current = setTimeout(connect, delay)
      }

      ws.onerror = () => { try { ws.close() } catch { /* ignore */ } }
    }

    connect()

    return () => {
      cancelled = true
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
