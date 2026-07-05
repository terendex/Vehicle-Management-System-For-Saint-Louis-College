import { useState, useEffect, useCallback, useRef } from 'react'
import {
  Shield, Users, AlertTriangle, RefreshCw, Clock,
  CheckCircle, XCircle, HelpCircle, ArrowRightLeft,
  UserCheck, Activity, Video, Wifi, MonitorDot,
  ChevronLeft, ChevronRight,
} from 'lucide-react'
import { formatDistanceToNow, format } from 'date-fns'
import { toast } from 'sonner'
import AdminLayout from '../../components/Layout/AdminLayout'
import { getCurrentShifts, getShifts, getAccessLogs, getGuardMonitor, getVisitorPasses } from '../../api/scanning'
import { camerasApi } from '../../api/cameras'
import { useCameraContext } from '../../context/CameraContext'
import './OperationsCenter.css'

// ─── Helpers ───────────────────────────────────────────────────────────────────
const GATE_LABELS = { gate1: 'Gate 1', gate4: 'Gate 4' }
const GATES = ['gate1', 'gate4']

const STATUS_META = {
  authorized: { label: 'Authorized', cls: 'authorized', Icon: CheckCircle  },
  denied:     { label: 'Denied',     cls: 'denied',     Icon: XCircle      },
  wrong_day:  { label: 'Wrong Day',  cls: 'denied',     Icon: XCircle      },
  unknown:    { label: 'Unregistered', cls: 'visitor',  Icon: HelpCircle   },
  no_pass:    { label: 'No Pass',    cls: 'visitor',    Icon: HelpCircle   },
  disabled:   { label: 'Disabled',   cls: 'denied',     Icon: XCircle      },
  unreadable: { label: 'Unreadable', cls: 'visitor',    Icon: HelpCircle   },
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

// Time-left / overstay for an active visitor pass (mirrors the guard entry page)
function passTimeInfo(p) {
  if (!p.expires_at) return { label: 'No limit', overdue: false, soon: false }
  const diffMin = Math.round((new Date(p.expires_at).getTime() - Date.now()) / 60000)
  if (diffMin >= 0) return { label: `${diffMin}m left`, overdue: false, soon: diffMin <= 10 }
  return { label: `Overstay +${-diffMin}m`, overdue: true, soon: false }
}

// ─── Pager (matches the violations table pagination) ──────────────────────────
const LIST_PAGE_SIZE = 8

function Pager({ page, totalPages, total, onPage }) {
  if (totalPages <= 1) return null
  return (
    <div className="oc-pager">
      <span className="oc-pager-info">
        Showing {(page - 1) * LIST_PAGE_SIZE + 1}–{Math.min(page * LIST_PAGE_SIZE, total)} of {total}
      </span>
      <div className="oc-pager-controls">
        <button className="oc-pager-btn" disabled={page === 1} onClick={() => onPage(page - 1)}>
          <ChevronLeft size={14} />
        </button>
        <span className="oc-pager-current">Page {page} of {totalPages}</span>
        <button className="oc-pager-btn" disabled={page === totalPages} onClick={() => onPage(page + 1)}>
          <ChevronRight size={14} />
        </button>
      </div>
    </div>
  )
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

// ─── Guard Table ──────────────────────────────────────────────────────────────
function GuardTable({ guards }) {
  return (
    <div className="oc-guard-table-wrap">
      <table className="oc-guard-table">
        <thead>
          <tr>
            <th>Guard</th>
            <th>Code</th>
            <th>Gate</th>
            <th>Status</th>
            <th>Total</th>
            <th>Auth</th>
            <th>Denied</th>
            <th>Visitors</th>
            <th>Exits</th>
          </tr>
        </thead>
        <tbody>
          {guards.map(g => {
            const { stats } = g
            return (
              <tr key={g.id} className={g.is_active ? 'active' : ''}>
                <td>
                  <div className="oc-gt-guard">
                    <div className="oc-guard-avatar sm">{g.full_name.charAt(0).toUpperCase()}</div>
                    <span className="oc-gt-name">{g.full_name}</span>
                  </div>
                </td>
                <td className="oc-gt-code">{g.user_code || '—'}</td>
                <td className="oc-gt-gate">{GATE_LABELS[g.gate_assignment] ?? g.gate_assignment ?? '—'}</td>
                <td>
                  <span className={`oc-duty-pill ${g.is_active ? 'active' : 'idle'}`}>
                    <span className="oc-duty-dot" />
                    {g.is_active ? 'On Duty' : 'Off Duty'}
                  </span>
                </td>
                <td className="oc-gt-n total">{stats.total}</td>
                <td className={`oc-gt-n authorized${stats.authorized > 0 ? ' nonzero' : ''}`}>{stats.authorized}</td>
                <td className={`oc-gt-n denied${stats.denied > 0 ? ' nonzero' : ''}`}>{stats.denied}</td>
                <td className={`oc-gt-n visitor${stats.visitors > 0 ? ' nonzero' : ''}`}>{stats.visitors}</td>
                <td className={`oc-gt-n exited${stats.exited > 0 ? ' nonzero' : ''}`}>{stats.exited}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ─── Camera Monitor ───────────────────────────────────────────────────────────
function CameraMonitor() {
  const { cameras: allCameras, addCamera, registerCanvas } = useCameraContext()
  const [activeCamId, setActiveCamId] = useState(null)
  const cameras = allCameras.filter(c => c.assignment === 'entry')
  const activeCam = cameras.find(c => c.id === activeCamId) ?? cameras[0] ?? null

  useEffect(() => {
    if (!activeCamId && cameras.length > 0) setActiveCamId(cameras[0].id)
  }) // intentionally no deps — runs after every render until activeCamId is set

  useEffect(() => {
    // View-only (no detect flag) — detection runs on the guard/entry terminals.
    // The gate is passed so the monitor can label feeds and so a later upgrade
    // to scan mode tags its logs with the right gate.
    camerasApi.list({ assignment: 'entry' })
      .then(cams => cams.forEach(c => addCamera(c.name, c.rtsp_url, 'entry', { gate: c.gate_id })))
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
              {activeCam?.gate && ` — ${GATE_LABELS[activeCam.gate] ?? activeCam.gate}`}
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
              {cam.name}{cam.gate && ` · ${GATE_LABELS[cam.gate] ?? cam.gate}`}
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
  const [visitorPasses, setVisitorPasses] = useState([]) // active passes — vehicles currently inside
  const [loading,       setLoading]       = useState(true)
  const [lastRefresh,   setLastRefresh]   = useState(null)
  const [shiftPage,     setShiftPage]     = useState(1)
  const [flagPage,      setFlagPage]      = useState(1)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [shiftsRes, monitorRes, gate1Res, gate4Res, historyRes, passesRes] = await Promise.allSettled([
        getCurrentShifts(),
        getGuardMonitor(),
        getAccessLogs({ gate_id: 'gate1', limit: 12 }),
        getAccessLogs({ gate_id: 'gate4', limit: 12 }),
        getShifts({ limit: 100 }),
        getVisitorPasses(),
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

      if (passesRes.status === 'fulfilled') {
        const list = (passesRes.value.data?.results ?? passesRes.value.data ?? [])
          .filter(p => p.status === 'active')
        setVisitorPasses(list)
      }

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

  // Newest first (LIFO) + client-side pagination, same as the violations table
  const sortedShifts    = [...shiftHistory].sort((a, b) => new Date(b.clocked_in_at) - new Date(a.clocked_in_at))
  const shiftTotalPages = Math.max(1, Math.ceil(sortedShifts.length / LIST_PAGE_SIZE))
  const shiftPageSafe   = Math.min(shiftPage, shiftTotalPages)
  const pagedShifts     = sortedShifts.slice((shiftPageSafe - 1) * LIST_PAGE_SIZE, shiftPageSafe * LIST_PAGE_SIZE)

  const sortedFlags    = [...crossFlags].sort((a, b) => new Date(b.exited_at) - new Date(a.exited_at))
  const flagTotalPages = Math.max(1, Math.ceil(sortedFlags.length / LIST_PAGE_SIZE))
  const flagPageSafe   = Math.min(flagPage, flagTotalPages)
  const pagedFlags     = sortedFlags.slice((flagPageSafe - 1) * LIST_PAGE_SIZE, flagPageSafe * LIST_PAGE_SIZE)

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
        {guards.some(g => g.is_active) && (
          <div className="oc-section">
            <div className="oc-section-head">
              <Shield size={15} />
              <span>Guard Activity</span>
              <span className="oc-duty-count">
                {guards.filter(g => g.is_active).length} on duty
              </span>
            </div>
            <GuardTable guards={guards.filter(g => g.is_active)} />
          </div>
        )}

        {/* ── Active Visitors (vehicles currently inside on a visitor pass) ── */}
        <div className="oc-section">
          <div className="oc-section-head">
            <UserCheck size={15} />
            <span>Active Visitors</span>
            <span className="oc-duty-count">{visitorPasses.length} inside</span>
          </div>
          <div className="oc-card">
            {visitorPasses.length === 0 ? (
              <div className="oc-clear">
                <CheckCircle size={18} />
                <span>No visitors currently inside</span>
              </div>
            ) : (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: 8 }}>
                {visitorPasses.map(p => {
                  const t = passTimeInfo(p)
                  return (
                    <div
                      key={p.id}
                      style={{
                        display: 'flex', alignItems: 'center', gap: 8, padding: '8px 10px', borderRadius: 8,
                        background: t.overdue ? '#fef2f2' : '#f8fafc',
                        border: `1px solid ${t.overdue ? '#fecaca' : '#e6e8f0'}`,
                      }}
                    >
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <span className="oc-log-plate">{p.plate_number}</span>
                        <div style={{ fontSize: 11, color: '#6b7280', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          {p.office_name || 'No office'}{p.purpose ? ` · ${p.purpose}` : ''}
                        </div>
                        {p.issued_by_name && (
                          <div style={{ fontSize: 10.5, color: '#9ca3af' }}>Issued by {p.issued_by_name}</div>
                        )}
                      </div>
                      <span style={{
                        fontSize: 11, fontWeight: 700, whiteSpace: 'nowrap',
                        color: t.overdue ? '#dc2626' : t.soon ? '#d97706' : '#059669',
                      }}>
                        {t.overdue && <AlertTriangle size={11} style={{ verticalAlign: -1, marginRight: 3 }} />}
                        {t.label}
                      </span>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        </div>

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
              <>
                <div className="oc-flags-list">
                  {pagedFlags.map((f, i) => (
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
                <Pager page={flagPageSafe} totalPages={flagTotalPages} total={sortedFlags.length} onPage={setFlagPage} />
              </>
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
              <>
                <div className="oc-shift-list">
                  {pagedShifts.map((s, i) => (
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
                <Pager page={shiftPageSafe} totalPages={shiftTotalPages} total={sortedShifts.length} onPage={setShiftPage} />
              </>
            )}
          </div>
        </div>

      </div>
    </AdminLayout>
  )
}
