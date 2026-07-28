/**
 * Origin for WebSocket connections (/ws/updates/, /ws/scan/).
 *
 * Defaults to the page's own origin, which is what makes the deployed build
 * work: Django serves the SPA and the API from a single host, so the socket
 * follows the browser to whatever domain the app was loaded from — localhost,
 * a tunnel, or the Railway URL — with no rebuild.
 *
 * The scheme has to track the page: a wss:// page cannot open a ws:// socket,
 * browsers block it as mixed content.
 *
 * VITE_API_URL still overrides for the case where the backend genuinely lives
 * on another host (e.g. the on-campus scanning agent).
 */
export function getWsBase() {
  // VITE_WS_URL is already a ws:// origin, so it is taken as-is.
  if (import.meta.env.VITE_WS_URL) {
    return import.meta.env.VITE_WS_URL
  }

  const override = import.meta.env.VITE_API_URL
  if (override) {
    return override.replace(/^http/, 'ws')
  }

  if (typeof window !== 'undefined' && window.location) {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    return `${proto}//${window.location.host}`
  }

  return 'ws://127.0.0.1:8000'
}

export const WS_BASE = getWsBase()
