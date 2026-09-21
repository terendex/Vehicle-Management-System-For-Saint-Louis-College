// =============================================================================
// Sign-in, sessions and password reset.
//
// Three ways in, and they are NOT interchangeable:
//   login()        everyone — email + password, may pause for a 2FA challenge
//   guardLogin()   a guard at a gate station — also picks the gate and opens a shift
//   (badge)        a guard scanning their QR — see the note on guardQrLogin below
//
// Every method returns `data` rather than the axios response, so callers never
// touch the transport.
// =============================================================================
import api from './axios'
import { deviceToken } from './twofa'

export const authApi = {
  /**
   * Sign in with email + password.
   *
   * May resolve WITHOUT tokens: when the account carries two-factor and this
   * browser is untrusted (or the account has been quiet for a week), the server
   * answers `{ twofa_required: true, twofa_action, challenge }` and the login is
   * finished by TwoFactorChallenge. The stored device token is what lets a
   * browser already trusted inside the window skip that step.
   */
  login: async (email, password) => {
    const { data } = await api.post(
      '/auth/login/',
      { email, password },
      { headers: { 'X-Device-Token': deviceToken.get() } },
    )
    return data
  },

  // ⚠ DEAD END — this posts to a route that does not exist.
  //
  // `/accounts/guard-qr-login/` is not in the backend's URL conf; its view
  // (accounts.views.GuardQrLoginView) is defined but unrouted. The only caller
  // is authStore.guardQrLogin, which no component calls — so the whole chain
  // is dead top to bottom and nothing 404s in production.
  //
  // The LIVE badge sign-in is a different scheme entirely: qrLogin() in
  // api/scanning.js, posting qr_token + gate to /auth/qr-login/. This one
  // carries the older `SLC-GUARD:` payload. Recorded, not changed.
  guardQrLogin: async (qr_data) => {
    const { data } = await api.post('/accounts/guard-qr-login/', { qr_data })
    return data
  },

  // The gate goes WITH the credentials, not after them: the server opens the
  // shift and persists the gate in the same call that authenticates.
  guardLogin: async (email, password, gate) => {
    const { data } = await api.post('/auth/guard-login/', { email, password, gate })
    return data
  },

  // ⚠ DEAD END, same scheme as guardQrLogin above: zero call sites, and
  // `/accounts/guard-qr/{pk}/` is not routed either. The live badge-printing
  // endpoint is `users/<int:pk>/qr/` (accounts.views.GuardQRView), which is
  // admin-only and returns a different secret. Recorded, not changed.
  getGuardQrCode: async (pk) => {
    const { data } = await api.get(`/accounts/guard-qr/${pk}/`)
    return data
  },

  /** Whether a guard has a usable QR badge — controls the QR tab on the gate login page.
   *  Pass an email to check that specific guard; omit it to check if any guard qualifies. */
  guardQrAvailable: async (email) => {
    const { data } = await api.get('/accounts/guard-qr-available/', {
      params: email ? { email } : {},
    })
    return data.qr_available === true
  },

  // Note: the axios response interceptor refreshes on its own and does NOT
  // call this — it posts to the same URL directly, to avoid importing this
  // module from inside the interceptor. This one is for explicit refreshes.
  refreshToken: async (refresh) => {
    const { data } = await api.post('/auth/refresh/', { refresh })
    return data
  },

  // No call sites. Left in place: it is the natural partner to refreshToken
  // and costs nothing. (Recorded, not changed.)
  verifyToken: async (token) => {
    const { data } = await api.post('/auth/verify/', { token })
    return data
  },

  getMe: async () => {
    const { data } = await api.get('/accounts/me/')
    return data
  },

  requestPasswordReset: async (email) => {
    const { data } = await api.post('/accounts/password-reset/request/', { email })
    return data
  },

  confirmPasswordReset: async (uid, token, new_password, confirm_password) => {
    const { data } = await api.post('/accounts/password-reset/confirm/', {
      uid,
      token,
      new_password,
      confirm_password,
    })
    return data
  },
}
