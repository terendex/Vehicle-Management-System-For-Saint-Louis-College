import { useState, useEffect } from 'react'
import {
  CalendarDays, Clock, Pencil, X, Settings2,
  Loader2, User, Car, Users, ChevronRight,
  DoorOpen, CalendarRange, Globe,
} from 'lucide-react'
import { toast } from 'sonner'
import AdminLayout from '../../components/Layout/AdminLayout'
import { getRuleConstraints, updateRuleConstraint, getSystemSettings, updateSystemSettings } from '../../api/vehicles'
import api from '../../api/axios'
import './RuleConstraints.css'

// ─── Constants ────────────────────────────────────────────────────────────────

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

  const toggleDay = (key) =>
    setDays((prev) => prev.includes(key) ? prev.filter((d) => d !== key) : [...prev, key])

  const handleSubmit = (e) => {
    e.preventDefault()
    onSave({ days, start_time: startTime, end_time: endTime, enabled })
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

        <form onSubmit={handleSubmit}>
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

            <div className="rc-field">
              <label className="rc-field-label">Status</label>
              <label style={{ display: 'flex', alignItems: 'center', gap: '10px', cursor: 'pointer' }}>
                <label className="rc-toggle" style={{ margin: 0 }}>
                  <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
                  <span className="rc-toggle-track" />
                </label>
                <span style={{ fontSize: '13px', color: '#4B5563', fontWeight: 500 }}>
                  {enabled ? 'Enabled — entry is allowed' : 'Disabled — entry is blocked'}
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

function ModeToggle({ active, onToggle, activeLabel, inactiveLabel, activeColor = '#16a34a' }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
      <button
        onClick={onToggle}
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 8,
          padding: '8px 18px', borderRadius: 8, border: '1.5px solid',
          fontSize: 13, fontWeight: 700, cursor: 'pointer',
          background:  active ? activeColor : '#fff',
          color:       active ? '#fff'      : '#374151',
          borderColor: active ? activeColor : '#d1d5db',
          transition: 'all 0.15s',
        }}
      >
        <span style={{ width: 32, height: 18, borderRadius: 9, border: '2px solid', display: 'inline-flex', alignItems: 'center', borderColor: active ? '#fff6' : '#9ca3af', background: 'none' }}>
          <span style={{ width: 14, height: 14, borderRadius: '50%', background: active ? '#fff' : '#9ca3af', marginLeft: active ? 14 : 0, transition: 'all 0.2s', flexShrink: 0 }} />
        </span>
        {active ? activeLabel : inactiveLabel}
      </button>
    </div>
  )
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function RuleConstraints() {
  const [rules,       setRules]       = useState({})
  const [loading,     setLoading]     = useState(true)
  const [editingType, setEditingType] = useState(null)

  // System settings state
  const SS_DEFAULTS = { event_mode_entry: false, open_campus_mode: false, registration_start: '', registration_end: '' }
  const [ss,        setSs]        = useState(SS_DEFAULTS)
  const [ssSaved,   setSsSaved]   = useState(SS_DEFAULTS)
  const [ssLoading, setSsLoading] = useState(true)

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
        const normalized = {
          event_mode_entry:   data.event_mode_entry   ?? false,
          open_campus_mode:   data.open_campus_mode   ?? false,
          registration_start: data.registration_start ?? '',
          registration_end:   data.registration_end   ?? '',
        }
        setSs(normalized)
        setSsSaved(normalized)
        setSsLoading(false)
      })
      .catch(() => { if (!cancelled) setSsLoading(false) })

    return () => { cancelled = true }
  }, [])

  const handleSave = async (data) => {
    const rule = rules[editingType.key]
    if (!rule?.id) return
    try {
      const { data: saved } = await updateRuleConstraint(rule.id, data)
      setRules((prev) => ({ ...prev, [editingType.key]: saved }))
      toast.success('Schedule updated.')
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Failed to update schedule.')
    }
  }

  const toggleMode = async (field) => {
    const next = !ss[field]
    try {
      const { data } = await api.patch('/vehicles/system-settings/', { [field]: next })
      const updated = { [field]: data[field] }
      setSs(f => ({ ...f, ...updated }))
      setSsSaved(f => ({ ...f, ...updated }))
      const labels = {
        event_mode_entry: next ? 'Event Mode enabled.' : 'Event Mode disabled.',
        open_campus_mode: next ? 'Open Campus Mode ENABLED — all vehicles will be allowed.' : 'Open Campus Mode disabled.',
      }
      toast[next ? 'success' : 'info'](labels[field])
    } catch {
      toast.error('Failed to toggle mode.')
    }
  }

  const regDirty =
    ss.registration_start !== (ssSaved.registration_start ?? '') ||
    ss.registration_end   !== (ssSaved.registration_end   ?? '')

  const saveRegPeriod = async () => {
    try {
      const { data } = await updateSystemSettings({
        ...ssSaved,
        registration_start: ss.registration_start || null,
        registration_end:   ss.registration_end   || null,
      })
      const updated = { registration_start: data.registration_start ?? '', registration_end: data.registration_end ?? '' }
      setSs(f => ({ ...f, ...updated }))
      setSsSaved(f => ({ ...f, ...updated }))
      toast.success('Registration period saved.')
    } catch {
      toast.error('Failed to save registration period.')
    }
  }

  return (
    <AdminLayout>
      <div className="rc-page">

        {/* ── Header ──────────────────────────────────────────── */}
        <div className="rc-header">
          <div>
            <h1 className="rc-title">Rule Constraints</h1>
            <p className="rc-subtitle">Configure entry schedules, access modes, and registration period.</p>
          </div>
          <div className="rc-config-badge">
            <Settings2 /> CONFIGURATION
          </div>
        </div>

        {/* ── Open Campus Mode ─────────────────────────────────── */}
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
                    onToggle={() => toggleMode('open_campus_mode')}
                    activeLabel="Open Campus ON"
                    inactiveLabel="Open Campus OFF"
                    activeColor="#7c3aed"
                  />
                  <span style={{ fontSize: 12, color: ss.open_campus_mode ? '#7c3aed' : '#9ca3af', fontWeight: 600 }}>
                    {ss.open_campus_mode
                      ? 'All vehicles are freely allowed — all rules bypassed.'
                      : 'Normal entry restrictions apply.'}
                  </span>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* ── Event Mode ───────────────────────────────────────── */}
        <div className="rc-section">
          <div className="rc-section-head">
            <span className="rc-section-label">
              <DoorOpen size={17} />
              Entry Gate Event Mode
            </span>
            {ss.event_mode_entry && (
              <span className="rc-mode-active-badge rc-mode-active-badge--event">ACTIVE</span>
            )}
          </div>
          <div className="rc-section-body">
            {ssLoading ? (
              <div className="rc-empty"><Loader2 size={22} className="rc-spin" /></div>
            ) : (
              <div className="rc-mode-block">
                <p className="rc-mode-desc">
                  When enabled, denied scans at the entry gate are <strong>auto-approved</strong> and logged for audit.
                  Registration and schedule rules still apply — only the scan denial is overridden.
                  Guards can always manually override parking regardless of this setting.
                </p>
                <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
                  <ModeToggle
                    active={ss.event_mode_entry}
                    onToggle={() => toggleMode('event_mode_entry')}
                    activeLabel="Event Mode ON"
                    inactiveLabel="Event Mode OFF"
                    activeColor="#16a34a"
                  />
                  <span style={{ fontSize: 12, color: ss.event_mode_entry ? '#15803d' : '#9ca3af', fontWeight: 600 }}>
                    {ss.event_mode_entry
                      ? 'All denied entry scans are auto-overridden and logged.'
                      : 'Normal entry restrictions apply.'}
                  </span>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* ── Registration Period ───────────────────────────────── */}
        <div className="rc-section">
          <div className="rc-section-head">
            <span className="rc-section-label">
              <CalendarRange size={17} />
              Vehicle Registration Period
            </span>
          </div>
          <div className="rc-section-body">
            {ssLoading ? (
              <div className="rc-empty"><Loader2 size={22} className="rc-spin" /></div>
            ) : (
              <>
                <p className="rc-mode-desc">
                  Set the date range during which vehicle registrations are accepted.
                  Leave blank to allow registrations at any time.
                </p>
                <div className="rc-reg-row">
                  <div className="rc-reg-field">
                    <label className="rc-field-label">Start date</label>
                    <input
                      type="date"
                      className="rc-field-input"
                      value={ss.registration_start || ''}
                      onChange={e => setSs(f => ({ ...f, registration_start: e.target.value }))}
                    />
                  </div>
                  <span className="rc-reg-sep">to</span>
                  <div className="rc-reg-field">
                    <label className="rc-field-label">End date</label>
                    <input
                      type="date"
                      className="rc-field-input"
                      value={ss.registration_end || ''}
                      min={ss.registration_start || undefined}
                      onChange={e => setSs(f => ({ ...f, registration_end: e.target.value }))}
                    />
                  </div>
                  {(ss.registration_start || ss.registration_end) && (
                    <button
                      className="rc-reg-clear"
                      onClick={() => setSs(f => ({ ...f, registration_start: '', registration_end: '' }))}
                      title="Clear dates"
                    >
                      <X size={13} /> Clear
                    </button>
                  )}
                  {regDirty && (
                    <button className="rc-btn rc-btn-primary" onClick={saveRegPeriod} style={{ marginLeft: 'auto' }}>
                      Save Period
                    </button>
                  )}
                </div>
                {ss.registration_start && ss.registration_end && (
                  <div className="rc-reg-info">
                    <CalendarRange size={13} />
                    Registration open from{' '}
                    <strong>{new Date(ss.registration_start + 'T00:00:00').toLocaleDateString('en-PH', { month: 'long', day: 'numeric', year: 'numeric' })}</strong>
                    {' '}to{' '}
                    <strong>{new Date(ss.registration_end + 'T00:00:00').toLocaleDateString('en-PH', { month: 'long', day: 'numeric', year: 'numeric' })}</strong>.
                  </div>
                )}
              </>
            )}
          </div>
        </div>

        {/* ── Entry Rules ──────────────────────────────────────── */}
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

      </div>

      {editingType && (
        <EditModal
          entryType={editingType}
          rule={rules[editingType.key]}
          onSave={handleSave}
          onClose={() => setEditingType(null)}
        />
      )}
    </AdminLayout>
  )
}
