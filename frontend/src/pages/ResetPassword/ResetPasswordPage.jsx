import { useState, useMemo } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Eye, EyeOff, KeyRound, CheckCircle, AlertCircle, ArrowLeft } from 'lucide-react'
import { authApi } from '../../api/auth'
import slcLogo from '../../assets/slclogo.jpg'
import '../Login/LoginPage.css'
import './ResetPasswordPage.css'

function strengthCheck(pw) {
  return {
    length:    pw.length >= 8,
    upper:     /[A-Z]/.test(pw),
    lower:     /[a-z]/.test(pw),
    number:    /[0-9]/.test(pw),
    special:   /[!@#$%^&*()_+\-=\[\]{};'"\\|,.<>/?]/.test(pw),
  }
}

function StrengthBar({ password }) {
  const checks = useMemo(() => strengthCheck(password), [password])
  const score  = Object.values(checks).filter(Boolean).length

  const colors = ['#e2e6ee', '#DC2626', '#F97316', '#EAB308', '#16A34A', '#15803D']
  const labels = ['', 'Very Weak', 'Weak', 'Fair', 'Strong', 'Very Strong']

  if (!password) return null

  return (
    <div className="rp-strength">
      <div className="rp-strength-bars">
        {[1, 2, 3, 4, 5].map((i) => (
          <div
            key={i}
            className="rp-strength-bar"
            style={{ background: i <= score ? colors[score] : '#e2e6ee' }}
          />
        ))}
      </div>
      <span className="rp-strength-label" style={{ color: colors[score] }}>
        {labels[score]}
      </span>

      <ul className="rp-rules">
        {[
          [checks.length,  'At least 8 characters'],
          [checks.upper,   'One uppercase letter'],
          [checks.lower,   'One lowercase letter'],
          [checks.number,  'One number'],
          [checks.special, 'One special character'],
        ].map(([ok, text]) => (
          <li key={text} className={ok ? 'rp-rule ok' : 'rp-rule'}>
            <span className="rp-rule-dot" />
            {text}
          </li>
        ))}
      </ul>
    </div>
  )
}

export default function ResetPasswordPage() {
  const navigate      = useNavigate()
  const [params]      = useSearchParams()
  const uid           = params.get('uid')   || ''
  const token         = params.get('token') || ''

  const [newPassword, setNewPassword]       = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [showNew, setShowNew]               = useState(false)
  const [showConfirm, setShowConfirm]       = useState(false)
  const [isLoading, setLoading]             = useState(false)
  const [success, setSuccess]               = useState(false)
  const [error, setError]                   = useState('')
  const [fieldErrors, setFieldErrors]       = useState([])

  const checks    = useMemo(() => strengthCheck(newPassword), [newPassword])
  const allValid  = Object.values(checks).every(Boolean)
  const matches   = newPassword && confirmPassword && newPassword === confirmPassword

  const invalidLink = !uid || !token

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setFieldErrors([])

    if (!matches) {
      setError('Passwords do not match.')
      return
    }
    if (!allValid) {
      setError('Please meet all password requirements.')
      return
    }

    setLoading(true)
    try {
      await authApi.confirmPasswordReset(uid, token, newPassword, confirmPassword)
      setSuccess(true)
    } catch (err) {
      const data = err?.response?.data
      if (data?.errors && Array.isArray(data.errors)) {
        setFieldErrors(data.errors)
      } else {
        setError(data?.error || 'Something went wrong. Please try again.')
      }
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

          {/* Invalid / missing token in URL */}
          {invalidLink ? (
            <div style={{ textAlign: 'center', padding: '8px 0 16px' }}>
              <div style={{
                width: 56, height: 56, borderRadius: '50%',
                background: 'linear-gradient(135deg,#DC2626,#F87171)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                margin: '0 auto 16px',
              }}>
                <AlertCircle size={26} color="#fff" />
              </div>
              <h2 className="card-title">Invalid Reset Link</h2>
              <p className="card-subtitle" style={{ marginBottom: 24 }}>
                This reset link is missing required information. Please request a new one.
              </p>
              <button className="login-button" onClick={() => navigate('/forgot-password')}>
                <div className="button-content">
                  <ArrowLeft size={17} />
                  <span>Request New Link</span>
                </div>
              </button>
            </div>

          /* Success state */
          ) : success ? (
            <div style={{ textAlign: 'center', padding: '8px 0 16px' }}>
              <div style={{
                width: 60, height: 60, borderRadius: '50%',
                background: 'linear-gradient(135deg,#2A2B61,#4a4b8e)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                margin: '0 auto 18px',
              }}>
                <CheckCircle size={28} color="#fff" />
              </div>
              <h2 className="card-title">Password Updated</h2>
              <p className="card-subtitle" style={{ marginBottom: 24 }}>
                Your password has been reset successfully. You can now log in with your new password.
              </p>
              <button className="login-button" onClick={() => navigate('/login')}>
                <div className="button-content">
                  <KeyRound size={17} />
                  <span>Go to Login</span>
                </div>
              </button>
            </div>

          /* Reset form */
          ) : (
            <>
              <div className="card-header">
                <h1 className="card-title">Set New Password</h1>
                <p className="card-subtitle">Choose a strong password for your account.</p>
              </div>

              {error && (
                <div className="error-alert" role="alert">
                  <AlertCircle size={16} />
                  <span>{error}</span>
                </div>
              )}

              {fieldErrors.length > 0 && (
                <div className="error-alert" role="alert" style={{ flexDirection: 'column', alignItems: 'flex-start', gap: 4 }}>
                  {fieldErrors.map((e) => (
                    <div key={e} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <AlertCircle size={14} style={{ flexShrink: 0 }} />
                      <span>{e}</span>
                    </div>
                  ))}
                </div>
              )}

              <form onSubmit={handleSubmit} className="login-form">
                {/* New Password */}
                <div className="form-group">
                  <label className="form-label" htmlFor="rp-new">
                    New Password <span className="required">*</span>
                  </label>
                  <div className="input-wrapper">
                    <input
                      id="rp-new"
                      type={showNew ? 'text' : 'password'}
                      value={newPassword}
                      onChange={(e) => setNewPassword(e.target.value)}
                      placeholder="••••••••"
                      className="form-input"
                      required
                      autoFocus
                      autoComplete="new-password"
                    />
                    <button
                      type="button"
                      className="toggle-password"
                      onClick={() => setShowNew(!showNew)}
                      aria-label={showNew ? 'Hide password' : 'Show password'}
                    >
                      {showNew ? <EyeOff size={18} /> : <Eye size={18} />}
                    </button>
                  </div>
                  <StrengthBar password={newPassword} />
                </div>

                {/* Confirm Password */}
                <div className="form-group">
                  <label className="form-label" htmlFor="rp-confirm">
                    Confirm Password <span className="required">*</span>
                  </label>
                  <div className="input-wrapper">
                    <input
                      id="rp-confirm"
                      type={showConfirm ? 'text' : 'password'}
                      value={confirmPassword}
                      onChange={(e) => setConfirmPassword(e.target.value)}
                      placeholder="••••••••"
                      className={`form-input ${
                        confirmPassword
                          ? matches
                            ? 'rp-input-ok'
                            : 'rp-input-err'
                          : ''
                      }`}
                      required
                      autoComplete="new-password"
                    />
                    <button
                      type="button"
                      className="toggle-password"
                      onClick={() => setShowConfirm(!showConfirm)}
                      aria-label={showConfirm ? 'Hide password' : 'Show password'}
                    >
                      {showConfirm ? <EyeOff size={18} /> : <Eye size={18} />}
                    </button>
                  </div>
                  {confirmPassword && !matches && (
                    <span className="rp-mismatch">Passwords do not match</span>
                  )}
                </div>

                <button
                  type="submit"
                  className="login-button"
                  disabled={isLoading || !allValid || !matches}
                >
                  {isLoading ? (
                    <div className="button-loading">
                      <div className="spinner" />
                      <span>Saving…</span>
                    </div>
                  ) : (
                    <div className="button-content">
                      <KeyRound size={17} />
                      <span>Reset Password</span>
                    </div>
                  )}
                </button>

                <div style={{ textAlign: 'center', marginTop: 4 }}>
                  <button
                    type="button"
                    className="forgot-link"
                    style={{ background: 'none', border: 'none', cursor: 'pointer', fontFamily: 'inherit' }}
                    onClick={() => navigate('/forgot-password')}
                  >
                    <ArrowLeft size={13} style={{ verticalAlign: 'middle', marginRight: 4 }} />
                    Request a new link
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
