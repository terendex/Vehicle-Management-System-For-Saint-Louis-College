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

  /** Update user details (full_name, email, role). */
  updateUser: async (id, userData) => {
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
}
