import { useState, useEffect, useCallback } from 'react'
import './SecurityDashboard.css'
import SecurityLayout from '../../components/Layout/SecurityLayout'
import { usersApi } from '../../api/users'
import { getAccessLogs } from '../../api/scanning'
import { camerasApi } from '../../api/cameras'
import { useMultiRtspStream } from '../../hooks/useMultiRtspStream'
import {
  ScanLine, Clock, TrendingUp, ClipboardList,
  CheckCircle, XCircle, RefreshCw, Video, Wifi,
} from 'lucide-react'

function StatCard({ icon: Icon, label, value, sub, color }) {
  return (
    <div className="sd-card">
      <div className="sd-card-icon" style={{ background: color }}>
        <Icon size={20} color="#fff" />
      </div>
      <div className="sd-card-body">
        <span className="sd-card-label">{label}</span>
        <span className="sd-card-value">{value ?? '—'}</span>
        {sub && <span className="sd-card-sub">{sub}</span>}
      </div>
    </div>
  )
}

function ScanRow({ log }) {
  const time = new Date(log.scanned_at).toLocaleString('en-US', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
  })
  const statusColors = {
    authorized: '#059669',
    denied:     '#DC2626',
    wrong_day:  '#DC2626',
    pending:    '#D97706',
    unknown:    '#7C3AED',
    unreadable: '#6B7280',
  }
  const color = statusColors[log.status] || '#6B7280'

  return (
    <div className="sd-scan-item">
      <span className="sd-scan-plate">{log.plate_number || '—'}</span>
      <span className="sd-scan-status" style={{ background: color + '18', color }}>
        {log.status || 'unknown'}
      </span>
      <span className="sd-scan-time">{time}</span>
    </div>
  )
}

function CameraMonitor() {
  const token = localStorage.getItem('access_token') || ''
  const { cameras, activeCamId, setActiveCamId, activeCam, addCamera, registerCanvas } =
    useMultiRtspStream(token)

  useEffect(() => {
    camerasApi.list({ assignment: 'entry' })
      .then(cams => cams.forEach(c => addCamera(c.name, c.rtsp_url, c.gate_id)))
      .catch(() => {})
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const isLive = cameras.some(c => c.streamConnected)

  return (
    <div className="sd-cam-panel">
      <div className="sd-cam-head">
        <span className="sd-cam-label"><Video size={14} /> CCTV Monitor</span>
        <span className={`sd-live-pill ${isLive ? 'live' : cameras.length === 0 ? 'none' : 'connecting'}`}>
          <span className="sd-live-dot" />
          {cameras.length === 0 ? 'No Cameras' : isLive ? 'Live' : 'Connecting…'}
        </span>
      </div>

      <div className="sd-cam-viewport">
        {cameras.length > 0 ? (
          <>
            {cameras.map((cam, idx) => (
              <div
                key={cam.id}
                style={{
                  display: activeCamId === cam.id ? 'block' : 'none',
                  width: '100%',
                  ...(idx > 0 ? { position: 'absolute', inset: 0 } : {}),
                }}
              >
                <canvas
                  ref={el => registerCanvas(cam.id, el)}
                  style={{ width: '100%', display: 'block', background: '#000', minHeight: 300 }}
                />
              </div>
            ))}
            {activeCam && !activeCam.streamConnected && activeCam.wsActive && (
              <div className="sd-cam-overlay">
                <div className="sd-cam-spinner" />
                <p>{activeCam.statusMsg || 'Connecting…'}</p>
              </div>
            )}
            <div className="sd-cam-nametag">
              <span className={`sd-cam-dot ${activeCam?.streamConnected ? 'live' : 'wait'}`} />
              {activeCam?.name || 'Camera'}
            </div>
          </>
        ) : (
          <div className="sd-cam-empty">
            <Wifi size={36} />
            <p>No entry cameras configured.</p>
            <span>Ask your administrator to add cameras.</span>
          </div>
        )}
      </div>

      {cameras.length > 1 && (
        <div className="sd-cam-strip">
          {cameras.map(cam => (
            <button
              key={cam.id}
              className={`sd-cam-thumb ${activeCamId === cam.id ? 'active' : ''}`}
              onClick={() => setActiveCamId(cam.id)}
            >
              <span className={`sd-cam-dot ${cam.streamConnected ? 'live' : cam.wsActive ? 'wait' : 'off'}`} />
              {cam.name}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export default function SecurityDashboard() {
  const [stats, setStats]           = useState(null)
  const [recentScans, setRecentScans] = useState([])
  const [loading, setLoading]       = useState(true)

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const [statsData, logsRes] = await Promise.all([
        usersApi.getDashboardStats().catch(() => null),
        getAccessLogs({ limit: 20 }).catch(() => null),
      ])

      if (statsData) setStats(statsData)

      if (logsRes?.results) {
        setRecentScans(logsRes.results.slice(0, 15))
      } else if (Array.isArray(logsRes)) {
        setRecentScans(logsRes.slice(0, 15))
      }
    } catch {
      // ignore
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  return (
    <SecurityLayout>
      <div className="sd-page">
        <div className="sd-header">
          <div>
            <h1 className="sd-title">Security Dashboard</h1>
            <p className="sd-subtitle">Welcome back. Here's your gate activity summary.</p>
          </div>
          <button className="sd-refresh-btn" onClick={fetchData} disabled={loading} title="Refresh">
            <RefreshCw size={15} />
          </button>
        </div>

        {loading && !stats ? (
          <div className="sd-loading">
            <div className="sd-spinner" />
            <p>Loading dashboard...</p>
          </div>
        ) : (
          <>
            <div className="sd-stats-grid">
              <StatCard
                icon={ScanLine}
                label="Scans Today"
                value={stats?.scans?.today}
                sub={`${stats?.scans?.week ?? 0} this week`}
                color="#2A2B61"
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
              <StatCard
                icon={Clock}
                label="Total Scans"
                value={stats?.scans?.total}
                sub="All time"
                color="#7C3AED"
              />
              <StatCard
                icon={TrendingUp}
                label="Weekly Activity"
                value={stats?.scans?.week}
                sub="Last 7 days"
                color="#D97706"
              />
            </div>

            <CameraMonitor />

            <div className="sd-recent-card">
              <div className="sd-recent-head">
                <span className="sd-recent-title">
                  <ClipboardList size={16} />
                  Recent Scans
                </span>
                <span className="sd-recent-count">{recentScans.length}</span>
              </div>

              {recentScans.length === 0 ? (
                <p className="sd-recent-empty">No scans recorded yet. Start scanning plates to see activity here.</p>
              ) : (
                <div className="sd-scan-list">
                  {recentScans.map((log) => (
                    <ScanRow key={log.id} log={log} />
                  ))}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </SecurityLayout>
  )
}
