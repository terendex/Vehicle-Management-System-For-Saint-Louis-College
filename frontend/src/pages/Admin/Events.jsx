import { useState, useEffect, useRef, Fragment } from 'react'
import { useLiveUpdates } from '../../realtime/useLiveUpdates'
import {
  CalendarDays, Plus, Trash2, ChevronDown, ChevronUp,
  Loader2, ToggleLeft, ToggleRight, X, AlertTriangle,
  ParkingCircle, Tag, Check, Archive, CalendarClock,
} from 'lucide-react'
import notify, { toast } from '../../components/Feedback/notify'
import { fieldProblems } from '../../components/Feedback/formProblems'
import AdminLayout from '../../components/Layout/AdminLayout'
import { getSystemSettings, patchSystemSettings, getEvents, createEvent, patchEvent, deleteEvent } from '../../api/vehicles'
import { zoneApi } from '../../api/parking'
import { formatPlateNumber, isValidPlateNumber } from '../../utils/plateFormat'
import './Events.css'

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
  const [form, setForm]       = useState({ name: '', date: '' })
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
    if (!isValidPlateNumber(p)) {
      await notify.error('Invalid Philippine plate number format.', { title: 'Plate not added' })
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
          <div className="ev-field">
            <label className="ev-label">Organizer Plates <span className="ev-label-optional">(optional)</span></label>
            <div className="ev-plate-input-row">
              <input
                ref={plateRef}
                className={`ev-text-input ev-plate-field${plateError ? ' ev-input-error' : ''}`}
                placeholder="e.g. ABC 123"
                value={plateInput}
                onChange={e => {
                  const formatted = formatPlateNumber(e.target.value)
                  setPlateInput(formatted)
                  setPlateError(formatted && !isValidPlateNumber(formatted) ? 'Invalid Philippine plate number format.' : '')
                }}
                onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addPlate() } }}
              />
              <button type="button" className="ev-add-plate-btn" onClick={addPlate}>
                <Plus size={15} /> Add
              </button>
            </div>
            <span className="ev-field-hint">e.g. ABC 1234 · AB 1234 · N123BC · ABC123</span>
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
    if (!isValidPlateNumber(p)) {
      await notify.error('Invalid Philippine plate number format.', { title: 'Plate not added' })
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
            <Tag size={12} />
            {localPlates.length} organizer plate{localPlates.length !== 1 ? 's' : ''}
          </div>
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
          <div className="ev-plates-label">Organizer Plates</div>

          <div className="ev-plate-input-row">
            <input
              ref={plateRef}
              className={`ev-text-input ev-plate-field${plateError ? ' ev-input-error' : ''}`}
              placeholder="Enter plate number"
              value={plateInput}
              onChange={e => {
                const formatted = formatPlateNumber(e.target.value)
                setPlateInput(formatted)
                setPlateError(formatted && !isValidPlateNumber(formatted) ? 'Invalid Philippine plate number format.' : '')
              }}
              onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addPlate() } }}
              disabled={saving}
            />
            <button className="ev-add-plate-btn" onClick={addPlate} disabled={saving}>
              {saving ? <Loader2 size={13} className="ev-spinner" /> : <Plus size={14} />}
              Add
            </button>
          </div>
            <span className="ev-field-hint">e.g. ABC 1234 · AB 1234 · N123BC · ABC123</span>

          {localPlates.length === 0 ? (
            <p className="ev-no-plates">No organizer plates added yet.</p>
          ) : (
            <div className="ev-plate-tags">
              {localPlates.map(p => (
                <span key={p} className="ev-plate-tag">
                  {p}
                  <button onClick={() => removePlate(p)} disabled={saving}><X size={11} /></button>
                </span>
              ))}
            </div>
          )}
        </div>
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
                    Organizer plates are noted by the system so they can be identified during entry scanning.
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
