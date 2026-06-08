import { useState, useEffect, useCallback } from 'react'
import AdminLayout from '../../components/Layout/AdminLayout'
import { usersApi } from '../../api/users'
import { Search, ClipboardList, Calendar, Filter, RefreshCw, ChevronLeft, ChevronRight } from 'lucide-react'
import './AuditLog.css'

const ACTION_LABELS = {
  user_created: 'User Created',
  user_updated: 'User Updated',
  user_deleted: 'User Deleted',
  user_disabled: 'User Disabled',
  user_enabled: 'User Enabled',
  admin_replaced: 'Admin Replaced',
  scan: 'Vehicle Scanned',
}

export default function AuditLog() {
  const [logs, setLogs] = useState([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [actionFilter, setActionFilter] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
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
    if (action === 'user_created') return 'created'
    if (action === 'user_updated') return 'updated'
    if (action === 'user_deleted') return 'deleted'
    if (action === 'user_disabled') return 'disabled'
    if (action === 'user_enabled') return 'enabled'
    if (action === 'admin_replaced') return 'replaced'
    if (action === 'scan') return 'scan'
    return ''
  }

  return (
    <AdminLayout>
      <div className="al-page">
        <div className="al-header">
          <div>
            <h1 className="al-title">Audit Log</h1>
            <p className="al-subtitle">Track all user management actions and vehicle scans.</p>
          </div>
          <div className="al-stats-badge">
            <ClipboardList size={16} />
            <span>Total: {totalCount} events</span>
          </div>
        </div>

        <div className="al-toolbar">
          <div className="al-search-wrapper">
            <Search size={16} />
            <input
              className="al-search-input"
              type="text"
              placeholder="Search by actor name..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <div className="al-filter-group">
            <div className="al-filter-item">
              <Filter size={14} />
              <select
                className="al-form-select"
                value={actionFilter}
                onChange={(e) => setActionFilter(e.target.value)}
              >
                <option value="">All Actions</option>
                <option value="user_created">User Created</option>
                <option value="user_updated">User Updated</option>
                <option value="user_deleted">User Deleted</option>
                <option value="user_disabled">User Disabled</option>
                <option value="user_enabled">User Enabled</option>
                <option value="admin_replaced">Admin Replaced</option>
                <option value="scan">Vehicle Scanned</option>
              </select>
            </div>
            <div className="al-filter-item">
              <Calendar size={14} />
              <input
                className="al-date-input"
                type="date"
                value={dateFrom}
                onChange={(e) => setDateFrom(e.target.value)}
                placeholder="From"
              />
            </div>
            <div className="al-filter-item">
              <Calendar size={14} />
              <input
                className="al-date-input"
                type="date"
                value={dateTo}
                onChange={(e) => setDateTo(e.target.value)}
                placeholder="To"
              />
            </div>
            <button className="al-refresh-btn" onClick={fetchLogs} title="Refresh">
              <RefreshCw size={14} />
            </button>
          </div>
        </div>

        <div className="al-table-container">
          {loading ? (
            <div className="al-loading">
              <div className="al-spinner" />
              <p>Loading audit logs...</p>
            </div>
          ) : logs.length === 0 ? (
            <div className="al-empty">
              <ClipboardList size={48} />
              <h3>No audit logs found</h3>
              <p>No events match your current filters.</p>
            </div>
          ) : (
            <>
              <table className="al-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Timestamp</th>
                    <th>Actor</th>
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
                      <td>{log.actor_name || '—'}</td>
                      <td>
                        <span className={`al-action-badge ${actionBadgeClass(log.action)}`}>
                          {ACTION_LABELS[log.action] || log.action}
                        </span>
                      </td>
                      <td>{log.target_name || '—'}</td>
                      <td className="al-details">{log.details || '—'}</td>
                      <td>{log.ip_address || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </div>

        {!loading && logs.length > 0 && totalPages > 1 && (
          <div className="al-pagination">
            <span className="al-pagination-info">
              Showing {(page - 1) * 10 + 1} to {Math.min(page * 10, totalCount)} of {totalCount} entries
            </span>
            <div className="al-pagination-controls">
              <button
                className="al-page-btn"
                disabled={page === 1}
                onClick={() => setPage(p => p - 1)}
              >
                <ChevronLeft size={16} />
              </button>
              <span className="al-page-current">Page {page} of {totalPages}</span>
              <button
                className="al-page-btn"
                disabled={page === totalPages}
                onClick={() => setPage(p => p + 1)}
              >
                <ChevronRight size={16} />
              </button>
            </div>
          </div>
        )}
      </div>
    </AdminLayout>
  )
}