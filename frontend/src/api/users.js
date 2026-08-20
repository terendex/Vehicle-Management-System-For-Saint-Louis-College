import api from './axios'

export const usersApi = {
  /** List all non-admin users. Optional search by name, role, status. Paginated. */
  getUsers: async (search = '', page = 1, role = '', status = '', registrantType = '') => {
    const params = { page }
    if (search) params.search = search
    if (role) params.role = role
    if (status) params.status = status
    if (registrantType) params.registrant_type = registrantType
    const { data } = await api.get('/accounts/users/', { params })
    return data
  },

  /** Get a single user by ID. */
  getUserById: async (id) => {
    const { data } = await api.get(`/accounts/users/${id}/`)
    return data
  },

  /** Register a new user (security or vehicle_owner). */
  createUser: async (userData) => {
    const { data } = await api.post('/accounts/register/', userData)
    return data
  },

  /** Update user details (full_name, email, role, optional photo). */
  updateUser: async (id, userData) => {
    if (userData.photo instanceof File) {
      const fd = new FormData()
      Object.entries(userData).forEach(([k, v]) => { if (v !== null && v !== undefined) fd.append(k, v) })
      const { data } = await api.patch(`/accounts/users/${id}/update/`, fd)
      return data
    }
    const { data } = await api.patch(`/accounts/users/${id}/update/`, userData)
    return data
  },

  /** Re-download an owner's approved-registration PDF (admin only). */
  getRegistrationPdf: async (id) => {
    const { data } = await api.get(`/accounts/users/${id}/registration-pdf/`, {
      responseType: 'blob',
    })
    return data
  },

  /** Toggle user active/disabled status. */
  toggleUserStatus: async (id) => {
    const { data } = await api.post(`/accounts/users/${id}/toggle-status/`)
    return data
  },

  /** Replace the current admin with a new admin account (password auto-generated & emailed). */
  replaceAdmin: async (adminData) => {
    const { data } = await api.post('/accounts/replace-admin/', adminData)
    return data
  },

  /** Get audit logs (admin sees all, security sees own actions). */
  getAuditLogs: async (params = {}) => {
    const { data } = await api.get('/accounts/audit-logs/', { params })
    return data
  },

  /** Download the filtered audit log as an Excel (.xlsx) report (admin only). */
  exportAuditLogsExcel: async (params = {}) => {
    const { data } = await api.get('/accounts/audit-logs/export/', {
      params, responseType: 'blob',
    })
    return data
  },

  /** Download the filtered audit log as a branded PDF report (admin only). */
  exportAuditLogsPdf: async (params = {}) => {
    const { data } = await api.get('/accounts/audit-logs/export-pdf/', {
      params, responseType: 'blob',
    })
    return data
  },

  /** Download a full JSON backup of all application data (admin only). */
  downloadBackup: async () => {
    const { data } = await api.get('/accounts/system/backup/', { responseType: 'blob' })
    return data
  },

  /** List the backup files held on the server, plus the automatic-backup
   *  schedule they were written under (admin only). */
  listBackups: async () => {
    const { data } = await api.get('/accounts/system/backups/')
    return data
  },

  /** Download one backup already saved on the server (admin only). */
  downloadSavedBackup: async (name) => {
    const { data } = await api.get(`/accounts/system/backups/${encodeURIComponent(name)}/`, {
      responseType: 'blob',
    })
    return data
  },

  /** Delete one saved backup file from the server (admin only). */
  deleteSavedBackup: async (name) => {
    const { data } = await api.delete(`/accounts/system/backups/${encodeURIComponent(name)}/`)
    return data
  },

  /** Restore application data from an uploaded JSON backup file (admin only). */
  restoreBackup: async (file) => {
    const form = new FormData()
    form.append('file', file)
    const { data } = await api.post('/accounts/system/restore/', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    return data
  },

  /** Restore from a backup already on the server — same endpoint and the same
   *  safety snapshot as an upload, without the download / re-upload trip. */
  restoreSavedBackup: async (name) => {
    const { data } = await api.post('/accounts/system/restore/', { filename: name })
    return data
  },

  /** Create an audit log entry manually (used for device management actions). */
  createAuditLog: async (payload) => {
    const { data } = await api.post('/accounts/audit-logs/', payload)
    return data
  },

  /** Clear all audit log records (admin only). */
  clearAuditLogs: async () => {
    const { data } = await api.delete('/accounts/audit-logs/clear/')
    return data
  },

  /** Get audit log statistics (admin only). */
  getAuditLogStats: async () => {
    const { data } = await api.get('/accounts/audit-logs/stats/')
    return data
  },

  /** Get rich dashboard stats (users, vehicles, scans, recent activity). */
  getDashboardStats: async () => {
    const { data } = await api.get('/accounts/dashboard/stats/')
    return data
  },

  /** Get the vehicle owner's own registration record. */
  getMyRegistration: async () => {
    const { data } = await api.get('/accounts/me/registration/')
    return data
  },

  // One-time replacement of a conduction number with the real plate.
  swapPlate: async (plate_number) => {
    const { data } = await api.post('/accounts/me/plate-swap/', { plate_number })
    return data
  },

  /** Change the authenticated user's password. */
  changePassword: async (currentPassword, newPassword, confirmPassword) => {
    const { data } = await api.post('/accounts/change-password/', {
      current_password: currentPassword,
      new_password: newPassword,
      confirm_password: confirmPassword,
    })
    return data
  },

  /** Admin creates a security guard (name, email, agency — password auto-generated & emailed). */
  createGuard: async (data) => {
    const { data: res } = await api.post('/accounts/admin/create-guard/', data)
    return res
  },

  /** Get a guard's QR token (admin only). */
  getGuardQR: async (id) => {
    const { data } = await api.get(`/accounts/users/${id}/qr/`)
    return data
  },
}
