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

  guardQrLogin: async (qr_data) => {
    const { data } = await api.post('/accounts/guard-qr-login/', { qr_data })
    return data
  },

  guardLogin: async (email, password, gate) => {
    const { data } = await api.post('/auth/guard-login/', { email, password, gate })
    return data
  },

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

  refreshToken: async (refresh) => {
    const { data } = await api.post('/auth/refresh/', { refresh })
    return data
  },

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
