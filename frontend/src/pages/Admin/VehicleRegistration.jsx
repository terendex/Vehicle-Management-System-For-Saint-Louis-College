import React, { useState, useEffect } from 'react'
import AdminLayout from '../../components/Layout/AdminLayout'
import { registrationApi } from '../../api/registration'
import { QRCodeSVG } from 'qrcode.react'
import { format } from 'date-fns'
import { QrCode, Copy, Check, X, Eye, Power, PowerOff } from 'lucide-react'
import './VehicleRegistration.css'

export default function VehicleRegistration() {
  // Token State
  const [tokens, setTokens] = useState([])
  const [isTokenModalOpen, setIsTokenModalOpen] = useState(false)
  const [tokenType, setTokenType] = useState('student')
  const [tokenExpiry, setTokenExpiry] = useState('')
  const [generatedToken, setGeneratedToken] = useState(null)
  const [copied, setCopied] = useState(false)

  // Registrations State
  const [registrations, setRegistrations] = useState([])
  const [statusFilter, setStatusFilter] = useState('pending')
  const [selectedReg, setSelectedReg] = useState(null)
  
  // Pagination State
  const [tokenPage, setTokenPage] = useState(1)
  const [regPage, setRegPage] = useState(1)
  const itemsPerPage = 5
  
  // Modals
  const [isViewModalOpen, setIsViewModalOpen] = useState(false)
  const [isRejectModalOpen, setIsRejectModalOpen] = useState(false)
  const [rejectReason, setRejectReason] = useState('')
  const [confirmAcceptModal, setConfirmAcceptModal] = useState(null) // holds registration id
  const [resultModal, setResultModal] = useState(null) // { message, type }
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    fetchTokens()
    fetchRegistrations()
  }, [statusFilter])

  const fetchTokens = async () => {
    try {
      const data = await registrationApi.listTokens()
      setTokens(data)
    } catch (error) {
      console.error("Failed to fetch tokens:", error)
    }
  }

  const fetchRegistrations = async () => {
    try {
      const data = await registrationApi.getPendingRegistrations(statusFilter)
      setRegistrations(data)
    } catch (error) {
      console.error("Failed to fetch registrations:", error)
    }
  }

  const handleGenerateQR = async (e) => {
    e.preventDefault()
    try {
      const data = await registrationApi.generateToken(tokenType, tokenExpiry)
      setGeneratedToken(data)
      fetchTokens()
    } catch (error) {
      console.error("Failed to generate token:", error)
    }
  }

  const handleCopyLink = () => {
    if (!generatedToken) return
    const link = `${window.location.origin}/register?token=${generatedToken.token}`
    navigator.clipboard.writeText(link)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const handleToggleToken = async (id) => {
    try {
      await registrationApi.toggleToken(id)
      fetchTokens()
    } catch (error) {
      console.error("Failed to toggle token:", error)
    }
  }

  const handleAcceptClick = (id) => {
    setConfirmAcceptModal(id)
  }

  const confirmAccept = async () => {
    if (!confirmAcceptModal) return
    setSubmitting(true)
    try {
      await registrationApi.acceptRegistration(confirmAcceptModal)
      setIsViewModalOpen(false)
      setConfirmAcceptModal(null)
      fetchRegistrations()
      showResult("Registration accepted successfully!", "success")
    } catch (error) {
      console.error("Failed to accept registration:", error)
      setConfirmAcceptModal(null)
      showResult(error.response?.data?.error || "Failed to accept registration.", "error")
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
      showResult("Registration rejected successfully.", "success")
    } catch (error) {
      console.error("Failed to reject registration:", error)
      showResult(error.response?.data?.error || "Failed to reject registration.", "error")
    } finally {
      setSubmitting(false)
    }
  }

  const showResult = (message, type = 'success') => {
    setResultModal({ message, type })
  }

  const openViewModal = (reg) => {
    setSelectedReg(reg)
    setIsViewModalOpen(true)
  }

  const openRejectModal = () => {
    setIsRejectModalOpen(true)
  }

  // Helper to get current datetime formatted for datetime-local input (YYYY-MM-DDThh:mm)
  const getMinDateTime = () => {
    const now = new Date()
    now.setMinutes(now.getMinutes() - now.getTimezoneOffset())
    return now.toISOString().slice(0, 16)
  }

  const paginatedTokens = tokens.slice((tokenPage - 1) * itemsPerPage, tokenPage * itemsPerPage)
  const totalTokenPages = Math.ceil(tokens.length / itemsPerPage)

  const paginatedRegistrations = registrations.slice((regPage - 1) * itemsPerPage, regPage * itemsPerPage)
  const totalRegPages = Math.ceil(registrations.length / itemsPerPage)

  return (
    <AdminLayout>
      <div className="vehicle-registration-page">
        <div className="page-header">
          <h1 className="page-title">Vehicle Registration Management</h1>
          <p className="page-subtitle">Manage QR invites and review pending registrations.</p>
        </div>

        {/* SECTION 1: Token Management */}
        <div className="section-container">
          <div className="flex justify-between items-center mb-4">
            <h2 className="section-title mb-0">Registration QR Codes</h2>
            <button 
              className="btn-primary"
              onClick={() => {
                setGeneratedToken(null)
                setTokenType('student')
                setTokenExpiry('')
                setIsTokenModalOpen(true)
              }}
            >
              <QrCode size={18} />
              Generate New QR
            </button>
          </div>

          <div className="table-container">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Type</th>
                  <th>Token Link</th>
                  <th>Expires At</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {paginatedTokens.map(t => (
                  <tr key={t.id}>
                    <td className="capitalize">{t.registrant_type}</td>
                    <td className="font-mono text-xs text-gray-500">
                      {window.location.origin}/register?token={t.token.substring(0, 8)}...
                    </td>
                    <td>{format(new Date(t.expires_at), 'PPp')}</td>
                    <td>
                      <span className={`status-badge status-${t.is_valid ? 'active' : (t.is_used ? 'disabled' : (t.is_active ? 'expired' : 'disabled'))}`}>
                        {t.is_valid ? 'Active' : (t.is_used ? 'Used' : (t.is_active ? 'Expired' : 'Disabled'))}
                      </span>
                    </td>
                    <td>
                      <button 
                        className={`p-1 rounded ${t.is_active ? 'text-red-500 hover:bg-red-50' : 'text-green-500 hover:bg-green-50'}`}
                        onClick={() => handleToggleToken(t.id)}
                        title={t.is_active ? "Disable Token" : "Enable Token"}
                      >
                        {t.is_active ? <PowerOff size={16} /> : <Power size={16} />}
                      </button>
                    </td>
                  </tr>
                ))}
                {tokens.length === 0 && (
                  <tr>
                    <td colSpan="5" className="text-center py-4 text-gray-500">No tokens generated yet.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          
          {/* Token Pagination */}
          {totalTokenPages > 1 && (
            <div className="flex justify-end items-center gap-4 mt-4 border-t pt-4">
              <span className="text-sm text-gray-500">Page {tokenPage} of {totalTokenPages}</span>
              <div className="flex gap-2">
                <button 
                  className="btn-outline px-3 py-1 text-sm"
                  disabled={tokenPage === 1}
                  onClick={() => setTokenPage(p => Math.max(1, p - 1))}
                >
                  Previous
                </button>
                <button 
                  className="btn-outline px-3 py-1 text-sm"
                  disabled={tokenPage === totalTokenPages}
                  onClick={() => setTokenPage(p => Math.min(totalTokenPages, p + 1))}
                >
                  Next
                </button>
              </div>
            </div>
          )}
        </div>

        {/* SECTION 2: Registrations */}
        <div className="section-container">
          <div className="flex justify-between items-center mb-4">
            <h2 className="section-title mb-0">Registrations</h2>
            <select 
              className="form-select w-auto"
              value={statusFilter}
              onChange={(e) => {
                setStatusFilter(e.target.value)
                setRegPage(1)
              }}
            >
              <option value="pending">Pending Review</option>
              <option value="accepted">Accepted</option>
              <option value="rejected">Rejected</option>
            </select>
          </div>

          <div className="table-container">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Type</th>
                  <th>Plate Number</th>
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
                    <td className="font-mono">{r.plate_number}</td>
                    <td>{format(new Date(r.created_at), 'PP')}</td>
                    <td>
                      <span className={`status-badge status-${r.status}`}>
                        {r.status.charAt(0).toUpperCase() + r.status.slice(1)}
                      </span>
                    </td>
                    <td>
                      <button 
                        className="p-1 text-blue-600 hover:bg-blue-50 rounded"
                        onClick={() => openViewModal(r)}
                        title="View Details"
                      >
                        <Eye size={18} />
                      </button>
                    </td>
                  </tr>
                ))}
                {registrations.length === 0 && (
                  <tr>
                    <td colSpan="6" className="text-center py-4 text-gray-500">No {statusFilter} registrations found.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          
          {/* Registrations Pagination */}
          {totalRegPages > 1 && (
            <div className="flex justify-end items-center gap-4 mt-4 border-t pt-4">
              <span className="text-sm text-gray-500">Page {regPage} of {totalRegPages}</span>
              <div className="flex gap-2">
                <button 
                  className="btn-outline px-3 py-1 text-sm"
                  disabled={regPage === 1}
                  onClick={() => setRegPage(p => Math.max(1, p - 1))}
                >
                  Previous
                </button>
                <button 
                  className="btn-outline px-3 py-1 text-sm"
                  disabled={regPage === totalRegPages}
                  onClick={() => setRegPage(p => Math.min(totalRegPages, p + 1))}
                >
                  Next
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* MODAL: Generate QR */}
      {isTokenModalOpen && (
        <div className="modal-overlay">
          <div className="modal-content">
            <h2 className="modal-title">Generate Registration QR</h2>
            
            {!generatedToken ? (
              <form onSubmit={handleGenerateQR}>
                <div className="form-group">
                  <label className="form-label">Registrant Type</label>
                  <select 
                    className="form-select"
                    value={tokenType}
                    onChange={(e) => setTokenType(e.target.value)}
                  >
                    <option value="student">Student</option>
                    <option value="employee">Employee</option>
                  </select>
                </div>
                
                <div className="form-group">
                  <label className="form-label">Expiration Date & Time</label>
                  <input 
                    type="datetime-local" 
                    className="form-input"
                    value={tokenExpiry}
                    min={getMinDateTime()}
                    onChange={(e) => setTokenExpiry(e.target.value)}
                    required
                  />
                </div>

                <div className="modal-actions">
                  <button type="button" className="btn-outline" onClick={() => setIsTokenModalOpen(false)}>Cancel</button>
                  <button type="submit" className="btn-primary">Generate</button>
                </div>
              </form>
            ) : (
              <div>
                <div className="qr-container">
                  <QRCodeSVG 
                    value={`${window.location.origin}/register?token=${generatedToken.token}`} 
                    size={200}
                    level="H"
                    includeMargin={true}
                  />
                  <div className="qr-link-box">
                    <input 
                      type="text" 
                      readOnly 
                      className="qr-link-input"
                      value={`${window.location.origin}/register?token=${generatedToken.token}`}
                    />
                    <button className="btn-outline px-2 py-1" onClick={handleCopyLink} title="Copy Link">
                      {copied ? <Check size={16} className="text-green-600" /> : <Copy size={16} />}
                    </button>
                  </div>
                </div>
                <div className="modal-actions">
                  <button type="button" className="btn-primary w-full justify-center" onClick={() => setIsTokenModalOpen(false)}>Close</button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* MODAL: View Registration Details */}
      {isViewModalOpen && selectedReg && (
        <div className="modal-overlay">
          <div className="modal-content max-w-2xl">
            <div className="flex justify-between items-center mb-4">
              <h2 className="modal-title mb-0">Registration Details</h2>
              <button className="text-gray-400 hover:text-gray-600" onClick={() => setIsViewModalOpen(false)}>
                <X size={24} />
              </button>
            </div>
            
            <div className="details-grid mb-6 border-b pb-4">
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
              
              {selectedReg.registrant_type === 'student' ? (
                <>
                  <div className="detail-item">
                    <div className="detail-label">Student ID</div>
                    <div className="detail-value">{selectedReg.student_id}</div>
                  </div>
                  <div className="detail-item">
                    <div className="detail-label">Program & Year</div>
                    <div className="detail-value">{selectedReg.program_year}</div>
                  </div>
                </>
              ) : (
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
              )}
              
              <div className="detail-item">
                <div className="detail-label">Contact Number</div>
                <div className="detail-value">{selectedReg.contact_number}</div>
              </div>
              <div className="detail-item">
                <div className="detail-label">Driver's License</div>
                <div className="detail-value">{selectedReg.drivers_license}</div>
              </div>
            </div>

            <h3 className="font-semibold text-[#1A1D2E] mb-3">Vehicle Information</h3>
            <div className="details-grid mb-6">
              <div className="detail-item">
                <div className="detail-label">Plate Number</div>
                <div className="detail-value font-mono">{selectedReg.plate_number}</div>
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

            {selectedReg.status === 'pending' && (
              <div className="flex gap-4 pt-4 border-t border-gray-100">
                <button 
                  className="btn-success flex-1 justify-center"
                  onClick={() => handleAcceptClick(selectedReg.id)}
                >
                  <Check size={18} /> Accept & Generate ID
                </button>
                <button 
                  className="btn-danger flex-1 justify-center"
                  onClick={openRejectModal}
                >
                  <X size={18} /> Reject
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
            <h2 className="modal-title text-red-600">Reject Registration</h2>
            <form onSubmit={handleReject}>
              <div className="form-group">
                <label className="form-label">Reason for Rejection <span className="text-red-500">*</span></label>
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

      {/* MODAL: Confirm Accept */}
      {confirmAcceptModal && (
        <div className="modal-overlay">
          <div className="modal-content text-center" style={{ maxWidth: '400px' }}>
            <div className="mx-auto w-12 h-12 bg-green-100 text-green-600 rounded-full flex items-center justify-center mb-4">
              <Check size={24} />
            </div>
            <h2 className="text-xl font-bold text-[#1A1D2E] mb-2">Accept Registration?</h2>
            <p className="text-[#5A5F72] mb-6">This will automatically provision a user account and generate a QR token for the vehicle owner. Are you sure you want to proceed?</p>
            <div className="flex gap-3 justify-end mt-4">
              <button 
                className="btn-outline flex-1 justify-center" 
                onClick={() => setConfirmAcceptModal(null)}
                disabled={submitting}
              >
                Cancel
              </button>
              <button 
                className="btn-success flex-1 justify-center" 
                onClick={confirmAccept}
                disabled={submitting}
              >
                {submitting ? 'Processing...' : 'Confirm Accept'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* MODAL: Result/Success/Error */}
      {resultModal && (
        <div className="modal-overlay">
          <div className="modal-content text-center" style={{ maxWidth: '400px' }}>
            <div className={`mx-auto w-12 h-12 rounded-full flex items-center justify-center mb-4 ${resultModal.type === 'success' ? 'bg-green-100 text-green-600' : 'bg-red-100 text-red-600'}`}>
              {resultModal.type === 'success' ? <Check size={24} /> : <X size={24} />}
            </div>
            <h2 className="text-xl font-bold text-[#1A1D2E] mb-2">{resultModal.type === 'success' ? 'Success' : 'Error'}</h2>
            <p className="text-[#5A5F72] mb-6">{resultModal.message}</p>
            <div className="flex justify-center mt-4">
              <button 
                className="btn-primary w-full justify-center" 
                onClick={() => setResultModal(null)}
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </AdminLayout>
  )
}
