import { useState, useMemo } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Eye, EyeOff, KeyRound, CheckCircle, AlertCircle, ArrowLeft, ShieldCheck } from 'lucide-react'
import { authApi } from '../../api/auth'
import notify from '../../components/Feedback/notify'
import { fieldProblems } from '../../components/Feedback/formProblems'
import slcLogo from '../../assets/slclogo.jpg'
import '../Login/LoginPage.css'
// For .tfa-warn on the post-reset notice, so the warning matches the one the
// two-factor screens use rather than being restyled here.
import '../../components/TwoFactor/twofactor.css'
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

  const colors = ['#D3E1EC', '#C62828', '#E0B00C', '#D4B00E', '#12915A', '#0F7A5A']
  const labels = ['', 'Very Weak', 'Weak', 'Fair', 'Strong', 'Very Strong']

  if (!password) return null

  return (
    <div className="rp-strength">
      <div className="rp-strength-bars">
        {[1, 2, 3, 4, 5].map((i) => (
          <div
            key={i}
            className="rp-strength-bar"
            style={{ background: i <= score ? colors[score] : '#D3E1EC' }}
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
  // The server tells us whether this account will be asked for an authenticator
  // code at the next sign-in. Saying so here means the prompt is expected rather
  // than alarming — a code demanded straight after a password reset is exactly
  // what a phishing victim should be suspicious of if it arrives unannounced.
  const [twofaNext, setTwofaNext]           = useState(false)
  const [resetRole, setResetRole]           = useState(null)

  // Guards don't use /login — route them back to the guard sign-in page instead.
  const loginPath = resetRole === 'security' ? '/security/guard-login' : '/login'

  const checks    = useMemo(() => strengthCheck(newPassword), [newPassword])
  const allValid  = Object.values(checks).every(Boolean)
  const matches   = newPassword && confirmPassword && newPassword === confirmPassword

  const invalidLink = !uid || !token

  const handleSubmit = async (e) => {
    e.preventDefault()
    // The form carries noValidate, so the browser's own bubble is gone and
    // its complaints have to be re-raised here.
    if (await notify.validation(fieldProblems(e.currentTarget))) return

    // The strength checklist under the field is a live guide, not a verdict —
    // what is actually wrong at submit time is stated here, as one dialog.
    const problems = []
    if (!allValid) {
      const unmet = [
        [checks.length,  'At least 8 characters'],
        [checks.upper,   'One uppercase letter'],
        [checks.lower,   'One lowercase letter'],
        [checks.number,  'One number'],
        [checks.special, 'One special character'],
      ].filter(([ok]) => !ok).map(([, text]) => text)
      problems.push(...unmet)
    }
    if (!matches) problems.push('The two passwords do not match.')
    if (await notify.validation(problems, { title: 'Password not accepted' })) return

    setLoading(true)
    try {
      const data = await authApi.confirmPasswordReset(uid, token, newPassword, confirmPassword)
      setResetRole(data?.role || null)
      setTwofaNext(!!data?.twofa_required_next_login)
      setSuccess(true)
    } catch (err) {
      const data = err?.response?.data
      if (data?.errors && Array.isArray(data.errors)) {
        notify.error('The password was rejected:', {
          title: 'Password not accepted',
          details: data.errors,
        })
      } else {
        notify.error(data?.error || 'Something went wrong. Please try again.', {
          title: 'Reset failed',
        })
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
              <span className="header-subtitle">Smart Parking and Vehicle Verification System</span>
            </div>
          </div>
        </div>
      </header>

      <main className="login-main">
        <div className="login-card">

          {/* Invalid / missing token in URL */}
          {invalidLink ? (
            <div className="rp-state-body">
              <div className="rp-icon-red">
                <AlertCircle size={26} color="#fff" />
              </div>
              <h2 className="card-title">Invalid Reset Link</h2>
              <p className="card-subtitle rp-state-subtitle">
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
            <div className="rp-state-body">
              <div className="rp-icon-navy">
                <CheckCircle size={28} color="#fff" />
              </div>
              <h2 className="card-title">Password Updated</h2>
              <p className="card-subtitle rp-state-subtitle">
                Your password has been reset successfully. You can now log in with your new password.
              </p>
              {twofaNext && (
                <div className="tfa-warn" style={{ margin: '0 0 18px', textAlign: 'left' }}>
                  <ShieldCheck size={15} />
                  <span>
                    For your security, you&rsquo;ll be asked for a code from your
                    authenticator app the next time you sign in &mdash; even on a
                    device you&rsquo;ve used before.
                  </span>
                </div>
              )}
              <button className="login-button" onClick={() => navigate(loginPath)}>
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

              <form onSubmit={handleSubmit} className="login-form" noValidate>
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
                  disabled={isLoading}
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

                <div className="rp-back-row">
                  <button
                    type="button"
                    className="forgot-link rp-back-btn"
                    onClick={() => navigate('/forgot-password')}
                  >
                    <ArrowLeft size={13} />
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
