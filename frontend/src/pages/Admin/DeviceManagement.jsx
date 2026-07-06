import { useState, useEffect, useCallback } from 'react'
import { useLiveUpdates } from '../../realtime/useLiveUpdates'
import {
  Camera, Plus, Pencil, Trash2, X, Eye, EyeOff,
  ShieldCheck, ParkingCircle, RefreshCw, Wifi, WifiOff, Loader2, Video, Activity,
  ChevronUp, ChevronDown, ChevronLeft, ChevronRight, ZoomIn, ZoomOut, Home, Move,
} from 'lucide-react'
import { toast } from 'sonner'
import AdminLayout from '../../components/Layout/AdminLayout'
import { camerasApi } from '../../api/cameras'
import { getGates } from '../../api/scanning'
import { useCameraContext } from '../../context/CameraContext'
import './DeviceManagement.css'

// ── Helpers ──────────────────────────────────────────────────────────────────

function buildRtspUrl(ip, deviceId, password) {
  if (!ip || !deviceId || !password) return ''
  return `rtsp://${deviceId}:${password}@${ip}/stream1`
}

const GATE_LABELS = { gate1: 'Gate 1', gate4: 'Gate 4' }

function AssignmentBadge({ value, gateId }) {
  if (value === 'entry') {
    return (
      <span className="dm-badge dm-badge-entry">
        <ShieldCheck size={11} /> {gateId ? GATE_LABELS[gateId] ?? gateId : 'Entry'}
      </span>
    )
  }
  return (
    <span className="dm-badge dm-badge-parking">
      <ParkingCircle size={11} /> Parking
    </span>
  )
}

// ── Camera Form Modal ─────────────────────────────────────────────────────────

// Fallback until the dynamic gate list loads — gates are managed in System Settings
const GATE_OPTIONS = [
  { value: 'gate1', label: 'Gate 1' },
  { value: 'gate4', label: 'Gate 4' },
]

const IP_PATTERN = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/

function isValidIp(value) {
  const m = IP_PATTERN.exec(value)
  return !!m && m.slice(1).every(part => Number(part) <= 255)
}

// Extract a readable message from a DRF error payload ({field: [msg]} or {detail})
function apiErrorMessage(err, fallback) {
  const data = err?.response?.data
  if (data?.detail) return data.detail
  if (data && typeof data === 'object') {
    const first = Object.values(data).flat()[0]
    if (typeof first === 'string') return first
  }
  return fallback
}

function CameraModal({ mode, camera, cameras = [], nextName, onClose, onSaved }) {
  const isEdit = mode === 'edit'

  const [ip,         setIp]         = useState(camera?.ip         ?? '')
  const [deviceId,   setDeviceId]   = useState(camera?.device_id  ?? '')
  const [password,   setPassword]   = useState(camera?.password   ?? '')
  const [assignment, setAssignment] = useState(camera?.assignment ?? 'entry')
  const [gateId,     setGateId]     = useState(camera?.gate_id    ?? 'gate1')
  const [showPw,     setShowPw]     = useState(false)
  const [saving,     setSaving]     = useState(false)
  const [gateOptions, setGateOptions] = useState(GATE_OPTIONS)

  useEffect(() => {
    getGates()
      .then(({ data }) => {
        if (Array.isArray(data) && data.length > 0) {
          setGateOptions(data.map(g => ({ value: g.gate_id, label: g.label.split('—')[0].trim() })))
        }
      })
      .catch(() => {})
  }, [])

  // Live duplicate check — warns as soon as the IP/Device ID matches an existing camera
  const ipDupe = cameras.find(c => c.id !== camera?.id && ip.trim() && c.ip === ip.trim())
  const deviceIdDupe = cameras.find(c => c.id !== camera?.id && deviceId.trim() && c.device_id === deviceId.trim())

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!ip.trim() || !deviceId.trim() || !password.trim()) {
      toast.error('Please fill in all fields.')
      return
    }
    if (!isValidIp(ip.trim())) {
      toast.error('Enter a valid IP address (e.g. 192.168.137.86).')
      return
    }
    if (assignment === 'entry' && !gateId) {
      toast.error('Please select a gate for this entry camera.')
      return
    }
    // Instant duplicate check against the loaded list (backend re-validates)
    const others = cameras.filter(c => !isEdit || c.id !== camera.id)
    const ipDup  = others.find(c => (c.ip || '').trim() === ip.trim())
    if (ipDup) {
      toast.error(`${ipDup.name} already uses this IP address.`)
      return
    }
    const idDup = others.find(c => (c.device_id || '').trim() === deviceId.trim())
    if (idDup) {
      toast.error(`${idDup.name} already uses this Device ID.`)
      return
    }
    setSaving(true)
    try {
      const rtspUrl = buildRtspUrl(ip.trim(), deviceId.trim(), password.trim())
      const payload = {
        ip: ip.trim(),
        device_id: deviceId.trim(),
        password: password.trim(),
        rtsp_url: rtspUrl,
        assignment,
        gate_id: assignment === 'entry' ? gateId : null,
      }
      if (isEdit) {
        await camerasApi.update(camera.id, payload)
        toast.success(`${camera.name} updated.`)
      } else {
        await camerasApi.create(payload)
        toast.success('Camera added successfully.')
      }
      onSaved()
      onClose()
    } catch (err) {
      toast.error(apiErrorMessage(err, 'Failed to save camera.'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal-content">
        <div className="modal-header">
          <h2 className="modal-title">{isEdit ? `Edit ${camera.name}` : 'Add New Camera Device'}</h2>
          <button className="modal-close-btn" onClick={onClose}><X size={20} /></button>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            <p className="dm-section-label">Connection Details</p>

            {!isEdit && nextName && (
              <p className="dm-assign-note">
                Will be assigned name: <strong>{nextName.name}</strong>
              </p>
            )}

            <div className="dm-field-row">
              <div className="form-group">
                <label className="form-label">Camera IP <span className="required">*</span></label>
                <input className="form-input" placeholder="e.g. 192.168.137.86" value={ip} onChange={(e) => setIp(e.target.value)} required />
                {ipDupe && (
                  <p className="dm-hint dm-hint-error">Already used by "{ipDupe.name}".</p>
                )}
              </div>
              <div className="form-group">
                <label className="form-label">Device ID <span className="required">*</span></label>
                <input className="form-input" placeholder="e.g. 110384665" value={deviceId} onChange={(e) => setDeviceId(e.target.value)} required />
                {deviceIdDupe && (
                  <p className="dm-hint dm-hint-error">Already used by "{deviceIdDupe.name}".</p>
                )}
              </div>
            </div>

            <div className="form-group">
              <label className="form-label">Password <span className="required">*</span></label>
              <div className="dm-input-group">
                <input
                  className="form-input"
                  type={showPw ? 'text' : 'password'}
                  placeholder="Camera password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                />
                <button type="button" className="dm-pw-addon" onClick={() => setShowPw(p => !p)}>
                  {showPw ? <EyeOff size={15} /> : <Eye size={15} />}
                </button>
              </div>
            </div>

            <p className="dm-section-label" style={{ marginTop: '20px' }}>Assignment</p>
            <div className="dm-assignment-row">
              <button
                type="button"
                className={`dm-assign-btn ${assignment === 'entry' ? 'dm-assign-btn-active-entry' : ''}`}
                onClick={() => setAssignment('entry')}
              >
                <ShieldCheck size={15} /> Entry Gate
              </button>
              <button
                type="button"
                className={`dm-assign-btn ${assignment === 'parking' ? 'dm-assign-btn-active-parking' : ''}`}
                onClick={() => { setAssignment('parking'); setGateId(null) }}
              >
                <ParkingCircle size={15} /> Parking Area
              </button>
            </div>

            {assignment === 'entry' && (
              <div className="form-group" style={{ marginTop: '14px' }}>
                <label className="form-label">Gate <span className="required">*</span></label>
                <div className="dm-gate-row">
                  {gateOptions.map(({ value, label }) => (
                    <button
                      key={value}
                      type="button"
                      className={`dm-gate-btn ${gateId === value ? 'dm-gate-btn-active' : ''}`}
                      onClick={() => setGateId(value)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <p className="dm-gate-hint">Each gate should have its own dedicated camera.</p>
              </div>
            )}
          </div>

          <div className="modal-footer">
            <button type="button" className="btn-outline" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn-primary" disabled={saving || !!ipDupe || !!deviceIdDupe}>
              {saving ? 'Saving…' : isEdit ? 'Save Changes' : 'Add Device'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ── Delete Confirm Modal ──────────────────────────────────────────────────────

function DeleteModal({ camera, onClose, onDeleted }) {
  const [deleting, setDeleting] = useState(false)

  const handleDelete = async () => {
    setDeleting(true)
    try {
      await camerasApi.remove(camera.id)
      toast.success(`${camera.name} removed.`)
      onDeleted()
      onClose()
    } catch {
      toast.error('Failed to remove camera.')
      setDeleting(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal-content modal-sm">
        <div className="modal-header">
          <h2 className="modal-title">Remove Device</h2>
          <button className="modal-close-btn" onClick={onClose}><X size={20} /></button>
        </div>
        <div className="modal-body">
          <p className="dm-delete-text">
            Are you sure you want to remove <strong>{camera.name}</strong>?
            It will be disconnected from all pages and the name slot will be reused for the next camera added.
          </p>
        </div>
        <div className="modal-footer">
          <button className="btn-outline" onClick={onClose}>Cancel</button>
          <button className="btn-danger" onClick={handleDelete} disabled={deleting}>
            {deleting ? 'Removing…' : 'Remove Device'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function DeviceManagement() {
  const [cameras,    setCameras]    = useState([])
  const [loading,    setLoading]    = useState(true)
  const [nextName,   setNextName]   = useState(null)
  const [modal,      setModal]      = useState(null)
  const [pingStates, setPingStates] = useState({})
  const [ptzActive,  setPtzActive]  = useState({})

  const {
    cameras:        streamCams,
    addCamera:      connectCamera,
    removeCamera:   disconnectCamera,
    disconnectAll,
    registerCanvas,
  } = useCameraContext()

  // Match a DB camera to its stream instance by name
  const getStreamCam    = (dbCam) => streamCams.find(c => c.name === dbCam.name)
  const isConnected     = (dbCam) => !!getStreamCam(dbCam)

  const handleConnect    = (cam) => connectCamera(cam.name, cam.rtsp_url, cam.assignment)
  const handleDisconnect = (cam) => {
    const sc = getStreamCam(cam)
    if (sc) disconnectCamera(sc.id)
  }

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [cams, next] = await Promise.all([camerasApi.list(), camerasApi.nextName()])
      setCameras(cams)
      setNextName(next)
    } catch {
      toast.error('Failed to load cameras.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  // Live-refresh device list on camera changes
  useLiveUpdates(load, ['camera'])

  // Camera CRUD is audited server-side (CameraViewSet) — no client-side audit posts.

  const handlePing = async (cam) => {
    setPingStates(p => ({ ...p, [cam.id]: 'testing' }))
    try {
      const result = await camerasApi.ping(cam.id)
      setPingStates(p => ({ ...p, [cam.id]: result.reachable ? 'ok' : 'fail' }))
    } catch {
      setPingStates(p => ({ ...p, [cam.id]: 'fail' }))
    }
    setTimeout(() => setPingStates(p => ({ ...p, [cam.id]: undefined })), 4000)
  }

  const handlePtzStart = useCallback(async (sc, command) => {
    const dbCam = cameras.find(c => c.name === sc.name)
    if (!dbCam) return
    try {
      await camerasApi.ptz(dbCam.id, command, 0.5)
    } catch (err) {
      toast.error(err?.response?.data?.error || 'PTZ not supported by this camera')
    }
  }, [cameras])

  const handlePtzStop = useCallback(async (sc) => {
    const dbCam = cameras.find(c => c.name === sc.name)
    if (!dbCam) return
    camerasApi.ptz(dbCam.id, 'stop', 0).catch(() => {})
  }, [cameras])

  const entryCams   = cameras.filter(c => c.assignment === 'entry')
  const parkingCams = cameras.filter(c => c.assignment === 'parking')

  return (
    <AdminLayout>
      <div className="device-management-page">

        {/* Page Header */}
        <div className="page-header">
          <div>
            <h1 className="page-title">Device Management</h1>
            <p className="page-subtitle">Manage IP cameras for entry and parking monitoring.</p>
          </div>
          <div className="page-header-actions">
            <button className="btn-outline btn-sm" onClick={load} disabled={loading}>
              <RefreshCw size={14} className={loading ? 'dm-spin' : ''} />
              Refresh
            </button>
            <button className="btn-primary" onClick={() => setModal({ type: 'add' })}>
              <Plus size={16} /> Add Device
            </button>
          </div>
        </div>

        {/* Stats */}
        <div className="dm-stats-row">
          <div className="dm-stat-card">
            <span className="dm-stat-value">{cameras.length}</span>
            <span className="dm-stat-label">Total Cameras</span>
          </div>
          <div className="dm-stat-card dm-stat-entry">
            <span className="dm-stat-value">{entryCams.length}</span>
            <span className="dm-stat-label"><ShieldCheck size={13} /> Entry</span>
          </div>
          <div className="dm-stat-card dm-stat-parking">
            <span className="dm-stat-value">{parkingCams.length}</span>
            <span className="dm-stat-label"><ParkingCircle size={13} /> Parking</span>
          </div>
        </div>

        {/* Cameras Table */}
        <div className="section-container" style={{ marginBottom: 24 }}>
          <div className="section-header">
            <h2 className="section-title">Cameras</h2>
          </div>

          {loading ? (
            <div className="dm-loading-row">
              <div className="dm-spinner-sm" />
              <span>Loading devices…</span>
            </div>
          ) : cameras.length === 0 ? (
            <div className="dm-empty">
              <Camera size={36} className="dm-empty-icon" />
              <p className="dm-empty-title">No cameras configured</p>
              <p className="dm-empty-sub">Add your first IP camera to start monitoring entry and parking areas.</p>
              <button className="btn-primary" onClick={() => setModal({ type: 'add' })}>
                <Plus size={15} /> Add First Device
              </button>
            </div>
          ) : (
            <div className="table-container">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Assignment</th>
                    <th>IP Address</th>
                    <th>Device ID</th>
                    <th>Connection</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {cameras.map(cam => {
                    const sc = getStreamCam(cam)
                    const connected = !!sc
                    const live = sc?.streamConnected
                    const connecting = connected && !live && sc?.wsActive

                    return (
                      <tr key={cam.id}>
                        <td className="dm-cam-name">
                          <Camera size={14} className="dm-cam-icon" />
                          {cam.name}
                        </td>
                        <td><AssignmentBadge value={cam.assignment} gateId={cam.gate_id} /></td>
                        <td className="token-link">{cam.ip}</td>
                        <td className="token-link">{cam.device_id}</td>
                        <td>
                          {!connected ? (
                            <button className="dm-conn-btn dm-conn-btn-connect" onClick={() => handleConnect(cam)}>
                              <Wifi size={12} /> Connect
                            </button>
                          ) : (
                            <div className="dm-conn-active">
                              <span className="dm-conn-status">
                                <span className={`dm-conn-dot ${live ? 'live' : 'connecting'}`} />
                                {connecting
                                  ? <><Loader2 size={11} className="dm-spin" /> Connecting…</>
                                  : 'Live'
                                }
                              </span>
                              <button className="dm-conn-btn dm-conn-btn-disconnect" onClick={() => handleDisconnect(cam)}>
                                <WifiOff size={12} /> Disconnect
                              </button>
                            </div>
                          )}
                        </td>
                        <td>
                          <div className="dm-row-actions">
                            {(() => {
                              const ps = pingStates[cam.id]
                              return (
                                <button
                                  className={`dm-ping-btn${ps === 'ok' ? ' dm-ping-ok' : ps === 'fail' ? ' dm-ping-fail' : ''}`}
                                  title={ps === 'ok' ? 'Reachable' : ps === 'fail' ? 'Unreachable' : 'Test Connection'}
                                  onClick={() => handlePing(cam)}
                                  disabled={ps === 'testing'}
                                >
                                  {ps === 'testing'
                                    ? <Loader2 size={15} className="dm-spin" />
                                    : ps === 'ok'
                                    ? <Wifi size={15} />
                                    : ps === 'fail'
                                    ? <WifiOff size={15} />
                                    : <Activity size={15} />}
                                </button>
                              )
                            })()}
                            <button className="view-btn" title="Edit" onClick={() => setModal({ type: 'edit', camera: cam })}>
                              <Pencil size={15} />
                            </button>
                            <button className="delete-btn" title="Remove" onClick={() => setModal({ type: 'delete', camera: cam })}>
                              <Trash2 size={15} />
                            </button>
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Live Feeds */}
        <div className="section-container" style={{ marginTop: 24 }}>
          <div className="section-header">
            <h2 className="section-title">Live Feeds</h2>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              {streamCams.length > 0 && (
                <span className="dm-feeds-live-count">
                  {streamCams.filter(c => c.streamConnected).length}/{streamCams.length} live
                </span>
              )}
              {streamCams.length > 0 && (
                <button className="btn-outline btn-sm" onClick={disconnectAll}>
                  <WifiOff size={13} /> Disconnect All
                </button>
              )}
            </div>
          </div>

          {streamCams.length === 0 ? (
            <div className="dm-feeds-empty">
              <Video size={32} className="dm-feeds-empty-icon" />
              <p className="dm-feeds-empty-title">No active feeds</p>
              <p className="dm-feeds-empty-sub">Click Connect on a camera above to view its live RTSP feed here.</p>
            </div>
          ) : (
            <div className={`dm-feeds-grid${streamCams.length === 1 ? ' dm-feeds-grid-single' : ''}`}>
              {streamCams.map(sc => {
                const dbCam = cameras.find(c => c.name === sc.name)
                const dotCls = sc.streamConnected ? 'live' : sc.wsActive ? 'connecting' : 'offline'
                return (
                  <div key={sc.id} className="dm-feed-card">
                    <div className="dm-feed-header">
                      <span className="dm-feed-name">
                        <span className={`dm-feed-dot ${dotCls}`} />
                        {sc.name}
                      </span>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        {dbCam && <AssignmentBadge value={dbCam.assignment} gateId={dbCam?.gate_id} />}
                        <button
                          className={`dm-feed-ptz-toggle${ptzActive[sc.id] ? ' dm-feed-ptz-toggle--on' : ''}`}
                          onClick={() => setPtzActive(p => ({ ...p, [sc.id]: !p[sc.id] }))}
                          title="PTZ Controls"
                        >
                          <Move size={13} />
                        </button>
                        <button
                          className="dm-feed-disconnect"
                          onClick={() => disconnectCamera(sc.id)}
                          title="Disconnect"
                        >
                          <WifiOff size={13} />
                        </button>
                      </div>
                    </div>
                    <div className="dm-feed-viewport">
                      <canvas
                        ref={el => registerCanvas(sc.id, el)}
                        className="dm-feed-canvas"
                      />
                      {!sc.streamConnected && (
                        <div className="dm-feed-overlay">
                          {sc.wsActive ? (
                            <>
                              <Loader2 size={28} className="dm-spin" style={{ color: '#60a5fa' }} />
                              <span>{sc.statusMsg || 'Connecting…'}</span>
                            </>
                          ) : (
                            <>
                              <WifiOff size={28} style={{ color: '#374151' }} />
                              <span>Disconnected</span>
                            </>
                          )}
                        </div>
                      )}
                      {ptzActive[sc.id] && (
                        <div className="dm-ptz-overlay">
                          <div className="dm-ptz-panel">
                            <span className="dm-ptz-label">PTZ</span>
                            <div className="dm-ptz-dpad">
                              <button className="dm-ptz-btn dm-ptz-up"
                                onPointerDown={e => { e.currentTarget.setPointerCapture(e.pointerId); handlePtzStart(sc, 'up') }}
                                onPointerUp={() => handlePtzStop(sc)} onPointerCancel={() => handlePtzStop(sc)}>
                                <ChevronUp size={15} />
                              </button>
                              <button className="dm-ptz-btn dm-ptz-left"
                                onPointerDown={e => { e.currentTarget.setPointerCapture(e.pointerId); handlePtzStart(sc, 'left') }}
                                onPointerUp={() => handlePtzStop(sc)} onPointerCancel={() => handlePtzStop(sc)}>
                                <ChevronLeft size={15} />
                              </button>
                              <button className="dm-ptz-btn dm-ptz-center" onClick={() => handlePtzStart(sc, 'home')} title="Go Home">
                                <Home size={12} />
                              </button>
                              <button className="dm-ptz-btn dm-ptz-right"
                                onPointerDown={e => { e.currentTarget.setPointerCapture(e.pointerId); handlePtzStart(sc, 'right') }}
                                onPointerUp={() => handlePtzStop(sc)} onPointerCancel={() => handlePtzStop(sc)}>
                                <ChevronRight size={15} />
                              </button>
                              <button className="dm-ptz-btn dm-ptz-down"
                                onPointerDown={e => { e.currentTarget.setPointerCapture(e.pointerId); handlePtzStart(sc, 'down') }}
                                onPointerUp={() => handlePtzStop(sc)} onPointerCancel={() => handlePtzStop(sc)}>
                                <ChevronDown size={15} />
                              </button>
                            </div>
                            <div className="dm-ptz-zoom">
                              <button className="dm-ptz-zoom-btn" title="Zoom In"
                                onPointerDown={e => { e.currentTarget.setPointerCapture(e.pointerId); handlePtzStart(sc, 'zoom_in') }}
                                onPointerUp={() => handlePtzStop(sc)} onPointerCancel={() => handlePtzStop(sc)}>
                                <ZoomIn size={13} />
                              </button>
                              <button className="dm-ptz-zoom-btn" title="Zoom Out"
                                onPointerDown={e => { e.currentTarget.setPointerCapture(e.pointerId); handlePtzStart(sc, 'zoom_out') }}
                                onPointerUp={() => handlePtzStop(sc)} onPointerCancel={() => handlePtzStop(sc)}>
                                <ZoomOut size={13} />
                              </button>
                            </div>
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>

      </div>

      {/* Modals */}
      {modal?.type === 'add' && (
        <CameraModal mode="add" cameras={cameras} nextName={nextName} onClose={() => setModal(null)} onSaved={load} />
      )}
      {modal?.type === 'edit' && (
        <CameraModal mode="edit" camera={modal.camera} cameras={cameras} nextName={nextName} onClose={() => setModal(null)} onSaved={load} />
      )}
      {modal?.type === 'delete' && (
        <DeleteModal
          camera={modal.camera}
          onClose={() => setModal(null)}
          onDeleted={() => {
            // Also tear down the live stream connection so no ghost feed lingers
            const sc = getStreamCam(modal.camera)
            if (sc) disconnectCamera(sc.id)
            load()
          }}
        />
      )}
    </AdminLayout>
  )
}
