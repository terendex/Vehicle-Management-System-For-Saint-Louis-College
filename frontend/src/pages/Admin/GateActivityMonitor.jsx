import { useState, useEffect, useCallback } from 'react'
import {
  Shield, Users, AlertTriangle, RefreshCw, Clock,
  CheckCircle, XCircle, HelpCircle, ArrowRightLeft,
  UserCheck, ScanLine, LogOut,
} from 'lucide-react'
import { format } from 'date-fns'
import { toast } from 'sonner'
import AdminLayout from '../../components/Layout/AdminLayout'
import { getGuardMonitor } from '../../api/scanning'
import './GateActivityMonitor.css'

const GATE_LABELS = { gate1: 'Gate 1', gate4: 'Gate 4' }

const STATUS_META = {
  authorized: { label: 'Approved',  cls: 'authorized', Icon: CheckCircle  },
  denied:     { label: 'Denied',    cls: 'denied',     Icon: XCircle      },
  wrong_day:  { label: 'Wrong Day', cls: 'denied',     Icon: XCircle      },
  unknown:    { label: 'Visitor',   cls: 'visitor',    Icon: HelpCircle   },
  no_pass:    { label: 'No Pass',   cls: 'visitor',    Icon: HelpCircle   },
  disabled:   { label: 'Disabled',  cls: 'denied',     Icon: XCircle      },
  exited:     { label: 'Exited',    cls: 'exited',     Icon: LogOut       },
  unreadable: { label: 'Unreadable', cls: 'denied',    Icon: XCircle      },
}
function getMeta(s) { return STATUS_META[s] ?? STATUS_META.unknown }

function fmt(ts) {
  try { return format(new Date(ts), 'h:mm a') } catch { return '—' }
}
function fmtFull(ts) {
  try { return format(new Date(ts), 'MMM d, h:mm a') } catch { return '—' }
}

// ─── GuardDailyCard ──────────────────────────────────────────────────────────
function GuardDailyCard({ guard }) {
  const { full_name, user_code, gate_assignment, is_active, stats, recent_logs, shifts_today } = guard

  const gateLabel = GATE_LABELS[gate_assignment] ?? gate_assignment ?? '—'
  const hasShifts = shifts_today?.length > 0
  const hasScans  = recent_logs?.length > 0

  return (
    <div className={`gam-guard-card ${is_active ? 'active' : ''}`}>
      {/* Guard header */}
      <div className="gam-guard-card-header">
        <div className="gam-guard-info">
          <div className="gam-guard-avatar">
            <UserCheck size={16} />
          </div>
          <div>
            <div className="gam-guard-name">{full_name}</div>
            <div className="gam-guard-meta">
              <span className="gam-gate-pill">{gateLabel}</span>
              {user_code && <span className="gam-guard-code">{user_code}</span>}
            </div>
          </div>
        </div>
        <div className="gam-guard-status-group">
          {is_active ? (
            <span className="gam-status-pill active">
              <span className="gam-dot-blink" />
              On Duty
            </span>
          ) : (
            <span className="gam-status-pill inactive">Off Duty</span>
          )}
          <div className="gam-guard-scan-count">
            <ScanLine size={12} />
            <span>{stats?.total ?? 0} scans</span>
          </div>
        </div>
      </div>

      {/* Today's shifts */}
      {hasShifts && (
        <div className="gam-shift-section">
          <span className="gam-section-label">
            <Clock size={11} /> Shift{shifts_today.length > 1 ? 's' : ''} Today
          </span>
          <div className="gam-shifts-inline">
            {shifts_today.map((s, i) => (
              <span key={s.id ?? i} className="gam-shift-chip">
                <span className="gam-shift-chip-gate">{GATE_LABELS[s.gate] ?? s.gate}</span>
                {fmt(s.clocked_in_at)}
                {' → '}
                {s.clocked_out_at ? fmt(s.clocked_out_at) : <span style={{ color: '#10b981' }}>Now</span>}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Scan stats mini-row */}
      {stats?.total > 0 && (
        <div className="gam-mini-stats">
          <span className="gam-mini-stat authorized">{stats.authorized} approved</span>
          <span className="gam-mini-stat denied">{stats.denied} denied</span>
          <span className="gam-mini-stat exited">{stats.exited} exits</span>
          {stats.visitors > 0 && <span className="gam-mini-stat visitor">{stats.visitors} visitors</span>}
        </div>
      )}

      {/* Scan list */}
      <div className="gam-scan-list">
        {!hasScans ? (
          <p className="gam-log-empty">No scans recorded today.</p>
        ) : (
          <>
            <div className="gam-scan-list-header">
              <span>Plate</span>
              <span>Owner</span>
              <span>Status</span>
              <span>Gate</span>
              <span>Time</span>
            </div>
            {recent_logs.map((log, i) => {
              const { cls, label } = getMeta(log.status)
              return (
                <div key={log.id ?? i} className="gam-scan-row">
                  <span className="gam-log-plate">{log.plate_number || '—'}</span>
                  <span className="gam-scan-owner">{log.vehicle_owner_name || '—'}</span>
                  <span className={`gam-log-badge ${cls}`}>{label}</span>
                  <span className="gam-scan-gate">{GATE_LABELS[log.gate_id] ?? log.gate_id ?? '—'}</span>
                  <span className="gam-log-time">{fmtFull(log.scanned_at)}</span>
                </div>
              )
            })}
          </>
        )}
      </div>
    </div>
  )
}

// ─── CrossGateFlags ───────────────────────────────────────────────────────────
function CrossGateFlags({ flags }) {
  if (!flags?.length) return (
    <div className="gam-card gam-flags-empty">
      <CheckCircle size={20} style={{ color: '#10b981' }} />
      <span>No cross-gate discrepancies today.</span>
    </div>
  )

  return (
    <div className="gam-card">
      <div className="gam-card-head">
        <span className="gam-card-label"><AlertTriangle size={14} /> Cross-Gate Discrepancies</span>
        <span className="gam-badge-warn">{flags.length}</span>
      </div>
      <div className="gam-flags-list">
        {flags.map((f, i) => (
          <div key={i} className="gam-flag-item">
            <ArrowRightLeft size={14} style={{ color: '#d97706', flexShrink: 0 }} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <span className="gam-log-plate" style={{ fontSize: 13 }}>{f.plate_number}</span>
              {f.owner_name && f.owner_name !== '—' && (
                <span style={{ fontSize: 11, color: '#6b7280', marginLeft: 6 }}>{f.owner_name}</span>
              )}
              <span style={{ fontSize: 11, color: '#92400e', marginLeft: 6 }}>
                Entered {GATE_LABELS[f.entry_gate] ?? f.entry_gate} → Exited {GATE_LABELS[f.exit_gate] ?? f.exit_gate}
              </span>
              <div style={{ fontSize: 10, color: '#b45309', marginTop: 1 }}>
                In: {fmtFull(f.entered_at)} · Out: {fmtFull(f.exited_at)}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ─── Main Page ────────────────────────────────────────────────────────────────
export default function GateActivityMonitor() {
  const [guards, setGuards]         = useState([])
  const [crossFlags, setCrossFlags] = useState([])
  const [loading, setLoading]       = useState(true)
  const [lastRefresh, setLastRefresh] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await getGuardMonitor()
      setGuards(res.data?.guards ?? [])
      setCrossFlags(res.data?.cross_gate_flags ?? [])
      setLastRefresh(new Date())
    } catch {
      toast.error('Failed to load guard activity.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    const id = setInterval(load, 30_000)
    return () => clearInterval(id)
  }, [load])

  const activeGuards = guards.filter(g => g.is_active).length
  const totalScans   = guards.reduce((s, g) => s + (g.stats?.total ?? 0), 0)
  const today        = format(new Date(), 'EEEE, MMMM d, yyyy')

  return (
    <AdminLayout>
      <div className="gam-page">

        {/* Header */}
        <div className="gam-header">
          <div>
            <h1 className="gam-title">Guard Activity</h1>
            <p className="gam-subtitle">
              Daily report — {today}
              {lastRefresh && (
                <span style={{ color: '#9ca3af', marginLeft: 8, fontSize: 11 }}>
                  · Updated {format(lastRefresh, 'h:mm:ss a')}
                </span>
              )}
            </p>
          </div>
          <button className="gam-refresh-btn" onClick={load} disabled={loading}>
            <RefreshCw size={14} className={loading ? 'gam-spin' : ''} />
            {loading ? 'Loading…' : 'Refresh'}
          </button>
        </div>

        {/* Stats */}
        <div className="gam-stats-row">
          <div className="gam-stat-card">
            <div className="gam-stat-icon blue"><Users size={18} /></div>
            <div>
              <p className="gam-stat-val">{activeGuards}</p>
              <p className="gam-stat-label">Guards On Duty</p>
            </div>
          </div>
          <div className="gam-stat-card">
            <div className="gam-stat-icon green"><ScanLine size={18} /></div>
            <div>
              <p className="gam-stat-val">{totalScans}</p>
              <p className="gam-stat-label">Total Scans Today</p>
            </div>
          </div>
          <div className="gam-stat-card">
            <div className="gam-stat-icon amber"><AlertTriangle size={18} /></div>
            <div>
              <p className="gam-stat-val">{crossFlags.length}</p>
              <p className="gam-stat-label">Cross-Gate Flags</p>
            </div>
          </div>
        </div>

        {/* Guard cards */}
        {loading && guards.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '40px 0', color: '#9ca3af', fontSize: 13 }}>
            Loading guard activity…
          </div>
        ) : guards.length === 0 ? (
          <div className="gam-card gam-flags-empty">
            <Shield size={20} style={{ color: '#d1d5db' }} />
            <span>No security guards found.</span>
          </div>
        ) : (
          <div className="gam-guards-grid">
            {guards.map(g => (
              <GuardDailyCard key={g.id} guard={g} />
            ))}
          </div>
        )}

        {/* Cross-gate discrepancies */}
        <CrossGateFlags flags={crossFlags} />

      </div>
    </AdminLayout>
  )
}
