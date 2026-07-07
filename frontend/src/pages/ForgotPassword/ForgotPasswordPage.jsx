import { useState } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { Mail, ArrowLeft, CheckCircle, AlertCircle } from 'lucide-react'
import { authApi } from '../../api/auth'
import slcLogo from '../../assets/slclogo.jpg'
import '../Login/LoginPage.css'
import './ForgotPasswordPage.css'

export default function ForgotPasswordPage() {
  const navigate = useNavigate()
  const location = useLocation()
  // Guards land here from the guard login page, not the admin/owner login —
  // send them back to where they came from instead of the wrong login form.
  const backPath = location.state?.from === 'security' ? '/security/guard-login' : '/login'
  const [email, setEmail]       = useState('')
  const [isLoading, setLoading] = useState(false)
  // `sent` swaps the whole card from "form" to "confirmation" view instead of routing away,
  // so the user stays on this page and can immediately go back if they mistyped the email.
  const [sent, setSent]         = useState(false)
  const [error, setError]       = useState('')

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      // Normalize the email client-side (trim + lowercase) so "  User@Mail.com" and
      // "user@mail.com" are treated as the same account lookup on the backend.
      await authApi.requestPasswordReset(email.trim().toLowerCase())
      setSent(true)
    } catch (err) {
      setError(err?.response?.data?.error || 'Something went wrong. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-page">
      <header className="login-header">
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

      <main className="login-main">
        <div className="login-card">
          {sent ? (
            <div className="fp-state-body">
              <div className="fp-icon-navy">
                <CheckCircle size={28} color="#fff" />
              </div>
              <h2 className="card-title">Check your email</h2>
              <p className="card-subtitle fp-sent-subtitle">
                If an account with <strong>{email}</strong> exists, we sent a password
                reset link. Check your inbox (and spam folder) — the link expires in{' '}
                <strong>1 hour</strong>.
              </p>
              <button className="login-button" onClick={() => navigate(backPath)}>
                <div className="button-content">
                  <ArrowLeft size={17} />
                  <span>Back to Login</span>
                </div>
              </button>
            </div>
          ) : (
            <>
              <div className="card-header">
                <h1 className="card-title">Forgot Password</h1>
                <p className="card-subtitle">
                  Enter your account email and we'll send you a reset link.
                </p>
              </div>

              {error && (
                <div className="error-alert" role="alert">
                  <AlertCircle size={16} />
                  <span>{error}</span>
                </div>
              )}

              <form onSubmit={handleSubmit} className="login-form">
                <div className="form-group">
                  <label className="form-label" htmlFor="fp-email">
                    Email <span className="required">*</span>
                  </label>
                  <input
                    id="fp-email"
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="Enter your email address"
                    className="form-input"
                    required
                    autoFocus
                    autoComplete="email"
                  />
                </div>

                <button type="submit" className="login-button" disabled={isLoading}>
                  {isLoading ? (
                    <div className="button-loading">
                      <div className="spinner" />
                      <span>Sending link…</span>
                    </div>
                  ) : (
                    <div className="button-content">
                      <Mail size={17} />
                      <span>Send Reset Link</span>
                    </div>
                  )}
                </button>

                <div className="fp-back-row">
                  <button
                    type="button"
                    className="forgot-link fp-back-btn"
                    onClick={() => navigate(backPath)}
                  >
                    <ArrowLeft size={13} />
                    Back to Login
                  </button>
                </div>
              </form>
            </>
          )}
        </div>
      </main>
    </div>
  )
}
