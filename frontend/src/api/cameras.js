import api from './axios'

export const camerasApi = {
  list: async (params = {}) => {
    const { data } = await api.get('/vehicles/cameras/', { params })
    return data
  },
  nextName: async () => {
    const { data } = await api.get('/vehicles/cameras/next-name/')
    return data
  },
  create: async (payload) => {
    const { data } = await api.post('/vehicles/cameras/', payload)
    return data
  },
  update: async (id, payload) => {
    const { data } = await api.patch(`/vehicles/cameras/${id}/`, payload)
    return data
  },
  remove: async (id) => {
    await api.delete(`/vehicles/cameras/${id}/`)
  },
}
