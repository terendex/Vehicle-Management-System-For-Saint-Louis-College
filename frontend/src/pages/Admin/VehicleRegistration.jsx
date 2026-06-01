import React, { useState, useEffect } from 'react'
import AdminLayout from '../../components/Layout/AdminLayout'
import { registrationApi } from '../../api/registration'
import { QRCodeSVG } from 'qrcode.react'
import { format } from 'date-fns'
import { QrCode, Copy, Check, X, Eye, Power, PowerOff, Trash2, Eraser, MoreVertical } from 'lucide-react'
import './VehicleRegistration.css'

export default function VehicleRegistration() {
  // Token State
  const [tokens, setTokens] = useState([])
  const [isTokenModalOpen, setIsTokenModalOpen] = useState(false)
  const [openActionId, setOpenActionId] = useState(null)
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
  const [isQRModalOpen, setIsQRModalOpen] = useState(false)
  const [qrDisplayData, setQrDisplayData] = useState(null)
  const [qrViewerCopied, setQrViewerCopied] = useState(false)

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

  const handleCopyTokenLink = (token) => {
    const link = `${window.location.origin}/register?token=${token}`
    navigator.clipboard.writeText(link)
    showResult("Link copied to clipboard!", "success")
  }

  const handleToggleToken = async (id) => {
    try {
      await registrationApi.toggleToken(id)
      fetchTokens()
    } catch (error) {
      console.error("Failed to toggle token:", error)
    }
  }

  const handleDeleteToken = async (id) => {
    if (!window.confirm("Are you sure you want to delete this token?")) return
    try {
      await registrationApi.deleteToken(id)
      fetchTokens()
    } catch (error) {
      console.error("Failed to delete token:", error)
    }
  }

  const handleViewTokenQR = (token) => {
    const link = `${window.location.origin}/register?token=${token}`
    openQRModal({ type: 'token', token, link }, 'Registration Invite QR Code', `${window.location.origin}/register?token=${token.substring(0, 8)}...`)
  }

  const handleViewVehicleQR = () => {
    if (!selectedReg) return
    const qrData = `VEHICLE:${selectedReg.plate_number}|ID:${selectedReg.id}`
    openQRModal(
      { type: 'vehicle', payload: qrData, plateNumber: selectedReg.plate_number, ownerName: selectedReg.full_name },
      'Vehicle Access QR Code',
      `${selectedReg.full_name} - ${selectedReg.plate_number}`,
    )
  }

  const handleCopyQRData = async () => {
    if (!qrDisplayData) return
    try {
      await navigator.clipboard.writeText(qrDisplayData.type === 'token' ? qrDisplayData.link : qrDisplayData.payload)
      setQrViewerCopied(true)
      setTimeout(() => setQrViewerCopied(false), 2000)
    } catch (err) {
      showResult('Failed to copy to clipboard.', 'error')
    }
  }

  const handleClearTokens = async () => {
    if (!window.confirm("Are you sure you want to clear all expired and used tokens?")) return
    try {
      await registrationApi.clearTokens()
      fetchTokens()
    } catch (error) {
      console.error("Failed to clear tokens:", error)
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

  const openQRModal = (qrData, title, subtitle) => {
    setQrDisplayData({ ...qrData, title, subtitle })
    setIsQRModalOpen(true)
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
          <div className="section-header">
            <h2 className="section-title">Registration QR Codes</h2>
            <div className="header-buttons">
              <button 
                className="btn-clear"
                onClick={handleClearTokens}
                title="Clear Expired & Used"
              >
                <Eraser size={16} />
                Clear Expired
              </button>
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
          </div>

          <div className="table-container">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Type</th>
                  <th>Expires At</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {paginatedTokens.map(t => (
                  <tr key={t.id}>
                    <td className="capitalize">{t.registrant_type}</td>
                    <td>{format(new Date(t.expires_at), 'PPp')}</td>
                    <td>
                      <span className={`status-badge status-${t.is_valid ? 'active' : (t.is_used ? 'disabled' : (t.is_active ? 'expired' : 'disabled'))}`}>
                        {t.is_valid ? 'Active' : (t.is_used ? 'Used' : (t.is_active ? 'Expired' : 'Disabled'))}
                      </span>
                    </td>
                    <td className="action-cell">
                      <button 
                        className="action-menu-trigger"
                        onClick={() => setOpenActionId(openActionId === t.id ? null : t.id)}
                        onBlur={() => setTimeout(() => setOpenActionId(null), 200)}
                      >
                        <MoreVertical size={18} />
                      </button>
                      
                      {openActionId === t.id && (
                        <div className="action-dropdown">
                           <button
                             className="action-dropdown-item"
                             onClick={() => {
                               handleViewTokenQR(t.token)
                               setOpenActionId(null)
                             }}
                           >
                             <Eye size={16} />
                             View QR
                           </button>
                           <button
                             className={`action-dropdown-item ${t.is_active ? 'toggle-disable' : 'toggle-enable'}`}
                             onClick={() => {
                               handleToggleToken(t.id)
                               setOpenActionId(null)
                             }}
                           >
                             {t.is_active ? <PowerOff size={16} /> : <Power size={16} />}
                             {t.is_active ? 'Disable' : 'Enable'}
                           </button>
                           <button
                             className="action-dropdown-item delete"
                             onClick={() => {
                               handleDeleteToken(t.id)
                               setOpenActionId(null)
                             }}
                           >
                             <Trash2 size={16} />
                             Delete
                           </button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
                {tokens.length === 0 && (
                  <tr className="empty-row">
                    <td colSpan="4">No tokens generated yet.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          
          {/* Token Pagination */}
          {totalTokenPages > 1 && (
            <div className="pagination-bar">
              <span className="pagination-info">Page {tokenPage} of {totalTokenPages}</span>
              <div className="pagination-buttons">
                <button 
                  className="pagination-btn"
                  disabled={tokenPage === 1}
                  onClick={() => setTokenPage(p => Math.max(1, p - 1))}
                >
                  Previous
                </button>
                <button 
                  className="pagination-btn"
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
          <div className="section-header">
            <h2 className="section-title">Registrations</h2>
            <select 
              className="filter-select"
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
                    <td className="token-link">{r.plate_number}</td>
                    <td>{format(new Date(r.created_at), 'PP')}</td>
                    <td>
                      <span className={`status-badge status-${r.status}`}>
                        {r.status.charAt(0).toUpperCase() + r.status.slice(1)}
                      </span>
                    </td>
                    <td>
                      <button 
                        className="view-btn"
                        onClick={() => openViewModal(r)}
                        title="View Details"
                      >
                        <Eye size={18} />
                      </button>
                    </td>
                  </tr>
                ))}
                {registrations.length === 0 && (
                  <tr className="empty-row">
                    <td colSpan="6">No {statusFilter} registrations found.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          
          {/* Registrations Pagination */}
          {totalRegPages > 1 && (
            <div className="pagination-bar">
              <span className="pagination-info">Page {regPage} of {totalRegPages}</span>
              <div className="pagination-buttons">
                <button 
                  className="pagination-btn"
                  disabled={regPage === 1}
                  onClick={() => setRegPage(p => Math.max(1, p - 1))}
                >
                  Previous
                </button>
                <button 
                  className="pagination-btn"
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
                  <label className="form-label">Expiration Date &amp; Time</label>
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
                    <button className="btn-outline" onClick={handleCopyLink} title="Copy Link" style={{ padding: '8px 12px' }}>
                      {copied ? <Check size={16} style={{ color: '#059669' }} /> : <Copy size={16} />}
                    </button>
                  </div>
                </div>
                <div className="modal-actions">
                  <button type="button" className="btn-primary" onClick={() => setIsTokenModalOpen(false)} style={{ width: '100%', justifyContent: 'center' }}>Close</button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* MODAL: View Registration Details */}
      {isViewModalOpen && selectedReg && (
        <div className="modal-overlay">
          <div className="modal-content modal-lg">
            <div className="modal-header">
              <h2 className="modal-title">Registration Details</h2>
              <button className="modal-close-btn" onClick={() => setIsViewModalOpen(false)}>
                <X size={24} />
              </button>
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

            {selectedReg.status === 'pending' && (
              <div className="detail-actions">
                <button
                  className="btn-success"
                  onClick={() => handleAcceptClick(selectedReg.id)}
                >
                  <Check size={18} /> Accept &amp; Generate ID
                </button>
                <button
                  className="btn-danger"
                  onClick={openRejectModal}
                >
                  <X size={18} /> Reject
                </button>
              </div>
            )}

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
            <h2 className="modal-title danger">Reject Registration</h2>
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

      {/* MODAL: Confirm Accept */}
      {confirmAcceptModal && (
        <div className="modal-overlay">
          <div className="modal-content confirm-modal">
            <div className="confirm-icon success">
              <Check size={24} />
            </div>
            <h2 className="confirm-title">Accept Registration?</h2>
            <p className="confirm-message">This will automatically provision a user account and generate a QR token for the vehicle owner. Are you sure you want to proceed?</p>
            <div className="confirm-actions">
              <button 
                className="btn-outline" 
                onClick={() => setConfirmAcceptModal(null)}
                disabled={submitting}
              >
                Cancel
              </button>
              <button 
                className="btn-success" 
                onClick={confirmAccept}
                disabled={submitting}
              >
                {submitting ? 'Processing...' : 'Confirm Accept'}
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
              <button className="modal-close-btn" onClick={() => setIsQRModalOpen(false)}>
                <X size={24} />
              </button>
            </div>
            
            <p className="qr-viewer-subtitle">{qrDisplayData.subtitle}</p>
            
            <div className="qr-display-wrapper">
              {qrDisplayData.type === 'token' ? (
                <QRCodeSVG
                  value={qrDisplayData.link}
                  size={220}
                  level="H"
                  includeMargin={true}
                />
              ) : (
                <QRCodeSVG
                  value={qrDisplayData.payload}
                  size={220}
                  level="H"
                  includeMargin={true}
                />
              )}
            </div>

            <div className="qr-data-box">
              <p className="qr-label">Encoded Data</p>
              <code className="qr-code-data">
                {qrDisplayData.type === 'token' ? qrDisplayData.link : qrDisplayData.payload}
              </code>
            </div>

            <button
              className="btn-primary"
              onClick={handleCopyQRData}
              style={{ width: '100%', justifyContent: 'center' }}
            >
              {qrViewerCopied ? <><Check size={16} />Copied!</> : <><Copy size={16} /> Copy Data</>}
            </button>
          </div>
        </div>
      )}

      {/* MODAL: Result/Success/Error */}
      {resultModal && (
        <div className="modal-overlay">
          <div className="modal-content confirm-modal">
            <div className={`confirm-icon ${resultModal.type === 'success' ? 'success' : 'error'}`}>
              {resultModal.type === 'success' ? <Check size={24} /> : <X size={24} />}
            </div>
            <h2 className="confirm-title">{resultModal.type === 'success' ? 'Success' : 'Error'}</h2>
            <p className="confirm-message">{resultModal.message}</p>
            <div className="confirm-actions">
              <button 
                className="btn-primary" 
                onClick={() => setResultModal(null)}
                style={{ width: '100%', justifyContent: 'center' }}
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
