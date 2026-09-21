// =============================================================================
// The gate: scans, visitor passes, slips, exits, and the guard's shift.
//
// The broadest module here, because it fronts everything a guard's screen
// does. Worth knowing before reading: the CAMERA path does not go through this
// file at all. Live frames travel over a WebSocket to scanning/consumers.py;
// what is left here is what a guard does by hand, plus the reports.
//
// That is why scanPlate below is unreferenced — the HTTP scan endpoint exists
// on the server but the frontend never posts to it.
// =============================================================================
import api from './axios'

// UNREFERENCED — the camera path is WebSocket-only, so nothing posts a frame
// over HTTP. The backend endpoint still exists. Recorded, not changed.
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

// Admin/CDSO: the same filtered vehicle log as a branded Excel or PDF report.
// Takes the screen's filters verbatim (gate_id, date_from, date_to, search,
// status) so the file matches the table it was exported from.
export const exportVehicleLogExcel = (params) =>
  api.get('/scan/logs/export/', { params, responseType: 'blob' }).then(r => r.data)

export const exportVehicleLogPdf = (params) =>
  api.get('/scan/logs/export-pdf/', { params, responseType: 'blob' }).then(r => r.data)

// Get all offices
export const getOffices = () => api.get('/scan/offices/')

// Get today's visitor passes
export const getVisitorPasses = () => api.get('/scan/visitor-pass/')

// Create a visitor pass
export const createVisitorPass = (data) => api.post('/scan/visitor-pass/', data)

// Confirm or reject a visitor pass
// UNREFERENCED — no import anywhere in frontend/src. Recorded, not changed.
export const updateVisitorPass = (id, data) => api.patch(`/scan/visitor-pass/${id}/`, data)

// Guard override — grant entry with logged reason
export const overrideEntry = (data) => api.post('/scan/override/', data)

// Guard deny — refuse a visitor/unregistered plate with logged reason
export const denyEntry = (data) => api.post('/scan/deny/', data)

// Record a vehicle exit and pair it to its entry log
// UNREFERENCED — no import anywhere in frontend/src. Recorded, not changed.
export const logExit = (data) => api.post('/scan/exit/', data)

// Test an RTSP URL from the server side — returns { ok, message }
// UNREFERENCED — no import anywhere in frontend/src. Recorded, not changed.
export const testRtsp = (rtsp_url) => api.post('/scan/test-rtsp/', { rtsp_url })

// Extend the allowed duration of an active visitor pass
export const extendVisitorPass = (id, extra_minutes) =>
  api.patch(`/scan/visitor-pass/${id}/extend/`, { extra_minutes })

// Gate slips — visitor passes (SLC-VISITOR:{id}) and no-plate entries
// (SLC-NOPLATE:{id}). Looking one up changes nothing; exit and reprint are
// separate, deliberate calls.
export const lookupSlip = (code) => api.get('/scan/slip/', { params: { code } })

// Print on the campus server's thermal printer — no dialog.
// 503 = this server has no printer (use the browser dialog); 502 = it did not print.
// target 'browser' = skip the printer, just issue the slip to print in the dialog.
// Every response carries `slip` as printed — a visitor slip gets a new serial
// (and so a new QR) on every print, so print from that, not the old one.
export const printSlipOnServer = (code, reprint = false, target) =>
  api.post('/scan/slip/print/', { code, reprint, ...(target ? { target } : {}) })

// A reprint done through the browser dialog, so it is audited like a server one.
export const confirmSlipReprinted = (code) => api.post('/scan/slip/reprinted/', { code })

export const exitSlip = (code, gate_id) => api.post('/scan/slip/exit/', { code, gate_id })

// Confirm the visitor slip was printed — this is what logs the visitor's entry
export const confirmVisitorSlipPrinted = (id, gate_id) =>
  api.post(`/scan/visitor-pass/${id}/printed/`, { gate_id })

// Record a visitor exit by scanning the slip QR (payload: SLC-VISITOR:{id})
// UNREFERENCED — no import anywhere in frontend/src. Recorded, not changed.
export const visitorQrExit = (qr_data, gate_id) =>
  api.post('/scan/visitor-pass/exit-scan/', { qr_data, gate_id })

// Admin: live guard activity monitor (now includes gate + shift + cross-gate data)
export const getGuardMonitor = () => api.get('/scan/guard-monitor/')

// QR code scan login — exchanges guard's QR token for JWT (registered at /api/auth/qr-login/)
// THE live guard badge sign-in. Note the URL: /auth/qr-login/, which
// config/urls.py maps to accounts.views.QRLoginView. It is NOT the
// /api/scan/qr-login/ route in scanning/urls.py — nothing calls that one, and
// scanning's own QRLoginView class is unrouted entirely.
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

// Find a vehicle by owner NAME, plate, or conduction number. The guard picks a
// result and the normal plate check runs on it — searching never skips a rule.
export const lookupOwner = (q) => api.get('/scan/owner-lookup/', { params: { q } })

// Plateless vehicles the guard records by hand
export const getUnrecognizedInside = (gate_id) =>
  api.get('/scan/unrecognized/', { params: gate_id ? { gate_id } : {} })

export const recordUnrecognizedEntry = (data) => api.post('/scan/unrecognized/', data)

export const recordUnrecognizedExit = (id, gate_id) =>
  api.post(`/scan/unrecognized/${id}/exit/`, { gate_id })
