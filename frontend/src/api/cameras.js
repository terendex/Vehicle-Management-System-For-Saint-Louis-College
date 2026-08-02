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
  // Ask the backend to probe the camera and report its working stream URL,
  // instead of making the admin identify the vendor.
  detectRtsp: async ({ ip, device_id, password }) => {
    const { data } = await api.post('/vehicles/cameras/detect-rtsp/', { ip, device_id, password })
    return data   // { ok, rtsp_url, format, attempts }
  },
  remove: async (id) => {
    await api.delete(`/vehicles/cameras/${id}/`)
  },
  ping: async (id) => {
    const { data } = await api.post(`/vehicles/cameras/${id}/ping/`)
    return data
  },
  ptz: async (id, command, speed = 0.5) => {
    const { data } = await api.post(`/vehicles/cameras/${id}/ptz/`, { command, speed })
    return data
  },
}
