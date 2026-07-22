import React, { useState, useEffect, useRef } from 'react'
import AdminLayout from '../../components/Layout/AdminLayout'
import { registrationApi } from '../../api/registration'
import { QRCodeSVG } from 'qrcode.react'
import { format } from 'date-fns'
import { Copy, Check, X, Eye, ShieldCheck, Mail, User, Car, KeyRound, Receipt, CalendarDays, AlertCircle, Search, ChevronLeft, ChevronRight, AlertTriangle, QrCode, Printer } from 'lucide-react'
import { useLiveUpdates } from '../../realtime/useLiveUpdates'
import ReportExportBar from '../../components/ReportExportBar'
import './VehicleRegistration.css'

const SCHEDULE_LABELS = { MWF: 'Mon · Wed · Fri', TTHS: 'Tue · Thu · Sat', ANY: 'Any Day', MIXED: 'Mixed Days' }
const ALL_DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
const DAY_SHORT = { Monday: 'Mon', Tuesday: 'Tue', Wednesday: 'Wed', Thursday: 'Thu', Friday: 'Fri', Saturday: 'Sat' }

function formatSchedule(entity) {
  if (!entity?.schedule) return '—'
  if (entity.schedule === 'MIXED' && entity.campus_days?.length > 0) {
    return entity.campus_days.map(day => DAY_SHORT[day] || day).join(' · ')
  }
  return SCHEDULE_LABELS[entity.schedule] || entity.schedule
}

const DATE_PERIODS = [
  { value: 'all',   label: 'All' },
  { value: 'day',   label: 'Today' },
  { value: 'week',  label: 'Week' },
  { value: 'month', label: 'Month' },
  { value: 'year',  label: 'Year' },
]

function getPeriodStart(period) {
  const d = new Date()
  if (period === 'day') { d.setHours(0, 0, 0, 0) }
  else if (period === 'week') {
    const dow = d.getDay()
    d.setDate(d.getDate() - (dow === 0 ? 6 : dow - 1))
    d.setHours(0, 0, 0, 0)
  } else if (period === 'month') { d.setDate(1); d.setHours(0, 0, 0, 0) }
  else if (period === 'year')  { d.setMonth(0, 1); d.setHours(0, 0, 0, 0) }
  return d
}

export default function VehicleRegistration() {
  // Registrations State
  const [registrations, setRegistrations] = useState([])
  const [statusFilter, setStatusFilter] = useState('pending')
  const [datePeriod, setDatePeriod] = useState('all')
  const [search, setSearch] = useState('')

  // Pagination
  const [regPage, setRegPage] = useState(1)
  const itemsPerPage = 10

  // Modals
  const [selectedReg, setSelectedReg] = useState(null)
  const [isViewModalOpen, setIsViewModalOpen] = useState(false)
  const [isRejectModalOpen, setIsRejectModalOpen] = useState(false)
  const [rejectReason, setRejectReason] = useState('')

  // Accept flow — OR number + free day-picker (inline in details modal)
  const [orNumber, setOrNumber] = useState('')
  const [daysOverride, setDaysOverride] = useState([])   // admin-chosen campus days
  const [specialCaseReason, setSpecialCaseReason] = useState('')
  const [scheduleSlots, setScheduleSlots] = useState(null) // per-day remaining student slots

  const orValid = orNumber.trim().length >= 6 && orNumber.trim().length <= 7

  const [resultModal, setResultModal] = useState(null)
  const [showAcceptConfirm, setShowAcceptConfirm] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [isQRModalOpen, setIsQRModalOpen] = useState(false)
  const [qrDisplayData, setQrDisplayData] = useState(null)
  const [qrViewerCopied, setQrViewerCopied] = useState(false)
  const [accountModal, setAccountModal] = useState(null)
  const [blockPrompt, setBlockPrompt] = useState(null)  // registration-block 409 payload
  const [emailFailed, setEmailFailed] = useState(false)

  useEffect(() => {
    fetchRegistrations()
  }, [statusFilter])

  const fetchRegistrations = async () => {
    try {
      const data = await registrationApi.getPendingRegistrations(statusFilter)
      setRegistrations(data)
    } catch (error) {
      console.error('Failed to fetch registrations:', error)
    }
  }

  // Live-refresh when a registration is created/approved/rejected anywhere
  useLiveUpdates(fetchRegistrations, ['vehicleregistration', 'vehicle'])

  const qrPrintRef = useRef(null)

  // QR of the public registration form URL — shown/printed at CDSO so
  // walk-in applicants can scan it and register on their own phone
  const handleViewRegistrationFormQR = () => {
    const link = `${window.location.origin}/register`
    setQrDisplayData({
      type: 'register-link',
      payload: link,
      title: 'Registration Form QR',
      subtitle: 'Walk-in applicants scan this to open the vehicle registration form',
    })
    setIsQRModalOpen(true)
  }

  const handlePrintQR = () => {
    const svg = qrPrintRef.current?.querySelector('svg')
    if (!svg || !qrDisplayData) return
    const win = window.open('', '_blank', 'width=480,height=640')
    if (!win) return
    win.document.write(`<!DOCTYPE html><html><head><title>${qrDisplayData.title}</title>
      <style>
        body { font-family: Arial, sans-serif; text-align: center; padding: 40px; }
        h1 { font-size: 20px; color: #2A2B61; margin-bottom: 8px; }
        p { color: #555; font-size: 13px; margin: 4px 0; }
        svg { width: 300px; height: 300px; margin: 24px 0; }
        .link { font-size: 12px; word-break: break-all; color: #333; margin-top: 8px; }
      </style></head><body>
      <h1>Vehicle Registration — Saint Louis College</h1>
      <p>Scan this QR code with your phone camera to open the vehicle registration form.</p>
      ${svg.outerHTML}
      <p class="link">${qrDisplayData.payload}</p>
      <script>window.onload = function () { window.print() }</script>
    </body></html>`)
    win.document.close()
  }

  const handleViewVehicleQR = () => {
    if (!selectedReg) return
    const qrData = `VEHICLE:${selectedReg.plate_number}|ID:${selectedReg.id}`
    setQrDisplayData({
      type: 'vehicle',
      payload: qrData,
      title: 'Vehicle Access QR Code',
      subtitle: `${selectedReg.full_name} — ${selectedReg.plate_number}`,
    })
    setIsQRModalOpen(true)
  }

  const handleCopyQRData = async () => {
    if (!qrDisplayData) return
    try {
      await navigator.clipboard.writeText(qrDisplayData.payload)
      setQrViewerCopied(true)
      setTimeout(() => setQrViewerCopied(false), 2000)
    } catch {
      showResult('Failed to copy to clipboard.', 'error')
    }
  }

  // ── Accept flow ──
  // acknowledgeBlock is set true after CDSO confirms a plate flagged by a prior
  // 3rd-offense violation (backend returns 409 registration_blocked otherwise)
  const confirmAccept = async (acknowledgeBlock = false) => {
    if (!selectedReg) return
    // Up to 3 campus days is the normal allowance; more than 3 is a special case
    const tooManyDays = daysOverride.length > 3
    setSubmitting(true)
    try {
      const result = await registrationApi.acceptRegistration(
        selectedReg.id,
        orNumber.trim(),
        daysOverride.length > 0 ? daysOverride : undefined,
        tooManyDays && specialCaseReason.trim() ? specialCaseReason.trim() : undefined,
        acknowledgeBlock,
      )
      setBlockPrompt(null)
      setIsViewModalOpen(false)
      fetchRegistrations()
      if (result?.account) {
        setAccountModal(result.account)
        setEmailFailed(result.email_status === 'failed')
      } else {
        showResult('Registration accepted successfully!', 'success')
      }
    } catch (error) {
      if (error.response?.status === 409 && error.response?.data?.error === 'registration_blocked') {
        // Plate is flagged — ask CDSO to confirm additional review before proceeding
        setBlockPrompt(error.response.data)
      } else {
        console.error('Failed to accept registration:', error)
        showResult(error.response?.data?.error || 'Failed to accept registration.', 'error')
      }
    } finally {
      setSubmitting(false)
    }
  }

  const handleReject = async (e) => {
    e.preventDefault()
    if (!selectedReg) return
    setSubmitting(true)
    try {
      await registrationApi.rejectRegistration(selectedReg.id, rejectReason)
      setIsRejectModalOpen(false)
      setIsViewModalOpen(false)
      setRejectReason('')
      fetchRegistrations()
      showResult('Registration rejected successfully.', 'success')
    } catch (error) {
      showResult(error.response?.data?.error || 'Failed to reject registration.', 'error')
    } finally {
      setSubmitting(false)
    }
  }

  const showResult = (message, type = 'success') => setResultModal({ message, type })
  const openViewModal = (reg) => {
    setSelectedReg(reg)
    setIsViewModalOpen(true)
    setOrNumber('')
    setDaysOverride(reg.campus_days?.length > 0 ? [...reg.campus_days] : [])
    setSpecialCaseReason('')
    // Per-day remaining student slots — so the admin can see capacity before
    // assigning campus days (same slot counts shown on the public register form).
    setScheduleSlots(null)
    if (reg.registrant_type === 'student') {
      registrationApi.getScheduleSlots().then(setScheduleSlots).catch(() => {})
    }
  }
  const openRejectModal = () => setIsRejectModalOpen(true)

  const filteredRegistrations = registrations.filter(r => {
    if (datePeriod !== 'all' && new Date(r.created_at) < getPeriodStart(datePeriod)) return false
    if (search.trim()) {
      const q = search.trim().toLowerCase()
      if (
        !r.full_name?.toLowerCase().includes(q) &&
        !r.plate_number?.toLowerCase().includes(q) &&
        !r.registrant_type?.toLowerCase().includes(q)
      ) return false
    }
    return true
  })
  const paginatedRegistrations = filteredRegistrations.slice((regPage - 1) * itemsPerPage, regPage * itemsPerPage)
  const totalRegPages = Math.ceil(filteredRegistrations.length / itemsPerPage)
  const hasActiveFilters = datePeriod !== 'all' || search.trim() !== ''
  const clearFilters = () => { setDatePeriod('all'); setSearch(''); setRegPage(1) }

  return (
    <AdminLayout>
      <div className="vehicle-registration-page">
        <div className="page-header vr-header-row">
          <div>
            <h1 className="page-title">Vehicle Registration Management</h1>
            <p className="page-subtitle">Review and process vehicle pass applications.</p>
          </div>
          <button className="btn-primary" onClick={handleViewRegistrationFormQR}>
            <QrCode size={18} /> Registration Form QR
          </button>
        </div>

        <ReportExportBar
          label="Registrations Report"
          fileBase="registrations-report"
          fetchBlob={registrationApi.exportRegistrationsReport}
        />

        {/* SECTION: Registrations */}
        <div className="section-container">
          <h2 className="section-title" style={{ marginBottom: 14 }}>Applications</h2>
          <div className="vr-toolbar">
            <div className="vr-toolbar-left">
              <div className="vr-period-btns">
                {DATE_PERIODS.map(p => (
                  <button
                    key={p.value}
                    className={`vr-period-btn ${datePeriod === p.value ? 'active' : ''}`}
                    onClick={() => { setDatePeriod(p.value); setRegPage(1) }}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
              <div className="vr-search-wrap">
                <Search size={14} className="vr-search-icon" />
                <input
                  className="vr-search-input"
                  placeholder="Search name, plate, type…"
                  value={search}
                  onChange={e => { setSearch(e.target.value); setRegPage(1) }}
                />
                {search && (
                  <button className="vr-search-clear" onClick={() => { setSearch(''); setRegPage(1) }}>
                    <X size={12} />
                  </button>
                )}
              </div>
            </div>
            <div className="vr-toolbar-right">
              <select
                className="filter-select"
                value={statusFilter}
                onChange={(e) => { setStatusFilter(e.target.value); setRegPage(1) }}
              >
                <option value="pending">Pending Review</option>
                <option value="accepted">Accepted</option>
                <option value="rejected">Rejected</option>
              </select>
              {hasActiveFilters && (
                <button className="vr-clear-btn" onClick={clearFilters} title="Clear filters">
                  <X size={13} /> Clear
                </button>
              )}
            </div>
          </div>
          <div className="table-container">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Type</th>
                  <th>Plate Number</th>
                  <th>Schedule</th>
                  <th>Submitted</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {paginatedRegistrations.map(r => (
                  <tr key={r.id}>
                    <td>{r.full_name}</td>
                    <td className="capitalize">{r.registrant_type}</td>
                    <td className="token-link">{r.plate_number}</td>
                    <td>{formatSchedule(r)}</td>
                    <td>{format(new Date(r.created_at), 'PP')}</td>
                    <td>
                      <span className={`status-badge status-${r.status}`}>
                        {r.status.charAt(0).toUpperCase() + r.status.slice(1)}
                      </span>
                      {r.is_special_case && (
                        <span className="special-case-badge">Special Case</span>
                      )}
                    </td>
                    <td>
                      <button className="view-btn" onClick={() => openViewModal(r)} title="View Details">
                        <Eye size={18} />
                      </button>
                    </td>
                  </tr>
                ))}
                {filteredRegistrations.length === 0 && (
                  <tr className="empty-row">
                    <td colSpan="7">No {statusFilter} registrations found{datePeriod !== 'all' ? ' for this period' : ''}.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {totalRegPages > 1 && (
            <div className="pagination-bar">
              <span className="pagination-info">
                Showing {(regPage - 1) * itemsPerPage + 1}–{Math.min(regPage * itemsPerPage, filteredRegistrations.length)} of {filteredRegistrations.length}
              </span>
              <div className="pagination-buttons">
                <button className="pagination-btn icon-only" disabled={regPage === 1} onClick={() => setRegPage(p => Math.max(1, p - 1))}>
                  <ChevronLeft size={16} />
                </button>
                <span className="pagination-info">Page {regPage} of {totalRegPages}</span>
                <button className="pagination-btn icon-only" disabled={regPage === totalRegPages} onClick={() => setRegPage(p => Math.min(totalRegPages, p + 1))}>
                  <ChevronRight size={16} />
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* MODAL: View Registration Details */}
      {isViewModalOpen && selectedReg && (
        <div className="modal-overlay">
          <div className="modal-content modal-lg">
            <div className="modal-header">
              <h2 className="modal-title">Registration Details</h2>
              <button className="modal-close-btn" onClick={() => setIsViewModalOpen(false)}><X size={24} /></button>
            </div>

            <div className="details-grid detail-divider">
              <div className="detail-item">
                <div className="detail-label">Full Name</div>
                <div className="detail-value">{selectedReg.full_name}</div>
              </div>
              <div className="detail-item">
                <div className="detail-label">Email</div>
                <div className="detail-value">{selectedReg.email}</div>
              </div>
              <div className="detail-item">
                <div className="detail-label">Type</div>
                <div className="detail-value capitalize">{selectedReg.registrant_type}</div>
              </div>
              <div className="detail-item">
                <div className="detail-label">Schedule</div>
                <div className="detail-value">{formatSchedule(selectedReg)}</div>
              </div>

              {selectedReg.registrant_type === 'student' ? (
                <>
                  <div className="detail-item">
                    <div className="detail-label">Student ID</div>
                    <div className="detail-value">{selectedReg.student_id}</div>
                  </div>
                  <div className="detail-item">
                    <div className="detail-label">Program &amp; Year</div>
                    <div className="detail-value">{selectedReg.program_year}</div>
                  </div>
                </>
              ) : selectedReg.registrant_type === 'employee' ? (
                <>
                  <div className="detail-item">
                    <div className="detail-label">Employee ID</div>
                    <div className="detail-value">{selectedReg.employee_id}</div>
                  </div>
                  <div className="detail-item">
                    <div className="detail-label">Department</div>
                    <div className="detail-value">{selectedReg.department}</div>
                  </div>
                </>
              ) : selectedReg.registrant_type === 'fetcher' ? (
                <>
                  <div className="detail-item">
                    <div className="detail-label">Fetcher Classification</div>
                    <div className="detail-value">
                      {selectedReg.fetcher_type === 'standby'
                        ? 'Standby — may park inside campus'
                        : selectedReg.fetcher_type === 'drop_and_go'
                          ? 'Fetcher / Drop & Go — allotted times only'
                          : '—'}
                    </div>
                  </div>
                  {(selectedReg.fetcher_students || []).length > 0 && (
                    <div className="detail-item" style={{ gridColumn: 'span 2' }}>
                      <div className="detail-label">Students to Fetch ({selectedReg.fetcher_students.length})</div>
                      <div className="detail-value" style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 4 }}>
                        {selectedReg.fetcher_students.map((s, i) => (
                          <div key={i} style={{ padding: '6px 12px', background: '#F5F6FF', border: '1px solid #E2E6EE', borderRadius: 8, fontSize: 13 }}>
                            <strong>{s.full_name}</strong>
                            {s.student_id && <span style={{ color: '#7C80A3' }}> · ID: {s.student_id}</span>}
                            {s.student_level && <span style={{ color: '#7C80A3' }}> · {s.student_level.toUpperCase()}</span>}
                            {s.program_year && <span style={{ color: '#7C80A3' }}> · {s.program_year}</span>}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </>
              ) : null}

              <div className="detail-item">
                <div className="detail-label">Address</div>
                <div className="detail-value">{selectedReg.address || 'N/A'}</div>
              </div>
              <div className="detail-item">
                <div className="detail-label">Age</div>
                <div className="detail-value">{selectedReg.age || 'N/A'}</div>
              </div>
              <div className="detail-item">
                <div className="detail-label">Contact Number</div>
                <div className="detail-value">{selectedReg.contact_number}</div>
              </div>
              <div className="detail-item">
                <div className="detail-label">Driver's License</div>
                <div className="detail-value">{selectedReg.drivers_license || 'N/A'}</div>
              </div>

              {/* Authorized driver — set when the student is a minor / non-driver */}
              {selectedReg.driver_name && (
                <div className="detail-item" style={{ gridColumn: 'span 2' }}>
                  <div className="detail-label">Authorized Driver (student does not drive)</div>
                  <div className="detail-value">
                    {selectedReg.driver_name}
                    {selectedReg.driver_relationship && (
                      <span style={{ color: '#7C80A3', fontWeight: 500 }}>
                        {' '}— {selectedReg.driver_relationship.replace('_', ' ')}
                      </span>
                    )}
                    {selectedReg.driver_contact && (
                      <span style={{ color: '#7C80A3', fontWeight: 500 }}> · {selectedReg.driver_contact}</span>
                    )}
                  </div>
                </div>
              )}

              {selectedReg.campus_days?.length > 0 && (
                <div className="detail-item" style={{ gridColumn: 'span 2' }}>
                  <div className="detail-label">Campus Days</div>
                  <div className="detail-value" style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginTop: '4px' }}>
                    {selectedReg.campus_days.map(day => (
                      <span key={day} style={{ display: 'inline-block', padding: '3px 12px', borderRadius: '50px', background: '#2A2B61', color: '#fff', fontSize: '12px', fontWeight: 600 }}>{day}</span>
                    ))}
                  </div>
                </div>
              )}

              {selectedReg.or_number && (
                <div className="detail-item" style={{ gridColumn: 'span 2' }}>
                  <div className="detail-label">Official Receipt (OR) No.</div>
                  <div className="detail-value token-link" style={{ fontWeight: 700, color: '#059669' }}>{selectedReg.or_number}</div>
                </div>
              )}

              {selectedReg.status === 'accepted' && (
                <div className="detail-item" style={{ gridColumn: 'span 2' }}>
                  <div className="detail-label">System ID (Assigned)</div>
                  <div className="detail-value token-link" style={{ fontSize: '15px', fontWeight: 700, color: '#059669', letterSpacing: '0.5px' }}>
                    {selectedReg.registrant_type === 'student'
                      ? selectedReg.system_student_id || '—'
                      : selectedReg.system_employee_id || '—'}
                  </div>
                </div>
              )}
            </div>

            <h3 className="detail-section-title">Vehicle Information</h3>
            <div className="details-grid" style={{ marginBottom: '24px' }}>
              <div className="detail-item">
                <div className="detail-label">Plate Number</div>
                <div className="detail-value token-link" style={{ fontSize: '14px', fontWeight: 600 }}>{selectedReg.plate_number}</div>
              </div>
              <div className="detail-item">
                <div className="detail-label">Vehicle Type</div>
                <div className="detail-value">{selectedReg.vehicle_type}</div>
              </div>
              <div className="detail-item">
                <div className="detail-label">Color</div>
                <div className="detail-value">{selectedReg.vehicle_color}</div>
              </div>
              <div className="detail-item">
                <div className="detail-label">Conduction Number</div>
                <div className="detail-value">{selectedReg.conduction_number || 'N/A'}</div>
              </div>
            </div>

            {selectedReg.status === 'accepted' && selectedReg.is_special_case && (
              <div className="special-case-section">
                <div className="special-case-section-head">
                  <AlertCircle size={15} />
                  Special Case
                </div>
                <p className="special-case-reason">{selectedReg.special_case_reason}</p>
              </div>
            )}

            {selectedReg.status === 'pending' && (() => {
              const originalDays = selectedReg.campus_days || []
              // Up to 3 campus days is the normal allowance; more than 3 is a special case
              const tooManyDays  = daysOverride.length > 3
              const canAccept    = orValid && (!tooManyDays || specialCaseReason.trim())
              return (
                <div className="accept-inline-section">
                  <h3 className="accept-inline-title">
                    <Receipt size={15} /> Accept Registration
                  </h3>
                  <p className="accept-inline-desc">
                    Confirm that <strong>{selectedReg.full_name}</strong> has paid the Vehicle Pass fee and enter their Official Receipt number.
                  </p>

                  <div className="form-group">
                    <label className="form-label">
                      Official Receipt (OR) Number <span className="required">*</span>
                    </label>
                    <input
                      type="text"
                      inputMode="numeric"
                      maxLength={7}
                      className={`form-input${orValid ? ' input-valid' : ''}`}
                      value={orNumber}
                      onChange={(e) => setOrNumber(e.target.value.replace(/\D/g, '').slice(0, 7))}
                      placeholder="e.g. 1380093"
                      disabled={submitting}
                    />
                    <p className="form-hint">
                      Issued by the Accounting Office upon payment of ₱{selectedReg.registrant_type === 'employee' ? '150.00 (50% employee discount)' : '300.00'}
                    </p>
                  </div>

                  {selectedReg.registrant_type === 'student' && (
                    <div className="form-group">
                      <label className="form-label">
                        <CalendarDays size={14} style={{ marginRight: 6, verticalAlign: 'middle' }} />
                        Campus Schedule
                      </label>
                      {selectedReg.campus_days?.length > 0 && (
                        <p className="form-hint" style={{ marginBottom: 8 }}>
                          Applicant chose:{' '}
                          {selectedReg.campus_days.map(d => (
                            <span key={d} className="day-chip">{d}</span>
                          ))}
                        </p>
                      )}
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 6 }}>
                        {ALL_DAYS.map(day => {
                          const sel = daysOverride.includes(day)
                          const isAdded = sel && !originalDays.includes(day)
                          const slot = scheduleSlots?.[day]
                          const isFull = slot && slot.available === 0 && !sel
                          return (
                            <button
                              key={day}
                              type="button"
                              disabled={submitting}
                              onClick={() => setDaysOverride(prev =>
                                prev.includes(day) ? prev.filter(d => d !== day) : [...prev, day]
                              )}
                              className={`day-override-btn${sel ? ' selected' : ''}${isAdded ? ' added' : ''}${isFull ? ' full' : ''}`}
                              title={slot ? `${slot.used}/${slot.limit} slots used — ${slot.available} available` : undefined}
                            >
                              <span>{DAY_SHORT[day]}</span>
                              <span className="day-override-slots">
                                {slot ? (isFull ? 'FULL' : `${slot.available} left`) : '—'}
                              </span>
                            </button>
                          )
                        })}
                      </div>
                      <p className="form-hint" style={{ marginTop: 0, marginBottom: 6 }}>
                        Numbers show remaining student slots per day. Assigning a <strong>full</strong> day
                        is allowed for special cases but exceeds the day's capacity.
                      </p>
                      {daysOverride.length > 0 ? (
                        <p className="form-hint">Assigned: <strong>{daysOverride.join(', ')}</strong></p>
                      ) : (
                        <p className="form-hint" style={{ color: '#dc2626' }}>No days selected — original choice will be kept.</p>
                      )}
                    </div>
                  )}

                  {tooManyDays && (
                    <div className="form-group special-case-reason-group">
                      <label className="form-label special-case-reason-label">
                        <AlertCircle size={13} />
                        Reason for Extra Days <span className="required">*</span>
                      </label>
                      <p className="form-hint special-case-added-hint">
                        You assigned <strong>{daysOverride.length} days</strong> — more than the standard 3-day allowance. Provide a reason; this registration will be flagged as a Special Case.
                      </p>
                      <textarea
                        className="form-textarea"
                        rows={2}
                        value={specialCaseReason}
                        onChange={(e) => setSpecialCaseReason(e.target.value)}
                        placeholder="e.g. Faculty clearance for make-up sessions on Friday…"
                        disabled={submitting}
                      />
                    </div>
                  )}

                  <div className="detail-actions">
                    <button className="btn-danger" onClick={openRejectModal} disabled={submitting}>
                      <X size={18} /> Reject
                    </button>
                    <button
                      className="btn-success"
                      onClick={() => setShowAcceptConfirm(true)}
                      disabled={submitting || !canAccept}
                      title={!orValid ? 'Enter a valid OR number to enable' : tooManyDays && !specialCaseReason.trim() ? 'Provide a reason for granting more than 3 days' : ''}
                    >
                      {submitting ? 'Processing…' : <><Check size={16} /> Confirm &amp; Accept</>}
                    </button>
                  </div>
                </div>
              )
            })()}

            {selectedReg.status === 'accepted' && (
              <div className="detail-actions">
                <button className="btn-outline" onClick={handleViewVehicleQR}>
                  <Eye size={18} /> View Vehicle QR
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* MODAL: Reject Reason */}
      {isRejectModalOpen && (
        <div className="modal-overlay">
          <div className="modal-content">
            <div className="modal-header">
              <h2 className="modal-title danger">Reject Registration</h2>
              <button className="modal-close-btn" onClick={() => setIsRejectModalOpen(false)}><X size={20} /></button>
            </div>
            <form onSubmit={handleReject}>
              <div className="form-group">
                <label className="form-label">Reason for Rejection <span className="required">*</span></label>
                <textarea
                  className="form-textarea"
                  rows={4}
                  value={rejectReason}
                  onChange={(e) => setRejectReason(e.target.value)}
                  placeholder="Explain why this registration is being rejected..."
                  required
                />
              </div>
              <div className="modal-actions">
                <button type="button" className="btn-outline" onClick={() => setIsRejectModalOpen(false)} disabled={submitting}>Cancel</button>
                <button type="submit" className="btn-danger" disabled={submitting}>
                  {submitting ? 'Processing...' : 'Confirm Rejection'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL: Accept Confirmation */}
      {showAcceptConfirm && selectedReg && (
        <div className="modal-overlay">
          <div className="modal-content confirm-modal">
            <button className="modal-close-btn" style={{ position: 'absolute', top: 12, right: 12 }} onClick={() => setShowAcceptConfirm(false)}><X size={18} /></button>
            <div className="confirm-icon success" style={{ marginBottom: 8 }}>
              <Check size={24} />
            </div>
            <h2 className="confirm-title">Accept Registration?</h2>
            <p className="confirm-message">
              You are accepting <strong>{selectedReg.full_name}</strong>'s registration for plate <strong>{selectedReg.plate_number}</strong> with OR No. <strong>{orNumber}</strong>.
              {daysOverride.length > 0 && <> Campus days: <strong>{daysOverride.join(', ')}</strong>.</>}
            </p>
            <div className="confirm-actions" style={{ gap: 10 }}>
              <button className="btn-outline" onClick={() => setShowAcceptConfirm(false)} disabled={submitting}>Cancel</button>
              <button
                className="btn-success"
                disabled={submitting}
                onClick={() => { setShowAcceptConfirm(false); confirmAccept() }}
              >
                {submitting ? 'Processing…' : <><Check size={15} /> Yes, Accept</>}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* MODAL: QR Viewer */}
      {isQRModalOpen && qrDisplayData && (
        <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && setIsQRModalOpen(false)}>
          <div className="modal-content qr-viewer-modal">
            <div className="modal-header">
              <h2 className="modal-title">{qrDisplayData.title}</h2>
              <button className="modal-close-btn" onClick={() => setIsQRModalOpen(false)}><X size={24} /></button>
            </div>
            <p className="qr-viewer-subtitle">{qrDisplayData.subtitle}</p>
            <div className="qr-display-wrapper" ref={qrPrintRef}>
              <QRCodeSVG value={qrDisplayData.payload} size={220} level="H" includeMargin={true} />
            </div>
            <div className="qr-data-box">
              <p className="qr-label">{qrDisplayData.type === 'register-link' ? 'Registration Link' : 'Encoded Data'}</p>
              <code className="qr-code-data">{qrDisplayData.payload}</code>
            </div>
            <div style={{ display: 'flex', gap: 10 }}>
              <button className="btn-primary" onClick={handleCopyQRData} style={{ flex: 1, justifyContent: 'center' }}>
                {qrViewerCopied
                  ? <><Check size={16} />Copied!</>
                  : <><Copy size={16} /> {qrDisplayData.type === 'register-link' ? 'Copy Link' : 'Copy Data'}</>}
              </button>
              {qrDisplayData.type === 'register-link' && (
                <button className="btn-outline" onClick={handlePrintQR} style={{ flex: 1, justifyContent: 'center' }}>
                  <Printer size={16} /> Print
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* MODAL: Result/Success/Error */}
      {/* MODAL: Registration block — plate flagged by prior 3rd-offense violation */}
      {blockPrompt && (
        <div className="modal-overlay">
          <div className="modal-content confirm-modal">
            <div className="confirm-icon error"><AlertTriangle size={24} /></div>
            <h2 className="confirm-title">Plate Flagged for Review</h2>
            <p className="confirm-message">
              {blockPrompt.detail}
            </p>
            {blockPrompt.registration_block && (
              <div style={{ background: '#FEF2F2', border: '1px solid #FECACA', borderRadius: 8, padding: '10px 14px', margin: '4px 0 12px', fontSize: 13, textAlign: 'left' }}>
                <div><strong>Flagged violations:</strong> {blockPrompt.registration_block.count}</div>
                <div><strong>Most recent:</strong> {blockPrompt.registration_block.latest_type} ({blockPrompt.registration_block.latest_status})</div>
              </div>
            )}
            <div className="confirm-actions" style={{ display: 'flex', gap: 8 }}>
              <button className="btn-secondary" onClick={() => setBlockPrompt(null)} disabled={submitting} style={{ flex: 1, justifyContent: 'center' }}>Cancel</button>
              <button className="btn-primary" onClick={() => confirmAccept(true)} disabled={submitting} style={{ flex: 1, justifyContent: 'center', background: '#dc2626', borderColor: '#dc2626' }}>
                {submitting ? 'Accepting…' : 'Reviewed — Accept Anyway'}
              </button>
            </div>
          </div>
        </div>
      )}

      {resultModal && (
        <div className="modal-overlay">
          <div className="modal-content confirm-modal">
            <div className={`confirm-icon ${resultModal.type === 'success' ? 'success' : 'error'}`}>
              {resultModal.type === 'success' ? <Check size={24} /> : <X size={24} />}
            </div>
            <h2 className="confirm-title">{resultModal.type === 'success' ? 'Success' : 'Error'}</h2>
            <p className="confirm-message">{resultModal.message}</p>
            <div className="confirm-actions">
              <button className="btn-primary" onClick={() => setResultModal(null)} style={{ width: '100%', justifyContent: 'center' }}>Close</button>
            </div>
          </div>
        </div>
      )}

      {/* MODAL: Account Created */}
      {accountModal && (
        <div className="modal-overlay">
          <div className="modal-content modal-account">
            <div className="account-modal-header">
              <div className="account-success-badge"><ShieldCheck size={28} /></div>
              <div>
                <h2 className="modal-title" style={{ marginBottom: 4 }}>Account Created Successfully</h2>
                <p className="account-modal-subtitle">The vehicle owner account has been provisioned and credentials sent via email.</p>
              </div>
              <button className="modal-close-btn" onClick={() => setAccountModal(null)}><X size={22} /></button>
            </div>

            <div className="account-id-banner">
              <div className="account-id-item">
                <span className="account-id-label">Portal Account ID</span>
                <span className="account-id-value">{accountModal.user_code || '—'}</span>
              </div>
              <div className="account-id-divider" />
              <div className="account-id-item">
                <span className="account-id-label">System Registration ID</span>
                <span className="account-id-value">{accountModal.system_id || '—'}</span>
              </div>
            </div>

            <div className="account-credentials-box">
              <div className="account-cred-header">
                <KeyRound size={16} />
                Login Credentials
                <span className="account-cred-note">Sent to owner's email</span>
              </div>
              <div className="account-cred-row">
                <Mail size={14} />
                <span className="account-cred-field">Email</span>
                <span className="account-cred-val">{accountModal.email}</span>
              </div>
              <div className="account-cred-row">
                <KeyRound size={14} />
                <span className="account-cred-field">Password</span>
                <span className="account-cred-val" style={{ color: '#7C80A3', fontStyle: 'italic' }}>Sent securely to owner's email</span>
              </div>
              {emailFailed ? (
                <p className="account-cred-warning" style={{ color: '#DC2626' }}>
                  ⚠ Email failed to send. Please check SMTP settings and share credentials manually.
                </p>
              ) : (
                <p className="account-cred-warning" style={{ color: '#059669' }}>
                  ✓ Credentials have been emailed to the vehicle owner.
                </p>
              )}
            </div>

            <div className="account-sections">
              <div className="account-info-section">
                <div className="account-section-head"><User size={14} /> Personal Information</div>
                <div className="account-info-grid">
                  <div className="account-info-item">
                    <span className="account-info-label">Full Name</span>
                    <span className="account-info-val">{accountModal.full_name}</span>
                  </div>
                  <div className="account-info-item">
                    <span className="account-info-label">Type</span>
                    <span className="account-info-val" style={{ textTransform: 'capitalize' }}>{accountModal.registrant_type}</span>
                  </div>
                  <div className="account-info-item">
                    <span className="account-info-label">Contact</span>
                    <span className="account-info-val">{accountModal.contact_number || '—'}</span>
                  </div>
                  <div className="account-info-item">
                    <span className="account-info-label">Schedule</span>
                    <span className="account-info-val">{formatSchedule(accountModal)}</span>
                  </div>
                </div>
              </div>
              <div className="account-info-section">
                <div className="account-section-head"><Car size={14} /> Vehicle Information</div>
                <div className="account-info-grid">
                  <div className="account-info-item">
                    <span className="account-info-label">Plate Number</span>
                    <span className="account-info-val account-plate">{accountModal.plate_number}</span>
                  </div>
                  <div className="account-info-item">
                    <span className="account-info-label">Vehicle Type</span>
                    <span className="account-info-val">{accountModal.vehicle_type}</span>
                  </div>
                </div>
              </div>
            </div>

            <div className="modal-actions" style={{ marginTop: 8 }}>
              <button className="btn-primary" onClick={() => setAccountModal(null)} style={{ flex: 1, justifyContent: 'center' }}>
                <Check size={16} /> Done
              </button>
            </div>
          </div>
        </div>
      )}
    </AdminLayout>
  )
}
