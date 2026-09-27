import { useState, useEffect, useCallback } from 'react'
import { useLiveUpdates } from '../../realtime/useLiveUpdates'
import {
  CheckCircle, XCircle, HelpCircle, AlertTriangle,
  ClipboardList, CalendarDays, RefreshCw, Filter, LogIn, LogOut, CalendarClock,
} from 'lucide-react'
import { formatDistanceToNow } from 'date-fns'
import { getAccessLogs } from '../../api/scanning'
import { displayStatus } from '../../utils/logStatus'
import useAuthStore from '../../stores/authStore'
import { useGates } from '../../hooks/useGates'
import './SecurityAuditLogPage.css'

const STATUS_META = {
  authorized: { label: 'Authorized',        Icon: CheckCircle,   cls: 'authorized' },
  open_entry: { label: 'Open Entry',        Icon: CheckCircle,   cls: 'authorized' },
  scheduled_entry: { label: 'Scheduled Entry', Icon: CalendarClock, cls: 'authorized' },
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

// Which gate a visit came in by and which it left by. A merged row is the
// entry carrying its exit; a lone exit row (its entry was on another day) is
// the exit carrying its entry. A refused or unreadable scan has only the gate
// it happened at, so it returns null and the row says "At <gate>".
function visitGates(log) {
  if (log.status === 'exited') {
    return { inGate: log.entry_gate_id, inAt: log.entered_at, outGate: log.gate_id, outAt: log.scanned_at }
  }
  if (log.status === 'authorized' || log.status === 'open_entry') {
    return { inGate: log.gate_id, inAt: log.scanned_at, outGate: log.exit_gate_id, outAt: log.exited_at }
  }
  return null
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
  const { gateLabel: labelFor } = useGates()
  const gateLabel = labelFor(user?.gate_assignment) || 'Gate'

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
        // A visit's exit is folded into its entry row, so "Exited" means any
        // visit that has left — not only a lone exit row.
        if (statusFilter === 'exited') return l.status === 'exited' || !!l.exited_at
        return l.status === statusFilter
      })
    : logs

  return (
    <>
      <div className="sal-page">

        {/* Header */}
        <div className="sal-header">
          <div>
            <h1 className="sal-title"><ClipboardList size={20} /> Vehicle Log</h1>
            <p className="sal-sub">Every scan at {gateLabel}, and each visit's entry and exit gate</p>
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
          <Filter size={13} style={{ color: '#64839C', flexShrink: 0 }} />
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
              <ClipboardList size={28} style={{ color: '#BDD4E5' }} />
              <p>No entries found{statusFilter ? ` for "${FILTERS.find(f=>f.key===statusFilter)?.label}"` : ''} on this date.</p>
            </div>
          ) : (
            <div className="sal-list">
              {filtered.map((log, i) => {
                const { Icon, label, cls } = getMeta(displayStatus(log))
                const gates = visitGates(log)
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
                      <div className="sal-gates">
                        {gates ? (
                          <>
                            <span className="sal-gate in">
                              <LogIn size={11} /> In · {labelFor(gates.inGate) || 'Earlier visit'}
                              {gates.inAt && ` · ${fmtTime(gates.inAt)}`}
                            </span>
                            {gates.outGate ? (
                              <span className="sal-gate out">
                                <LogOut size={11} /> Out · {labelFor(gates.outGate)} · {fmtTime(gates.outAt)}
                                {log.duration_minutes != null && ` · ${log.duration_minutes} min`}
                              </span>
                            ) : (
                              <span className="sal-gate inside">Still inside</span>
                            )}
                          </>
                        ) : (
                          <span className="sal-gate">At {labelFor(log.gate_id) || log.gate_id || '—'}</span>
                        )}
                      </div>
                      {/* Joined rather than each part carrying its own "· ",
                          which left a stray dot in front of "On duty" on any
                          row with no owner (visitors, unregistered plates). */}
                      {(() => {
                        const who = [
                          log.scheduled_visit_ref && `Booked: ${[log.scheduled_visit_ref, log.scheduled_visit_name].filter(Boolean).join(' ')}`,
                          log.vehicle_owner_name && `Owner: ${log.vehicle_owner_name}`,
                          log.on_duty_guard_name && `On duty: ${log.on_duty_guard_name}`,
                          log.scanned_by_name && log.scanned_by_name !== log.on_duty_guard_name
                            && `Scanned by: ${log.scanned_by_name}`,
                        ].filter(Boolean)
                        return who.length > 0 && <div className="sal-row-sub">{who.join(' · ')}</div>
                      })()}
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
    </>
  )
}
