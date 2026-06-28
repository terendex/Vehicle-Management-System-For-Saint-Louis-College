import { useState, useEffect, useCallback } from 'react'
import AdminLayout from '../../components/Layout/AdminLayout'
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

function ActivityItem({ log }) {
  const time = new Date(log.created_at).toLocaleString('en-US', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
  })
  return (
    <div className="ad-activity-item">
      <div className="ad-activity-dot" />
      <div className="ad-activity-content">
        <span className="ad-activity-text">
          <strong>{log.actor_name || 'Unknown'}</strong> — {log.action_label || log.action}
        </span>
        {log.target_name && (
          <span className="ad-activity-target">Target: {log.target_name}</span>
        )}
        {log.details && (
          <span className="ad-activity-details">{log.details}</span>
        )}
        <span className="ad-activity-time">{time}</span>
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

  const lastUpdatedStr = lastUpdated
    ? lastUpdated.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
    : null

  const today = new Date().toLocaleDateString('en-US', {
    weekday: 'long', month: 'long', day: 'numeric'
  })

  // ── Derived chart data ───────────────────────────────────────────────────────

  const vehicleSlices = stats ? [
    { name: 'Authorized',   value: stats.vehicles?.authorized   ?? 0, color: '#059669' },
    { name: 'Unauthorized', value: stats.vehicles?.unauthorized ?? 0, color: '#DC2626' },
    { name: 'Pending',
      value: Math.max(0, (stats.vehicles?.total ?? 0) - (stats.vehicles?.authorized ?? 0) - (stats.vehicles?.unauthorized ?? 0)),
      color: '#D97706' },
  ].filter(s => s.value > 0) : []

  const userSlices = stats ? [
    { name: 'Vehicle Owners', value: stats.users?.vehicle_owner ?? 0, color: '#7C3AED' },
    { name: 'Security',       value: stats.users?.security      ?? 0, color: '#2563EB' },
    { name: 'Admin',
      value: Math.max(0, (stats.users?.total ?? 0) - (stats.users?.security ?? 0) - (stats.users?.vehicle_owner ?? 0)),
      color: '#2A2B61' },
  ].filter(s => s.value > 0) : []

  const scanSlices = stats ? (() => {
    const auth   = stats.scans?.authorized_today ?? 0
    const denied = stats.scans?.denied_today     ?? 0
    const other  = Math.max(0, (stats.scans?.today ?? 0) - auth - denied)
    return [
      { name: 'Authorized', value: auth,   color: '#059669' },
      { name: 'Denied',     value: denied, color: '#DC2626' },
      { name: 'Other',      value: other,  color: '#8892A4' },
    ].filter(s => s.value > 0)
  })() : []

  const kpiItems = stats ? [
    { icon: Users,        label: 'Total Users',      value: stats.users?.total,            color: '#2A2B61', sub: `${stats.users?.active ?? 0} active` },
    { icon: CarIcon,      label: 'Registered Vehicles', value: stats.vehicles?.total,       color: '#059669', sub: `${stats.vehicles?.authorized ?? 0} authorized` },
    { icon: AlertTriangle, label: 'Unauthorized',    value: stats.vehicles?.unauthorized,   color: '#DC2626', sub: 'need clearance' },
    { icon: ClipboardList, label: 'Pending Reviews', value: stats.registrations?.pending,  color: '#D97706', sub: 'awaiting approval' },
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
                  centerValue={stats?.vehicles?.total}
                  centerLabel="Vehicles"
                />
              </ChartCard>

              <ChartCard
                icon={Users}
                title="User Roles Breakdown"
                subtitle={`${stats?.users?.active ?? 0} active · ${stats?.users?.disabled ?? 0} disabled`}
              >
                <DonutChart
                  slices={userSlices}
                  centerValue={stats?.users?.total}
                  centerLabel="Users"
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
                  weekTotal={stats?.scans?.week}
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
