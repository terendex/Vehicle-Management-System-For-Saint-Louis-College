import { useCallback, useEffect, useState } from 'react'
import {
  AlertCircle, Copy, Download, KeyRound, RefreshCw, ShieldCheck, Smartphone,
} from 'lucide-react'
import { twofaApi } from '../../api/twofa'
import CodeField from './CodeField'
import './twofactor.css'

/**
 * Self-service two-factor management, shared by the CDSO page and the owner's
 * dashboard modal so both roles get identical behaviour from one definition.
 *
 * It exists mainly for two situations that would otherwise dead-end:
 *
 *   * someone closed the tab on the backup-codes screen. They are enrolled, but
 *     holding ten codes they have never read — and only a fresh set can fix
 *     that, since the originals are stored hashed and cannot be shown again.
 *   * someone is replacing a phone while they still have the old one, which
 *     should not need an admin at all.
 *
 * Both actions are step-up protected by the server. Nothing here asks for a
 * code directly: the axios interceptor raises the prompt and replays the call.
 */
export default function SecurityPanel({ compact = false }) {
  const [status, setStatus] = useState(null)
  const [loadError, setLoadError] = useState('')

  const [mode, setMode] = useState('idle')      // idle | pairing | codes
  const [enrollment, setEnrollment] = useState(null)
  const [codes, setCodes] = useState(null)
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)

  const load = useCallback(() => {
    twofaApi.status()
      .then(setStatus)
      .catch(() => setLoadError('Could not load your security settings.'))
  }, [])

  useEffect(() => { load() }, [load])

  // Losing the codes is irreversible, so leaving the page while they are on
  // screen is worth one interruption. This is the exact accident the panel
  // exists to undo, and the cheapest place to prevent it is here.
  useEffect(() => {
    if (mode !== 'codes') return undefined
    const warn = (e) => { e.preventDefault(); e.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [mode])

  const failed = (err, fallback) => {
    if (err.stepUpCancelled) return          // they backed out of the prompt
    setError(err.response?.data?.error || err.response?.data?.detail || fallback)
  }

  const startPairing = async () => {
    setBusy(true)
    setError('')
    try {
      setEnrollment(await twofaApi.setup())
      setCode('')
      setMode('pairing')
    } catch (err) {
      failed(err, 'Could not start pairing a new device.')
    } finally { setBusy(false) }
  }

  const confirmPairing = useCallback(async (submitted) => {
    const value = (submitted || code).trim()
    if (busy || value.length !== 6) return
    setBusy(true)
    setError('')
    try {
      const data = await twofaApi.confirm(value)
      setCodes(data.backup_codes || [])
      setMode('codes')
      load()
    } catch (err) {
      failed(err, 'That code is not correct. Please try again.')
      setCode('')
    } finally { setBusy(false) }
  }, [busy, code, load])

  const regenerate = async () => {
    setBusy(true)
    setError('')
    try {
      const data = await twofaApi.regenerateBackupCodes()
      setCodes(data.backup_codes || [])
      setMode('codes')
      load()
    } catch (err) {
      failed(err, 'Could not generate new backup codes.')
    } finally { setBusy(false) }
  }

  const copyCodes = () => {
    navigator.clipboard?.writeText(codes.join('\n')).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2200)
    })
  }

  const downloadCodes = () => {
    const body = [
      'Saint Louis College — Smart Parking and Vehicle Verification System',
      `Backup codes for ${status?.email || 'your account'}`,
      `Generated ${new Date().toLocaleString()}`,
      '',
      'Each code works once. Keep them somewhere safe and private.',
      '',
      ...codes,
    ].join('\r\n')
    const url = URL.createObjectURL(new Blob([body], { type: 'text/plain' }))
    const link = document.createElement('a')
    link.href = url
    link.download = 'slc-vms-backup-codes.txt'
    link.click()
    URL.revokeObjectURL(url)
  }

  if (loadError) {
    return <div className="tfa-error"><AlertCircle size={15} /><span>{loadError}</span></div>
  }
  if (!status) {
    return <p className="tfa-hint">Loading your security settings…</p>
  }
  if (!status.applicable) {
    return (
      <div className="tfa-hint">
        Two-factor authentication does not apply to this account.
      </div>
    )
  }

  // ── Fresh codes, shown once ─────────────────────────────────────────────
  if (mode === 'codes' && codes) {
    return (
      <div className="tfa-sec">
        <h3 className="tfa-sec-title">Your new backup codes</h3>
        <p className="tfa-hint" style={{ marginTop: 0 }}>
          Any codes you had before have stopped working. Save these in their place.
        </p>

        <div className="tfa-codes-grid">
          {codes.map((c) => <span key={c}>{c}</span>)}
        </div>

        <div className="tfa-warn">
          <AlertCircle size={15} />
          <span>
            This is the only time these are shown. Each one works once. Save them
            before you close this &mdash; you can always generate a new set, but
            these exact codes cannot be shown again.
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
            onClick={() => { setCodes(null); setMode('idle') }}
          >
            I&rsquo;ve saved them &mdash; done
          </button>
        </div>
      </div>
    )
  }

  // ── Pairing a new phone ─────────────────────────────────────────────────
  if (mode === 'pairing' && enrollment) {
    return (
      <div className="tfa-sec">
        <h3 className="tfa-sec-title">Pair a new device</h3>
        <p className="tfa-hint" style={{ marginTop: 0 }}>
          Scan this in Google Authenticator on the new phone, then enter the code
          it shows. Your old device stops working as soon as you finish.
        </p>

        <div className="tfa-qr-wrap">
          <img src={enrollment.qr_code} alt="QR code for your authenticator app" />
          <div className="tfa-secret">
            <p className="tfa-secret-label">Can&rsquo;t scan? Enter this key by hand:</p>
            <code>{enrollment.secret}</code>
          </div>
        </div>

        <form onSubmit={(e) => { e.preventDefault(); confirmPairing() }}>
          <CodeField
            id="tfa-repair-code"
            value={code}
            onChange={setCode}
            onComplete={confirmPairing}
            invalid={!!error}
            disabled={busy}
          />
          {error && (
            <div className="tfa-error" role="alert">
              <AlertCircle size={15} /><span>{error}</span>
            </div>
          )}
          <div className="tfa-actions">
            <button
              type="button"
              className="tfa-btn tfa-btn-ghost"
              onClick={() => { setMode('idle'); setError('') }}
              disabled={busy}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="tfa-btn tfa-btn-primary"
              disabled={busy || code.length !== 6}
            >
              <ShieldCheck size={16} />{busy ? 'Verifying…' : 'Finish pairing'}
            </button>
          </div>
        </form>
      </div>
    )
  }

  // ── Overview ────────────────────────────────────────────────────────────
  const low = status.backup_codes_remaining <= 2

  return (
    <div className="tfa-sec">
      {!compact && <h3 className="tfa-sec-title">Two-factor authentication</h3>}

      <div className="tfa-sec-row">
        <div className={`tfa-sec-badge${status.confirmed ? ' on' : ''}`}>
          <ShieldCheck size={15} />
          {status.confirmed ? 'On' : 'Not set up'}
        </div>
        <p className="tfa-hint" style={{ margin: 0 }}>
          {status.confirmed
            ? <>You&rsquo;ll be asked for a code from your authenticator app when
              you sign in on a new device, after {status.device_trust_days} days
              away, or before changing anything sensitive.</>
            : <>You&rsquo;ll be asked to set this up the next time you sign in.</>}
        </p>
      </div>

      {status.confirmed && (
        <>
          <div className="tfa-sec-row">
            <div className={`tfa-sec-badge${low ? ' warn' : ''}`}>
              <KeyRound size={15} />
              {status.backup_codes_remaining} backup code
              {status.backup_codes_remaining === 1 ? '' : 's'} left
            </div>
            <p className="tfa-hint" style={{ margin: 0 }}>
              {status.backup_codes_remaining === 0
                ? <><strong>You have none left.</strong> If you lose your phone
                  you will need the CDSO to reset your account. Generate a new
                  set now.</>
                : low
                  ? 'Running low — generate a new set so you are not locked out.'
                  : <>Never saw your codes, or lost them? Generate a new set &mdash;
                    the old ones stop working straight away.</>}
            </p>
          </div>

          {error && (
            <div className="tfa-error" role="alert">
              <AlertCircle size={15} /><span>{error}</span>
            </div>
          )}

          <div className="tfa-actions tfa-actions-wide">
            <button
              type="button"
              className="tfa-btn tfa-btn-ghost"
              onClick={regenerate}
              disabled={busy}
            >
              <RefreshCw size={16} />New backup codes
            </button>
            <button
              type="button"
              className="tfa-btn tfa-btn-ghost"
              onClick={startPairing}
              disabled={busy}
            >
              <Smartphone size={16} />Pair a new phone
            </button>
          </div>
        </>
      )}
    </div>
  )
}
