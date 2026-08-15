import { useState, useEffect, useCallback, useRef } from 'react'
import { QRCodeSVG, QRCodeCanvas } from 'qrcode.react'
import { jsPDF } from 'jspdf'
import { useLiveUpdates } from '../../realtime/useLiveUpdates'
import { usersApi } from '../../api/users'
import useAuthStore from '../../stores/authStore'
import { useGates } from '../../hooks/useGates'
import { toUpperName, normalizeEmail } from '../../utils/textFormat'
import {
  Search, UserPlus, Eye, Ban, CheckCircle, X,
  Users, UserCheck, UserX, AlertTriangle, ShieldAlert,
  MoreVertical, ChevronLeft, ChevronRight, QrCode, Pencil,
  Shield, Info, Lock, Printer,
} from 'lucide-react'
import { toast } from 'sonner'
import './UserManagement.css'

const DEFAULT_AGENCY = 'RANNIAG'
const EMPTY_GUARD = { full_name: '', email: '', agency: DEFAULT_AGENCY }
const EMPTY_ADMIN = { full_name: '', email: '' }

/* ─── Main Component ───────────────────────────────────────────── */
export default function UserManagement() {
  const { logout } = useAuthStore()
  const { gateLabel: gateLabelFor, gateFullLabel } = useGates()

  /* ── users state ── */
  const [users, setUsers]             = useState([])
  const [loading, setLoading]         = useState(true)
  const [search, setSearch]           = useState('')
  const [resultModal, setResultModal] = useState(null)
  const [page, setPage]               = useState(1)
  const [totalPages, setTotalPages]   = useState(1)
  const [totalCount, setTotalCount]   = useState(0)
  const [activeTab, setActiveTab]     = useState('all')
  const [statusFilter, setStatusFilter] = useState('')
  const [modal, setModal]             = useState(null)
  const [selectedUser, setSelectedUser] = useState(null)
  const [submitting, setSubmitting]   = useState(false)
  const [activeMenu, setActiveMenu]   = useState(null)
  const [addType, setAddType]         = useState('guard')   // 'guard' | 'admin'
  const [formErrors, setFormErrors]   = useState({})

  /* ── per-type form state ── */
  const [guardForm, setGuardForm] = useState(EMPTY_GUARD)
  const [adminForm, setAdminForm] = useState(EMPTY_ADMIN)
  // 'RANNIAG' uses the default agency as-is; 'other' reveals a free-text input
  const [agencyMode, setAgencyMode] = useState(DEFAULT_AGENCY)

  /* ── profile edit state ── */
  const [editMode, setEditMode]           = useState(false)
  const [editForm, setEditForm]           = useState(null)
  const [editAgencyMode, setEditAgencyMode] = useState(DEFAULT_AGENCY)

  /* ── registration re-print state ── */
  const [printingReg, setPrintingReg] = useState(false)

  /* ── QR state ── */
  const [qrUser,    setQrUser]    = useState(null)
  const [qrToken,   setQrToken]   = useState(null)
  const [qrLoading, setQrLoading] = useState(false)
  const qrCanvasRef = useRef(null)  // hidden high-res canvas used to embed the QR into the PDF

  // A guard's gate comes from their last login, so an account that has never
  // signed in has none yet.
  const gateLabel = (u) => gateLabelFor(u?.gate_assignment) || 'Gate selected at login'

  /* ── Guard badge → PDF download ── */
  const downloadBadgePdf = () => {
    if (!qrUser || !qrToken) return
    const canvas = qrCanvasRef.current?.querySelector('canvas')
    if (!canvas) { toast.error('QR not ready yet — try again in a moment.'); return }
    const qrDataUrl = canvas.toDataURL('image/png')

    // ID-card sized portrait badge (85.6mm × 54mm landscape → use portrait 54×86)
    const doc = new jsPDF({ orientation: 'portrait', unit: 'mm', format: [54, 86] })
    const W = 54
    const navy = [42, 43, 97]

    // Header band
    doc.setFillColor(...navy)
    doc.rect(0, 0, W, 16, 'F')
    doc.setTextColor(255, 255, 255)
    doc.setFont('helvetica', 'bold')
    doc.setFontSize(9)
    doc.text('SAINT LOUIS COLLEGE', W / 2, 7, { align: 'center' })
    doc.setFont('helvetica', 'normal')
    doc.setFontSize(6)
    doc.text('Vehicle Management System', W / 2, 11.5, { align: 'center' })

    // Badge type
    doc.setTextColor(...navy)
    doc.setFont('helvetica', 'bold')
    doc.setFontSize(7.5)
    doc.text('SECURITY GUARD BADGE', W / 2, 22, { align: 'center' })

    // QR
    const qrSize = 32
    doc.addImage(qrDataUrl, 'PNG', (W - qrSize) / 2, 25, qrSize, qrSize)

    // Name + code + gate
    doc.setTextColor(20, 20, 30)
    doc.setFont('helvetica', 'bold')
    doc.setFontSize(9)
    doc.text(qrUser.full_name || '—', W / 2, 63, { align: 'center', maxWidth: W - 6 })
    doc.setFont('helvetica', 'normal')
    doc.setFontSize(6.5)
    doc.setTextColor(90, 95, 114)
    doc.text(qrUser.user_code || '', W / 2, 68, { align: 'center' })
    doc.text(gateLabel(qrUser), W / 2, 72, { align: 'center' })

    // Footer note
    doc.setDrawColor(226, 230, 238)
    doc.line(4, 76, W - 4, 76)
    doc.setFontSize(5)
    doc.setTextColor(140, 143, 163)
    doc.text('Scan to log in at the gate. Do not share digitally.', W / 2, 80, { align: 'center', maxWidth: W - 6 })

    const safeName = (qrUser.full_name || 'guard').replace(/[^a-z0-9]+/gi, '-').toLowerCase()
    doc.save(`guard-badge-${safeName}.pdf`)
  }

  // A guard's QR badge stays locked until they log in with their credentials
  // and replace the temporary password (clears must_change_password).
  const badgeLocked = (u) => u?.role === 'security' && u?.must_change_password

  /* ── QR open ── */
  const openQrModal = async (user) => {
    if (badgeLocked(user)) {
      toast.info('QR badge is locked until this guard logs in and changes their temporary password.')
      return
    }
    setQrUser(user)
    setQrToken(null)
    setQrLoading(true)
    if (user.role === 'security') {
      try {
        const data = await usersApi.getGuardQR(user.id)
        setQrToken(data.qr_token)
      } catch (err) {
        toast.error(err.response?.data?.detail || 'Failed to load QR token.')
        setQrUser(null)
      } finally {
        setQrLoading(false)
      }
    } else {
      setQrToken(user.user_code || user.email || String(user.id))
      setQrLoading(false)
    }
  }

  /* ── result helper ── */
  const showResult = (message, type = 'success') => {
    setResultModal({ message, type })
    setModal(null)
    setSelectedUser(null)
  }

  const TABS = [
    { key: 'all',            label: 'All Users',               role: '',              registrantType: '' },
    { key: 'security',       label: 'Security Personnel',      role: 'security',      registrantType: '' },
    { key: 'owner_employee', label: 'Vehicle Owner — Employee', role: 'vehicle_owner', registrantType: 'employee' },
    { key: 'owner_student',  label: 'Vehicle Owner — Student',  role: 'vehicle_owner', registrantType: 'student' },
    { key: 'owner_fetcher',  label: 'Fetcher / Drop & Go',      role: 'vehicle_owner', registrantType: 'fetcher' },
  ]

  /* ── fetch users ── */
  const fetchUsers = useCallback(async () => {
    setLoading(true)
    try {
      const tab = TABS.find(t => t.key === activeTab) || TABS[0]
      const data = await usersApi.getUsers(search, page, tab.role, statusFilter, tab.registrantType)
      if (data && data.results) {
        setUsers(data.results)
        setTotalCount(data.count)
        setTotalPages(Math.ceil(data.count / 10))
      } else {
        setUsers(data || [])
        setTotalCount(data ? data.length : 0)
        setTotalPages(1)
      }
    } catch {
      showResult('Failed to load users', 'error')
    } finally {
      setLoading(false)
    }
  }, [search, page, activeTab, statusFilter])

  useEffect(() => { setPage(1) }, [search, activeTab, statusFilter])
  useEffect(() => {
    const timer = setTimeout(fetchUsers, 300)
    return () => clearTimeout(timer)
  }, [fetchUsers])

  // Live-refresh the user list on any user/vehicle change
  useLiveUpdates(fetchUsers, ['user', 'vehicle', 'vehicleregistration'])

  /* ── user stats ── */
  const totalUsers    = users.length
  const activeUsers   = users.filter((u) => u.is_active).length
  const disabledUsers = users.filter((u) => !u.is_active).length

  /* ── close menu on outside click ── */
  useEffect(() => {
    const handleClickOutside = () => setActiveMenu(null)
    if (activeMenu) window.addEventListener('click', handleClickOutside)
    return () => window.removeEventListener('click', handleClickOutside)
  }, [activeMenu])

  /* ── open modals ── */
  const openAdd = () => {
    setAddType('guard')
    setGuardForm(EMPTY_GUARD)
    setAdminForm(EMPTY_ADMIN)
    setAgencyMode(DEFAULT_AGENCY)
    setFormErrors({})
    setModal('add')
  }
  const switchAddType = (t) => { setAddType(t); setFormErrors({}) }
  const openView   = (user) => { setSelectedUser(user); setEditMode(false); setModal('view') }
  const openToggle = (user) => { setSelectedUser(user); setModal('toggle') }
  const closeModal = () => {
    setModal(null); setSelectedUser(null)
    setSubmitting(false); setFormErrors({}); setEditMode(false)
  }

  /* ── profile edit ── */
  const startEdit = () => {
    setEditForm({
      full_name: selectedUser.full_name || '',
      email:     selectedUser.email || '',
      agency:    selectedUser.agency || DEFAULT_AGENCY,
      contact:   selectedUser.contact || '',
      address:   selectedUser.address || '',
    })
    setEditAgencyMode(
      !selectedUser.agency || selectedUser.agency === DEFAULT_AGENCY ? DEFAULT_AGENCY : 'other'
    )
    setFormErrors({})
    setEditMode(true)
  }

  const handleSaveEdit = async () => {
    const errors = {}
    if (!editForm.full_name.trim()) errors.full_name = 'Full name is required.'
    if (!editForm.email.trim() || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(editForm.email.trim())) errors.email = 'Enter a valid email address.'
    if (selectedUser.role === 'security' && !editForm.agency.trim()) errors.agency = 'Agency is required.'
    setFormErrors(errors)
    if (Object.keys(errors).length > 0) return
    setSubmitting(true)
    try {
      const payload = {
        full_name: editForm.full_name.trim(),
        email:     editForm.email.trim(),
      }
      if (selectedUser.role === 'security') payload.agency = editForm.agency.trim()
      if (selectedUser.role === 'vehicle_owner') {
        payload.contact = editForm.contact.trim()
        payload.address = editForm.address.trim()
      }
      await usersApi.updateUser(selectedUser.id, payload)
      setSelectedUser({ ...selectedUser, ...payload })
      setEditMode(false)
      fetchUsers()
      toast.success('User details updated.')
    } catch (err) {
      const data = err.response?.data
      const fieldErrors = {}
      for (const f of ['full_name', 'email', 'agency', 'contact', 'address']) {
        if (data?.[f]) fieldErrors[f] = Array.isArray(data[f]) ? data[f][0] : data[f]
      }
      if (Object.keys(fieldErrors).length > 0) setFormErrors(fieldErrors)
      else toast.error('Failed to update user.')
    } finally { setSubmitting(false) }
  }

  /* ── guard validation & submit ── */
  const validateGuard = () => {
    const errors = {}
    if (!guardForm.full_name.trim()) errors.full_name = 'Full name is required.'
    if (!guardForm.email.trim()) errors.email = 'Email is required.'
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(guardForm.email.trim())) errors.email = 'Enter a valid email address.'
    if (!guardForm.agency.trim()) errors.agency = 'Agency is required.'
    setFormErrors(errors)
    return Object.keys(errors).length === 0
  }

  const handleAddGuard = async () => {
    setSubmitting(true)
    try {
      const guard = await usersApi.createGuard({
        full_name: guardForm.full_name.trim(),
        email:     guardForm.email.trim(),
        agency:    guardForm.agency.trim(),
      })
      fetchUsers()
      closeModal()
      toast.success(`Guard "${guard.full_name}" created. Login credentials have been emailed. The QR badge unlocks after their first login and password change.`)
    } catch (err) {
      const data = err.response?.data
      const fieldErrors = {}
      for (const field of ['full_name', 'email', 'agency']) {
        if (data?.[field]) fieldErrors[field] = Array.isArray(data[field]) ? data[field][0] : data[field]
      }
      if (Object.keys(fieldErrors).length > 0) {
        setFormErrors(fieldErrors)
        setModal('add')
      } else {
        showResult('Failed to create guard.', 'error')
      }
    } finally { setSubmitting(false) }
  }

  const onAddClick = () => {
    if (addType === 'guard')  { if (!validateGuard()) return;  setModal('confirmAdd') }
    if (addType === 'admin')  { if (!validateAdmin()) return;  setModal('confirmAdd') }
  }

  /* ── admin validation ── */
  const validateAdmin = () => {
    const errors = {}
    if (!adminForm.full_name.trim()) errors.full_name = 'Full name is required.'
    if (!adminForm.email.trim()) errors.email = 'Email is required.'
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(adminForm.email)) errors.email = 'Invalid email format.'
    setFormErrors(errors)
    return Object.keys(errors).length === 0
  }

  const handleReplaceAdmin = async () => {
    setSubmitting(true)
    try {
      await usersApi.replaceAdmin({
        full_name: adminForm.full_name.trim(), email: adminForm.email.trim(),
      })
      showResult('CDSO replaced. Login credentials have been emailed. Logging out…')
      setTimeout(() => { logout(); window.location.href = '/login' }, 1500)
    } catch (err) {
      const data = err.response?.data
      if (data) {
        const errors = {}
        if (data.full_name) errors.full_name = Array.isArray(data.full_name) ? data.full_name[0] : data.full_name
        if (data.email) errors.email = Array.isArray(data.email) ? data.email[0] : data.email
        if (data.password) errors.password = Array.isArray(data.password) ? data.password.join(' ') : data.password
        setFormErrors(errors); setModal('add')
      } else { showResult('Failed to replace CDSO', 'error') }
      setSubmitting(false)
    }
  }

  /* ── Registration confirmation → PDF re-print ──
     For an owner who lost the copy emailed to them on approval. The server
     rebuilds the same document, so the reprint matches the original. */
  // Takes the user explicitly: the row menu calls this straight after
  // setSelectedUser, and reading that state back here would still see the
  // previous value.
  const printRegistrationFor = async (user) => {
    if (!user) return
    setPrintingReg(true)
    try {
      const blob = await usersApi.getRegistrationPdf(user.id)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `SLC Vehicle Registration - ${user.full_name}.pdf`
      a.click()
      URL.revokeObjectURL(url)
      toast.success('Registration PDF downloaded.')
    } catch (err) {
      // responseType 'blob' means an error body arrives as a Blob, not JSON —
      // read it back or the toast would only ever say "failed".
      let message = 'Failed to generate the registration PDF.'
      try {
        const body = err.response?.data
        if (body instanceof Blob) {
          const parsed = JSON.parse(await body.text())
          if (parsed?.detail) message = parsed.detail
        } else if (body?.detail) {
          message = body.detail
        }
      } catch { /* keep the generic message */ }
      toast.error(message)
    } finally {
      setPrintingReg(false)
    }
  }

  const handleToggle = async () => {
    setSubmitting(true)
    try {
      await usersApi.toggleUserStatus(selectedUser.id)
      fetchUsers()
      showResult(`User ${selectedUser.is_active ? 'disabled' : 'enabled'} successfully!`)
    } catch { showResult('Failed to toggle user status', 'error') }
    finally { setSubmitting(false) }
  }

  const formatDate    = (iso) => iso ? new Date(iso).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' }) : '—'
  const roleLabel     = (user) => {
    if (user.role === 'security') return 'Security Personnel'
    if (user.role === 'vehicle_owner') {
      if (user.registrant_type === 'student')  return 'Student Owner'
      if (user.registrant_type === 'employee') return 'Employee Owner'
      if (user.registrant_type === 'fetcher')  return 'Fetcher'
      return 'Vehicle Owner'
    }
    if (user.role === 'admin') return 'CDSO'
    return user.role
  }
  const roleBadgeClass = (user) => {
    if (user.role === 'security') return 'security'
    if (user.role === 'vehicle_owner') {
      if (user.registrant_type === 'student')  return 'owner_student'
      if (user.registrant_type === 'employee') return 'owner_employee'
      if (user.registrant_type === 'fetcher')  return 'owner_fetcher'
    }
    return 'vehicle_owner'
  }

  /* ─── render ─────────────────────────────────────────────────── */
  return (
    <>

      {/* Page Header */}
      <div className="um-header">
        <div className="um-header-left">
          <h1>User Management</h1>
          <p>Manage system user accounts and access.</p>
        </div>
        <div className="um-header-actions">
          <button className="um-add-btn" onClick={openAdd}>
            <UserPlus size={16} /> Add User
          </button>
        </div>
      </div>

      {/* Stats */}
      <div className="um-stats-bar">
        <div className="um-stat-card">
          <div className="um-stat-icon total"><Users size={20} /></div>
          <div className="um-stat-info"><h4>Total Users</h4><span>{totalUsers}</span></div>
        </div>
        <div className="um-stat-card">
          <div className="um-stat-icon active-stat"><UserCheck size={20} /></div>
          <div className="um-stat-info"><h4>Active</h4><span>{activeUsers}</span></div>
        </div>
        <div className="um-stat-card">
          <div className="um-stat-icon disabled-stat"><UserX size={20} /></div>
          <div className="um-stat-info"><h4>Disabled</h4><span>{disabledUsers}</span></div>
        </div>
      </div>

      {/* Table */}
      <div className="um-table-container">
        {/* Role tabs */}
        <div className="um-role-tabs">
          {TABS.map(tab => (
            <button
              key={tab.key}
              className={`um-role-tab ${activeTab === tab.key ? 'active' : ''}`}
              onClick={() => setActiveTab(tab.key)}
            >
              {tab.label}
            </button>
          ))}
        </div>

        <div className="um-table-toolbar">
          <div className="um-search-wrapper">
            <Search size={16} />
            <input
              className="um-search-input"
              type="text"
              placeholder="Search by name, email, or user ID…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <div className="um-filter-group">
            <select className="um-form-select" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="">All Statuses</option>
              <option value="active">Active</option>
              <option value="disabled">Disabled</option>
            </select>
          </div>
        </div>

        {loading ? (
          <div className="um-loading"><div className="um-spinner" /><p>Loading users…</p></div>
        ) : users.length === 0 ? (
          <div className="um-empty">
            <Users size={48} />
            <h3>No users found</h3>
            <p>{search ? 'Try a different search term.' : 'Click "Add User" to create the first account.'}</p>
          </div>
        ) : (
          <table className="um-table">
            <thead>
              <tr>
                <th>User ID</th>
                <th>Full Name</th>
                <th>Email</th>
                <th>Role</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <td>
                    <span style={{ fontFamily: 'monospace', fontSize: '11px', color: '#5C7B92', fontWeight: 600 }}>
                      {u.user_code || `#${u.id}`}
                    </span>
                  </td>
                  <td>
                    <div className="um-user-cell">
                      <div className={`um-user-avatar ${roleBadgeClass(u)}`}>
                        {u.full_name.charAt(0).toUpperCase()}
                      </div>
                      <span className="um-user-name">{u.full_name}</span>
                    </div>
                  </td>
                  <td>{u.email || '—'}</td>
                  <td>
                    <span className={`um-role-badge ${roleBadgeClass(u)}`}>{roleLabel(u)}</span>
                  </td>
                  <td>
                    <span className={`um-status-badge ${u.is_active ? 'active' : 'disabled'}`}>
                      <span className="status-dot" />
                      {u.is_active ? 'Active' : 'Disabled'}
                    </span>
                  </td>
                  {/* QR lives in View Profile — no column here */}
                  <td style={{ position: 'relative' }}>
                    <button
                      className="um-action-btn"
                      onClick={(e) => { e.stopPropagation(); setActiveMenu(activeMenu === u.id ? null : u.id) }}
                    >
                      <MoreVertical size={16} />
                    </button>
                    {activeMenu === u.id && (
                      <div className="um-actions-dropdown" onClick={(e) => e.stopPropagation()}>
                        <button className="um-dropdown-item view" onClick={() => { openView(u); setActiveMenu(null) }}>
                          <Eye size={15} /> View Profile
                        </button>
                        {u.role === 'security' && (
                          <button
                            className="um-dropdown-item view"
                            disabled={badgeLocked(u)}
                            title={badgeLocked(u) ? 'Locked — guard must log in and change their temporary password first' : undefined}
                            onClick={() => openQrModal(u)}
                          >
                            {badgeLocked(u) ? <Lock size={15} /> : <QrCode size={15} />} QR Badge{badgeLocked(u) ? ' (locked)' : ''}
                          </button>
                        )}
                        {u.role === 'vehicle_owner' && (
                          <button
                            className="um-dropdown-item view"
                            title="Re-download the approved registration PDF emailed to this owner"
                            onClick={() => { setSelectedUser(u); setActiveMenu(null); printRegistrationFor(u) }}
                          >
                            <Printer size={15} /> Print Registration
                          </button>
                        )}
                        <button
                          className={`um-dropdown-item ${u.is_active ? 'disable' : 'enable'}`}
                          onClick={() => { openToggle(u); setActiveMenu(null) }}
                        >
                          {u.is_active ? <><Ban size={15} /> Disable</> : <><CheckCircle size={15} /> Enable</>}
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {!loading && users.length > 0 && totalPages > 1 && (
          <div className="um-pagination">
            <span className="um-pagination-info">
              Showing {(page - 1) * 10 + 1} to {Math.min(page * 10, totalCount)} of {totalCount} users
            </span>
            <div className="um-pagination-controls">
              <button className="um-page-btn" disabled={page === 1} onClick={() => setPage(p => p - 1)}>
                <ChevronLeft size={16} />
              </button>
              <span className="um-page-current">Page {page} of {totalPages}</span>
              <button className="um-page-btn" disabled={page === totalPages} onClick={() => setPage(p => p + 1)}>
                <ChevronRight size={16} />
              </button>
            </div>
          </div>
        )}
      </div>

      {/* ── MODALS ── */}

      {/* ADD USER */}
      {modal === 'add' && (
        <div className="um-modal-overlay" onClick={closeModal}>
          <div className="um-modal um-modal--wide" onClick={(e) => e.stopPropagation()}>
            <div className="um-modal-header">
              <h2>{addType === 'admin' ? 'Replace CDSO' : 'Add Security Guard'}</h2>
              <button className="um-modal-close" onClick={closeModal}><X size={18} /></button>
            </div>

            <div className="um-modal-body um-modal-scroll">

              {/* ── type switcher — always visible so the form can be swapped freely ── */}
              <div className="um-user-type-selector" style={{ marginBottom: 18 }}>
                <div
                  className="um-type-option"
                  onClick={() => switchAddType('guard')}
                  style={addType === 'guard'
                    ? { borderColor: '#03396C', background: '#F7FAFC', boxShadow: '0 0 0 1px #03396C' }
                    : { opacity: 0.75 }}
                >
                  <div className="um-type-option-icon"><Shield size={20} /></div>
                  <span className="um-type-option-label">Security Guard</span>
                  <span className="um-type-option-desc">Name, email & agency — QR badge auto-generated</span>
                </div>
                <div
                  className="um-type-option"
                  onClick={() => switchAddType('admin')}
                  style={addType === 'admin'
                    ? { borderColor: '#8A6B00', background: '#FEF9E4', boxShadow: '0 0 0 1px #8A6B00' }
                    : { opacity: 0.75 }}
                >
                  <div className="um-type-option-icon"><ShieldAlert size={20} /></div>
                  <span className="um-type-option-label">New CDSO</span>
                  <span className="um-type-option-desc">Replaces current CDSO account</span>
                </div>
              </div>

              {/* ══ GUARD FORM ══ */}
              {addType === 'guard' && (
                <>
                  <div className="um-info-banner">
                    <Info size={14} />
                    A temporary password will be auto-generated and emailed to the guard.
                    They must change it on first login. Their QR badge (alternative login) unlocks after that first login and password change.
                  </div>
                  <div className="um-form-group">
                    <label>Full Name <span className="um-required">*</span></label>
                    <input className={`um-form-input ${formErrors.full_name ? 'error' : ''}`}
                      value={guardForm.full_name}
                      onChange={e => setGuardForm({ ...guardForm, full_name: e.target.value })}
                      onBlur={e => setGuardForm(f => ({ ...f, full_name: toUpperName(e.target.value) }))}
                      placeholder="e.g. Juan Dela Cruz" />
                    {formErrors.full_name && <div className="um-form-error">{formErrors.full_name}</div>}
                  </div>
                  <div className="um-form-group">
                    <label>Email <span className="um-required">*</span></label>
                    <input className={`um-form-input ${formErrors.email ? 'error' : ''}`}
                      type="email"
                      value={guardForm.email}
                      onChange={e => setGuardForm({ ...guardForm, email: e.target.value })}
                      onBlur={e => setGuardForm(f => ({ ...f, email: normalizeEmail(e.target.value) }))}
                      placeholder="e.g. guard@slc.edu.ph" />
                    {formErrors.email && <div className="um-form-error">{formErrors.email}</div>}
                  </div>
                  <div className="um-form-group">
                    <label>Agency <span className="um-required">*</span></label>
                    <select
                      className="um-form-select"
                      value={agencyMode}
                      onChange={e => {
                        const mode = e.target.value
                        setAgencyMode(mode)
                        setGuardForm({ ...guardForm, agency: mode === 'other' ? '' : DEFAULT_AGENCY })
                      }}
                    >
                      <option value={DEFAULT_AGENCY}>{DEFAULT_AGENCY} (default)</option>
                      <option value="other">Other…</option>
                    </select>
                    {agencyMode === 'other' && (
                      <input className={`um-form-input ${formErrors.agency ? 'error' : ''}`}
                        style={{ marginTop: 8 }}
                        value={guardForm.agency}
                        onChange={e => setGuardForm({ ...guardForm, agency: e.target.value })}
                        placeholder="Enter agency name" />
                    )}
                    {formErrors.agency && <div className="um-form-error">{formErrors.agency}</div>}
                  </div>
                </>
              )}

              {/* ══ ADMIN FORM ══ */}
              {addType === 'admin' && (
                <>
                  <div className="um-admin-warning">
                    <AlertTriangle size={18} />
                    <div className="um-admin-warning-text">
                      <strong>Warning:</strong> This will <strong>delete your current CDSO account</strong> and create a new one. You will be logged out immediately.
                    </div>
                  </div>
                  <div className="um-info-banner">
                    <Info size={14} />
                    A temporary password will be auto-generated and emailed to the new CDSO.
                    They must change it on first login.
                  </div>
                  <div className="um-form-group">
                    <label>Full Name <span className="um-required">*</span></label>
                    <input className={`um-form-input ${formErrors.full_name ? 'error' : ''}`}
                      value={adminForm.full_name}
                      onChange={e => setAdminForm({ ...adminForm, full_name: e.target.value })}
                      onBlur={e => setAdminForm(f => ({ ...f, full_name: toUpperName(e.target.value) }))}
                      placeholder="Enter full name" />
                    {formErrors.full_name && <div className="um-form-error">{formErrors.full_name}</div>}
                  </div>
                  <div className="um-form-group">
                    <label>Email <span className="um-required">*</span></label>
                    <input className={`um-form-input ${formErrors.email ? 'error' : ''}`}
                      type="email" value={adminForm.email}
                      onChange={e => setAdminForm({ ...adminForm, email: e.target.value })}
                      onBlur={e => setAdminForm(f => ({ ...f, email: normalizeEmail(e.target.value) }))}
                      placeholder="Enter email address" />
                    {formErrors.email && <div className="um-form-error">{formErrors.email}</div>}
                  </div>
                </>
              )}
            </div>

            <div className="um-modal-footer">
              <button className="um-btn-secondary" onClick={closeModal}>Cancel</button>
              {addType === 'admin' ? (
                <button className="um-btn-warning" disabled={submitting} onClick={onAddClick}>
                  <ShieldAlert size={16} /> Continue
                </button>
              ) : (
                <button className="um-btn-primary" disabled={submitting} onClick={onAddClick}>
                  <UserPlus size={16} /> Continue
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* VIEW PROFILE */}
      {modal === 'view' && selectedUser && (
        <div className="um-modal-overlay" onClick={closeModal}>
          {/* Wider than the confirm dialogs: this one carries a two-column
              field grid and up to four footer actions. */}
          <div className="um-modal um-modal--profile" onClick={(e) => e.stopPropagation()}>
            <div className="um-modal-header">
              <h2>User Profile</h2>
              <button className="um-modal-close" onClick={closeModal}><X size={18} /></button>
            </div>
            <div className="um-modal-body">
              <div style={{ textAlign: 'center', marginBottom: 24 }}>
                <div style={{ display: 'inline-block', marginBottom: 12 }}>
                  {selectedUser.photo_url ? (
                    <img
                      src={selectedUser.photo_url}
                      alt={selectedUser.full_name}
                      style={{ width: 72, height: 72, borderRadius: '50%', objectFit: 'cover', border: '2px solid #D3E1EC', display: 'block' }}
                    />
                  ) : (
                    <div
                      className={`um-user-avatar ${roleBadgeClass(selectedUser)}`}
                      style={{ width: 72, height: 72, fontSize: 28, margin: 0 }}
                    >
                      {selectedUser.full_name.charAt(0).toUpperCase()}
                    </div>
                  )}
                </div>
                <h3 style={{ margin: 0, color: '#0B2340', fontSize: 18 }}>{selectedUser.full_name}</h3>
                <span className={`um-role-badge ${roleBadgeClass(selectedUser)}`} style={{ marginTop: 8, display: 'inline-flex' }}>
                  {roleLabel(selectedUser)}
                </span>
              </div>
              {editMode && editForm ? (
                <>
                  <div className="um-form-group">
                    <label>Full Name <span className="um-required">*</span></label>
                    <input className={`um-form-input ${formErrors.full_name ? 'error' : ''}`}
                      value={editForm.full_name}
                      onChange={e => setEditForm({ ...editForm, full_name: e.target.value })} />
                    {formErrors.full_name && <div className="um-form-error">{formErrors.full_name}</div>}
                  </div>
                  <div className="um-form-group">
                    <label>Email <span className="um-required">*</span></label>
                    <input className={`um-form-input ${formErrors.email ? 'error' : ''}`}
                      type="email"
                      value={editForm.email}
                      onChange={e => setEditForm({ ...editForm, email: e.target.value })} />
                    {formErrors.email && <div className="um-form-error">{formErrors.email}</div>}
                  </div>
                  {selectedUser.role === 'security' && (
                    <div className="um-form-group">
                      <label>Agency <span className="um-required">*</span></label>
                      <select
                        className="um-form-select"
                        value={editAgencyMode}
                        onChange={e => {
                          const mode = e.target.value
                          setEditAgencyMode(mode)
                          setEditForm({ ...editForm, agency: mode === 'other' ? '' : DEFAULT_AGENCY })
                        }}
                      >
                        <option value={DEFAULT_AGENCY}>{DEFAULT_AGENCY} (default)</option>
                        <option value="other">Other…</option>
                      </select>
                      {editAgencyMode === 'other' && (
                        <input className={`um-form-input ${formErrors.agency ? 'error' : ''}`}
                          style={{ marginTop: 8 }}
                          value={editForm.agency}
                          onChange={e => setEditForm({ ...editForm, agency: e.target.value })}
                          placeholder="Enter agency name" />
                      )}
                      {formErrors.agency && <div className="um-form-error">{formErrors.agency}</div>}
                    </div>
                  )}
                  {selectedUser.role === 'vehicle_owner' && (
                    <>
                      <div className="um-form-group">
                        <label>Contact Number</label>
                        <input className="um-form-input"
                          value={editForm.contact}
                          onChange={e => setEditForm({ ...editForm, contact: e.target.value })}
                          placeholder="e.g. 09xxxxxxxxx" />
                      </div>
                      <div className="um-form-group">
                        <label>Address</label>
                        <input className="um-form-input"
                          value={editForm.address}
                          onChange={e => setEditForm({ ...editForm, address: e.target.value })}
                          placeholder="Home address" />
                      </div>
                    </>
                  )}
                </>
              ) : (
                // Field order pairs the short values into columns and gives the
                // long free-text ones a row of their own, so nothing has to wrap
                // mid-word to fit a half-width cell.
                <div className="um-profile-grid">
                  <div className="um-profile-item">
                    <span className="um-profile-label">Status</span>
                    <span className={`um-status-badge ${selectedUser.is_active ? 'active' : 'disabled'}`}>
                      <span className="status-dot" />{selectedUser.is_active ? 'Active' : 'Disabled'}
                    </span>
                  </div>
                  <div className="um-profile-item">
                    <span className="um-profile-label">User ID</span>
                    <span className="um-profile-value" style={{ fontFamily: 'monospace', fontWeight: 700, color: '#03396C' }}>
                      {selectedUser.user_code || `#${selectedUser.id}`}
                    </span>
                  </div>
                  <div className="um-profile-item full-width">
                    <span className="um-profile-label">Email</span>
                    <span className="um-profile-value">{selectedUser.email || '—'}</span>
                  </div>
                  <div className="um-profile-item">
                    <span className="um-profile-label">Date Joined</span>
                    <span className="um-profile-value">{formatDate(selectedUser.date_joined)}</span>
                  </div>
                  {selectedUser.role === 'security' && (
                    <>
                      <div className="um-profile-item">
                        <span className="um-profile-label">Agency</span>
                        <span className="um-profile-value">{selectedUser.agency || '—'}</span>
                      </div>
                      <div className="um-profile-item">
                        <span className="um-profile-label">Last Gate Login</span>
                        <span className="um-profile-value">
                          {gateFullLabel(selectedUser.gate_assignment) || 'Not yet logged in'}
                        </span>
                      </div>
                    </>
                  )}
                  {selectedUser.role === 'vehicle_owner' && (
                    <>
                      <div className="um-profile-item">
                        <span className="um-profile-label">Contact Number</span>
                        <span className="um-profile-value">{selectedUser.contact || '—'}</span>
                      </div>
                      {/* Full width: an address is the one free-text field long
                          enough to wrap and drag the row beside it out of line. */}
                      <div className="um-profile-item full-width">
                        <span className="um-profile-label">Address</span>
                        <span className="um-profile-value">{selectedUser.address || '—'}</span>
                      </div>
                    </>
                  )}
                </div>
              )}
            </div>
            <div className="um-modal-footer">
              {editMode ? (
                <>
                  <button className="um-btn-secondary" disabled={submitting} onClick={() => { setEditMode(false); setFormErrors({}) }}>Cancel</button>
                  <button className="um-btn-primary" disabled={submitting} onClick={handleSaveEdit}>
                    <CheckCircle size={15} /> {submitting ? 'Saving…' : 'Save Changes'}
                  </button>
                </>
              ) : (
                <>
                  <button className="um-btn-secondary um-footer-spacer" onClick={closeModal}>Close</button>
                  <button className="um-btn-secondary" onClick={startEdit}>
                    <Pencil size={15} /> Edit Details
                  </button>
                  {selectedUser.role === 'vehicle_owner' && (
                    <button
                      className="um-btn-secondary"
                      disabled={printingReg}
                      title="Re-download the approved registration PDF that was emailed to this owner"
                      onClick={() => printRegistrationFor(selectedUser)}
                    >
                      <Printer size={15} /> {printingReg ? 'Preparing…' : 'Print Registration'}
                    </button>
                  )}
                  <button
                    className="um-btn-primary"
                    disabled={badgeLocked(selectedUser)}
                    title={badgeLocked(selectedUser) ? 'Locked — guard must log in and change their temporary password first' : undefined}
                    onClick={() => { closeModal(); openQrModal(selectedUser) }}
                  >
                    {badgeLocked(selectedUser) ? <Lock size={15} /> : <QrCode size={15} />} View QR
                  </button>
                </>
              )}
            </div>
          </div>
        </div>
      )}

      {/* TOGGLE STATUS */}
      {modal === 'toggle' && selectedUser && (
        <div className="um-modal-overlay" onClick={closeModal}>
          <div className="um-modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 420 }}>
            <div className="um-modal-header">
              <h2>{selectedUser.is_active ? 'Disable' : 'Enable'} User</h2>
              <button className="um-modal-close" onClick={closeModal}><X size={18} /></button>
            </div>
            <div className="um-modal-body">
              <div className="um-confirm-body">
                <div className={`um-confirm-icon ${selectedUser.is_active ? 'warning' : 'info'}`}>
                  {selectedUser.is_active ? <Ban size={24} /> : <CheckCircle size={24} />}
                </div>
                <h3>{selectedUser.is_active ? 'Disable this user?' : 'Enable this user?'}</h3>
                <p>
                  {selectedUser.is_active
                    ? <><span className="um-confirm-name">{selectedUser.full_name}</span> will no longer be able to log in.</>
                    : <><span className="um-confirm-name">{selectedUser.full_name}</span> will be able to log in again.</>}
                </p>
              </div>
            </div>
            <div className="um-modal-footer">
              <button className="um-btn-secondary" onClick={closeModal}>Cancel</button>
              {selectedUser.is_active ? (
                <button className="um-btn-warning" disabled={submitting} onClick={handleToggle}>
                  <Ban size={16} /> {submitting ? 'Disabling…' : 'Disable User'}
                </button>
              ) : (
                <button className="um-btn-primary" disabled={submitting} onClick={handleToggle}>
                  <CheckCircle size={16} /> {submitting ? 'Enabling…' : 'Enable User'}
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* CONFIRM ADD */}
      {modal === 'confirmAdd' && (
        <div className="um-modal-overlay">
          <div className="um-modal" style={{ maxWidth: 420 }}>
            <div className="um-modal-header"><h2>Confirm Creation</h2></div>
            <div className="um-modal-body">
              <div className="um-confirm-body">
                <div className="um-confirm-icon info"><UserPlus size={24} /></div>
                <h3>{addType === 'admin' ? 'Replace CDSO?' : 'Create User?'}</h3>
                <p>Are you sure you want to {addType === 'admin' ? 'replace the current CDSO' : 'create this new account'}?</p>
              </div>
            </div>
            <div className="um-modal-footer">
              <button className="um-btn-secondary" disabled={submitting} onClick={() => setModal('add')}>Back</button>
              <button className="um-btn-primary" disabled={submitting} onClick={addType === 'admin' ? handleReplaceAdmin : handleAddGuard}>
                {submitting ? 'Processing...' : 'Confirm'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* QR MODAL */}
      {qrUser && (
        <div className="um-modal-overlay" onClick={() => setQrUser(null)}>
          <div className="um-modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 400, textAlign: 'center' }}>
            <div className="um-modal-header">
              <h2>
                <QrCode size={16} style={{ verticalAlign: 'middle', marginRight: 6 }} />
                {qrUser.role === 'security' ? 'Guard QR Badge' : 'Owner ID QR'}
              </h2>
              <button className="um-modal-close" onClick={() => setQrUser(null)}><X size={18} /></button>
            </div>
            <div className="um-modal-body" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 16, padding: '24px 20px' }}>
              <div>
                <p style={{ margin: 0, fontWeight: 700, fontSize: 15 }}>{qrUser.full_name}</p>
                <p style={{ margin: '2px 0 0', fontSize: 12, color: '#5C7B92' }}>
                  {qrUser.user_code}
                  {qrUser.role === 'security' && (
                    <> · {gateLabel(qrUser)}</>
                  )}
                  {qrUser.role !== 'security' && <> · Vehicle Owner</>}
                </p>
              </div>
              {qrLoading ? (
                <div style={{ width: 200, height: 200, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <div className="um-spinner" />
                </div>
              ) : qrToken ? (
                <>
                  <div style={{ padding: 14, background: '#fff', border: '2px solid #D3E1EC', borderRadius: 14, boxShadow: '0 2px 12px rgba(3, 57, 108,0.08)' }}>
                    <QRCodeSVG value={qrToken} size={190} level="M" />
                  </div>
                  {/* Hidden high-res canvas — source for the PDF badge export */}
                  <div ref={qrCanvasRef} style={{ position: 'absolute', width: 0, height: 0, overflow: 'hidden', opacity: 0, pointerEvents: 'none' }} aria-hidden="true">
                    <QRCodeCanvas value={qrToken} size={512} level="M" />
                  </div>
                  {qrUser.role === 'security' ? (
                    <>
                      <p style={{ margin: 0, fontSize: 11, color: '#64839C', wordBreak: 'break-all', maxWidth: 320 }}>
                        Token: {qrToken}
                      </p>
                      <p style={{ margin: 0, fontSize: 11, color: '#8A6B00', background: '#FDF0BE', border: '1px solid #F7E08A', borderRadius: 6, padding: '4px 10px' }}>
                        Print this QR code as the guard's badge. Do not share digitally.
                      </p>
                    </>
                  ) : (
                    <p style={{ margin: 0, fontSize: 11, color: '#4A6B85', background: '#EEF4F9', borderRadius: 6, padding: '5px 12px' }}>
                      Scan to identify this vehicle owner — encodes their User ID.
                    </p>
                  )}
                </>
              ) : null}
            </div>
            <div className="um-modal-footer" style={{ justifyContent: qrUser.role === 'security' ? 'space-between' : 'flex-end' }}>
              {qrUser.role === 'security' && (
                <button
                  className="um-btn-primary"
                  onClick={downloadBadgePdf}
                  disabled={qrLoading || !qrToken}
                  title="Download this guard's name & QR badge as a PDF"
                >
                  <QrCode size={15} style={{ marginRight: 6, verticalAlign: 'middle' }} />
                  Download PDF
                </button>
              )}
              <button className="um-btn-secondary" onClick={() => setQrUser(null)}>Close</button>
            </div>
          </div>
        </div>
      )}

      {/* RESULT MODAL */}
      {resultModal && (
        <div className="um-modal-overlay">
          <div className="um-modal" style={{ maxWidth: 420 }}>
            <div className="um-modal-header">
              <h2>{resultModal.type === 'success' ? 'Success' : 'Error'}</h2>
            </div>
            <div className="um-modal-body">
              <div className="um-confirm-body">
                <div className={`um-confirm-icon ${resultModal.type === 'success' ? 'active-stat' : 'danger'}`}>
                  {resultModal.type === 'success' ? <CheckCircle size={24} /> : <AlertTriangle size={24} />}
                </div>
                <h3>{resultModal.message}</h3>
              </div>
            </div>
            <div className="um-modal-footer" style={{ justifyContent: 'center' }}>
              <button className="um-btn-primary" onClick={() => setResultModal(null)}>Close</button>
            </div>
          </div>
        </div>
      )}

    </>
  )
}
