import api from './axios'

// ── Legacy parking space CRUD (no zone) ──────────────────────────
export const parkingApi = {
  listAll: async () => {
    const { data } = await api.get('/vehicles/parking/')
    return data
  },
  markOccupied: async (id, plateNumber) => {
    const { data } = await api.patch(`/vehicles/parking/${id}/`, {
      is_occupied: true,
      occupied_by: plateNumber.trim().toUpperCase(),
    })
    return data
  },
  markFree: async (id) => {
    const { data } = await api.patch(`/vehicles/parking/${id}/`, {
      is_occupied: false,
      occupied_by: '',
    })
    return data
  },
  remove: async (id) => {
    await api.delete(`/vehicles/parking/${id}/`)
  },
}

// ── Zone-based parking management ─────────────────────────────────
export const zoneApi = {
  listAll: async () => {
    const { data } = await api.get('/vehicles/parking-zones/')
    return data
  },
  get: async (id) => {
    const { data } = await api.get(`/vehicles/parking-zones/${id}/`)
    return data
  },
  create: async ({ name, vehicle_category }) => {
    const { data } = await api.post('/vehicles/parking-zones/', { name, vehicle_category })
    return data
  },
  remove: async (id) => {
    await api.delete(`/vehicles/parking-zones/${id}/`)
  },
  uploadImage: async (id, file) => {
    const form = new FormData()
    form.append('image', file)
    const { data } = await api.post(`/vehicles/parking-zones/${id}/upload-image/`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    return data
  },
  saveLayout: async (id, spaces) => {
    const { data } = await api.post(`/vehicles/parking-zones/${id}/save-layout/`, { spaces })
    return data
  },
  markOccupied: async (spaceId, plateNumber) => {
    const { data } = await api.patch(`/vehicles/parking/${spaceId}/`, {
      is_occupied: true,
      occupied_by: plateNumber.trim().toUpperCase(),
    })
    return data
  },
  markFree: async (spaceId) => {
    const { data } = await api.patch(`/vehicles/parking/${spaceId}/`, {
      is_occupied: false,
      occupied_by: '',
    })
    return data
  },
}
