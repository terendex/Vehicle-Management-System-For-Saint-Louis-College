import { useState, useEffect, useCallback } from 'react'
import SecurityLayout from '../../components/Layout/SecurityLayout'
import { usersApi } from '../../api/users'
import { ClipboardList, Calendar, Filter, RefreshCw, ChevronLeft, ChevronRight } from 'lucide-react'
import './SecurityAuditLog.css'

const ACTION_LABELS = {
  user_created:    'User Created',
  user_updated:    'User Updated',
  user_deleted:    'User Deleted',
  user_disabled:   'User Disabled',
  user_enabled:    'User Enabled',
  admin_replaced:  'Admin Replaced',
  scan:            'Vehicle Scanned',
  vehicle_entered: 'Vehicle Entered',
  vehicle_exited:  'Vehicle Exited',
  visitor_issued:  'Visitor Pass Issued',
  visitor_exited:  'Visitor Exited',
  entry_override:  'Entry Override',
}

const DATE_PERIODS = [
  { value: 'all',   label: 'All' },
  { value: 'day',   label: 'Today' },
  { value: 'week',  label: 'Week' },
  { value: 'month', label: 'Month' },
  { value: 'year',  label: 'Year' },
]

function getPeriodStart(period) {
  const d = new Date()
  if (period === 'day') { d.setHours(0, 0, 0, 0) }
  else if (period === 'week') {
    const dow = d.getDay()
    d.setDate(d.getDate() - (dow === 0 ? 6 : dow - 1))
    d.setHours(0, 0, 0, 0)
  } else if (period === 'month') { d.setDate(1); d.setHours(0, 0, 0, 0) }
  else if (period === 'year')  { d.setMonth(0, 1); d.setHours(0, 0, 0, 0) }
  return d
}

function toDateStr(d) { return d.toISOString().split('T')[0] }

export default function SecurityAuditLog() {
  const [logs, setLogs] = useState([])
  const [loading, setLoading] = useState(true)
  const [actionFilter, setActionFilter] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [datePeriod, setDatePeriod] = useState('all')
  const [page, setPage] = useState(1)
  const [totalPages, setTotalPages] = useState(1)
  const [totalCount, setTotalCount] = useState(0)

  const fetchLogs = useCallback(async () => {
    setLoading(true)
    try {
      const params = { page }
      if (actionFilter) params.action = actionFilter
      if (dateFrom) params.date_from = dateFrom
      if (dateTo) params.date_to = dateTo
      const data = await usersApi.getAuditLogs(params)
      if (data && data.results) {
        setLogs(data.results)
        setTotalCount(data.count)
        setTotalPages(Math.ceil(data.count / 10))
      } else {
        setLogs(data || [])
        setTotalCount(data ? data.length : 0)
        setTotalPages(1)
      }
    } catch {
      setLogs([])
    } finally {
      setLoading(false)
    }
  }, [page, actionFilter, dateFrom, dateTo])

  useEffect(() => {
    setPage(1)
  }, [actionFilter, dateFrom, dateTo])

  useEffect(() => {
    fetchLogs()
  }, [fetchLogs])

  const applyPeriod = (period) => {
    setDatePeriod(period)
    if (period === 'all') {
      setDateFrom(''); setDateTo('')
    } else {
      setDateFrom(toDateStr(getPeriodStart(period)))
      setDateTo(toDateStr(new Date()))
    }
  }

  const formatDate = (iso) => {
    if (!iso) return '—'
    return new Date(iso).toLocaleString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    })
  }

  const actionBadgeClass = (action) => {
    if (action === 'user_created')    return 'created'
    if (action === 'user_updated')    return 'updated'
    if (action === 'user_deleted')    return 'deleted'
    if (action === 'user_disabled')   return 'disabled'
    if (action === 'user_enabled')    return 'enabled'
    if (action === 'admin_replaced')  return 'replaced'
    if (action === 'scan')            return 'scan'
    if (action === 'vehicle_entered') return 'scan'
    if (action === 'vehicle_exited')  return 'scan'
    if (action === 'visitor_issued')  return 'created'
    if (action === 'visitor_exited')  return 'scan'
    if (action === 'entry_override')  return 'updated'
    return ''
  }

  return (
    <SecurityLayout>
      <div className="sal-page">
        <div className="sal-header">
          <div>
            <h1 className="sal-title">My Activity Log</h1>
            <p className="sal-subtitle">View all your recent actions and vehicle scan history.</p>
          </div>
          <div className="sal-stats-badge">
            <ClipboardList size={16} />
            <span>Total: {totalCount} events</span>
          </div>
        </div>

        <div className="sal-toolbar">
          <div className="sal-period-btns">
            {DATE_PERIODS.map(p => (
              <button
                key={p.value}
                className={`sal-period-btn ${datePeriod === p.value ? 'active' : ''}`}
                onClick={() => applyPeriod(p.value)}
              >
                {p.label}
              </button>
            ))}
          </div>
          <div className="sal-filter-group">
            <div className="sal-filter-item">
              <Filter size={14} />
              <select
                className="sal-form-select"
                value={actionFilter}
                onChange={(e) => setActionFilter(e.target.value)}
              >
                <option value="">All Actions</option>
                <option value="vehicle_entered">Vehicle Entered</option>
                <option value="vehicle_exited">Vehicle Exited</option>
                <option value="visitor_issued">Visitor Pass Issued</option>
                <option value="visitor_exited">Visitor Exited</option>
                <option value="entry_override">Entry Override</option>
                <option value="scan">Vehicle Scanned (denied)</option>
                <option value="user_created">User Created</option>
                <option value="user_updated">User Updated</option>
                <option value="user_deleted">User Deleted</option>
                <option value="user_disabled">User Disabled</option>
                <option value="user_enabled">User Enabled</option>
                <option value="admin_replaced">Admin Replaced</option>
              </select>
            </div>
            <div className="sal-filter-item">
              <Calendar size={14} />
              <input
                className="sal-date-input"
                type="date"
                value={dateFrom}
                onChange={(e) => { setDateFrom(e.target.value); setDatePeriod('') }}
                placeholder="From"
              />
            </div>
            <div className="sal-filter-item">
              <Calendar size={14} />
              <input
                className="sal-date-input"
                type="date"
                value={dateTo}
                onChange={(e) => { setDateTo(e.target.value); setDatePeriod('') }}
                placeholder="To"
              />
            </div>
            <button className="sal-refresh-btn" onClick={fetchLogs} title="Refresh">
              <RefreshCw size={14} />
            </button>
          </div>
        </div>

        <div className="sal-table-container">
          {loading ? (
            <div className="sal-loading">
              <div className="sal-spinner" />
              <p>Loading your activity...</p>
            </div>
          ) : logs.length === 0 ? (
            <div className="sal-empty">
              <ClipboardList size={48} />
              <h3>No activity recorded</h3>
              <p>Your action history will appear here.</p>
            </div>
          ) : (
            <>
              <table className="sal-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Timestamp</th>
                    <th>Action</th>
                    <th>Target</th>
                    <th>Details</th>
                    <th>IP Address</th>
                  </tr>
                </thead>
                <tbody>
                  {logs.map((log, idx) => (
                    <tr key={log.id}>
                      <td>{(page - 1) * 10 + idx + 1}</td>
                      <td>{formatDate(log.created_at)}</td>
                      <td>
                        <span className={`sal-action-badge ${actionBadgeClass(log.action)}`}>
                          {ACTION_LABELS[log.action] || log.action}
                        </span>
                      </td>
                      <td>{log.target_name || '—'}</td>
                      <td className="sal-details">{log.details || '—'}</td>
                      <td>{log.ip_address || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </div>

        {!loading && logs.length > 0 && totalPages > 1 && (
          <div className="sal-pagination">
            <span className="sal-pagination-info">
              Showing {(page - 1) * 10 + 1} to {Math.min(page * 10, totalCount)} of {totalCount} entries
            </span>
            <div className="sal-pagination-controls">
              <button
                className="sal-page-btn"
                disabled={page === 1}
                onClick={() => setPage(p => p - 1)}
              >
                <ChevronLeft size={16} />
              </button>
              <span className="sal-page-current">Page {page} of {totalPages}</span>
              <button
                className="sal-page-btn"
                disabled={page === totalPages}
                onClick={() => setPage(p => p + 1)}
              >
                <ChevronRight size={16} />
              </button>
            </div>
          </div>
        )}
      </div>
    </SecurityLayout>
  )
}
