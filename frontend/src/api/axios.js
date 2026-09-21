// =============================================================================
// THE HTTP CLIENT — every request the app makes goes through this file.
//
// One axios instance with two interceptors, and between them they handle three
// things no individual screen should have to know about:
//
//   outgoing   attach the access token, and the step-up token when one is live
//   401        refresh the session once, replay the request, or log out
//   403 + stepup_required   prompt for an authenticator code, replay, or fail
//
// The pattern in both failure paths is the same: mark the request so it can
// only be retried ONCE (_retry / _stepUpRetry), fix the cause, then re-issue
// the identical config. A screen calling api.get() sees either its data or a
// plain error — never the retry.
// =============================================================================
import axios from 'axios'
import { liveStepUpToken } from './stepUpToken'

const api = axios.create({
  baseURL: '/api',                              // relative, so the same build works on the campus LAN and on Railway
  headers: {
    'Content-Type': 'application/json',
    'ngrok-skip-browser-warning': 'true',       // suppresses ngrok's interstitial, which would otherwise arrive instead of JSON during tunnelled demos
  },
})

// Request interceptor — attach access token, and a live step-up token if we
// hold one. Sending the step-up proactively means the second and later
// sensitive calls inside a ten-minute window never bounce off a 403 at all.
api.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('access_token')
    // Read fresh from storage on EVERY request rather than captured once: the
    // refresh path below writes a new token, and a captured value would go
    // stale the moment that happened.
    const hasAuthHeader = !!config.headers.Authorization
    // An explicit header wins. The guard login screens set their own while
    // signing somebody in, and must not have it overwritten by whoever was
    // signed in before them.
    if (token && !hasAuthHeader) {
      config.headers.Authorization = `Bearer ${token}`
    }
    if (!config.headers['X-StepUp-Token']) {
      const stepUp = liveStepUpToken()          // '' once it has expired — see stepUpToken.js
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
      originalRequest._stepUpRetry = true        // set BEFORE awaiting, so a second 403 on the replay cannot loop
      try {
        // Imported dynamically, not at the top: the store imports this module,
        // and a static import back would be a cycle. The await also means the
        // store is only loaded for users who actually hit a step-up.
        const { default: useTwofaStore } = await import('../stores/twofaStore')
        // Resolves when the user enters a valid code, rejects if they dismiss
        // it. The prompt itself is the store's business, not this file's.
        const token = await useTwofaStore.getState().requestStepUp(
          error.response.data.error || ''       // the server's reason, shown in the prompt so the user knows what they are approving
        )
        originalRequest.headers['X-StepUp-Token'] = token
        return api(originalRequest)             // the SAME config, so the replay carries the original method, url and body
      } catch {
        // Dismissed, or the code was never verified. The original 403 is what
        // gets surfaced, so a screen that does not care sees an ordinary
        // failure — but it is tagged first, because "you changed your mind" and
        // "you may not do this" deserve different words on screen.
        error.stepUpCancelled = true             // a flag on the original error, so callers can tell "cancelled" from "refused"
        return Promise.reject(error)
      }
    }

    if (status === 401 && !originalRequest._retry) {
      // A 401 from a login endpoint means "those credentials are wrong", not
      // "your session expired" — there is no session to refresh yet. Letting
      // these fall through would log out and bounce the page to /login, which
      // at a gate kiosk throws the guard off the guard-login screen instead of
      // showing them the failure in place.
      // Matched by substring against the request url — these are the paths
      // where a 401 means bad credentials rather than an expired session.
      const authEndpoints = [
        '/auth/login/',
        '/auth/refresh/',
        '/auth/verify/',
        '/auth/guard-login/',
        '/auth/qr-login/',
        '/accounts/guard-qr-login/',
      ]
      if (authEndpoints.some(endpoint => originalRequest.url.includes(endpoint))) {
        return Promise.reject(error)
      }

      originalRequest._retry = true             // one refresh attempt per request; a second 401 falls through to logout

      try {
        const refreshToken = localStorage.getItem('refresh_token')
        if (!refreshToken) throw new Error('No refresh token')   // thrown, not returned, so it lands in the same catch as a failed refresh

        // Uses `api` itself, so the refresh call carries the same baseURL and
        // headers. It cannot recurse: '/auth/refresh/' is in the list above,
        // so a 401 on this call is rejected rather than retried.
        const { data } = await api.post('/auth/refresh/', {
          refresh: refreshToken,
        })

        localStorage.setItem('access_token', data.access)
        // Only when the server sent one. With refresh-token rotation on it
        // does; without it, overwriting with undefined would destroy the
        // still-valid refresh token and end the session on the next 401.
        if (data.refresh) {
          localStorage.setItem('refresh_token', data.refresh)
        }

        // Set on the held request explicitly — the request interceptor already
        // ran for it, so it would otherwise replay with the expired token.
        originalRequest.headers.Authorization = `Bearer ${data.access}`
        return api(originalRequest)
      } catch (refreshError) {
        // Let authStore.logout handle cleanup and redirect so the timer is also cleared
        const { default: useAuthStore, expiredSessionRedirect } = await import('../stores/authStore')
        useAuthStore.getState().logout(expiredSessionRedirect())
        return Promise.reject(refreshError)
      }
    }

    return Promise.reject(error)
  }
)

export default api
