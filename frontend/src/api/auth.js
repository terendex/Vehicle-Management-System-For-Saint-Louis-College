import api from './axios'

export const authApi = {
  login: async (email, password) => {
    const { data } = await api.post('/auth/login/', { email, password })
    return data
  },

  guardQrLogin: async (qr_data) => {
    const { data } = await api.post('/accounts/guard-qr-login/', { qr_data })
    return data
  },

  getGuardQrCode: async (pk) => {
    const { data } = await api.get(`/accounts/guard-qr/${pk}/`)
    return data
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
