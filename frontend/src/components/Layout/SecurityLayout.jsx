import { useState, useEffect } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import {
  ShieldCheck,
  ParkingCircle,
  LogOut,
  MapPin,
  Clock,
  DoorOpen,
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
    logout('/security/qr-login')
  }

  const entryPath = gate ? `/security/gate/${gate}/entries` : '/security/entries'

  const navItems = [
    { name: 'Entry Management', path: entryPath,           icon: <ShieldCheck size={18} /> },
    { name: 'Parking',          path: '/security/parking', icon: <ParkingCircle size={18} /> },
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

          {/* Gate selector */}
          <div style={{ padding: '8px 12px', marginTop: 6 }}>
            <span style={{ fontSize: 10, fontWeight: 700, color: '#7C80A3', textTransform: 'uppercase', letterSpacing: '.5px', display: 'block', marginBottom: 6 }}>
              Switch Gate
            </span>
            <div style={{ display: 'flex', gap: 6 }}>
              {[{ key: 'gate1', label: '1' }, { key: 'gate4', label: '4' }].map(g => (
                <NavLink
                  key={g.key}
                  to={`/security/gate/${g.key}/entries`}
                  className={({ isActive }) =>
                    `nav-item${isActive ? ' active' : ''}`
                  }
                  style={{ flex: 1, justifyContent: 'center', padding: '6px 8px', fontSize: 12 }}
                >
                  <DoorOpen size={14} />
                  Gate {g.label}
                </NavLink>
              ))}
            </div>
          </div>
        </nav>

        <div className="sidebar-footer">
          <div className="user-profile">
            {user?.photo_url ? (
              <img src={user.photo_url} alt={user.full_name} style={{ width: 34, height: 34, borderRadius: '50%', objectFit: 'cover', border: '2px solid #E2E6EE' }} />
            ) : (
              <div className="user-avatar">
                {user?.full_name ? user.full_name.charAt(0).toUpperCase() : 'S'}
              </div>
            )}
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
