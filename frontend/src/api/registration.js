import api from './axios'

export const registrationApi = {
  // ── Public open registration ──
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
  // Follow-up multipart upload — attaches a driver's license photo to a registration
  // that was just created by submitOpenRegistration (kept separate so the main
  // payload, with its nested campus_days/fetcher_students, can stay plain JSON).
  // Uses fetch (not the shared axios instance) so the browser sets the multipart
  // Content-Type boundary itself instead of inheriting axios's default JSON header.
  uploadLicenseImage: async (registrationId, email, file) => {
    const form = new FormData()
    form.append('registration_id', registrationId)
    form.append('email', email)
    form.append('image', file)
    const res = await fetch('/api/vehicles/register/license-image/', { method: 'POST', body: form })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      const err = new Error(data?.error || 'Failed to upload license image.')
      err.response = { data }
      throw err
    }
    return data
  },
  // Live duplicate check for the registration form's plate/email/license/student/employee ID fields
  checkAvailability: async ({ plate_number, email, drivers_license, student_id, employee_id }) => {
    const { data } = await api.get('/vehicles/register/availability/', {
      params: { plate_number, email, drivers_license, student_id, employee_id },
    })
    return data
  },

  // ── Admin: pending registrations ──
  getPendingRegistrations: async (status = 'pending') => {
    const { data } = await api.get(`/vehicles/registrations/pending/?status=${status}`)
    return data
  },
  // Admin/CDSO — branded Vehicle Registrations report (format: 'pdf' | 'excel')
  exportRegistrationsReport: async (format, params = {}) => {
    const { data } = await api.get(`/vehicles/registrations/report/${format}/`, {
      params, responseType: 'blob',
    })
    return data
  },
  // orNumber required; campusDaysOverride is an optional string[] the admin freely picks;
  // specialCaseReason is required when campusDaysOverride adds days not in the original request
  // acknowledgeBlock: pass true to accept a plate flagged by a prior 3rd-offense
  // violation (the backend returns 409 registration_blocked until acknowledged)
  acceptRegistration: async (id, orNumber, campusDaysOverride, specialCaseReason, acknowledgeBlock) => {
    const payload = { or_number: orNumber }
    if (campusDaysOverride && campusDaysOverride.length > 0) payload.campus_days = campusDaysOverride
    if (specialCaseReason) payload.special_case_reason = specialCaseReason
    if (acknowledgeBlock) payload.acknowledge_block = true
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
