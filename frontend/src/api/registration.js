import api from './axios'

export const registrationApi = {
  // ── Admin: token management (kept for legacy use) ──
  generateToken: async (registrantType, expiresAt) => {
    const { data } = await api.post('/vehicles/tokens/generate/', {
      registrant_type: registrantType,
      expires_at: expiresAt,
    })
    return data
  },
  listTokens: async () => {
    const { data } = await api.get('/vehicles/tokens/')
    return data
  },
  toggleToken: async (id) => {
    const { data } = await api.post(`/vehicles/tokens/${id}/toggle/`)
    return data
  },
  deleteToken: async (id) => {
    const { data } = await api.delete(`/vehicles/tokens/${id}/`)
    return data
  },
  clearTokens: async () => {
    const { data } = await api.delete('/vehicles/tokens/clear/')
    return data
  },

  // ── Token-based registration (legacy) ──
  validateToken: async (token) => {
    const { data } = await api.get(`/vehicles/register/validate-token/${token}/`)
    return data
  },
  submitRegistration: async (token, registrationData) => {
    const { data } = await api.post('/vehicles/register/submit/', { token, ...registrationData })
    return data
  },

  // ── Public open registration (no token required) ──
  getRegistrationStatus: async () => {
    const { data } = await api.get('/vehicles/register/status/')
    return data
  },
  getScheduleSlots: async () => {
    const { data } = await api.get('/vehicles/register/schedule-slots/')
    return data
  },
  getDepartments: async () => {
    const { data } = await api.get('/vehicles/departments/')
    return data
  },
  getPrograms: async () => {
    const { data } = await api.get('/vehicles/programs/')
    return data
  },
  submitOpenRegistration: async (registrationData) => {
    const { data } = await api.post('/vehicles/register/open/', registrationData)
    return data
  },

  // ── Admin: pending registrations ──
  getPendingRegistrations: async (status = 'pending') => {
    const { data } = await api.get(`/vehicles/registrations/pending/?status=${status}`)
    return data
  },
  // orNumber required; campusDaysOverride is an optional string[] the admin freely picks
  acceptRegistration: async (id, orNumber, campusDaysOverride) => {
    const payload = { or_number: orNumber }
    if (campusDaysOverride && campusDaysOverride.length > 0) payload.campus_days = campusDaysOverride
    const { data } = await api.post(`/vehicles/registrations/${id}/accept/`, payload)
    return data
  },
  rejectRegistration: async (id, reason) => {
    const { data } = await api.post(`/vehicles/registrations/${id}/reject/`, { reason })
    return data
  },

  // ── Parking availability ──
  getParkingAvailability: async (category) => {
    const params = category ? { category } : {}
    const { data } = await api.get('/vehicles/parking-availability/', { params })
    return data
  },
}
