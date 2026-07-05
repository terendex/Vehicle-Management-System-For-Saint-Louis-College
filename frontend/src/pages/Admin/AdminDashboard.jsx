import { useState, useEffect, useCallback } from 'react'
import AdminLayout from '../../components/Layout/AdminLayout'
import { useLiveUpdates } from '../../realtime/useLiveUpdates'
import { usersApi } from '../../api/users'
import {
  Users, ShieldCheck, ClipboardList,
  Activity, Shield, RefreshCw, CheckCircle, XCircle,
  AlertTriangle, Car as CarIcon, Inbox, BarChart2, PieChart as PieIcon,
} from 'lucide-react'
import {
  PieChart, Pie, Cell, BarChart, Bar, XAxis, YAxis,
  CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts'
import './AdminDashboard.css'

// ── Shared chart config ────────────────────────────────────────────────────────

const TOOLTIP_STYLE = {
  background: '#fff',
  border: '1px solid #E8EBF4',
  borderRadius: 10,
  fontSize: 12,
  boxShadow: '0 4px 16px rgba(42,43,97,0.08)',
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
              strokeWidth={0}
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
          <CartesianGrid strokeDasharray="3 3" stroke="#F0F2F7" vertical={false} />
          <XAxis
            dataKey="day"
            tick={{ fontSize: 11.5, fill: '#8892A4', fontWeight: 600 }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            tick={{ fontSize: 11, fill: '#B0B8CC' }}
            axisLine={false}
            tickLine={false}
            allowDecimals={false}
          />
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            cursor={{ fill: '#F0F2F7', radius: 4 }}
            formatter={(val) => [val, 'Entries']}
          />
          <Bar dataKey="count" name="Entries" fill="#2A2B61" radius={[6, 6, 0, 0]} maxBarSize={48} />
        </BarChart>
      </ResponsiveContainer>
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
                background: '#F0F2F7', color: '#5A5F72', border: '1px solid #E5E8F0',
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

  // Registration outcome breakdown (accepted / pending / denied)
  const vehicleSlices = stats ? [
    { name: 'Authorized', value: stats.registrations?.accepted ?? 0, color: '#059669' },
    { name: 'Pending',    value: stats.registrations?.pending  ?? 0, color: '#D97706' },
    { name: 'Denied',     value: stats.registrations?.rejected ?? 0, color: '#DC2626' },
  ].filter(s => s.value > 0) : []

  // Registered categories: owner types + suppliers, plus a disabled slice
  const userSlices = stats ? [
    { name: 'Students',  value: stats.owners?.student   ?? 0, color: '#7C3AED' },
    { name: 'Employees', value: stats.owners?.employee  ?? 0, color: '#0D9488' },
    { name: 'Fetchers',  value: stats.owners?.fetcher   ?? 0, color: '#D97706' },
    { name: 'Suppliers', value: stats.suppliers?.active ?? 0, color: '#DB2777' },
    { name: 'Disabled',  value: stats.owners?.disabled  ?? 0, color: '#94A3B8' },
  ].filter(s => s.value > 0) : []

  const scanSlices = stats ? (() => {
    const s = stats.scans?.today_by_status ?? {}
    return [
      { name: 'Authorized',   value: stats.scans?.registered_today ?? 0, color: '#059669' },
      { name: 'Denied',       value: s.denied ?? 0,                      color: '#DC2626' },
      { name: 'Wrong Day',    value: s.wrong_day ?? 0,                   color: '#EA580C' },
      { name: 'Exited',       value: s.exited ?? 0,                      color: '#2563EB' },
      { name: 'Visitor',      value: stats.scans?.visitor_today ?? 0,    color: '#0D9488' },
      { name: 'Unregistered', value: s.unknown ?? 0,                     color: '#7C3AED' },
      { name: 'Unreadable',   value: s.unreadable ?? 0,                  color: '#8892A4' },
    ].filter(sl => sl.value > 0)
  })() : []

  const kpiItems = stats ? [
    { icon: Users,        label: 'Total Users',      value: stats.users?.total,            color: '#2A2B61', sub: `${stats.users?.active ?? 0} active` },
    { icon: CarIcon,      label: 'Registered Vehicles', value: stats.vehicles?.total,       color: '#059669', sub: `${stats.vehicles?.authorized ?? 0} authorized` },
    { icon: ClipboardList, label: 'Pending Reviews', value: stats.registrations?.pending,  color: '#D97706', sub: 'awaiting approval' },
    { icon: AlertTriangle, label: 'Open Violations', value: stats.violations?.open,         color: '#DC2626', sub: `${stats.violations?.fee_imposed ?? 0} with fee imposed` },
    { icon: ShieldCheck,  label: 'Visitor Passes',   value: stats.visitor_passes?.active_today, color: '#2563EB', sub: 'active today' },
    { icon: Activity,     label: "Today's Scans",    value: stats.scans?.today,             color: '#7C3AED', sub: `${stats.scans?.week ?? 0} this week` },
  ] : []

  return (
    <AdminLayout>
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

            </div>

            {/* ── Recent Activity ────────────────────────────────────── */}
            <SectionLabel>Recent Activity</SectionLabel>
            <div className="ad-activity-section">
              <div className="ad-activity-grid">
                <div className="ad-activity-card">
                  <div className="ad-activity-card-head">
                    <Shield size={13} />
                    <span>Admin Actions</span>
                  </div>
                  <div className="ad-activity-list">
                    {stats?.recent_activity?.admin?.length > 0
                      ? stats.recent_activity.admin.map(log => <ActivityItem key={log.id} log={log} />)
                      : <EmptyActivity message="No recent admin activity." />}
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
    </AdminLayout>
  )
}
