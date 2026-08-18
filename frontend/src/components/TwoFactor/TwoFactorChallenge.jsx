import { useCallback, useEffect, useState } from 'react'
import {
  AlertCircle, ArrowLeft, Copy, Download, KeyRound, ShieldCheck, Smartphone,
} from 'lucide-react'
import { twofaApi } from '../../api/twofa'
import CodeField from './CodeField'
import './twofactor.css'

/**
 * The second half of a login the server paused.
 *
 * Two shapes, driven by what the login endpoint said it wanted:
 *   'setup'  — no authenticator paired yet (the first-login case). Shows the QR
 *              to scan, takes a code to prove the pairing worked, then the
 *              backup codes before letting go.
 *   'verify' — already paired, but this browser is new or the account has been
 *              quiet for a week. Just takes a code.
 *
 * `onComplete` receives the full login payload — tokens included — so the
 * caller can start the session without a second trip through the password form.
 */
export default function TwoFactorChallenge({
  challenge,
  action,
  email,
  onComplete,
  onCancel,
}) {
  const isSetup = action === 'setup'

  const [enrollment, setEnrollment] = useState(null)   // { qr_code, secret, ... }
  const [backupCodes, setBackupCodes] = useState(null) // shown once, after confirm
  const [pendingLogin, setPendingLogin] = useState(null)

  const [code, setCode] = useState('')
  const [backupCode, setBackupCode] = useState('')
  const [useBackup, setUseBackup] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)

  // Fetch the QR as soon as an enrollment challenge lands. Nothing is set
  // synchronously here — the "still loading" state is derived below instead,
  // which keeps this effect from triggering a cascading render.
  useEffect(() => {
    if (!isSetup || !challenge) return undefined
    let cancelled = false
    twofaApi.setup(challenge)
      .then((data) => { if (!cancelled) setEnrollment(data) })
      .catch((err) => {
        if (!cancelled) {
          setError(err.response?.data?.error
            || 'Could not start the setup. Please sign in again.')
        }
      })
    return () => { cancelled = true }
  }, [isSetup, challenge])

  // The QR is on its way exactly while we have neither it nor a reason we
  // cannot get it — no separate flag needed.
  const loadingEnrollment = isSetup && !enrollment && !error

  const submit = useCallback(async (submitted) => {
    if (busy) return
    const value = (submitted || code).trim()
    const usingBackup = useBackup && !!backupCode.trim()
    if (!usingBackup && value.length !== 6) return

    setBusy(true)
    setError('')
    try {
      if (isSetup) {
        const data = await twofaApi.confirm(value, challenge)
        // Hold the session back until the backup codes have been seen — they
        // are shown exactly once, and navigating away loses them for good.
        setBackupCodes(data.backup_codes || [])
        setPendingLogin(data)
      } else {
        const data = await twofaApi.verify(
          challenge,
          usingBackup ? { backupCode: backupCode.trim() } : { code: value },
        )
        onComplete(data)
      }
    } catch (err) {
      setError(err.response?.data?.error
        || err.response?.data?.detail
        || 'That code is not correct. Please try again.')
      setCode('')
    } finally {
      setBusy(false)
    }
  }, [busy, code, useBackup, backupCode, isSetup, challenge, onComplete])

  const copyCodes = () => {
    navigator.clipboard?.writeText(backupCodes.join('\n')).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2200)
    })
  }

  const downloadCodes = () => {
    const body = [
      'Saint Louis College — Smart Parking and Vehicle Verification System',
      `Backup codes for ${email}`,
      `Generated ${new Date().toLocaleString()}`,
      '',
      'Each code works once. Keep them somewhere safe and private.',
      '',
      ...backupCodes,
    ].join('\r\n')
    const url = URL.createObjectURL(new Blob([body], { type: 'text/plain' }))
    const link = document.createElement('a')
    link.href = url
    link.download = 'slc-vms-backup-codes.txt'
    link.click()
    URL.revokeObjectURL(url)
  }

  // ── Backup codes, shown once before the session starts ──────────────────
  if (backupCodes) {
    return (
      <div className="tfa-card">
        <div className="tfa-card-head">
          <h1 className="tfa-card-title">Save your backup codes</h1>
          <p className="tfa-card-sub">
            Two-factor authentication is on. These codes let you sign in if you
            ever lose your phone.
          </p>
        </div>

        <div className="tfa-codes-grid">
          {backupCodes.map((c) => <span key={c}>{c}</span>)}
        </div>

        <div className="tfa-warn">
          <AlertCircle size={15} />
          <span>
            This is the only time these are shown. Each one works once. If you
            lose them and your phone, the CDSO has to reset your account.
          </span>
        </div>

        <div className="tfa-actions">
          <button type="button" className="tfa-btn tfa-btn-ghost" onClick={copyCodes}>
            <Copy size={16} />{copied ? 'Copied' : 'Copy'}
          </button>
          <button type="button" className="tfa-btn tfa-btn-ghost" onClick={downloadCodes}>
            <Download size={16} />Download
          </button>
        </div>
        <div className="tfa-actions">
          <button
            type="button"
            className="tfa-btn tfa-btn-primary"
            onClick={() => onComplete(pendingLogin)}
          >
            I&rsquo;ve saved them &mdash; continue
          </button>
        </div>
      </div>
    )
  }

  // ── Enrollment ──────────────────────────────────────────────────────────
  if (isSetup) {
    return (
      <div className="tfa-card">
        <div className="tfa-card-head">
          <h1 className="tfa-card-title">Set up two-factor authentication</h1>
          <p className="tfa-card-sub">
            Your account needs a second step at sign-in. It takes about a minute.
          </p>
        </div>

        <ol className="tfa-steps">
          <li>Install <strong>Google Authenticator</strong> on your phone
            (Authy and Microsoft Authenticator work too).</li>
          <li>Open it, tap <strong>+</strong>, and scan the code below.</li>
          <li>Enter the 6-digit code it shows to finish.</li>
        </ol>

        {enrollment ? (
          <div className="tfa-qr-wrap">
            <img src={enrollment.qr_code} alt="QR code for your authenticator app" />
            <div className="tfa-secret">
              <p className="tfa-secret-label">Can&rsquo;t scan? Enter this key by hand:</p>
              <code>{enrollment.secret}</code>
            </div>
          </div>
        ) : (
          <div className="tfa-qr-wrap">
            <Smartphone size={30} color="#5C7B92" />
            <p className="tfa-hint">
              {loadingEnrollment ? 'Preparing your code…' : 'Setup could not be started.'}
            </p>
          </div>
        )}

        <form onSubmit={(e) => { e.preventDefault(); submit() }}>
          <CodeField
            id="tfa-setup-code"
            value={code}
            onChange={setCode}
            onComplete={submit}
            invalid={!!error}
            disabled={busy || !enrollment}
            autoFocus={!!enrollment}
          />

          {error && (
            <div className="tfa-error" role="alert">
              <AlertCircle size={15} /><span>{error}</span>
            </div>
          )}

          <div className="tfa-actions">
            <button type="button" className="tfa-btn tfa-btn-ghost" onClick={onCancel}>
              <ArrowLeft size={16} />Back
            </button>
            <button
              type="submit"
              className="tfa-btn tfa-btn-primary"
              disabled={busy || !enrollment || code.length !== 6}
            >
              <ShieldCheck size={16} />{busy ? 'Verifying…' : 'Turn on'}
            </button>
          </div>
        </form>
      </div>
    )
  }

  // ── Verification ────────────────────────────────────────────────────────
  return (
    <div className="tfa-card">
      <div className="tfa-card-head">
        <h1 className="tfa-card-title">Two-step verification</h1>
        <p className="tfa-card-sub">
          {useBackup
            ? 'Enter one of the backup codes you saved when you set this up.'
            : <>Enter the 6-digit code from your authenticator app for <strong>{email}</strong>.</>}
        </p>
      </div>

      <form onSubmit={(e) => { e.preventDefault(); submit() }}>
        {useBackup ? (
          <>
            <label className="tfa-field-label" htmlFor="tfa-verify-backup">Backup code</label>
            <input
              id="tfa-verify-backup"
              className="tfa-code-input tfa-backup"
              type="text"
              autoFocus
              autoComplete="off"
              placeholder="00000-00000"
              disabled={busy}
              value={backupCode}
              onChange={(e) => setBackupCode(e.target.value)}
            />
          </>
        ) : (
          <CodeField
            id="tfa-verify-code"
            value={code}
            onChange={setCode}
            onComplete={submit}
            invalid={!!error}
            disabled={busy}
          />
        )}

        {error && (
          <div className="tfa-error" role="alert">
            <AlertCircle size={15} /><span>{error}</span>
          </div>
        )}

        <div className="tfa-actions">
          <button type="button" className="tfa-btn tfa-btn-ghost" onClick={onCancel}>
            <ArrowLeft size={16} />Back
          </button>
          <button
            type="submit"
            className="tfa-btn tfa-btn-primary"
            disabled={busy || (useBackup ? !backupCode.trim() : code.length !== 6)}
          >
            <KeyRound size={16} />{busy ? 'Verifying…' : 'Verify'}
          </button>
        </div>

        <button
          type="button"
          className="tfa-link-btn"
          onClick={() => { setUseBackup((v) => !v); setError('') }}
          disabled={busy}
        >
          {useBackup
            ? 'Use my authenticator app instead'
            : "Can't access your app? Use a backup code"}
        </button>
      </form>
    </div>
  )
}
