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
  // Follow-up multipart upload — attaches the supporting documents (driver's
  // license photo, assessment form) to a registration that was just created by
  // submitOpenRegistration (kept separate so the main payload, with its nested
  // campus_days/fetcher_students, can stay plain JSON). Either file may be
  // omitted; the backend rejects a call carrying neither.
  // Uses fetch (not the shared axios instance) so the browser sets the multipart
  // Content-Type boundary itself instead of inheriting axios's default JSON header.
  uploadRegistrationDocuments: async (registrationId, email, { license, assessment } = {}) => {
    const form = new FormData()
    form.append('registration_id', registrationId)
    form.append('email', email)
    if (license) form.append('image', license)
    if (assessment) form.append('assessment_form', assessment)
    const res = await fetch('/api/vehicles/register/documents/', { method: 'POST', body: form })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      const err = new Error(data?.error || 'Failed to upload the supporting documents.')
      err.response = { data }
      throw err
    }
    return data
  },
  // ── Applicant-driven proof of payment ──
  // Reached from the link in the pending email. The token is the only key: the
  // (id, email) pair the document upload uses stopped being a secret once school
  // addresses became <8-digit ID>@slc-sflu.edu.ph over sequential ids.
  getPaymentDetails: async (token) => {
    const { data } = await api.get('/vehicles/register/payment/', { params: { token } })
    return data
  },
  // fetch, not axios, so the browser writes its own multipart boundary rather
  // than inheriting axios's JSON Content-Type — same reason as the doc upload.
  submitPaymentReceipt: async (token, orNumber, receipt) => {
    const form = new FormData()
    form.append('token', token)
    form.append('or_number', orNumber)
    form.append('receipt', receipt)
    const res = await fetch('/api/vehicles/register/payment/', { method: 'POST', body: form })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      const err = new Error(data?.error || 'Failed to submit the receipt.')
      err.response = { data }
      throw err
    }
    return data
  },

  // Live duplicate check for the registration form's plate/conduction/email/license/
  // student/employee ID fields. conduction_number must be forwarded like the rest:
  // the form passes it and reads result.conduction_number back, but it used to be
  // dropped here, so a brand-new vehicle's duplicate sticker was never flagged in
  // the field — the applicant only found out when the submit itself 400'd.
  checkAvailability: async ({ plate_number, conduction_number, email, drivers_license, student_id, employee_id }) => {
    const { data } = await api.get('/vehicles/register/availability/', {
      params: { plate_number, conduction_number, email, drivers_license, student_id, employee_id },
    })
    return data
  },

  // ── Admin: pending registrations ──
  getPendingRegistrations: async (status = 'pending') => {
    const { data } = await api.get(`/vehicles/registrations/pending/?status=${status}`)
    return data
  },
  // Admin/CDSO — headline counts (total, per status, per registrant type).
  // Separate from the list, which only ever loads one status at a time.
  getRegistrationSummary: async () => {
    const { data } = await api.get('/vehicles/registrations/summary/')
    return data
  },
  // Admin/CDSO — branded PDF counting registrations by type and status
  exportRegistrationSummaryReport: async (params = {}) => {
    const { data } = await api.get('/vehicles/registrations/report/summary-pdf/', {
      params, responseType: 'blob',
    })
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
  // unpaidAcceptReason is required by the backend when the registration has no
  // Official Receipt on file at all — a pass may still be granted, but never
  // without a stated reason.
  acceptRegistration: async (id, orNumber, campusDaysOverride, specialCaseReason, acknowledgeBlock, unpaidAcceptReason) => {
    const payload = { or_number: orNumber }
    if (unpaidAcceptReason) payload.unpaid_accept_reason = unpaidAcceptReason
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
  /** The approved-registration confirmation PDF, with the applicant's uploaded
   *  documents appended. Accepted registrations only — the document states the
   *  pass was granted. */
  getRegistrationPdf: async (id) => {
    const { data } = await api.get(`/vehicles/registrations/${id}/pdf/`, {
      responseType: 'blob',
    })
    return data
  },

  // ── Parking availability ──
  getParkingAvailability: async (category) => {
    const params = category ? { category } : {}
    const { data } = await api.get('/vehicles/parking-availability/', { params })
    return data
  },
}
