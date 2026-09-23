// Parking, in two generations. The file says so itself in the two section
// headers below, and the split is the thing to understand:
//
//   parkingApi  the pre-zone API — bays addressed directly, no grouping
//   zoneApi     the replacement — bays belong to zones, which carry capacity
//
// The migration COMPLETED: four files import zoneApi (DoubleParkingAlerts,
// Events, ParkingManagement, SecurityParkingView) and none import parkingApi.
// The old half was simply left behind, so it is safe to delete rather than an
// unfinished piece of work. Recorded, not changed.
import api from './axios'

// ── Legacy parking space CRUD (no zone) ──────────────────────────
// UNREFERENCED in full — superseded by zoneApi below.
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
  create: async ({ name, vehicle_category, camera = null, lens_index = 0 }) => {
    const { data } = await api.post('/vehicles/parking-zones/', {
      name, vehicle_category, camera, lens_index,
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

  // ── Bay monitoring ───────────────────────────────────────────────
  // Bays are judged against an empty-lot baseline and nothing else; a zone
  // without one (`has_baseline` false) is not monitored at all. This copies the
  // zone's reference image into its baseline server-side, so the bays must be
  // empty in that picture — a car in it becomes that bay's idea of "empty".
  setBaseline: async (id) => {
    const { data } = await api.post(`/vehicles/parking-zones/${id}/set-baseline/`)
    return data
  },

  // Raw per-bay scores, for tuning thresholds against a real camera.
  //
  // `bays` is keyed by bay id and carries each reading together with the
  // per-bay thresholds it was judged against, whether those were measured or
  // are the fallback, how far that bay's live baseline has drifted, and why it
  // was last held occupied or stopped from claiming. The zone-level `fps` is
  // the measured scoring rate — a claim waits for `claim_frames` AND
  // `claim_seconds`, and the frame count only starts to bind below
  // claim_frames / claim_seconds.
  getSignals: async (id) => {
    const { data } = await api.get(`/vehicles/parking-zones/${id}/signals/`)
    return { fps: null, person_suppression: false, bays: {}, ...(data || {}) }
  },

  // Score bays against the captured baseline again, discarding the drift the
  // live baseline has picked up. Omit spaceId to reset the whole zone.
  resetLiveBaseline: async (id, spaceId = null) => {
    const { data } = await api.post(
      `/vehicles/parking-zones/${id}/reset-live-baseline/`,
      spaceId == null ? {} : { space_id: spaceId },
    )
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
