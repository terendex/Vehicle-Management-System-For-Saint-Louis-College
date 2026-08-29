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
  // `camera` is the Device Management camera id (or null). A zone created
  // without one has no feed to draw against and no detector to run, so the
  // caller should pass the camera the admin is actually looking at.
  create: async ({ name, vehicle_category, camera = null }) => {
    const { data } = await api.post('/vehicles/parking-zones/', {
      name, vehicle_category, camera,
    })
    return data
  },
  update: async (id, fields) => {
    const { data } = await api.patch(`/vehicles/parking-zones/${id}/`, fields)
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

  setCapacity: async (id, capacity_override) => {
    const { data } = await api.patch(`/vehicles/parking-zones/${id}/set-capacity/`, { capacity_override })
    return data
  },

  // ── Bay scoring method ───────────────────────────────────────────
  // 'ml'      — the vehicle detector decides which bays are taken
  // 'classic' — each bay is compared against an empty-lot baseline, no model.
  // A zone set to 'classic' with no baseline captured keeps running on the
  // detector, so `has_baseline` is what says which is actually in effect.
  setOccupancyMethod: async (id, occupancy_method) => {
    const { data } = await api.patch(`/vehicles/parking-zones/${id}/`, { occupancy_method })
    return data
  },

  // Captures the current live frame as the empty-lot baseline. The camera must
  // be running, and the lot must actually be empty — a car in shot becomes part
  // of that bay's idea of "empty".
  setBaseline: async (id) => {
    const { data } = await api.post(`/vehicles/parking-zones/${id}/set-baseline/`)
    return data
  },

  // Raw per-bay scores, for tuning thresholds against a real camera.
  getSignals: async (id) => {
    const { data } = await api.get(`/vehicles/parking-zones/${id}/signals/`)
    return data
  },

  // Vehicles the zone is following, with how long each has been stationary.
  // Occupancy and double parking both wait for a vehicle to settle, so this is
  // what explains a bay that is taken on screen but still reads free.
  getTrackedVehicles: async (id) => {
    const { data } = await api.get(`/vehicles/parking-zones/${id}/tracked-vehicles/`)
    return Array.isArray(data) ? data : []
  },

  // ── IP Camera ────────────────────────────────────────────────────
  startCamera: async (id) => {
    const { data } = await api.post(`/vehicles/parking-zones/${id}/start-camera/`)
    return data
  },
  stopCamera: async (id) => {
    const { data } = await api.post(`/vehicles/parking-zones/${id}/stop-camera/`)
    return data
  },
  getCameraStatus: async () => {
    const { data } = await api.get('/vehicles/parking-zones/camera-status/')
    return data  // { zone_id: bool }
  },
  // The boxes the detector last saw, per running zone. Polled rather than
  // pushed: the worker only re-detects every couple of seconds, so a socket
  // would spend most of its life idle to show the same rectangles.
  getDetections: async () => {
    const { data } = await api.get('/vehicles/parking-zones/detections/')
    return data && typeof data === 'object' ? data : {}
  },

  // Live double-parking alerts. Self-clearing: an entry disappears once the
  // vehicle moves off the line, so this is current state, not a history.
  getAlerts: async () => {
    const { data } = await api.get('/vehicles/parking-zones/alerts/')
    return Array.isArray(data) ? data : []
  },

  // Guard names the vehicle behind a double-parking alert → issues the violation
  // with the captured evidence and clears the alert.
  attributeDoublePark: async (zoneId, spaceIds, plateNumber) => {
    const { data } = await api.post('/vehicles/parking-zones/attribute-double-park/', {
      zone_id: zoneId, space_ids: spaceIds, plate_number: plateNumber,
    })
    return data
  },
}
