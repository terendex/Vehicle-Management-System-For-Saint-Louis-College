import { useState, useEffect, useCallback } from 'react'
import AdminLayout from '../../components/Layout/AdminLayout'
import { usersApi } from '../../api/users'
import {
  Users, Car, ShieldCheck, ClipboardList,
  Activity, Shield, RefreshCw, CheckCircle, XCircle,
  AlertTriangle, Car as CarIcon
} from 'lucide-react'
import './AdminDashboard.css'

function StatCard({ icon: Icon, label, value, sub, color }) {
  return (
    <div className="ad-card">
      <div className="ad-card-icon" style={{ background: color }}>
        <Icon size={20} color="#fff" />
      </div>
      <div className="ad-card-body">
        <span className="ad-card-label">{label}</span>
        <span className="ad-card-value">{value ?? '—'}</span>
        {sub && <span className="ad-card-sub">{sub}</span>}
      </div>
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

  return (
    <AdminLayout>
      <div className="ad-page">
        <div className="ad-header">
          <div>
            <h1 className="ad-title">Dashboard Overview</h1>
            <p className="ad-subtitle">
              Welcome back. Here's what's happening across the system.
              {lastUpdatedStr && <span className="ad-last-updated"> Updated at {lastUpdatedStr}</span>}
            </p>
          </div>
          <button className="ad-refresh-btn" onClick={fetchData} disabled={loading} title="Refresh">
            <RefreshCw size={15} className={loading ? 'ad-spin' : ''} />
            <span>Refresh</span>
          </button>
        </div>

        {loading && !stats ? (
          <div className="ad-loading">
            <div className="ad-spinner" />
            <p>Loading dashboard...</p>
          </div>
        ) : (
          <>
            {/* User & Vehicle Stats */}
            <div className="ad-stats-grid">
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

            {/* Recent Activity */}
            <div className="ad-activity-section">
              <div className="ad-section-head">
                <h2 className="ad-section-title">
                  <Activity size={18} />
                  Recent Activity
                </h2>
              </div>

              <div className="ad-activity-grid">
                <div className="ad-activity-card">
                  <div className="ad-activity-card-head">
                    <Shield size={14} />
                    <span>Admin Actions</span>
                  </div>
                  <div className="ad-activity-list">
                    {stats?.recent_activity?.admin?.length > 0 ? (
                      stats.recent_activity.admin.map((log) => (
                        <ActivityItem key={log.id} log={log} />
                      ))
                    ) : (
                      <p className="ad-activity-empty">No recent admin activity.</p>
                    )}
                  </div>
                </div>

                <div className="ad-activity-card">
                  <div className="ad-activity-card-head">
                    <ShieldCheck size={14} />
                    <span>Security Personnel Actions</span>
                  </div>
                  <div className="ad-activity-list">
                    {stats?.recent_activity?.security?.length > 0 ? (
                      stats.recent_activity.security.map((log) => (
                        <ActivityItem key={log.id} log={log} />
                      ))
                    ) : (
                      <p className="ad-activity-empty">No recent security activity.</p>
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
