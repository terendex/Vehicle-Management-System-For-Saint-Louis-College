import { useState, useEffect } from 'react'
import AdminLayout from '../../components/Layout/AdminLayout'
import { usersApi } from '../../api/users'
import { registrationApi } from '../../api/registration'
import { getVehicleTypeAccess } from '../../api/vehicles'
import {
  Users, Car, ShieldCheck, FileSliders,
  ClipboardList, UserPlus, UserMinus, UserCheck,
  Activity, TrendingUp, Clock, Shield, Car as CarIcon
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
        <span className="ad-card-value">{value}</span>
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
  const [recentActivity, setRecentActivity] = useState([])

  useEffect(() => {
    let cancelled = false
    const fetchData = async () => {
      setLoading(true)
      try {
        const [statsRes, logsRes] = await Promise.all([
          usersApi.getAuditLogStats().catch(() => null),
          usersApi.getAuditLogs({ page_size: 20 }).catch(() => null),
        ])

        if (cancelled) return

        if (statsRes?.data) setStats(statsRes.data)

        if (logsRes?.data?.results) {
          setRecentActivity(logsRes.data.results.slice(0, 15))
        } else if (Array.isArray(logsRes?.data)) {
          setRecentActivity(logsRes.data.slice(0, 15))
        }
      } catch {
        // ignore
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    fetchData()
    return () => { cancelled = true }
  }, [])

  return (
    <AdminLayout>
      <div className="ad-page">
        <div className="ad-header">
          <div>
            <h1 className="ad-title">Dashboard Overview</h1>
            <p className="ad-subtitle">Welcome back. Here's what's happening across the system.</p>
          </div>
        </div>

        {loading ? (
          <div className="ad-loading">
            <div className="ad-spinner" />
            <p>Loading dashboard...</p>
          </div>
        ) : (
          <>
            {/* Stat Cards */}
            <div className="ad-stats-grid">
              <StatCard
                icon={Users}
                label="Total Users"
                value={stats?.users?.total ?? '—'}
                sub={`${stats?.users?.active ?? 0} active, ${stats?.users?.disabled ?? 0} disabled`}
                color="#2A2B61"
              />
              <StatCard
                icon={ShieldCheck}
                label="Security Personnel"
                value={stats?.users?.security ?? '—'}
                sub="Gate staff"
                color="#059669"
              />
              <StatCard
                icon={CarIcon}
                label="Registered Vehicles"
                value={stats?.vehicles?.total ?? '—'}
                sub={`${stats?.vehicles?.authorized ?? 0} authorized`}
                color="#2A2B61"
              />
              <StatCard
                icon={ClipboardList}
                label="Pending Registrations"
                value={stats?.registrations?.pending ?? '—'}
                sub="Awaiting review"
                color="#D97706"
              />
              <StatCard
                icon={Activity}
                label="Scans Today"
                value={stats?.scans?.today ?? '—'}
                sub={`${stats?.scans?.week ?? 0} this week`}
                color="#7C3AED"
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
                {/* Admin Activity */}
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

                {/* Security Activity */}
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
