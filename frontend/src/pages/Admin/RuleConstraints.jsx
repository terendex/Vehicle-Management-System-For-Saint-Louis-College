import { useState, useEffect } from 'react'
import {
  CalendarDays, Clock, Pencil, X, Settings2,
  Loader2, User, Car, Users, ChevronRight,
} from 'lucide-react'
import { toast } from 'sonner'
import AdminLayout from '../../components/Layout/AdminLayout'
import { getRuleConstraints, updateRuleConstraint } from '../../api/vehicles'
import './RuleConstraints.css'

// ─── Constants ────────────────────────────────────────────────────────────────

const DAY_LABELS = [
  { key: 'mon', label: 'Mon' },
  { key: 'tue', label: 'Tue' },
  { key: 'wed', label: 'Wed' },
  { key: 'thu', label: 'Thu' },
  { key: 'fri', label: 'Fri' },
  { key: 'sat', label: 'Sat' },
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

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function RuleConstraints() {
  const [rules, setRules] = useState({})
  const [loading, setLoading] = useState(true)
  const [editingType, setEditingType] = useState(null)

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

  return (
    <AdminLayout>
      <div className="rc-page">

        {/* ── Header ──────────────────────────────────────────── */}
        <div className="rc-header">
          <div>
            <h1 className="rc-title">Rule Constraints</h1>
            <p className="rc-subtitle">Configure entry schedules and access rules.</p>
          </div>
          <div className="rc-config-badge">
            <Settings2 /> CONFIGURATION
          </div>
        </div>

        {/* ── Entry Type Cards ─────────────────────────────────── */}
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
