import { useState, useEffect, useCallback } from 'react'
import AdminLayout from '../../components/Layout/AdminLayout'
import { usersApi } from '../../api/users'
import useAuthStore from '../../stores/authStore'
import {
  Search, UserPlus, Eye, Pencil, Ban, CheckCircle, Trash2, X,
  Users, UserCheck, UserX, AlertTriangle, ShieldAlert, EyeOff,
  Check, Circle, MoreVertical, ChevronLeft, ChevronRight
} from 'lucide-react'
import './UserManagement.css'

/* ─── password helpers ───────────────────────────────────── */
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

/* ─── main component ─────────────────────────────────────── */
export default function UserManagement() {
  const { logout } = useAuthStore()
  const [users, setUsers] = useState([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [resultModal, setResultModal] = useState(null)
  
  // Pagination & Filters
  const [page, setPage] = useState(1)
  const [totalPages, setTotalPages] = useState(1)
  const [totalCount, setTotalCount] = useState(0)
  const [roleFilter, setRoleFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')

  // Modal state
  const [modal, setModal] = useState(null)        // 'add' | 'addAdmin' | 'edit' | 'view' | 'delete' | 'toggle'
  const [selectedUser, setSelectedUser] = useState(null)
  const [submitting, setSubmitting] = useState(false)
  const [activeMenu, setActiveMenu] = useState(null)

  // Add/Edit form
  const [form, setForm] = useState({ full_name: '', email: '', role: 'security', password: '', confirm_password: '' })
  const [showPassword, setShowPassword] = useState(false)
  const [showConfirm, setShowConfirm] = useState(false)
  const [formErrors, setFormErrors] = useState({})

  // Add type selector: 'user' or 'admin'
  const [addType, setAddType] = useState(null) // null = show selector, 'user' | 'admin' = show form

  /* ─── result helper ───── */
  const showResult = (message, type = 'success') => {
    setResultModal({ message, type })
    setModal(null)
    setSelectedUser(null)
    setAddType(null)
  }

  /* ─── fetch users ───── */
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
    } catch (err) {
      showResult('Failed to load users', 'error')
    } finally {
      setLoading(false)
    }
  }, [search, page, roleFilter, statusFilter])

  useEffect(() => {
    setPage(1)
  }, [search, roleFilter, statusFilter])

  useEffect(() => {
    const timer = setTimeout(fetchUsers, 300)
    return () => clearTimeout(timer)
  }, [fetchUsers])

  /* ─── stats ───── */
  const totalUsers = users.length
  const activeUsers = users.filter((u) => u.is_active).length
  const disabledUsers = users.filter((u) => !u.is_active).length

  /* ─── close menu on outside click ───── */
  useEffect(() => {
    const handleClickOutside = () => setActiveMenu(null)
    if (activeMenu) {
      window.addEventListener('click', handleClickOutside)
    }
    return () => window.removeEventListener('click', handleClickOutside)
  }, [activeMenu])

  /* ─── open modals ───── */
  const openAdd = () => {
    setAddType(null)
    setForm({ full_name: '', email: '', role: 'security', password: '', confirm_password: '' })
    setFormErrors({})
    setShowPassword(false)
    setShowConfirm(false)
    setModal('add')
  }

  const openEdit = (user) => {
    setSelectedUser(user)
    setForm({ full_name: user.full_name, email: user.email, role: user.role, password: '', confirm_password: '' })
    setFormErrors({})
    setModal('edit')
  }

  const openView = (user) => {
    setSelectedUser(user)
    setModal('view')
  }

  const openDelete = (user) => {
    setSelectedUser(user)
    setModal('delete')
  }

  const openToggle = (user) => {
    setSelectedUser(user)
    setModal('toggle')
  }

  const closeModal = () => {
    setModal(null)
    setSelectedUser(null)
    setSubmitting(false)
    setFormErrors({})
    setAddType(null)
  }

  /* ─── form validation ───── */
  const validateForm = (isAdmin = false) => {
    const errors = {}
    if (!form.full_name.trim()) errors.full_name = 'Full name is required.'
    if (!form.email.trim()) errors.email = 'Email is required.'
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email)) errors.email = 'Invalid email format.'

    // password only required for new user / admin
    if (modal === 'add') {
      if (!form.password) errors.password = 'Password is required.'
      else {
        const { score } = getStrength(form.password)
        if (score < 5) errors.password = 'Password does not meet all requirements.'
      }
      if (!form.confirm_password) errors.confirm_password = 'Please confirm password.'
      else if (form.password !== form.confirm_password) errors.confirm_password = 'Passwords do not match.'
    }
    setFormErrors(errors)
    return Object.keys(errors).length === 0
  }

  /* ─── handle add user ───── */
  const onAddClick = () => {
    if (!validateForm(addType === 'admin')) return
    setModal('confirmAdd')
  }

  const handleAddUser = async () => {
    setSubmitting(true)
    try {
      await usersApi.createUser({
        full_name: form.full_name.trim(),
        email: form.email.trim(),
        role: form.role,
        password: form.password,
        confirm_password: form.confirm_password,
      })
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
        setFormErrors(errors)
        setModal('add')
      } else {
        showResult('Failed to create user', 'error')
      }
    } finally {
      setSubmitting(false)
    }
  }

  /* ─── handle add admin (replace) ───── */
  const handleReplaceAdmin = async () => {
    setSubmitting(true)
    try {
      await usersApi.replaceAdmin({
        full_name: form.full_name.trim(),
        email: form.email.trim(),
        password: form.password,
        confirm_password: form.confirm_password,
      })
      showResult('Admin replaced. Logging out…')
      setTimeout(() => {
        logout()
        window.location.href = '/login'
      }, 1500)
    } catch (err) {
      const data = err.response?.data
      if (data) {
        const errors = {}
        if (data.full_name) errors.full_name = Array.isArray(data.full_name) ? data.full_name[0] : data.full_name
        if (data.email) errors.email = Array.isArray(data.email) ? data.email[0] : data.email
        if (data.password) errors.password = Array.isArray(data.password) ? data.password.join(' ') : data.password
        if (data.confirm_password) errors.confirm_password = Array.isArray(data.confirm_password) ? data.confirm_password[0] : data.confirm_password
        setFormErrors(errors)
        setModal('add')
      } else {
        showResult('Failed to replace admin', 'error')
      }
      setSubmitting(false)
    }
  }

  /* ─── handle edit ───── */
  const onEditClick = () => {
    const errors = {}
    if (!form.full_name.trim()) errors.full_name = 'Full name is required.'
    if (!form.email.trim()) errors.email = 'Email is required.'
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email)) errors.email = 'Invalid email format.'
    setFormErrors(errors)
    if (Object.keys(errors).length > 0) return
    setModal('confirmEdit')
  }

  const handleEdit = async () => {
    setSubmitting(true)
    try {
      await usersApi.updateUser(selectedUser.id, {
        full_name: form.full_name.trim(),
        email: form.email.trim(),
        role: form.role,
      })
      fetchUsers()
      showResult('User updated successfully!')
    } catch (err) {
      const data = err.response?.data
      if (data) {
        const errors = {}
        if (data.full_name) errors.full_name = Array.isArray(data.full_name) ? data.full_name[0] : data.full_name
        if (data.email) errors.email = Array.isArray(data.email) ? data.email[0] : data.email
        setFormErrors(errors)
        setModal('edit')
      } else {
        showResult('Failed to update user', 'error')
      }
    } finally {
      setSubmitting(false)
    }
  }

  /* ─── handle delete ───── */
  const handleDelete = async () => {
    setSubmitting(true)
    try {
      await usersApi.deleteUser(selectedUser.id)
      fetchUsers()
      showResult('User deleted successfully!')
    } catch {
      showResult('Failed to delete user', 'error')
    } finally {
      setSubmitting(false)
    }
  }

  /* ─── handle toggle ───── */
  const handleToggle = async () => {
    setSubmitting(true)
    try {
      await usersApi.toggleUserStatus(selectedUser.id)
      fetchUsers()
      showResult(`User ${selectedUser.is_active ? 'disabled' : 'enabled'} successfully!`)
    } catch {
      showResult('Failed to toggle user status', 'error')
    } finally {
      setSubmitting(false)
    }
  }

  /* ─── password strength for current form ───── */
  const strength = getStrength(form.password)

  /* ─── format date ───── */
  const formatDate = (iso) => {
    if (!iso) return '—'
    return new Date(iso).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })
  }

  const roleLabel = (role) => {
    if (role === 'security') return 'Security'
    if (role === 'vehicle_owner') return 'Vehicle Owner'
    return role
  }

  /* ─── render ─────────────────────────────────────────────── */
  return (
    <AdminLayout>
      {/* Header */}
      <div className="um-header">
        <div className="um-header-left">
          <h1>User Management</h1>
          <p>Manage security personnel and vehicle owner accounts.</p>
        </div>
        <div className="um-header-actions">
          <button id="add-user-btn" className="um-add-btn" onClick={openAdd}>
            <UserPlus size={16} />
            Add User
          </button>
        </div>
      </div>

      {/* Stats */}
      <div className="um-stats-bar">
        <div className="um-stat-card">
          <div className="um-stat-icon total"><Users size={20} /></div>
          <div className="um-stat-info">
            <h4>Total Users</h4>
            <span>{totalUsers}</span>
          </div>
        </div>
        <div className="um-stat-card">
          <div className="um-stat-icon active-stat"><UserCheck size={20} /></div>
          <div className="um-stat-info">
            <h4>Active</h4>
            <span>{activeUsers}</span>
          </div>
        </div>
        <div className="um-stat-card">
          <div className="um-stat-icon disabled-stat"><UserX size={20} /></div>
          <div className="um-stat-info">
            <h4>Disabled</h4>
            <span>{disabledUsers}</span>
          </div>
        </div>
      </div>

      {/* Table & Toolbar */}
      <div className="um-table-container">
        <div className="um-table-toolbar">
          <div className="um-search-wrapper">
            <Search size={16} />
            <input
              id="user-search"
              className="um-search-input"
              type="text"
              placeholder="Search by name…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <div className="um-filter-group">
            <select
              className="um-form-select"
              value={roleFilter}
              onChange={(e) => setRoleFilter(e.target.value)}
            >
              <option value="">All Roles</option>
              <option value="security">Security Personnel</option>
              <option value="vehicle_owner">Vehicle Owner</option>
            </select>
            <select
              className="um-form-select"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
            >
              <option value="">All Statuses</option>
              <option value="active">Active</option>
              <option value="disabled">Disabled</option>
            </select>
          </div>
        </div>

        {loading ? (
          <div className="um-loading">
            <div className="um-spinner" />
            <p>Loading users…</p>
          </div>
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
                <th>#</th>
                <th>Full Name</th>
                <th>Email</th>
                <th>Role</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u, idx) => (
                <tr key={u.id}>
                  <td>{idx + 1}</td>
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
                  <td style={{ position: 'relative' }}>
                    <button
                      className="um-action-btn"
                      onClick={(e) => {
                        e.stopPropagation()
                        setActiveMenu(activeMenu === u.id ? null : u.id)
                      }}
                    >
                      <MoreVertical size={16} />
                    </button>
                    {activeMenu === u.id && (
                      <div className="um-actions-dropdown" onClick={(e) => e.stopPropagation()}>
                        <button className="um-dropdown-item view" onClick={() => { openView(u); setActiveMenu(null) }}>
                          <Eye size={15} /> View Profile
                        </button>
                        <button className="um-dropdown-item edit" onClick={() => { openEdit(u); setActiveMenu(null) }}>
                          <Pencil size={15} /> Edit
                        </button>
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
              <button
                className="um-page-btn"
                disabled={page === 1}
                onClick={() => setPage(p => p - 1)}
              >
                <ChevronLeft size={16} />
              </button>
              <span className="um-page-current">Page {page} of {totalPages}</span>
              <button
                className="um-page-btn"
                disabled={page === totalPages}
                onClick={() => setPage(p => p + 1)}
              >
                <ChevronRight size={16} />
              </button>
            </div>
          </div>
        )}
      </div>

      {/* ────── MODALS ────── */}

      {/* ADD USER MODAL */}
      {modal === 'add' && (
        <div className="um-modal-overlay" onClick={closeModal}>
          <div className="um-modal" onClick={(e) => e.stopPropagation()}>
            <div className="um-modal-header">
              <h2>{addType === 'admin' ? 'Replace Admin' : addType === 'user' ? 'Add New User' : 'Add User'}</h2>
              <button className="um-modal-close" onClick={closeModal}><X size={18} /></button>
            </div>
            <div className="um-modal-body">
              {/* type selector */}
              {addType === null && (
                <div className="um-user-type-selector">
                  <div
                    className="um-type-option"
                    onClick={() => setAddType('user')}
                  >
                    <div className="um-type-option-icon"><UserPlus size={20} /></div>
                    <span className="um-type-option-label">Regular User</span>
                    <span className="um-type-option-desc">Security or Vehicle Owner</span>
                  </div>
                  <div
                    className="um-type-option"
                    onClick={() => setAddType('admin')}
                  >
                    <div className="um-type-option-icon"><ShieldAlert size={20} /></div>
                    <span className="um-type-option-label">New Admin</span>
                    <span className="um-type-option-desc">Replaces current admin</span>
                  </div>
                </div>
              )}

              {/* admin warning */}
              {addType === 'admin' && (
                <div className="um-admin-warning">
                  <AlertTriangle size={18} />
                  <div className="um-admin-warning-text">
                    <strong>Warning:</strong> This will <strong>delete your current admin account</strong> and create a new one.
                    You will be logged out after this action.
                  </div>
                </div>
              )}

              {/* form fields */}
              {addType !== null && (
                <>
                  <div className="um-form-group">
                    <label htmlFor="add-fullname">Full Name</label>
                    <input
                      id="add-fullname"
                      className={`um-form-input ${formErrors.full_name ? 'error' : ''}`}
                      value={form.full_name}
                      onChange={(e) => setForm({ ...form, full_name: e.target.value })}
                      placeholder="Enter full name"
                    />
                    {formErrors.full_name && <div className="um-form-error">{formErrors.full_name}</div>}
                  </div>

                  <div className="um-form-group">
                    <label htmlFor="add-email">Email</label>
                    <input
                      id="add-email"
                      className={`um-form-input ${formErrors.email ? 'error' : ''}`}
                      type="email"
                      value={form.email}
                      onChange={(e) => setForm({ ...form, email: e.target.value })}
                      placeholder="Enter email address"
                    />
                    {formErrors.email && <div className="um-form-error">{formErrors.email}</div>}
                  </div>

                  {addType === 'user' && (
                    <div className="um-form-group">
                      <label htmlFor="add-role">Role</label>
                      <select
                        id="add-role"
                        className="um-form-select"
                        value={form.role}
                        onChange={(e) => setForm({ ...form, role: e.target.value })}
                      >
                        <option value="security">Security Personnel</option>
                        <option value="vehicle_owner">Vehicle Owner</option>
                      </select>
                    </div>
                  )}

                  <div className="um-form-group">
                    <label htmlFor="add-password">Password</label>
                    <div className="um-password-wrapper">
                      <input
                        id="add-password"
                        className={`um-form-input ${formErrors.password ? 'error' : ''}`}
                        type={showPassword ? 'text' : 'password'}
                        value={form.password}
                        onChange={(e) => setForm({ ...form, password: e.target.value })}
                        placeholder="Enter password"
                      />
                      <button
                        type="button"
                        className="um-password-toggle"
                        onClick={() => setShowPassword(!showPassword)}
                        tabIndex={-1}
                      >
                        {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                      </button>
                    </div>
                    {formErrors.password && <div className="um-form-error">{formErrors.password}</div>}

                    {form.password && (
                      <div className="um-password-strength">
                        <div className="um-strength-bar-bg">
                          <div className={`um-strength-bar ${strength.level}`} />
                        </div>
                        <span className={`um-strength-label ${strength.level}`}>
                          {STRENGTH_LABELS[strength.level] || ''}
                        </span>
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
                    <label htmlFor="add-confirm">Confirm Password</label>
                    <div className="um-password-wrapper">
                      <input
                        id="add-confirm"
                        className={`um-form-input ${formErrors.confirm_password ? 'error' : ''}`}
                        type={showConfirm ? 'text' : 'password'}
                        value={form.confirm_password}
                        onChange={(e) => setForm({ ...form, confirm_password: e.target.value })}
                        placeholder="Re-enter password"
                      />
                      <button
                        type="button"
                        className="um-password-toggle"
                        onClick={() => setShowConfirm(!showConfirm)}
                        tabIndex={-1}
                      >
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
                    <ShieldAlert size={16} />
                    Continue
                  </button>
                ) : (
                  <button className="um-btn-primary" disabled={submitting} onClick={onAddClick}>
                    <UserPlus size={16} />
                    Continue
                  </button>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* VIEW PROFILE MODAL */}
      {modal === 'view' && selectedUser && (
        <div className="um-modal-overlay" onClick={closeModal}>
          <div className="um-modal" onClick={(e) => e.stopPropagation()}>
            <div className="um-modal-header">
              <h2>User Profile</h2>
              <button className="um-modal-close" onClick={closeModal}><X size={18} /></button>
            </div>
            <div className="um-modal-body">
              <div style={{ textAlign: 'center', marginBottom: 24 }}>
                <div
                  className={`um-user-avatar ${selectedUser.role}`}
                  style={{ width: 64, height: 64, fontSize: 26, margin: '0 auto 12px' }}
                >
                  {selectedUser.full_name.charAt(0).toUpperCase()}
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
                    <span className="status-dot" />
                    {selectedUser.is_active ? 'Active' : 'Disabled'}
                  </span>
                </div>
                <div className="um-profile-item">
                  <span className="um-profile-label">Date Joined</span>
                  <span className="um-profile-value">{formatDate(selectedUser.date_joined)}</span>
                </div>
                <div className="um-profile-item">
                  <span className="um-profile-label">User ID</span>
                  <span className="um-profile-value">#{selectedUser.id}</span>
                </div>
              </div>
            </div>
            <div className="um-modal-footer">
              <button className="um-btn-secondary" onClick={closeModal}>Close</button>
            </div>
          </div>
        </div>
      )}

      {/* EDIT USER MODAL */}
      {modal === 'edit' && selectedUser && (
        <div className="um-modal-overlay" onClick={closeModal}>
          <div className="um-modal" onClick={(e) => e.stopPropagation()}>
            <div className="um-modal-header">
              <h2>Edit User</h2>
              <button className="um-modal-close" onClick={closeModal}><X size={18} /></button>
            </div>
            <div className="um-modal-body">
              <div className="um-form-group">
                <label htmlFor="edit-fullname">Full Name</label>
                <input
                  id="edit-fullname"
                  className={`um-form-input ${formErrors.full_name ? 'error' : ''}`}
                  value={form.full_name}
                  onChange={(e) => setForm({ ...form, full_name: e.target.value })}
                />
                {formErrors.full_name && <div className="um-form-error">{formErrors.full_name}</div>}
              </div>
              <div className="um-form-group">
                <label htmlFor="edit-email">Email</label>
                <input
                  id="edit-email"
                  className={`um-form-input ${formErrors.email ? 'error' : ''}`}
                  type="email"
                  value={form.email}
                  onChange={(e) => setForm({ ...form, email: e.target.value })}
                />
                {formErrors.email && <div className="um-form-error">{formErrors.email}</div>}
              </div>
              <div className="um-form-group">
                <label htmlFor="edit-role">Role</label>
                <select
                  id="edit-role"
                  className="um-form-select"
                  value={form.role}
                  onChange={(e) => setForm({ ...form, role: e.target.value })}
                >
                  <option value="security">Security Personnel</option>
                  <option value="vehicle_owner">Vehicle Owner</option>
                </select>
              </div>
            </div>
            <div className="um-modal-footer">
              <button className="um-btn-secondary" onClick={closeModal}>Cancel</button>
              <button className="um-btn-primary" disabled={submitting} onClick={onEditClick}>
                <Pencil size={16} />
                Continue
              </button>
            </div>
          </div>
        </div>
      )}

      {/* DELETE CONFIRMATION */}
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
                <p>
                  This will permanently delete <span className="um-confirm-name">{selectedUser.full_name}</span>.
                  This action cannot be undone.
                </p>
              </div>
            </div>
            <div className="um-modal-footer">
              <button className="um-btn-secondary" onClick={closeModal}>Cancel</button>
              <button className="um-btn-danger" disabled={submitting} onClick={handleDelete}>
                <Trash2 size={16} />
                {submitting ? 'Deleting…' : 'Delete User'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* TOGGLE STATUS CONFIRMATION */}
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
                    ? <>User <span className="um-confirm-name">{selectedUser.full_name}</span> will no longer be able to log in.</>
                    : <>User <span className="um-confirm-name">{selectedUser.full_name}</span> will be able to log in again.</>
                  }
                </p>
              </div>
            </div>
            <div className="um-modal-footer">
              <button className="um-btn-secondary" onClick={closeModal}>Cancel</button>
              {selectedUser.is_active ? (
                <button className="um-btn-warning" disabled={submitting} onClick={handleToggle}>
                  <Ban size={16} />
                  {submitting ? 'Disabling…' : 'Disable User'}
                </button>
              ) : (
                <button className="um-btn-primary" disabled={submitting} onClick={handleToggle}>
                  <CheckCircle size={16} />
                  {submitting ? 'Enabling…' : 'Enable User'}
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* CONFIRM ADD MODAL */}
      {modal === 'confirmAdd' && (
        <div className="um-modal-overlay">
          <div className="um-modal" style={{ maxWidth: 420 }}>
            <div className="um-modal-header">
              <h2>Confirm Creation</h2>
            </div>
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

      {/* CONFIRM EDIT MODAL */}
      {modal === 'confirmEdit' && selectedUser && (
        <div className="um-modal-overlay">
          <div className="um-modal" style={{ maxWidth: 420 }}>
            <div className="um-modal-header">
              <h2>Confirm Changes</h2>
            </div>
            <div className="um-modal-body">
              <div className="um-confirm-body">
                <div className="um-confirm-icon warning"><Pencil size={24} /></div>
                <h3>Save Changes?</h3>
                <p>Are you sure you want to save the changes made to <span className="um-confirm-name">{selectedUser.full_name}</span>?</p>
              </div>
            </div>
            <div className="um-modal-footer">
              <button className="um-btn-secondary" disabled={submitting} onClick={() => setModal('edit')}>Back</button>
              <button className="um-btn-primary" disabled={submitting} onClick={handleEdit}>
                {submitting ? 'Saving...' : 'Save Changes'}
              </button>
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
