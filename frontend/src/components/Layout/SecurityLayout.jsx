import { NavLink, useNavigate } from 'react-router-dom'
import { 
  LayoutDashboard, 
  ShieldCheck, 
  ClipboardList,
  HelpCircle,
  LogOut
} from 'lucide-react'
import slcLogo from '../../assets/slclogo.jpg'
import useAuthStore from '../../stores/authStore'
import './AdminLayout.css' // Reusing the exact same layout styles

export default function SecurityLayout({ children }) {
  const { user, logout } = useAuthStore()
  const navigate = useNavigate()

  const handleLogout = () => {
    logout()
    navigate('/login')
  }

  const navItems = [
    { name: 'Dashboard', path: '/security', icon: <LayoutDashboard size={18} /> },
    { name: 'Entry Management', path: '/security/entries', icon: <ShieldCheck size={18} /> },
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
        </nav>
        
        <div className="sidebar-footer">
          <div className="user-profile">
            <div className="user-avatar">
              {user?.full_name ? user.full_name.charAt(0).toUpperCase() : 'S'}
            </div>
            <div className="user-info">
              <span className="user-name">{user?.full_name || 'Security Guard'}</span>
              <span className="user-role">Security Personnel</span>
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
