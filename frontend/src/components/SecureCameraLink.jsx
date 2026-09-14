import { useEffect, useState } from 'react'
import { Lock } from 'lucide-react'

/**
 * Shown under a camera error. A browser refuses the camera on a page that is
 * not a secure context — which is the campus server opened at
 * http://<LAN IP>:8000 from any device but the launcher's own kiosk. The
 * campus server also serves https on another port (run-campus.ps1), so this
 * offers the same page there. Renders nothing on Railway, on localhost, in the
 * kiosk (all already secure) or when the server has no https port.
 */
export default function SecureCameraLink({ style }) {
  const [href, setHref] = useState(null)

  useEffect(() => {
    if (window.isSecureContext) return
    let cancelled = false
    fetch('/api/deployment/')
      .then(r => (r.ok ? r.json() : null))
      .then(d => {
        if (cancelled || !d?.https_port) return
        const { hostname, pathname, search } = window.location
        setHref(`https://${hostname}:${d.https_port}${pathname}${search}`)
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [])

  if (!href) return null
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4, ...style }}>
      <a
        href={href}
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 6, padding: '8px 14px',
          borderRadius: 8, background: '#03396C', color: '#fff', fontSize: 13,
          fontWeight: 600, textDecoration: 'none',
        }}
      >
        <Lock size={14} /> Open the secure page to use the camera
      </a>
      <span style={{ fontSize: 11, color: '#64839C', textAlign: 'center' }}>
        First time on this device: the browser warns about the certificate — choose Advanced → Proceed.
      </span>
    </div>
  )
}
