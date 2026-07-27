import { useState, useEffect, useCallback, useRef } from 'react'
import { useLiveUpdates } from '../../realtime/useLiveUpdates'
import { usersApi } from '../../api/users'
import {
  Search, ClipboardList, Filter,
  RefreshCw, ChevronLeft, ChevronRight, Download, X, FileText
} from 'lucide-react'
import { reportFileName } from '../../utils/reportName'
import './AuditLog.css'

const ACTION_LABELS = {
  scan:            'Vehicle Scanned',
  vehicle_entered: 'Vehicle Entered',
  vehicle_exited:  'Vehicle Exited',
  entry_override:  'Entry Override',
  visitor_issued:  'Visitor Pass Issued',
  visitor_exited:  'Visitor Exited',
  created:         'Record Created',
  updated:         'Record Updated',
  deleted:         'Record Deleted',
  user_created:    'User Created',
  user_updated:    'User Updated',
  user_deleted:    'User Deleted',
  user_disabled:   'User Disabled',
  user_enabled:    'User Enabled',
  admin_replaced:  'Admin Replaced',
  device_created:  'Device Added',
  device_updated:  'Device Updated',
  device_deleted:  'Device Removed',
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

// Local calendar date (YYYY-MM-DD). Using toISOString() here would shift to UTC
// and miss today's rows, since the server filters created_at by its Asia/Manila date.
function toDateStr(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export default function AuditLog() {
  const [logs, setLogs]             = useState([])
  const [loading, setLoading]       = useState(true)
  const [search, setSearch]         = useState('')
  const [actionFilter, setAction]   = useState('')
  const [dateFrom, setDateFrom]     = useState('')
  const [dateTo, setDateTo]         = useState('')
  const [datePeriod, setDatePeriod] = useState('all')
  const [page, setPage]             = useState(1)
  const [totalPages, setTotalPages] = useState(1)
  const [totalCount, setTotalCount] = useState(0)
  const searchTimer = useRef(null)

  const hasFilters = search || actionFilter || datePeriod !== 'all'

  const fetchLogs = useCallback(async (currentPage, currentSearch) => {
    setLoading(true)
    try {
      const params = { page: currentPage }
      if (actionFilter) params.action   = actionFilter
      if (dateFrom)     params.date_from = dateFrom
      if (dateTo)       params.date_to   = dateTo
      if (currentSearch) params.search   = currentSearch

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
  }, [actionFilter, dateFrom, dateTo])

  // Debounce search: wait 400ms after the user stops typing
  useEffect(() => {
    clearTimeout(searchTimer.current)
    searchTimer.current = setTimeout(() => {
      setPage(1)
      fetchLogs(1, search)
    }, 400)
    return () => clearTimeout(searchTimer.current)
  }, [search, fetchLogs])

  // Immediate reload when filters change
  useEffect(() => {
    setPage(1)
    fetchLogs(1, search)
  }, [actionFilter, dateFrom, dateTo]) // eslint-disable-line react-hooks/exhaustive-deps

  // Reload when page changes
  useEffect(() => {
    fetchLogs(page, search)
  }, [page]) // eslint-disable-line react-hooks/exhaustive-deps

  // New audit entries appear instantly (keeps current page/search)
  useLiveUpdates(() => fetchLogs(page, search), 'auditlog')

  const applyPeriod = (period) => {
    setDatePeriod(period)
    if (period === 'all') {
      setDateFrom(''); setDateTo('')
    } else {
      setDateFrom(toDateStr(getPeriodStart(period)))
      setDateTo(toDateStr(new Date()))
    }
  }

  const [exporting, setExporting]       = useState(false)
  const [exportingPdf, setExportingPdf] = useState(false)

  const today = toDateStr(new Date())

  const buildExportParams = () => {
    const params = {}
    if (actionFilter) params.action    = actionFilter
    if (dateFrom)     params.date_from = dateFrom
    if (dateTo)       params.date_to   = dateTo
    if (search)       params.search    = search
    return params
  }

  const downloadBlob = (blob, ext) => {
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = reportFileName('Audit Log Report', ext)
    a.click()
    URL.revokeObjectURL(url)
  }

  // Server-generated Excel report of ALL rows matching the current filters
  const exportExcel = async () => {
    setExporting(true)
    try {
      const blob = await usersApi.exportAuditLogsExcel(buildExportParams())
      downloadBlob(blob, 'xlsx')
    } catch {
      // download failed silently — surface nothing destructive
    } finally {
      setExporting(false)
    }
  }

  // Server-generated branded PDF report of ALL rows matching the current filters
  const exportPdf = async () => {
    setExportingPdf(true)
    try {
      const blob = await usersApi.exportAuditLogsPdf(buildExportParams())
      downloadBlob(blob, 'pdf')
    } catch {
      // download failed silently
    } finally {
      setExportingPdf(false)
    }
  }

  const formatDate = (iso) => {
    if (!iso) return '—'
    return new Date(iso).toLocaleString('en-US', {
      year: 'numeric', month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit'
    })
  }

  const actionBadgeClass = (action) => {
    const map = {
      user_created: 'created', user_updated: 'updated', user_deleted: 'deleted',
      user_disabled: 'disabled', user_enabled: 'enabled', admin_replaced: 'replaced',
      created: 'created', updated: 'updated', deleted: 'deleted',
      scan: 'scan', vehicle_entered: 'enabled', vehicle_exited: 'updated',
      entry_override: 'replaced', visitor_issued: 'scan', visitor_exited: 'updated',
      device_created: 'device-created', device_updated: 'device-updated', device_deleted: 'device-deleted',
    }
    return map[action] || ''
  }

  return (
    <>
      <div className="al-page">
        <div className="al-header">
          <div>
            <h1 className="al-title">Audit Log</h1>
            <p className="al-subtitle">Track all user management actions and vehicle scans.</p>
          </div>
          <div className="al-header-actions">
            <div className="al-stats-badge">
              <ClipboardList size={16} />
              <span>{totalCount} events</span>
            </div>
            <button className="al-export-btn" onClick={exportPdf} disabled={exportingPdf || totalCount === 0} title="Download all filtered entries as a branded PDF report">
              <FileText size={14} />
              <span>{exportingPdf ? 'Exporting…' : 'Export PDF'}</span>
            </button>
            <button className="al-export-btn" onClick={exportExcel} disabled={exporting || totalCount === 0} title="Download all filtered entries as an Excel report">
              <Download size={14} />
              <span>{exporting ? 'Exporting…' : 'Export Excel'}</span>
            </button>
          </div>
        </div>

        <div className="al-toolbar">
          <div className="al-period-btns">
            {DATE_PERIODS.map(p => (
              <button
                key={p.value}
                className={`al-period-btn ${datePeriod === p.value ? 'active' : ''}`}
                onClick={() => applyPeriod(p.value)}
              >
                {p.label}
              </button>
            ))}
          </div>

          <div className="al-search-wrapper">
            <Search size={15} />
            <input
              className="al-search-input"
              type="text"
              placeholder="Search actor, plate, details…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            {search && (
              <button className="al-search-clear" onClick={() => setSearch('')}>
                <X size={12} />
              </button>
            )}
          </div>

          <span className="al-sep" />

          <div className="al-filter-item">
            <Filter size={13} />
            <select
              className="al-form-select"
              value={actionFilter}
              onChange={(e) => setAction(e.target.value)}
            >
              <option value="">All Actions</option>
              {Object.entries(ACTION_LABELS).map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
          </div>

          <button className="al-refresh-btn" onClick={() => fetchLogs(page, search)} title="Refresh">
            <RefreshCw size={14} />
          </button>
        </div>

        <div className="al-table-container">
          {loading ? (
            <div className="al-loading">
              <div className="al-spinner" />
              <p>Loading audit logs…</p>
            </div>
          ) : logs.length === 0 ? (
            <div className="al-empty">
              <ClipboardList size={48} />
              <h3>No audit logs found</h3>
              <p>{hasFilters ? 'No events match your current filters.' : 'No events recorded yet.'}</p>
            </div>
          ) : (
            <table className="al-table">
              <thead>
                <tr>
                  <th style={{ width: 165 }}>Time Stamp</th>
                  <th style={{ width: 170 }}>Actor</th>
                  <th style={{ width: 160 }}>Action</th>
                  <th>Details</th>
                </tr>
              </thead>
              <tbody>
                {logs.map((log) => (
                  <tr key={log.id}>
                    <td className="al-timestamp">{formatDate(log.created_at)}</td>
                    <td>{log.actor_name || 'System'}</td>
                    <td>
                      <span className={`al-action-badge ${actionBadgeClass(log.action)}`}>
                        {log.exited_at ? 'Vehicle Entered → Exited' : (ACTION_LABELS[log.action] || log.action)}
                      </span>
                    </td>
                    <td className="al-details">
                      <span className="al-details-text">{log.details || '—'}</span>
                      {log.exited_at && (
                        <span className="al-details-text" style={{ display: 'block', color: '#059669', marginTop: 2 }}>
                          Exited {formatDate(log.exited_at)} · Duration: {log.duration_minutes} min
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {!loading && totalCount > 0 && (
          <div className="al-pagination">
            <span className="al-pagination-info">
              Showing {(page - 1) * 10 + 1}–{Math.min(page * 10, totalCount)} of {totalCount} entries
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
                disabled={page >= totalPages}
                onClick={() => setPage(p => p + 1)}
              >
                <ChevronRight size={16} />
              </button>
            </div>
          </div>
        )}
      </div>
    </>
  )
}
