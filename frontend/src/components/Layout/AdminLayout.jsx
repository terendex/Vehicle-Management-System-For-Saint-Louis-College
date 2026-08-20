import { useState, useEffect } from 'react'
import { NavLink, useNavigate, useLocation } from 'react-router-dom'
import {
  LayoutDashboard,
  Car,
  Users,
  FileSliders,
  ClipboardList,
  ParkingCircle,
  Settings2,
  LogOut,
  Video,
  AlertTriangle,
  TowerControl,
  HelpCircle,
  ChevronDown,
  Briefcase,
  Truck,
  Menu,
  X,
  Shield,
  ShieldCheck,
  KeyRound,
} from 'lucide-react'
import slcLogo from '../../assets/slclogo.jpg'
import useAuthStore from '../../stores/authStore'
import SecurityPanel from '../TwoFactor/SecurityPanel'
import ChangePasswordModal from '../Auth/ChangePasswordModal'
import NotificationBell from '../NotificationBell'
import './AdminLayout.css'

function buildNavGroups(isAdmin) {
  const groups = []

  if (isAdmin) {
    groups.push({
      id: 'dashboard', type: 'link', name: 'Dashboard',
      path: '/admin', icon: <LayoutDashboard size={18} />, end: true,
    })
    groups.push({
      id: 'management', type: 'group', name: 'Management',
      icon: <Briefcase size={18} />,
      children: [
        { name: 'Vehicle Registration', path: '/admin/vehicles',   icon: <Car size={18} />   },
        { name: 'User Management',      path: '/admin/users',      icon: <Users size={18} /> },
        { name: 'Device Management',    path: '/admin/devices',    icon: <Video size={18} /> },
        { name: 'Suppliers',            path: '/admin/suppliers',  icon: <Truck size={18} /> },
      ],
    })
  }

  if (isAdmin) {
    groups.push({
      id: 'operations', type: 'group', name: 'Operations',
      icon: <TowerControl size={18} />,
      children: [
        { name: 'Operations Center', path: '/admin/entries', icon: <TowerControl size={18} /> },
        { name: 'Parking Space Management', path: '/admin/parking', icon: <ParkingCircle size={18} /> },
        { name: 'Violations', path: '/admin/violations', icon: <AlertTriangle size={18} /> },
      ],
    })
  }

  if (isAdmin) {
    groups.push({
      id: 'system', type: 'group', name: 'System',
      icon: <Settings2 size={18} />,
      children: [
        { name: 'Rule Constraints', path: '/admin/rules', icon: <FileSliders size={18} />  },
        { name: 'Audit Log',        path: '/admin/audit', icon: <ClipboardList size={18} /> },
        { name: 'System Settings', path: '/admin/settings', icon: <Settings2 size={18} /> },
      ],
    })
  }

  return groups
}

function getGroupForPath(groups, pathname) {
  for (const g of groups) {
    if (g.type === 'group' && g.children.some(c => pathname === c.path || pathname.startsWith(c.path + '/'))) {
      return g.id
    }
  }
  return null
}

export default function AdminLayout({ children, fillHeight = false }) {
  const { user, logout } = useAuthStore()
  // A pop-up rather than a route: this is about the signed-in person's own
  // phone, not a screen of the system, and it is reached from the same footer
  // as Help and Policy on every page instead of navigating away from work.
  const [securityOpen, setSecurityOpen] = useState(false)
  // A CDSO created from User Management signs in with the temporary password
  // that was emailed to them; the card below blocks the dashboard until it is
  // replaced. Opening it from the footer is the same card without the block.
  const [pwOpen, setPwOpen] = useState(false)
  const navigate = useNavigate()
  const location = useLocation()

  const isAdmin = user?.role === 'admin'
  const mustChangePassword = user?.must_change_password === true

  const navGroups = buildNavGroups(isAdmin)

  const [openGroup, setOpenGroup] = useState(() => getGroupForPath(navGroups, location.pathname))
  const [sidebarOpen, setSidebarOpen] = useState(false)

  useEffect(() => {
    const activeId = getGroupForPath(navGroups, location.pathname)
    if (activeId) setOpenGroup(activeId)
  }, [location.pathname])

  useEffect(() => {
    setSidebarOpen(false)
  }, [location.pathname])

  const toggleGroup = (id) => {
    setOpenGroup(prev => prev === id ? null : id)
  }

  const handleLogout = () => {
    logout()
    navigate('/login')
  }

  return (
    <div className="admin-layout">

      {/* Mobile menu toggle */}
      <button
        className="admin-menu-toggle"
        onClick={() => setSidebarOpen(true)}
        aria-label="Open menu"
      >
        <Menu size={22} />
      </button>

      {/* Backdrop (mobile only, shown while sidebar is open) */}
      {sidebarOpen && (
        <div className="admin-sidebar-backdrop" onClick={() => setSidebarOpen(false)} />
      )}

      {/* Sidebar */}
      <aside className={`admin-sidebar${sidebarOpen ? ' open' : ''}`}>
        <div className="sidebar-brand">
          <img src={slcLogo} alt="SLC Logo" className="brand-logo" />
          <span className="brand-text">SLC CDSO</span>
          <NotificationBell />
          <button
            className="admin-sidebar-close"
            onClick={() => setSidebarOpen(false)}
            aria-label="Close menu"
          >
            <X size={20} />
          </button>
        </div>

        <nav className="sidebar-nav">
          {navGroups.map((item) => {
            if (item.type === 'link') {
              return (
                <NavLink
                  key={item.path}
                  to={item.path}
                  end={item.end}
                  className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
                >
                  {item.icon}
                  <span>{item.name}</span>
                </NavLink>
              )
            }

            const isOpen = openGroup === item.id
            const hasActive = item.children.some(
              c => location.pathname === c.path || location.pathname.startsWith(c.path + '/')
            )

            return (
              <div key={item.id} className="nav-group">
                <button
                  className={`nav-group-header${hasActive ? ' has-active' : ''}`}
                  onClick={() => toggleGroup(item.id)}
                >
                  <span className="nav-group-header-left">
                    {item.icon}
                    <span>{item.name}</span>
                  </span>
                  <ChevronDown size={14} className={`nav-group-chevron${isOpen ? ' open' : ''}`} />
                </button>
                <div className={`nav-group-children${isOpen ? ' open' : ''}`}>
                  {item.children.map((child) => (
                    <NavLink
                      key={child.path}
                      to={child.path}
                      className={({ isActive }) => `nav-item nav-child${isActive ? ' active' : ''}`}
                    >
                      {child.icon}
                      <span>{child.name}</span>
                    </NavLink>
                  ))}
                </div>
              </div>
            )
          })}
        </nav>

        <div className="sidebar-footer">
          {/* Identity, the icon shortcuts and Log Out are one card divided by
              hairlines, so the footer reads as a single object. */}
          <div className="footer-card">
            <div className="user-profile">
              <div className="user-avatar">
                {user?.full_name ? user.full_name.charAt(0).toUpperCase() : 'A'}
              </div>
              <div className="user-info">
                <span className="user-name" title={user?.full_name || 'CDSO'}>
                  {user?.full_name || 'CDSO'}
                </span>
                <span className="user-role">CDSO</span>
              </div>
            </div>
            <div className="footer-actions">
              <button className="action-btn" title="Help & User Manual" onClick={() => navigate('/help')}>
                <HelpCircle size={17} />
                <span className="action-btn-label">Help</span>
              </button>
              <button className="action-btn" title="Privacy Policy & Terms" onClick={() => navigate('/policy')}>
                <Shield size={17} />
                <span className="action-btn-label">Policy</span>
              </button>
              <button
                className="action-btn"
                title="Account Security — authenticator app and backup code"
                onClick={() => setSecurityOpen(true)}
              >
                <ShieldCheck size={17} />
                <span className="action-btn-label">Security</span>
              </button>
            </div>
            <button
              className="footer-row-btn footer-row-btn--password"
              onClick={() => setPwOpen(true)}
              title="Change your account password"
            >
              <KeyRound size={14} />
              <span>Change Password</span>
            </button>
            <button className="footer-row-btn footer-row-btn--logout" onClick={handleLogout}>
              <LogOut size={15} />
              <span>Log Out</span>
            </button>
          </div>
        </div>
      </aside>

      {(mustChangePassword || pwOpen) && (
        <ChangePasswordModal
          forced={mustChangePassword}
          subtitle={mustChangePassword
            ? 'You are signed in with the temporary password emailed to you. Set a new one to continue.'
            : undefined}
          onCancel={mustChangePassword ? handleLogout : () => setPwOpen(false)}
          onDone={handleLogout}
        />
      )}

      {securityOpen && (
        <div className="tfa-overlay" onMouseDown={(e) => {
          if (e.target === e.currentTarget) setSecurityOpen(false)
        }}>
          <div className="tfa-dialog" style={{ maxWidth: 520 }}>
            <div className="tfa-dialog-head">
              <div className="tfa-dialog-icon"><ShieldCheck size={21} /></div>
              <div>
                <h2 className="tfa-dialog-title">Account Security</h2>
                <p className="tfa-dialog-sub">
                  Your authenticator app and backup code.
                </p>
              </div>
            </div>
            <div className="tfa-dialog-body">
              <SecurityPanel compact />
              <div className="tfa-actions">
                <button
                  type="button"
                  className="tfa-btn tfa-btn-ghost"
                  onClick={() => setSecurityOpen(false)}
                >
                  Close
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Main Column */}
      <main className="admin-main">
        <div className={`admin-content${fillHeight ? ' admin-content--fill' : ''}`}>
          {children}
        </div>
      </main>
    </div>
  )
}
