import { useNavigate } from 'react-router-dom'
import { Shield, LogOut, HelpCircle } from 'lucide-react'
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
      <header className="owner-header">
        <div className="header-logo-group">
          <img src={slcLogo} alt="SLC Logo" className="header-logo" />
          <div className="header-text">
            <span className="header-title">SAINT LOUIS COLLEGE</span>
            <span className="header-subtitle">Smart Parking and Vehicle Verification System</span>
          </div>
        </div>

        <div className="header-actions">
          <span className="user-greeting">
            {user?.full_name ? `Hi, ${user.full_name}` : 'Vehicle Owner'}
          </span>
          <button className="action-btn" title="Help & User Manual" onClick={() => navigate('/help')}>
            <HelpCircle size={18} />
          </button>
          <button className="action-btn" title="Privacy Policy & Terms" onClick={() => navigate('/policy')}>
            <Shield size={18} />
          </button>
          <button className="logout-btn" onClick={handleLogout}>
            <LogOut size={16} />
            <span>Log Out</span>
          </button>
        </div>
      </header>

      <main className="owner-main">
        {children}
      </main>

      <footer className="owner-footer">
        <span className="owner-footer-copy">
          &copy; {new Date().getFullYear()} Saint Louis College
        </span>
        <button className="owner-footer-policy-btn" onClick={() => navigate('/policy')}>
          <Shield size={12} />
          Privacy Policy &amp; Terms
        </button>
      </footer>
    </div>
  )
}
