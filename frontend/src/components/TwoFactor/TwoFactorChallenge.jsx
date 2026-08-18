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
  // True when these codes replace one just spent, rather than being the first
  // set handed out at enrollment — the two need different words.
  const [replaced, setReplaced] = useState(false)

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

  // Closing the tab on the backup-codes screen is the one irreversible slip in
  // this whole flow: the account is already enrolled by then, and the codes are
  // stored hashed, so they can never be shown again. Everything else here is
  // safely repeatable — abandoning the QR just means an unconfirmed device and
  // a fresh secret next time. One browser warning is worth it for the one step
  // that cannot be undone.
  useEffect(() => {
    if (!backupCodes) return undefined
    const warn = (e) => { e.preventDefault(); e.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [backupCodes])

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
        // Signing in with the last backup code returns a replacement. Show it
        // on the same screen enrollment uses, and hold the session back until
        // it has been acknowledged — handing someone a code they never saw is
        // no better than leaving them with none.
        if (data.backup_codes?.length) {
          setBackupCodes(data.backup_codes)
          setReplaced(true)
          setPendingLogin(data)
        } else {
          onComplete(data)
        }
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
    // Worded from the actual count rather than assuming one or many, so
    // changing BACKUP_CODE_COUNT on the server cannot leave the copy lying.
    const one = backupCodes.length === 1
    return (
      <div className="tfa-card">
        <div className="tfa-card-head">
          <h1 className="tfa-card-title">
            {replaced
              ? <>Here&rsquo;s your new backup code{one ? '' : 's'}</>
              : <>Save your backup code{one ? '' : 's'}</>}
          </h1>
          <p className="tfa-card-sub">
            {replaced
              ? <>You just signed in with your last one, so we&rsquo;ve issued
                {one ? ' a replacement' : ' replacements'}. Save
                {one ? ' it' : ' them'} before you continue.</>
              : <>Two-factor authentication is on. {one
                ? 'This code lets you sign in if you ever lose your phone.'
                : 'These codes let you sign in if you ever lose your phone.'}</>}
          </p>
        </div>

        <div className="tfa-codes-grid">
          {backupCodes.map((c) => <span key={c}>{c}</span>)}
        </div>

        <div className="tfa-warn">
          <AlertCircle size={15} />
          <span>
            {one
              ? <>This is the only time it is shown, and it works <strong>once</strong>.
                Write it down somewhere safe. If you close this without saving it,
                generate a new one from <strong>Account Security</strong> &mdash; you
                will not be locked out, but this exact code cannot be shown again.</>
              : <>This is the only time these are shown. Each one works once. If you
                close this without saving them, generate a new set from
                <strong> Account Security</strong> &mdash; you will not be locked out,
                but these exact codes cannot be shown again.</>}
          </span>
        </div>

        {/* Reaching this screen by *using* a code means the authenticator did
            not answer. The server cannot see a deleted app — there is no channel
            to the phone — so this is the one moment it has evidence something is
            wrong, and the only chance to say so before the person carries on and
            forgets. Without it they fall into signing in by backup code forever
            and never re-pair. */}
        {replaced && (
          <div className="tfa-warn" style={{ marginTop: 10 }}>
            <Smartphone size={15} />
            <span>
              Couldn&rsquo;t use your authenticator app? If you&rsquo;ve lost it,
              changed phone, or deleted the entry, use <strong>Pair a new
              phone</strong> in <strong>Account Security</strong> right after this
              &mdash; it shows a QR to scan, and for the next few minutes it
              won&rsquo;t ask you for another code. Otherwise you&rsquo;ll be
              signing in this way every time.
            </span>
          </div>
        )}

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
            I&rsquo;ve saved {one ? 'it' : 'them'} &mdash; continue
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
            ? 'Enter the backup code you saved when you set this up.'
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
