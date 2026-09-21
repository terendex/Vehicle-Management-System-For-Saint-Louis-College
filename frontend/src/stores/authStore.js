// =============================================================================
// WHO IS SIGNED IN — the single source of truth for the whole app.
//
// Three things live here, and they are easy to confuse:
//
//   1. the session itself      tokens + user, mirrored into localStorage so a
//                              reload does not sign anybody out
//   2. a refresh TIMER         one per app, at module scope, firing a minute
//                              before the access token expires
//   3. where an expiry GOES    /login, or nowhere at all on a public page
//
// (2) is why this file holds module-scope state instead of putting everything
// in the store: a timer is not app state, and it must survive re-renders and
// be cancellable from anywhere.
//
// (3) exists because of a real bug, described at PUBLIC_PATHS below.
//
// Four sign-in paths land here — password, two-factor, guard credentials and
// guard badge. Only the first two share `_startSession`; see the note on it.
// =============================================================================
import { create } from 'zustand'
import { authApi } from '../api/auth'
import { deviceToken } from '../api/twofa'
import { clearStepUpToken, setStepUpToken } from '../api/stepUpToken'

// Pages anyone may open without an account (see the public routes in App.jsx).
//
// A session that dies while one of these is open has nothing to protect, so it
// is cleared in place rather than redirected. Redirecting was the bug: a person
// opening their emailed reset link in a browser that still held a week-old
// session watched the page jump to /login a moment later — the refresh failed,
// logout navigated away, and the token in the link was gone with the URL.
const PUBLIC_PATHS = [
  '/login', '/register', '/registration/', '/forgot-password', '/reset-password',
  '/policy', '/guide', '/security/guard-login', '/security/qr-login',
]

export function onPublicPage() {
  const path = window.location.pathname
  // Prefix match, not equality: '/reset-password' has to cover
  // '/reset-password?uid=...' and '/registration/' its child routes. The
  // ternary normalises the separator so '/login' cannot match '/loginfoo'.
  return path === '/' || PUBLIC_PATHS.some(p => path === p || path.startsWith(p.endsWith('/') ? p : `${p}/`))
}

// Where an expired session goes: back to sign-in, unless the page does not need one.
export const expiredSessionRedirect = () => (onPublicPage() ? null : '/login')

// Decode JWT payload without verifying signature (verification is the server's job)
function _jwtExp(token) {
  try {
    // [1] is the payload segment; the signature is never looked at. Safe
    // because this value only schedules a timer — a forged exp would make the
    // browser refresh at the wrong moment, and the SERVER still decides
    // whether the token is any good.
    const payload = JSON.parse(atob(token.split('.')[1]))
    return typeof payload.exp === 'number' ? payload.exp * 1000 : null   // JWT exp is seconds; JS wants ms
  } catch {
    return null                                 // malformed token: no timer, and the next 401 handles it
  }
}

// How far ahead of expiry (ms) to proactively refresh
const _REFRESH_AHEAD_MS = 60_000 // 1 minute

// ONE timer for the whole application, at module scope. A second sign-in
// replaces it rather than adding to it, which is what stops two guards' timers
// racing at a shared gate terminal.
let _refreshTimer = null

function _clearTimer() {
  if (_refreshTimer !== null) {
    clearTimeout(_refreshTimer)
    _refreshTimer = null
  }
}

function _scheduleRefresh(accessToken, refreshFn, logoutFn) {
  _clearTimer()                                 // always first, so every caller is safe to invoke without cancelling by hand
  const exp = _jwtExp(accessToken)
  if (!exp) return                              // unreadable token: leave it to the interceptor's reactive 401 path

  const delay = exp - Date.now() - _REFRESH_AHEAD_MS
  if (delay <= 0) {
    // Already expired or about to — refresh immediately
    // Happens on a reload after the laptop has been asleep, and on a token
    // that was already short-lived when it arrived.
    refreshFn()
    return
  }

  _refreshTimer = setTimeout(refreshFn, delay)
}

const useAuthStore = create((set, get) => {
  // The PROACTIVE half of session keeping. The axios interceptor is the
  // reactive half — it refreshes after a 401. Both exist: this one means a
  // long-idle screen does not have to fail a request first, which at a gate
  // terminal is the difference between a scan working and a scan retrying.
  const _doRefresh = async () => {
    const refreshToken = localStorage.getItem('refresh_token')
    if (!refreshToken) {
      get().logout(expiredSessionRedirect())    // nothing to refresh with: end it now rather than at the next request
      return
    }
    try {
      // Dynamic import for the same reason as in axios.js: that module imports
      // this one, and a static import back would be a cycle.
      const { default: api } = await import('../api/axios')
      const { data } = await api.post('/auth/refresh/', { refresh: refreshToken })
      const newAccess = data.access
      localStorage.setItem('access_token', newAccess)
      if (data.refresh) localStorage.setItem('refresh_token', data.refresh)
      set({ accessToken: newAccess })
      _scheduleRefresh(newAccess, _doRefresh, get().logout)   // re-arm from the NEW token's expiry, so the chain continues indefinitely
    } catch {
      // Any failure ends the session. A refresh token the server has rejected
      // will not start working on a retry.
      get().logout(expiredSessionRedirect())
    }
  }

  return {
    // Initial state read straight from localStorage, so a reload restores the
    // session synchronously — without this the app would flash its signed-out
    // shell on every refresh before rehydrating.
    user: JSON.parse(localStorage.getItem('user') || 'null'),   // 'null' string as the default, so JSON.parse cannot throw
    accessToken: localStorage.getItem('access_token') || null,
    refreshToken: localStorage.getItem('refresh_token') || null,
    // Presence of a token, not its validity — the server decides that. An
    // expired token here means the first request 401s and the interceptor
    // refreshes or logs out.
    isAuthenticated: !!localStorage.getItem('access_token'),
    isLoading: false,
    error: null,

    // Call once on app mount if the user is already logged in
    initAutoLogout: () => {
      const token = localStorage.getItem('access_token')
      if (token) _scheduleRefresh(token, _doRefresh, get().logout)
    },

    /**
     * Persist a login payload and start the session.
     *
     * Shared by the password path and the two-factor path so both store the
     * same things — including the `device_token` that lets this browser skip
     * the code next time, which is the whole mechanism behind the weekly rule.
     */
    // NOTE: only `login` and `completeTwoFactorLogin` call this. The three
    // guard paths below (`guardLogin`, `qrLogin`, and the dead `guardQrLogin`)
    // each repeat the same six lines inline instead — four copies in one file.
    // Not a defect today: those endpoints return neither `device_token` nor
    // `step_up_token`, so the two things this helper does beyond the copies
    // have nothing to act on. Recorded as duplication, not a bug.
    _startSession: (data) => {
      const user = data.user
      const accessToken = data.access
      const refreshToken = data.refresh

      localStorage.setItem('access_token', accessToken)
      localStorage.setItem('refresh_token', refreshToken)
      localStorage.setItem('user', JSON.stringify(user))
      deviceToken.set(data.device_token)

      // Enrollment hands back a step-up alongside the session: a code was just
      // entered, and a brand-new account is about to be pushed straight into a
      // forced password change, which is itself step-up protected. Without this
      // the user would be asked for a second code seconds after the first.
      if (data.step_up_token) {
        setStepUpToken(data.step_up_token, data.step_up_expires_in)
      }

      set({
        user,
        accessToken,
        refreshToken,
        isAuthenticated: true,
        isLoading: false,
        error: null,
      })

      _scheduleRefresh(accessToken, _doRefresh, get().logout)
      return user
    },

    /** Finish a login that was paused for a code. `data` is the verify/confirm
     *  response, which carries the same fields a direct login would have. */
    completeTwoFactorLogin: (data) => get()._startSession(data),

    /**
     * Password sign-in.
     *
     * Resolves to `{ user }` on a completed login, or `{ twofa }` when the
     * server paused it for a code — the caller renders the challenge and hands
     * the result back through `completeTwoFactorLogin`. Nothing is written to
     * localStorage in the paused case: there is no session yet.
     */
    login: async (email, password) => {
      set({ isLoading: true, error: null })
      try {
        const data = await authApi.login(email, password)

        if (data.twofa_required) {
          set({ isLoading: false, error: null })
          return { twofa: data }
        }

        return { user: get()._startSession(data) }
      } catch (error) {
        const raw =
          error.response?.data?.detail ||
          error.response?.data?.non_field_errors?.[0] ||
          'Login failed. Please check your credentials.'

        // Humanise SimpleJWT's generic "no active account" into something clear
        const message = raw === 'No active account found with the given credentials'
          ? 'Incorrect email or password.'
          : raw

        set({ isLoading: false, error: message })
        throw new Error(message)
      }
    },

    /**
     * QR-based guard login: logs out any currently active guard session and logs in the new one.
     * Called from the guard QR login page at the gate station.
     */
    // ⚠ DEAD — no component calls this. It posts to
    // /accounts/guard-qr-login/, which is not a route; its backend view is
    // unrouted too. The live badge path is `qrLogin` further down.
    // Recorded, not changed.
    guardQrLogin: async (qr_data) => {
      set({ isLoading: true, error: null })
      try {
        const data = await authApi.guardQrLogin(qr_data)

        const user = data.user
        const accessToken = data.access
        const refreshToken = data.refresh

        // Silently expire the previous session by overwriting tokens
        _clearTimer()
        localStorage.setItem('access_token', accessToken)
        localStorage.setItem('refresh_token', refreshToken)
        localStorage.setItem('user', JSON.stringify(user))

        set({
          user,
          accessToken,
          refreshToken,
          isAuthenticated: true,
          isLoading: false,
          error: null,
        })

        _scheduleRefresh(accessToken, _doRefresh, get().logout)

        return user
      } catch (error) {
        const message =
          error.response?.data?.detail ||
          'QR login failed. Please try again or contact an administrator.'
        set({ isLoading: false, error: message })
        throw new Error(message)
      }
    },

    // The single exit. Every failure path funnels here so the timer, the
    // storage and the step-up token can never be cleaned up by halves.
    logout: (redirectTo = '/login') => {
      _clearTimer()                             // first, so a timer cannot fire mid-teardown and re-arm the session
      localStorage.removeItem('access_token')
      localStorage.removeItem('refresh_token')
      localStorage.removeItem('user')
      // Any sudo-mode authority dies with the session. The device token is
      // deliberately kept: "remember this browser" is meant to outlive a
      // sign-out, and it is worthless without the password anyway.
      clearStepUpToken()

      set({
        user: null,
        accessToken: null,
        refreshToken: null,
        isAuthenticated: false,
        error: null,
      })

      // null: clear the session but stay put (an expired session on a public page).
      if (redirectTo) window.location.href = redirectTo
    },

    /** Guard email + password login at the gate station — replaces the current session. */
    guardLogin: async (email, password, gate) => {
      set({ isLoading: true, error: null })
      try {
        const data = await authApi.guardLogin(email, password, gate)

        const user         = data.user
        const accessToken  = data.access
        const refreshToken = data.refresh

        _clearTimer()
        localStorage.setItem('access_token',  accessToken)
        localStorage.setItem('refresh_token', refreshToken)
        localStorage.setItem('user', JSON.stringify(user))

        set({ user, accessToken, refreshToken, isAuthenticated: true, isLoading: false, error: null })
        _scheduleRefresh(accessToken, _doRefresh, get().logout)

        return user
      } catch (error) {
        const message =
          error.response?.data?.error ||
          error.response?.data?.detail ||
          'Login failed. Please check your credentials.'
        set({ isLoading: false, error: message })
        throw new Error(message)
      }
    },

    /** Guard QR scan login — replaces the current session with the scanned guard's session. */
    // THE live guard badge sign-in — the one a gate terminal actually uses.
    // Not to be confused with `guardQrLogin` above, which is the dead
    // `SLC-GUARD:` scheme. This one sends qr_token + gate to /auth/qr-login/.
    qrLogin: async (qr_token, gate) => {
      set({ isLoading: true, error: null })
      try {
        const { qrLogin: qrLoginApi } = await import('../api/scanning')   // renamed on import to avoid shadowing this action
        const { data } = await qrLoginApi(qr_token, gate)

        const user         = data.user
        const accessToken  = data.access
        const refreshToken = data.refresh

        localStorage.setItem('access_token',  accessToken)
        localStorage.setItem('refresh_token', refreshToken)
        localStorage.setItem('user', JSON.stringify(user))

        set({ user, accessToken, refreshToken, isAuthenticated: true, isLoading: false, error: null })
        _scheduleRefresh(accessToken, _doRefresh, get().logout)

        return user
      } catch (error) {
        const message =
          error.response?.data?.error ||
          error.response?.data?.detail ||
          'QR scan failed. Please try again.'
        set({ isLoading: false, error: message })
        throw new Error(message)
      }
    },

    /** Keep the cached display name in step with the registration record.
     *
     *  The name in `user` comes from the JWT claims at login, so an owner whose
     *  name change CDSO has just approved would keep being greeted by the old
     *  one until their next sign-in — while the record right below the greeting
     *  shows the new one. Patched locally, the way clearMustChangePassword
     *  below does, rather than forcing a token refresh: the claim is only ever
     *  used for display. */
    syncDisplayName: (fullName) => {
      set((state) => {
        if (!state.user || !fullName || state.user.full_name === fullName) return state
        const updatedUser = { ...state.user, full_name: fullName }
        localStorage.setItem('user', JSON.stringify(updatedUser))
        return { user: updatedUser }
      })
    },

    /** Called after a successful password change to clear the must_change_password flag in local state. */
    clearMustChangePassword: () => {
      set((state) => {
        const updatedUser = { ...state.user, must_change_password: false }
        localStorage.setItem('user', JSON.stringify(updatedUser))
        return { user: updatedUser }
      })
    },

    clearError: () => set({ error: null }),
  }
})

export default useAuthStore
