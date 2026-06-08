import api from './axios'

export const usersApi = {
  /** List all non-admin users. Optional search by name, role, status. Paginated. */
  getUsers: async (search = '', page = 1, role = '', status = '') => {
    const params = { page }
    if (search) params.search = search
    if (role) params.role = role
    if (status) params.status = status
    const { data } = await api.get('/api/accounts/users/', { params })
    return data
  },

  /** Get a single user by ID. */
  getUserById: async (id) => {
    const { data } = await api.get(`/api/accounts/users/${id}/`)
    return data
  },

  /** Register a new user (security or vehicle_owner). */
  createUser: async (userData) => {
    const { data } = await api.post('/api/accounts/register/', userData)
    return data
  },

  /** Update user details (full_name, email, role). */
  updateUser: async (id, userData) => {
    const { data } = await api.patch(`/api/accounts/users/${id}/update/`, userData)
    return data
  },

  /** Hard-delete a user. */
  deleteUser: async (id) => {
    const { data } = await api.delete(`/api/accounts/users/${id}/delete/`)
    return data
  },

  /** Toggle user active/disabled status. */
  toggleUserStatus: async (id) => {
    const { data } = await api.post(`/api/accounts/users/${id}/toggle-status/`)
    return data
  },

  /** Replace the current admin with a new admin account. */
  replaceAdmin: async (adminData) => {
    const { data } = await api.post('/api/accounts/replace-admin/', adminData)
    return data
  },

  /** Get audit logs (admin sees all, security sees own actions). */
  getAuditLogs: async (params = {}) => {
    const { data } = await api.get('/api/accounts/audit-logs/', { params })
    return data
  },

  /** Get audit log statistics (admin only). */
  getAuditLogStats: async () => {
    const { data } = await api.get('/api/accounts/audit-logs/stats/')
    return data
  },
}
