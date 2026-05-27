import api from './axios'

export const authApi = {
  login: async (username, password) => {
    const { data } = await api.post('/api/auth/login/', { username, password })
    return data
  },

  refreshToken: async (refresh) => {
    const { data } = await api.post('/api/auth/refresh/', { refresh })
    return data
  },

  verifyToken: async (token) => {
    const { data } = await api.post('/api/auth/verify/', { token })
    return data
  },

  getMe: async () => {
    const { data } = await api.get('/api/accounts/me/')
    return data
  },
}
