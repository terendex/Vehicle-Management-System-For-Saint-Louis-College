import { useState, useEffect, useCallback } from 'react'
import AdminLayout from '../../components/Layout/AdminLayout'
import { usersApi } from '../../api/users'
import {
  Users, Car, ShieldCheck, ClipboardList,
  Activity, Shield, RefreshCw, CheckCircle, XCircle,
  AlertTriangle, Car as CarIcon, Inbox, BarChart2
} from 'lucide-react'
import './AdminDashboard.css'

function DayDistributionChart({ data }) {
  if (!data || data.length === 0) return null
  const max = Math.max(...data.map(d => d.count), 1)
  return (
    <div className="ad-day-chart">
      {data.map(({ day, count }) => {
        const pct = Math.round((count / max) * 100)
        return (
          <div key={day} className="ad-day-bar-col">
            <span className="ad-day-bar-count">{count}</span>
            <div className="ad-day-bar-track">
              <div className="ad-day-bar-fill" style={{ height: `${pct}%` }} />
            </div>
            <span className="ad-day-bar-label">{day}</span>
          </div>
        )
      })}
    </div>
  )
}

function StatCard({ icon: Icon, label, value, sub, color }) {
  return (
    <div className="ad-card" style={{ '--c': color, '--cl': color + '18' }}>
      <div className="ad-card-header">
        <span className="ad-card-label">{label}</span>
        <div className="ad-card-icon">
          <Icon size={15} />
        </div>
      </div>
      <span className="ad-card-value">{value ?? '—'}</span>
      {sub && <span className="ad-card-sub">{sub}</span>}
    </div>
  )
}

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

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const lastUpdatedStr = lastUpdated
    ? lastUpdated.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
    : null

  const today = new Date().toLocaleDateString('en-US', {
    weekday: 'long', month: 'long', day: 'numeric'
  })

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
            <SectionLabel>System Overview</SectionLabel>
            <div className="ad-stats-grid ad-grid-5">
              <StatCard
                icon={Users}
                label="Total Users"
                value={stats?.users?.total}
                sub={`${stats?.users?.active ?? 0} active · ${stats?.users?.disabled ?? 0} disabled`}
                color="#2A2B61"
              />
              <StatCard
                icon={ShieldCheck}
                label="Security Personnel"
                value={stats?.users?.security}
                sub={`${stats?.users?.vehicle_owner ?? 0} vehicle owners`}
                color="#059669"
              />
              <StatCard
                icon={CarIcon}
                label="Registered Vehicles"
                value={stats?.vehicles?.total}
                sub={`${stats?.vehicles?.authorized ?? 0} authorized`}
                color="#2A2B61"
              />
              <StatCard
                icon={AlertTriangle}
                label="Unauthorized Vehicles"
                value={stats?.vehicles?.unauthorized}
                sub="Not yet cleared"
                color="#DC2626"
              />
              <StatCard
                icon={ClipboardList}
                label="Pending Registrations"
                value={stats?.registrations?.pending}
                sub="Awaiting review"
                color="#D97706"
              />
            </div>

            <SectionLabel live>Today's Activity</SectionLabel>
            <div className="ad-stats-grid ad-grid-3">
              <StatCard
                icon={Activity}
                label="Scans Today"
                value={stats?.scans?.today}
                sub={`${stats?.scans?.week ?? 0} this week`}
                color="#7C3AED"
              />
              <StatCard
                icon={CheckCircle}
                label="Authorized Today"
                value={stats?.scans?.authorized_today}
                sub="Allowed entry"
                color="#059669"
              />
              <StatCard
                icon={XCircle}
                label="Denied Today"
                value={stats?.scans?.denied_today}
                sub="Blocked entry"
                color="#DC2626"
              />
            </div>

            {stats?.day_distribution?.length > 0 && (
              <>
                <SectionLabel>Authorized Entries by Day of Week</SectionLabel>
                <div className="ad-activity-section ad-day-dist-section">
                  <div className="ad-section-head">
                    <h2 className="ad-section-title">
                      <BarChart2 size={16} />
                      Vehicle Distribution — Mon to Sat
                    </h2>
                  </div>
                  <div style={{ padding: '20px 24px' }}>
                    <DayDistributionChart data={stats.day_distribution} />
                  </div>
                </div>
              </>
            )}

            <div className="ad-activity-section">
              <div className="ad-section-head">
                <h2 className="ad-section-title">
                  <Activity size={16} />
                  Recent Activity
                </h2>
              </div>

              <div className="ad-activity-grid">
                <div className="ad-activity-card">
                  <div className="ad-activity-card-head">
                    <Shield size={13} />
                    <span>Admin Actions</span>
                  </div>
                  <div className="ad-activity-list">
                    {stats?.recent_activity?.admin?.length > 0 ? (
                      stats.recent_activity.admin.map((log) => (
                        <ActivityItem key={log.id} log={log} />
                      ))
                    ) : (
                      <EmptyActivity message="No recent admin activity." />
                    )}
                  </div>
                </div>

                <div className="ad-activity-card">
                  <div className="ad-activity-card-head">
                    <ShieldCheck size={13} />
                    <span>Security Personnel Actions</span>
                  </div>
                  <div className="ad-activity-list">
                    {stats?.recent_activity?.security?.length > 0 ? (
                      stats.recent_activity.security.map((log) => (
                        <ActivityItem key={log.id} log={log} />
                      ))
                    ) : (
                      <EmptyActivity message="No recent security activity." />
                    )}
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
