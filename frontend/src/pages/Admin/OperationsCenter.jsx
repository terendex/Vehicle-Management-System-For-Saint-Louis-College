import { useState, useEffect, useCallback, useRef } from 'react'
import { useLiveUpdates } from '../../realtime/useLiveUpdates'
import {
  Shield, Users, AlertTriangle, RefreshCw, Clock,
  CheckCircle, XCircle, HelpCircle, ArrowRightLeft,
  UserCheck, Activity, Video, Wifi, MonitorDot, ParkingCircle,
  ChevronLeft, ChevronRight, Search, X, Maximize2, Minimize2,
} from 'lucide-react'
import { formatDistanceToNow, format } from 'date-fns'
import { toast } from '../../components/Feedback/notify'
import { getCurrentShifts, getShifts, getAccessLogs, getGuardMonitor, getVisitorPasses } from '../../api/scanning'
import { camerasApi } from '../../api/cameras'
import { useCameraContext } from '../../context/CameraContext'
import { useGates } from '../../hooks/useGates'
import { useFullscreen } from '../../hooks/useFullscreen'
import TableLoader from '../../components/TableLoader'
import ConfiscatedAccounts from '../../components/ConfiscatedAccounts'
import './OperationsCenter.css'

// ─── Helpers ───────────────────────────────────────────────────────────────────

const STATUS_META = {
  authorized: { label: 'Authorized', cls: 'authorized', Icon: CheckCircle  },
  open_entry: { label: 'Open Entry', cls: 'authorized', Icon: CheckCircle  },
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
function GatePanel({ label, shift, logs }) {
  return (
    <div className="oc-gate-panel">
      <div className="oc-gate-head">
        <div className="oc-gate-title">
          <Shield size={15} />
          <span>{label}</span>
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
function GuardTable({ guards, gateLabel }) {
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
                <td className="oc-gt-gate">{gateLabel(g.gate_assignment) || '—'}</td>
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
// Scopes a camera can be filtered by. One tab per gate — built from the live
// gate list, so a gate added in System Settings gets its own filter — then
// parking, which is one bucket regardless of how many zones share the lot.
function buildCamScopes(gates, gateLabel) {
  return [
    { key: 'all', label: 'All Cameras', match: () => true },
    ...gates.map(g => ({
      key:   g.gate_id,
      label: gateLabel(g.gate_id),
      match: c => c.assignment === 'entry' && c.gate_id === g.gate_id,
    })),
    { key: 'parking', label: 'Parking', match: c => c.assignment === 'parking' },
  ]
}

function CameraMonitor() {
  const { cameras: liveCameras, addCamera, disconnectCamera, registerCanvas } = useCameraContext()
  const { gates, gateLabel } = useGates()
  const [devices,   setDevices]   = useState([])   // registered cameras (from the API)
  const [scope,     setScope]     = useState('all')
  const [selectedId, setSelectedId] = useState(null) // Camera.id from the API
  const [camQuery,  setCamQuery]  = useState('')
  const fs = useFullscreen()

  const camScopeLabel = (cam) => {
    if (cam.assignment === 'parking') return 'Parking'
    return gateLabel(cam.gate_id) || 'Unassigned'
  }

  // Only feeds this panel opened may be closed by it. A camera another page is
  // already streaming — a guard terminal running detection, say — must survive
  // switching cameras here.
  const ownedRef = useRef(new Set())
  const liveRef  = useRef(liveCameras)
  // Declared above the connect effect so it is already current when that runs.
  useEffect(() => { liveRef.current = liveCameras }, [liveCameras])

  useEffect(() => {
    camerasApi.list()
      .then(cams => setDevices(cams.filter(c => c.is_active !== false && c.rtsp_url)))
      .catch(() => {})
  }, [])

  const camScopes = buildCamScopes(gates, gateLabel)
  const scopes    = camScopes.filter(s => s.key === 'all' || devices.some(s.match))
  const inScope   = devices.filter(camScopes.find(s => s.key === scope)?.match ?? (() => true))
  // Search narrows within the active scope rather than replacing it, so the
  // gate tabs and the box compose instead of fighting each other.
  const camQ      = camQuery.trim().toLowerCase()
  const visible   = camQ
    ? inScope.filter(c => [c.name, c.ip, c.device_id, camScopeLabel(c)]
        .some(f => String(f ?? '').toLowerCase().includes(camQ)))
    : inScope

  // Keep the selection inside the current filter.
  const selected = visible.find(c => c.id === selectedId) ?? visible[0] ?? null

  // One feed at a time. Opening every registered camera at once meant N RTSP
  // decoders on the campus PC for one visible picture, which is a large part of
  // why feeds were stalling.
  useEffect(() => {
    if (!selected) return
    const owned = ownedRef.current          // stable Set, created once
    const alreadyOpen = liveRef.current.some(c => c.url === selected.rtsp_url)
    // View-only: no detect flag, so this never downgrades a scanning connection.
    const camId = addCamera(selected.name, selected.rtsp_url, selected.assignment,
                            { gate: selected.gate_id })
    if (camId == null) return
    if (!alreadyOpen) owned.add(camId)

    return () => {
      if (owned.has(camId)) {
        owned.delete(camId)
        disconnectCamera(camId)
      }
    }
  }, [selected?.id, selected?.rtsp_url]) // eslint-disable-line react-hooks/exhaustive-deps

  const feed = liveCameras.find(c => c.url === selected?.rtsp_url) ?? null
  const isLive = !!feed?.streamConnected

  return (
    <div className="oc-cam-panel">
      <div className="oc-cam-head">
        <span className="oc-cam-label"><Video size={14} /> Camera Monitor</span>
        <span className="oc-cam-note">View only — detection is handled by guard terminals</span>
        <span className={`oc-live-pill ${isLive ? 'live' : devices.length === 0 ? 'none' : 'connecting'}`}>
          <span className="oc-live-dot" />
          {devices.length === 0 ? 'No Cameras' : isLive ? 'Live' : 'Connecting…'}
        </span>
      </div>

      {devices.length > 0 && (
        <div className="oc-cam-controls">
          <div className="oc-cam-scopes" role="group" aria-label="Filter cameras">
            {scopes.map(s => (
              <button
                key={s.key}
                className={`oc-cam-scope ${scope === s.key ? 'active' : ''}`}
                onClick={() => setScope(s.key)}
              >
                {s.key === 'parking' ? <ParkingCircle size={12} /> : s.key === 'all' ? null : <Shield size={12} />}
                {s.label}
              </button>
            ))}
          </div>

          <div className="oc-cam-search">
            <Search size={13} className="oc-cam-search-icon" />
            <input
              type="search"
              placeholder="Search cameras…"
              value={camQuery}
              onChange={e => setCamQuery(e.target.value)}
              aria-label="Search cameras"
            />
            {camQ && (
              <button
                type="button"
                className="oc-cam-search-clear"
                onClick={() => setCamQuery('')}
                title="Clear search"
                aria-label="Clear search"
              >
                <X size={12} />
              </button>
            )}
          </div>

          <label className="oc-cam-picker">
            <span className="oc-cam-picker-lbl">Camera</span>
            <select
              value={selected?.id ?? ''}
              onChange={e => setSelectedId(Number(e.target.value))}
              disabled={visible.length === 0}
            >
              {visible.length === 0 && (
                <option value="">{camQ ? 'No cameras match' : 'No cameras in this filter'}</option>
              )}
              {visible.map(c => (
                <option key={c.id} value={c.id}>{c.name} — {camScopeLabel(c)}</option>
              ))}
            </select>
          </label>
        </div>
      )}

      <div className="oc-cam-viewport" ref={fs.setRef('monitor')}>
        {selected ? (
          <>
            {/* Keyed by stream URL so switching cameras remounts the canvas
                instead of painting the new feed over the old one's last frame. */}
            <canvas
              key={selected.rtsp_url}
              ref={el => feed && registerCanvas(feed.id, el)}
              style={{ width: '100%', display: 'block', background: '#000', minHeight: 260 }}
            />
            {!isLive && (
              <div className="oc-cam-overlay">
                <div className="oc-cam-spinner" />
                <p>{feed?.statusMsg || 'Connecting…'}</p>
              </div>
            )}
            <div className="oc-cam-name-tag">
              <span className={`oc-cam-dot ${isLive ? 'live' : 'wait'}`} />
              {selected.name} — {camScopeLabel(selected)}
            </div>
            <button
              className="oc-cam-fs"
              onClick={async () => {
                if (!(await fs.toggle('monitor'))) toast.error('Fullscreen was blocked by the browser.')
              }}
              title={fs.isFullscreen('monitor') ? 'Exit fullscreen' : 'Fullscreen'}
              aria-label={fs.isFullscreen('monitor') ? 'Exit fullscreen' : 'Fullscreen'}
            >
              {fs.isFullscreen('monitor') ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
            </button>
          </>
        ) : (
          <div className="oc-cam-empty">
            <Wifi size={36} />
            <p>{devices.length === 0
                  ? 'No cameras configured.'
                  : 'No cameras match this filter.'}</p>
            <span>{devices.length === 0
                     ? 'Add cameras in Device Management.'
                     : 'Pick another gate, or Parking.'}</span>
          </div>
        )}
      </div>

      {visible.length > 1 && (
        <div className="oc-cam-strip">
          {visible.map(cam => {
            const f = liveCameras.find(l => l.url === cam.rtsp_url)
            return (
              <button
                key={cam.id}
                className={`oc-cam-thumb ${selected?.id === cam.id ? 'active' : ''}`}
                onClick={() => setSelectedId(cam.id)}
              >
                <span className={`oc-cam-dot ${f?.streamConnected ? 'live' : f?.wsActive ? 'wait' : 'off'}`} />
                {cam.name} · {camScopeLabel(cam)}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ─── Main Page ─────────────────────────────────────────────────────────────────
export default function OperationsCenter() {
  const { gates, gateIds, gateLabel } = useGates()
  const [currentShifts, setCurrentShifts] = useState({})
  const [logsByGate,    setLogsByGate]    = useState({})
  const [crossFlags,    setCrossFlags]    = useState([])
  const [shiftHistory,  setShiftHistory]  = useState([])
  const [guards,        setGuards]        = useState([])
  const [visitorPasses, setVisitorPasses] = useState([]) // active passes — vehicles currently inside
  const [loading,       setLoading]       = useState(true)
  const [lastRefresh,   setLastRefresh]   = useState(null)
  const [shiftPage,     setShiftPage]     = useState(1)
  const [flagPage,      setFlagPage]      = useState(1)

  // gateIds is a fresh array each render; key the callback on its contents so
  // load() is only rebuilt when the gate list actually changes.
  const gateKey = gateIds.join(',')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      // One log request per gate, so gates added in System Settings get a feed
      // without touching this file.
      const [shiftsRes, monitorRes, historyRes, passesRes, ...logResults] = await Promise.allSettled([
        getCurrentShifts(),
        getGuardMonitor(),
        getShifts({ limit: 100 }),
        getVisitorPasses(),
        ...gateIds.map(id => getAccessLogs({ gate_id: id, limit: 12 })),
      ])

      if (shiftsRes.status === 'fulfilled')
        setCurrentShifts(shiftsRes.value.data ?? {})

      if (monitorRes.status === 'fulfilled') {
        const d = monitorRes.value.data
        setCrossFlags(d?.cross_gate_flags ?? [])
        setGuards(d?.guards ?? [])
      }

      setLogsByGate(Object.fromEntries(gateIds.map((id, i) => {
        const res = logResults[i]
        return [id, res?.status === 'fulfilled' ? (res.value.data?.results ?? res.value.data ?? []) : []]
      })))

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
  }, [gateKey]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { load() }, [load])
  useLiveUpdates(load)
  useEffect(() => {
    const id = setInterval(load, 30_000)
    return () => clearInterval(id)
  }, [load])

  const activeGuards   = Object.values(currentShifts).filter(Boolean).length
  const totalEntries   = Object.values(logsByGate).reduce((n, l) => n + (l?.length ?? 0), 0)

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
    <>
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
            {gates.map(g => (
              <GatePanel
                key={g.gate_id}
                label={gateLabel(g.gate_id)}
                shift={currentShifts[g.gate_id] ?? null}
                logs={logsByGate[g.gate_id] ?? []}
              />
            ))}
          </div>
        </div>

        {/* ── Guard activity ── */}
        {/* While loading the section stays visible with a spinner; it used to
            be hidden entirely (guards is empty until the fetch lands), so the
            table appeared out of nowhere with no indication it was coming. */}
        {loading ? (
          <div className="oc-section">
            <div className="oc-section-head">
              <Shield size={15} />
              <span>Guard Activity</span>
            </div>
            <TableLoader label="Loading guard activity…" />
          </div>
        ) : guards.some(g => g.is_active) && (
          <div className="oc-section">
            <div className="oc-section-head">
              <Shield size={15} />
              <span>Guard Activity</span>
              <span className="oc-duty-count">
                {guards.filter(g => g.is_active).length} on duty
              </span>
            </div>
            <GuardTable guards={guards.filter(g => g.is_active)} gateLabel={gateLabel} />
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
                        background: t.overdue ? '#FCEDED' : '#F7FAFC',
                        border: `1px solid ${t.overdue ? '#F3C0C0' : '#D3E1EC'}`,
                      }}
                    >
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <span className="oc-log-plate">{p.plate_number}</span>
                        <div style={{ fontSize: 11, color: '#5C7B92', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          {p.office_name || 'No office'}{p.purpose ? ` · ${p.purpose}` : ''}
                        </div>
                        {p.issued_by_name && (
                          <div style={{ fontSize: 10.5, color: '#64839C' }}>Issued by {p.issued_by_name}</div>
                        )}
                      </div>
                      <span style={{
                        fontSize: 11, fontWeight: 700, whiteSpace: 'nowrap',
                        color: t.overdue ? '#C62828' : t.soon ? '#8A6B00' : '#0F7A5A',
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
                          Entered {gateLabel(f.entry_gate)} → Exited {gateLabel(f.exit_gate)}
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
                          <span className="oc-shift-gate">{gateLabel(s.gate)}</span>
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

        {/* Accounts serving a violation penalty — barred from entering and
            from parking until the term runs out or the CDSO lifts it. */}
        <div className="oc-confiscated">
          <ConfiscatedAccounts />
        </div>

      </div>
    </>
  )
}
