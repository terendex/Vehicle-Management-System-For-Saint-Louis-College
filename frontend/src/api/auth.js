import axios from 'axios'
import { API_BASE_URL } from './axios'

const authHttp = axios.create({
  baseURL: API_BASE_URL,
  headers: { 'Content-Type': 'application/json' },
})

export const authApi = {
  login: async (email, password) => {
    console.log('[authApi.login] POST /api/auth/login/', { email })
    try {
      const { data } = await authHttp.post('/api/auth/login/', { email, password })
      console.log('[authApi.login] success keys:', Object.keys(data))
      return data
    } catch (err) {
      console.log('[authApi.login] error status:', err.response?.status, 'data:', err.response?.data)
      throw err
    }
  },

  refreshToken: async (refresh) => {
    const { data } = await authHttp.post('/api/auth/refresh/', { refresh })
    return data
  },

  verifyToken: async (token) => {
    const { data } = await authHttp.post('/api/auth/verify/', { token })
    return data
  },

  getMe: async () => {
    const { data } = await authHttp.get('/api/accounts/me/')
    return data
  },
}
