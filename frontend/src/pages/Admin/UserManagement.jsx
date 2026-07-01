import { useState, useEffect, useCallback, useRef } from 'react'
import { QRCodeSVG } from 'qrcode.react'
import { useNavigate } from 'react-router-dom'
import AdminLayout from '../../components/Layout/AdminLayout'
import { usersApi } from '../../api/users'
import useAuthStore from '../../stores/authStore'
import {
  Search, UserPlus, Eye, Ban, CheckCircle, Trash2, X,
  Users, UserCheck, UserX, AlertTriangle, ShieldAlert, EyeOff,
  Check, Circle, MoreVertical, ChevronLeft, ChevronRight, QrCode, RefreshCw,
  Shield, Car, Info, LogIn,
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

const CAMPUS_DAYS = [
  { key: 'Monday', short: 'Mon' },
  { key: 'Tuesday', short: 'Tue' },
  { key: 'Wednesday', short: 'Wed' },
  { key: 'Thursday', short: 'Thu' },
  { key: 'Friday', short: 'Fri' },
  { key: 'Saturday', short: 'Sat' },
]

const EMPTY_GUARD = { full_name: '' }
const EMPTY_OWNER = {
  last_name: '', first_name: '', middle_name: '',
  email: '', contact_number: '', age: '', drivers_license: '',
  registrant_type: '',
  house_street: '', province: '', city_municipality: '', barangay: '',
  student_id: '', student_level: '', program_year: '',
  student_strand: '', student_grade: '',
  employee_id: '', department_type: '',
  campus_days: [],
  plate_number: '', conduction_number: '', vehicle_type: '', vehicle_color: '', body_number: '',
}
const EMPTY_ADMIN = { full_name: '', email: '', password: '', confirm_password: '' }

/* ─── Main Component ───────────────────────────────────────────── */
export default function UserManagement() {
  const { logout } = useAuthStore()
  const navigate = useNavigate()

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
  const [addType, setAddType]         = useState(null)   // 'guard' | 'owner' | 'admin'
  const [showGateConfirm, setShowGateConfirm] = useState(false)
  const [formErrors, setFormErrors]   = useState({})

  /* ── per-type form state ── */
  const [guardForm, setGuardForm] = useState(EMPTY_GUARD)
  const [ownerForm, setOwnerForm] = useState(EMPTY_OWNER)
  const [adminForm, setAdminForm] = useState(EMPTY_ADMIN)
  const [showPassword, setShowPassword] = useState(false)
  const [showConfirm,  setShowConfirm]  = useState(false)

  /* ── owner address cascading dropdowns ── */
  const [provinces,        setProvinces]        = useState([])
  const [cities,           setCities]           = useState([])
  const [barangays,        setBarangays]        = useState([])
  const [loadingCities,    setLoadingCities]    = useState(false)
  const [loadingBarangays, setLoadingBarangays] = useState(false)
  const [selProvince,      setSelProvince]      = useState('')
  const [selCity,          setSelCity]          = useState('')

  /* ── QR state ── */
  const [qrUser,    setQrUser]    = useState(null)
  const [qrToken,   setQrToken]   = useState(null)
  const [qrLoading, setQrLoading] = useState(false)

  /* ── provinces load once ── */
  useEffect(() => {
    fetch('https://psgc.gitlab.io/api/provinces/')
      .then(r => r.json())
      .then(d => setProvinces(d.sort((a, b) => a.name.localeCompare(b.name))))
      .catch(() => {})
  }, [])

  /* ── cities load when province changes ── */
  useEffect(() => {
    if (!selProvince) { setCities([]); setBarangays([]); return }
    setLoadingCities(true); setCities([]); setBarangays([])
    fetch(`https://psgc.gitlab.io/api/provinces/${selProvince}/cities-municipalities/`)
      .then(r => r.json())
      .then(d => setCities(d.sort((a, b) => a.name.localeCompare(b.name))))
      .catch(() => {})
      .finally(() => setLoadingCities(false))
  }, [selProvince])

  /* ── barangays load when city changes ── */
  useEffect(() => {
    if (!selCity) { setBarangays([]); return }
    setLoadingBarangays(true); setBarangays([])
    fetch(`https://psgc.gitlab.io/api/cities-municipalities/${selCity}/barangays/`)
      .then(r => r.json())
      .then(d => setBarangays(d.sort((a, b) => a.name.localeCompare(b.name))))
      .catch(() => {})
      .finally(() => setLoadingBarangays(false))
  }, [selCity])

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
    setGuardForm(EMPTY_GUARD)
    setOwnerForm(EMPTY_OWNER)
    setAdminForm(EMPTY_ADMIN)
    setFormErrors({})
    setShowPassword(false)
    setShowConfirm(false)
    setSelProvince(''); setSelCity('')
    setCities([]); setBarangays([])
    setModal('add')
  }
  const openView   = (user) => { setSelectedUser(user); setModal('view') }
  const openDelete = (user) => { setSelectedUser(user); setModal('delete') }
  const openToggle = (user) => { setSelectedUser(user); setModal('toggle') }
  const closeModal = () => {
    setModal(null); setSelectedUser(null)
    setSubmitting(false); setFormErrors({}); setAddType(null)
  }

  /* ── toggle campus day (owner form) ── */
  const toggleOwnerDay = (dayKey) => {
    setOwnerForm(prev => {
      const days = prev.campus_days
      if (days.includes(dayKey)) return { ...prev, campus_days: days.filter(d => d !== dayKey) }
      if (days.length >= 3) return prev
      return { ...prev, campus_days: [...days, dayKey] }
    })
  }

  /* ── guard validation & submit ── */
  const validateGuard = () => {
    const errors = {}
    if (!guardForm.full_name.trim()) errors.full_name = 'Full name is required.'
    setFormErrors(errors)
    return Object.keys(errors).length === 0
  }

  const handleAddGuard = async () => {
    setSubmitting(true)
    try {
      const guard = await usersApi.createGuard({
        full_name: guardForm.full_name.trim(),
      })
      fetchUsers()
      closeModal()
      toast.success(`Guard "${guard.full_name}" created.`)
      // Open QR badge immediately
      openQrModal(guard)
    } catch (err) {
      const data = err.response?.data
      if (data?.full_name) {
        setFormErrors({ full_name: Array.isArray(data.full_name) ? data.full_name[0] : data.full_name })
        setModal('add')
      } else {
        showResult('Failed to create guard.', 'error')
      }
    } finally { setSubmitting(false) }
  }

  /* ── owner validation & submit ── */
  const validateOwner = () => {
    const errors = {}
    if (!ownerForm.last_name.trim())   errors.last_name   = 'Last name is required.'
    if (!ownerForm.first_name.trim())  errors.first_name  = 'First name is required.'
    if (!ownerForm.email.trim())       errors.email       = 'Email is required.'
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(ownerForm.email)) errors.email = 'Invalid email format.'
    if (!ownerForm.registrant_type)    errors.registrant_type = 'Please select a registrant type.'
    if (!ownerForm.plate_number.trim()) errors.plate_number = 'Plate number is required.'
    if (!ownerForm.vehicle_type)       errors.vehicle_type = 'Vehicle type is required.'
    if (!ownerForm.house_street.trim()) errors.house_street = 'Street address is required.'
    if (!ownerForm.province)           errors.province = 'Province is required.'
    if (!ownerForm.city_municipality)  errors.city_municipality = 'City / Municipality is required.'
    if (!ownerForm.barangay)           errors.barangay = 'Barangay is required.'
    if (!ownerForm.contact_number.trim()) errors.contact_number = 'Contact number is required.'
    if (!ownerForm.drivers_license.trim()) errors.drivers_license = 'Driver\'s license is required.'
    if (ownerForm.registrant_type === 'student') {
      if (!ownerForm.student_id.trim()) errors.student_id = 'Student ID is required.'
      if (!ownerForm.student_level)     errors.student_level = 'Education level is required.'
      if (ownerForm.campus_days.length === 0 && ownerForm.student_level !== 'sped')
        errors.campus_days = 'Select at least one campus day.'
    }
    if (ownerForm.registrant_type === 'employee') {
      if (!ownerForm.employee_id.trim()) errors.employee_id = 'Employee ID is required.'
      if (!ownerForm.department_type)    errors.department_type = 'Department is required.'
    }
    setFormErrors(errors)
    return Object.keys(errors).length === 0
  }

  const handleAddOwner = async () => {
    setSubmitting(true)
    try {
      const address = [ownerForm.house_street, ownerForm.barangay, ownerForm.city_municipality, ownerForm.province]
        .map(s => s.trim()).filter(Boolean).join(', ')
      let program_year = ownerForm.program_year
      if (ownerForm.registrant_type === 'student' && ownerForm.student_level !== 'college') {
        if (ownerForm.student_level === 'shs')
          program_year = ['SHS', ownerForm.student_strand, ownerForm.student_grade ? `Grade ${ownerForm.student_grade}` : ''].filter(Boolean).join(' - ')
        else if (ownerForm.student_level === 'jhs')
          program_year = ['JHS', ownerForm.student_grade ? `Grade ${ownerForm.student_grade}` : ''].filter(Boolean).join(' - ')
        else if (ownerForm.student_level === 'elementary')
          program_year = ['Elementary', ownerForm.student_grade ? `Grade ${ownerForm.student_grade}` : ''].filter(Boolean).join(' - ')
        else if (ownerForm.student_level === 'sped')
          program_year = ownerForm.student_grade ? `SpEd - Grade ${ownerForm.student_grade}` : 'SpEd'
      }
      const campus_days = ownerForm.student_level === 'sped'
        ? CAMPUS_DAYS.map(d => d.key)
        : ownerForm.campus_days

      await usersApi.createOwner({
        last_name: ownerForm.last_name.trim(),
        first_name: ownerForm.first_name.trim(),
        middle_name: ownerForm.middle_name.trim(),
        email: ownerForm.email.trim(),
        contact_number: ownerForm.contact_number.trim(),
        age: ownerForm.age ? parseInt(ownerForm.age) : null,
        drivers_license: ownerForm.drivers_license.trim(),
        address,
        registrant_type: ownerForm.registrant_type,
        student_id: ownerForm.student_id.trim(),
        program_year,
        campus_days,
        employee_id: ownerForm.employee_id.trim(),
        department_type: ownerForm.department_type,
        plate_number: ownerForm.plate_number.trim(),
        conduction_number: ownerForm.conduction_number.trim(),
        vehicle_type: ownerForm.vehicle_type,
        vehicle_color: ownerForm.vehicle_color.trim(),
        body_number: ownerForm.body_number.trim(),
      })
      fetchUsers()
      showResult('Vehicle owner account created. Login credentials have been emailed.')
    } catch (err) {
      const data = err.response?.data
      if (data && typeof data === 'object') {
        const errors = {}
        Object.entries(data).forEach(([k, v]) => {
          errors[k] = Array.isArray(v) ? v[0] : String(v)
        })
        setFormErrors(errors); setModal('add')
      } else {
        showResult('Failed to create vehicle owner.', 'error')
      }
    } finally { setSubmitting(false) }
  }

  const onAddClick = () => {
    if (addType === 'guard')  { if (!validateGuard()) return;  setModal('confirmAdd') }
    if (addType === 'owner')  { if (!validateOwner()) return;  setModal('confirmAdd') }
    if (addType === 'admin')  { if (!validateAdmin()) return;  setModal('confirmAdd') }
  }

  /* ── admin validation ── */
  const validateAdmin = () => {
    const errors = {}
    if (!adminForm.full_name.trim()) errors.full_name = 'Full name is required.'
    if (!adminForm.email.trim()) errors.email = 'Email is required.'
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(adminForm.email)) errors.email = 'Invalid email format.'
    if (!adminForm.password) errors.password = 'Password is required.'
    else if (getStrength(adminForm.password).score < 5) errors.password = 'Password does not meet all requirements.'
    if (!adminForm.confirm_password) errors.confirm_password = 'Please confirm password.'
    else if (adminForm.password !== adminForm.confirm_password) errors.confirm_password = 'Passwords do not match.'
    setFormErrors(errors)
    return Object.keys(errors).length === 0
  }

  const handleReplaceAdmin = async () => {
    setSubmitting(true)
    try {
      await usersApi.replaceAdmin({
        full_name: adminForm.full_name.trim(), email: adminForm.email.trim(),
        password: adminForm.password, confirm_password: adminForm.confirm_password,
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

  const adminStrength = getStrength(adminForm.password)
  const formatDate    = (iso) => iso ? new Date(iso).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' }) : '—'
  const roleLabel     = (role) => role === 'security' ? 'Security' : role === 'vehicle_owner' ? 'Vehicle Owner' : role

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
          <button className="um-gate-login-btn" onClick={() => setShowGateConfirm(true)}>
            <LogIn size={16} /> Gate Login
          </button>
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
          <div className="um-modal um-modal--wide" onClick={(e) => e.stopPropagation()}>
            <div className="um-modal-header">
              <h2>
                {addType === 'guard' ? 'Add Security Guard'
                  : addType === 'owner' ? 'Add Vehicle Owner'
                  : addType === 'admin' ? 'Replace Admin'
                  : 'Add User'}
              </h2>
              <button className="um-modal-close" onClick={closeModal}><X size={18} /></button>
            </div>

            <div className="um-modal-body um-modal-scroll">

              {/* ── type selector ── */}
              {addType === null && (
                <div className="um-user-type-selector">
                  <div className="um-type-option" onClick={() => setAddType('guard')}>
                    <div className="um-type-option-icon"><Shield size={20} /></div>
                    <span className="um-type-option-label">Security Guard</span>
                    <span className="um-type-option-desc">Name & gate — QR badge auto-generated</span>
                  </div>
                  <div className="um-type-option" onClick={() => setAddType('owner')}>
                    <div className="um-type-option-icon"><Car size={20} /></div>
                    <span className="um-type-option-label">Vehicle Owner</span>
                    <span className="um-type-option-desc">Full registration — password emailed</span>
                  </div>
                  <div className="um-type-option" onClick={() => setAddType('admin')}>
                    <div className="um-type-option-icon"><ShieldAlert size={20} /></div>
                    <span className="um-type-option-label">New Admin</span>
                    <span className="um-type-option-desc">Replaces current admin account</span>
                  </div>
                </div>
              )}

              {/* ══ GUARD FORM ══ */}
              {addType === 'guard' && (
                <>
                  <div className="um-info-banner">
                    <Info size={14} />
                    Guards authenticate via QR badge only — no email or password is needed.
                    The QR badge will be shown immediately after creation.
                  </div>
                  <div className="um-form-group">
                    <label>Full Name <span className="um-required">*</span></label>
                    <input className={`um-form-input ${formErrors.full_name ? 'error' : ''}`}
                      value={guardForm.full_name}
                      onChange={e => setGuardForm({ ...guardForm, full_name: e.target.value })}
                      placeholder="e.g. Juan Dela Cruz" />
                    {formErrors.full_name && <div className="um-form-error">{formErrors.full_name}</div>}
                  </div>
                </>
              )}

              {/* ══ OWNER FORM ══ */}
              {addType === 'owner' && (
                <>
                  <div className="um-info-banner">
                    <Info size={14} />
                    A temporary password will be auto-generated and emailed to the owner.
                    They must change it on first login.
                  </div>

                  {/* Registrant type */}
                  <div className="um-form-section-title">Registrant Type</div>
                  <div className="um-reg-type-row">
                    {['student', 'employee', 'fetcher'].map(t => (
                      <button key={t} type="button"
                        className={`um-reg-type-btn ${ownerForm.registrant_type === t ? 'selected' : ''}`}
                        onClick={() => setOwnerForm({ ...ownerForm, registrant_type: t, campus_days: [], student_level: '' })}>
                        {t === 'student' ? 'Student' : t === 'employee' ? 'Employee' : 'Fetcher / Drop & Go'}
                      </button>
                    ))}
                  </div>
                  {formErrors.registrant_type && <div className="um-form-error">{formErrors.registrant_type}</div>}

                  {/* Vehicle */}
                  <div className="um-form-section-title">Vehicle Information</div>
                  <div className="um-form-grid-2">
                    <div className="um-form-group">
                      <label>Plate Number <span className="um-required">*</span></label>
                      <input className={`um-form-input ${formErrors.plate_number ? 'error' : ''}`}
                        value={ownerForm.plate_number}
                        onChange={e => setOwnerForm({ ...ownerForm, plate_number: e.target.value.toUpperCase() })}
                        placeholder="e.g. ABC 1234" />
                      {formErrors.plate_number && <div className="um-form-error">{formErrors.plate_number}</div>}
                    </div>
                    <div className="um-form-group">
                      <label>Conduction Number</label>
                      <input className="um-form-input"
                        value={ownerForm.conduction_number}
                        onChange={e => setOwnerForm({ ...ownerForm, conduction_number: e.target.value })}
                        placeholder="e.g. CS12345A678" />
                    </div>
                    <div className="um-form-group">
                      <label>Vehicle Type <span className="um-required">*</span></label>
                      <select className={`um-form-select ${formErrors.vehicle_type ? 'error' : ''}`}
                        value={ownerForm.vehicle_type}
                        onChange={e => setOwnerForm({ ...ownerForm, vehicle_type: e.target.value })}>
                        <option value="">Select Type</option>
                        <option>Sedan</option><option>SUV</option>
                        <option>Motorcycle</option><option>Tricycle</option>
                        <option>Van</option><option>Truck</option><option>Other</option>
                      </select>
                      {formErrors.vehicle_type && <div className="um-form-error">{formErrors.vehicle_type}</div>}
                    </div>
                    <div className="um-form-group">
                      <label>Vehicle Color</label>
                      <input className="um-form-input"
                        value={ownerForm.vehicle_color}
                        onChange={e => setOwnerForm({ ...ownerForm, vehicle_color: e.target.value.toUpperCase() })}
                        placeholder="e.g. White" />
                    </div>
                    {ownerForm.vehicle_type === 'Tricycle' && (
                      <div className="um-form-group um-col-span-2">
                        <label>Body Number</label>
                        <input className="um-form-input"
                          value={ownerForm.body_number}
                          onChange={e => setOwnerForm({ ...ownerForm, body_number: e.target.value })} />
                      </div>
                    )}
                  </div>

                  {/* Personal info */}
                  <div className="um-form-section-title">Personal Information</div>
                  <div className="um-form-grid-2">
                    <div className="um-form-group">
                      <label>Last Name <span className="um-required">*</span></label>
                      <input className={`um-form-input ${formErrors.last_name ? 'error' : ''}`}
                        value={ownerForm.last_name}
                        onChange={e => setOwnerForm({ ...ownerForm, last_name: e.target.value.toUpperCase() })}
                        placeholder="e.g. Dela Cruz" />
                      {formErrors.last_name && <div className="um-form-error">{formErrors.last_name}</div>}
                    </div>
                    <div className="um-form-group">
                      <label>First Name <span className="um-required">*</span></label>
                      <input className={`um-form-input ${formErrors.first_name ? 'error' : ''}`}
                        value={ownerForm.first_name}
                        onChange={e => setOwnerForm({ ...ownerForm, first_name: e.target.value.toUpperCase() })}
                        placeholder="e.g. Juan" />
                      {formErrors.first_name && <div className="um-form-error">{formErrors.first_name}</div>}
                    </div>
                    <div className="um-form-group um-col-span-2">
                      <label>Middle Name <span className="um-optional">(optional)</span></label>
                      <input className="um-form-input"
                        value={ownerForm.middle_name}
                        onChange={e => setOwnerForm({ ...ownerForm, middle_name: e.target.value.toUpperCase() })}
                        placeholder="e.g. Santos" />
                    </div>

                    {/* Address */}
                    <div className="um-form-group um-col-span-2">
                      <label>House / Unit No. & Street <span className="um-required">*</span></label>
                      <input className={`um-form-input ${formErrors.house_street ? 'error' : ''}`}
                        value={ownerForm.house_street}
                        onChange={e => setOwnerForm({ ...ownerForm, house_street: e.target.value })}
                        placeholder="e.g. 123 Rizal Street" />
                      {formErrors.house_street && <div className="um-form-error">{formErrors.house_street}</div>}
                    </div>
                    <div className="um-form-group">
                      <label>Province <span className="um-required">*</span></label>
                      <select className={`um-form-select ${formErrors.province ? 'error' : ''}`}
                        value={selProvince}
                        onChange={e => {
                          const opt = provinces.find(p => p.code === e.target.value)
                          setSelProvince(e.target.value); setSelCity('')
                          setOwnerForm(f => ({ ...f, province: opt?.name ?? '', city_municipality: '', barangay: '' }))
                        }}>
                        <option value="">Select Province</option>
                        {provinces.map(p => <option key={p.code} value={p.code}>{p.name}</option>)}
                      </select>
                      {formErrors.province && <div className="um-form-error">{formErrors.province}</div>}
                    </div>
                    <div className="um-form-group">
                      <label>City / Municipality <span className="um-required">*</span></label>
                      <select className={`um-form-select ${formErrors.city_municipality ? 'error' : ''}`}
                        value={selCity}
                        disabled={!selProvince || loadingCities}
                        onChange={e => {
                          const opt = cities.find(c => c.code === e.target.value)
                          setSelCity(e.target.value)
                          setOwnerForm(f => ({ ...f, city_municipality: opt?.name ?? '', barangay: '' }))
                        }}>
                        <option value="">{loadingCities ? 'Loading…' : !selProvince ? 'Select province first' : 'Select City / Municipality'}</option>
                        {cities.map(c => <option key={c.code} value={c.code}>{c.name}</option>)}
                      </select>
                      {formErrors.city_municipality && <div className="um-form-error">{formErrors.city_municipality}</div>}
                    </div>
                    <div className="um-form-group">
                      <label>Barangay <span className="um-required">*</span></label>
                      <select className={`um-form-select ${formErrors.barangay ? 'error' : ''}`}
                        value={ownerForm.barangay}
                        disabled={!selCity || loadingBarangays}
                        onChange={e => setOwnerForm(f => ({ ...f, barangay: e.target.value }))}>
                        <option value="">{loadingBarangays ? 'Loading…' : !selCity ? 'Select city first' : 'Select Barangay'}</option>
                        {barangays.map(b => <option key={b.code} value={b.name}>{b.name}</option>)}
                      </select>
                      {formErrors.barangay && <div className="um-form-error">{formErrors.barangay}</div>}
                    </div>

                    {/* Contact + email */}
                    <div className="um-form-group um-col-span-2">
                      <label>Email Address <span className="um-required">*</span></label>
                      <input className={`um-form-input ${formErrors.email ? 'error' : ''}`}
                        type="email" value={ownerForm.email}
                        onChange={e => setOwnerForm({ ...ownerForm, email: e.target.value })}
                        placeholder="e.g. juan@example.com" />
                      {formErrors.email && <div className="um-form-error">{formErrors.email}</div>}
                    </div>
                    <div className="um-form-group">
                      <label>Contact Number <span className="um-required">*</span></label>
                      <input className={`um-form-input ${formErrors.contact_number ? 'error' : ''}`}
                        value={ownerForm.contact_number}
                        onChange={e => setOwnerForm({ ...ownerForm, contact_number: e.target.value })}
                        placeholder="+639XXXXXXXXX" />
                      {formErrors.contact_number && <div className="um-form-error">{formErrors.contact_number}</div>}
                    </div>
                    <div className="um-form-group">
                      <label>Age</label>
                      <input className="um-form-input" type="number" min="15" max="99"
                        value={ownerForm.age}
                        onChange={e => setOwnerForm({ ...ownerForm, age: e.target.value })} />
                    </div>
                    <div className="um-form-group um-col-span-2">
                      <label>Driver's License Number <span className="um-required">*</span></label>
                      <input className={`um-form-input ${formErrors.drivers_license ? 'error' : ''}`}
                        value={ownerForm.drivers_license}
                        onChange={e => setOwnerForm({ ...ownerForm, drivers_license: e.target.value.toUpperCase() })}
                        placeholder="e.g. N01-20-123456" maxLength={13} />
                      {formErrors.drivers_license && <div className="um-form-error">{formErrors.drivers_license}</div>}
                    </div>
                  </div>

                  {/* Student-specific */}
                  {ownerForm.registrant_type === 'student' && (
                    <>
                      <div className="um-form-section-title">Student Details</div>
                      <div className="um-form-group">
                        <label>Education Level <span className="um-required">*</span></label>
                        <div className="um-level-row">
                          {['college', 'shs', 'jhs', 'elementary', 'sped'].map(lvl => (
                            <button key={lvl} type="button"
                              className={`um-level-btn ${ownerForm.student_level === lvl ? 'selected' : ''}`}
                              onClick={() => setOwnerForm(f => ({
                                ...f, student_level: lvl,
                                student_grade: '', student_strand: '', program_year: '',
                                campus_days: lvl === 'sped' ? CAMPUS_DAYS.map(d => d.key) : [],
                              }))}>
                              {lvl === 'college' ? 'College' : lvl === 'shs' ? 'Senior HS' : lvl === 'jhs' ? 'Junior HS' : lvl === 'elementary' ? 'Elementary' : 'SpEd'}
                            </button>
                          ))}
                        </div>
                        {formErrors.student_level && <div className="um-form-error">{formErrors.student_level}</div>}
                      </div>
                      <div className="um-form-grid-2">
                        <div className="um-form-group">
                          <label>Student ID <span className="um-required">*</span></label>
                          <input className={`um-form-input ${formErrors.student_id ? 'error' : ''}`}
                            value={ownerForm.student_id}
                            onChange={e => setOwnerForm({ ...ownerForm, student_id: e.target.value })}
                            placeholder="e.g. 23100174" />
                          {formErrors.student_id && <div className="um-form-error">{formErrors.student_id}</div>}
                        </div>
                        {ownerForm.student_level === 'college' && (
                          <div className="um-form-group">
                            <label>Program & Year Level</label>
                            <input className="um-form-input"
                              value={ownerForm.program_year}
                              onChange={e => setOwnerForm({ ...ownerForm, program_year: e.target.value })}
                              placeholder="e.g. BSIT - 3" />
                          </div>
                        )}
                        {ownerForm.student_level === 'shs' && (
                          <>
                            <div className="um-form-group">
                              <label>Track / Strand</label>
                              <select className="um-form-select" value={ownerForm.student_strand}
                                onChange={e => setOwnerForm({ ...ownerForm, student_strand: e.target.value })}>
                                <option value="">Select Strand</option>
                                {['ABM','STEM','HUMSS','ICT','HE'].map(s => <option key={s}>{s}</option>)}
                              </select>
                            </div>
                            <div className="um-form-group">
                              <label>Grade Level</label>
                              <select className="um-form-select" value={ownerForm.student_grade}
                                onChange={e => setOwnerForm({ ...ownerForm, student_grade: e.target.value })}>
                                <option value="">Select</option>
                                <option value="11">Grade 11</option><option value="12">Grade 12</option>
                              </select>
                            </div>
                          </>
                        )}
                        {ownerForm.student_level === 'jhs' && (
                          <div className="um-form-group">
                            <label>Grade Level</label>
                            <select className="um-form-select" value={ownerForm.student_grade}
                              onChange={e => setOwnerForm({ ...ownerForm, student_grade: e.target.value })}>
                              <option value="">Select</option>
                              {['7','8','9','10'].map(g => <option key={g} value={g}>Grade {g}</option>)}
                            </select>
                          </div>
                        )}
                        {ownerForm.student_level === 'elementary' && (
                          <div className="um-form-group">
                            <label>Grade Level</label>
                            <select className="um-form-select" value={ownerForm.student_grade}
                              onChange={e => setOwnerForm({ ...ownerForm, student_grade: e.target.value })}>
                              <option value="">Select</option>
                              {['Kinder 1','Kinder 2','1','2','3','4','5','6'].map(g => (
                                <option key={g} value={g}>{g.includes('Kinder') ? g : `Grade ${g}`}</option>
                              ))}
                            </select>
                          </div>
                        )}
                      </div>
                      {/* Campus days */}
                      {ownerForm.student_level && ownerForm.student_level !== 'sped' && (
                        <div className="um-form-group">
                          <label>Campus Days <span className="um-required">*</span></label>
                          <div className="um-day-picker">
                            {CAMPUS_DAYS.map(d => {
                              const sel = ownerForm.campus_days.includes(d.key)
                              const max = ownerForm.campus_days.length >= 3 && !sel
                              return (
                                <button key={d.key} type="button"
                                  className={`um-day-btn ${sel ? 'selected' : ''} ${max ? 'disabled' : ''}`}
                                  onClick={() => !max && toggleOwnerDay(d.key)}>
                                  {d.short}
                                </button>
                              )
                            })}
                          </div>
                          {formErrors.campus_days && <div className="um-form-error">{formErrors.campus_days}</div>}
                          <div className="um-day-note">{ownerForm.campus_days.length}/3 days selected</div>
                        </div>
                      )}
                      {ownerForm.student_level === 'sped' && (
                        <p className="um-info-note"><Info size={12} /> SpEd students are assigned all campus days.</p>
                      )}
                    </>
                  )}

                  {/* Employee-specific */}
                  {ownerForm.registrant_type === 'employee' && (
                    <>
                      <div className="um-form-section-title">Employee Details</div>
                      <div className="um-form-grid-2">
                        <div className="um-form-group">
                          <label>Employee ID <span className="um-required">*</span></label>
                          <input className={`um-form-input ${formErrors.employee_id ? 'error' : ''}`}
                            value={ownerForm.employee_id}
                            onChange={e => setOwnerForm({ ...ownerForm, employee_id: e.target.value })}
                            placeholder="e.g. 23100174" />
                          {formErrors.employee_id && <div className="um-form-error">{formErrors.employee_id}</div>}
                        </div>
                        <div className="um-form-group">
                          <label>Department <span className="um-required">*</span></label>
                          <select className={`um-form-select ${formErrors.department_type ? 'error' : ''}`}
                            value={ownerForm.department_type}
                            onChange={e => setOwnerForm({ ...ownerForm, department_type: e.target.value })}>
                            <option value="">Select Department</option>
                            <option value="teaching">Teaching</option>
                            <option value="non_teaching">Non-Teaching</option>
                          </select>
                          {formErrors.department_type && <div className="um-form-error">{formErrors.department_type}</div>}
                        </div>
                      </div>
                    </>
                  )}

                  {ownerForm.registrant_type === 'fetcher' && (
                    <p className="um-info-note"><Info size={12} /> Fetchers may enter on any day during designated drop-off and pick-up hours.</p>
                  )}
                </>
              )}

              {/* ══ ADMIN FORM ══ */}
              {addType === 'admin' && (
                <>
                  <div className="um-admin-warning">
                    <AlertTriangle size={18} />
                    <div className="um-admin-warning-text">
                      <strong>Warning:</strong> This will <strong>delete your current admin account</strong> and create a new one. You will be logged out immediately.
                    </div>
                  </div>
                  <div className="um-form-group">
                    <label>Full Name <span className="um-required">*</span></label>
                    <input className={`um-form-input ${formErrors.full_name ? 'error' : ''}`}
                      value={adminForm.full_name}
                      onChange={e => setAdminForm({ ...adminForm, full_name: e.target.value })}
                      placeholder="Enter full name" />
                    {formErrors.full_name && <div className="um-form-error">{formErrors.full_name}</div>}
                  </div>
                  <div className="um-form-group">
                    <label>Email <span className="um-required">*</span></label>
                    <input className={`um-form-input ${formErrors.email ? 'error' : ''}`}
                      type="email" value={adminForm.email}
                      onChange={e => setAdminForm({ ...adminForm, email: e.target.value })}
                      placeholder="Enter email address" />
                    {formErrors.email && <div className="um-form-error">{formErrors.email}</div>}
                  </div>
                  <div className="um-form-group">
                    <label>Password <span className="um-required">*</span></label>
                    <div className="um-password-wrapper">
                      <input className={`um-form-input ${formErrors.password ? 'error' : ''}`}
                        type={showPassword ? 'text' : 'password'} value={adminForm.password}
                        onChange={e => setAdminForm({ ...adminForm, password: e.target.value })}
                        placeholder="Enter password" />
                      <button type="button" className="um-password-toggle" onClick={() => setShowPassword(v => !v)} tabIndex={-1}>
                        {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                      </button>
                    </div>
                    {formErrors.password && <div className="um-form-error">{formErrors.password}</div>}
                    {adminForm.password && (
                      <div className="um-password-strength">
                        <div className="um-strength-bar-bg"><div className={`um-strength-bar ${adminStrength.level}`} /></div>
                        <span className={`um-strength-label ${adminStrength.level}`}>{STRENGTH_LABELS[adminStrength.level] || ''}</span>
                        <div className="um-password-rules">
                          {PASSWORD_RULES.map(rule => (
                            <div key={rule.key} className={`um-password-rule ${rule.test(adminForm.password) ? 'met' : ''}`}>
                              {rule.test(adminForm.password) ? <Check size={12} /> : <Circle size={12} />}
                              {rule.label}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                  <div className="um-form-group">
                    <label>Confirm Password <span className="um-required">*</span></label>
                    <div className="um-password-wrapper">
                      <input className={`um-form-input ${formErrors.confirm_password ? 'error' : ''}`}
                        type={showConfirm ? 'text' : 'password'} value={adminForm.confirm_password}
                        onChange={e => setAdminForm({ ...adminForm, confirm_password: e.target.value })}
                        placeholder="Re-enter password" />
                      <button type="button" className="um-password-toggle" onClick={() => setShowConfirm(v => !v)} tabIndex={-1}>
                        {showConfirm ? <EyeOff size={16} /> : <Eye size={16} />}
                      </button>
                    </div>
                    {formErrors.confirm_password && <div className="um-form-error">{formErrors.confirm_password}</div>}
                  </div>
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
              <button className="um-btn-primary" disabled={submitting} onClick={addType === 'admin' ? handleReplaceAdmin : addType === 'guard' ? handleAddGuard : handleAddOwner}>
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
                <div style={{ display: 'flex', gap: 8 }}>
                  <button className="um-btn-warning" disabled={qrLoading} onClick={handleRegenerateQR}
                    style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <RefreshCw size={14} />
                    {qrLoading ? 'Regenerating…' : 'Regenerate Badge'}
                  </button>
                  <button className="um-btn-primary" onClick={() => setShowGateConfirm(true)}
                    style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <LogIn size={14} /> Go to Gate Login
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* GATE LOGIN CONFIRM */}
      {showGateConfirm && (
        <div className="um-modal-overlay" onClick={() => setShowGateConfirm(false)}>
          <div className="um-modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 420 }}>
            <div className="um-modal-header">
              <h2>Open Gate Login?</h2>
              <button className="um-modal-close" onClick={() => setShowGateConfirm(false)}><X size={18} /></button>
            </div>
            <div className="um-modal-body">
              <div className="um-confirm-body">
                <div className="um-confirm-icon warning">
                  <LogIn size={24} />
                </div>
                <h3>You will be logged out</h3>
                <p>This will end your admin session and open the security gate login screen for guard clock-in.</p>
              </div>
            </div>
            <div className="um-modal-footer">
              <button className="um-btn-secondary" onClick={() => setShowGateConfirm(false)}>Cancel</button>
              <button className="um-btn-primary" onClick={() => logout('/security/qr-login')}>
                <LogIn size={16} /> Proceed
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
