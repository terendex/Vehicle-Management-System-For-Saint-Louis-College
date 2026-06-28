import { create } from 'zustand'
import { authApi } from '../api/auth'

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

    login: async (email, password) => {
      set({ isLoading: true, error: null })
      try {
        const data = await authApi.login(email, password)

        const user = data.user
        const accessToken = data.access
        const refreshToken = data.refresh

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

    logout: () => {
      _clearTimer()
      localStorage.removeItem('access_token')
      localStorage.removeItem('refresh_token')
      localStorage.removeItem('user')

      set({
        user: null,
        accessToken: null,
        refreshToken: null,
        isAuthenticated: false,
        error: null,
      })

      window.location.href = '/login'
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
