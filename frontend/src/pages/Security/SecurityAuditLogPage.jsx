import { useState, useEffect, useCallback } from 'react'
import { useLiveUpdates } from '../../realtime/useLiveUpdates'
import {
  CheckCircle, XCircle, HelpCircle, AlertTriangle,
  ClipboardList, CalendarDays, RefreshCw, Filter,
} from 'lucide-react'
import { formatDistanceToNow } from 'date-fns'
import SecurityLayout from '../../components/Layout/SecurityLayout'
import { getAccessLogs } from '../../api/scanning'
import useAuthStore from '../../stores/authStore'
import './SecurityAuditLogPage.css'

const STATUS_META = {
  authorized: { label: 'Authorized',        Icon: CheckCircle,   cls: 'authorized' },
  open_entry: { label: 'Open Entry',        Icon: CheckCircle,   cls: 'authorized' },
  wrong_day:  { label: 'Wrong Day',         Icon: XCircle,       cls: 'denied'     },
  denied:     { label: 'Denied',            Icon: XCircle,       cls: 'denied'     },
  unknown:    { label: 'Visitor',           Icon: HelpCircle,    cls: 'visitor'    },
  no_pass:    { label: 'No Pass',           Icon: AlertTriangle, cls: 'visitor'    },
  disabled:   { label: 'Disabled',          Icon: XCircle,       cls: 'denied'     },
  unreadable: { label: 'Unreadable',        Icon: AlertTriangle, cls: 'visitor'    },
  exited:     { label: 'Exited',            Icon: CheckCircle,   cls: 'exited'     },
}

const FILTERS = [
  { key: '',           label: 'All'        },
  { key: 'authorized', label: 'Authorized' },
  { key: 'denied',     label: 'Denied'     },
  { key: 'wrong_day',  label: 'Wrong Day'  },
  { key: 'unknown',    label: 'Visitor'    },
  { key: 'exited',     label: 'Exited'     },
]

function getMeta(status) {
  return STATUS_META[status] ?? { label: status || '—', Icon: HelpCircle, cls: '' }
}

function timeAgo(ts) {
  try { return formatDistanceToNow(new Date(ts), { addSuffix: true }) }
  catch { return '' }
}

function fmtTime(ts) {
  if (!ts) return '—'
  return new Date(ts).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: true })
}

// Local (browser/Manila) calendar date as YYYY-MM-DD. Using the UTC date here
// hides today's scans during early-morning hours because the server filters
// scanned_at by its own (Asia/Manila) date, not UTC.
function localDateStr(d = new Date()) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export default function SecurityAuditLogPage() {
  const { user } = useAuthStore()
  const today = localDateStr()
  const gateLabel = { gate1: 'Gate 1', gate4: 'Gate 4' }[user?.gate_assignment] || 'Gate'

  const [logs, setLogs]           = useState([])
  const [loading, setLoading]     = useState(true)
  const [date, setDate]           = useState(today)
  const [statusFilter, setStatus] = useState('')

  const fetchLogs = useCallback(async () => {
    setLoading(true)
    try {
      const params = {
        limit: 100,
        ...(user?.gate_assignment ? { gate_id: user.gate_assignment } : {}),
        ...(date ? { date } : {}),
      }
      const res = await getAccessLogs(params)
      setLogs(res.data?.results ?? res.data ?? [])
    } catch {
      setLogs([])
    } finally {
      setLoading(false)
    }
  }, [date, user?.gate_assignment])

  useEffect(() => { fetchLogs() }, [fetchLogs])

  // New gate scans appear instantly
  useLiveUpdates(fetchLogs, ['accesslog'])

  const filtered = statusFilter
    ? logs.filter(l => {
        if (statusFilter === 'denied') return ['denied', 'wrong_day', 'disabled'].includes(l.status)
        if (statusFilter === 'unknown') return ['unknown', 'no_pass'].includes(l.status)
        return l.status === statusFilter
      })
    : logs

  return (
    <SecurityLayout>
      <div className="sal-page">

        {/* Header */}
        <div className="sal-header">
          <div>
            <h1 className="sal-title"><ClipboardList size={20} /> Vehicle Log</h1>
            <p className="sal-sub">All scans recorded at {gateLabel}</p>
          </div>
          <div className="sal-header-actions">
            <div className="sal-date-wrap">
              <CalendarDays size={14} />
              <input
                type="date"
                className="sal-date-input"
                value={date}
                max={today}
                onChange={e => setDate(e.target.value)}
              />
            </div>
            <button className="sal-refresh-btn" onClick={fetchLogs} disabled={loading} title="Refresh">
              <RefreshCw size={14} className={loading ? 'sal-spin' : ''} />
            </button>
          </div>
        </div>

        {/* Filter pills */}
        <div className="sal-filters">
          <Filter size={13} style={{ color: '#9ca3af', flexShrink: 0 }} />
          {FILTERS.map(f => (
            <button
              key={f.key}
              className={`sal-filter-pill${statusFilter === f.key ? ' active' : ''}`}
              onClick={() => setStatus(f.key)}
            >
              {f.label}
            </button>
          ))}
        </div>

        {/* Log table */}
        <div className="sal-card">
          <div className="sal-card-head">
            <span className="sal-card-label">
              {date === today ? "Today's entries" : `Entries on ${date}`}
            </span>
            <span className="sal-count">{filtered.length}</span>
          </div>

          {loading ? (
            <div className="sal-loading">
              <div className="sal-spinner" />
              <p>Loading logs…</p>
            </div>
          ) : filtered.length === 0 ? (
            <div className="sal-empty">
              <ClipboardList size={28} style={{ color: '#d1d5db' }} />
              <p>No entries found{statusFilter ? ` for "${FILTERS.find(f=>f.key===statusFilter)?.label}"` : ''} on this date.</p>
            </div>
          ) : (
            <div className="sal-list">
              {filtered.map((log, i) => {
                const { Icon, label, cls } = getMeta(log.status)
                return (
                  <div key={log.id ?? i} className={`sal-row ${cls}`}>
                    <div className={`sal-row-icon ${cls}`}>
                      <Icon size={14} />
                    </div>
                    <div className="sal-row-info">
                      <div className="sal-row-top">
                        <span className="sal-plate">{log.plate_number || '—'}</span>
                        <span className={`sal-badge ${cls}`}>{label}</span>
                      </div>
                      {(log.vehicle_owner_name || log.scanned_by_name || log.on_duty_guard_name) && (
                        <div className="sal-row-sub">
                          {log.vehicle_owner_name && <span>Owner: {log.vehicle_owner_name}</span>}
                          {log.on_duty_guard_name && <span>· On duty: {log.on_duty_guard_name}</span>}
                          {log.scanned_by_name && log.scanned_by_name !== log.on_duty_guard_name && (
                            <span>· Scanned by: {log.scanned_by_name}</span>
                          )}
                        </div>
                      )}
                    </div>
                    <div className="sal-row-time">
                      <span className="sal-time-abs">{fmtTime(log.scanned_at)}</span>
                      <span className="sal-time-rel">{timeAgo(log.scanned_at)}</span>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </SecurityLayout>
  )
}
