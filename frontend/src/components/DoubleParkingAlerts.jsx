import { useState, useEffect } from 'react'
import { AlertTriangle, ShieldAlert } from 'lucide-react'
import { toast } from 'sonner'
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
 *
 * When `canAttribute` is set (the guard's parking screen), each unattributed
 * alert gets an inline plate/conduction input: the guard names the offender,
 * which issues the violation with the captured boxed evidence and clears the
 * card. Admins only see the alert — issuing is the guard's responsibility.
 */
export default function DoubleParkingAlerts({ zoneId = null, pollMs = 8000, canAttribute = false }) {
  const [alerts, setAlerts] = useState([])
  const [plateInputs, setPlateInputs] = useState({}) // key → typed plate
  const [submitting, setSubmitting] = useState(null)  // key currently submitting

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

  const keyOf = (a) => `${a.zone_id}-${(a.space_ids || []).join('-')}`

  const handleAttribute = async (a) => {
    const key = keyOf(a)
    const plate = (plateInputs[key] || '').trim().toUpperCase()
    if (!plate) { toast.error('Enter the plate or conduction number.'); return }
    setSubmitting(key)
    try {
      await zoneApi.attributeDoublePark(a.zone_id, a.space_ids || [], plate)
      toast.success(`Double-parking violation issued to ${plate}.`)
      setPlateInputs(prev => { const n = { ...prev }; delete n[key]; return n })
      load() // the alert is now cleared server-side
    } catch (err) {
      toast.error(err?.response?.data?.error || 'Could not issue the violation.')
    } finally {
      setSubmitting(null)
    }
  }

  return (
    <div className="dpa-banner" role="alert">
      <AlertTriangle size={18} className="dpa-icon" />
      <div className="dpa-body">
        <p className="dpa-title">
          Double parking detected{shown.length > 1 ? ` (${shown.length})` : ''}
        </p>
        <ul className="dpa-list">
          {shown.map(a => {
            const key = keyOf(a)
            return (
              <li key={key} className="dpa-item">
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
                {canAttribute && !a.attributed && (
                  <span className="dpa-attribute">
                    <input
                      className="dpa-plate-input"
                      value={plateInputs[key] || ''}
                      onChange={e => setPlateInputs(prev => ({ ...prev, [key]: e.target.value.toUpperCase() }))}
                      placeholder="Plate / conduction no."
                      disabled={submitting === key}
                    />
                    <button
                      type="button"
                      className="dpa-attribute-btn"
                      onClick={() => handleAttribute(a)}
                      disabled={submitting === key}
                    >
                      {submitting === key ? 'Issuing…' : 'Issue violation'}
                    </button>
                  </span>
                )}
              </li>
            )
          })}
        </ul>
      </div>
    </div>
  )
}
