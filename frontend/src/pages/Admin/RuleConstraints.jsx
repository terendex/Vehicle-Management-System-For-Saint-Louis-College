import { useState, useEffect } from 'react'
import { useLiveUpdates } from '../../realtime/useLiveUpdates'
import {
  CalendarDays, Clock, Pencil, X, Settings2,
  Loader2, User, Car, Users, ChevronRight, Truck, Timer,
  CalendarRange, Globe, Plus, CheckCircle, Archive, AlertTriangle,
} from 'lucide-react'
import notify, { toast } from '../../components/Feedback/notify'
import { fieldProblems } from '../../components/Feedback/formProblems'
import {
  getRuleConstraints, createRuleConstraint, updateRuleConstraint, getSystemSettings,
  getRegistrationPeriods, createRegistrationPeriod, updateRegistrationPeriod,
  activateRegistrationPeriod, deactivateRegistrationPeriod,
} from '../../api/vehicles'
import api from '../../api/axios'
import useTwofaStore from '../../stores/twofaStore'
import './RuleConstraints.css'

// ─── Constants ────────────────────────────────────────────────────────────────

/* Registration windows are named for the school year they belong to, so the
   label is picked rather than typed. Typing it produced four different names
   for the same thing — 'test', 'test 2', 'S.Y 26-27' and 'SY 26-27' — which is
   no help at all when you are looking down the archived list a year later.
   The window is opened once per school year, not once per term, so the label
   carries the year alone — a term in it only invited a second window that the
   registration rules never actually recognised. */
function periodLabelOptions(today = new Date()) {
  // The school year rolls over in June: register in April and you are still
  // finishing the year that began the previous June, so the label has to be
  // that year's, not the calendar year's.
  const startYear = today.getMonth() >= 5 ? today.getFullYear() : today.getFullYear() - 1
  return [-1, 0, 1, 2].map((offset) => {
    const from = startYear + offset
    return `S.Y. ${from}–${from + 1}`
  })
}

const DAY_LABELS = [
  { key: 'mon', label: 'Mon' },
  { key: 'tue', label: 'Tue' },
  { key: 'wed', label: 'Wed' },
  { key: 'thu', label: 'Thu' },
  { key: 'fri', label: 'Fri' },
  { key: 'sat', label: 'Sat' },
  { key: 'sun', label: 'Sun' },
]

const ENTRY_TYPES = [
  {
    key: 'student_vehicle',
    title: 'Student — Vehicle',
    desc: 'Registered SLC student with a car or motorcycle',
    Icon: User,
    hasOwnDays: true,
  },
  {
    key: 'employee',
    title: 'Employee',
    desc: 'SLC faculty or staff member',
    Icon: Car,
  },
  {
    key: 'fetcher',
    title: 'Fetcher / Drop & Go',
    desc: 'Parent or guardian fetching a student',
    Icon: Users,
    hasStayLimit: true,
    hasOwnDays: true,
  },
  {
    key: 'supplier',
    title: 'Supplier',
    desc: 'Supplier company vehicles (auto-permitted plates)',
    Icon: Truck,
    hasStayLimit: true,
  },
]

function formatTime12(t) {
  if (!t) return ''
  const [h, m] = t.split(':').map(Number)
  const ampm = h >= 12 ? 'PM' : 'AM'
  const h12 = h % 12 || 12
  return `${h12}:${String(m).padStart(2, '0')} ${ampm}`
}

// ─── Edit Modal ───────────────────────────────────────────────────────────────

function EditModal({ entryType, rule, onSave, onClose }) {
  const [days, setDays] = useState(rule?.days ?? ['mon', 'tue', 'wed', 'thu', 'fri', 'sat'])
  const [startTime, setStartTime] = useState(rule?.start_time ?? '06:00')
  const [endTime, setEndTime] = useState(rule?.end_time ?? '19:00')
  const [enabled, setEnabled] = useState(rule?.enabled ?? true)
  const [maxStay, setMaxStay] = useState(rule?.max_stay_minutes ?? '')

  const toggleDay = (key) =>
    setDays((prev) => prev.includes(key) ? prev.filter((d) => d !== key) : [...prev, key])

  const handleSubmit = async (e) => {
    e.preventDefault()
    // The form carries noValidate, so the browser's own bubble is gone and
    // its complaints have to be re-raised here.
    if (await notify.validation(fieldProblems(e.currentTarget))) return
    const payload = { days, start_time: startTime, end_time: endTime, enabled }
    if (entryType.hasStayLimit) {
      payload.max_stay_minutes = maxStay === '' ? null : Math.max(1, parseInt(maxStay, 10) || 0)
    }
    onSave(payload)
    onClose()
  }

  return (
    <div className="rc-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="rc-modal">
        <div className="rc-modal-head">
          <span className="rc-modal-title">
            <Pencil size={15} />
            {entryType.title}
          </span>
          <button className="rc-modal-close" onClick={onClose}><X size={15} /></button>
        </div>

        <form onSubmit={handleSubmit} noValidate>
          <div className="rc-modal-body">
            <div className="rc-field">
              <label className="rc-field-label">Allowed Days</label>
              <div className="rc-day-selector">
                {DAY_LABELS.map((d) => (
                  <button
                    key={d.key}
                    type="button"
                    className={`rc-day-chip editable ${days.includes(d.key) ? 'active' : ''}`}
                    onClick={() => toggleDay(d.key)}
                  >
                    {d.label}
                  </button>
                ))}
              </div>
              {entryType.hasOwnDays && (
                <span style={{ fontSize: 11.5, color: '#64839C', marginTop: 4, display: 'block' }}>
                  Campus-wide ceiling. An owner still needs the day on their own
                  registered campus days to be let in.
                </span>
              )}
            </div>

            <div className="rc-field-row">
              <div className="rc-field">
                <label className="rc-field-label">From</label>
                <input
                  className="rc-field-input"
                  type="time"
                  value={startTime}
                  onChange={(e) => setStartTime(e.target.value)}
                  required
                />
              </div>
              <div className="rc-field">
                <label className="rc-field-label">To</label>
                <input
                  className="rc-field-input"
                  type="time"
                  value={endTime}
                  onChange={(e) => setEndTime(e.target.value)}
                  required
                />
              </div>
            </div>

            {entryType.hasStayLimit && (
              <div className="rc-field">
                <label className="rc-field-label">Max Stay (minutes)</label>
                <input
                  className="rc-field-input"
                  type="number"
                  min="1"
                  placeholder="No limit"
                  value={maxStay}
                  onChange={(e) => setMaxStay(e.target.value)}
                />
                <span style={{ fontSize: 11.5, color: '#64839C', marginTop: 4, display: 'block' }}>
                  Exceeding this on exit auto-issues a time-exceed violation. Leave blank for no limit.
                </span>
              </div>
            )}

            <div className="rc-field">
              <label className="rc-field-label">Status</label>
              <label style={{ display: 'flex', alignItems: 'center', gap: '10px', cursor: 'pointer' }}>
                <label className="rc-toggle" style={{ margin: 0 }}>
                  <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
                  <span className="rc-toggle-track" />
                </label>
                <span style={{ fontSize: '13px', color: '#3E5B72', fontWeight: 500 }}>
                  {enabled
                    ? 'Enabled — day & time restrictions apply at the gate'
                    : entryType.hasOwnDays
                      ? 'Disabled — this rule adds no restriction, but each owner’s own registered campus days still apply'
                      : 'Disabled — no schedule restriction (entry not limited by this rule)'}
                </span>
              </label>
            </div>
          </div>

          <div className="rc-modal-foot">
            <button type="button" className="rc-btn rc-btn-secondary" onClick={onClose}>Cancel</button>
            <button type="submit" className="rc-btn rc-btn-primary">Save Changes</button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ─── Mode Toggle Button ───────────────────────────────────────────────────────

function ModeToggle({ active, onToggle, activeLabel, inactiveLabel, activeColor = '#12915A' }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
      <button
        onClick={onToggle}
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 8,
          padding: '8px 18px', borderRadius: 8, border: '1.5px solid',
          fontSize: 13, fontWeight: 700, cursor: 'pointer',
          background:  active ? activeColor : '#fff',
          color:       active ? '#fff'      : '#2E4C63',
          borderColor: active ? activeColor : '#BDD4E5',
          transition: 'all 0.15s',
        }}
      >
        <span style={{ width: 32, height: 18, borderRadius: 9, border: '2px solid', display: 'inline-flex', alignItems: 'center', borderColor: active ? '#fff6' : '#64839C', background: 'none' }}>
          <span style={{ width: 14, height: 14, borderRadius: '50%', background: active ? '#fff' : '#64839C', marginLeft: active ? 14 : 0, transition: 'all 0.2s', flexShrink: 0 }} />
        </span>
        {active ? activeLabel : inactiveLabel}
      </button>
    </div>
  )
}

// ─── Main Page ────────────────────────────────────────────────────────────────

// Three separate jobs live on this page and only one is ever being done at a
// time: setting who may enter and when, opening or closing the registration
// window for a school year, and flipping the campus-wide override. Showing all
// three at once made the page a scroll; they are tabs now.
//
// Entry Rules leads because it is the page's namesake and by far the most
// edited — the registration window is touched once a year and the override
// only for an event.
const TABS = [
  { id: 'entry',   label: 'Entry Rules',         icon: CalendarDays },
  { id: 'periods', label: 'Registration Period', icon: CalendarRange },
  { id: 'access',  label: 'Access Mode',         icon: Globe },
]

export default function RuleConstraints() {
  const [rules,       setRules]       = useState({})
  const [loading,     setLoading]     = useState(true)
  const [editingType, setEditingType] = useState(null)

  // System settings (open campus mode only now)
  const SS_DEFAULTS = { open_campus_mode: false }
  const [ss,        setSs]        = useState(SS_DEFAULTS)
  const [ssLoading, setSsLoading] = useState(true)

  // Registration periods
  const [periods,        setPeriods]        = useState([])
  const [periodsLoading, setPeriodsLoading] = useState(true)
  // null when closed, { mode: 'add' } or { mode: 'edit', id } while open
  const [periodEditor,   setPeriodEditor]   = useState(null)
  const EMPTY_PERIOD = { label: '', start_date: '', end_date: '' }
  const [periodForm,     setPeriodForm]     = useState(EMPTY_PERIOD)
  const [periodErrors,   setPeriodErrors]   = useState({})
  const [savingPeriod,   setSavingPeriod]   = useState(false)
  const [togglingId,     setTogglingId]     = useState(null)
  const [confirmAction,  setConfirmAction]  = useState(null) // { type, id?, period? }
  const [tab,            setTab]            = useState('entry')

  useEffect(() => {
    let cancelled = false
    getRuleConstraints()
      .then((res) => {
        if (cancelled) return
        const data = res.data?.results ?? res.data ?? []
        const map = {}
        data.forEach((r) => { map[r.constraint_type] = r })
        setRules(map)
        setLoading(false)
      })
      .catch(() => { if (!cancelled) setLoading(false) })

    getSystemSettings()
      .then(({ data }) => {
        if (cancelled) return
        setSs({ open_campus_mode: data.open_campus_mode ?? false })
        setSsLoading(false)
      })
      .catch(() => { if (!cancelled) setSsLoading(false) })

    getRegistrationPeriods()
      .then(({ data }) => { if (!cancelled) setPeriods(data) })
      .catch(() => {})
      .finally(() => { if (!cancelled) setPeriodsLoading(false) })

    return () => { cancelled = true }
  }, [])

  // Live-refresh rules / open-campus mode / registration periods on change
  const reloadRuleData = () => {
    getRuleConstraints()
      .then((res) => {
        const data = res.data?.results ?? res.data ?? []
        const map = {}
        data.forEach((r) => { map[r.constraint_type] = r })
        setRules(map)
      })
      .catch(() => {})
    getSystemSettings()
      .then(({ data }) => setSs({ open_campus_mode: data.open_campus_mode ?? false }))
      .catch(() => {})
    getRegistrationPeriods()
      .then(({ data }) => setPeriods(data))
      .catch(() => {})
  }
  useLiveUpdates(reloadRuleData, ['ruleconstraint', 'systemsettings', 'registrationperiod'])

  // Asked for before the change goes anywhere, rather than after the server
  // bounces it — these rules decide who may enter campus, and the prompt should
  // read as part of the decision, not as an error.
  const ensureStepUp = useTwofaStore((s) => s.ensureStepUp)

  const handleSave = async (data) => {
    const rule = rules[editingType.key]
    try {
      await ensureStepUp('Confirm it’s you before changing a campus rule.')
    } catch {
      return
    }
    try {
      // A fresh database may not have a row for this type yet — create it on first save
      const { data: saved } = rule?.id
        ? await updateRuleConstraint(rule.id, data)
        : await createRuleConstraint({ ...data, name: editingType.title, constraint_type: editingType.key })
      setRules((prev) => ({ ...prev, [editingType.key]: saved }))
      toast.success('Schedule updated.')
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Failed to update schedule.')
    }
  }

  const toggleMode = async (field) => {
    const next = !ss[field]
    try {
      await ensureStepUp('Confirm it’s you before changing this mode.')
    } catch {
      return
    }
    try {
      const { data } = await api.patch('/vehicles/system-settings/', { [field]: next })
      setSs(f => ({ ...f, [field]: data[field] }))
      const labels = {
        open_campus_mode: next ? 'Open Campus Mode ENABLED — all vehicles will be allowed.' : 'Open Campus Mode disabled.',
      }
      toast[next ? 'success' : 'info'](labels[field])
    } catch {
      toast.error('Failed to toggle mode.')
    }
  }

  const openAddPeriod = () => {
    setPeriodEditor({ mode: 'add' })
    setPeriodForm(EMPTY_PERIOD)
    setPeriodErrors({})
  }

  const openEditPeriod = (p) => {
    setPeriodEditor({ mode: 'edit', id: p.id })
    setPeriodForm({ label: p.label, start_date: p.start_date, end_date: p.end_date })
    setPeriodErrors({})
  }

  const closePeriodEditor = () => {
    setPeriodEditor(null)
    setPeriodForm(EMPTY_PERIOD)
    setPeriodErrors({})
  }

  const handleSavePeriod = async () => {
    const editing = periodEditor?.mode === 'edit'
    const errors = {}
    if (!periodForm.label.trim())      errors.label      = 'Select a school year.'
    if (!periodForm.start_date)        errors.start_date = 'Start date is required.'
    if (!periodForm.end_date)          errors.end_date   = 'End date is required.'
    if (periodForm.start_date && periodForm.end_date && periodForm.end_date < periodForm.start_date)
      errors.end_date = 'End date must be on or after start date.'
    setPeriodErrors(errors)
    if (await notify.validation(errors)) return

    setSavingPeriod(true)
    try {
      if (editing) {
        const { data } = await updateRegistrationPeriod(periodEditor.id, periodForm)
        setPeriods(prev => prev.map(p => (p.id === data.id ? data : p)))
        toast.success('Registration period updated.')
      } else {
        const { data } = await createRegistrationPeriod(periodForm)
        setPeriods(prev => [data, ...prev.map(p => ({ ...p, is_active: false }))])
        toast.success('Registration period created and set as active.')
      }
      closePeriodEditor()
    } catch (err) {
      /* Only a payload that actually names form fields can be shown against
         them. A PATCH can also come back as {detail: …} — the period was
         archived by someone else, or the session lost its permission — and
         routing that into the field errors would render nothing at all,
         leaving the save looking like it silently did nothing. */
      const d = err.response?.data
      const fieldErrors = d && typeof d === 'object' && !Array.isArray(d)
        ? Object.fromEntries(Object.entries(d).filter(([k]) => k in EMPTY_PERIOD))
        : {}
      if (Object.keys(fieldErrors).length) {
        setPeriodErrors(fieldErrors)
        notify.validation(fieldErrors, {
          title: `Period not ${editing ? 'updated' : 'created'}`,
        })
      } else {
        toast.error(d?.detail || `Failed to ${editing ? 'update' : 'create'} registration period.`)
      }
    } finally {
      setSavingPeriod(false)
    }
  }

  const handleActivatePeriod = async (id) => {
    setTogglingId(id)
    try {
      const { data } = await activateRegistrationPeriod(id)
      setPeriods(prev => prev.map(p => ({ ...p, is_active: p.id === data.id })))
      toast.success('Registration period activated.')
    } catch {
      toast.error('Failed to activate period.')
    } finally {
      setTogglingId(null)
    }
  }

  const handleDeactivatePeriod = async (id) => {
    setTogglingId(id)
    try {
      await deactivateRegistrationPeriod(id)
      setPeriods(prev => prev.map(p => p.id === id ? { ...p, is_active: false } : p))
      toast.info('Registration period deactivated. No active period — registrations closed.')
    } catch {
      toast.error('Failed to deactivate period.')
    } finally {
      setTogglingId(null)
    }
  }

  return (
    <>
      <div className="rc-page">

        {/* ── Header ──────────────────────────────────────────── */}
        <div className="rc-header">
          <div>
            <h1 className="rc-title">Rule Constraints</h1>
            <p className="rc-subtitle">Entry schedules, registration window, and campus access mode — pick a category below.</p>
          </div>
          <div className="rc-config-badge">
            <Settings2 /> CONFIGURATION
          </div>
        </div>

        {/* Open Campus Mode overrides every rule on this page, so when it is on
            it has to be visible from whichever tab you are standing on — the
            Entry Rules tab showing carefully configured schedules that are
            currently being ignored would otherwise be actively misleading. */}
        {ss.open_campus_mode && (
          <div className="rc-open-banner">
            <AlertTriangle size={16} />
            <span>
              <strong>Open Campus Mode is ON.</strong> Every vehicle is admitted regardless of
              registration or schedule — the entry rules below are not being applied.
            </span>
            <button
              type="button"
              className="rc-open-banner-link"
              onClick={() => setTab('access')}
            >
              Manage
            </button>
          </div>
        )}

        {/* ── Category tabs ────────────────────────────────────── */}
        <div className="rc-tabs" role="tablist" aria-label="Rule categories">
          {TABS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              type="button"
              role="tab"
              id={`rc-tab-${id}`}
              aria-selected={tab === id}
              aria-controls="rc-tabpanel"
              className={`rc-tab ${tab === id ? 'rc-tab--active' : ''}`}
              onClick={() => setTab(id)}
            >
              <Icon size={15} />
              <span>{label}</span>
              {id === 'access' && ss.open_campus_mode && (
                <span className="rc-tab-dot" aria-label="Open Campus Mode is on" />
              )}
            </button>
          ))}
        </div>

        <div id="rc-tabpanel" role="tabpanel" aria-labelledby={`rc-tab-${tab}`}>

          {tab === 'entry' ? (
          /* ── Entry Rules ── */
          <div className="rc-section">
            <div className="rc-section-head">
              <span className="rc-section-label">
                <CalendarDays size={17} />
                Entry Rules
              </span>
            </div>

            <div className="rc-section-body" style={{ padding: 0 }}>
              {loading ? (
                <div className="rc-empty">
                  <Loader2 size={36} className="rc-spin" />
                  <p>Loading entry rules…</p>
                </div>
              ) : (
                <div className="rc-entry-list">
                  {ENTRY_TYPES.map((et) => {
                    const rule = rules[et.key]
                    const { Icon } = et
                    return (
                      <div key={et.key} className="rc-entry-card" style={{ opacity: rule?.enabled === false ? 0.5 : 1 }}>
                        <div className="rc-entry-icon">
                          <Icon size={20} />
                        </div>
                        <div className="rc-entry-info">
                          <p className="rc-entry-title">{et.title}</p>
                          <p className="rc-entry-desc">{et.desc}</p>
                          {rule && (
                            <div className="rc-entry-schedule">
                              <span className="rc-entry-days">
                                {DAY_LABELS.filter((d) => rule.days?.includes(d.key)).map((d) => d.label).join(' · ')}
                              </span>
                              <span className="rc-entry-time">
                                <Clock size={11} />
                                {formatTime12(rule.start_time)} – {formatTime12(rule.end_time)}
                              </span>
                              {rule.max_stay_minutes != null && (
                                <span className="rc-entry-time" style={{ color: '#8A6B00' }}>
                                  <Timer size={11} />
                                  Max stay: {rule.max_stay_minutes} min
                                </span>
                              )}
                            </div>
                          )}
                        </div>
                        <button
                          className="rc-entry-chevron"
                          onClick={() => setEditingType(et)}
                          title="Edit schedule"
                        >
                          <ChevronRight size={18} />
                        </button>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          </div>
          ) : tab === 'periods' ? (
          /* ── Registration Period ── */
          <div className="rc-section">
            <div className="rc-section-head">
              <span className="rc-section-label">
                <CalendarRange size={17} />
                Vehicle Registration Period
              </span>
              <button
                className="rc-btn rc-btn-primary rc-btn-sm"
                onClick={() => (periodEditor?.mode === 'add' ? closePeriodEditor() : openAddPeriod())}
              >
                <Plus size={14} /> New Period
              </button>
            </div>
            <div className="rc-section-body">
              {/* Add / edit form */}
              {periodEditor && (() => {
                const editing = periodEditor.mode === 'edit'
                /* An older window's label is no longer in the rolling option list,
                   and a running window's start date is in the past — neither may
                   be dropped just because the form was reopened to move the end
                   date, so both are carried into the inputs as they stand. */
                const rolling = periodLabelOptions()
                const labelOptions = periodForm.label && !rolling.includes(periodForm.label)
                  ? [periodForm.label, ...rolling]
                  : rolling
                return (
                  <div className="rc-period-form">
                    <p className="rc-period-form-title">
                      {editing
                        ? <><Pencil size={13} /> Editing period</>
                        : <><Plus size={13} /> New registration period</>}
                    </p>
                    <div className="rc-period-form-fields">
                      <div className="rc-reg-field" style={{ flex: 2 }}>
                        <label className="rc-field-label">Label <span style={{ color: '#D93B3B' }}>*</span></label>
                        <select
                          className={`rc-field-select ${periodErrors.label ? 'rc-input-error' : ''}`}
                          value={periodForm.label}
                          onChange={e => setPeriodForm(f => ({ ...f, label: e.target.value }))}
                        >
                          <option value="">Select a school year…</option>
                          {labelOptions.map(opt => (
                            <option key={opt} value={opt}>{opt}</option>
                          ))}
                        </select>
                      </div>
                      <div className="rc-reg-field">
                        <label className="rc-field-label">Start date <span style={{ color: '#D93B3B' }}>*</span></label>
                        <input
                          type="date"
                          className={`rc-field-input ${periodErrors.start_date ? 'rc-input-error' : ''}`}
                          value={periodForm.start_date}
                          min={editing ? undefined : new Date().toISOString().slice(0, 10)}
                          onChange={e => setPeriodForm(f => ({ ...f, start_date: e.target.value }))}
                        />
                      </div>
                      <div className="rc-reg-field">
                        <label className="rc-field-label">End date <span style={{ color: '#D93B3B' }}>*</span></label>
                        <input
                          type="date"
                          className={`rc-field-input ${periodErrors.end_date ? 'rc-input-error' : ''}`}
                          value={periodForm.end_date}
                          min={periodForm.start_date || undefined}
                          onChange={e => setPeriodForm(f => ({ ...f, end_date: e.target.value }))}
                        />
                      </div>
                    </div>
                    <div className="rc-period-form-actions">
                      <button className="rc-btn rc-btn-secondary" onClick={closePeriodEditor}>Cancel</button>
                      <button className="rc-btn rc-btn-primary" disabled={savingPeriod} onClick={handleSavePeriod}>
                        {savingPeriod
                          ? <Loader2 size={14} className="rc-spin" />
                          : editing ? <Pencil size={14} /> : <Plus size={14} />}
                        {savingPeriod ? 'Saving…' : editing ? 'Save Changes' : 'Save & Activate'}
                      </button>
                    </div>
                  </div>
                )
              })()}

              {/* Periods table */}
              {periodsLoading ? (
                <div className="rc-empty"><Loader2 size={22} className="rc-spin" /></div>
              ) : periods.length === 0 ? (
                <p className="rc-mode-desc" style={{ color: '#64839C', fontStyle: 'italic' }}>
                  No registration periods yet. Click "New Period" to create one.
                </p>
              ) : (
                <div className="rc-period-table-wrap">
                  <table className="rc-period-table">
                    <thead>
                      <tr>
                        <th>Label</th>
                        <th>Start</th>
                        <th>End</th>
                        <th>Status</th>
                        <th></th>
                      </tr>
                    </thead>
                    <tbody>
                      {periods.map(p => {
                        const fmt = (d) => new Date(d + 'T00:00:00').toLocaleDateString('en-PH', { month: 'short', day: 'numeric', year: 'numeric' })
                        const todayIso = new Date().toLocaleDateString('en-CA')
                        const ended = p.end_date < todayIso
                        const isActive = p.is_active && !ended
                        return (
                          <tr key={p.id} className={isActive ? 'rc-period-row--active' : ''}>
                            <td className="rc-period-label">{p.label}</td>
                            <td>{fmt(p.start_date)}</td>
                            <td>{fmt(p.end_date)}</td>
                            <td>
                              {isActive
                                ? <span className="rc-period-badge rc-period-badge--active"><CheckCircle size={11} /> Active</span>
                                : <span className="rc-period-badge rc-period-badge--archived"><Archive size={11} /> Archived</span>}
                            </td>
                            <td>
                              <div className="rc-period-actions">
                                {/* Editable while it is still running — a deadline
                                    that has to move is the whole reason to touch a
                                    live window, and archiving to re-create one only
                                    leaves a duplicate row behind. */}
                                {!ended && (
                                  <button
                                    className="rc-btn rc-btn-secondary rc-btn-sm"
                                    onClick={() => openEditPeriod(p)}
                                  >
                                    <Pencil size={12} /> Edit
                                  </button>
                                )}
                                {ended ? null : isActive ? (
                                  <button
                                    className="rc-btn rc-btn-secondary rc-btn-sm"
                                    disabled={togglingId === p.id}
                                    onClick={() => setConfirmAction({ type: 'deactivatePeriod', id: p.id, period: p })}
                                  >
                                    {togglingId === p.id ? <Loader2 size={12} className="rc-spin" /> : <Archive size={12} />}
                                    Deactivate
                                  </button>
                                ) : (
                                  <button
                                    className="rc-btn rc-btn-primary rc-btn-sm"
                                    disabled={togglingId === p.id}
                                    onClick={() => setConfirmAction({ type: 'activatePeriod', id: p.id, period: p })}
                                  >
                                    {togglingId === p.id ? <Loader2 size={12} className="rc-spin" /> : <CheckCircle size={12} />}
                                    Set Active
                                  </button>
                                )}
                              </div>
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
          ) : (
          /* ── Open Campus Mode ── */
          <div className="rc-section">
            <div className="rc-section-head">
              <span className="rc-section-label">
                <Globe size={17} />
                Open Campus Mode
              </span>
              {ss.open_campus_mode && (
                <span className="rc-mode-active-badge rc-mode-active-badge--open">ACTIVE</span>
              )}
            </div>
            <div className="rc-section-body">
              {ssLoading ? (
                <div className="rc-empty"><Loader2 size={22} className="rc-spin" /></div>
              ) : (
                <div className="rc-mode-block rc-mode-block--open">
                  <p className="rc-mode-desc">
                    When enabled, <strong>all vehicles</strong> are allowed to enter regardless of registration status,
                    schedule rules, or entry constraints. Use during open events, graduation, or campus-wide access days.
                  </p>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
                    <ModeToggle
                      active={ss.open_campus_mode}
                      onToggle={() => setConfirmAction({ type: 'toggleCampusMode' })}
                      activeLabel="Open Campus ON"
                      inactiveLabel="Open Campus OFF"
                      activeColor="#1072B3"
                    />
                    <span style={{ fontSize: 12, color: ss.open_campus_mode ? '#1072B3' : '#64839C', fontWeight: 600 }}>
                      {ss.open_campus_mode
                        ? 'All vehicles are freely allowed — all rules bypassed.'
                        : 'Normal entry restrictions apply.'}
                    </span>
                  </div>
                </div>
              )}
            </div>
          </div>
          )}

        </div>


      </div>

      {editingType && (
        <EditModal
          entryType={editingType}
          rule={rules[editingType.key]}
          onSave={handleSave}
          onClose={() => setEditingType(null)}
        />
      )}

      {/* ── Confirmation Modal ── */}
      {confirmAction && (() => {
        const isCampus    = confirmAction.type === 'toggleCampusMode'
        const isActivate  = confirmAction.type === 'activatePeriod'
        const isDeactivate = confirmAction.type === 'deactivatePeriod'
        const enabling = isCampus && !ss.open_campus_mode

        const config = isCampus ? {
          title:   enabling ? 'Enable Open Campus Mode?' : 'Disable Open Campus Mode?',
          body:    enabling
            ? 'All vehicles will be allowed to enter regardless of registration, schedule, or entry constraints.'
            : 'Normal entry restrictions will apply again.',
          confirm: enabling ? 'Enable' : 'Disable',
          cls:     enabling ? 'rc-confirm-btn rc-confirm-btn--danger' : 'rc-confirm-btn rc-confirm-btn--primary',
          onConfirm: () => { setConfirmAction(null); toggleMode('open_campus_mode') },
        } : isActivate ? {
          title:   'Set as Active Period?',
          body:    `"${confirmAction.period.label}" will become the active registration period. The current active period (if any) will be archived.`,
          confirm: 'Set Active',
          cls:     'rc-confirm-btn rc-confirm-btn--primary',
          onConfirm: () => { setConfirmAction(null); handleActivatePeriod(confirmAction.id) },
        } : {
          title:   'Deactivate Period?',
          body:    `"${confirmAction.period.label}" will be archived. No registration period will be active — new vehicle registrations will be closed.`,
          confirm: 'Deactivate',
          cls:     'rc-confirm-btn rc-confirm-btn--warning',
          onConfirm: () => { setConfirmAction(null); handleDeactivatePeriod(confirmAction.id) },
        }

        return (
          <div className="rc-overlay" onClick={() => setConfirmAction(null)}>
            <div className="rc-confirm-modal" onClick={e => e.stopPropagation()}>
              <button className="rc-confirm-close" onClick={() => setConfirmAction(null)}><X size={16} /></button>
              <AlertTriangle size={32} className={`rc-confirm-icon ${enabling || isDeactivate ? 'rc-confirm-icon--warn' : 'rc-confirm-icon--info'}`} />
              <h2 className="rc-confirm-title">{config.title}</h2>
              <p className="rc-confirm-body">{config.body}</p>
              <div className="rc-confirm-actions">
                <button className="rc-btn rc-btn-secondary" onClick={() => setConfirmAction(null)}>Cancel</button>
                <button className={config.cls} onClick={config.onConfirm}>{config.confirm}</button>
              </div>
            </div>
          </div>
        )
      })()}

    </>
  )
}
