import { useState, useRef, useEffect } from 'react'
import Webcam from 'react-webcam'
import {
  Camera, CameraOff, ScanLine, Upload, RotateCcw,
  CheckCircle, XCircle, Clock, HelpCircle, AlertTriangle,
  ClipboardList, UserPlus, X, ShieldCheck, Video, Plus,
  Shield, Wifi, Link2
} from 'lucide-react'
import { toast } from "sonner"
import { formatDistanceToNow } from 'date-fns'
import SecurityLayout from '../../components/Layout/SecurityLayout'
import { getAccessLogs, getOffices, createVisitorPass, scanPlate } from '../../api/scanning'
import { camerasApi } from '../../api/cameras'
import { getRuleConstraints, getVehicleTypeAccess } from '../../api/vehicles'
import { useScanStream } from '../../hooks/useScanStream'
import { useMultiRtspStream } from '../../hooks/useMultiRtspStream'
import './SecurityEntryManagement.css'

const STATUS_META = {
  authorized: { label: 'Approved for Entry',     Icon: CheckCircle,   cls: 'authorized', logCls: 'authorized' },
  wrong_day:  { label: 'Wrong Schedule Day',     Icon: XCircle,       cls: 'wrong_day',  logCls: 'wrong_day'  },
  denied:     { label: 'Entry Denied',           Icon: XCircle,       cls: 'denied',     logCls: 'denied'     },
  pending:    { label: 'Awaiting Approval',      Icon: Clock,         cls: 'pending',    logCls: 'pending'    },
  unknown:    { label: 'Visitor / Unregistered', Icon: HelpCircle,    cls: 'visitor',    logCls: 'visitor'    },
  no_pass:    { label: 'No Visitor Pass',        Icon: AlertTriangle, cls: 'visitor',    logCls: 'visitor'    },
  disabled:   { label: 'Access Disabled',        Icon: XCircle,       cls: 'denied',     logCls: 'denied'     },
  unreadable: { label: 'Unreadable Plate',       Icon: AlertTriangle, cls: 'visitor',    logCls: 'visitor'    },
  cooldown:   { label: 'Recently Scanned',       Icon: Clock,         cls: 'pending',    logCls: 'pending'    },
}

function getMeta(status) {
  return STATUS_META[status] ?? STATUS_META.unknown
}

function timeAgo(ts) {
  try { return formatDistanceToNow(new Date(ts), { addSuffix: true }) }
  catch { return '' }
}

// ─── VisitorPassModal ─────────────────────────────────────────────────────────
function VisitorPassModal({ plate, offices, onClose, onCreated }) {
  const [officeId, setOfficeId] = useState('')
  const [purpose, setPurpose] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!officeId || !purpose.trim()) { toast.error('Please fill in all fields.'); return }
    setLoading(true)
    try {
      await createVisitorPass({ plate_number: plate, office: officeId, purpose })
      toast.success('Visitor pass created — awaiting office confirmation.')
      onCreated()
      onClose()
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Failed to create visitor pass.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="em-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="em-modal">
        <div className="em-modal-head">
          <span className="em-modal-title"><UserPlus size={17} /> Create Visitor Pass</span>
          <button className="em-modal-close" onClick={onClose}><X size={15} /></button>
        </div>
        <form onSubmit={handleSubmit}>
          <div className="em-modal-body">
            <div className="em-field">
              <label className="em-label">License Plate</label>
              <input className="em-input" value={plate} readOnly />
            </div>
            <div className="em-field">
              <label className="em-label">Destination Office</label>
              <select className="em-select" value={officeId} onChange={(e) => setOfficeId(e.target.value)} required>
                <option value="">Select office…</option>
                {offices.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
              </select>
            </div>
            <div className="em-field">
              <label className="em-label">Purpose of Visit</label>
              <textarea
                className="em-textarea"
                placeholder="e.g. Enrollment inquiry, document pick-up…"
                value={purpose}
                onChange={(e) => setPurpose(e.target.value)}
                required
              />
            </div>
          </div>
          <div className="em-modal-foot">
            <button type="button" className="em-btn em-btn-secondary" onClick={onClose}>Cancel</button>
            <button type="submit" className="em-btn em-btn-primary" disabled={loading}>
              {loading ? <><div className="em-spinner" /> Creating…</> : 'Create Pass'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ─── ResultCard ───────────────────────────────────────────────────────────────
function ResultCard({ result, offices, onPassCreated }) {
  const [showModal, setShowModal] = useState(false)

  if (!result) {
    return (
      <div className="em-card em-result">
        <div className="em-result-banner idle">
          <div className="em-result-icon idle"><ScanLine size={20} /></div>
          <div className="em-result-text">
            <p className="em-result-status" style={{ color: '#9BA3BF' }}>Awaiting scan</p>
            <p className="em-result-plate" style={{ color: '#C8CCDE', fontSize: 15, letterSpacing: 1 }}>— — — — —</p>
          </div>
        </div>
        <p className="em-idle-hint">Point the camera at a license plate to start live scanning.</p>
      </div>
    )
  }

  const { Icon, label, cls } = getMeta(result.status)
  const owner = result.owner ?? result.vehicle?.owner
  const isVisitor = result.status === 'unknown' || result.status === 'no_pass'

  return (
    <>
      <div className={`em-card em-result ${cls}`}>
        <div className={`em-result-banner ${cls}`}>
          <div className="em-result-icon"><Icon size={20} /></div>
          <div className="em-result-text">
            <p className="em-result-status">{label}</p>
            <p className="em-result-plate">{result.plate_number || '—'}</p>
          </div>
        </div>
        <div className="em-result-body">
          <p className="em-result-msg">{result.message}</p>
          {result.constraint && (
            <div className="em-constraint-info">
              <AlertTriangle size={13} style={{ flexShrink: 0 }} />
              <span>Rule blocked: <strong>{result.constraint}</strong></span>
            </div>
          )}
          {owner && (
            <div className="em-result-rows">
              {owner.full_name && (
                <div className="em-result-row">
                  <span className="em-result-row-label">Owner</span>
                  <span className="em-result-row-value">{owner.full_name}</span>
                </div>
              )}
              {owner.owner_type && (
                <div className="em-result-row">
                  <span className="em-result-row-label">Type</span>
                  <span className="em-result-row-value" style={{ textTransform: 'capitalize' }}>
                    {owner.owner_type.replace('_', ' ')}
                  </span>
                </div>
              )}
              {owner.schedule && (
                <div className="em-result-row">
                  <span className="em-result-row-label">Schedule</span>
                  <span className="em-result-row-value">{owner.schedule}</span>
                </div>
              )}
              {result.has_violations && (
                <div className="em-result-row">
                  <span className="em-result-row-label">Violations</span>
                  <span className="em-violation-pill">
                    <AlertTriangle size={10} /> Unresolved violations
                  </span>
                </div>
              )}
            </div>
          )}
          {isVisitor && (
            <button className="em-btn em-btn-secondary" style={{ width: '100%', marginTop: 4 }} onClick={() => setShowModal(true)}>
              <UserPlus size={14} /> Create Visitor Pass
            </button>
          )}
        </div>
      </div>

      {showModal && (
        <VisitorPassModal
          plate={result.plate_number}
          offices={offices}
          onClose={() => setShowModal(false)}
          onCreated={onPassCreated}
        />
      )}
    </>
  )
}

// ─── BBoxLegend ───────────────────────────────────────────────────────────────
const BBOX_LEGEND = [
  { color: '#00ff88', label: 'Plate / Vehicle' },
  { color: '#3b82f6', label: 'Motor'           },
]

function BBoxLegend() {
  return (
    <div className="em-bbox-legend">
      {BBOX_LEGEND.map(({ color, label }) => (
        <div key={label} className="em-bbox-legend-item">
          <span className="em-bbox-legend-swatch" style={{ background: color }} />
          <span className="em-bbox-legend-label">{label}</span>
        </div>
      ))}
    </div>
  )
}

// ─── SecurityEntryManagement (page) ──────────────────────────────────────────
export default function SecurityEntryManagement() {
  // Use the same JWT access token as the admin page — no prompt needed
  const token = localStorage.getItem('access_token') || ''

  const [mode, setMode]               = useState('camera')
  const [cameraOn, setCameraOn]       = useState(false)
  const [uploadFile, setUploadFile]   = useState(null)
  const [dragOver, setDragOver]       = useState(false)
  const [cooldown, setCooldown]       = useState(false)
  const [displayResult, setDisplayResult] = useState(null)
  const [logs, setLogs]               = useState([])
  const [offices, setOffices]         = useState([])
  const [rules, setRules]             = useState([])
  const [loadingRules, setLoadingRules] = useState(true)
  const [vehicleTypes, setVehicleTypes] = useState([])
  const [loadingVehicles, setLoadingVehicles] = useState(true)
  const [webcams, setWebcams]           = useState([{ id: 1, name: 'Main Gate - Front' }])
  const [activeCamId, setActiveCamId]   = useState(1)
  const webcamRefs = useRef({})
  const fileInputRef = useRef(null)

  const {
    scanning,
    connected,
    results: wsResults,
    flash,
    activeTracks,
    videoRef,
    canvasRef,
  } = useScanStream(token, cameraOn)

  const {
    cameras:         rtspCameras,
    activeCamId:     rtspActiveCamId,
    setActiveCamId:  setRtspActiveCam,
    activeCam:       rtspActiveCam,
    addCamera:       addRtspCamera,
    removeCamera:    removeRtspCamera,
    disconnectAll:   disconnectAllRtsp,
    results:         rtspResults,
    flash:           rtspFlash,
    registerCanvas:  registerRtspCanvas,
  } = useMultiRtspStream(token)

  // ── Data fetching ───────────────────────────────────────────────────────────
  useEffect(() => {
    getAccessLogs({ limit: 20 }).then((r) => setLogs(r.data?.results ?? r.data ?? [])).catch(() => {})
    getOffices().then((r) => setOffices(r.data?.results ?? r.data ?? [])).catch(() => {})
    getRuleConstraints().then((r) => {
      setRules((r.data?.results ?? r.data ?? []).filter(rule => rule.enabled))
      setLoadingRules(false)
    }).catch(() => setLoadingRules(false))
    getVehicleTypeAccess().then((r) => {
      setVehicleTypes((r.data?.results ?? r.data ?? []).filter(v => v.enabled))
      setLoadingVehicles(false)
    }).catch(() => setLoadingVehicles(false))
  }, [])

  // ── WS plate results → display + log ───────────────────────────────────────
  useEffect(() => {
    if (!wsResults?.length) return
    setDisplayResult(wsResults)
    setCooldown(true)
    setTimeout(() => setCooldown(false), 5000)
    setLogs((prev) => {
      const newLogs = wsResults.map(r => ({
        id: Date.now() + Math.random(),
        plate_number: r.plate_number,
        status: r.status,
        scanned_at: new Date().toISOString(),
      }))
      return [...newLogs, ...prev].slice(0, 20)
    })
  }, [wsResults])

  // ── RTSP results (any camera) → display + log ──────────────────────────────
  useEffect(() => {
    if (!rtspResults?.length) return
    setDisplayResult(rtspResults)
    setCooldown(true)
    setTimeout(() => setCooldown(false), 5000)
    setLogs((prev) => {
      const newLogs = rtspResults.map(r => ({
        id: Date.now() + Math.random(),
        plate_number: r.plate_number,
        status: r.status,
        scanned_at: new Date().toISOString(),
      }))
      return [...newLogs, ...prev].slice(0, 20)
    })
  }, [rtspResults])

  // ── Load entry cameras from Device Management (DB only) ──────────────────────
  useEffect(() => {
    camerasApi.list({ assignment: 'entry' })
      .then(cams => cams.forEach(c => addRtspCamera(c.name, c.rtsp_url)))
      .catch(() => {})
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Handlers ───────────────────────────────────────────────────────────────────
  const handleRtspDisconnectAll = () => {
    disconnectAllRtsp()
    setCooldown(false)
    setDisplayResult(null)
  }

  const handleRemoveRtspCam = (camId) => {
    removeRtspCamera(camId)
  }

  const addWebcam = () => {
    if (webcams.length >= 4) { toast.error('Maximum of 4 cameras allowed.'); return }
    const id = Date.now()
    setWebcams(prev => [...prev, { id, name: `Angle ${prev.length + 1}` }])
  }

  const removeWebcam = (id) => {
    setWebcams(prev => {
      const next = prev.filter(c => c.id !== id)
      if (activeCamId === id && next.length > 0) setActiveCamId(next[0].id)
      return next
    })
  }

  const stopCamera = () => {
    setCameraOn(false)
    setCooldown(false)
    setDisplayResult(null)
  }

  const stopAllAndSwitchMode = (nextMode) => {
    if (nextMode !== 'camera') { setCameraOn(false); setCooldown(false) }
    if (nextMode !== 'rtsp') { disconnectAllRtsp(); setDisplayResult(null) }
    if (nextMode !== 'upload') { /* nothing */ }
  }

  const handleFileChange = (file) => {
    if (!file || !file.type.startsWith('image/')) { toast.error('Please select an image file.'); return }
    setUploadFile({ file, url: URL.createObjectURL(file) })
    setDisplayResult(null)
  }

  const handleDrop = (e) => {
    e.preventDefault()
    setDragOver(false)
    handleFileChange(e.dataTransfer.files?.[0])
  }

  const resetUpload = () => {
    if (uploadFile?.url) URL.revokeObjectURL(uploadFile.url)
    setUploadFile(null)
    setDisplayResult(null)
  }

  const handleUploadScan = async () => {
    if (!uploadFile) return
    try {
      const res = await scanPlate(uploadFile.file)
      const data = res.data?.results ?? res.data ?? []
      if (data.length) {
        setDisplayResult(data)
        setCooldown(true)
        setTimeout(() => setCooldown(false), 5000)
        setLogs((prev) => {
          const newLogs = data.map(r => ({
            id: Date.now() + Math.random(),
            plate_number: r.plate_number,
            status: r.status,
            scanned_at: new Date().toISOString(),
          }))
          return [...newLogs, ...prev].slice(0, 20)
        })
      } else {
        toast.info('No plate detected in this image.')
      }
    } catch {
      toast.error('Upload scan failed')
    }
  }

  const handlePassCreated = () => {
    getAccessLogs({ limit: 20 }).then((r) => setLogs(r.data?.results ?? r.data ?? [])).catch(() => {})
  }

  // ── Render ──────────────────────────────────────────────────────────────────
  return (
    <SecurityLayout>
      <div className="em-page">

        {/* Header */}
        <div className="em-header">
          <div>
            <h1 className="em-title">Vehicle Entry Management</h1>
            <p className="em-subtitle">
              Scan license plates — entry is decided automatically based on registration and schedule.
            </p>
          </div>
          {cameraOn ? (
            <div className={`em-live-badge ${connected ? '' : 'connecting'}`}>
              <span className="em-live-dot" />
              {connected ? 'LIVE' : 'CONNECTING…'}
            </div>
          ) : (
            <div className="em-live-badge offline">
              <span className="em-live-dot" /> OFFLINE
            </div>
          )}
        </div>

        {/* Main grid */}
        <div className="em-grid">

          {/* Camera / Upload card */}
          <div className="em-card">
            <div className="em-card-head">
              <span className="em-card-label">
                <Camera size={15} />
                {mode === 'camera' ? `Live Camera Feed` : mode === 'rtsp' ? `IP Cameras (${rtspCameras.length})` : 'Upload Plate Image'}
              </span>
              <div className="em-mode-toggle">
                <button
                  className={`em-mode-btn ${mode === 'camera' ? 'active' : ''}`}
                  onClick={() => { stopAllAndSwitchMode('camera'); setMode('camera') }}
                >Camera</button>
                <button
                  className={`em-mode-btn ${mode === 'rtsp' ? 'active' : ''}`}
                  onClick={() => { stopAllAndSwitchMode('rtsp'); setMode('rtsp') }}
                ><Wifi size={12} style={{ marginRight: 4 }} />RTSP</button>
                <button
                  className={`em-mode-btn ${mode === 'upload' ? 'active' : ''}`}
                  onClick={() => { stopAllAndSwitchMode('upload'); resetUpload(); setMode('upload') }}
                >Upload</button>
              </div>
            </div>

            {/* Viewport */}
            {mode === 'camera' ? (
              <div className="em-viewport" style={{ background: cameraOn ? '#1A1D2E' : '#08090F', padding: cameraOn ? '10px 1.25rem' : 0, minHeight: cameraOn ? 340 : undefined }}>
                {cameraOn ? (
                  <div className="em-multi-cam-container">
                    <div className="em-primary-cam">
                      {webcams.map(cam => (
                        <div key={`primary-${cam.id}`} style={{ display: activeCamId === cam.id ? 'flex' : 'none', width: '100%', height: '100%', flex: 1 }}>
                          <Webcam
                            ref={(el) => {
                              webcamRefs.current[cam.id] = el
                              if (cam.id === activeCamId) videoRef.current = el
                            }}
                            audio={false}
                            screenshotFormat="image/jpeg"
                            screenshotQuality={0.95}
                            className="em-video"
                            videoConstraints={{ facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } }}
                            onUserMediaError={(err) => toast.error(`Camera error: ${err?.message || err?.name || 'Could not access camera'}`)}
                            style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
                          />
                        </div>
                      ))}
                      {cameraOn && <BBoxLegend />}

                      {flash && <div className="em-flash" />}

                      <canvas
                        ref={canvasRef}
                        style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', pointerEvents: 'none' }}
                      />
                      <div style={{ position: 'absolute', top: 12, left: 12, background: 'rgba(0,0,0,0.6)', color: 'white', padding: '4px 10px', borderRadius: 6, fontSize: 11, fontWeight: 600 }}>
                        {webcams.find(c => c.id === activeCamId)?.name}
                      </div>
                    </div>

                    <div className="em-cam-thumbnails">
                      {webcams.map(cam => (
                        <div
                          key={`thumb-${cam.id}`}
                          className={`em-cam-thumb ${activeCamId === cam.id ? 'active' : ''}`}
                          onClick={() => setActiveCamId(cam.id)}
                        >
                          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: activeCamId === cam.id ? '#60A5FA' : '#5A5F72' }}>
                            <Video size={24} />
                          </div>
                          <div className="em-cam-thumb-label">{cam.name}</div>
                          {webcams.length > 1 && (
                            <div className="em-cam-thumb-actions">
                              <button className="em-cam-delete" onClick={(e) => { e.stopPropagation(); removeWebcam(cam.id) }} title="Remove angle">
                                <X size={12} />
                              </button>
                            </div>
                          )}
                        </div>
                      ))}
                      {webcams.length < 4 && (
                        <div
                          className="em-cam-thumb"
                          style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#2A304D', border: '1px dashed #4A5070', cursor: 'pointer' }}
                          onClick={addWebcam}
                          title="Add another angle"
                        >
                          <Plus size={20} color="#9BA3BF" />
                        </div>
                      )}
                    </div>
                  </div>
                ) : (
                  <div className="em-cam-off">
                    <CameraOff size={52} />
                    <p>Camera is off — press Start Camera to begin</p>
                  </div>
                )}
              </div>
            ) : mode === 'rtsp' ? (
              <>
                {/* Main canvas area */}
                <div className="em-viewport" style={{ background: '#0d1117', minHeight: 340, position: 'relative' }}>
                  {rtspCameras.length > 0 ? (
                    <div style={{ position: 'relative', width: '100%', minHeight: 300 }}>
                      {/* Stacked canvases — only the active camera is visible */}
                      {rtspCameras.map((cam, idx) => (
                        <div
                          key={cam.id}
                          style={{ display: rtspActiveCamId === cam.id ? 'block' : 'none', width: '100%', ...(idx === 0 ? {} : { position: 'absolute', inset: 0 }) }}
                        >
                          <canvas
                            ref={el => registerRtspCanvas(cam.id, el)}
                            style={{ width: '100%', display: 'block', background: '#000', minHeight: 300 }}
                          />
                        </div>
                      ))}
                      {/* Flash effect */}
                      {rtspFlash && <div className="em-flash" style={{ position: 'absolute', inset: 0 }} />}
                      {/* Connecting overlay */}
                      {rtspActiveCam && !rtspActiveCam.streamConnected && rtspActiveCam.wsActive && (
                        <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.7)', gap: 12, pointerEvents: 'none' }}>
                          <div className="em-spinner" style={{ width: 36, height: 36, borderWidth: 3, borderTopColor: '#60a5fa', borderColor: 'rgba(96,165,250,0.15)' }} />
                          <p style={{ color: '#93c5fd', fontSize: 13, margin: 0 }}>{rtspActiveCam.statusMsg || 'Connecting…'}</p>
                        </div>
                      )}
                      {/* Camera name badge */}
                      <div style={{ position: 'absolute', top: 12, left: 12, background: 'rgba(0,0,0,0.65)', color: '#fff', padding: '4px 10px', borderRadius: 6, fontSize: 11, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 5, pointerEvents: 'none' }}>
                        <span style={{ width: 7, height: 7, borderRadius: '50%', background: rtspActiveCam?.streamConnected ? '#22c55e' : '#f59e0b', display: 'inline-block' }} />
                        {rtspActiveCam?.name || 'IP Camera'}
                      </div>
                      {/* Camera count badge */}
                      {rtspCameras.length > 1 && (
                        <div style={{ position: 'absolute', top: 12, right: 12, background: 'rgba(0,0,0,0.65)', color: '#60a5fa', padding: '4px 10px', borderRadius: 6, fontSize: 11, fontWeight: 600, pointerEvents: 'none' }}>
                          {rtspCameras.filter(c => c.streamConnected).length}/{rtspCameras.length} live
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="em-cam-off">
                      <Wifi size={52} style={{ color: '#374151' }} />
                      <p>Add RTSP cameras below to begin monitoring</p>
                    </div>
                  )}
                </div>

                {/* Camera thumbnail strip (when 2+ cameras) */}
                {rtspCameras.length > 0 && (
                  <div className="em-cam-thumbnails" style={{ marginTop: 0, borderTop: '1px solid #1e2235' }}>
                    {rtspCameras.map(cam => (
                      <div
                        key={`rthumb-${cam.id}`}
                        className={`em-cam-thumb ${rtspActiveCamId === cam.id ? 'active' : ''}`}
                        onClick={() => setRtspActiveCam(cam.id)}
                        style={{ position: 'relative' }}
                      >
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: rtspActiveCamId === cam.id ? '#60A5FA' : '#5A5F72' }}>
                          <Wifi size={20} />
                        </div>
                        <div className="em-cam-thumb-label">{cam.name}</div>
                        {/* Status dot */}
                        <span style={{ position: 'absolute', top: 4, left: 4, width: 6, height: 6, borderRadius: '50%', background: cam.streamConnected ? '#22c55e' : cam.wsActive ? '#f59e0b' : '#6b7280', display: 'inline-block' }} />
                        <div className="em-cam-thumb-actions">
                          <button className="em-cam-delete" onClick={e => { e.stopPropagation(); handleRemoveRtspCam(cam.id) }} title="Disconnect">
                            <X size={12} />
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </>
            ) : (
              uploadFile ? (
                <div className="em-upload-preview">
                  <img src={uploadFile.url} alt="Plate capture" style={{ width: '100%', height: '100%', objectFit: 'contain' }} />
                  {flash && <div className="em-flash" />}
                </div>
              ) : (
                <div
                  className={`em-upload-zone ${dragOver ? 'drag-over' : ''}`}
                  onClick={() => fileInputRef.current?.click()}
                  onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
                  onDragLeave={() => setDragOver(false)}
                  onDrop={handleDrop}
                >
                  <Upload size={38} />
                  <p>Click to upload or drag &amp; drop</p>
                  <span>JPG · PNG · WEBP</span>
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept="image/*"
                    style={{ display: 'none' }}
                    onChange={(e) => handleFileChange(e.target.files?.[0])}
                  />
                </div>
              )
            )}

            {/* Controls */}
            <div className="em-controls">
              {mode === 'camera' ? (
                cameraOn ? (
                  <>
                    <div className={`em-autoscan-status ${cooldown ? 'cooldown' : ''}`}>
                      {cooldown
                        ? <><Clock size={13} /> Cooldown…</>
                        : scanning
                          ? <><div className="em-spinner" style={{ borderTopColor: '#065F46', borderColor: 'rgba(6,95,70,.2)' }} /> Scanning…</>
                          : <><Zap size={13} /> Auto-scanning</>
                      }
                    </div>
                    <button className="em-btn em-btn-danger" onClick={stopCamera}>
                      <CameraOff size={15} /> Stop
                    </button>
                  </>
                ) : (
                  <button className="em-btn em-btn-primary em-btn-lg" onClick={() => setCameraOn(true)}>
                    <Camera size={17} /> Start Camera
                  </button>
                )
              ) : mode === 'rtsp' ? (
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', width: '100%' }}>
                  <div className={`em-autoscan-status ${rtspCameras.some(c => c.streamConnected) ? '' : 'cooldown'}`} style={{ flex: 1 }}>
                    {rtspCameras.length === 0
                      ? <><Video size={13} /> No entry cameras configured — add them in Device Management</>
                      : rtspCameras.some(c => c.streamConnected)
                        ? <><Zap size={13} /> {rtspCameras.filter(c => c.streamConnected).length}/{rtspCameras.length} camera{rtspCameras.length !== 1 ? 's' : ''} live</>
                        : <><div className="em-spinner" style={{ borderTopColor: '#3b82f6', borderColor: 'rgba(59,130,246,.2)' }} /> Connecting…</>}
                  </div>
                  {rtspCameras.length > 0 && (
                    <button className="em-btn em-btn-danger" onClick={handleRtspDisconnectAll}>
                      <Link2 size={14} /> Disconnect All
                    </button>
                  )}
                </div>
              ) : (
                uploadFile ? (
                  <>
                    <button className="em-btn em-btn-primary em-btn-lg" onClick={handleUploadScan}>
                      <ScanLine size={17} /> Scan Plate
                    </button>
                    <button className="em-btn em-btn-secondary em-btn-lg" onClick={() => fileInputRef.current?.click()}>
                      <Upload size={15} /> Choose Different
                    </button>
                    <button className="em-btn em-btn-secondary em-btn-icon" onClick={resetUpload} title="Reset">
                      <RotateCcw size={15} />
                    </button>
                  </>
                ) : (
                  <button className="em-btn em-btn-secondary em-btn-lg" onClick={() => fileInputRef.current?.click()}>
                    <Upload size={15} /> Choose Image
                  </button>
                )
              )}
            </div>
          </div>

          {/* Right panel */}
          <div className="em-right">
            {displayResult?.length > 0 ? (
              <div className="em-results-stack">
                {displayResult.map((r, idx) => (
                  <ResultCard key={`result-${idx}-${r.plate_number}`} result={r} offices={offices} onPassCreated={handlePassCreated} />
                ))}
              </div>
            ) : (
              <ResultCard result={null} offices={offices} onPassCreated={handlePassCreated} />
            )}

            {/* Entry rules */}
            <div className="em-card em-rules">
              <div className="em-card-head">
                <span className="em-card-label"><ShieldCheck size={14} /> Entry Rules</span>
              </div>
              <div className="em-rules-body">
                <div className="em-rules-section-label" style={{ borderColor: '#3b82f6' }}>Schedule Restrictions</div>
                {loadingRules ? (
                  <p className="em-log-empty">Loading rules…</p>
                ) : rules.length === 0 ? (
                  <p className="em-log-empty">No rules configured.</p>
                ) : (
                  rules.map((rule) => {
                    const dotColor = rule.constraint_type === 'employee' ? 'green'
                                   : rule.constraint_type === 'student'  ? 'blue' : 'purple'
                    const daysSummary = rule.days.length === 6 ? 'Mon–Sat'
                      : rule.days.length === 5 ? 'Mon–Fri'
                      : rule.days.join(', ').toUpperCase() || 'All days'
                    return (
                      <div key={`rule-${rule.id}`} className="em-rule-row">
                        <span className={`em-rule-dot ${dotColor}`} />
                        <span className="em-rule-text">
                          <strong>{rule.constraint_type.charAt(0).toUpperCase() + rule.constraint_type.slice(1)}s</strong>
                          {' — '}{daysSummary}, {rule.start_time}–{rule.end_time}
                        </span>
                      </div>
                    )
                  })
                )}

                <div className="em-rules-section-label" style={{ borderColor: '#f59e0b', marginTop: 16 }}>Vehicle Access Privileges</div>
                {loadingVehicles ? (
                  <p className="em-log-empty">Loading vehicle types…</p>
                ) : vehicleTypes.length === 0 ? (
                  <p className="em-log-empty">No vehicle types configured.</p>
                ) : (
                  vehicleTypes.map((v) => {
                    const dotColor = v.status === 'allowed' ? 'green' : 'orange'
                    const hoursDisplay = v.hours_display || (v.is_all_hours ? 'All hours' : `${v.hours_start || ''}–${v.hours_end || ''}`)
                    return (
                      <div key={`vt-${v.id}`} className="em-rule-row">
                        <span className={`em-rule-dot ${dotColor}`} />
                        <span className="em-rule-text" title={v.sub}>
                          <strong>{v.label}</strong>{' — '}{v.gate}, {hoursDisplay}
                        </span>
                      </div>
                    )
                  })
                )}
              </div>
            </div>

            {/* Recent scans */}
            <div className="em-card">
              <div className="em-card-head">
                <span className="em-card-label"><ClipboardList size={14} /> Recent Scans</span>
                <span className="em-logs-count">{logs.length}</span>
              </div>
              {logs.length === 0 ? (
                <p className="em-log-empty">No scans yet today.</p>
              ) : (
                <div className="em-log-list">
                  {logs.map((log, i) => {
                    const m = getMeta(log.status)
                    return (
                      <div key={log.id ?? i} className="em-log-item">
                        <span className={`em-log-dot ${m.logCls}`} />
                        <span className="em-log-plate">{log.plate_number}</span>
                        <span className={`em-log-badge ${m.logCls}`}>{m.label}</span>
                        <span className="em-log-time">{timeAgo(log.scanned_at)}</span>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

    </SecurityLayout>
  )
}
