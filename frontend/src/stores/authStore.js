import { create } from 'zustand'
import { authApi } from '../api/auth'

const useAuthStore = create((set, get) => ({
  user: JSON.parse(localStorage.getItem('user') || 'null'),
  accessToken: localStorage.getItem('access_token') || null,
  refreshToken: localStorage.getItem('refresh_token') || null,
  isAuthenticated: !!localStorage.getItem('access_token'),
  isLoading: false,
  error: null,

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

      return user
    } catch (error) {
      const message =
        error.response?.data?.detail ||
        error.response?.data?.non_field_errors?.[0] ||
        'Login failed. Please check your credentials.'

      set({ isLoading: false, error: message })
      throw new Error(message)
    }
  },

  logout: () => {
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
}))

export default useAuthStore
