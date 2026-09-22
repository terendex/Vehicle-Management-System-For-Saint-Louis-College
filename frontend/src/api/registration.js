// Vehicle-pass registration, both halves of it:
//
//   the PUBLIC half   — what an applicant with no account can call while
//                       filling in the form (status, slots, availability,
//                       submit, then the payment link from their email)
//   the CDSO half     — review, approve, reject, and the change requests
//
// They share this module because they share the resource, not because they
// share permissions: the public calls work without a token, and the review
// calls need an admin one. The server decides which is which.
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
  // Preview of the FM- control number an e-bike will be issued. The number is
  // only fixed at submit; the submit response carries the one actually given.
  getEbikeControlNumber: async () => {
    const { data } = await api.get('/vehicles/register/ebike-control-number/')
    return data.control_number
  },
  submitOpenRegistration: async (registrationData) => {
    const { data } = await api.post('/vehicles/register/open/', registrationData)
    return data
  },
  // DPO: uploadRegistrationDocuments is gone with the uploads
  // it carried — no licence photo, no assessment form. The backend endpoint is
  // closed too, so there is nothing left for a caller to reach.
  // ── Applicant-driven proof of payment ──
  // Reached from the link in the pending email. The token is the only key: the
  // (id, email) pair the document upload uses stopped being a secret once school
  // addresses became <8-digit ID>@slc-sflu.edu.ph over sequential ids.
  getPaymentDetails: async (token) => {
    const { data } = await api.get('/vehicles/register/payment/', { params: { token } })
    return data
  },
  // DPO: the OR number alone — no photo of the receipt is
  // collected, so this is plain JSON rather than a multipart upload. CDSO checks
  // the paper receipt at the counter instead of an image on the review screen.
  // Multipart rather than JSON, because the receipt photo travels with the
  // number. The OR number on its own is a claim; the photograph is what CDSO
  // checks it against on the review screen. The file is never emailed - see
  // the note on RegistrationPaymentView.
  submitPaymentReceipt: async (token, orNumber, receiptFile) => {
    const form = new FormData()
    form.append('token', token)
    form.append('or_number', orNumber)
    // Omitted when the applicant is only correcting a number they already
    // filed: the backend keeps the photo already on the row.
    if (receiptFile) form.append('or_receipt_image', receiptFile)
    // No explicit Content-Type: the browser has to set the multipart boundary
    // itself, and naming the type here strips it.
    const { data } = await api.post('/vehicles/register/payment/', form)
    return data
  },

  // ── Correcting a still-pending application ──
  // Same token as the payment step, and the same rule behind it: only a PENDING
  // registration is reachable, so CDSO's decision closes this page.
  getEditableDetails: async (token) => {
    const { data } = await api.get('/vehicles/register/details/', { params: { token } })
    return data
  },
  submitDetailChanges: async (token, changes) => {
    const { data } = await api.post('/vehicles/register/details/', { token, ...changes })
    return data
  },

  // ── Approval-gated detail changes on an approved registration ──
  // The owner files one and waits; CDSO decides. Nothing on the registration
  // moves until it is approved, which is why the owner's page shows the request
  // rather than the new value.
  getMyChangeRequests: async () => {
    const { data } = await api.get('/vehicles/registrations/my/changes/')
    return data
  },
  requestDetailChange: async (changes) => {
    const { data } = await api.post('/vehicles/registrations/my/changes/', changes)
    return data
  },
  cancelMyChangeRequest: async (id) => {
    const { data } = await api.post(`/vehicles/registrations/my/changes/${id}/cancel/`)
    return data
  },
  // Admin/CDSO — the review queue. status: 'pending' (default) | 'approved' |
  // 'rejected' | 'cancelled' | 'all'.
  getChangeRequests: async (status = 'pending') => {
    const { data } = await api.get('/vehicles/registrations/changes/', { params: { status } })
    return data
  },
  approveChangeRequest: async (id, note) => {
    const { data } = await api.post(`/vehicles/registrations/changes/${id}/approve/`,
      note ? { note } : {})
    return data
  },
  // note is required by the backend — an owner told "no" with no reason has
  // nothing to correct and will file the same request again.
  rejectChangeRequest: async (id, note) => {
    const { data } = await api.post(`/vehicles/registrations/changes/${id}/reject/`, { note })
    return data
  },

  // Live duplicate check for the registration form's plate/conduction/email/license
  // fields. conduction_number must be forwarded like the rest: the form passes it
  // and reads result.conduction_number back, but it used to be dropped here, so a
  // brand-new vehicle's duplicate sticker was never flagged in the field — the
  // applicant only found out when the submit itself 400'd.
  // DPO: student/employee ID are no longer collected, so there
  // is nothing to check them against.
  checkAvailability: async ({ plate_number, conduction_number, email, drivers_license }) => {
    const { data } = await api.get('/vehicles/register/availability/', {
      params: { plate_number, conduction_number, email, drivers_license },
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
  // There is deliberately no unpaid-acceptance argument. An unsettled fee is a
  // hard block on the backend now: it refuses the approval rather than taking
  // a written justification, so there is nothing for a caller to send.
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
  /** The approved-registration confirmation PDF. Accepted registrations only —
   *  the document states the pass was granted. */
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
