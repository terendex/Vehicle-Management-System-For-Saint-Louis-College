import api from './axios'

export const registrationApi = {
  // Generate a new QR token
  generateToken: async (registrantType, expiresAt) => {
    const { data } = await api.post('/api/vehicles/tokens/generate/', {
      registrant_type: registrantType,
      expires_at: expiresAt,
    })
    return data
  },

  // List all tokens
  listTokens: async () => {
    const { data } = await api.get('/api/vehicles/tokens/')
    return data
  },

  // Toggle token active status
  toggleToken: async (id) => {
    const { data } = await api.post(`/api/vehicles/tokens/${id}/toggle/`)
    return data
  },

  // Delete token
  deleteToken: async (id) => {
    const { data } = await api.delete(`/api/vehicles/tokens/${id}/`)
    return data
  },

  // Clear expired/used tokens
  clearTokens: async () => {
    const { data } = await api.delete('/api/vehicles/tokens/clear/')
    return data
  },

  // Validate a token (public)
  validateToken: async (token) => {
    const { data } = await api.get(`/api/vehicles/register/validate-token/${token}/`)
    return data
  },

  // Submit a registration (public)
  submitRegistration: async (token, registrationData) => {
    const { data } = await api.post('/api/vehicles/register/submit/', {
      token,
      ...registrationData
    })
    return data
  },

  // Get pending registrations (Admin)
  getPendingRegistrations: async (status = 'pending') => {
    const { data } = await api.get(`/api/vehicles/registrations/pending/?status=${status}`)
    return data
  },

  // Accept a registration — returns { message, account: { user_code, system_id, temp_password, ... } }
  acceptRegistration: async (id) => {
    const { data } = await api.post(`/api/vehicles/registrations/${id}/accept/`)
    return data
  },

  // Reject a registration
  rejectRegistration: async (id, reason) => {
    const { data } = await api.post(`/api/vehicles/registrations/${id}/reject/`, { reason })
    return data
  }
}
