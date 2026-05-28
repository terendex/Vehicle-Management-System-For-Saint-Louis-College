import { useNavigate } from 'react-router-dom'
import { HelpCircle, LogOut } from 'lucide-react'
import slcLogo from '../../assets/slclogo.jpg'
import useAuthStore from '../../stores/authStore'
import './OwnerLayout.css'

export default function OwnerLayout({ children }) {
  const { user, logout } = useAuthStore()
  const navigate = useNavigate()

  const handleLogout = () => {
    logout()
    navigate('/login')
  }

  return (
    <div className="owner-layout">
      {/* Header */}
      <header className="owner-header">
        <div className="header-logo-group">
          <img src={slcLogo} alt="Saint Louis College Logo" className="header-logo" />
          <div className="header-text">
            <span className="header-title">SAINT LOUIS COLLEGE</span>
            <span className="header-subtitle">Vehicle Management System</span>
          </div>
        </div>

        <div className="header-actions">
          <div className="user-greeting">
            Hello, {user?.full_name || 'Vehicle Owner'}
          </div>
          <button className="action-btn" title="Policy Help">
            <HelpCircle size={18} />
          </button>
          <button className="logout-btn" onClick={handleLogout}>
            <LogOut size={16} />
            <span>Log Out</span>
          </button>
        </div>
      </header>

      {/* Main Content Area */}
      <main className="owner-main">
        {children}
      </main>
    </div>
  )
}
