import { useCallback, useEffect, useState } from 'react'
import { AlertCircle, KeyRound, ShieldCheck } from 'lucide-react'
import useTwofaStore from '../../stores/twofaStore'
import CodeField from './CodeField'
import './twofactor.css'

/**
 * The "confirm it's you" dialog for sensitive actions.
 *
 * Mounted once, at the app root. It renders nothing until the axios
 * interceptor hits a `stepup_required` 403 and asks the store for a token —
 * which is why System Settings, Rule Constraints, Backup/Restore and Change
 * Password needed no changes at all to gain a second factor. The held request
 * is replayed the moment a code checks out.
 */
export default function StepUpGate() {
  const prompting = useTwofaStore((s) => s.prompting)
  const promptId = useTwofaStore((s) => s.promptId)

  if (!prompting) return null
  // Keyed on the prompt counter so every open mounts a fresh dialog with empty
  // fields. Remounting is what clears the form here — resetting it from an
  // effect would trigger the cascading render React now warns about.
  return <StepUpDialog key={promptId} />
}

function StepUpDialog() {
  const promptReason = useTwofaStore((s) => s.promptReason)
  const submitting = useTwofaStore((s) => s.submitting)
  const error = useTwofaStore((s) => s.error)
  const submitCode = useTwofaStore((s) => s.submitCode)
  const cancel = useTwofaStore((s) => s.cancel)

  const [code, setCode] = useState('')
  const [backupCode, setBackupCode] = useState('')
  const [useBackup, setUseBackup] = useState(false)

  const submit = useCallback(
    async (submittedCode) => {
      if (submitting) return
      if (useBackup) {
        if (!backupCode.trim()) return
        await submitCode({ backupCode: backupCode.trim() })
        return
      }
      const value = submittedCode || code
      if (value.length !== 6) return
      const ok = await submitCode({ code: value })
      if (!ok) setCode('')
    },
    [submitting, useBackup, backupCode, code, submitCode]
  )

  // Escape cancels, which rejects the held request rather than leaving the
  // calling screen waiting on a promise that never settles.
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') cancel() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [cancel])

  return (
    <div
      className="tfa-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="tfa-stepup-title"
      onMouseDown={(e) => { if (e.target === e.currentTarget) cancel() }}
    >
      <div className="tfa-dialog">
        <div className="tfa-dialog-head">
          <div className="tfa-dialog-icon"><ShieldCheck size={21} /></div>
          <div>
            <h2 className="tfa-dialog-title" id="tfa-stepup-title">Confirm it&rsquo;s you</h2>
            <p className="tfa-dialog-sub">
              {promptReason || 'This action needs a code from your authenticator app.'}
            </p>
          </div>
        </div>

        <form
          className="tfa-dialog-body"
          onSubmit={(e) => { e.preventDefault(); submit() }}
        >
          {useBackup ? (
            <>
              <label className="tfa-field-label" htmlFor="tfa-stepup-backup">
                Backup code
              </label>
              <input
                id="tfa-stepup-backup"
                className="tfa-code-input tfa-backup"
                type="text"
                autoFocus
                autoComplete="off"
                placeholder="00000-00000"
                disabled={submitting}
                value={backupCode}
                onChange={(e) => setBackupCode(e.target.value)}
              />
              <p className="tfa-hint">
                Each backup code works once. Generate a new set afterwards if you are running low.
              </p>
            </>
          ) : (
            <>
              <CodeField
                id="tfa-stepup-code"
                value={code}
                onChange={setCode}
                onComplete={submit}
                invalid={!!error}
                disabled={submitting}
              />
              <p className="tfa-hint">
                Open Google Authenticator and enter the 6-digit code shown for
                Saint Louis College.
              </p>
            </>
          )}

          {error && (
            <div className="tfa-error" role="alert">
              <AlertCircle size={15} />
              <span>{error}</span>
            </div>
          )}

          <div className="tfa-actions">
            <button
              type="button"
              className="tfa-btn tfa-btn-ghost"
              onClick={cancel}
              disabled={submitting}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="tfa-btn tfa-btn-primary"
              disabled={submitting || (useBackup ? !backupCode.trim() : code.length !== 6)}
            >
              <KeyRound size={16} />
              {submitting ? 'Verifying…' : 'Verify'}
            </button>
          </div>

          <button
            type="button"
            className="tfa-link-btn"
            onClick={() => setUseBackup((v) => !v)}
            disabled={submitting}
          >
            {useBackup ? 'Use my authenticator app instead' : "Can't access your app? Use a backup code"}
          </button>
        </form>
      </div>
    </div>
  )
}
