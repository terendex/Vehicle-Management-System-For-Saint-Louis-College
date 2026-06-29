import api from './axios'

export const usersApi = {
  /** List all non-admin users. Optional search by name, role, status. Paginated. */
  getUsers: async (search = '', page = 1, role = '', status = '') => {
    const params = { page }
    if (search) params.search = search
    if (role) params.role = role
    if (status) params.status = status
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

  /** Hard-delete a user. */
  deleteUser: async (id) => {
    const { data } = await api.delete(`/accounts/users/${id}/delete/`)
    return data
  },

  /** Toggle user active/disabled status. */
  toggleUserStatus: async (id) => {
    const { data } = await api.post(`/accounts/users/${id}/toggle-status/`)
    return data
  },

  /** Replace the current admin with a new admin account. */
  replaceAdmin: async (adminData) => {
    const { data } = await api.post('/accounts/replace-admin/', adminData)
    return data
  },

  /** Get audit logs (admin sees all, security sees own actions). */
  getAuditLogs: async (params = {}) => {
    const { data } = await api.get('/accounts/audit-logs/', { params })
    return data
  },

  /** Create an audit log entry manually (used for device management actions). */
  createAuditLog: async (payload) => {
    const { data } = await api.post('/accounts/audit-logs/', payload)
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

  /** Change the authenticated user's password. */
  changePassword: async (currentPassword, newPassword, confirmPassword) => {
    const { data } = await api.post('/accounts/change-password/', {
      current_password: currentPassword,
      new_password: newPassword,
      confirm_password: confirmPassword,
    })
    return data
  },

  /** Get a guard's QR token (admin only). */
  getGuardQR: async (id) => {
    const { data } = await api.get(`/accounts/users/${id}/qr/`)
    return data
  },

  /** Regenerate a guard's QR token, invalidating the old one (admin only). */
  regenerateGuardQR: async (id) => {
    const { data } = await api.post(`/accounts/users/${id}/regenerate-qr/`)
    return data
  },
}
