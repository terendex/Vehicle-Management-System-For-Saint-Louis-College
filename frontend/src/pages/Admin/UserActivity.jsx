import { useEffect, useMemo, useState } from 'react'
import { format } from 'date-fns'
import { X, CalendarDays, Clock, Car, AlertTriangle, LogIn, LogOut, Loader2, Ban } from 'lucide-react'
import { usersApi } from '../../api/users'
import './UserActivity.css'

/* A vehicle owner's schedule, violations and gate visits, from
   GET /accounts/users/<id>/activity/ (backend accounts/activity.py).

   OwnerActivitySummary sits inside View Profile; UserActivityModal is the full
   record, opened from its View Activity button. */

const when = (iso) => (iso ? format(new Date(iso), 'MMM d, yyyy h:mm a') : '')

function clockTime(hhmm) {
  if (!hhmm) return ''
  const [h, m] = hhmm.split(':').map(Number)
  return `${h % 12 || 12}:${String(m).padStart(2, '0')} ${h < 12 ? 'AM' : 'PM'}`
}

function minutesText(minutes) {
  if (minutes == null) return ''
  const h = Math.floor(minutes / 60), m = minutes % 60
  if (!h) return `${m} min`
  return m ? `${h} hr ${m} min` : `${h} hr`
}

// Loading is "the answer on hand is for another request", so a change of
// dates shows the spinner without a state update inside the effect.
function useActivity(userId, dateFrom = '', dateTo = '') {
  const key = `${userId}|${dateFrom}|${dateTo}`
  const [result, setResult] = useState({ key: null, data: null, error: false })
  useEffect(() => {
    let cancelled = false
    usersApi.getUserActivity(userId, { dateFrom, dateTo })
      .then((data) => { if (!cancelled) setResult({ key, data, error: false }) })
      .catch(() => { if (!cancelled) setResult({ key, data: null, error: true }) })
    return () => { cancelled = true }
  }, [key, userId, dateFrom, dateTo])
  const loading = result.key !== key
  return { loading, data: loading ? null : result.data, error: !loading && result.error }
}

function ScheduleBlock({ schedule }) {
  if (!schedule) return null
  return (
    <div className="ua-block">
      <h4><CalendarDays size={14} /> Schedule</h4>
      {schedule.days.length ? (
        <div className="ua-days">
          {schedule.days.map((d) => <span key={d} className="ua-day">{d.slice(0, 3)}</span>)}
        </div>
      ) : (
        <p className="ua-muted">No campus days on record.</p>
      )}
      <p className="ua-line">
        <Clock size={13} />
        {schedule.start_time
          ? <>Allowed {clockTime(schedule.start_time)} to {clockTime(schedule.end_time)}</>
          : <>No entry hours rule for {schedule.owner_type_label || 'this owner'}</>}
        {schedule.max_stay_minutes ? <> · stay up to {minutesText(schedule.max_stay_minutes)}</> : null}
      </p>
      {schedule.expires_at && (
        <p className="ua-muted">Pass valid until {format(new Date(schedule.expires_at + 'T00:00:00'), 'MMMM d, yyyy')}</p>
      )}
    </div>
  )
}

function ViolationRow({ v }) {
  return (
    <li className={`ua-viol ${v.settled ? 'ua-viol--settled' : ''}`}>
      <span className="ua-viol-label">{v.label}</span>
      <span className="ua-muted">{when(v.issued_at)}</span>
      <span className={`ua-pill ${v.settled ? '' : 'ua-pill--active'}`}>
        {v.settled ? v.status_label : (v.offense_number ? `Offense ${v.offense_number}` : v.status_label)}
      </span>
    </li>
  )
}

export function OwnerActivitySummary({ userId }) {
  const { loading, data, error } = useActivity(userId)
  if (loading) return <div className="ua-summary"><Loader2 size={18} className="ua-spin" /></div>
  if (error || !data) return <div className="ua-summary ua-muted">Schedule and violations could not be loaded.</div>
  const { schedule, violations, counts, vehicles } = data
  return (
    <div className="ua-summary">
      <ScheduleBlock schedule={schedule} />
      {vehicles.length > 0 && (
        <div className="ua-block">
          <h4><Car size={14} /> Vehicles</h4>
          <p className="ua-line">{vehicles.map((v) => v.plate || 'No plate').join(' · ')}</p>
        </div>
      )}
      <div className="ua-block">
        <h4><AlertTriangle size={14} /> Violations</h4>
        {counts.violations === 0 ? (
          <p className="ua-muted">No violations on record.</p>
        ) : (
          <>
            <p className="ua-line">
              {counts.violations} on record, <strong>{counts.violations_active} active</strong>
            </p>
            <ul className="ua-viol-list">
              {violations.slice(0, 3).map((v) => <ViolationRow key={v.id} v={v} />)}
            </ul>
          </>
        )}
      </div>
    </div>
  )
}

const TABS = [
  { id: 'all',        label: 'All' },
  { id: 'visits',     label: 'Entries & Exits' },
  { id: 'violations', label: 'Violations' },
]

export function UserActivityModal({ user, onClose }) {
  const [tab, setTab] = useState('all')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const { loading, data, error } = useActivity(user.id, dateFrom, dateTo)

  // One timeline, newest first. The date filter narrows violations here and
  // visits on the server, so both read the same days.
  const items = useMemo(() => {
    if (!data) return []
    const inRange = (iso) => {
      const day = iso ? format(new Date(iso), 'yyyy-MM-dd') : ''
      return (!dateFrom || day >= dateFrom) && (!dateTo || day <= dateTo)
    }
    const visits = data.visits.map((v) => ({ kind: 'visit', at: v.entered_at || v.exited_at, v }))
    const violations = data.violations.filter((v) => inRange(v.issued_at))
      .map((v) => ({ kind: 'violation', at: v.issued_at, v }))
    const chosen = tab === 'visits' ? visits : tab === 'violations' ? violations : [...visits, ...violations]
    return chosen.sort((a, b) => (b.at || '').localeCompare(a.at || ''))
  }, [data, tab, dateFrom, dateTo])

  return (
    <div className="um-modal-overlay" onClick={onClose}>
      <div className="um-modal ua-modal" onClick={(e) => e.stopPropagation()}>
        <div className="um-modal-header">
          <h2>Activity: {user.full_name}</h2>
          <button className="um-modal-close" onClick={onClose}><X size={18} /></button>
        </div>
        <div className="um-modal-body">
          {data?.schedule && <ScheduleBlock schedule={data.schedule} />}
          <div className="ua-toolbar">
            <div className="ua-tabs" role="tablist">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  role="tab"
                  aria-selected={tab === t.id}
                  className={`ua-tab ${tab === t.id ? 'ua-tab--active' : ''}`}
                  onClick={() => setTab(t.id)}
                >
                  {t.label}
                  {data && t.id === 'visits' && <span className="ua-count">{data.counts.visits}</span>}
                  {data && t.id === 'violations' && <span className="ua-count">{data.counts.violations}</span>}
                </button>
              ))}
            </div>
            <div className="ua-dates">
              <label>From <input type="date" value={dateFrom} max={dateTo || undefined}
                onChange={(e) => setDateFrom(e.target.value)} /></label>
              <label>To <input type="date" value={dateTo} min={dateFrom || undefined}
                onChange={(e) => setDateTo(e.target.value)} /></label>
            </div>
          </div>

          {loading ? (
            <div className="ua-empty"><Loader2 size={20} className="ua-spin" /></div>
          ) : error ? (
            <div className="ua-empty">The activity could not be loaded.</div>
          ) : items.length === 0 ? (
            <div className="ua-empty">Nothing recorded{dateFrom || dateTo ? ' in these dates' : ''}.</div>
          ) : (
            <ol className="ua-timeline">
              {items.map((item) => item.kind === 'visit' ? (
                <li key={`v${item.v.id}`} className="ua-item">
                  <span className={`ua-icon ${item.v.status === 'authorized' || item.v.status === 'exited' ? 'ua-icon--in' : 'ua-icon--denied'}`}>
                    {item.v.status === 'authorized' || item.v.status === 'exited' ? <LogIn size={14} /> : <Ban size={14} />}
                  </span>
                  <div className="ua-item-body">
                    <div className="ua-item-title">
                      {item.v.status === 'authorized' || item.v.status === 'exited'
                        ? <>Entered {item.v.gate && <>at {item.v.gate}</>}</>
                        : <>{item.v.status_label} {item.v.gate && <>at {item.v.gate}</>}</>}
                      <span className="ua-plate">{item.v.plate}</span>
                    </div>
                    <div className="ua-muted">
                      {when(item.v.entered_at)}
                      {item.v.exited_at && (
                        <> · <LogOut size={12} /> exited {format(new Date(item.v.exited_at), 'h:mm a')}
                          {item.v.exit_gate && item.v.exit_gate !== item.v.gate && <> at {item.v.exit_gate}</>}
                          {item.v.duration_minutes != null && <> · {minutesText(item.v.duration_minutes)}</>}</>
                      )}
                      {item.v.still_inside && <> · <strong>still inside</strong></>}
                      {item.v.no_exit && <> · no exit recorded</>}
                      {item.v.denied_reason && <> · {item.v.denied_reason}</>}
                    </div>
                  </div>
                </li>
              ) : (
                <li key={`x${item.v.id}`} className="ua-item">
                  <span className="ua-icon ua-icon--violation"><AlertTriangle size={14} /></span>
                  <div className="ua-item-body">
                    <div className="ua-item-title">
                      {item.v.label}
                      {item.v.plate && <span className="ua-plate">{item.v.plate}</span>}
                    </div>
                    <div className="ua-muted">
                      {when(item.v.issued_at)} · {item.v.settled ? item.v.status_label
                        : (item.v.offense_number ? `Offense ${item.v.offense_number}, active` : 'Active')}
                    </div>
                  </div>
                </li>
              ))}
            </ol>
          )}
          {data?.counts.visits_truncated && (
            <p className="ua-muted">Only the latest gate records are shown. Narrow the dates to see older ones.</p>
          )}
        </div>
        <div className="um-modal-footer">
          <button className="um-btn-secondary" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  )
}
