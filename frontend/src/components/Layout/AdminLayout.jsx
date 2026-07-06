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
  CalendarDays,
  Truck,
} from 'lucide-react'
import slcLogo from '../../assets/slclogo.jpg'
import useAuthStore from '../../stores/authStore'
import NotificationBell from '../NotificationBell'
import './AdminLayout.css'

function buildNavGroups(isAdmin, isCdso) {
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

  if (isAdmin || isCdso) {
    groups.push({
      id: 'operations', type: 'group', name: 'Operations',
      icon: <TowerControl size={18} />,
      children: [
        ...(isAdmin ? [{ name: 'Operations Center', path: '/admin/entries', icon: <TowerControl size={18} /> }] : []),
        { name: 'Parking',    path: '/admin/parking',    icon: <ParkingCircle size={18} /> },
        { name: 'Violations', path: '/admin/violations', icon: <AlertTriangle size={18} /> },
        { name: 'Events',     path: '/admin/events',     icon: <CalendarDays size={18} /> },
      ],
    })
  }

  if (isAdmin || isCdso) {
    groups.push({
      id: 'system', type: 'group', name: 'System',
      icon: <Settings2 size={18} />,
      children: [
        ...(isAdmin ? [
          { name: 'Rule Constraints', path: '/admin/rules', icon: <FileSliders size={18} />  },
          { name: 'Audit Log',        path: '/admin/audit', icon: <ClipboardList size={18} /> },
        ] : []),
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
  const navigate = useNavigate()
  const location = useLocation()

  const isAdmin = user?.role === 'admin'
  const isCdso  = user?.role === 'cdso'

  const navGroups = buildNavGroups(isAdmin, isCdso)

  const [openGroup, setOpenGroup] = useState(() => getGroupForPath(navGroups, location.pathname))

  useEffect(() => {
    const activeId = getGroupForPath(navGroups, location.pathname)
    if (activeId) setOpenGroup(activeId)
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

      {/* Sidebar */}
      <aside className="admin-sidebar">
        <div className="sidebar-brand">
          <img src={slcLogo} alt="SLC Logo" className="brand-logo" />
          <span className="brand-text">SLC Admin</span>
          <NotificationBell />
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
          <div className="user-profile">
            <div className="user-avatar">
              {user?.full_name ? user.full_name.charAt(0).toUpperCase() : 'A'}
            </div>
            <div className="user-info">
              <span className="user-name">{user?.full_name || 'System Admin'}</span>
              <span className="user-role">{user?.role === 'cdso' ? 'CDSO Staff' : 'Administrator'}</span>
            </div>
          </div>
          <div className="footer-actions">
            <button className="action-btn" title="Privacy Policy & Terms" onClick={() => navigate('/policy')}>
              <HelpCircle size={18} />
            </button>
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
