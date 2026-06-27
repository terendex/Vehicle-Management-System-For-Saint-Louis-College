import api from './axios'

// Scan a plate image — returns entry decision
export const scanPlate = (imageBlob) => {
  const formData = new FormData()
  formData.append('image', imageBlob, 'capture.jpg')
  return api.post('/scan/', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

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

// Guard override — grant entry with logged reason
export const overrideEntry = (data) => api.post('/scan/override/', data)

// Record a vehicle exit and pair it to its entry log
export const logExit = (data) => api.post('/scan/exit/', data)

// Test an RTSP URL from the server side — returns { ok, message }
export const testRtsp = (rtsp_url) => api.post('/scan/test-rtsp/', { rtsp_url })
