import { create } from 'zustand'
import { authApi } from '../api/auth'
import { deviceToken } from '../api/twofa'
import { clearStepUpToken, setStepUpToken } from '../api/stepUpToken'

// Decode JWT payload without verifying signature (verification is the server's job)
function _jwtExp(token) {
  try {
    const payload = JSON.parse(atob(token.split('.')[1]))
    return typeof payload.exp === 'number' ? payload.exp * 1000 : null
  } catch {
    return null
  }
}

// How far ahead of expiry (ms) to proactively refresh
const _REFRESH_AHEAD_MS = 60_000 // 1 minute

let _refreshTimer = null

function _clearTimer() {
  if (_refreshTimer !== null) {
    clearTimeout(_refreshTimer)
    _refreshTimer = null
  }
}

function _scheduleRefresh(accessToken, refreshFn, logoutFn) {
  _clearTimer()
  const exp = _jwtExp(accessToken)
  if (!exp) return

  const delay = exp - Date.now() - _REFRESH_AHEAD_MS
  if (delay <= 0) {
    // Already expired or about to — refresh immediately
    refreshFn()
    return
  }

  _refreshTimer = setTimeout(refreshFn, delay)
}

const useAuthStore = create((set, get) => {
  const _doRefresh = async () => {
    const refreshToken = localStorage.getItem('refresh_token')
    if (!refreshToken) {
      get().logout()
      return
    }
    try {
      const { default: api } = await import('../api/axios')
      const { data } = await api.post('/auth/refresh/', { refresh: refreshToken })
      const newAccess = data.access
      localStorage.setItem('access_token', newAccess)
      if (data.refresh) localStorage.setItem('refresh_token', data.refresh)
      set({ accessToken: newAccess })
      _scheduleRefresh(newAccess, _doRefresh, get().logout)
    } catch {
      get().logout()
    }
  }

  return {
    user: JSON.parse(localStorage.getItem('user') || 'null'),
    accessToken: localStorage.getItem('access_token') || null,
    refreshToken: localStorage.getItem('refresh_token') || null,
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

    logout: (redirectTo = '/login') => {
      _clearTimer()
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

      window.location.href = redirectTo
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
    qrLogin: async (qr_token, gate) => {
      set({ isLoading: true, error: null })
      try {
        const { qrLogin: qrLoginApi } = await import('../api/scanning')
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
