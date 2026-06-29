import { useState, useEffect, useCallback, useRef } from 'react'
import {
  Shield, Users, AlertTriangle, RefreshCw, Clock,
  CheckCircle, XCircle, HelpCircle, ArrowRightLeft,
  UserCheck, Activity, Video, Wifi, MonitorDot,
} from 'lucide-react'
import { formatDistanceToNow, format } from 'date-fns'
import { toast } from 'sonner'
import AdminLayout from '../../components/Layout/AdminLayout'
import { getCurrentShifts, getShifts, getAccessLogs, getGuardMonitor } from '../../api/scanning'
import { camerasApi } from '../../api/cameras'
import { useMultiRtspStream } from '../../hooks/useMultiRtspStream'
import './OperationsCenter.css'

// ─── Helpers ───────────────────────────────────────────────────────────────────
const GATE_LABELS = { gate1: 'Gate 1', gate4: 'Gate 4' }
const GATES = ['gate1', 'gate4']

const STATUS_META = {
  authorized: { label: 'Authorized', cls: 'authorized', Icon: CheckCircle  },
  denied:     { label: 'Denied',     cls: 'denied',     Icon: XCircle      },
  wrong_day:  { label: 'Wrong Day',  cls: 'denied',     Icon: XCircle      },
  unknown:    { label: 'Visitor',    cls: 'visitor',    Icon: HelpCircle   },
  no_pass:    { label: 'No Pass',    cls: 'visitor',    Icon: HelpCircle   },
  disabled:   { label: 'Disabled',   cls: 'denied',     Icon: XCircle      },
  exited:     { label: 'Exited',     cls: 'exited',     Icon: CheckCircle  },
  pending:    { label: 'Pending',    cls: 'pending',    Icon: Clock        },
}
function getMeta(s) { return STATUS_META[s] ?? STATUS_META.unknown }

function timeAgo(ts) {
  try { return formatDistanceToNow(new Date(ts), { addSuffix: true }) } catch { return '' }
}
function fmt(ts) {
  try { return format(new Date(ts), 'MMM d, h:mm a') } catch { return '—' }
}
function shiftDur(start) {
  const mins = Math.floor((Date.now() - new Date(start).getTime()) / 60000)
  if (mins < 60) return `${mins}m`
  return `${Math.floor(mins / 60)}h ${mins % 60}m`
}

// ─── Gate Panel ───────────────────────────────────────────────────────────────
function GatePanel({ gate, shift, logs }) {
  return (
    <div className="oc-gate-panel">
      <div className="oc-gate-head">
        <div className="oc-gate-title">
          <Shield size={15} />
          <span>{GATE_LABELS[gate]}</span>
        </div>
        {shift ? (
          <div className="oc-guard-pill active">
            <span className="oc-guard-dot" />
            <span className="oc-guard-name">{shift.guard_name}</span>
            <span className="oc-guard-since">{shiftDur(shift.clocked_in_at)}</span>
          </div>
        ) : (
          <div className="oc-guard-pill inactive">No guard on duty</div>
        )}
      </div>

      <div className="oc-gate-log">
        {logs.length === 0 ? (
          <p className="oc-empty">No recent activity.</p>
        ) : (
          logs.map((log, i) => {
            const { cls, label } = getMeta(log.status)
            return (
              <div key={log.id ?? i} className="oc-log-item">
                <span className={`oc-log-dot ${cls}`} />
                <div className="oc-log-main">
                  <span className="oc-log-plate">{log.plate_number || '—'}</span>
                  {(log.vehicle_owner_name || log.scanned_by_name) && (
                    <span className="oc-log-meta">
                      {log.vehicle_owner_name}
                      {log.scanned_by_name && ` · ${log.scanned_by_name}`}
                    </span>
                  )}
                </div>
                <span className={`oc-log-badge ${cls}`}>{label}</span>
                <span className="oc-log-time">{timeAgo(log.scanned_at)}</span>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}

// ─── Guard Card ───────────────────────────────────────────────────────────────
function GuardCard({ guard }) {
  const { stats, recent_logs } = guard
  return (
    <div className={`oc-guard-card ${guard.is_active ? 'active' : ''}`}>
      <div className="oc-guard-card-head">
        <div className="oc-guard-avatar">{guard.full_name.charAt(0).toUpperCase()}</div>
        <div className="oc-guard-info">
          <span className="oc-guard-card-name">{guard.full_name}</span>
          <span className="oc-guard-card-code">{guard.user_code || 'Security'}</span>
        </div>
        <span className={`oc-duty-pill ${guard.is_active ? 'active' : 'idle'}`}>
          <span className="oc-duty-dot" />
          {guard.is_active ? 'On Duty' : 'Off Duty'}
        </span>
      </div>

      <div className="oc-guard-stats">
        {[
          { n: stats.total,      l: 'Total',    cls: 'total'      },
          { n: stats.authorized, l: 'Auth',      cls: 'authorized' },
          { n: stats.denied,     l: 'Denied',    cls: 'denied'     },
          { n: stats.visitors,   l: 'Visitors',  cls: 'visitor'    },
          { n: stats.exited,     l: 'Exits',     cls: 'exited'     },
        ].map(({ n, l, cls }) => (
          <div key={l} className={`oc-gstat ${cls}`}>
            <span className={`oc-gstat-n${n > 0 ? ' nonzero' : ''}`}>{n}</span>
            <span className="oc-gstat-l">{l}</span>
          </div>
        ))}
      </div>

      {recent_logs.length > 0 && (
        <div className="oc-guard-log">
          {recent_logs.slice(0, 4).map(log => {
            const m = getMeta(log.status)
            return (
              <div key={log.id} className="oc-log-item compact">
                <span className={`oc-log-dot ${m.cls}`} />
                <span className="oc-log-plate">{log.plate_number || '—'}</span>
                <span className={`oc-log-badge ${m.cls}`}>{m.label}</span>
                <span className="oc-log-time">{timeAgo(log.scanned_at)}</span>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ─── Camera Monitor ───────────────────────────────────────────────────────────
function CameraMonitor() {
  const getToken = useCallback(() => localStorage.getItem('access_token') || '', [])

  const {
    cameras,
    activeCamId,
    setActiveCamId,
    activeCam,
    addCamera,
    registerCanvas,
  } = useMultiRtspStream(getToken())

  useEffect(() => {
    camerasApi.list({ assignment: 'entry' })
      .then(cams => cams.forEach(c => addCamera(c.name, c.rtsp_url)))
      .catch(() => {})
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const isLive = cameras.some(c => c.streamConnected)

  return (
    <div className="oc-cam-panel">
      <div className="oc-cam-head">
        <span className="oc-cam-label"><Video size={14} /> Camera Monitor</span>
        <span className="oc-cam-note">View only — detection is handled by guard terminals</span>
        <span className={`oc-live-pill ${isLive ? 'live' : cameras.length === 0 ? 'none' : 'connecting'}`}>
          <span className="oc-live-dot" />
          {cameras.length === 0 ? 'No Cameras' : isLive ? 'Live' : 'Connecting…'}
        </span>
      </div>

      <div className="oc-cam-viewport">
        {cameras.length > 0 ? (
          <>
            {cameras.map((cam, idx) => (
              <div
                key={cam.id}
                style={{ display: activeCamId === cam.id ? 'block' : 'none', width: '100%', ...(idx > 0 ? { position: 'absolute', inset: 0 } : {}) }}
              >
                <canvas
                  ref={el => registerCanvas(cam.id, el)}
                  style={{ width: '100%', display: 'block', background: '#000', minHeight: 260 }}
                />
              </div>
            ))}
            {activeCam && !activeCam.streamConnected && activeCam.wsActive && (
              <div className="oc-cam-overlay">
                <div className="oc-cam-spinner" />
                <p>{activeCam.statusMsg || 'Connecting…'}</p>
              </div>
            )}
            <div className="oc-cam-name-tag">
              <span className={`oc-cam-dot ${activeCam?.streamConnected ? 'live' : 'wait'}`} />
              {activeCam?.name || 'Camera'}
            </div>
          </>
        ) : (
          <div className="oc-cam-empty">
            <Wifi size={36} />
            <p>No entry cameras configured.</p>
            <span>Add cameras in Device Management.</span>
          </div>
        )}
      </div>

      {cameras.length > 1 && (
        <div className="oc-cam-strip">
          {cameras.map(cam => (
            <button
              key={cam.id}
              className={`oc-cam-thumb ${activeCamId === cam.id ? 'active' : ''}`}
              onClick={() => setActiveCamId(cam.id)}
            >
              <span className={`oc-cam-dot ${cam.streamConnected ? 'live' : cam.wsActive ? 'wait' : 'off'}`} />
              {cam.name}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── Main Page ─────────────────────────────────────────────────────────────────
export default function OperationsCenter() {
  const [currentShifts, setCurrentShifts] = useState({})
  const [logsByGate,    setLogsByGate]    = useState({ gate1: [], gate4: [] })
  const [crossFlags,    setCrossFlags]    = useState([])
  const [shiftHistory,  setShiftHistory]  = useState([])
  const [guards,        setGuards]        = useState([])
  const [loading,       setLoading]       = useState(true)
  const [lastRefresh,   setLastRefresh]   = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [shiftsRes, monitorRes, gate1Res, gate4Res, historyRes] = await Promise.allSettled([
        getCurrentShifts(),
        getGuardMonitor(),
        getAccessLogs({ gate_id: 'gate1', limit: 12 }),
        getAccessLogs({ gate_id: 'gate4', limit: 12 }),
        getShifts({ limit: 20 }),
      ])

      if (shiftsRes.status === 'fulfilled')
        setCurrentShifts(shiftsRes.value.data ?? {})

      if (monitorRes.status === 'fulfilled') {
        const d = monitorRes.value.data
        setCrossFlags(d?.cross_gate_flags ?? [])
        setGuards(d?.guards ?? [])
      }

      setLogsByGate({
        gate1: gate1Res.status === 'fulfilled' ? (gate1Res.value.data?.results ?? gate1Res.value.data ?? []) : [],
        gate4: gate4Res.status === 'fulfilled' ? (gate4Res.value.data?.results ?? gate4Res.value.data ?? []) : [],
      })

      if (historyRes.status === 'fulfilled')
        setShiftHistory(historyRes.value.data?.results ?? historyRes.value.data ?? [])

      setLastRefresh(new Date())
    } catch {
      toast.error('Failed to load operations data.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    const id = setInterval(load, 30_000)
    return () => clearInterval(id)
  }, [load])

  const activeGuards   = Object.values(currentShifts).filter(Boolean).length
  const totalEntries   = (logsByGate.gate1?.length ?? 0) + (logsByGate.gate4?.length ?? 0)

  return (
    <AdminLayout>
      <div className="oc-page">

        {/* ── Header ── */}
        <div className="oc-header">
          <div>
            <h1 className="oc-title">Operations Center</h1>
            <p className="oc-subtitle">
              Live view of all gate activity, guard shifts, and camera feeds.
              {lastRefresh && (
                <span className="oc-updated"> · Updated {timeAgo(lastRefresh)}</span>
              )}
            </p>
          </div>
          <button className="oc-refresh-btn" onClick={load} disabled={loading}>
            <RefreshCw size={14} className={loading ? 'oc-spin' : ''} />
            {loading ? 'Loading…' : 'Refresh'}
          </button>
        </div>

        {/* ── Stats ── */}
        <div className="oc-stats-row">
          <div className="oc-stat-card">
            <div className="oc-stat-icon blue"><Users size={18} /></div>
            <div>
              <p className="oc-stat-val">{activeGuards}</p>
              <p className="oc-stat-lbl">Guards On Duty</p>
            </div>
          </div>
          <div className="oc-stat-card">
            <div className="oc-stat-icon green"><Activity size={18} /></div>
            <div>
              <p className="oc-stat-val">{totalEntries}</p>
              <p className="oc-stat-lbl">Recent Entries</p>
            </div>
          </div>
          <div className="oc-stat-card">
            <div className="oc-stat-icon amber"><AlertTriangle size={18} /></div>
            <div>
              <p className="oc-stat-val">{crossFlags.length}</p>
              <p className="oc-stat-lbl">Cross-Gate Flags</p>
            </div>
          </div>
          <div className="oc-stat-card">
            <div className="oc-stat-icon purple"><MonitorDot size={18} /></div>
            <div>
              <p className="oc-stat-val">{guards.length}</p>
              <p className="oc-stat-lbl">Total Guards</p>
            </div>
          </div>
        </div>

        {/* ── Main grid: camera + gate panels ── */}
        <div className="oc-main-grid">
          <CameraMonitor />

          <div className="oc-gates-col">
            {GATES.map(gate => (
              <GatePanel
                key={gate}
                gate={gate}
                shift={currentShifts[gate] ?? null}
                logs={logsByGate[gate] ?? []}
              />
            ))}
          </div>
        </div>

        {/* ── Guard activity ── */}
        {guards.length > 0 && (
          <div className="oc-section">
            <div className="oc-section-head">
              <Shield size={15} />
              <span>Guard Activity</span>
              {guards.filter(g => g.is_active).length > 0 && (
                <span className="oc-duty-count">
                  {guards.filter(g => g.is_active).length} on duty
                </span>
              )}
            </div>
            <div className="oc-guards-grid">
              {guards.map(g => <GuardCard key={g.id} guard={g} />)}
            </div>
          </div>
        )}

        {/* ── Bottom row: discrepancies + shift history ── */}
        <div className="oc-bottom-row">
          {/* Cross-gate flags */}
          <div className="oc-card">
            <div className="oc-card-head">
              <ArrowRightLeft size={14} />
              <span>Cross-Gate Discrepancies</span>
              {crossFlags.length > 0 && <span className="oc-flag-count">{crossFlags.length}</span>}
            </div>
            {crossFlags.length === 0 ? (
              <div className="oc-clear">
                <CheckCircle size={18} />
                <span>No discrepancies detected</span>
              </div>
            ) : (
              <div className="oc-flags-list">
                {crossFlags.map((f, i) => (
                  <div key={i} className="oc-flag-item">
                    <AlertTriangle size={13} className="oc-flag-icon" />
                    <div>
                      <span className="oc-log-plate">{f.plate_number}</span>
                      <span className="oc-flag-route">
                        Entered {GATE_LABELS[f.entry_gate] ?? f.entry_gate} → Exited {GATE_LABELS[f.exit_gate] ?? f.exit_gate}
                      </span>
                      <div className="oc-flag-times">{fmt(f.entered_at)} · {fmt(f.exited_at)}</div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Shift history */}
          <div className="oc-card">
            <div className="oc-card-head">
              <Clock size={14} />
              <span>Recent Shift History</span>
            </div>
            {shiftHistory.length === 0 ? (
              <p className="oc-empty">No shift records found.</p>
            ) : (
              <div className="oc-shift-list">
                {shiftHistory.map((s, i) => (
                  <div key={s.id ?? i} className="oc-shift-item">
                    <UserCheck size={13} className="oc-shift-icon" />
                    <div className="oc-shift-info">
                      <div className="oc-shift-name">
                        {s.guard_name}
                        {s.is_active && <span className="oc-shift-active">On Duty</span>}
                      </div>
                      <div className="oc-shift-meta">
                        <span className="oc-shift-gate">{GATE_LABELS[s.gate] ?? s.gate}</span>
                        <span>In: {fmt(s.clocked_in_at)}</span>
                        {s.clocked_out_at
                          ? <span>Out: {fmt(s.clocked_out_at)}</span>
                          : <span className="oc-shift-still">Still active</span>
                        }
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

      </div>
    </AdminLayout>
  )
}
