import { useState, useEffect, useCallback, useRef } from 'react'
import {
  ParkingCircle, Bike, Car, RefreshCw, Video, Wifi,
} from 'lucide-react'
import SecurityLayout from '../../components/Layout/SecurityLayout'
import { zoneApi } from '../../api/parking'
import { useMultiRtspStream } from '../../hooks/useMultiRtspStream'
import '../Admin/ParkingManagement.css'

const CAT_OPTS = [
  { key: 'motorcycle', label: 'Motorcycle', Icon: Bike },
  { key: 'car',        label: 'Car',        Icon: Car  },
]

export default function SecurityParkingView() {
  const [zones,       setZones]       = useState([])
  const [selId,       setSelId]       = useState(null)
  const [camStatus,   setCamStatus]   = useState({})
  const [loading,     setLoading]     = useState(true)
  const [showCamPanel, setShowCamPanel] = useState(false)

  const token = typeof localStorage !== 'undefined'
    ? localStorage.getItem('access_token') || ''
    : ''

  const {
    cameras:        parkingCams,
    activeCamId:    pkActiveCamId,
    setActiveCamId: setPkActiveCam,
    activeCam:      pkActiveCam,
    addCamera:      addPkCamera,
    disconnectAll:  disconnectAllPkCams,
    registerCanvas: registerPkCanvas,
  } = useMultiRtspStream(token)

  const selZone    = zones.find(z => z.id === selId) ?? null
  const camRunning = selId != null && !!camStatus[selId]
  const streamUrl  = (selId && token && camRunning)
    ? `/api/vehicles/parking-zones/${selId}/stream/?token=${token}`
    : null

  // ── Load zones ──────────────────────────────────────────────────
  const loadZones = useCallback(async () => {
    setLoading(true)
    try {
      const data = await zoneApi.listAll()
      setZones(data)
      setSelId(id => id ?? data[0]?.id ?? null)
    } finally { setLoading(false) }
  }, [])

  useEffect(() => { loadZones() }, [loadZones])

  // Load cameras saved by admin from localStorage when zone changes
  useEffect(() => {
    disconnectAllPkCams()
    if (!selId) return
    const saved = JSON.parse(localStorage.getItem(`rtsp_cams_${selId}`) || '[]')
    saved.forEach(c => addPkCamera(c.name, c.url))
  }, [selId]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Camera status polling ───────────────────────────────────────
  const refreshCamStatus = useCallback(async () => {
    try {
      const s = await zoneApi.getCameraStatus()
      const normalised = {}
      Object.entries(s).forEach(([k, v]) => { normalised[parseInt(k, 10)] = v })
      setCamStatus(normalised)
    } catch { /* silent */ }
  }, [])

  useEffect(() => {
    refreshCamStatus()
    const t = setInterval(refreshCamStatus, 5000)
    return () => clearInterval(t)
  }, [refreshCamStatus])

  // ── Live occupancy polling ──────────────────────────────────────
  const refreshZone = useCallback(async () => {
    if (!selId) return
    try {
      const z = await zoneApi.get(selId)
      setZones(p => p.map(x => x.id === z.id ? z : x))
    } catch { /* silent */ }
  }, [selId])

  useEffect(() => {
    const t = setInterval(refreshZone, 8000)
    return () => clearInterval(t)
  }, [refreshZone])

  // ── Derived ─────────────────────────────────────────────────────
  const liveSpaces = selZone?.spaces ?? []
  const occ        = liveSpaces.filter(s => s.is_occupied).length
  const sumFr      = liveSpaces.length - occ

  return (
    <SecurityLayout>
      <div className="pm-page">

        {/* Header */}
        <div className="pm-header">
          <div className="pm-header-left">
            <ParkingCircle size={22} className="pm-header-icon" />
            <div>
              <h1 className="pm-title">Parking Overview</h1>
              <p className="pm-subtitle">
                Live view of parking zones and CCTV cameras. Auto-refreshes every 8 seconds.
              </p>
            </div>
          </div>
          <div className="pm-header-actions">
            <button className="pm-btn pm-btn--outline" onClick={loadZones} disabled={loading}>
              <RefreshCw size={14} className={loading ? 'pm-spin' : ''} /> Refresh
            </button>
            {parkingCams.length > 0 && (
              <button
                className={`pm-btn ${showCamPanel ? 'pm-btn--camera-on' : 'pm-btn--outline'}`}
                onClick={() => setShowCamPanel(p => !p)}
              >
                <Video size={14} /> Cameras ({parkingCams.length})
              </button>
            )}
          </div>
        </div>

        {/* Zone tabs */}
        <div className="pm-zone-tabs">
          {zones.map(z => {
            const C = CAT_OPTS.find(c => c.key === z.vehicle_category)?.Icon ?? ParkingCircle
            return (
              <button
                key={z.id}
                className={`pm-zone-tab${z.id === selId ? ' pm-zone-tab--active' : ''}`}
                onClick={() => setSelId(z.id)}
              >
                <C size={13} /> {z.name}
              </button>
            )
          })}
          {!loading && zones.length === 0 && (
            <span className="pm-zone-empty">No parking zones configured yet.</span>
          )}
        </div>

        {/* Main content */}
        {!selZone ? (
          <div className="pm-canvas-placeholder">
            <ParkingCircle size={36} />
            <span>{loading ? 'Loading…' : 'Select a parking zone.'}</span>
          </div>
        ) : (
          <div className="pm-content-row">
            <div className="pm-canvas-area" style={{ flex: 1, minWidth: 0 }}>

              {/* Toolbar — view-only summary */}
              <div className="pm-toolbar">
                <div className="pm-toolbar-left">
                  <div className="pm-summary">
                    <span className="pm-sum-free">{sumFr} free</span>
                    <span className="pm-sum-sep">·</span>
                    <span className="pm-sum-occ">{occ} occupied</span>
                    <span className="pm-sum-sep">·</span>
                    <span className="pm-sum-total">{liveSpaces.length} total</span>
                  </div>
                  {camRunning && (
                    <span className="pm-camera-badge">
                      <span className="pm-camera-dot" /> Camera active
                    </span>
                  )}
                </div>
              </div>

              {/* Canvas */}
              <div className="pm-canvas-wrapper">
                {camRunning && streamUrl ? (
                  <img
                    key={`stream-${selId}`}
                    src={streamUrl}
                    className="pm-canvas-img"
                    draggable={false}
                    alt="Camera stream"
                  />
                ) : selZone.reference_image_url ? (
                  <img
                    src={selZone.reference_image_url}
                    className="pm-canvas-img"
                    draggable={false}
                    alt=""
                  />
                ) : (
                  <div className="pm-canvas-no-img">No reference image for this zone.</div>
                )}

                {/* SVG overlay — read-only (no event handlers) */}
                <svg
                  className="pm-canvas-svg"
                  viewBox="0 0 1 1"
                  preserveAspectRatio="none"
                >
                  {liveSpaces.map(s => {
                    const x     = Math.min(s.x1, s.x2), y = Math.min(s.y1, s.y2)
                    const w     = Math.abs(s.x2 - s.x1), h = Math.abs(s.y2 - s.y1)
                    const color = s.is_occupied ? '#EF4444' : '#22C55E'
                    const fill  = s.is_occupied ? 'rgba(239,68,68,0.3)' : 'rgba(34,197,94,0.25)'
                    return (
                      <g key={s.id}>
                        <rect
                          x={x} y={y} width={w} height={h}
                          fill={fill} stroke={color} strokeWidth={0.003} rx={0.004}
                        />
                        <text
                          x={x + w / 2}
                          y={y + h / 2 - (s.is_occupied && s.occupied_by ? 0.013 : 0)}
                          textAnchor="middle" dominantBaseline="middle"
                          fill="#fff" fontSize={0.028} fontWeight="bold"
                          style={{ paintOrder: 'stroke', stroke: 'rgba(0,0,0,0.55)', strokeWidth: '0.005' }}
                        >
                          {s.space_number}
                        </text>
                        {s.is_occupied && s.occupied_by && (
                          <text
                            x={x + w / 2} y={y + h / 2 + 0.023}
                            textAnchor="middle" dominantBaseline="middle"
                            fill="#FECACA" fontSize={0.02} fontWeight="600"
                            style={{ paintOrder: 'stroke', stroke: 'rgba(0,0,0,0.5)', strokeWidth: '0.004' }}
                          >
                            {s.occupied_by}
                          </text>
                        )}
                      </g>
                    )
                  })}
                </svg>
              </div>

              {/* Legend */}
              <div className="pm-legend">
                <span className="pm-legend-item">
                  <span className="pm-legend-dot pm-legend-dot--free" />Free
                </span>
                <span className="pm-legend-item">
                  <span className="pm-legend-dot pm-legend-dot--occ" />Occupied
                </span>
                <span className="pm-legend-note">
                  {camRunning
                    ? 'IP camera active — occupancy updated automatically'
                    : 'Auto-refreshes every 8 s'}
                </span>
              </div>

            </div>{/* /pm-canvas-area */}

            {/* ── CCTV Camera Sidebar (view only) ── */}
            {showCamPanel && (
              <div className="pm-cam-sidebar">

                <div className="pm-cam-sidebar-header">
                  <span className="pm-cam-panel-title">
                    <Video size={14} /> {selZone.name}
                    {parkingCams.length > 0 && (
                      <span className="pm-cam-live-badge">
                        {parkingCams.filter(c => c.streamConnected).length}/{parkingCams.length} live
                      </span>
                    )}
                  </span>
                </div>

                {/* Main camera view */}
                <div className="pm-cam-main-wrap">
                  {parkingCams.length > 0 ? (
                    <>
                      {parkingCams.map((cam, idx) => (
                        <div
                          key={cam.id}
                          style={{
                            display: pkActiveCamId === cam.id ? 'block' : 'none',
                            width: '100%',
                            ...(idx === 0 ? {} : { position: 'absolute', inset: 0 }),
                          }}
                        >
                          <canvas
                            ref={el => registerPkCanvas(cam.id, el)}
                            style={{ width: '100%', display: 'block', background: '#000', minHeight: 180 }}
                          />
                        </div>
                      ))}
                      {pkActiveCam && !pkActiveCam.streamConnected && pkActiveCam.wsActive && (
                        <div className="pm-cam-overlay">
                          <div className="pm-spin-sm" />
                          <span>{pkActiveCam.statusMsg || 'Connecting…'}</span>
                        </div>
                      )}
                      <div className="pm-cam-main-badge">
                        <span style={{
                          width: 7, height: 7, borderRadius: '50%', flexShrink: 0,
                          background: pkActiveCam?.streamConnected ? '#22c55e'
                            : pkActiveCam?.wsActive ? '#f59e0b' : '#6b7280',
                        }} />
                        {pkActiveCam?.name || 'Camera'}
                      </div>
                    </>
                  ) : (
                    <div className="pm-cam-empty" style={{ minHeight: 160 }}>
                      <Video size={28} style={{ color: '#374151' }} />
                      <p>No cameras configured for this zone.</p>
                    </div>
                  )}
                </div>

                {/* Thumbnail strip — 2+ cameras */}
                {parkingCams.length > 1 && (
                  <div className="pm-cam-thumb-strip">
                    {parkingCams.map(cam => (
                      <div
                        key={`st-${cam.id}`}
                        className={`pm-cam-strip-thumb ${pkActiveCamId === cam.id ? 'active' : ''}`}
                        onClick={() => setPkActiveCam(cam.id)}
                        style={{ cursor: 'pointer' }}
                      >
                        <span
                          className="pm-cam-strip-dot"
                          style={{
                            background: cam.streamConnected ? '#22c55e'
                              : cam.wsActive ? '#f59e0b' : '#6b7280',
                          }}
                        />
                        <Wifi size={14} />
                        <span className="pm-cam-strip-label">{cam.name}</span>
                      </div>
                    ))}
                  </div>
                )}

              </div>
            )}

          </div>
        )}

      </div>
    </SecurityLayout>
  )
}
