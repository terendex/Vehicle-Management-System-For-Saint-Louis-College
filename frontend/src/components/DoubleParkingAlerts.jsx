import { useState, useEffect } from 'react'
import { AlertTriangle, ShieldAlert } from 'lucide-react'
import { zoneApi } from '../api/parking'
import { useLiveUpdates } from '../realtime/useLiveUpdates'
import './DoubleParkingAlerts.css'

/**
 * Live double-parking banner, shared by the admin and guard parking screens.
 *
 * Alerts are current state, not history: the backend drops one the moment the
 * vehicle stops straddling, so an empty list means nothing is wrong right now
 * and the banner disappears on its own.
 *
 * An alert that resolved to a registered plate already has a Violation behind
 * it. One that did not is shown as needing a guard — the camera cannot read
 * every plate from overhead, and an unreadable plate must never be turned into
 * a fine against a guess.
 */
export default function DoubleParkingAlerts({ zoneId = null, pollMs = 8000 }) {
  const [alerts, setAlerts] = useState([])

  const load = async () => {
    try {
      setAlerts(await zoneApi.getAlerts())
    } catch {
      // A failed poll must not blank a banner that is still valid — keep the
      // last known state and try again on the next tick.
    }
  }

  useEffect(() => {
    load()
    const t = setInterval(load, pollMs)
    return () => clearInterval(t)
  }, [pollMs])

  // Refresh immediately when the camera thread broadcasts one.
  useLiveUpdates(load, 'parkingspace')

  const shown = zoneId == null ? alerts : alerts.filter(a => a.zone_id === zoneId)
  if (shown.length === 0) return null

  return (
    <div className="dpa-banner" role="alert">
      <AlertTriangle size={18} className="dpa-icon" />
      <div className="dpa-body">
        <p className="dpa-title">
          Double parking detected{shown.length > 1 ? ` (${shown.length})` : ''}
        </p>
        <ul className="dpa-list">
          {shown.map(a => (
            <li key={`${a.zone_id}-${(a.space_ids || []).join('-')}`} className="dpa-item">
              <span className="dpa-bays">
                {(a.spaces || []).join(' + ') || `bays ${(a.space_ids || []).join(', ')}`}
              </span>
              {a.attributed ? (
                <span className="dpa-tag dpa-tag--issued">
                  <ShieldAlert size={12} /> {a.plate} — violation issued
                </span>
              ) : (
                <span className="dpa-tag dpa-tag--manual">
                  {a.plate ? `plate ${a.plate} not registered` : 'plate unreadable'} — needs a guard
                </span>
              )}
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}
