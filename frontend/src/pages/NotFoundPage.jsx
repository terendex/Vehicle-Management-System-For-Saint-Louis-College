import { useNavigate } from 'react-router-dom'
import { AlertCircle, Home } from 'lucide-react'
import slcLogo from '../assets/slclogo.jpg'
import './NotFoundPage.css'

export default function NotFoundPage() {
  const navigate = useNavigate()

  return (
    <div className="not-found-page">
      <header className="not-found-header" id="not-found-header">
        <div className="header-content">
          <div className="header-logo-group">
            <img src={slcLogo} alt="Saint Louis College Logo" className="header-logo" />
            <div className="header-text">
              <span className="header-title">SAINT LOUIS COLLEGE</span>
              <span className="header-subtitle">Smart Parking and Vehicle Verification System</span>
            </div>
          </div>
        </div>
      </header>

      <main className="not-found-main">
        <div className="not-found-card">
          <div className="card-icon">
            <AlertCircle size={48} color="#C62828" />
          </div>
          <h1 className="not-found-title">404</h1>
          <h2 className="not-found-subtitle">Page Not Found</h2>
          <p className="not-found-message">
            The page you are looking for doesn't exist or has been moved.
          </p>
          <button className="btn-home" onClick={() => navigate('/login')}>
            <Home size={18} />
            <span>Back to Login</span>
          </button>
        </div>
      </main>
    </div>
  )
}
