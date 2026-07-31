import api from './axios'

export const violationsApi = {
  getMyViolations: async () => {
    const { data } = await api.get('/violations/my/')
    return data
  },
}

// Guard — violations issued by the current guard
export const getGuardViolations = (date) =>
  api.get('/violations/guard/', { params: date ? { date } : {} })

// Admin/CDSO — full CRUD
export const getAllViolations   = ()      => api.get('/violations/')
export const releaseViolation   = (id)   => api.post(`/violations/${id}/release/`)
export const unreleaseViolation = (id)   => api.post(`/violations/${id}/unrelease/`)
export const resolveViolation   = (id)   => api.patch(`/violations/${id}/`, { is_resolved: true })
export const createViolation = (data) => {
  const fd = new FormData()
  Object.entries(data).forEach(([k, v]) => { if (v !== null && v !== undefined) fd.append(k, v) })
  return api.post('/violations/', fd, { headers: { 'Content-Type': 'multipart/form-data' } })
}

// Admin/CDSO — branded Violations report (format: 'pdf' | 'excel')
export const exportViolationsReport = (format, params = {}) =>
  api.get(`/violations/report/${format}/`, { params, responseType: 'blob' }).then(r => r.data)

// CDSO workflow for 3rd-offense violations
export const issueCDSOReport  = (id)              => api.post(`/violations/${id}/issue-report/`)
export const clearViolation   = (id, officialReceipt) =>
  api.post(`/violations/${id}/clear/`, { official_receipt: officialReceipt })

// Void a violation as a false alarm. Unlike clearing (offence happened, fee
// settled), this removes it from the offence ladder and renumbers the rest.
export const liftViolation = (id, reason) =>
  api.post(`/violations/${id}/lift/`, { reason })
