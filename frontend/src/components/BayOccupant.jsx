import { useEffect, useState } from 'react'
import { AlertTriangle, Loader2, RefreshCw, ShieldCheck, X } from 'lucide-react'
import { lookupVehicleByPlate } from '../api/vehicles'

/**
 * Who is parked in a bay.
 *
 * A bay carries one fact — the plate the detector read, or the one a guard
 * typed when marking it occupied. That is enough to identify the car and not
 * enough to act on it, so both parking screens used to just paint the plate on
 * the rectangle and leave the guard to look the owner up somewhere else.
 *
 * The lookup is read-only on purpose. The other way to turn a plate into an
 * owner is /scan/manual-entry/, which answers the question *and logs a gate
 * entry* — clicking a parking space must never put a car through a barrier.
 */
export function BayOccupantDetails({ plate }) {
  const [state, setState] = useState({ status: 'loading' })
  // Bumped by the retry button to run the effect again. The reload is not
  // state the fetch can derive on its own, and setting `loading` from inside
  // the effect would make the mount itself a second render.
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let cancelled = false
    lookupVehicleByPlate(plate)
      .then(res => { if (!cancelled) setState({ status: 'done', data: res.data }) })
      .catch(()  => { if (!cancelled) setState({ status: 'failed' }) })
    return () => { cancelled = true }
  }, [plate, attempt])

  const retry = () => {
    setState({ status: 'loading' })
    setAttempt(n => n + 1)
  }

  if (state.status === 'loading') {
    return (
      <p className="pm-bay-note">
        <Loader2 size={13} className="pm-spin" /> Looking up {plate}…
      </p>
    )
  }

  // Reported here rather than through `notify`: this dialog exists to answer
  // one question, and a message box on top of it would cover the answer it is
  // apologising for. The retry is the acknowledgement.
  if (state.status === 'failed') {
    return (
      <div className="pm-bay-note pm-bay-note--warn">
        <span>Could not reach the vehicle records for {plate}.</span>
        <button type="button" className="pm-btn pm-btn--outline pm-bay-retry" onClick={retry}>
          <RefreshCw size={12} /> Try again
        </button>
      </div>
    )
  }

  const { found, vehicle, active_violations: violations } = state.data
  // A lot is full of visitors and deliveries. An unregistered plate is an
  // ordinary answer, so it reads as one rather than as a failed lookup.
  if (!found) {
    return (
      <p className="pm-bay-note">
        <AlertTriangle size={13} />
        <span><strong>{plate}</strong> is not a registered vehicle — visitor, delivery, or a misread plate.</span>
      </p>
    )
  }

  const owner = vehicle?.user
  const descr = [vehicle?.vehicle_type, vehicle?.color, vehicle?.model].filter(Boolean).join(' · ')

  return (
    <div className="pm-bay-rows">
      <div className="pm-bay-row">
        <span className="pm-bay-label">Plate</span>
        <span className="pm-bay-value pm-bay-plate">{vehicle?.plate_number || vehicle?.conduction_number || plate}</span>
      </div>
      {owner?.full_name && (
        <div className="pm-bay-row">
          <span className="pm-bay-label">Owner</span>
          <span className="pm-bay-value">{owner.full_name}</span>
        </div>
      )}
      {owner?.owner_type && (
        <div className="pm-bay-row">
          <span className="pm-bay-label">Type</span>
          <span className="pm-bay-value pm-bay-value--cap">{String(owner.owner_type).replace('_', ' ')}</span>
        </div>
      )}
      {descr && (
        <div className="pm-bay-row">
          <span className="pm-bay-label">Vehicle</span>
          <span className="pm-bay-value pm-bay-value--cap">{descr}</span>
        </div>
      )}
      <div className="pm-bay-row">
        <span className="pm-bay-label">Status</span>
        {violations?.length ? (
          <span className="pm-bay-pill pm-bay-pill--bad">
            <AlertTriangle size={11} />
            {violations.length} unresolved violation{violations.length > 1 ? 's' : ''}
          </span>
        ) : vehicle?.is_authorized ? (
          <span className="pm-bay-pill pm-bay-pill--ok"><ShieldCheck size={11} /> Authorized</span>
        ) : (
          <span className="pm-bay-pill"><AlertTriangle size={11} /> Not authorized</span>
        )}
      </div>
    </div>
  )
}

/**
 * The details above as a dialog, for the screens where clicking a bay is a
 * question rather than an action. `children` is the footer, so a screen that
 * can also free the bay puts its button there instead of opening a second
 * dialog on top of this one.
 */
export default function BayOccupantModal({ space, zoneName, onClose, children }) {
  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== 'Escape') return
      e.preventDefault()
      e.stopImmediatePropagation()
      onClose?.()
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [onClose])

  return (
    <div className="pm-overlay" onClick={e => e.target === e.currentTarget && onClose?.()}>
      <div className="pm-modal" onClick={e => e.stopPropagation()} role="dialog" aria-modal="true">
        <div className="pm-modal-header">
          <span>Space {space.space_number}{zoneName ? ` — ${zoneName}` : ''}</span>
          <button className="pm-modal-close" onClick={onClose} aria-label="Close"><X size={16} /></button>
        </div>
        <div className="pm-modal-body">
          <BayOccupantDetails plate={space.occupied_by} />
        </div>
        <div className="pm-modal-footer">
          {children ?? (
            <button className="pm-btn pm-btn--primary" onClick={onClose} autoFocus>Close</button>
          )}
        </div>
      </div>
    </div>
  )
}
