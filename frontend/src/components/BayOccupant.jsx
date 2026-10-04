import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, Camera, Loader2, Pencil, RefreshCw, ShieldCheck, Trash2, UserPlus, X } from 'lucide-react'
import { lookupVehicleByPlate } from '../api/vehicles'
import { zoneApi } from '../api/parking'
import notify, { useFeedbackStore } from './Feedback/notify'
import { bayPlate } from '../utils/bayPlate'

// The server's own reason, when it gave one.
function apiError(err, fallback) {
  if (!err?.response) return `${fallback} The server could not be reached.`
  const data = err.response.data
  if (typeof data?.error === 'string') return data.error
  if (typeof data?.detail === 'string') return data.detail
  const first = data && typeof data === 'object' ? Object.values(data).flat()[0] : null
  return typeof first === 'string' ? first : fallback
}

const fmtNoted = (iso) => (iso
  ? new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
  : '')

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
 * Who a guard recorded as parked in this bay: driver, plate, and who noted it
 * when. Shown to guards and the admin — the API leaves these fields out for
 * anyone else.
 */
export function OccupantRecord({ space }) {
  if (!space.occupant_plate) {
    return (
      <p className="pm-bay-note">
        <Camera size={13} />
        <span>
          {bayPlate(space)
            ? 'No guard has recorded who parked here yet.'
            : 'Detected by the camera. No plate has been recorded yet.'}
        </span>
      </p>
    )
  }
  return (
    <div className="pm-bay-rows">
      <div className="pm-bay-row">
        <span className="pm-bay-label">Parked by</span>
        <span className="pm-bay-value">{space.occupant_name || 'Not given'}</span>
      </div>
      <div className="pm-bay-row">
        <span className="pm-bay-label">Plate / conduction no.</span>
        <span className="pm-bay-value pm-bay-plate">{space.occupant_plate}</span>
      </div>
      <p className="pm-occ-noted">
        Recorded{space.occupant_noted_by_name ? ` by ${space.occupant_noted_by_name}` : ''}
        {space.occupant_noted_at ? ` · ${fmtNoted(space.occupant_noted_at)}` : ''}
      </p>
    </div>
  )
}

/**
 * The guard's form: plate (required) and the driver (optional). A registered
 * plate fills the name in from its owner while the box is still empty — the
 * guard can overwrite it, since whoever drove may not be the registrant.
 */
function OccupantForm({ space, onSaved, onCancel, onFreed }) {
  const [plate, setPlate]   = useState(space.occupant_plate || bayPlate(space))
  const [name, setName]     = useState(space.occupant_name || '')
  const [saving, setSaving] = useState(false)
  // What the boxes hold right now, for answers that arrive after the guard
  // has moved on; and the name the lookup filled in, which a later lookup may
  // replace — a name the guard typed is never replaced.
  const plateNow   = useRef(plate)
  const nameNow    = useRef(name)
  const autoName   = useRef(null)
  const lookedUp   = useRef(null)    // the plate last looked up, so Tab does not ask twice
  const savingNow  = useRef(false)   // Enter pressed twice must not send twice
  useEffect(() => { plateNow.current = plate }, [plate])
  useEffect(() => { nameNow.current = name }, [name])

  const fillName = async () => {
    const p = plate.trim()
    const typed = name.trim() && name !== autoName.current
    if (!p || typed || lookedUp.current === p) return
    lookedUp.current = p
    try {
      const { data } = await lookupVehicleByPlate(p)
      const owner = data?.found ? (data.vehicle?.user?.full_name || '').toUpperCase() : ''
      if (plateNow.current.trim() !== p) return                  // the plate changed meanwhile
      const cur = nameNow.current
      if (cur.trim() && cur !== autoName.current) return        // the guard typed a name meanwhile
      autoName.current = owner || null
      setName(owner)                                            // unregistered: clear a stale auto-fill
    } catch {
      lookedUp.current = null                                   // let a later blur try again
    }
  }

  const save = async () => {
    if (savingNow.current) return
    if (!plate.trim()) {
      await notify.error('Enter the plate or conduction number of the vehicle parked here.', {
        title: 'Occupant not recorded',
      })
      return
    }
    savingNow.current = true
    setSaving(true)
    try {
      const updated = await zoneApi.recordOccupant(space.id, { plate: plate.trim(), name: name.trim() })
      onSaved(updated)
      notify.success(`Space ${space.space_number}: ${updated.occupant_plate} recorded.`, {
        title: 'Occupant recorded',
      })
    } catch (err) {
      await notify.error(apiError(err, 'The occupant could not be recorded.'), {
        title: 'Occupant not recorded',
      })
      // The bay went free while the form was open: there is no one to record,
      // so the dialog closes instead of offering a Save that cannot work.
      if (err?.response?.status === 409) onFreed?.()
    } finally {
      savingNow.current = false
      setSaving(false)
    }
  }

  return (
    <div className="pm-occ-form">
      <label className="pm-modal-label" htmlFor="occ-plate">
        Plate / conduction number <span className="pm-req">*</span>
      </label>
      <input
        id="occ-plate"
        className="pm-modal-input"
        value={plate}
        onChange={e => setPlate(e.target.value.toUpperCase())}
        onBlur={fillName}
        onKeyDown={e => e.key === 'Enter' && save()}
        placeholder="e.g. ABC 1234"
        maxLength={30} autoFocus
      />
      <label className="pm-modal-label" htmlFor="occ-name">Who parked here</label>
      <input
        id="occ-name"
        className="pm-modal-input"
        value={name}
        onChange={e => setName(e.target.value.toUpperCase())}
        onFocus={fillName}
        onKeyDown={e => e.key === 'Enter' && save()}
        placeholder="Driver's name (filled in for registered vehicles)"
        maxLength={150}
      />
      <div className="pm-occ-form-actions">
        <button type="button" className="pm-btn pm-btn--outline" onClick={onCancel} disabled={saving}>Cancel</button>
        <button type="button" className="pm-btn pm-btn--primary" onClick={save} disabled={saving}>
          {saving ? <Loader2 size={13} className="pm-spin" /> : <UserPlus size={13} />} Save
        </button>
      </div>
    </div>
  )
}

/**
 * The details above as a dialog, for the screens where clicking a bay is a
 * question rather than an action. `children` is the footer, so a screen that
 * can also free the bay puts its button there instead of opening a second
 * dialog on top of this one.
 *
 * `canRecord` (guards and the admin) adds the occupant record and its form.
 * `onUpdated` hears about a save at once; every other open screen picks it up
 * from the live `parkingspace` refresh.
 */
export default function BayOccupantModal({ space: initial, zoneName, onClose, canRecord = false, onUpdated, children }) {
  const [space, setSpace]     = useState(initial)
  const [editing, setEditing] = useState(false)

  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== 'Escape') return
      // A message box open over this dialog owns the key. Both listen on the
      // capture phase, and this one was added first, so it runs first — without
      // this, Esc on "Occupant recorded" closed the dialog underneath and left
      // the message on screen.
      if (useFeedbackStore.getState().queue.length > 0) return
      e.preventDefault()
      e.stopImmediatePropagation()
      if (editing) setEditing(false)   // Esc backs out of the form first
      else onClose?.()
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [onClose, editing])

  const saved = (u) => {
    setSpace(u)
    setEditing(false)
    onUpdated?.(u)
  }

  const clear = async () => {
    const ok = await notify.confirm({
      title: 'Clear occupant record?',
      message: `Remove ${space.occupant_plate} as the vehicle parked in space ${space.space_number}?`,
      confirmLabel: 'Clear record',
      danger: true,
    })
    if (!ok) return
    try {
      saved(await zoneApi.clearOccupant(space.id))
    } catch (err) {
      await notify.error(apiError(err, 'The record could not be cleared.'), { title: 'Record not cleared' })
    }
  }

  const plate = bayPlate(space)

  return (
    <div className="pm-overlay" onClick={e => e.target === e.currentTarget && onClose?.()}>
      <div className="pm-modal" onClick={e => e.stopPropagation()} role="dialog" aria-modal="true">
        <div className="pm-modal-header">
          <span>Space {space.space_number}{zoneName ? ` — ${zoneName}` : ''}</span>
          <button className="pm-modal-close" onClick={onClose} aria-label="Close"><X size={16} /></button>
        </div>
        <div className="pm-modal-body">
          {canRecord && (editing ? (
            <OccupantForm space={space} onSaved={saved} onCancel={() => setEditing(false)} onFreed={onClose} />
          ) : (
            <div className="pm-occ-section">
              <OccupantRecord space={space} />
              <div className="pm-occ-actions">
                <button type="button" className="pm-btn pm-btn--outline" onClick={() => setEditing(true)}>
                  {space.occupant_plate
                    ? <><Pencil size={13} /> Edit</>
                    : <><UserPlus size={13} /> Record who parked here</>}
                </button>
                {space.occupant_plate && (
                  <button type="button" className="pm-btn pm-btn--outline pm-occ-clear" onClick={clear}>
                    <Trash2 size={13} /> Clear
                  </button>
                )}
              </div>
            </div>
          ))}
          {!editing && plate && (
            <div className={canRecord ? 'pm-occ-lookup' : undefined}>
              {canRecord && <div className="pm-occ-lookup-title">Vehicle records</div>}
              <BayOccupantDetails key={plate} plate={plate} />
            </div>
          )}
          {!canRecord && !plate && (
            <p className="pm-bay-note"><Camera size={13} /> Detected by the camera. No plate has been recorded yet.</p>
          )}
        </div>
        {!editing && (
          <div className="pm-modal-footer">
            {children ?? (
              <button className="pm-btn pm-btn--primary" onClick={onClose} autoFocus>Close</button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
