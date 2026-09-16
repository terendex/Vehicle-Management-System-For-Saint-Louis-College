import { useState, useEffect, useRef, Fragment } from 'react'
import { useLiveUpdates } from '../../realtime/useLiveUpdates'
import {
  CalendarDays, Plus, Trash2, ChevronDown, ChevronUp,
  Loader2, ToggleLeft, ToggleRight, X, AlertTriangle,
  ParkingCircle, Tag, Check, Archive, CalendarClock, Clock,
  Printer, Monitor,
} from 'lucide-react'
import { QRCodeSVG } from 'qrcode.react'
import notify, { toast } from '../../components/Feedback/notify'
import { fieldProblems } from '../../components/Feedback/formProblems'
import AdminLayout from '../../components/Layout/AdminLayout'
import { getSystemSettings, patchSystemSettings, getEvents, createEvent, patchEvent, deleteEvent } from '../../api/vehicles'
import { lookupSlip, printSlipOnServer } from '../../api/scanning'
import { printSlipInBrowser } from '../../utils/slipPrint'
import { zoneApi } from '../../api/parking'
import { formatPlateNumber, isValidPlateNumber, isValidConductionNumber } from '../../utils/plateFormat'
import './Events.css'

// How much of campus parking an event is expected to take up. Fractions rather
// than a free percentage box, because that is how the CDSO actually plans an
// event — "half the parking is gone" — and a number field invites 37%, which
// nobody can act on. Mirrors Event.ParkingShare on the server.
const PARKING_SHARES = [
  { value: 'none',           label: 'None — parking unaffected', short: null      },
  { value: 'quarter',        label: 'About 1/4 of parking',      short: '1/4 full' },
  { value: 'third',          label: 'About 1/3 of parking',      short: '1/3 full' },
  { value: 'half',           label: 'About 1/2 of parking',      short: '1/2 full' },
  { value: 'two_thirds',     label: 'About 2/3 of parking',      short: '2/3 full' },
  { value: 'three_quarters', label: 'About 3/4 of parking',      short: '3/4 full' },
  { value: 'full',           label: 'All of parking',            short: 'All full' },
]
const shareShort = (v) => PARKING_SHARES.find(s => s.value === v)?.short ?? null

// What an organizer may be listed by: a plate, a conduction sticker (a new car
// with no plate yet), or an e-bike control number (FM-001). The server stores
// each the way the gate compares it — FM001 becomes FM-001.
const isOrganizerIdentifier = (raw) => isValidPlateNumber(raw) || isValidConductionNumber(raw)
const IDENTIFIER_ERROR = 'Enter a plate, conduction sticker, or e-bike control number (e.g. FM-001).'

// ── Toggle card for event mode switches ──────────────────────────────
function ModeToggle({ label, description, enabled, loading, onToggle }) {
  return (
    <div className={`ev-mode-card${enabled ? ' ev-mode-card--on' : ''}`}>
      <div className="ev-mode-info">
        <span className="ev-mode-label">{label}</span>
        <p className="ev-mode-desc">{description}</p>
      </div>
      <button
        className={`ev-toggle-btn${enabled ? ' ev-toggle-btn--on' : ''}`}
        onClick={onToggle}
        disabled={loading}
        aria-pressed={enabled}
      >
        {loading
          ? <Loader2 size={22} className="ev-spinner" />
          : enabled ? <ToggleRight size={28} /> : <ToggleLeft size={28} />
        }
        <span>{enabled ? 'ON' : 'OFF'}</span>
      </button>
    </div>
  )
}

// ── Add Event modal ──────────────────────────────────────────────────
function AddEventModal({ onClose, onCreated }) {
  const [form, setForm]       = useState({
    name: '', date: '', start_time: '', end_time: '', parking_share: 'none',
  })
  const [plateInput, setPlateInput] = useState('')
  const [plateError, setPlateError] = useState('')
  const [plates, setPlates]   = useState([])
  const [saving, setSaving]   = useState(false)
  const plateRef = useRef(null)

  const addPlate = async () => {
    const p = formatPlateNumber(plateInput.trim())
    if (!p) {
      await notify.error('Enter a plate number first.', { title: 'Nothing to add' })
      return
    }
    if (!isOrganizerIdentifier(p)) {
      await notify.error(IDENTIFIER_ERROR, { title: 'Plate not added' })
      return
    }
    if (plates.includes(p)) { toast.error('Plate already added.'); return }
    setPlates(prev => [...prev, p])
    setPlateInput('')
    plateRef.current?.focus()
  }

  const removePlate = (p) => setPlates(prev => prev.filter(x => x !== p))

  const handleSubmit = async (e) => {
    e.preventDefault()
    // The form carries noValidate, so the browser's own bubble is gone and
    // its complaints have to be re-raised here.
    if (await notify.validation(fieldProblems(e.currentTarget))) return
    setSaving(true)
    try {
      const { data } = await createEvent({ ...form, organizer_plates: plates })
      onCreated(data)
      toast.success('Event created.')
      onClose()
    } catch (err) {
      const msg = err.response?.data
        ? Object.values(err.response.data).flat().join(' ')
        : 'Failed to create event.'
      toast.error(msg)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="ev-overlay" onClick={onClose}>
      <div className="ev-modal" onClick={e => e.stopPropagation()}>
        <div className="ev-modal-head">
          <h2 className="ev-modal-title">New Event</h2>
          <button className="ev-modal-close" onClick={onClose}><X size={16} /></button>
        </div>

        <form onSubmit={handleSubmit} className="ev-modal-form" noValidate>
          <div className="ev-field">
            <label className="ev-label">Event Name</label>
            <input
              className="ev-text-input"
              placeholder="e.g. SLC Foundation Day"
              value={form.name}
              onChange={e => setForm(p => ({ ...p, name: e.target.value }))}
              required
            />
          </div>
          <div className="ev-field">
            <label className="ev-label">Date</label>
            <input
              className="ev-text-input"
              type="date"
              value={form.date}
              min={new Date().toISOString().slice(0, 10)}
              onChange={e => setForm(p => ({ ...p, date: e.target.value }))}
              required
            />
          </div>
          {/* Times are optional — an all-day event genuinely has none, and a
              blank pair reads as "All day" rather than as midnight-to-midnight.
              They matter because the parking reservation below follows the
              clock: an evening event must not make the car park read as half
              gone at nine in the morning. */}
          <div className="ev-field-row">
            <div className="ev-field">
              <label className="ev-label">Start Time <span className="ev-label-optional">(optional)</span></label>
              <input
                className="ev-text-input"
                type="time"
                value={form.start_time}
                onChange={e => setForm(p => ({ ...p, start_time: e.target.value }))}
              />
            </div>
            <div className="ev-field">
              <label className="ev-label">End Time <span className="ev-label-optional">(optional)</span></label>
              <input
                className="ev-text-input"
                type="time"
                value={form.end_time}
                onChange={e => setForm(p => ({ ...p, end_time: e.target.value }))}
              />
            </div>
          </div>
          <span className="ev-field-hint">Leave both blank for an all-day event.</span>

          <div className="ev-field">
            <label className="ev-label">Parking Taken Up</label>
            <select
              className="ev-text-input"
              value={form.parking_share}
              onChange={e => setForm(p => ({ ...p, parking_share: e.target.value }))}
            >
              {PARKING_SHARES.map(o => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
            <span className="ev-field-hint">
              Held back from the free-space count while the event is running, so the
              gate stops admitting before the bays the event needs are taken.
            </span>
          </div>

          <div className="ev-field">
            <label className="ev-label">Organizer Plates <span className="ev-label-optional">(optional)</span></label>
            <div className="ev-plate-input-row">
              <input
                ref={plateRef}
                className={`ev-text-input ev-plate-field${plateError ? ' ev-input-error' : ''}`}
                placeholder="e.g. AAA 000"
                value={plateInput}
                onChange={e => {
                  const formatted = formatPlateNumber(e.target.value)
                  setPlateInput(formatted)
                  setPlateError(formatted && !isOrganizerIdentifier(formatted) ? IDENTIFIER_ERROR : '')
                }}
                onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addPlate() } }}
              />
              <button type="button" className="ev-add-plate-btn" onClick={addPlate}>
                <Plus size={15} /> Add
              </button>
            </div>
            <span className="ev-field-hint">Plate (AAA 0000 · AA 0000), conduction sticker, or e-bike control number (FM-001)</span>
            {plates.length > 0 && (
              <div className="ev-plate-tags">
                {plates.map(p => (
                  <span key={p} className="ev-plate-tag">
                    {p}
                    <button type="button" onClick={() => removePlate(p)}><X size={11} /></button>
                  </span>
                ))}
              </div>
            )}
          </div>

          <div className="ev-modal-actions">
            <button type="button" className="ev-btn ev-btn-ghost" onClick={onClose}>Cancel</button>
            <button type="submit" className="ev-btn ev-btn-primary" disabled={saving}>
              {saving ? <Loader2 size={14} className="ev-spinner" /> : <Plus size={14} />}
              {saving ? 'Creating…' : 'Create Event'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ── Print Event Pass modal ────────────────────────────────────────────
// A standing pass for one organizer plate, printed before the event and kept
// in the vehicle. Its QR carries the plate (VEHICLE:{plate}|EVENT:{id}), so the
// guard scans it at the gate like a registered vehicle's QR: the first scan
// records the entry, the next the exit, and the event's organizer list is what
// admits it. The slip comes from the server (scanning/slips.py) so the preview,
// the thermal print and the browser print all say the same thing.
function PrintPassModal({ event, plate, onClose }) {
  const [slip, setSlip]   = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy]   = useState('')   // 'thermal' | 'browser' | ''

  useEffect(() => {
    let cancelled = false
    lookupSlip(`SLC-EVENT-PASS:${event.id}:${plate}`)
      .then(({ data }) => { if (!cancelled) setSlip(data) })
      .catch(err => { if (!cancelled) setError(err?.response?.data?.error || 'Could not load the pass.') })
    return () => { cancelled = true }
  }, [event.id, plate])

  const asking = useRef(false)   // a double-click must not confirm once and print twice

  const printThermal = async () => {
    if (asking.current) return
    asking.current = true
    try {
      const go = await notify.confirm({
        title: 'Print Event Pass?',
        message: `Print the Event Pass for ${plate} (${event.name}) on the thermal printer?`,
        confirmLabel: 'Print',
      })
      if (!go) return
    } finally { asking.current = false }
    setBusy('thermal')
    try {
      await printSlipOnServer(slip.code)
      onClose()
      await notify.success(`Event Pass for ${plate} printed on the thermal printer.`, { title: 'Pass printed' })
    } catch (err) {
      const noPrinter = err?.response?.status === 503
      await notify.error(
        noPrinter
          ? 'This server has no thermal printer connected. Use “Print from This Computer” instead.'
          : (err?.response?.data?.error || 'The pass did not print — the printer could not be reached.'),
        { title: 'Pass not printed' },
      )
    } finally { setBusy('') }
  }

  const printBrowser = async () => {
    setBusy('browser')
    const opened = printSlipInBrowser(slip)
    setBusy('')
    if (!opened) {
      await notify.error('The print window was blocked by the browser. Allow pop-ups for this site, then try again.',
        { title: 'Pass not printed' })
    }
  }

  return (
    <div className="ev-overlay" onClick={onClose}>
      <div className="ev-modal ev-modal--print" onClick={e => e.stopPropagation()}>
        <div className="ev-modal-head">
          <h2 className="ev-modal-title">Print Event Pass</h2>
          <button className="ev-modal-close" onClick={onClose}><X size={16} /></button>
        </div>
        <div className="ev-modal-form">
          {error ? (
            <p className="ev-modal-body" style={{ color: '#C62828' }}>{error}</p>
          ) : !slip ? (
            <div className="ev-loading"><Loader2 size={22} className="ev-spinner" /><span>Loading pass…</span></div>
          ) : (
            <>
              {/* The gate admits an organizer only while the event is under way,
                  so a pass printed ahead of time is paper that does not open the
                  gate yet. Said here rather than discovered at the gate. */}
              {slip.state !== 'active' && (
                <p className="ev-pass-warn">
                  <AlertTriangle size={14} /> This event is not under way right now. The pass will print, but
                  the gate admits this plate as an organizer only during the event’s date and time.
                </p>
              )}
              <div className="ev-pass-preview" aria-label="Pass preview">
                <div className="ev-pass-title">{slip.title}</div>
                <div className="ev-pass-plate">{slip.headline}</div>
                {slip.sections.flat().map(([label, value, key]) => (
                  <div key={label} className={`ev-pass-row${key ? ' ev-pass-row--key' : ''}`}>
                    <span>{label}:</span><span>{value}</span>
                  </div>
                ))}
                <div className="ev-pass-qr"><QRCodeSVG value={slip.qr || slip.code} size={116} level="M" /></div>
                {(slip.footer || []).map(line => <div key={line} className="ev-pass-foot">{line}</div>)}
              </div>
              <p className="ev-field-hint" style={{ margin: 0 }}>
                The guard scans this QR at the gate — first scan records the entry, the next records the exit.
              </p>
            </>
          )}
          <div className="ev-modal-actions">
            <button type="button" className="ev-btn ev-btn-ghost" onClick={onClose}>Cancel</button>
            <button type="button" className="ev-btn ev-btn-ghost" onClick={printBrowser} disabled={!slip || !!busy}>
              <Monitor size={14} /> Print from This Computer
            </button>
            <button type="button" className="ev-btn ev-btn-primary" onClick={printThermal} disabled={!slip || !!busy}>
              {busy === 'thermal' ? <Loader2 size={14} className="ev-spinner" /> : <Printer size={14} />}
              {busy === 'thermal' ? 'Printing…' : 'Print on Thermal Printer'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Single event card ─────────────────────────────────────────────────
function EventCard({ event, onUpdated, onDeleted }) {
  const [expanded, setExpanded]         = useState(false)
  const [plateInput, setPlateInput]     = useState('')
  const [plateError, setPlateError]     = useState('')
  const [toggling, setToggling]         = useState(false)
  const [deleting, setDeleting]         = useState(false)
  const [confirmDel, setConfirmDel]     = useState(false)
  const [saving, setSaving]             = useState(false)
  const [rescheduling, setRescheduling] = useState(false)
  const [newDate, setNewDate]           = useState(event.date)
  const [reschedSaving, setReschedSaving] = useState(false)
  const [sched, setSched]               = useState({
    start_time:    event.start_time || '',
    end_time:      event.end_time || '',
    parking_share: event.parking_share || 'none',
  })
  const [schedSaving, setSchedSaving]   = useState(false)
  const [printPlate, setPrintPlate]     = useState(null)
  const schedDirty =
    sched.start_time !== (event.start_time || '') ||
    sched.end_time !== (event.end_time || '') ||
    sched.parking_share !== (event.parking_share || 'none')
  const plateRef = useRef(null)

  const localPlates = event.organizer_plates ?? []

  const handleToggleActive = async () => {
    setToggling(true)
    try {
      const { data } = await patchEvent(event.id, { is_active: !event.is_active })
      onUpdated(data)
      toast.success(data.is_active ? 'Event activated.' : 'Event deactivated.')
    } catch {
      toast.error('Failed to update event.')
    } finally {
      setToggling(false)
    }
  }

  const handleSaveSchedule = async () => {
    setSchedSaving(true)
    try {
      const { data } = await patchEvent(event.id, sched)
      onUpdated(data)
      toast.success('Event time and parking updated.')
    } catch (err) {
      const body = err.response?.data
      toast.error(
        body && typeof body === 'object'
          ? Object.values(body).flat().join(' ')
          : 'Failed to update the event.',
      )
    } finally {
      setSchedSaving(false)
    }
  }

  const handleReschedule = async () => {
    if (!newDate) {
      await notify.error('Pick the new date first.', { title: 'Event not rescheduled' })
      return
    }
    setReschedSaving(true)
    try {
      const { data } = await patchEvent(event.id, { date: newDate })
      onUpdated(data)
      setRescheduling(false)
      toast.success('Event rescheduled.')
    } catch (err) {
      const msg = err.response?.data?.date || 'Failed to reschedule event.'
      toast.error(msg)
    } finally {
      setReschedSaving(false)
    }
  }

  const addPlate = async () => {
    const p = formatPlateNumber(plateInput.trim())
    if (!p) return
    if (!isOrganizerIdentifier(p)) {
      await notify.error(IDENTIFIER_ERROR, { title: 'Plate not added' })
      return
    }
    if (localPlates.includes(p)) { toast.error('Plate already listed.'); return }
    setSaving(true)
    try {
      const { data } = await patchEvent(event.id, { organizer_plates: [...localPlates, p] })
      onUpdated(data)
      setPlateInput('')
      plateRef.current?.focus()
    } catch {
      toast.error('Failed to add plate.')
    } finally {
      setSaving(false)
    }
  }

  const removePlate = async (p) => {
    setSaving(true)
    try {
      const { data } = await patchEvent(event.id, { organizer_plates: localPlates.filter(x => x !== p) })
      onUpdated(data)
    } catch {
      toast.error('Failed to remove plate.')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async () => {
    setDeleting(true)
    try {
      await deleteEvent(event.id)
      onDeleted(event.id)
      toast.success('Event deleted.')
    } catch {
      toast.error('Failed to delete event.')
    } finally {
      setDeleting(false)
      setConfirmDel(false)
    }
  }

  const fmtDate = (d) =>
    new Date(d + 'T00:00:00').toLocaleDateString('en-PH', { year: 'numeric', month: 'short', day: 'numeric' })

  const cardClass = [
    'ev-card',
    event.archived  ? ' ev-card--archived' : '',
    event.is_active ? ' ev-card--active'   : '',
  ].join('')

  return (
    <div className={cardClass}>
      <div className="ev-card-head">
        <div className="ev-card-meta">
          <div className="ev-card-name-row">
            <span className="ev-card-name">{event.name}</span>
            {event.archived  && <span className="ev-archived-badge"><Archive size={11} /> Archived</span>}
            {event.is_active && !event.archived && <span className="ev-active-badge">Active</span>}
            {!event.is_active && !event.archived && <span className="ev-pending-badge">Pending</span>}
          </div>
          <div className="ev-card-sub">
            <CalendarDays size={13} />
            {fmtDate(event.date)}
            <span className="ev-dot" />
            <Clock size={12} />
            {event.time_display || 'All day'}
            <span className="ev-dot" />
            <Tag size={12} />
            {localPlates.length} organizer plate{localPlates.length !== 1 ? 's' : ''}
          </div>
          {shareShort(event.parking_share) && (
            <div className="ev-card-parking">
              <ParkingCircle size={12} />
              Parking ~{shareShort(event.parking_share)}
              {/* "Reserved now" vs "will be reserved" is the difference between
                  a gate that is already turning cars away and one that is not,
                  so the card says which it is rather than leaving it implied. */}
              <span className={`ev-parking-state${event.is_under_way ? ' ev-parking-state--live' : ''}`}>
                {event.is_under_way ? 'Held now' : 'Held during the event'}
              </span>
            </div>
          )}
        </div>

        <div className="ev-card-actions">
          {event.archived ? (
            <button
              className="ev-reschedule-btn"
              onClick={() => { setRescheduling(true); setNewDate('') }}
              title="Reschedule event"
            >
              <CalendarClock size={14} />
              Reschedule
            </button>
          ) : (
            <>
              <button
                className={`ev-status-btn${event.is_active ? ' ev-status-btn--on' : ''}`}
                onClick={handleToggleActive}
                disabled={toggling}
                title={event.is_active ? 'Deactivate' : 'Activate'}
              >
                {toggling
                  ? <Loader2 size={13} className="ev-spinner" />
                  : event.is_active ? <ToggleRight size={15} /> : <ToggleLeft size={15} />
                }
                {event.is_active ? 'Deactivate' : 'Activate'}
              </button>

              <button
                className="ev-reschedule-btn"
                onClick={() => { setRescheduling(true); setNewDate(event.date) }}
                title="Reschedule event"
              >
                <CalendarClock size={14} />
              </button>
            </>
          )}

          <button
            className="ev-expand-btn"
            onClick={() => setExpanded(p => !p)}
            title="Manage plates"
          >
            {expanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
          </button>

          <button
            className="ev-delete-btn"
            onClick={() => setConfirmDel(true)}
            disabled={deleting}
            title="Delete event"
          >
            {deleting ? <Loader2 size={14} className="ev-spinner" /> : <Trash2 size={14} />}
          </button>
        </div>
      </div>

      {rescheduling && (
        <div className="ev-reschedule-row">
          <CalendarClock size={14} className="ev-reschedule-icon" />
          <span className="ev-reschedule-label">New date:</span>
          <input
            type="date"
            className="ev-text-input ev-reschedule-input"
            value={newDate}
            min={new Date().toISOString().slice(0, 10)}
            onChange={e => setNewDate(e.target.value)}
          />
          <button
            className="ev-btn ev-btn-primary ev-btn-sm"
            onClick={handleReschedule}
            disabled={reschedSaving}
          >
            {reschedSaving ? <Loader2 size={13} className="ev-spinner" /> : <Check size={13} />}
            Confirm
          </button>
          <button
            className="ev-btn ev-btn-ghost ev-btn-sm"
            onClick={() => setRescheduling(false)}
            disabled={reschedSaving}
          >
            Cancel
          </button>
        </div>
      )}

      {expanded && (
        <div className="ev-plates-section">
          <div className="ev-plates-label">Time &amp; Parking</div>

          <div className="ev-sched-row">
            <label className="ev-sched-field">
              <span className="ev-sched-label">Start</span>
              <input
                type="time"
                className="ev-text-input"
                value={sched.start_time}
                onChange={e => setSched(p => ({ ...p, start_time: e.target.value }))}
              />
            </label>
            <label className="ev-sched-field">
              <span className="ev-sched-label">End</span>
              <input
                type="time"
                className="ev-text-input"
                value={sched.end_time}
                onChange={e => setSched(p => ({ ...p, end_time: e.target.value }))}
              />
            </label>
            <label className="ev-sched-field ev-sched-field--wide">
              <span className="ev-sched-label">Parking taken up</span>
              <select
                className="ev-text-input"
                value={sched.parking_share}
                onChange={e => setSched(p => ({ ...p, parking_share: e.target.value }))}
              >
                {PARKING_SHARES.map(o => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </label>
            <button
              className={`ev-save-capacity-btn${schedDirty ? ' ev-save-capacity-btn--dirty' : ''}`}
              onClick={handleSaveSchedule}
              disabled={!schedDirty || schedSaving}
            >
              {schedSaving ? <Loader2 size={13} className="ev-spinner" /> : <Check size={13} />}
              Save
            </button>
          </div>
          <span className="ev-field-hint">
            Leave the times blank for an all-day event. Parking is only held back
            while the event is actually running.
          </span>

          <div className="ev-plates-label" style={{ marginTop: 18 }}>Organizer Plates</div>

          <div className="ev-plate-input-row">
            <input
              ref={plateRef}
              className={`ev-text-input ev-plate-field${plateError ? ' ev-input-error' : ''}`}
              placeholder="Enter plate number"
              value={plateInput}
              onChange={e => {
                const formatted = formatPlateNumber(e.target.value)
                setPlateInput(formatted)
                setPlateError(formatted && !isOrganizerIdentifier(formatted) ? IDENTIFIER_ERROR : '')
              }}
              onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addPlate() } }}
              disabled={saving}
            />
            <button className="ev-add-plate-btn" onClick={addPlate} disabled={saving}>
              {saving ? <Loader2 size={13} className="ev-spinner" /> : <Plus size={14} />}
              Add
            </button>
          </div>
            <span className="ev-field-hint">Plate (AAA 0000 · AA 0000), conduction sticker, or e-bike control number (FM-001)</span>

          {localPlates.length === 0 ? (
            <p className="ev-no-plates">No organizer plates added yet.</p>
          ) : (
            <div className="ev-plate-tags">
              {localPlates.map(p => (
                <span key={p} className="ev-plate-tag">
                  {p}
                  <button className="ev-plate-print" onClick={() => setPrintPlate(p)}
                    title={`Print Event Pass for ${p}`} aria-label={`Print Event Pass for ${p}`}>
                    <Printer size={12} /> Print Pass
                  </button>
                  <button onClick={() => removePlate(p)} disabled={saving}
                    title={`Remove ${p}`} aria-label={`Remove ${p}`}>
                    <X size={11} />
                  </button>
                </span>
              ))}
            </div>
          )}
          {localPlates.length > 0 && (
            <span className="ev-field-hint">
              A pass is the organizer’s paper copy — the gate admits these plates whether or not one was printed.
            </span>
          )}
        </div>
      )}

      {printPlate && (
        <PrintPassModal event={event} plate={printPlate} onClose={() => setPrintPlate(null)} />
      )}

      {confirmDel && (
        <div className="ev-overlay" onClick={() => setConfirmDel(false)}>
          <div className="ev-modal ev-modal--sm" onClick={e => e.stopPropagation()}>
            <AlertTriangle size={30} className="ev-warn-icon" />
            <h2 className="ev-modal-title">Delete Event?</h2>
            <p className="ev-modal-body">
              <strong>"{event.name}"</strong> and all its organizer plates will be permanently removed.
            </p>
            <div className="ev-modal-actions">
              <button className="ev-btn ev-btn-ghost" onClick={() => setConfirmDel(false)}>Cancel</button>
              <button className="ev-btn ev-btn-danger" onClick={handleDelete} disabled={deleting}>
                {deleting ? <Loader2 size={14} className="ev-spinner" /> : <Trash2 size={14} />}
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Zone capacity row ─────────────────────────────────────────────────
function ZoneCapacityRow({ zone, onSaved }) {
  const [override, setOverride] = useState(
    zone.capacity_override != null ? String(zone.capacity_override) : ''
  )
  const [saving, setSaving] = useState(false)
  const dirty = String(zone.capacity_override ?? '') !== override

  const handleSave = async () => {
    setSaving(true)
    try {
      const val = override === '' ? null : Number(override)
      await zoneApi.setCapacity(zone.id, val)
      onSaved(zone.id, val)
      toast.success(`Capacity updated for ${zone.name}.`)
    } catch {
      toast.error('Failed to update capacity.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="ev-zone-row">
      <div className="ev-zone-info">
        <span className="ev-zone-name">{zone.name}</span>
        <span className="ev-zone-type">{zone.vehicle_category}</span>
      </div>
      <div className="ev-zone-capacity-cell">
        <span className="ev-zone-base">{zone.space_count} spaces</span>
      </div>
      <div className="ev-zone-override-cell">
        <input
          className="ev-capacity-input"
          type="number"
          min={0}
          placeholder="No override"
          value={override}
          onChange={e => setOverride(e.target.value)}
        />
        {override && (
          <button className="ev-clear-override" onClick={() => setOverride('')} title="Clear override">
            <X size={12} />
          </button>
        )}
      </div>
      <button
        className={`ev-save-capacity-btn${dirty ? ' ev-save-capacity-btn--dirty' : ''}`}
        onClick={handleSave}
        disabled={!dirty || saving}
      >
        {saving ? <Loader2 size={13} className="ev-spinner" /> : <Check size={13} />}
        Save
      </button>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────
// `embedded` renders the content without its own AdminLayout so the page can
// live as a tab inside Parking Space Management.
export default function Events({ embedded = false }) {
  const Wrapper = embedded ? Fragment : AdminLayout
  const [settings, setSettings]           = useState(null)
  const [modeLoading, setModeLoading]     = useState({ parking: false, entry: false })
  const [zones, setZones]                 = useState([])
  const [events, setEvents]               = useState([])
  const [pageLoading, setPageLoading]     = useState(true)
  const [showAdd, setShowAdd]             = useState(false)
  const [showArchived, setShowArchived]   = useState(false)

  const loadEventsData = () => {
    Promise.all([
      getSystemSettings(),
      zoneApi.listAll(),
      getEvents(),
    ])
      .then(([{ data: s }, z, { data: ev }]) => {
        setSettings(s)
        setZones(z)
        setEvents(ev)
      })
      .catch(() => toast.error('Failed to load events data.'))
      .finally(() => setPageLoading(false))
  }

  useEffect(() => { loadEventsData() }, [])

  // Live-refresh on event / settings / zone changes
  useLiveUpdates(loadEventsData, ['event', 'systemsettings', 'parkingzone'])

  const toggleMode = async (field) => {
    const key = field === 'event_mode_parking' ? 'parking' : 'entry'
    setModeLoading(p => ({ ...p, [key]: true }))
    try {
      const { data } = await patchSystemSettings({ [field]: !settings[field] })
      setSettings(data)
      toast.success(`${key === 'parking' ? 'Parking' : 'Entry'} override ${data[field] ? 'enabled' : 'disabled'}.`)
    } catch {
      toast.error('Failed to update event mode.')
    } finally {
      setModeLoading(p => ({ ...p, [key]: false }))
    }
  }

  const handleZoneSaved = (id, val) => {
    setZones(prev => prev.map(z => z.id === id ? { ...z, capacity_override: val } : z))
  }

  const handleEventCreated = (ev) => setEvents(prev => [ev, ...prev])
  const handleEventUpdated = (ev) => setEvents(prev => prev.map(e => e.id === ev.id ? ev : e))
  const handleEventDeleted = (id) => setEvents(prev => prev.filter(e => e.id !== id))

  return (
    <Wrapper>
      <div className="ev-page">

        {/* ── Header ─────────────────────────────── */}
        {/* Embedded, this whole block is dropped: the two section headings
            below ("Event Mode", "Events & Organizers") already say what each
            part does, and Add Event moves down to the list it acts on. */}
        {!embedded && (
          <div className="ev-header">
            <div>
              <h1 className="ev-title">Events</h1>
              <p className="ev-subtitle">
                Activate event mode, manage parking capacity overrides, and track organizer vehicles.
              </p>
            </div>
            <button className="ev-btn ev-btn-primary" onClick={() => setShowAdd(true)}>
              <Plus size={15} /> Add Event
            </button>
          </div>
        )}

        {pageLoading ? (
          <div className="ev-loading">
            <Loader2 size={28} className="ev-spinner" />
            <span>Loading…</span>
          </div>
        ) : (
          <>
            {/* ── Event Mode ─────────────────────────── */}
            <section className="ev-section">
              <div className="ev-section-head">
                <h2 className="ev-section-title">Event Mode</h2>
                <p className="ev-section-desc">
                  When enabled, security guards gain override access for the selected systems during the event.
                </p>
              </div>

              <div className="ev-mode-grid">
                <ModeToggle
                  label="Parking Override"
                  description="Guards can admit vehicles even when a zone is at full capacity."
                  enabled={settings?.event_mode_parking ?? false}
                  loading={modeLoading.parking}
                  onToggle={() => toggleMode('event_mode_parking')}
                />
                <ModeToggle
                  label="Entry Override"
                  description="Guards can allow entry for plates that would otherwise be denied."
                  enabled={settings?.event_mode_entry ?? false}
                  loading={modeLoading.entry}
                  onToggle={() => toggleMode('event_mode_entry')}
                />
              </div>

              {/* Capacity overrides — shown when parking mode is on */}
              {settings?.event_mode_parking && (
                <div className="ev-capacity-block">
                  <div className="ev-capacity-head">
                    <ParkingCircle size={15} />
                    <span>Zone Capacity Overrides</span>
                    <span className="ev-capacity-hint">
                      Set a temporary capacity limit per zone. Leave blank to use the zone's default.
                    </span>
                  </div>

                  {zones.length === 0 ? (
                    <p className="ev-empty">No parking zones configured.</p>
                  ) : (
                    <div className="ev-zone-table">
                      <div className="ev-zone-header">
                        <span>Zone</span>
                        <span>Default</span>
                        <span>Override</span>
                        <span />
                      </div>
                      {zones.map(z => (
                        <ZoneCapacityRow key={z.id} zone={z} onSaved={handleZoneSaved} />
                      ))}
                    </div>
                  )}
                </div>
              )}
            </section>

            {/* ── Events List ────────────────────────── */}
            <section className="ev-section">
              <div className="ev-section-head ev-section-head--row">
                <div>
                  <h2 className="ev-section-title">Events &amp; Organizers</h2>
                  <p className="ev-section-desc">
                    While an event is running, an unregistered organizer plate is let in at the gate
                    and gets an event slip. Registered vehicles keep their usual rules.
                  </p>
                </div>
                {/* Sits with the list it adds to, now that the page header is gone */}
                {embedded && (
                  <button className="ev-btn ev-btn-primary" onClick={() => setShowAdd(true)}>
                    <Plus size={15} /> Add Event
                  </button>
                )}
              </div>

              {(() => {
                const upcoming = events.filter(e => !e.archived)
                const archived = events.filter(e => e.archived)
                return (
                  <>
                    {upcoming.length === 0 ? (
                      <div className="ev-empty-state">
                        <CalendarDays size={36} className="ev-empty-icon" />
                        <p>No upcoming events. Add one to get started.</p>
                      </div>
                    ) : (
                      <div className="ev-events-list">
                        {upcoming.map(ev => (
                          <EventCard
                            key={ev.id}
                            event={ev}
                            onUpdated={handleEventUpdated}
                            onDeleted={handleEventDeleted}
                          />
                        ))}
                      </div>
                    )}

                    {archived.length > 0 && (
                      <div className="ev-archived-section">
                        <button
                          className="ev-archived-toggle"
                          onClick={() => setShowArchived(p => !p)}
                        >
                          <Archive size={14} />
                          Archived Events ({archived.length})
                          {showArchived ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                        </button>

                        {showArchived && (
                          <div className="ev-events-list ev-events-list--archived">
                            {archived.map(ev => (
                              <EventCard
                                key={ev.id}
                                event={ev}
                                onUpdated={handleEventUpdated}
                                onDeleted={handleEventDeleted}
                              />
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                  </>
                )
              })()}
            </section>
          </>
        )}
      </div>

      {showAdd && (
        <AddEventModal
          onClose={() => setShowAdd(false)}
          onCreated={handleEventCreated}
        />
      )}
    </Wrapper>
  )
}
