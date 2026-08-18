import axios from 'axios'
import { liveStepUpToken } from './stepUpToken'

const api = axios.create({
  baseURL: '/api',
  headers: {
    'Content-Type': 'application/json',
    'ngrok-skip-browser-warning': 'true',
  },
})

// Request interceptor — attach access token, and a live step-up token if we
// hold one. Sending the step-up proactively means the second and later
// sensitive calls inside a ten-minute window never bounce off a 403 at all.
api.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('access_token')
    const hasAuthHeader = !!config.headers.Authorization
    if (token && !hasAuthHeader) {
      config.headers.Authorization = `Bearer ${token}`
    }
    if (!config.headers['X-StepUp-Token']) {
      const stepUp = liveStepUpToken()
      if (stepUp) config.headers['X-StepUp-Token'] = stepUp
    }
    return config
  },
  (error) => Promise.reject(error)
)

// Response interceptor — handle 401 refresh, and 403 step-up challenges.
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config
    const status = error.response?.status

    // ── Sensitive action needs a fresh authenticator code ──────────────────
    // The server answers `stepup_required` rather than a bare 403 so this can
    // be told apart from "you are not allowed to do this at all". Prompt for a
    // code, then replay the exact request that was held. Screens issuing the
    // call never learn any of this happened.
    if (status === 403 && error.response?.data?.stepup_required && !originalRequest._stepUpRetry) {
      originalRequest._stepUpRetry = true
      try {
        const { default: useTwofaStore } = await import('../stores/twofaStore')
        const token = await useTwofaStore.getState().requestStepUp(
          error.response.data.error || ''
        )
        originalRequest.headers['X-StepUp-Token'] = token
        return api(originalRequest)
      } catch {
        // Dismissed, or the code was never verified. The original 403 is what
        // gets surfaced, so a screen that does not care sees an ordinary
        // failure — but it is tagged first, because "you changed your mind" and
        // "you may not do this" deserve different words on screen.
        error.stepUpCancelled = true
        return Promise.reject(error)
      }
    }

    if (status === 401 && !originalRequest._retry) {
      const authEndpoints = ['/auth/login/', '/auth/refresh/', '/auth/verify/']
      if (authEndpoints.some(endpoint => originalRequest.url.includes(endpoint))) {
        return Promise.reject(error)
      }

      originalRequest._retry = true

      try {
        const refreshToken = localStorage.getItem('refresh_token')
        if (!refreshToken) throw new Error('No refresh token')

        const { data } = await api.post('/auth/refresh/', {
          refresh: refreshToken,
        })

        localStorage.setItem('access_token', data.access)
        if (data.refresh) {
          localStorage.setItem('refresh_token', data.refresh)
        }

        originalRequest.headers.Authorization = `Bearer ${data.access}`
        return api(originalRequest)
      } catch (refreshError) {
        // Let authStore.logout handle cleanup and redirect so the timer is also cleared
        const { default: useAuthStore } = await import('../stores/authStore')
        useAuthStore.getState().logout()
        return Promise.reject(refreshError)
      }
    }

    return Promise.reject(error)
  }
)

export default api
