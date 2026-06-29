import { useState, useEffect, useCallback, useRef } from 'react'
import { QRCodeSVG } from 'qrcode.react'
import AdminLayout from '../../components/Layout/AdminLayout'
import { usersApi } from '../../api/users'
import { authApi } from '../../api/auth'
import useAuthStore from '../../stores/authStore'
import {
  Search, UserPlus, Eye, Ban, CheckCircle, Trash2, X,
  Users, UserCheck, UserX, AlertTriangle, ShieldAlert, EyeOff,
  Check, Circle, MoreVertical, ChevronLeft, ChevronRight, QrCode, RefreshCw,
} from 'lucide-react'
import { toast } from 'sonner'
import './UserManagement.css'

/* ─── password helpers ─────────────────────────────────────────── */
const PASSWORD_RULES = [
  { key: 'length',  label: 'At least 8 characters',        test: (p) => p.length >= 8 },
  { key: 'upper',   label: 'One uppercase letter',         test: (p) => /[A-Z]/.test(p) },
  { key: 'lower',   label: 'One lowercase letter',         test: (p) => /[a-z]/.test(p) },
  { key: 'number',  label: 'One number',                    test: (p) => /[0-9]/.test(p) },
  { key: 'special', label: 'One special character (!@#$…)', test: (p) => /[!@#$%^&*()_+\-=[\]{};':"\\|,.<>/?]/.test(p) },
]

function getStrength(pw) {
  if (!pw) return { level: '', score: 0 }
  const passed = PASSWORD_RULES.filter((r) => r.test(pw)).length
  if (passed <= 1) return { level: 'weak', score: 1 }
  if (passed === 2) return { level: 'fair', score: 2 }
  if (passed === 3) return { level: 'good', score: 3 }
  if (passed === 4) return { level: 'strong', score: 4 }
  return { level: 'excellent', score: 5 }
}
const STRENGTH_LABELS = { weak: 'Weak', fair: 'Fair', good: 'Good', strong: 'Strong', excellent: 'Excellent' }

/* ─── Main Component ───────────────────────────────────────────── */
export default function UserManagement() {
  const { logout } = useAuthStore()

  /* ── users state ── */
  const [users, setUsers]             = useState([])
  const [loading, setLoading]         = useState(true)
  const [search, setSearch]           = useState('')
  const [resultModal, setResultModal] = useState(null)
  const [page, setPage]               = useState(1)
  const [totalPages, setTotalPages]   = useState(1)
  const [totalCount, setTotalCount]   = useState(0)
  const [roleFilter, setRoleFilter]   = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [modal, setModal]             = useState(null)
  const [selectedUser, setSelectedUser] = useState(null)
  const [submitting, setSubmitting]   = useState(false)
  const [activeMenu, setActiveMenu]   = useState(null)
  const [form, setForm]               = useState({ full_name: '', email: '', role: 'security', password: '', confirm_password: '' })
  const [showPassword, setShowPassword] = useState(false)
  const [showConfirm, setShowConfirm]   = useState(false)
  const [formErrors, setFormErrors]   = useState({})
  const [addType, setAddType]         = useState(null)

  /* ── QR state ── */
  const [qrUser,    setQrUser]    = useState(null)
  const [qrToken,   setQrToken]   = useState(null)
  const [qrLoading, setQrLoading] = useState(false)

  /* ── QR open ── */
  const openQrModal = async (user) => {
    setQrUser(user)
    setQrToken(null)
    setQrLoading(true)
    if (user.role === 'security') {
      try {
        const data = await usersApi.getGuardQR(user.id)
        setQrToken(data.qr_token)
      } catch {
        toast.error('Failed to load QR token.')
        setQrUser(null)
      } finally {
        setQrLoading(false)
      }
    } else {
      setQrToken(user.user_code || user.email || String(user.id))
      setQrLoading(false)
    }
  }

  const handleRegenerateQR = async () => {
    if (!qrUser || qrUser.role !== 'security') return
    setQrLoading(true)
    try {
      const data = await usersApi.regenerateGuardQR(qrUser.id)
      setQrToken(data.qr_token)
      toast.success('QR badge regenerated. Old badge is now invalid.')
    } catch {
      toast.error('Failed to regenerate QR.')
    } finally {
      setQrLoading(false)
    }
  }

  /* ── result helper ── */
  const showResult = (message, type = 'success') => {
    setResultModal({ message, type })
    setModal(null)
    setSelectedUser(null)
    setAddType(null)
  }

  /* ── fetch users ── */
  const fetchUsers = useCallback(async () => {
    setLoading(true)
    try {
      const data = await usersApi.getUsers(search, page, roleFilter, statusFilter)
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
  }, [search, page, roleFilter, statusFilter])

  useEffect(() => { setPage(1) }, [search, roleFilter, statusFilter])
  useEffect(() => {
    const timer = setTimeout(fetchUsers, 300)
    return () => clearTimeout(timer)
  }, [fetchUsers])

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
    setAddType(null)
    setForm({ full_name: '', email: '', role: 'security', gate_assignment: '', password: '', confirm_password: '' })
    setFormErrors({})
    setShowPassword(false)
    setShowConfirm(false)
    setModal('add')
  }
  const openView   = (user) => { setSelectedUser(user); setModal('view') }
  const openDelete = (user) => { setSelectedUser(user); setModal('delete') }
  const openToggle = (user) => { setSelectedUser(user); setModal('toggle') }
  const closeModal = () => {
    setModal(null); setSelectedUser(null)
    setSubmitting(false); setFormErrors({}); setAddType(null)
  }

  /* ── form validation ── */
  const validateForm = () => {
    const errors = {}
    if (!form.full_name.trim()) errors.full_name = 'Full name is required.'
    if (!form.email.trim()) errors.email = 'Email is required.'
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email)) errors.email = 'Invalid email format.'
    if (modal === 'add') {
      if (!form.password) errors.password = 'Password is required.'
      else if (getStrength(form.password).score < 5) errors.password = 'Password does not meet all requirements.'
      if (!form.confirm_password) errors.confirm_password = 'Please confirm password.'
      else if (form.password !== form.confirm_password) errors.confirm_password = 'Passwords do not match.'
    }
    setFormErrors(errors)
    return Object.keys(errors).length === 0
  }
  const onAddClick = () => { if (!validateForm()) return; setModal('confirmAdd') }

  const handleAddUser = async () => {
    setSubmitting(true)
    try {
      const payload = {
        full_name: form.full_name.trim(), email: form.email.trim(),
        role: form.role, password: form.password, confirm_password: form.confirm_password,
      }
      if (form.role === 'security' && form.gate_assignment) payload.gate_assignment = form.gate_assignment
      await usersApi.createUser(payload)
      fetchUsers()
      showResult('User created successfully!')
    } catch (err) {
      const data = err.response?.data
      if (data) {
        const errors = {}
        if (data.full_name) errors.full_name = Array.isArray(data.full_name) ? data.full_name[0] : data.full_name
        if (data.email) errors.email = Array.isArray(data.email) ? data.email[0] : data.email
        if (data.password) errors.password = Array.isArray(data.password) ? data.password.join(' ') : data.password
        if (data.confirm_password) errors.confirm_password = Array.isArray(data.confirm_password) ? data.confirm_password[0] : data.confirm_password
        if (data.non_field_errors) errors.general = Array.isArray(data.non_field_errors) ? data.non_field_errors.join(' ') : data.non_field_errors
        setFormErrors(errors); setModal('add')
      } else { showResult('Failed to create user', 'error') }
    } finally { setSubmitting(false) }
  }

  const handleReplaceAdmin = async () => {
    setSubmitting(true)
    try {
      await usersApi.replaceAdmin({
        full_name: form.full_name.trim(), email: form.email.trim(),
        password: form.password, confirm_password: form.confirm_password,
      })
      showResult('Admin replaced. Logging out…')
      setTimeout(() => { logout(); window.location.href = '/login' }, 1500)
    } catch (err) {
      const data = err.response?.data
      if (data) {
        const errors = {}
        if (data.full_name) errors.full_name = Array.isArray(data.full_name) ? data.full_name[0] : data.full_name
        if (data.email) errors.email = Array.isArray(data.email) ? data.email[0] : data.email
        if (data.password) errors.password = Array.isArray(data.password) ? data.password.join(' ') : data.password
        setFormErrors(errors); setModal('add')
      } else { showResult('Failed to replace admin', 'error') }
      setSubmitting(false)
    }
  }

  const handleDelete = async () => {
    setSubmitting(true)
    try { await usersApi.deleteUser(selectedUser.id); fetchUsers(); showResult('User deleted successfully!') }
    catch { showResult('Failed to delete user', 'error') }
    finally { setSubmitting(false) }
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

  const strength    = getStrength(form.password)
  const formatDate  = (iso) => iso ? new Date(iso).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' }) : '—'
  const roleLabel   = (role) => role === 'security' ? 'Security' : role === 'vehicle_owner' ? 'Vehicle Owner' : role

  /* ─── render ─────────────────────────────────────────────────── */
  return (
    <AdminLayout>

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
        <div className="um-table-toolbar">
          <div className="um-search-wrapper">
            <Search size={16} />
            <input
              className="um-search-input"
              type="text"
              placeholder="Search by name…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <div className="um-filter-group">
            <select className="um-form-select" value={roleFilter} onChange={(e) => setRoleFilter(e.target.value)}>
              <option value="">All Roles</option>
              <option value="security">Security Personnel</option>
              <option value="vehicle_owner">Vehicle Owner</option>
            </select>
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
                <th>QR</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <td>
                    <span style={{ fontFamily: 'monospace', fontSize: '11px', color: '#6b7280', fontWeight: 600 }}>
                      {u.user_code || `#${u.id}`}
                    </span>
                  </td>
                  <td>
                    <div className="um-user-cell">
                      <div className={`um-user-avatar ${u.role}`}>
                        {u.full_name.charAt(0).toUpperCase()}
                      </div>
                      <span className="um-user-name">{u.full_name}</span>
                    </div>
                  </td>
                  <td>{u.email || '—'}</td>
                  <td>
                    <span className={`um-role-badge ${u.role}`}>{roleLabel(u.role)}</span>
                  </td>
                  <td>
                    <span className={`um-status-badge ${u.is_active ? 'active' : 'disabled'}`}>
                      <span className="status-dot" />
                      {u.is_active ? 'Active' : 'Disabled'}
                    </span>
                  </td>
                  {/* QR — visible for all users */}
                  <td>
                    <button
                      className="um-qr-btn"
                      title={u.role === 'security' ? 'Guard QR Badge' : 'Owner ID QR'}
                      onClick={() => openQrModal(u)}
                    >
                      <QrCode size={15} />
                      <span>{u.role === 'security' ? 'Badge' : 'ID'}</span>
                    </button>
                  </td>
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
                          <button className="um-dropdown-item view" onClick={() => openQrModal(u)}>
                            <QrCode size={15} /> QR Badge
                          </button>
                        )}
                        <button
                          className={`um-dropdown-item ${u.is_active ? 'disable' : 'enable'}`}
                          onClick={() => { openToggle(u); setActiveMenu(null) }}
                        >
                          {u.is_active ? <><Ban size={15} /> Disable</> : <><CheckCircle size={15} /> Enable</>}
                        </button>
                        <button className="um-dropdown-item delete" onClick={() => { openDelete(u); setActiveMenu(null) }}>
                          <Trash2 size={15} /> Delete
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
          <div className="um-modal" onClick={(e) => e.stopPropagation()}>
            <div className="um-modal-header">
              <h2>{addType === 'admin' ? 'Replace Admin' : addType === 'user' ? 'Add New User' : 'Add User'}</h2>
              <button className="um-modal-close" onClick={closeModal}><X size={18} /></button>
            </div>
            <div className="um-modal-body">
              {addType === null && (
                <div className="um-user-type-selector">
                  <div className="um-type-option" onClick={() => setAddType('user')}>
                    <div className="um-type-option-icon"><UserPlus size={20} /></div>
                    <span className="um-type-option-label">Regular User</span>
                    <span className="um-type-option-desc">Security or Vehicle Owner</span>
                  </div>
                  <div className="um-type-option" onClick={() => setAddType('admin')}>
                    <div className="um-type-option-icon"><ShieldAlert size={20} /></div>
                    <span className="um-type-option-label">New Admin</span>
                    <span className="um-type-option-desc">Replaces current admin</span>
                  </div>
                </div>
              )}
              {addType === 'admin' && (
                <div className="um-admin-warning">
                  <AlertTriangle size={18} />
                  <div className="um-admin-warning-text">
                    <strong>Warning:</strong> This will <strong>delete your current admin account</strong> and create a new one. You will be logged out.
                  </div>
                </div>
              )}
              {addType !== null && (
                <>
                  <div className="um-form-group">
                    <label>Full Name</label>
                    <input className={`um-form-input ${formErrors.full_name ? 'error' : ''}`}
                      value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} placeholder="Enter full name" />
                    {formErrors.full_name && <div className="um-form-error">{formErrors.full_name}</div>}
                  </div>
                  <div className="um-form-group">
                    <label>Email</label>
                    <input className={`um-form-input ${formErrors.email ? 'error' : ''}`}
                      type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} placeholder="Enter email address" />
                    {formErrors.email && <div className="um-form-error">{formErrors.email}</div>}
                  </div>
                  {addType === 'user' && (
                    <div className="um-form-group">
                      <label>Role</label>
                      <select className="um-form-select" value={form.role}
                        onChange={(e) => setForm({ ...form, role: e.target.value, gate_assignment: '' })}>
                        <option value="security">Security Personnel</option>
                        <option value="vehicle_owner">Vehicle Owner</option>
                      </select>
                    </div>
                  )}
                  {addType === 'user' && form.role === 'security' && (
                    <div className="um-form-group">
                      <label>Gate Assignment</label>
                      <select className="um-form-select" value={form.gate_assignment}
                        onChange={(e) => setForm({ ...form, gate_assignment: e.target.value })}>
                        <option value="">Unassigned (guard selects at kiosk)</option>
                        <option value="gate1">Gate 1 — Main Entrance</option>
                        <option value="gate4">Gate 4 — Side Entrance</option>
                      </select>
                    </div>
                  )}
                  <div className="um-form-group">
                    <label>Password</label>
                    <div className="um-password-wrapper">
                      <input className={`um-form-input ${formErrors.password ? 'error' : ''}`}
                        type={showPassword ? 'text' : 'password'} value={form.password}
                        onChange={(e) => setForm({ ...form, password: e.target.value })} placeholder="Enter password" />
                      <button type="button" className="um-password-toggle" onClick={() => setShowPassword(!showPassword)} tabIndex={-1}>
                        {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                      </button>
                    </div>
                    {formErrors.password && <div className="um-form-error">{formErrors.password}</div>}
                    {form.password && (
                      <div className="um-password-strength">
                        <div className="um-strength-bar-bg"><div className={`um-strength-bar ${strength.level}`} /></div>
                        <span className={`um-strength-label ${strength.level}`}>{STRENGTH_LABELS[strength.level] || ''}</span>
                        <div className="um-password-rules">
                          {PASSWORD_RULES.map((rule) => (
                            <div key={rule.key} className={`um-password-rule ${rule.test(form.password) ? 'met' : ''}`}>
                              {rule.test(form.password) ? <Check size={12} /> : <Circle size={12} />}
                              {rule.label}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                  <div className="um-form-group">
                    <label>Confirm Password</label>
                    <div className="um-password-wrapper">
                      <input className={`um-form-input ${formErrors.confirm_password ? 'error' : ''}`}
                        type={showConfirm ? 'text' : 'password'} value={form.confirm_password}
                        onChange={(e) => setForm({ ...form, confirm_password: e.target.value })} placeholder="Re-enter password" />
                      <button type="button" className="um-password-toggle" onClick={() => setShowConfirm(!showConfirm)} tabIndex={-1}>
                        {showConfirm ? <EyeOff size={16} /> : <Eye size={16} />}
                      </button>
                    </div>
                    {formErrors.confirm_password && <div className="um-form-error">{formErrors.confirm_password}</div>}
                  </div>
                  {formErrors.general && <div className="um-form-error" style={{ marginBottom: 8 }}>{formErrors.general}</div>}
                </>
              )}
            </div>
            {addType !== null && (
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
            )}
          </div>
        </div>
      )}

      {/* VIEW PROFILE */}
      {modal === 'view' && selectedUser && (
        <div className="um-modal-overlay" onClick={closeModal}>
          <div className="um-modal" onClick={(e) => e.stopPropagation()}>
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
                      style={{ width: 72, height: 72, borderRadius: '50%', objectFit: 'cover', border: '2px solid #E2E6EE', display: 'block' }}
                    />
                  ) : (
                    <div
                      className={`um-user-avatar ${selectedUser.role}`}
                      style={{ width: 72, height: 72, fontSize: 28, margin: 0 }}
                    >
                      {selectedUser.full_name.charAt(0).toUpperCase()}
                    </div>
                  )}
                </div>
                <h3 style={{ margin: 0, color: '#1A1D2E', fontSize: 18 }}>{selectedUser.full_name}</h3>
                <span className={`um-role-badge ${selectedUser.role}`} style={{ marginTop: 8, display: 'inline-flex' }}>
                  {roleLabel(selectedUser.role)}
                </span>
              </div>
              <div className="um-profile-grid">
                <div className="um-profile-item">
                  <span className="um-profile-label">Email</span>
                  <span className="um-profile-value">{selectedUser.email || '—'}</span>
                </div>
                <div className="um-profile-item">
                  <span className="um-profile-label">Status</span>
                  <span className={`um-status-badge ${selectedUser.is_active ? 'active' : 'disabled'}`}>
                    <span className="status-dot" />{selectedUser.is_active ? 'Active' : 'Disabled'}
                  </span>
                </div>
                <div className="um-profile-item">
                  <span className="um-profile-label">Date Joined</span>
                  <span className="um-profile-value">{formatDate(selectedUser.date_joined)}</span>
                </div>
                <div className="um-profile-item">
                  <span className="um-profile-label">User ID</span>
                  <span className="um-profile-value" style={{ fontFamily: 'monospace', fontWeight: 700, color: '#2A2B61' }}>
                    {selectedUser.user_code || `#${selectedUser.id}`}
                  </span>
                </div>
                {selectedUser.role === 'security' && (
                  <div className="um-profile-item">
                    <span className="um-profile-label">Gate Assignment</span>
                    <span className="um-profile-value">
                      {selectedUser.gate_assignment === 'gate1' ? 'Gate 1 — Main Entrance'
                        : selectedUser.gate_assignment === 'gate4' ? 'Gate 4 — Side Entrance'
                        : 'Unassigned'}
                    </span>
                  </div>
                )}
              </div>
            </div>
            <div className="um-modal-footer">
              <button className="um-btn-secondary" onClick={closeModal}>Close</button>
              <button className="um-btn-primary" onClick={() => { closeModal(); openQrModal(selectedUser) }}>
                <QrCode size={15} /> View QR
              </button>
            </div>
          </div>
        </div>
      )}

      {/* DELETE */}
      {modal === 'delete' && selectedUser && (
        <div className="um-modal-overlay" onClick={closeModal}>
          <div className="um-modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 420 }}>
            <div className="um-modal-header">
              <h2>Delete User</h2>
              <button className="um-modal-close" onClick={closeModal}><X size={18} /></button>
            </div>
            <div className="um-modal-body">
              <div className="um-confirm-body">
                <div className="um-confirm-icon danger"><Trash2 size={24} /></div>
                <h3>Are you sure?</h3>
                <p>Permanently delete <span className="um-confirm-name">{selectedUser.full_name}</span>. This cannot be undone.</p>
              </div>
            </div>
            <div className="um-modal-footer">
              <button className="um-btn-secondary" onClick={closeModal}>Cancel</button>
              <button className="um-btn-danger" disabled={submitting} onClick={handleDelete}>
                <Trash2 size={16} /> {submitting ? 'Deleting…' : 'Delete User'}
              </button>
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
                <h3>{addType === 'admin' ? 'Replace Admin?' : 'Create User?'}</h3>
                <p>Are you sure you want to {addType === 'admin' ? 'replace the current admin' : 'create this new account'}?</p>
              </div>
            </div>
            <div className="um-modal-footer">
              <button className="um-btn-secondary" disabled={submitting} onClick={() => setModal('add')}>Back</button>
              <button className="um-btn-primary" disabled={submitting} onClick={addType === 'admin' ? handleReplaceAdmin : handleAddUser}>
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
                <p style={{ margin: '2px 0 0', fontSize: 12, color: '#6b7280' }}>
                  {qrUser.user_code}
                  {qrUser.role === 'security' && (
                    <> · {qrUser.gate_assignment === 'gate1' ? 'Gate 1' : qrUser.gate_assignment === 'gate4' ? 'Gate 4' : 'No gate assigned'}</>
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
                  <div style={{ padding: 14, background: '#fff', border: '2px solid #E2E6EE', borderRadius: 14, boxShadow: '0 2px 12px rgba(42,43,97,0.08)' }}>
                    <QRCodeSVG value={qrToken} size={190} level="M" />
                  </div>
                  {qrUser.role === 'security' ? (
                    <>
                      <p style={{ margin: 0, fontSize: 11, color: '#9ca3af', wordBreak: 'break-all', maxWidth: 320 }}>
                        Token: {qrToken}
                      </p>
                      <p style={{ margin: 0, fontSize: 11, color: '#b45309', background: '#fef9c3', border: '1px solid #fde68a', borderRadius: 6, padding: '4px 10px' }}>
                        Print this QR code as the guard's badge. Do not share digitally.
                      </p>
                    </>
                  ) : (
                    <p style={{ margin: 0, fontSize: 11, color: '#5A5F72', background: '#F0F2F7', borderRadius: 6, padding: '5px 12px' }}>
                      Scan to identify this vehicle owner — encodes their User ID.
                    </p>
                  )}
                </>
              ) : null}
            </div>
            <div className="um-modal-footer" style={{ justifyContent: qrUser.role === 'security' ? 'space-between' : 'flex-end' }}>
              <button className="um-btn-secondary" onClick={() => setQrUser(null)}>Close</button>
              {qrUser.role === 'security' && (
                <button className="um-btn-warning" disabled={qrLoading} onClick={handleRegenerateQR}
                  style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <RefreshCw size={14} />
                  {qrLoading ? 'Regenerating…' : 'Regenerate Badge'}
                </button>
              )}
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

    </AdminLayout>
  )
}
