import { useState, useEffect, useCallback } from 'react'
import {
  Camera, Plus, Pencil, Trash2, X, Eye, EyeOff,
  ShieldCheck, ParkingCircle, Video, Link2, AlertTriangle, RefreshCw,
  CheckCircle2, XCircle, Wifi,
} from 'lucide-react'
import { toast } from 'sonner'
import AdminLayout from '../../components/Layout/AdminLayout'
import { camerasApi } from '../../api/cameras'
import { testRtsp } from '../../api/scanning'
import './DeviceManagement.css'

// ── Helpers ──────────────────────────────────────────────────────────────────

function buildRtspUrl(ip, deviceId, password) {
  if (!ip || !deviceId || !password) return ''
  return `rtsp://${deviceId}:${password}@${ip}/stream1`
}

function AssignmentBadge({ value }) {
  if (value === 'entry') {
    return (
      <span className="dm-badge dm-badge-entry">
        <ShieldCheck size={11} /> Entry
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

function CameraModal({ mode, camera, nextName, onClose, onSaved }) {
  const isEdit = mode === 'edit'

  const [ip,          setIp]          = useState(camera?.ip         ?? '')
  const [deviceId,    setDeviceId]    = useState(camera?.device_id  ?? '')
  const [password,    setPassword]    = useState(camera?.password   ?? '')
  const [rtspUrl,     setRtspUrl]     = useState(camera?.rtsp_url   ?? '')
  const [assignment,  setAssignment]  = useState(camera?.assignment ?? 'entry')
  const [showPw,      setShowPw]      = useState(false)
  const [saving,      setSaving]      = useState(false)
  const [rtspTouched, setRtspTouched] = useState(false)
  const [testing,     setTesting]     = useState(false)
  const [testResult,  setTestResult]  = useState(null) // { ok, message } | null

  useEffect(() => {
    if (!rtspTouched) setRtspUrl(buildRtspUrl(ip, deviceId, password))
  }, [ip, deviceId, password, rtspTouched])

  const handleRtspChange = (v) => { setRtspTouched(true); setRtspUrl(v); setTestResult(null) }
  const handleAutoFill   = () => { setRtspTouched(false); setRtspUrl(buildRtspUrl(ip, deviceId, password)); setTestResult(null) }

  const handleTestRtsp = async () => {
    if (!rtspUrl.startsWith('rtsp://')) { toast.error('RTSP URL must start with rtsp://'); return }
    setTesting(true)
    setTestResult(null)
    try {
      const res = await testRtsp(rtspUrl)
      setTestResult(res.data)
    } catch {
      setTestResult({ ok: false, message: 'Could not reach the server to run the test.' })
    } finally {
      setTesting(false)
    }
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!ip.trim() || !deviceId.trim() || !password.trim() || !rtspUrl.trim()) {
      toast.error('Please fill in all fields.')
      return
    }
    if (!rtspUrl.startsWith('rtsp://')) {
      toast.error('RTSP URL must start with rtsp://')
      return
    }
    setSaving(true)
    try {
      const payload = { ip: ip.trim(), device_id: deviceId.trim(), password: password.trim(), rtsp_url: rtspUrl.trim(), assignment }
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
      toast.error(err?.response?.data?.detail || 'Failed to save camera.')
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
              </div>
              <div className="form-group">
                <label className="form-label">Device ID <span className="required">*</span></label>
                <input className="form-input" placeholder="e.g. 110384665" value={deviceId} onChange={(e) => setDeviceId(e.target.value)} required />
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

            <div className="form-group">
              <div className="dm-label-row">
                <label className="form-label">RTSP Stream URL <span className="required">*</span></label>
                <div className="dm-label-actions">
                  <button type="button" className="dm-link-btn" onClick={handleAutoFill}>
                    <Link2 size={12} /> Auto-fill
                  </button>
                  <button type="button" className="dm-link-btn" onClick={handleTestRtsp} disabled={testing || !rtspUrl}>
                    <Wifi size={12} /> {testing ? 'Testing…' : 'Test Connection'}
                  </button>
                </div>
              </div>
              <input
                className="form-input dm-input-mono"
                placeholder="rtsp://device_id:password@ip/stream1"
                value={rtspUrl}
                onChange={(e) => handleRtspChange(e.target.value)}
                required
              />
              {rtspTouched && !testResult && (
                <p className="dm-hint"><AlertTriangle size={11} /> Manually edited — click Auto-fill to regenerate</p>
              )}
              {testResult && (
                <p className={`dm-hint ${testResult.ok ? 'dm-hint-ok' : 'dm-hint-error'}`}>
                  {testResult.ok
                    ? <CheckCircle2 size={11} />
                    : <XCircle size={11} />}
                  {' '}{testResult.message}
                </p>
              )}
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
                onClick={() => setAssignment('parking')}
              >
                <ParkingCircle size={15} /> Parking Area
              </button>
            </div>
          </div>

          <div className="modal-footer">
            <button type="button" className="btn-outline" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn-primary" disabled={saving}>
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
  const [cameras,  setCameras]  = useState([])
  const [loading,  setLoading]  = useState(true)
  const [nextName, setNextName] = useState(null)
  const [modal,    setModal]    = useState(null)
  const [showPwId, setShowPwId] = useState(null)

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

        {/* Content */}
        <div className="section-container">
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
                    <th>Password</th>
                    <th>RTSP URL</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {cameras.map(cam => {
                    const masked = '•'.repeat(Math.min(cam.password.length, 10))
                    const showing = showPwId === cam.id
                    return (
                      <tr key={cam.id}>
                        <td className="dm-cam-name">
                          <Camera size={14} className="dm-cam-icon" />
                          {cam.name}
                        </td>
                        <td><AssignmentBadge value={cam.assignment} /></td>
                        <td className="token-link">{cam.ip}</td>
                        <td className="token-link">{cam.device_id}</td>
                        <td>
                          <span className="dm-pw-cell">
                            <span className="dm-pw-text">{showing ? cam.password : masked}</span>
                            <button className="dm-pw-toggle" onClick={() => setShowPwId(showing ? null : cam.id)}>
                              {showing ? <EyeOff size={12} /> : <Eye size={12} />}
                            </button>
                          </span>
                        </td>
                        <td className="dm-rtsp-cell">{cam.rtsp_url}</td>
                        <td>
                          <div className="dm-row-actions">
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

      </div>

      {/* Modals */}
      {modal?.type === 'add' && (
        <CameraModal mode="add" nextName={nextName} onClose={() => setModal(null)} onSaved={load} />
      )}
      {modal?.type === 'edit' && (
        <CameraModal mode="edit" camera={modal.camera} nextName={nextName} onClose={() => setModal(null)} onSaved={load} />
      )}
      {modal?.type === 'delete' && (
        <DeleteModal camera={modal.camera} onClose={() => setModal(null)} onDeleted={load} />
      )}
    </AdminLayout>
  )
}
