import { NavLink, useNavigate } from 'react-router-dom'
import {
  LayoutDashboard,
  Car,
  Users,
  ShieldCheck,
  FileSliders,
  ClipboardList,
  ParkingCircle,
  HelpCircle,
  LogOut,
  Video,
} from 'lucide-react'
import slcLogo from '../../assets/slclogo.jpg'
import useAuthStore from '../../stores/authStore'
import './AdminLayout.css'

export default function AdminLayout({ children }) {
  const { user, logout } = useAuthStore()
  const navigate = useNavigate()

  const handleLogout = () => {
    logout()
    navigate('/login')
  }

  const navItems = [
    { name: 'Dashboard', path: '/admin', icon: <LayoutDashboard size={18} /> },
    { name: 'Vehicle Registration', path: '/admin/vehicles', icon: <Car size={18} /> },
    { name: 'User Management', path: '/admin/users', icon: <Users size={18} /> },
    { name: 'Entry Management', path: '/admin/entries', icon: <ShieldCheck size={18} /> },
    { name: 'Parking', path: '/admin/parking', icon: <ParkingCircle size={18} /> },
    { name: 'Rule Constraints', path: '/admin/rules', icon: <FileSliders size={18} /> },
    { name: 'Audit Log', path: '/admin/audit', icon: <ClipboardList size={18} /> },
    { name: 'Device Management', path: '/admin/devices', icon: <Video size={18} /> },
  ]

  return (
    <div className="admin-layout">

      {/* Sidebar */}
      <aside className="admin-sidebar">
        <div className="sidebar-brand">
          <img src={slcLogo} alt="SLC Logo" className="brand-logo" />
          <span className="brand-text">SLC Admin</span>
        </div>

        <nav className="sidebar-nav">
          {navItems.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
              end={item.path === '/admin'}
            >
              {item.icon}
              <span>{item.name}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div className="user-profile">
            <div className="user-avatar">
              {user?.full_name ? user.full_name.charAt(0).toUpperCase() : 'A'}
            </div>
            <div className="user-info">
              <span className="user-name">{user?.full_name || 'System Admin'}</span>
              <span className="user-role">Administrator</span>
            </div>
          </div>
          <div className="footer-actions">
            <button className="action-btn" title="Policy Help">
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

        {/* Dynamic Page Content */}
        <div className="admin-content">
          {children}
        </div>
      </main>
    </div>
  )
}
