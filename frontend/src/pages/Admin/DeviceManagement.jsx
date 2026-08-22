import { useState, useEffect, useCallback } from 'react'
import { useLiveUpdates } from '../../realtime/useLiveUpdates'
import {
  Camera, Plus, Pencil, Trash2, X, Eye, EyeOff,
  ShieldCheck, ParkingCircle, RefreshCw, Wifi, WifiOff, Loader2, Video, Activity,
  ChevronUp, ChevronDown, ChevronLeft, ChevronRight, ZoomIn, ZoomOut, Home, Move,
} from 'lucide-react'
import { toast } from '../../components/Feedback/notify'
import { camerasApi } from '../../api/cameras'
import { useCameraContext } from '../../context/CameraContext'
import { useGates } from '../../hooks/useGates'
import './DeviceManagement.css'

// ── Helpers ──────────────────────────────────────────────────────────────────

// Channel number encoded in a saved stream URL, so the edit modal opens on the
// right one. Dahua puts it in a query string, Hikvision folds it into the
// channel number (201 = channel 2), others use /chNN/ or a trailing digit.
function channelOf(rtspUrl) {
  if (!rtspUrl) return 1
  const dahua = /[?&]channel=(\d+)/.exec(rtspUrl)
  if (dahua) return Number(dahua[1])
  const hik = /\/Streaming\/Channels\/(\d)0\d/.exec(rtspUrl)
  if (hik) return Number(hik[1])
  const ch = /\/ch(\d+)\//.exec(rtspUrl)
  if (ch) return Number(ch[1])
  return 1
}

function AssignmentBadge({ value, gateId }) {
  const { gateLabel } = useGates()
  if (value === 'entry') {
    return (
      <span className="dm-badge dm-badge-entry">
        <ShieldCheck size={11} /> {gateId ? gateLabel(gateId) : 'Entry'}
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
  // IMOU/Dahua units refuse RTSP without it. Optional, so a genuinely open
  // camera can still be added without inventing a credential.
  const [password,   setPassword]   = useState(camera?.password   ?? '')
  // One IP can host several cameras (an NVR, or a multi-lens unit); the channel
  // number is what tells them apart inside the RTSP path.
  const [channel,    setChannel]    = useState(String(channelOf(camera?.rtsp_url) || 1))
  const [showPw,     setShowPw]     = useState(false)
  // The vendor picker is gone — the backend probes the camera and finds the
  // stream path itself. These only come into play when that fails, or when a
  // camera was already saved with a URL no template produces.
  const [detecting,      setDetecting]      = useState(false)
  const [detectError,    setDetectError]    = useState('')
  const [detectedFormat, setDetectedFormat] = useState('')
  // Set when probing fails. The next submit saves with this URL, so a camera
  // the probe cannot identify can still be registered — without ever asking
  // the admin to hand-write an RTSP URL.
  const [fallbackUrl,    setFallbackUrl]    = useState('')
  // Every URL the backend tried and what the camera answered. Hidden by
  // default, but a camera that plainly works and still will not detect is
  // undiagnosable without it.
  const [detectAttempts, setDetectAttempts] = useState([])
  const { gates, gateLabel } = useGates()
  const [assignment, setAssignment] = useState(camera?.assignment ?? 'entry')
  const [pickedGate, setPickedGate] = useState(camera?.gate_id ?? '')
  const [saving,     setSaving]     = useState(false)

  const gateOptions = gates.map(g => ({ value: g.gate_id, label: gateLabel(g.gate_id) }))
  // A new camera defaults to the first gate — derived, not a hardcoded 'gate1',
  // which a school that renamed or retired its founding gates would not have.
  const gateId = pickedGate || gates[0]?.gate_id || ''

  // Another camera on the same IP is normal for an NVR, so this is a note, not
  // a block. Only a duplicate *stream* is rejected, and the backend does that.
  const sameDevice = cameras.filter(c => c.id !== camera?.id && ip.trim() && c.ip === ip.trim())
  const ipDupe = null
  const deviceIdDupe = null

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
    // No IP/Device-ID duplicate check: several cameras legitimately share one
    // device. The backend rejects a duplicate stream URL, which is the real
    // identity of a camera.
    setSaving(true)
    try {
      // Ask the camera which stream path it answers on. The admin is never
      // asked for an RTSP URL: if probing fails, the first submit reports it
      // and the second saves with the best-guess URL.
      let rtspUrl
      if (fallbackUrl) {
        rtspUrl = fallbackUrl          // second press: save despite the failure
      } else {
        setDetecting(true)
        try {
          const found = await camerasApi.detectRtsp({
            ip: ip.trim(), device_id: deviceId.trim(), password: password.trim(),
            channel: Number(channel) || 1,
          })
          rtspUrl = found.rtsp_url
          setDetectedFormat(found.format)
        } catch (err) {
          const data = err?.response?.data
          setDetectError(data?.error || 'Could not detect the camera stream.')
          // Keep the camera's existing URL when editing; otherwise fall back to
          // the backend's best guess so the next press can still save.
          setFallbackUrl(data?.suggestion || camera?.rtsp_url || '')
          setDetectAttempts(Array.isArray(data?.attempts) ? data.attempts : [])
          toast.error('Could not detect the stream.')
          return
        } finally {
          setDetecting(false)
        }
      }
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
                <input className="form-input" placeholder="e.g. 192.168.137.86" value={ip} onChange={(e) => { setIp(e.target.value); setDetectError(''); setFallbackUrl('') }} required />
                {ipDupe && (
                  <p className="dm-hint dm-hint-error">Already used by "{ipDupe.name}".</p>
                )}
              </div>
              <div className="form-group">
                <label className="form-label">Device ID <span className="required">*</span></label>
                <input className="form-input" placeholder="e.g. 110384665" value={deviceId} onChange={(e) => { setDeviceId(e.target.value); setDetectError(''); setFallbackUrl('') }} required />
                {deviceIdDupe && (
                  <p className="dm-hint dm-hint-error">Already used by "{deviceIdDupe.name}".</p>
                )}
              </div>
            </div>

            <div className="form-group">
              <label className="form-label">Channel</label>
              <input
                className="form-input"
                type="number"
                min="1"
                max="32"
                value={channel}
                onChange={(e) => { setChannel(e.target.value); setDetectError(''); setFallbackUrl('') }}
              />
              <p className="dm-gate-hint">
                1 for a normal camera. Use 2, 3… for extra cameras on the same
                device (an NVR or multi-lens unit).
                {sameDevice.length > 0 && (
                  <> This IP already has {sameDevice.length} camera
                  {sameDevice.length > 1 ? 's' : ''}: {sameDevice.map(c => c.name).join(', ')}.</>
                )}
              </p>
            </div>

            <div className="form-group">
              <label className="form-label">Password <span className="required">*</span></label>
              <div className="dm-input-group">
                <input
                  className="form-input"
                  type={showPw ? 'text' : 'password'}
                  placeholder="Camera password"
                  value={password}
                  onChange={(e) => { setPassword(e.target.value); setDetectError(''); setFallbackUrl('') }}
                  required
                />
                <button type="button" className="dm-pw-addon" onClick={() => setShowPw(p => !p)}>
                  {showPw ? <EyeOff size={15} /> : <Eye size={15} />}
                </button>
              </div>
            </div>

            {/* No vendor picker. Which firmware a camera runs is not something
                the person mounting it should have to know, so the backend probes
                the device and finds the working stream path itself. When that
                fails the submit button becomes "Add Anyway" — telling someone to
                press the same button a second time reads as "it did not work". */}
            {detectError ? (
              <div style={{ marginTop: '20px' }}>
                <p className="dm-gate-hint dm-detect-error">{detectError}</p>
                <p className="dm-gate-hint">
                  Correct the details above and retry, or register it anyway with
                  the most likely stream address — you can edit it later.
                </p>
                {detectAttempts.length > 0 && (
                  <details className="dm-detect-attempts">
                    <summary>What was tried ({detectAttempts.length})</summary>
                    <ul>
                      {detectAttempts.map((a, i) => <li key={i}>{a}</li>)}
                    </ul>
                    <p className="dm-detect-legend">
                      401 = login refused · 404 = no such stream path · 200 = accepted
                    </p>
                  </details>
                )}
                <button
                  type="button"
                  className="dm-detect-retry"
                  onClick={() => { setDetectError(''); setFallbackUrl(''); setDetectAttempts([]) }}
                >
                  Try auto-detect again
                </button>
              </div>
            ) : (
              <p className="dm-gate-hint" style={{ marginTop: '20px' }}>
                {detecting
                  ? 'Contacting the camera to find its stream…'
                  : detectedFormat
                    ? `Stream detected (${detectedFormat} format).`
                    : 'The stream URL is detected automatically when you save.'}
              </p>
            )}

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
                onClick={() => { setAssignment('parking'); setPickedGate('') }}
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
                      onClick={() => setPickedGate(value)}
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
              {saving
                ? (detecting ? 'Detecting…' : 'Saving…')
                : fallbackUrl
                  ? (isEdit ? 'Save Anyway' : 'Add Anyway')
                  : (isEdit ? 'Save Changes' : 'Add Device')}
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
    paneCounts,
  } = useCameraContext()

  // Viewports on screen, which is not the number of cameras: a dual-lens unit
  // contributes two. The grid drops to one column only for a genuinely single
  // tile, so a split camera still gets its two views side by side.
  const feedTileCount = streamCams.reduce((n, sc) => n + (paneCounts[sc.id] ?? 1), 0)

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
    <>
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
            <div className={`dm-feeds-grid${feedTileCount === 1 ? ' dm-feeds-grid-single' : ''}`}>
              {streamCams.flatMap(sc => {
                const dbCam = cameras.find(c => c.name === sc.name)
                const dotCls = sc.streamConnected ? 'live' : sc.wsActive ? 'connecting' : 'offline'
                // A dual-lens camera sends both views inside one frame, so it
                // gets one viewport per view. The controls below act on the
                // device, not the view, so they stay on the first tile only —
                // two Disconnect buttons for one camera would just be a trap.
                const lenses = paneCounts[sc.id] ?? 1
                return Array.from({ length: lenses }, (_, pane) => (
                  <div key={`${sc.id}:${pane}`} className="dm-feed-card">
                    <div className="dm-feed-header">
                      <span className="dm-feed-name">
                        <span className={`dm-feed-dot ${dotCls}`} />
                        {sc.name}
                        {lenses > 1 && <span className="dm-feed-lens">Lens {pane + 1}</span>}
                      </span>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        {dbCam && <AssignmentBadge value={dbCam.assignment} gateId={dbCam?.gate_id} />}
                        {pane === 0 && (
                          <>
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
                          </>
                        )}
                      </div>
                    </div>
                    <div className="dm-feed-viewport">
                      <canvas
                        ref={el => registerCanvas(sc.id, el, pane)}
                        className="dm-feed-canvas"
                      />
                      {!sc.streamConnected && (
                        <div className="dm-feed-overlay">
                          {sc.wsActive ? (
                            <>
                              <Loader2 size={28} className="dm-spin" style={{ color: '#5CA9DC' }} />
                              <span>{sc.statusMsg || 'Connecting…'}</span>
                            </>
                          ) : (
                            <>
                              <WifiOff size={28} style={{ color: '#2E4C63' }} />
                              <span>Disconnected</span>
                            </>
                          )}
                        </div>
                      )}
                      {pane === 0 && ptzActive[sc.id] && (
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
                ))
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
    </>
  )
}
