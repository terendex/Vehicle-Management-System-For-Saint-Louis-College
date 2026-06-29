import { NavLink, useNavigate, useParams } from 'react-router-dom'
import {
  ShieldCheck,
  ClipboardList,
  ParkingCircle,
  AlertTriangle,
  LogOut,
  QrCode,
  DoorOpen
} from 'lucide-react'
import slcLogo from '../../assets/slclogo.jpg'
import useAuthStore from '../../stores/authStore'
import './AdminLayout.css' // Reusing the exact same layout styles

export default function SecurityLayout({ children, fillHeight = false }) {
  const { user, logout } = useAuthStore()
  const navigate = useNavigate()
  const { gate } = useParams()

  const entryPath = gate ? `/security/gate/${gate}/entries` : '/security/entries'

  const navItems = [
    { name: gate ? `Entry – Gate ${gate}` : 'Entry Management', path: entryPath, icon: <ShieldCheck size={18} /> },
    { name: 'Parking', path: '/security/parking', icon: <ParkingCircle size={18} /> },
    { name: 'Violations Issued', path: '/security/violations', icon: <AlertTriangle size={18} /> },
    { name: 'Audit Log', path: '/security/audit', icon: <ClipboardList size={18} /> },
  ]

  return (
    <div className="admin-layout">

      {/* Sidebar */}
      <aside className="admin-sidebar">
        <div className="sidebar-brand">
          <img src={slcLogo} alt="SLC Logo" className="brand-logo" />
          <span className="brand-text">SLC Security</span>
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
              {['1', '2'].map(g => (
                <NavLink
                  key={g}
                  to={`/security/gate/${g}/entries`}
                  className={({ isActive }) =>
                    `nav-item${isActive ? ' active' : ''}`
                  }
                  style={{ flex: 1, justifyContent: 'center', padding: '6px 8px', fontSize: 12 }}
                >
                  <DoorOpen size={14} />
                  Gate {g}
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
              <span className="user-role">{user?.user_code || 'Security Personnel'}</span>
            </div>
          </div>
          <div className="footer-actions">
            <button
              className="action-btn"
              title="Guard QR Login Page"
              onClick={() => navigate('/guard-login')}
            >
              <QrCode size={18} />
            </button>
            <button className="logout-btn" onClick={() => {
              // Guards return to the QR login page rather than the main /login form
              localStorage.removeItem('access_token')
              localStorage.removeItem('refresh_token')
              localStorage.removeItem('user')
              window.location.href = '/guard-login'
            }}>
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
