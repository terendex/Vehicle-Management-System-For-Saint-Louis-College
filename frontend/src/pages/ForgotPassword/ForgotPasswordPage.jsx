import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Mail, ArrowLeft, CheckCircle, AlertCircle } from 'lucide-react'
import { authApi } from '../../api/auth'
import slcLogo from '../../assets/slclogo.jpg'
import '../Login/LoginPage.css'

export default function ForgotPasswordPage() {
  const navigate = useNavigate()
  const [email, setEmail]       = useState('')
  const [isLoading, setLoading] = useState(false)
  const [sent, setSent]         = useState(false)
  const [error, setError]       = useState('')

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
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
            <div style={{ textAlign: 'center', padding: '8px 0 16px' }}>
              <div style={{
                width: 60, height: 60, borderRadius: '50%',
                background: 'linear-gradient(135deg,#2A2B61,#4a4b8e)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                margin: '0 auto 18px',
              }}>
                <CheckCircle size={28} color="#fff" />
              </div>
              <h2 className="card-title">Check your email</h2>
              <p className="card-subtitle" style={{ marginBottom: 24, lineHeight: 1.6 }}>
                If an account with <strong>{email}</strong> exists, we sent a password reset link.
                Check your inbox (and spam folder) — the link expires in <strong>1 hour</strong>.
              </p>
              <button
                className="login-button"
                onClick={() => navigate('/login')}
                style={{ marginTop: 0 }}
              >
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

                <div style={{ textAlign: 'center', marginTop: 4 }}>
                  <button
                    type="button"
                    className="forgot-link"
                    style={{ background: 'none', border: 'none', cursor: 'pointer', fontFamily: 'inherit' }}
                    onClick={() => navigate('/login')}
                  >
                    <ArrowLeft size={13} style={{ verticalAlign: 'middle', marginRight: 4 }} />
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
