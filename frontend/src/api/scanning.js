import api from './axios'

// Scan a plate image — returns entry decision
export const scanPlate = (imageBlob) => {
  const formData = new FormData()
  formData.append('image', imageBlob, 'capture.jpg')
  return api.post('/api/scan/', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

// Get recent access logs
export const getAccessLogs = (params) => api.get('/api/scan/logs/', { params })

// Get all offices
export const getOffices = () => api.get('/api/scan/offices/')

// Get today's visitor passes
export const getVisitorPasses = () => api.get('/api/scan/visitor-pass/')

// Create a visitor pass
export const createVisitorPass = (data) => api.post('/api/scan/visitor-pass/', data)

// Confirm or reject a visitor pass
export const updateVisitorPass = (id, data) => api.patch(`/api/scan/visitor-pass/${id}/`, data)
