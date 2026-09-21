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
// Module scope, not store state, and that is deliberate: a promise is not
// something React should re-render on. Cleared in the .finally() below, so a
// dismissed prompt does not wedge every later request behind a settled promise.
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
  /** null until asked: does this account owe a code for sensitive actions?
   *  Cached for the session so `ensureStepUp` costs one request, not one per
   *  action. Guards and anyone not yet enrolled answer false. */
  stepUpApplies: null,

  /** A token we still believe is good, or '' — checked before prompting. */
  liveToken: () => liveStepUpToken(),

  /**
   * Ask for the code BEFORE the work, not after.
   *
   * The 403-driven flow in the axios interceptor is the safety net and still
   * covers everything, but on its own it means filling in a whole password form
   * and only then being interrupted for a code. Calling this first flips the
   * order: verify, then let the person start.
   *
   * Resolves with a token (or '' when the account owes no code, e.g. a guard).
   * Rejects only if the prompt is dismissed, which callers treat as "cancelled"
   * and simply abandon the action.
   */
  ensureStepUp: async (reason) => {
    const existing = get().liveToken()
    if (existing) return existing               // already inside the window: no prompt, no request

    // `null` means "never asked", which is NOT the same as false — hence the
    // explicit comparison rather than a truthiness test. Asked once per
    // session and cached, so a screen with six protected buttons costs one
    // status call rather than six.
    let applies = get().stepUpApplies
    if (applies === null) {
      try {
        const st = await twofaApi.status()
        applies = !!(st.applicable && st.confirmed)
      } catch {
        // Can't tell — say no and let the interceptor's 403 handle it rather
        // than demanding a code from someone who may not even have one.
        applies = false
      }
      set({ stepUpApplies: applies })
    }
    if (!applies) return ''

    return get().requestStepUp(reason)
  },

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
    if (existing) return Promise.resolve(existing)   // wrapped, so every caller gets a promise whichever branch answers
    if (inFlight) return inFlight               // THE dedupe: three simultaneous requests share one dialog and one code

    // The promise is created but never resolved HERE. Its resolve/reject are
    // parked in store state and called later by submitCode/cancel, which is
    // what lets a React dialog drive an async function it knows nothing about.
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
      inFlight = null                           // released on BOTH settle paths, so a cancelled prompt does not block the next one
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

      set({ expiresAt, prompting: false, submitting: false, error: '' })
      // The token goes to whoever was waiting — typically the axios
      // interceptor, which then replays the request that triggered all this.
      get()._resolve?.(token)                   // optional-call: a prompt opened directly by a screen has no waiter
      set({ _resolve: null, _reject: null })    // cleared so a later cancel() cannot settle an already-settled promise
      return true
    } catch (err) {
      const message =
        err.response?.data?.error ||
        err.response?.data?.detail ||
        'That code could not be verified. Please try again.'
      // NOT rejected: a wrong code leaves the dialog open so the user can try
      // again. Only cancel() rejects, because only cancelling ends the action.
      set({ submitting: false, error: message })
      return false
    }
  },

  /** Called by the dialog when the user backs out. */
  cancel: () => {
    const reject = get()._reject                // captured BEFORE the set() clears it
    set({ prompting: false, submitting: false, error: '', _resolve: null, _reject: null })
    // Rejecting is how the axios interceptor learns to surface the original
    // 403 with `stepUpCancelled` set, rather than retrying forever.
    reject?.(new Error('step-up-cancelled'))
  },

  // Underscore-prefixed because they are plumbing, not UI state: the dialog
  // reads `prompting`/`error`/`submitting`, never these two.
  _resolve: null,
  _reject: null,
}))

export default useTwofaStore
