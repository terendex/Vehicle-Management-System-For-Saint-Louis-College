import { create } from 'zustand'
import { twofaApi } from '../api/twofa'
import { clearStepUpToken, liveStepUpToken, setStepUpToken } from '../api/stepUpToken'

/**
 * "Sudo mode" state for sensitive actions.
 *
 * The point of this store is that no screen has to know 2FA exists. The axios
 * response interceptor catches the server's `stepup_required` 403, calls
 * `requestStepUp()`, waits for the user to type a code, then replays the
 * original request with the resulting token. System Settings, Rule Constraints,
 * Backup/Restore and Change Password are therefore untouched by this feature —
 * they issue the same calls they always did, and are simply held mid-flight the
 * first time in each ten-minute window.
 *
 * The token is kept in memory only. Persisting it would outlive the tab and
 * hand a walk-up attacker the very window the step-up exists to close.
 */

// One shared promise while a prompt is open, so a screen that fires three
// requests at once opens one dialog and replays all three with its token —
// rather than stacking three dialogs and asking for three codes.
let inFlight = null

const useTwofaStore = create((set, get) => ({
  /** Epoch ms at which the current token stops being accepted. Mirrored into
   *  state only so the UI can show a countdown; the token itself lives in
   *  api/stepUpToken so the axios interceptor can reach it without a cycle. */
  expiresAt: 0,
  /** Set while the code dialog is open. */
  prompting: false,
  /** Bumped on every open. Used as the dialog's React key so each prompt gets
   *  a brand-new component with empty fields — a stale code from the previous
   *  prompt is always expired, and pre-filling it would just burn one of the
   *  five attempts before lockout. */
  promptId: 0,
  /** Message explaining which action triggered the prompt. */
  promptReason: '',
  submitting: false,
  error: '',
  /** Set when a spent backup code was replaced. The held request waits until
   *  the user acknowledges the new one — issuing a code without showing it is
   *  exactly the dead end backup codes exist to prevent. */
  newBackupCodes: null,

  /** A token we still believe is good, or '' — checked before prompting. */
  liveToken: () => liveStepUpToken(),

  clear: () => {
    clearStepUpToken()
    set({ expiresAt: 0 })
  },

  /**
   * Resolve with a usable step-up token, opening the code dialog if needed.
   * Rejects if the user dismisses the dialog, which the caller surfaces as a
   * cancelled action rather than an error.
   */
  requestStepUp: (reason = '') => {
    const existing = get().liveToken()
    if (existing) return Promise.resolve(existing)
    if (inFlight) return inFlight

    inFlight = new Promise((resolve, reject) => {
      set((state) => ({
        prompting: true,
        promptId: state.promptId + 1,
        promptReason: reason,
        error: '',
        submitting: false,
        _resolve: resolve,
        _reject: reject,
      }))
    }).finally(() => {
      inFlight = null
    })

    return inFlight
  },

  /** Called by the dialog when the user submits a code. */
  submitCode: async ({ code, backupCode }) => {
    set({ submitting: true, error: '' })
    try {
      const data = await twofaApi.stepUp({ code, backupCode })
      const token = data.step_up_token
      const expiresAt = setStepUpToken(token, data.expires_in)

      if (data.backup_codes?.length) {
        // Keep the dialog open on the new code. The original request is still
        // held; it goes through the moment they acknowledge.
        set({
          expiresAt,
          submitting: false,
          error: '',
          newBackupCodes: data.backup_codes,
          _pendingToken: token,
        })
        return true
      }

      set({ expiresAt, prompting: false, submitting: false, error: '' })
      get()._resolve?.(token)
      set({ _resolve: null, _reject: null })
      return true
    } catch (err) {
      const message =
        err.response?.data?.error ||
        err.response?.data?.detail ||
        'That code could not be verified. Please try again.'
      set({ submitting: false, error: message })
      return false
    }
  },

  /** Called once the replacement backup code has been noted down. Releases
   *  the request that has been waiting behind the dialog. */
  acknowledgeBackupCodes: () => {
    const token = get()._pendingToken
    set({ newBackupCodes: null, _pendingToken: '', prompting: false })
    get()._resolve?.(token)
    set({ _resolve: null, _reject: null })
  },

  /** Called by the dialog when the user backs out. */
  cancel: () => {
    const reject = get()._reject
    set({ prompting: false, submitting: false, error: '', newBackupCodes: null,
          _pendingToken: '', _resolve: null, _reject: null })
    reject?.(new Error('step-up-cancelled'))
  },

  _resolve: null,
  _reject: null,
  _pendingToken: '',
}))

export default useTwofaStore
