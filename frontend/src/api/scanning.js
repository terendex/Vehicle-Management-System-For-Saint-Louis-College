import api from './axios'

// Scan a plate image — returns entry decision
export const scanPlate = (imageBlob, gateId = 'main') => {
  const formData = new FormData()
  formData.append('image', imageBlob, 'capture.jpg')
  formData.append('gate_id', gateId)
  return api.post('/scan/', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

// Guard manually types a plate number (no image)
export const manualEntry = (data) => api.post('/scan/manual-entry/', data)

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

// Extend the allowed duration of an active visitor pass
export const extendVisitorPass = (id, extra_minutes) =>
  api.patch(`/scan/visitor-pass/${id}/extend/`, { extra_minutes })

// Admin: live guard activity monitor (now includes gate + shift + cross-gate data)
export const getGuardMonitor = () => api.get('/scan/guard-monitor/')

// QR code scan login — exchanges guard's QR token for JWT (registered at /api/auth/qr-login/)
export const qrLogin = (qr_token, gate) => api.post('/auth/qr-login/', { qr_token, gate })

// Current active shifts per gate
export const getCurrentShifts = () => api.get('/scan/current-shifts/')

// Shift history (admin) — optional params: gate, guard, date
export const getShifts = (params) => api.get('/scan/shifts/', { params })
