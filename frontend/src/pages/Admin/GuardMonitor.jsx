import { useState, useEffect, useCallback } from 'react'
import AdminLayout from '../../components/Layout/AdminLayout'
import { getGuardMonitor } from '../../api/scanning'
import {
  Users, RefreshCw, CheckCircle, XCircle,
  HelpCircle, Clock, Shield,
} from 'lucide-react'
import './GuardMonitor.css'

const STATUS_META = {
  authorized: { label: 'Authorized', cls: 'authorized' },
  wrong_day:  { label: 'Wrong Day',  cls: 'denied'     },
  denied:     { label: 'Denied',     cls: 'denied'     },
  unknown:    { label: 'Visitor',    cls: 'visitor'    },
  no_pass:    { label: 'No Pass',    cls: 'visitor'    },
  disabled:   { label: 'Disabled',   cls: 'denied'     },
  unreadable: { label: 'Unreadable', cls: 'visitor'    },
  exited:     { label: 'Exited',     cls: 'exited'     },
}

function getMeta(status) {
  return STATUS_META[status] ?? { label: status || '—', cls: '' }
}

function timeAgo(ts) {
  if (!ts) return '—'
  const diff = Math.floor((Date.now() - new Date(ts)) / 1000)
  if (diff < 60)    return `${diff}s ago`
  if (diff < 3600)  return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return new Date(ts).toLocaleDateString()
}

function GuardCard({ guard }) {
  const { stats, recent_logs } = guard

  return (
    <div className={`gm-card ${guard.is_active ? 'active' : 'idle'}`}>

      {/* ── Header ── */}
      <div className="gm-card-head">
        <div className="gm-guard-info">
          <div className="gm-guard-avatar">{guard.full_name.charAt(0).toUpperCase()}</div>
          <div className="gm-guard-meta">
            <span className="gm-guard-name">{guard.full_name}</span>
            <span className="gm-guard-code">{guard.user_code || 'Security'}</span>
          </div>
        </div>
        <div className="gm-guard-status-wrap">
          <span className={`gm-status-pill ${guard.is_active ? 'active' : 'idle'}`}>
            <span className="gm-status-dot" />
            {guard.is_active ? 'On Duty' : 'Idle'}
          </span>
          <span className="gm-last-seen">
            <Clock size={11} />
            {guard.last_seen ? timeAgo(guard.last_seen) : 'No scans today'}
          </span>
        </div>
      </div>

      {/* ── Stats row ── */}
      <div className="gm-stats">
        <div className="gm-stat">
          <span className="gm-stat-num">{stats.total}</span>
          <span className="gm-stat-lbl">Total</span>
        </div>
        <div className="gm-stat authorized">
          <span className="gm-stat-num">{stats.authorized}</span>
          <span className="gm-stat-lbl">Auth</span>
        </div>
        <div className="gm-stat denied">
          <span className="gm-stat-num">{stats.denied}</span>
          <span className="gm-stat-lbl">Denied</span>
        </div>
        <div className="gm-stat visitor">
          <span className="gm-stat-num">{stats.visitors}</span>
          <span className="gm-stat-lbl">Visitors</span>
        </div>
        <div className="gm-stat exited">
          <span className="gm-stat-num">{stats.exited}</span>
          <span className="gm-stat-lbl">Exits</span>
        </div>
      </div>

      {/* ── Recent scans ── */}
      <div className="gm-log-head">Recent Scans</div>
      {recent_logs.length === 0 ? (
        <p className="gm-log-empty">No scans recorded today.</p>
      ) : (
        <div className="gm-log-list">
          {recent_logs.map((log) => {
            const m = getMeta(log.status)
            return (
              <div key={log.id} className="gm-log-item">
                <span className={`gm-log-dot ${m.cls}`} />
                <div className="gm-log-main">
                  <span className="gm-log-plate">{log.plate_number || '—'}</span>
                  {log.vehicle_owner_name && (
                    <span className="gm-log-owner">{log.vehicle_owner_name}</span>
                  )}
                </div>
                <span className={`gm-log-badge ${m.cls}`}>{m.label}</span>
                <span className="gm-log-time">{timeAgo(log.scanned_at)}</span>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

export default function GuardMonitor() {
  const [guards, setGuards]           = useState([])
  const [loading, setLoading]         = useState(true)
  const [lastUpdated, setLastUpdated] = useState(null)
  const [error, setError]             = useState(false)

  const fetchGuards = useCallback(async () => {
    try {
      const res = await getGuardMonitor()
      setGuards(res.data.guards)
      setLastUpdated(new Date())
      setError(false)
    } catch {
      setError(true)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchGuards()
    const id = setInterval(fetchGuards, 10000)
    return () => clearInterval(id)
  }, [fetchGuards])

  const activeCount = guards.filter((g) => g.is_active).length

  return (
    <AdminLayout>
      <div className="gm-page">

        {/* ── Page header ── */}
        <div className="gm-page-head">
          <div>
            <h1 className="gm-page-title">
              <Shield size={20} style={{ color: '#2A2B61' }} />
              Guard Monitor
            </h1>
            <p className="gm-page-subtitle">
              Read-only live view of all security personnel activity today.
            </p>
          </div>
          <div className="gm-page-actions">
            {activeCount > 0 && (
              <span className="gm-active-badge">
                <span className="gm-live-dot" />
                {activeCount} on duty
              </span>
            )}
            {lastUpdated && (
              <span className="gm-updated-at">
                Updated {timeAgo(lastUpdated)}
              </span>
            )}
            <button className="gm-refresh-btn" onClick={fetchGuards} title="Refresh now">
              <RefreshCw size={14} />
            </button>
          </div>
        </div>

        {/* ── Body ── */}
        {loading ? (
          <div className="gm-state">
            <div className="gm-spinner" />
            <p>Loading guard activity…</p>
          </div>
        ) : error ? (
          <div className="gm-state">
            <XCircle size={40} style={{ color: '#ef4444' }} />
            <p>Could not load guard data. Retrying…</p>
          </div>
        ) : guards.length === 0 ? (
          <div className="gm-state">
            <Users size={40} style={{ color: '#9ca3af' }} />
            <p>No security personnel accounts found.</p>
          </div>
        ) : (
          <div className="gm-grid">
            {guards.map((g) => <GuardCard key={g.id} guard={g} />)}
          </div>
        )}
      </div>
    </AdminLayout>
  )
}
