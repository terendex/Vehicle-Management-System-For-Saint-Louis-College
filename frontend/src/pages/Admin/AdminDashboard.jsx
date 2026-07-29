import { useState, useEffect, useCallback } from 'react'
import { useLiveUpdates } from '../../realtime/useLiveUpdates'
import { usersApi } from '../../api/users'
import {
  Users, ShieldCheck, ClipboardList,
  Activity, Shield, RefreshCw, CheckCircle, XCircle,
  AlertTriangle, Car as CarIcon, Inbox, BarChart2, PieChart as PieIcon,
} from 'lucide-react'
import {
  PieChart, Pie, Cell, BarChart, Bar, XAxis, YAxis,
  CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts'
import './AdminDashboard.css'

// ── Shared chart config ────────────────────────────────────────────────────────

const TOOLTIP_STYLE = {
  background: '#fff',
  border: '1px solid #D3E1EC',
  borderRadius: 10,
  fontSize: 12,
  boxShadow: '0 4px 16px rgba(3, 57, 108,0.08)',
}

// ── Chart palette ───────────────────────────────────────────────────────────────
// Colour-blind-safe by construction: every ordering used below was checked with
// the dataviz validator (adjacent-pair CVD ΔE ≥ 8, normal-vision ≥ 15). Every chart
// also renders a labelled legend, so identity never rests on colour alone.
//
// STATUS hues carry meaning and are reused consistently across charts —
// Authorized is always green, Denied always red, Pending always amber.
const STATUS = {
  good:     '#0ca30c',   // authorized / accepted
  warning:  '#fab219',   // pending
  critical: '#d03b3b',   // denied / rejected
}
// CAT hues are identity keys only, assigned per entity in a fixed order (never
// cycled). `muted` is the reserved neutral for "Other" / disabled slices.
const CAT = {
  blue:    '#2a78d6',
  orange:  '#eb6834',
  aqua:    '#1baf7a',
  yellow:  '#eda100',
  magenta: '#e87ba4',
  green:   '#008300',
  violet:  '#4a3aa7',
  muted:   '#898781',
}

// ── Donut Chart ───────────────────────────────────────────────────────────────

function DonutChart({ slices, centerValue, centerLabel }) {
  const total = slices.reduce((s, d) => s + d.value, 0)
  if (total === 0) {
    return <div className="ad-chart-empty">No data available</div>
  }
  return (
    <div className="ad-donut-wrap">
      <div className="ad-donut-container">
        <ResponsiveContainer width="100%" height={200}>
          <PieChart>
            <Pie
              data={slices}
              cx="50%"
              cy="50%"
              innerRadius={58}
              outerRadius={85}
              paddingAngle={3}
              dataKey="value"
              stroke="#fff"
              strokeWidth={2}
            >
              {slices.map((s, i) => (
                <Cell key={i} fill={s.color} />
              ))}
            </Pie>
            <Tooltip
              contentStyle={TOOLTIP_STYLE}
              formatter={(val, name) => [val, name]}
            />
          </PieChart>
        </ResponsiveContainer>
        <div className="ad-donut-center">
          <span className="ad-donut-center-val">{centerValue ?? total}</span>
          <span className="ad-donut-center-label">{centerLabel ?? 'Total'}</span>
        </div>
      </div>

      <div className="ad-donut-legend">
        {slices.map((s, i) => (
          <div key={i} className="ad-donut-legend-item">
            <span className="ad-donut-legend-dot" style={{ background: s.color }} />
            <span className="ad-donut-legend-label">{s.name}</span>
            <span className="ad-donut-legend-val">{s.value}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Bar Chart ─────────────────────────────────────────────────────────────────

function DayBarChart({ data, weekTotal }) {
  if (!data || data.length === 0) {
    return <div className="ad-chart-empty">No scan data this week</div>
  }
  return (
    <div className="ad-bar-wrap">
      {weekTotal != null && (
        <div className="ad-bar-summary">
          <span className="ad-bar-summary-val">{weekTotal}</span>
          <span className="ad-bar-summary-label">entries this week</span>
        </div>
      )}
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={data} margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#EEF4F9" vertical={false} />
          <XAxis
            dataKey="day"
            tick={{ fontSize: 11.5, fill: '#5C7B92', fontWeight: 600 }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            tick={{ fontSize: 11, fill: '#9DB6C9' }}
            axisLine={false}
            tickLine={false}
            allowDecimals={false}
          />
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            cursor={{ fill: '#EEF4F9', radius: 4 }}
            formatter={(val) => [val, 'Entries']}
          />
          <Bar dataKey="count" name="Entries" fill="#03396C" radius={[6, 6, 0, 0]} maxBarSize={48} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

// ── Registrations-by-day Chart ────────────────────────────────────────────────
// Stacked accepted/pending registrations per campus day (Mon–Sat) against the
// daily slot capacity (dashed line).

const DAY_ABBREV = {
  Monday: 'Mon', Tuesday: 'Tue', Wednesday: 'Wed',
  Thursday: 'Thu', Friday: 'Fri', Saturday: 'Sat',
}

function DayRegistrationChart({ data }) {
  if (!data || data.length === 0) {
    return <div className="ad-chart-empty">No registration data</div>
  }
  const capacity = data[0]?.capacity ?? 0
  const rows = data.map(d => ({ ...d, label: DAY_ABBREV[d.day] || d.day }))
  return (
    <div className="ad-bar-wrap">
      <div className="ad-bar-summary">
        <span className="ad-bar-summary-val">{capacity}</span>
        <span className="ad-bar-summary-label">slots per day</span>
      </div>
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={rows} margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#EEF4F9" vertical={false} />
          <XAxis
            dataKey="label"
            tick={{ fontSize: 11.5, fill: '#5C7B92', fontWeight: 600 }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            tick={{ fontSize: 11, fill: '#9DB6C9' }}
            axisLine={false}
            tickLine={false}
            allowDecimals={false}
            domain={[0, dataMax => Math.max(dataMax, capacity)]}
          />
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            cursor={{ fill: '#EEF4F9', radius: 4 }}
            formatter={(val, name) => [val, name]}
            labelFormatter={(label, payload) => {
              const row = payload?.[0]?.payload
              return row ? `${row.day} — ${row.accepted + row.pending}/${row.capacity} slots used` : label
            }}
          />
          <Bar dataKey="accepted" stackId="regs" name="Accepted" fill={STATUS.good} maxBarSize={48} />
          <Bar dataKey="pending"  stackId="regs" name="Pending"  fill={STATUS.warning} radius={[6, 6, 0, 0]} maxBarSize={48} />
          <ReferenceLine y={capacity} stroke={STATUS.critical} strokeDasharray="4 4" />
        </BarChart>
      </ResponsiveContainer>
      <div className="ad-donut-legend" style={{ marginTop: 8 }}>
        <div className="ad-donut-legend-item">
          <span className="ad-donut-legend-dot" style={{ background: STATUS.good }} />
          <span className="ad-donut-legend-label">Accepted</span>
        </div>
        <div className="ad-donut-legend-item">
          <span className="ad-donut-legend-dot" style={{ background: STATUS.warning }} />
          <span className="ad-donut-legend-label">Pending</span>
        </div>
        <div className="ad-donut-legend-item">
          <span className="ad-donut-legend-dot" style={{ background: STATUS.critical }} />
          <span className="ad-donut-legend-label">Capacity ({capacity})</span>
        </div>
      </div>
    </div>
  )
}

// ── Chart Card wrapper ────────────────────────────────────────────────────────

function ChartCard({ icon: Icon, title, subtitle, children }) {
  return (
    <div className="ad-chart-card">
      <div className="ad-chart-head">
        <h2 className="ad-section-title">
          <Icon size={15} />
          {title}
        </h2>
        {subtitle && <span className="ad-chart-subtitle">{subtitle}</span>}
      </div>
      <div className="ad-chart-body">
        {children}
      </div>
    </div>
  )
}

// ── KPI Strip ─────────────────────────────────────────────────────────────────

function KpiStrip({ items }) {
  return (
    <div className="ad-kpi-strip">
      {items.map((item, i) => (
        <div key={i} className="ad-kpi-item">
          <div className="ad-kpi-icon" style={{ background: item.color + '18', color: item.color }}>
            <item.icon size={14} />
          </div>
          <div className="ad-kpi-text">
            <span className="ad-kpi-val">{item.value ?? '—'}</span>
            <span className="ad-kpi-label">{item.label}</span>
          </div>
          {item.sub && <span className="ad-kpi-sub">{item.sub}</span>}
        </div>
      ))}
    </div>
  )
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function SectionLabel({ children, live }) {
  return (
    <div className="ad-section-label">
      {live && <span className="ad-live-dot" />}
      {children}
    </div>
  )
}

function relativeTime(iso) {
  const diffMs = Date.now() - new Date(iso).getTime()
  const mins = Math.round(diffMs / 60000)
  if (mins < 1)   return 'just now'
  if (mins < 60)  return `${mins}m ago`
  const hours = Math.round(mins / 60)
  if (hours < 24) return `${hours}h ago`
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function ActivityItem({ log }) {
  // Audit details are pipe-delimited ("Plate: X | Owner: Y | …") — render as chips
  const chips = (log.details || '').split('|').map(s => s.trim()).filter(Boolean)
  return (
    <div className="ad-activity-item">
      <div className="ad-activity-dot" />
      <div className="ad-activity-content">
        <span className="ad-activity-text">
          <strong>{log.actor_name || 'Unknown'}</strong> — {log.action_label || log.action}
          <span className="ad-activity-time" style={{ marginLeft: 8 }}>{relativeTime(log.created_at)}</span>
        </span>
        {log.target_name && (
          <span className="ad-activity-target">Target: {log.target_name}</span>
        )}
        {chips.length > 0 && (
          <span style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 3 }}>
            {chips.map((c, i) => (
              <span key={i} style={{
                fontSize: 10.5, lineHeight: 1.4, padding: '2px 7px', borderRadius: 5,
                background: '#EEF4F9', color: '#4A6B85', border: '1px solid #D3E1EC',
                maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>
                {c}
              </span>
            ))}
          </span>
        )}
      </div>
    </div>
  )
}

function EmptyActivity({ message }) {
  return (
    <div className="ad-activity-empty">
      <Inbox size={30} strokeWidth={1.4} className="ad-activity-empty-icon" />
      <p>{message}</p>
    </div>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function AdminDashboard() {
  const [stats, setStats] = useState(null)
  const [loading, setLoading] = useState(true)
  const [lastUpdated, setLastUpdated] = useState(null)

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const data = await usersApi.getDashboardStats()
      setStats(data)
      setLastUpdated(new Date())
    } catch {
      // ignore
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchData() }, [fetchData])

  // Instant refresh on any data change (dashboard aggregates everything)
  useLiveUpdates(fetchData)

  // Keep the dashboard live — silent refresh every 60s (spinner only shows on first load)
  useEffect(() => {
    const timer = setInterval(fetchData, 60000)
    return () => clearInterval(timer)
  }, [fetchData])

  const lastUpdatedStr = lastUpdated
    ? lastUpdated.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
    : null

  const today = new Date().toLocaleDateString('en-US', {
    weekday: 'long', month: 'long', day: 'numeric'
  })

  // ── Derived chart data ───────────────────────────────────────────────────────

  // Registration outcome breakdown — status semantics (green/amber/red).
  const vehicleSlices = stats ? [
    { name: 'Authorized', value: stats.registrations?.accepted ?? 0, color: STATUS.good },
    { name: 'Pending',    value: stats.registrations?.pending  ?? 0, color: STATUS.warning },
    { name: 'Denied',     value: stats.registrations?.rejected ?? 0, color: STATUS.critical },
  ].filter(s => s.value > 0) : []

  // Registered categories — one distinct hue per type; disabled uses the muted
  // neutral. Validated order: blue, orange, aqua, yellow, (muted).
  const userSlices = stats ? [
    { name: 'Students',  value: stats.owners?.student   ?? 0, color: CAT.blue },
    { name: 'Employees', value: stats.owners?.employee  ?? 0, color: CAT.orange },
    { name: 'Fetchers',  value: stats.owners?.fetcher   ?? 0, color: CAT.aqua },
    { name: 'Suppliers', value: stats.suppliers?.active ?? 0, color: CAT.yellow },
    { name: 'Disabled',  value: stats.owners?.disabled  ?? 0, color: CAT.muted },
  ].filter(s => s.value > 0) : []

  // Today's entry outcomes. Authorized/Denied keep their status hues; the rest
  // take distinct categorical hues. Order is arranged so no two hard-to-separate
  // hues sit adjacent (validated: green, blue, orange, violet, yellow, red, muted).
  const scanSlices = stats ? (() => {
    const s = stats.scans?.today_by_status ?? {}
    return [
      { name: 'Authorized',   value: stats.scans?.registered_today ?? 0, color: STATUS.good },
      { name: 'Exited',       value: s.exited ?? 0,                      color: CAT.blue },
      { name: 'Wrong Day',    value: s.wrong_day ?? 0,                   color: CAT.orange },
      { name: 'Visitor',      value: stats.scans?.visitor_today ?? 0,    color: CAT.violet },
      { name: 'Unregistered', value: s.unknown ?? 0,                     color: CAT.yellow },
      { name: 'Denied',       value: s.denied ?? 0,                      color: STATUS.critical },
      { name: 'Unreadable',   value: s.unreadable ?? 0,                  color: CAT.muted },
    ].filter(sl => sl.value > 0)
  })() : []

  const VEHICLE_TYPE_LABELS = { car: 'Car', motorcycle: 'Motorcycle', ebike: 'E-Bike', van: 'Van', truck: 'Truck', bus: 'Bus' }
  // Fleet mix — six distinct hues in validated order.
  const vehicleTypeSlices = stats ? [
    { key: 'car',        color: CAT.blue },
    { key: 'motorcycle', color: CAT.orange },
    { key: 'ebike',      color: CAT.aqua },
    { key: 'van',        color: CAT.yellow },
    { key: 'truck',      color: CAT.magenta },
    { key: 'bus',        color: CAT.green },
  ].map(t => ({ name: VEHICLE_TYPE_LABELS[t.key], value: stats.vehicles?.by_type?.[t.key] ?? 0, color: t.color }))
    .filter(s => s.value > 0) : []

  const VIOLATION_TYPE_LABELS = {
    unauthorized_entry: 'Unauthorized Entry', double_parking: 'Double Parking',
    time_exceed: 'Time Exceed', no_sticker: 'No Sticker',
    expired_registration: 'Expired Registration', unauthorized: 'Unauthorized (Legacy)', other: 'Other',
  }
  // Violation types — distinct hues; the most severe (unauthorized entry) leads
  // in red, "other" uses the muted neutral. Order validated so red and orange
  // are not adjacent (red, yellow, blue, aqua, violet, magenta, muted).
  const violationTypeSlices = stats ? [
    { key: 'unauthorized_entry',   color: STATUS.critical },
    { key: 'double_parking',       color: CAT.yellow },
    { key: 'time_exceed',          color: CAT.blue },
    { key: 'no_sticker',           color: CAT.aqua },
    { key: 'expired_registration', color: CAT.violet },
    { key: 'unauthorized',         color: CAT.magenta },
    { key: 'other',                color: CAT.muted },
  ].map(t => ({ name: VIOLATION_TYPE_LABELS[t.key], value: stats.violations?.by_type?.[t.key] ?? 0, color: t.color }))
    .filter(s => s.value > 0) : []

  const kpiItems = stats ? [
    { icon: Users,        label: 'Total Users',      value: stats.users?.total,            color: '#03396C', sub: `${stats.users?.active ?? 0} active` },
    { icon: CarIcon,      label: 'Registered Vehicles', value: stats.vehicles?.total,       color: '#0F7A5A', sub: `${stats.vehicles?.authorized ?? 0} authorized` },
    { icon: ClipboardList, label: 'Pending Reviews', value: stats.registrations?.pending,  color: '#8A6B00', sub: 'awaiting approval' },
    { icon: AlertTriangle, label: 'Open Violations', value: stats.violations?.open,         color: '#C62828', sub: `${stats.violations?.fee_imposed ?? 0} with fee imposed` },
    { icon: ShieldCheck,  label: 'Visitor Passes',   value: stats.visitor_passes?.active_today, color: '#1072B3', sub: 'active today' },
    { icon: Activity,     label: "Today's Scans",    value: stats.scans?.today,             color: '#1072B3', sub: `${stats.scans?.week ?? 0} this week` },
  ] : []

  return (
    <>
      <div className="ad-page">
        <div className="ad-header">
          <div>
            <h1 className="ad-title">Dashboard Overview</h1>
            <p className="ad-subtitle">
              {today}
              {lastUpdatedStr && <span className="ad-last-updated"> · Updated at {lastUpdatedStr}</span>}
            </p>
          </div>
          <button className="ad-refresh-btn" onClick={fetchData} disabled={loading} title="Refresh">
            <RefreshCw size={14} className={loading ? 'ad-spin' : ''} />
            <span>Refresh</span>
          </button>
        </div>

        {loading && !stats ? (
          <div className="ad-loading">
            <div className="ad-spinner" />
            <p>Loading dashboard…</p>
          </div>
        ) : (
          <>
            {/* ── Compact KPI strip ──────────────────────────────────── */}
            {kpiItems.length > 0 && <KpiStrip items={kpiItems} />}

            {/* ── Charts ─────────────────────────────────────────────── */}
            <SectionLabel>Analytics</SectionLabel>
            <div className="ad-charts-grid">

              <ChartCard
                icon={PieIcon}
                title="Vehicle Registration Status"
                subtitle={`${stats?.registrations?.pending ?? 0} pending review`}
              >
                <DonutChart
                  slices={vehicleSlices}
                  centerValue={stats?.registrations?.total}
                  centerLabel="Registrations"
                />
              </ChartCard>

              <ChartCard
                icon={Users}
                title="Registered Categories"
                subtitle={`${stats?.owners?.total ?? 0} owners · ${stats?.suppliers?.active ?? 0} supplier plates`}
              >
                <DonutChart
                  slices={userSlices}
                  centerLabel="Registered"
                />
              </ChartCard>

              <ChartCard
                icon={Activity}
                title="Today's Entry Outcome"
                subtitle={`${stats?.scans?.authorized_today ?? 0} authorized · ${stats?.scans?.denied_today ?? 0} denied`}
              >
                <DonutChart
                  slices={scanSlices}
                  centerValue={stats?.scans?.today}
                  centerLabel="Today"
                />
              </ChartCard>

              <ChartCard
                icon={BarChart2}
                title="Authorized Entries by Day"
              >
                <DayBarChart
                  data={stats?.day_distribution}
                  weekTotal={stats?.scans?.authorized_week}
                />
              </ChartCard>

              <ChartCard
                icon={BarChart2}
                title="Registrations by Campus Day"
                subtitle="Accepted + pending vs daily capacity"
              >
                <DayRegistrationChart data={stats?.day_registrations} />
              </ChartCard>

              <ChartCard
                icon={CarIcon}
                title="Fleet Mix"
                subtitle={`${stats?.vehicles?.total ?? 0} registered vehicles`}
              >
                <DonutChart
                  slices={vehicleTypeSlices}
                  centerValue={stats?.vehicles?.total}
                  centerLabel="Vehicles"
                />
              </ChartCard>

              <ChartCard
                icon={AlertTriangle}
                title="Violations by Type"
                subtitle="Last 30 days"
              >
                <DonutChart
                  slices={violationTypeSlices}
                  centerLabel="Violations"
                />
              </ChartCard>

            </div>

            {/* ── Recent Activity ────────────────────────────────────── */}
            <SectionLabel>Recent Activity</SectionLabel>
            <div className="ad-activity-section">
              <div className="ad-activity-grid">
                <div className="ad-activity-card">
                  <div className="ad-activity-card-head">
                    <Shield size={13} />
                    <span>CDSO Actions</span>
                  </div>
                  <div className="ad-activity-list">
                    {stats?.recent_activity?.admin?.length > 0
                      ? stats.recent_activity.admin.map(log => <ActivityItem key={log.id} log={log} />)
                      : <EmptyActivity message="No recent CDSO activity." />}
                  </div>
                </div>

                <div className="ad-activity-card">
                  <div className="ad-activity-card-head">
                    <ShieldCheck size={13} />
                    <span>Security Personnel Actions</span>
                  </div>
                  <div className="ad-activity-list">
                    {stats?.recent_activity?.security?.length > 0
                      ? stats.recent_activity.security.map(log => <ActivityItem key={log.id} log={log} />)
                      : <EmptyActivity message="No recent security activity." />}
                  </div>
                </div>
              </div>
            </div>
          </>
        )}
      </div>
    </>
  )
}
