import api from './axios'

export const violationsApi = {
  getMyViolations: async () => {
    const { data } = await api.get('/violations/my/')
    return data
  },
}

// Admin/CDSO — full CRUD
export const getAllViolations   = ()      => api.get('/violations/')
export const releaseViolation   = (id)   => api.post(`/violations/${id}/release/`)
export const unreleaseViolation = (id)   => api.post(`/violations/${id}/unrelease/`)
export const resolveViolation   = (id)   => api.patch(`/violations/${id}/`, { is_resolved: true })
export const createViolation    = (data) => api.post('/violations/', data)
