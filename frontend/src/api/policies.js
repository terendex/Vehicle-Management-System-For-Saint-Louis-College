// The Policies page (/policy). Anyone reads; the CDSO saves or restores a tab.
// A tab never edited comes back with `content: null`, and the page shows its
// built-in wording (pages/Policy/policyDefaults.js).
import api from './axios'

export const policiesApi = {
  list: async () => {
    const { data } = await api.get('/vehicles/policies/')
    return data
  },
  // Writes need a fresh two-factor step-up; the axios interceptor prompts for it.
  save: async (key, content) => {
    const { data } = await api.put(`/vehicles/policies/${key}/`, { content })
    return data
  },
  restoreDefault: async (key) => {
    const { data } = await api.delete(`/vehicles/policies/${key}/`)
    return data
  },
}
