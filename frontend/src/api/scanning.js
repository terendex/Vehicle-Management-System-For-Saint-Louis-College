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

// Guard deny — refuse a visitor/unregistered plate with logged reason
export const denyEntry = (data) => api.post('/scan/deny/', data)

// Record a vehicle exit and pair it to its entry log
export const logExit = (data) => api.post('/scan/exit/', data)

// Test an RTSP URL from the server side — returns { ok, message }
export const testRtsp = (rtsp_url) => api.post('/scan/test-rtsp/', { rtsp_url })

// Extend the allowed duration of an active visitor pass
export const extendVisitorPass = (id, extra_minutes) =>
  api.patch(`/scan/visitor-pass/${id}/extend/`, { extra_minutes })

// Confirm the visitor slip was printed — this is what logs the visitor's entry
export const confirmVisitorSlipPrinted = (id, gate_id) =>
  api.post(`/scan/visitor-pass/${id}/printed/`, { gate_id })

// Record a visitor exit by scanning the slip QR (payload: SLC-VISITOR:{id})
export const visitorQrExit = (qr_data, gate_id) =>
  api.post('/scan/visitor-pass/exit-scan/', { qr_data, gate_id })

// Admin: live guard activity monitor (now includes gate + shift + cross-gate data)
export const getGuardMonitor = () => api.get('/scan/guard-monitor/')

// QR code scan login — exchanges guard's QR token for JWT (registered at /api/auth/qr-login/)
export const qrLogin = (qr_token, gate) => api.post('/auth/qr-login/', { qr_token, gate })

// Current active shifts per gate
export const getCurrentShifts = () => api.get('/scan/current-shifts/')

// Gates — public list of active gates; admin can pass all=true to include inactive
export const getGates = (all = false) => api.get('/scan/gates/', { params: all ? { all: 1 } : {} })

// Admin/CDSO: create a new gate (school expansion)
export const createGate = (payload) => api.post('/scan/gates/', payload)

// Admin/CDSO: rename a gate or toggle it active/inactive
export const updateGate = (id, payload) => api.patch(`/scan/gates/${id}/`, payload)

// Shift history (admin) — optional params: gate, guard, date
export const getShifts = (params) => api.get('/scan/shifts/', { params })
