import { useState, useEffect, useCallback, useRef } from 'react'
import { useLiveUpdates } from '../../realtime/useLiveUpdates'
import { getAccessLogs, exportVehicleLogExcel, exportVehicleLogPdf } from '../../api/scanning'
import { useGates } from '../../hooks/useGates'
import { reportFileName } from '../../utils/reportName'
import { notify } from '../../components/Feedback/notify'
import {
  Search, Car, Filter, RefreshCw, ChevronLeft, ChevronRight,
  X, Calendar, DoorOpen, CheckCircle, XCircle, HelpCircle, AlertTriangle,
  Download, FileText,
} from 'lucide-react'
import './VehicleLog.css'

// Gate movement, not account administration — the Audit Log next door records
// who changed what; this records which vehicle passed which gate, across every
// gate at once (a guard's own Vehicle Log is scoped to the gate they man).

// Keys are AccessLog.Status on the backend. 'open_entry' / 'no_pass' are
// gate-screen labels only — they are stored as authorized / denied — but they
// are kept here so a row is never left unlabelled if one is ever persisted.
const STATUS_META = {
  authorized: { label: 'Authorized', Icon: CheckCircle,   cls: 'authorized' },
  open_entry: { label: 'Open Entry', Icon: CheckCircle,   cls: 'authorized' },
  exited:     { label: 'Exited',     Icon: DoorOpen,      cls: 'exited'     },
  denied:     { label: 'Denied',     Icon: XCircle,       cls: 'denied'     },
  wrong_day:  { label: 'Wrong Day',  Icon: XCircle,       cls: 'denied'     },
  no_pass:    { label: 'No Pass',    Icon: AlertTriangle, cls: 'visitor'    },
  unknown:    { label: 'Visitor',    Icon: HelpCircle,    cls: 'visitor'    },
  unreadable: { label: 'Unreadable', Icon: AlertTriangle, cls: 'visitor'    },
}

// Grouped the way an admin asks the question ("show me everything refused"),
// so one choice can cover several stored statuses.
const STATUS_FILTERS = [
  { value: '',           label: 'All Statuses', match: null },
  { value: 'authorized', label: 'Authorized',   match: ['authorized', 'open_entry'] },
  { value: 'denied',     label: 'Denied',       match: ['denied', 'wrong_day'] },
  { value: 'unknown',    label: 'Visitor',      match: ['unknown', 'no_pass'] },
  { value: 'unreadable', label: 'Unreadable',   match: ['unreadable'] },
  { value: 'exited',     label: 'Exited',       match: ['exited'] },
]

const CLASSIFICATION_LABELS = {
  student: 'Student', employee: 'Employee', fetcher: 'Fetcher',
  visitor: 'Visitor', supplier: 'Supplier', unknown: 'Unregistered',
}

const DATE_PERIODS = [
  { value: 'all',   label: 'All' },
  { value: 'day',   label: 'Today' },
  { value: 'week',  label: 'Week' },
  { value: 'month', label: 'Month' },
  { value: 'year',  label: 'Year' },
]

const PAGE_SIZE = 15
const FETCH_LIMIT = 500   // server caps at 1000; 500 keeps the payload sane

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

// Local calendar date (YYYY-MM-DD). toISOString() would shift to UTC and drop
// today's rows, since the server filters scanned_at by its Asia/Manila date.
function toDateStr(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function fmtDateTime(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('en-US', {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

function fmtTime(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleTimeString('en-US', {
    hour: '2-digit', minute: '2-digit', hour12: true,
  })
}

function fmtDuration(minutes) {
  if (minutes == null) return ''
  if (minutes < 60) return `${minutes} min`
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return m ? `${h}h ${m}m` : `${h}h`
}

function getMeta(status) {
  return STATUS_META[status] ?? { label: status || '—', Icon: HelpCircle, cls: '' }
}

export default function VehicleLog() {
  const { gates, gateLabel } = useGates()

  // 'Today' by default: a gate log opened cold should show the current shift,
  // not the oldest slice of every scan ever recorded.
  const today = toDateStr(new Date())

  const [logs, setLogs]             = useState([])
  const [loading, setLoading]       = useState(true)
  const [search, setSearch]         = useState('')
  const [gateFilter, setGate]       = useState('')
  const [statusFilter, setStatus]   = useState('')
  const [dateFrom, setDateFrom]     = useState(today)
  const [dateTo, setDateTo]         = useState(today)
  const [datePeriod, setDatePeriod] = useState('day')
  const [page, setPage]             = useState(1)
  const searchTimer = useRef(null)

  const fetchLogs = useCallback(async (currentSearch) => {
    setLoading(true)
    try {
      const params = { limit: FETCH_LIMIT }
      if (gateFilter)    params.gate_id   = gateFilter
      if (dateFrom)      params.date_from = dateFrom
      if (dateTo)        params.date_to   = dateTo
      if (currentSearch) params.search    = currentSearch

      const res = await getAccessLogs(params)
      setLogs(res.data?.results ?? res.data ?? [])
    } catch {
      setLogs([])
    } finally {
      setLoading(false)
    }
  }, [gateFilter, dateFrom, dateTo])

  // Debounce search: wait 400ms after the user stops typing. Filter changes
  // rebuild fetchLogs, which re-runs this effect, so one timer covers both.
  useEffect(() => {
    clearTimeout(searchTimer.current)
    searchTimer.current = setTimeout(() => fetchLogs(search), 400)
    return () => clearTimeout(searchTimer.current)
  }, [search, fetchLogs])

  // New gate scans appear without a manual refresh
  useLiveUpdates(() => fetchLogs(search), ['accesslog'])

  const applyPeriod = (period) => {
    setDatePeriod(period)
    setPage(1)
    if (period === 'all') {
      setDateFrom(''); setDateTo('')
    } else {
      setDateFrom(toDateStr(getPeriodStart(period)))
      setDateTo(today)
    }
  }

  // Manual From/To — clears the active preset and keeps the pair ordered
  const onDateFromChange = (value) => {
    setDatePeriod(''); setPage(1)
    if (value && dateTo && value > dateTo) setDateTo(value)
    setDateFrom(value)
  }
  const onDateToChange = (value) => {
    setDatePeriod(''); setPage(1)
    if (value && dateFrom && value < dateFrom) setDateFrom(value)
    setDateTo(value)
  }

  // Status is filtered here rather than in the query: the server folds an exit
  // row into its entry row, which it can only do while both are in the result.
  const match = STATUS_FILTERS.find(f => f.value === statusFilter)?.match
  const filtered = match ? logs.filter(l => match.includes(l.status)) : logs

  const totalCount = filtered.length
  const totalPages = Math.max(1, Math.ceil(totalCount / PAGE_SIZE))
  const pageSafe   = Math.min(page, totalPages)
  const pageRows   = filtered.slice((pageSafe - 1) * PAGE_SIZE, pageSafe * PAGE_SIZE)

  const hasFilters = search || gateFilter || statusFilter || datePeriod !== 'all' || dateFrom || dateTo

  // ── Reports ───────────────────────────────────────────────────────────────
  // The server re-runs the same filters and merges entry/exit the same way, so
  // the file holds every row matching the screen — not just the page in view.
  const [exporting, setExporting]       = useState(false)
  const [exportingPdf, setExportingPdf] = useState(false)

  const buildExportParams = () => {
    const params = {}
    if (gateFilter)   params.gate_id   = gateFilter
    if (dateFrom)     params.date_from = dateFrom
    if (dateTo)       params.date_to   = dateTo
    if (search)       params.search    = search
    if (statusFilter) params.status    = statusFilter
    return params
  }

  const downloadBlob = (blob, ext) => {
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = reportFileName('Vehicle Log Report', ext)
    a.click()
    URL.revokeObjectURL(url)
  }

  const runExport = async (fn, ext, setBusy) => {
    setBusy(true)
    try {
      downloadBlob(await fn(buildExportParams()), ext)
    } catch {
      notify.error('The report could not be generated. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  const exportExcel = () => runExport(exportVehicleLogExcel, 'xlsx', setExporting)
  const exportPdf   = () => runExport(exportVehicleLogPdf,   'pdf',  setExportingPdf)

  const clearFilters = () => {
    setSearch(''); setGate(''); setStatus('')
    setDatePeriod('all'); setDateFrom(''); setDateTo(''); setPage(1)
  }

  return (
    <>
      <div className="vl-page">
        <div className="vl-header">
          <div>
            <h1 className="vl-title">Vehicle Log</h1>
            <p className="vl-subtitle">
              Every gate scan, entry and exit recorded by security, across all gates.
              Account and record changes are in the Audit Log.
            </p>
          </div>
          <div className="vl-header-actions">
            <div className="vl-stats-badge">
              <Car size={16} />
              <span>{totalCount} {totalCount === 1 ? 'record' : 'records'}</span>
            </div>
            <button
              className="vl-export-btn"
              onClick={exportPdf}
              disabled={exportingPdf || totalCount === 0}
              title="Download all filtered entries as a branded PDF report"
            >
              <FileText size={14} />
              <span>{exportingPdf ? 'Exporting…' : 'Export PDF'}</span>
            </button>
            <button
              className="vl-export-btn"
              onClick={exportExcel}
              disabled={exporting || totalCount === 0}
              title="Download all filtered entries as an Excel report"
            >
              <Download size={14} />
              <span>{exporting ? 'Exporting…' : 'Export Excel'}</span>
            </button>
          </div>
        </div>

        <div className="vl-toolbar">
          {/* Row 1 — date range: quick presets or a custom From/To */}
          <div className="vl-toolbar-row">
            <span className="vl-toolbar-label"><Calendar size={14} /> Date range</span>
            <div className="vl-period-btns">
              {DATE_PERIODS.map(p => (
                <button
                  key={p.value}
                  className={`vl-period-btn ${datePeriod === p.value ? 'active' : ''}`}
                  onClick={() => applyPeriod(p.value)}
                >
                  {p.label}
                </button>
              ))}
            </div>
            <span className="vl-daterange-or">or pick</span>
            <div className="vl-daterange">
              <input
                className="vl-date-input"
                type="date"
                value={dateFrom}
                max={dateTo || today}
                onChange={(e) => onDateFromChange(e.target.value)}
                title="Date From"
                aria-label="Date From"
              />
              <span className="vl-daterange-sep">to</span>
              <input
                className="vl-date-input"
                type="date"
                value={dateTo}
                min={dateFrom || undefined}
                max={today}
                onChange={(e) => onDateToChange(e.target.value)}
                title="Date To"
                aria-label="Date To"
              />
            </div>
          </div>

          {/* Row 2 — search + gate + status */}
          <div className="vl-toolbar-row">
            <div className="vl-search-wrapper">
              <Search size={15} />
              <input
                className="vl-search-input"
                type="text"
                placeholder="Search plate, owner or guard…"
                value={search}
                onChange={(e) => { setSearch(e.target.value); setPage(1) }}
              />
              {search && (
                <button className="vl-search-clear" onClick={() => setSearch('')} title="Clear search">
                  <X size={12} />
                </button>
              )}
            </div>

            <div className="vl-filter-item">
              <DoorOpen size={13} />
              <select
                className="vl-form-select"
                value={gateFilter}
                onChange={(e) => { setGate(e.target.value); setPage(1) }}
                aria-label="Gate"
              >
                <option value="">All Gates</option>
                {gates.map(g => (
                  <option key={g.gate_id} value={g.gate_id}>{g.label}</option>
                ))}
              </select>
            </div>

            <div className="vl-filter-item">
              <Filter size={13} />
              <select
                className="vl-form-select"
                value={statusFilter}
                onChange={(e) => { setStatus(e.target.value); setPage(1) }}
                aria-label="Status"
              >
                {STATUS_FILTERS.map(f => (
                  <option key={f.value} value={f.value}>{f.label}</option>
                ))}
              </select>
            </div>

            {hasFilters && (
              <button className="vl-clear-btn" onClick={clearFilters}>Clear filters</button>
            )}

            <button className="vl-refresh-btn" onClick={() => fetchLogs(search)} title="Refresh">
              <RefreshCw size={14} className={loading ? 'vl-spin' : ''} />
            </button>
          </div>
        </div>

        <div className="vl-table-container">
          {loading ? (
            <div className="vl-loading">
              <div className="vl-spinner" />
              <p>Loading vehicle logs…</p>
            </div>
          ) : pageRows.length === 0 ? (
            <div className="vl-empty">
              <Car size={48} />
              <h3>No vehicle logs found</h3>
              <p>{hasFilters ? 'No scans match your current filters.' : 'No gate scans recorded yet.'}</p>
            </div>
          ) : (
            <>
              <table className="vl-table">
                <thead>
                  <tr>
                    <th style={{ width: 160 }}>Time Stamp</th>
                    <th style={{ width: 115 }}>Plate</th>
                    <th>Owner</th>
                    <th style={{ width: 105 }}>Gate</th>
                    <th style={{ width: 145 }}>Status</th>
                    <th style={{ width: 150 }}>Guard on Duty</th>
                    <th style={{ width: 140 }}>Exit</th>
                  </tr>
                </thead>
                <tbody>
                  {pageRows.map((log, i) => {
                    const { Icon, label, cls } = getMeta(log.status)
                    return (
                      <tr key={log.id ?? i}>
                        <td className="vl-timestamp">{fmtDateTime(log.scanned_at)}</td>
                        <td className="vl-plate">{log.plate_number || '—'}</td>
                        <td>
                          <span className="vl-owner">{log.vehicle_owner_name || 'Unregistered'}</span>
                          {log.classification && (
                            <span className="vl-classification">
                              {CLASSIFICATION_LABELS[log.classification] || log.classification}
                              {log.vehicle_type_info ? ` · ${log.vehicle_type_info}` : ''}
                            </span>
                          )}
                        </td>
                        <td className="vl-gate">{gateLabel(log.gate_id) || '—'}</td>
                        <td>
                          <span className={`vl-status-badge ${cls}`}>
                            <Icon size={12} /> {label}
                          </span>
                          {log.is_override && (
                            <span className="vl-override" title={log.override_reason}>Override</span>
                          )}
                          {log.denied_reason && <span className="vl-reason">{log.denied_reason}</span>}
                        </td>
                        <td className="vl-guard">
                          {log.on_duty_guard_name || '—'}
                          {log.scanned_by_name && log.scanned_by_name !== log.on_duty_guard_name && (
                            <span className="vl-scanned-by">Scanned by {log.scanned_by_name}</span>
                          )}
                        </td>
                        <td className="vl-exit">
                          {log.exited_at ? (
                            <>
                              {fmtTime(log.exited_at)}
                              <span className="vl-duration">{fmtDuration(log.duration_minutes)} inside</span>
                            </>
                          ) : log.status === 'authorized' ? (
                            <span className="vl-inside">Still inside</span>
                          ) : '—'}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>

              <div className="vl-pagination">
                <span className="vl-pagination-info">
                  Showing {(pageSafe - 1) * PAGE_SIZE + 1}–{Math.min(pageSafe * PAGE_SIZE, totalCount)} of {totalCount} records
                </span>
                <div className="vl-pagination-controls">
                  <button
                    className="vl-page-btn"
                    disabled={pageSafe === 1}
                    onClick={() => setPage(p => p - 1)}
                  >
                    <ChevronLeft size={16} />
                  </button>
                  <span className="vl-page-current">Page {pageSafe} of {totalPages}</span>
                  <button
                    className="vl-page-btn"
                    disabled={pageSafe >= totalPages}
                    onClick={() => setPage(p => p + 1)}
                  >
                    <ChevronRight size={16} />
                  </button>
                </div>
              </div>
            </>
          )}
        </div>

        {!loading && logs.length >= FETCH_LIMIT && (
          <p className="vl-limit-note">
            Showing the {FETCH_LIMIT} most recent scans in this range — narrow the date range to reach older ones.
          </p>
        )}
      </div>
    </>
  )
}
