import { useState, useEffect, useCallback } from 'react'
import {
  Camera, Plus, Pencil, Trash2, X, Eye, EyeOff, Wifi, WifiOff,
  ShieldCheck, ParkingCircle, Video, Link2, AlertTriangle, RefreshCw,
} from 'lucide-react'
import { toast } from 'sonner'
import AdminLayout from '../../components/Layout/AdminLayout'
import { camerasApi } from '../../api/cameras'
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

  const [ip,         setIp]         = useState(camera?.ip         ?? '')
  const [deviceId,   setDeviceId]   = useState(camera?.device_id  ?? '')
  const [password,   setPassword]   = useState(camera?.password   ?? '')
  const [rtspUrl,    setRtspUrl]    = useState(camera?.rtsp_url   ?? '')
  const [assignment, setAssignment] = useState(camera?.assignment ?? 'entry')
  const [showPw,     setShowPw]     = useState(false)
  const [saving,     setSaving]     = useState(false)
  const [rtspTouched, setRtspTouched] = useState(false)

  // Auto-fill RTSP URL when connection fields change (unless user manually edited it)
  useEffect(() => {
    if (!rtspTouched) {
      setRtspUrl(buildRtspUrl(ip, deviceId, password))
    }
  }, [ip, deviceId, password, rtspTouched])

  const handleRtspChange = (v) => {
    setRtspTouched(true)
    setRtspUrl(v)
  }

  const handleAutoFill = () => {
    setRtspTouched(false)
    setRtspUrl(buildRtspUrl(ip, deviceId, password))
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
      const payload = {
        ip:         ip.trim(),
        device_id:  deviceId.trim(),
        password:   password.trim(),
        rtsp_url:   rtspUrl.trim(),
        assignment,
      }
      if (isEdit) {
        await camerasApi.update(camera.id, payload)
        toast.success(`${camera.name} updated.`)
      } else {
        await camerasApi.create(payload)
        toast.success(`Camera added successfully.`)
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
    <div className="dm-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dm-modal">
        {/* Header */}
        <div className="dm-modal-head">
          <div className="dm-modal-head-left">
            <div className="dm-modal-icon">
              <Video size={18} />
            </div>
            <div>
              <h2 className="dm-modal-title">{isEdit ? `Edit ${camera.name}` : 'Add New Camera Device'}</h2>
              {!isEdit && (
                <p className="dm-modal-subtitle">Will be assigned: <strong>{nextName?.name ?? '…'}</strong></p>
              )}
            </div>
          </div>
          <button className="dm-modal-close" onClick={onClose}><X size={16} /></button>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="dm-modal-body">

            {/* Connection Details */}
            <p className="dm-section-label">Connection Details</p>

            <div className="dm-field-row">
              <div className="dm-field">
                <label className="dm-label">Camera IP</label>
                <input
                  className="dm-input"
                  placeholder="e.g. 192.168.137.86"
                  value={ip}
                  onChange={(e) => setIp(e.target.value)}
                  required
                />
              </div>
              <div className="dm-field">
                <label className="dm-label">Device ID</label>
                <input
                  className="dm-input"
                  placeholder="e.g. 110384665"
                  value={deviceId}
                  onChange={(e) => setDeviceId(e.target.value)}
                  required
                />
              </div>
            </div>

            <div className="dm-field">
              <label className="dm-label">Password</label>
              <div className="dm-input-group">
                <input
                  className="dm-input"
                  type={showPw ? 'text' : 'password'}
                  placeholder="Camera password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                />
                <button type="button" className="dm-input-addon" onClick={() => setShowPw(p => !p)}>
                  {showPw ? <EyeOff size={15} /> : <Eye size={15} />}
                </button>
              </div>
            </div>

            <div className="dm-field">
              <div className="dm-label-row">
                <label className="dm-label">RTSP Stream URL</label>
                <button type="button" className="dm-link-btn" onClick={handleAutoFill}>
                  <Link2 size={12} /> Auto-fill
                </button>
              </div>
              <input
                className="dm-input dm-input-mono"
                placeholder="rtsp://device_id:password@ip/stream1"
                value={rtspUrl}
                onChange={(e) => handleRtspChange(e.target.value)}
                required
              />
              {rtspTouched && (
                <p className="dm-hint"><AlertTriangle size={11} /> Manually edited — click Auto-fill to regenerate</p>
              )}
            </div>

            {/* Assignment */}
            <p className="dm-section-label" style={{ marginTop: '20px' }}>Assignment</p>
            <div className="dm-assignment-row">
              <button
                type="button"
                className={`dm-assign-btn ${assignment === 'entry' ? 'active-entry' : ''}`}
                onClick={() => setAssignment('entry')}
              >
                <ShieldCheck size={16} />
                Entry Gate
              </button>
              <button
                type="button"
                className={`dm-assign-btn ${assignment === 'parking' ? 'active-parking' : ''}`}
                onClick={() => setAssignment('parking')}
              >
                <ParkingCircle size={16} />
                Parking Area
              </button>
            </div>
          </div>

          <div className="dm-modal-foot">
            <button type="button" className="dm-btn dm-btn-ghost" onClick={onClose}>Cancel</button>
            <button type="submit" className="dm-btn dm-btn-primary" disabled={saving}>
              {saving ? <><span className="dm-spinner" /> Saving…</> : isEdit ? 'Save Changes' : 'Add Device'}
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
    <div className="dm-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dm-modal dm-modal-sm">
        <div className="dm-modal-head">
          <div className="dm-modal-head-left">
            <div className="dm-modal-icon dm-modal-icon-danger">
              <Trash2 size={18} />
            </div>
            <div>
              <h2 className="dm-modal-title">Remove Device</h2>
              <p className="dm-modal-subtitle">This action cannot be undone</p>
            </div>
          </div>
          <button className="dm-modal-close" onClick={onClose}><X size={16} /></button>
        </div>
        <div className="dm-modal-body">
          <p className="dm-delete-text">
            Are you sure you want to remove <strong>{camera.name}</strong>?
            It will be disconnected from all pages and the name slot will be reused for the next camera added.
          </p>
        </div>
        <div className="dm-modal-foot">
          <button className="dm-btn dm-btn-ghost" onClick={onClose}>Cancel</button>
          <button className="dm-btn dm-btn-danger" onClick={handleDelete} disabled={deleting}>
            {deleting ? <><span className="dm-spinner" /> Removing…</> : 'Remove Device'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Camera Card ───────────────────────────────────────────────────────────────

function CameraCard({ camera, onEdit, onDelete }) {
  const [showPw, setShowPw] = useState(false)
  const masked = '•'.repeat(Math.min(camera.password.length, 10))

  return (
    <div className="dm-card">
      <div className="dm-card-header">
        <div className="dm-card-icon">
          <Camera size={20} />
        </div>
        <div className="dm-card-title-block">
          <h3 className="dm-card-name">{camera.name}</h3>
          <AssignmentBadge value={camera.assignment} />
        </div>
        <div className="dm-card-actions">
          <button className="dm-icon-btn dm-icon-edit" onClick={() => onEdit(camera)} title="Edit">
            <Pencil size={14} />
          </button>
          <button className="dm-icon-btn dm-icon-delete" onClick={() => onDelete(camera)} title="Remove">
            <Trash2 size={14} />
          </button>
        </div>
      </div>

      <div className="dm-card-body">
        <div className="dm-detail-row">
          <span className="dm-detail-label">IP</span>
          <span className="dm-detail-value dm-mono">{camera.ip}</span>
        </div>
        <div className="dm-detail-row">
          <span className="dm-detail-label">Device ID</span>
          <span className="dm-detail-value dm-mono">{camera.device_id}</span>
        </div>
        <div className="dm-detail-row">
          <span className="dm-detail-label">Password</span>
          <span className="dm-detail-value dm-mono dm-pw-row">
            {showPw ? camera.password : masked}
            <button className="dm-pw-toggle" onClick={() => setShowPw(p => !p)}>
              {showPw ? <EyeOff size={12} /> : <Eye size={12} />}
            </button>
          </span>
        </div>
        <div className="dm-detail-row dm-detail-rtsp">
          <span className="dm-detail-label">RTSP</span>
          <span className="dm-detail-value dm-mono dm-rtsp">{camera.rtsp_url}</span>
        </div>
      </div>

      <div className="dm-card-footer">
        <span className="dm-card-status">
          <span className="dm-status-dot" />
          Ready
        </span>
        <span className="dm-card-added">
          Added {new Date(camera.created_at).toLocaleDateString()}
        </span>
      </div>
    </div>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function DeviceManagement() {
  const [cameras,    setCameras]    = useState([])
  const [loading,    setLoading]    = useState(true)
  const [nextName,   setNextName]   = useState(null)
  const [modal,      setModal]      = useState(null) // null | { type: 'add' } | { type: 'edit', camera } | { type: 'delete', camera }

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [cams, next] = await Promise.all([
        camerasApi.list(),
        camerasApi.nextName(),
      ])
      setCameras(cams)
      setNextName(next)
    } catch {
      toast.error('Failed to load cameras.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const handleSaved = () => load()
  const handleDeleted = () => load()

  const entryCams   = cameras.filter(c => c.assignment === 'entry')
  const parkingCams = cameras.filter(c => c.assignment === 'parking')

  return (
    <AdminLayout>
      <div className="dm-page">

        {/* Page Header */}
        <div className="dm-page-header">
          <div className="dm-header-left">
            <div className="dm-header-icon"><Video size={22} /></div>
            <div>
              <h1 className="dm-page-title">Device Management</h1>
              <p className="dm-page-sub">Manage IP cameras for entry and parking monitoring</p>
            </div>
          </div>
          <div className="dm-header-right">
            <button className="dm-btn dm-btn-ghost dm-btn-sm" onClick={load} disabled={loading}>
              <RefreshCw size={14} className={loading ? 'dm-spin' : ''} />
              Refresh
            </button>
            <button className="dm-btn dm-btn-primary" onClick={() => setModal({ type: 'add' })}>
              <Plus size={16} />
              Add Device
            </button>
          </div>
        </div>

        {/* Stats */}
        <div className="dm-stats">
          <div className="dm-stat">
            <span className="dm-stat-value">{cameras.length}</span>
            <span className="dm-stat-label">Total Cameras</span>
          </div>
          <div className="dm-stat dm-stat-entry">
            <span className="dm-stat-value">{entryCams.length}</span>
            <span className="dm-stat-label"><ShieldCheck size={13} /> Entry</span>
          </div>
          <div className="dm-stat dm-stat-parking">
            <span className="dm-stat-value">{parkingCams.length}</span>
            <span className="dm-stat-label"><ParkingCircle size={13} /> Parking</span>
          </div>
        </div>

        {/* Content */}
        {loading ? (
          <div className="dm-loading">
            <span className="dm-spinner-lg" />
            <p>Loading devices…</p>
          </div>
        ) : cameras.length === 0 ? (
          <div className="dm-empty">
            <div className="dm-empty-icon"><Camera size={40} /></div>
            <h3>No cameras configured</h3>
            <p>Add your first IP camera to start monitoring entry and parking areas.</p>
            <button className="dm-btn dm-btn-primary" onClick={() => setModal({ type: 'add' })}>
              <Plus size={15} /> Add First Device
            </button>
          </div>
        ) : (
          <div className="dm-sections">
            {/* Entry Cameras */}
            {entryCams.length > 0 && (
              <section className="dm-section">
                <div className="dm-section-head">
                  <ShieldCheck size={16} className="dm-section-icon-entry" />
                  <h2 className="dm-section-title">Entry Gate Cameras</h2>
                  <span className="dm-section-count">{entryCams.length}</span>
                </div>
                <div className="dm-grid">
                  {entryCams.map(cam => (
                    <CameraCard
                      key={cam.id}
                      camera={cam}
                      onEdit={(c) => setModal({ type: 'edit', camera: c })}
                      onDelete={(c) => setModal({ type: 'delete', camera: c })}
                    />
                  ))}
                </div>
              </section>
            )}

            {/* Parking Cameras */}
            {parkingCams.length > 0 && (
              <section className="dm-section">
                <div className="dm-section-head">
                  <ParkingCircle size={16} className="dm-section-icon-parking" />
                  <h2 className="dm-section-title">Parking Area Cameras</h2>
                  <span className="dm-section-count">{parkingCams.length}</span>
                </div>
                <div className="dm-grid">
                  {parkingCams.map(cam => (
                    <CameraCard
                      key={cam.id}
                      camera={cam}
                      onEdit={(c) => setModal({ type: 'edit', camera: c })}
                      onDelete={(c) => setModal({ type: 'delete', camera: c })}
                    />
                  ))}
                </div>
              </section>
            )}
          </div>
        )}

        {/* Info Banner */}
        {cameras.length > 0 && (
          <div className="dm-info-banner">
            <Wifi size={15} />
            <span>
              Entry cameras auto-connect when visiting <strong>Entry Management</strong>.
              Parking cameras auto-connect when visiting <strong>Parking Management</strong>.
            </span>
          </div>
        )}
      </div>

      {/* Modals */}
      {modal?.type === 'add' && (
        <CameraModal
          mode="add"
          nextName={nextName}
          onClose={() => setModal(null)}
          onSaved={handleSaved}
        />
      )}
      {modal?.type === 'edit' && (
        <CameraModal
          mode="edit"
          camera={modal.camera}
          nextName={nextName}
          onClose={() => setModal(null)}
          onSaved={handleSaved}
        />
      )}
      {modal?.type === 'delete' && (
        <DeleteModal
          camera={modal.camera}
          onClose={() => setModal(null)}
          onDeleted={handleDeleted}
        />
      )}
    </AdminLayout>
  )
}
