import { useState, useEffect } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import {
  ShieldCheck,
  ClipboardList,
  ParkingCircle,
  LogOut,
  MapPin,
  Clock,
} from 'lucide-react'
import slcLogo from '../../assets/slclogo.jpg'
import useAuthStore from '../../stores/authStore'
import { getCurrentShifts } from '../../api/scanning'
import './AdminLayout.css'
import './SecurityLayout.css'

const GATE_LABELS = { gate1: 'Gate 1', gate4: 'Gate 4' }

function useCurrentShift(gate) {
  const [shift, setShift] = useState(null)
  useEffect(() => {
    if (!gate) return
    getCurrentShifts()
      .then(r => setShift(r.data?.[gate] ?? null))
      .catch(() => {})
  }, [gate])
  return shift
}

function shiftDuration(clockedInAt) {
  if (!clockedInAt) return null
  const mins = Math.floor((Date.now() - new Date(clockedInAt).getTime()) / 60000)
  if (mins < 60) return `${mins}m`
  return `${Math.floor(mins / 60)}h ${mins % 60}m`
}

export default function SecurityLayout({ children, fillHeight = false }) {
  const { user, logout } = useAuthStore()
  const navigate = useNavigate()
  const gate      = user?.gate_assignment
  const gateLabel = GATE_LABELS[gate] || gate || 'Gate'
  const shift     = useCurrentShift(gate)

  const handleLogout = () => {
    logout()
    navigate('/login')
  }

  const navItems = [
    { name: 'Entry Management', path: '/security/entries', icon: <ShieldCheck size={18} /> },
    { name: 'Parking',          path: '/security/parking', icon: <ParkingCircle size={18} /> },
    { name: 'Audit Log',        path: '/security/audit',   icon: <ClipboardList size={18} /> },
  ]

  return (
    <div className="admin-layout security-layout">

      {/* Sidebar */}
      <aside className="admin-sidebar">
        <div className="sidebar-brand">
          <img src={slcLogo} alt="SLC Logo" className="brand-logo" />
          <div>
            <span className="brand-text">SLC Security</span>
            {gate && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginTop: 2 }}>
                <MapPin size={10} style={{ color: '#10b981', flexShrink: 0 }} />
                <span style={{ fontSize: 10, color: '#10b981', fontWeight: 600, letterSpacing: 0.3 }}>
                  {gateLabel}
                </span>
              </div>
            )}
          </div>
        </div>

        <nav className="sidebar-nav">
          {navItems.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
              end={item.path === '/security'}
            >
              {item.icon}
              <span>{item.name}</span>
            </NavLink>
          ))}
        </nav>

        <div className="sidebar-footer">
          <div className="user-profile">
            <div className="user-avatar">
              {user?.full_name ? user.full_name.charAt(0).toUpperCase() : 'S'}
            </div>
            <div className="user-info">
              <span className="user-name">{user?.full_name || 'Security Guard'}</span>
              {shift?.clocked_in_at ? (
                <span className="user-role" style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
                  <Clock size={9} />
                  On duty · {shiftDuration(shift.clocked_in_at)}
                </span>
              ) : (
                <span className="user-role">{gateLabel} · Security</span>
              )}
            </div>
          </div>
          <div className="footer-actions">
            <button className="logout-btn" onClick={handleLogout}>
              <LogOut size={16} />
              <span>Log Out</span>
            </button>
          </div>
        </div>
      </aside>

      {/* Main Column */}
      <main className="admin-main">
        <div className={`admin-content${fillHeight ? ' admin-content--fill' : ''}`}>
          {children}
        </div>
      </main>
    </div>
  )
}
