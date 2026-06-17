import api from './axios'

// Scan a plate image — returns entry decision
export const scanPlate = (imageBlob) => {
  const formData = new FormData()
  formData.append('image', imageBlob, 'capture.jpg')
  return api.post('/scan/', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

// Verify digital ID for unplated vehicle entry
export const verifyDigitalId = (data) => api.post('/scan/digital-id/', data)

// Get recent access logs
export const getAccessLogs = (params) => api.get('/scan/logs/', { params })

// Get all offices
export const getOffices = () => api.get('/scan/offices/')

// Get today's visitor passes
export const getVisitorPasses = () => api.get('/scan/visitor-pass/')

// Create a visitor pass
export const createVisitorPass = (data) => api.post('/scan/visitor-pass/', data)

// Confirm or reject a visitor pass
export const updateVisitorPass = (id, data) => api.patch(`/scan/visitor-pass/${id}/`, data)
