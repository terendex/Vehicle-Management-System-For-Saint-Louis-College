import { useState } from 'react'
import { Eye, EyeOff, LogIn, AlertCircle } from 'lucide-react'
import useAuthStore from '../../stores/authStore'
import slcLogo from '../../assets/slclogo.jpg'
import './LoginPage.css'

export default function LoginPage() {

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [rememberMe, setRememberMe] = useState(false)
  const [showPassword, setShowPassword] = useState(false)

  const { login, isLoading, error, clearError } = useAuthStore()

  const handleSubmit = async (e) => {
    e.preventDefault()
    clearError()

    try {
      const user = await login(username, password)
      // TODO: Navigate based on user.role
      console.log('Logged in as:', user)
    } catch (err) {
      // Error is already set in the store
    }
  }

  return (
    <div className="login-page">
      {/* Top Navigation Bar */}
      <header className="login-header" id="login-header">
        <div className="header-content">
          <div className="header-logo-group">
            <img src={slcLogo} alt="Saint Louis College Logo" className="header-logo" />
            <div className="header-text">
              <span className="header-title">SAINT LOUIS COLLEGE</span>
              <span className="header-subtitle">Vehicle Management System with entry authentication</span>
            </div>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="login-main">
        <div className="login-card" id="login-card">
          {/* Card Header */}
          <div className="card-header">
            <h1 className="card-title">Account Login</h1>
            <p className="card-subtitle">Sign in to access the vehicle management system</p>
          </div>

          {/* Error Alert */}
          {error && (
            <div className="error-alert" id="login-error" role="alert">
              <AlertCircle size={16} />
              <span>{error}</span>
            </div>
          )}

          <form onSubmit={handleSubmit} className="login-form" id="login-form">

            {/* Username */}
            <div className="form-group">
              <label className="form-label" htmlFor="login-username">
                Username <span className="required">*</span>
              </label>
              <input
                id="login-username"
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="Enter your username"
                className="form-input"
                required
                autoComplete="username"
              />
            </div>

            {/* Password */}
            <div className="form-group">
              <label className="form-label" htmlFor="login-password">
                Password <span className="required">*</span>
              </label>
              <div className="input-wrapper">
                <input
                  id="login-password"
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                  className="form-input"
                  required
                  autoComplete="current-password"
                />
                <button
                  type="button"
                  className="toggle-password"
                  onClick={() => setShowPassword(!showPassword)}
                  aria-label={showPassword ? 'Hide password' : 'Show password'}
                  id="toggle-password"
                >
                  {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
                </button>
              </div>
            </div>

            {/* Remember Me & Forgot Password */}
            <div className="form-row">
              <label className="checkbox-label" id="remember-me-label">
                <div className="checkbox-wrapper">
                  <input
                    type="checkbox"
                    checked={rememberMe}
                    onChange={(e) => setRememberMe(e.target.checked)}
                    className="checkbox-input"
                    id="remember-me"
                  />
                  <div className="checkbox-custom">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="20 6 9 17 4 12" />
                    </svg>
                  </div>
                </div>
                <span className="checkbox-text">Remember Me</span>
              </label>
              <a href="#" className="forgot-link" id="forgot-password-link">
                Forgot Password
              </a>
            </div>

            {/* Login Button */}
            <button
              type="submit"
              className="login-button"
              disabled={isLoading}
              id="login-submit"
            >
              {isLoading ? (
                <div className="button-loading">
                  <div className="spinner" />
                  <span>Signing in...</span>
                </div>
              ) : (
                <div className="button-content">
                  <LogIn size={18} />
                  <span>Login</span>
                </div>
              )}
            </button>


            {/* Terms */}
            <div className="terms-row">
              <a href="#" className="terms-link">Terms</a>
              <span className="terms-divider">|</span>
              <a href="#" className="terms-link">Policy</a>
            </div>
          </form>
        </div>
      </main>
    </div>
  )
}
