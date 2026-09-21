// Camera administration — the CRUD behind the Device Management screen, plus
// the three that talk to the hardware: detectRtsp, ping and ptz. Those three
// are slow by nature (they reach out over the network to a physical device),
// so callers are expected to show progress rather than assume a quick reply.
import api from './axios'

export const camerasApi = {
  list: async (params = {}) => {
    const { data } = await api.get('/vehicles/cameras/', { params })
    return data
  },
  // The server proposes the next free camera name, so two admins adding
  // cameras at once do not both pick the same one.
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
  // `channel` matters on an NVR or multi-lens unit: dropping it here made every
  // probe ask for channel 1, so the second camera on a device could not be found.
  detectRtsp: async ({ ip, device_id, password, channel = 1 }) => {
    const { data } = await api.post('/vehicles/cameras/detect-rtsp/',
                                    { ip, device_id, password, channel })
    return data   // { ok, rtsp_url, format, attempts }
  },
  remove: async (id) => {
    await api.delete(`/vehicles/cameras/${id}/`)
  },
  ping: async (id) => {
    const { data } = await api.post(`/vehicles/cameras/${id}/ping/`)
    return data
  },
  // Pan/tilt/zoom. `speed` is a 0-1 fraction with a middling default, so a
  // caller that only sends a direction gets a usable movement.
  ptz: async (id, command, speed = 0.5) => {
    const { data } = await api.post(`/vehicles/cameras/${id}/ptz/`, { command, speed })
    return data
  },
}
