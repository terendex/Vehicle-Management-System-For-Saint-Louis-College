import { useState, useRef, useEffect } from 'react'
import Webcam from 'react-webcam'
import {
  Camera, CameraOff, ScanLine, Upload, RotateCcw,
  CheckCircle, XCircle, Clock, HelpCircle, AlertTriangle,
  ClipboardList, UserPlus, X, ShieldCheck, Zap, Video, Plus,
  QrCode, Type, Bike, Shield
} from 'lucide-react'
import { toast } from "sonner"
import { formatDistanceToNow } from 'date-fns'
import SecurityLayout from '../../components/Layout/SecurityLayout'
import { getAccessLogs, getOffices, createVisitorPass, scanPlate, verifyDigitalId } from '../../api/scanning'
import { getRuleConstraints, getVehicleTypeAccess } from '../../api/vehicles'
import { useScanStream } from '../../hooks/useScanStream'
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

const VEHICLE_TYPE_META = {
  bicycle:          { label: 'Bicycle',         Icon: Bike, color: '#3b82f6' },
  e_bike:           { label: 'E-Bike',          Icon: Bike, color: '#8b5cf6' },
  electric_scooter: { label: 'Electric Scooter', Icon: Zap,  color: '#10b981' },
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

// ─── DigitalIDModal ───────────────────────────────────────────────────────────
function DigitalIDModal({ trackId, vehicleType, onVerify, onClose }) {
  const [digitalId, setDigitalId] = useState('')
  const [loading, setLoading] = useState(false)
  const [scanMode, setScanMode] = useState('manual')

  const handleSubmit = async (e) => {
    e?.preventDefault()
    if (!digitalId.trim()) { toast.error('Please enter a digital ID.'); return }
    setLoading(true)
    try {
      const result = await verifyDigitalId({ digital_id: digitalId.trim(), vehicle_type: vehicleType })
      onVerify(trackId, result.data ?? result)
      onClose()
    } catch (err) {
      toast.error(err?.response?.data?.message || 'Digital ID verification failed.')
    } finally {
      setLoading(false)
    }
  }

  const meta = VEHICLE_TYPE_META[vehicleType] || { label: vehicleType, Icon: Type, color: '#6b7280' }
  const Icon = meta.Icon

  return (
    <div className="em-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="em-modal">
        <div className="em-modal-head">
          <span className="em-modal-title"><Icon size={17} /> Verify {meta.label} Entry</span>
          <button className="em-modal-close" onClick={onClose}><X size={15} /></button>
        </div>
        <div className="em-modal-body">
          <div className="em-tabs">
            <button className={`em-tab ${scanMode === 'manual' ? 'active' : ''}`} onClick={() => setScanMode('manual')}>
              Manual Entry
            </button>
            <button className={`em-tab ${scanMode === 'scan' ? 'active' : ''}`} onClick={() => setScanMode('scan')}>
              Scan QR Code
            </button>
          </div>

          {scanMode === 'scan' ? (
            <div className="em-qr-scan-area">
              <QrCode size={64} color="#6b7280" />
              <p>Align QR code within the frame</p>
              <button className="em-btn em-btn-secondary" onClick={() => toast.info('QR scanning coming soon.')}>
                Capture QR Code
              </button>
            </div>
          ) : (
            <form onSubmit={handleSubmit}>
              <div className="em-field">
                <label className="em-label">Digital ID Number</label>
                <input
                  className="em-input"
                  placeholder="e.g., SLC-OWN-001234"
                  value={digitalId}
                  onChange={(e) => setDigitalId(e.target.value)}
                  autoFocus
                />
              </div>
              <p className="em-help-text">Enter the ID number from the rider's phone or ID card</p>
            </form>
          )}
        </div>
        <div className="em-modal-foot">
          <button type="button" className="em-btn em-btn-secondary" onClick={onClose}>Cancel</button>
          {scanMode === 'manual' && (
            <button
              type="button"
              className="em-btn em-btn-primary"
              disabled={loading || !digitalId.trim()}
              onClick={handleSubmit}
            >
              {loading ? <><div className="em-spinner" /> Verifying…</> : 'Verify ID'}
            </button>
          )}
        </div>
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
        <p className="em-idle-hint">Point the camera at a license plate or unplated vehicle to start live scanning.</p>
      </div>
    )
  }

  const { Icon, label, cls } = getMeta(result.status)
  const owner = result.owner ?? result.vehicle?.owner
  const isVisitor = result.status === 'unknown' || result.status === 'no_pass'
  const vehicleType = result.vehicle_type
  const vMeta = vehicleType ? (VEHICLE_TYPE_META[vehicleType] || { label: vehicleType, Icon: Type, color: '#6b7280' }) : null

  return (
    <>
      <div className={`em-card em-result ${cls}`}>
        <div className={`em-result-banner ${cls}`}>
          <div className="em-result-icon"><Icon size={20} /></div>
          <div className="em-result-text">
            <p className="em-result-status">{label}</p>
            {vMeta ? (
              <p className="em-result-plate"><vMeta.Icon size={14} /> {vMeta.label}</p>
            ) : (
              <p className="em-result-plate">{result.plate_number || '—'}</p>
            )}
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
          {isVisitor && !vehicleType && (
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
  { color: '#8b5cf6', label: 'E-Bike'          },
  { color: '#10b981', label: 'E-Scooter'       },
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
  const [idModalState, setIdModalState] = useState({ open: false, trackId: null, vehicleType: null })

  const [cameras, setCameras]         = useState([{ id: 1, name: 'Main Gate - Front' }])
  const [activeCamId, setActiveCamId] = useState(1)

  const webcamRefs = useRef({})
  const fileInputRef = useRef(null)
  const openedModalIds = useRef(new Set())

  const {
    scanning,
    connected,
    results: wsResults,
    flash,
    activeTracks,
    idRequiredTracks,
    videoRef,
    canvasRef,
    dismissIdRequired,
  } = useScanStream(token, cameraOn)

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

  // ── id_required events → open modal (once per track) ───────────────────────
  useEffect(() => {
    const entries = Object.entries(idRequiredTracks)
    if (!entries.length) return
    for (const [trackId, info] of entries) {
      const tid = parseInt(trackId)
      if (!openedModalIds.current.has(tid)) {
        openedModalIds.current.add(tid)
        setIdModalState({ open: true, trackId: tid, vehicleType: info.vehicle_type })
        break
      }
    }
  }, [idRequiredTracks])

  // ── Handlers ────────────────────────────────────────────────────────────────
  const handleIdVerify = (trackId, result) => {
    const vMeta = VEHICLE_TYPE_META[result.vehicle_type]
    const displayLabel = vMeta ? vMeta.label : (result.vehicle_type || 'Unplated')
    const ownerSuffix = result.owner?.full_name ? ` — ${result.owner.full_name}` : ''
    setLogs((prev) => ([{
      id: Date.now() + Math.random(),
      plate_number: `${displayLabel}${ownerSuffix}`,
      status: result.status,
      scanned_at: new Date().toISOString(),
    }, ...prev]).slice(0, 20))
    toast.success(result.message || 'ID verified')
  }

  const handleModalClose = () => {
    dismissIdRequired(idModalState.trackId)
    setIdModalState({ open: false, trackId: null, vehicleType: null })
  }

  const addCamera = () => {
    if (cameras.length >= 4) { toast.error('Maximum of 4 cameras allowed.'); return }
    const id = Date.now()
    setCameras(prev => [...prev, { id, name: `Angle ${prev.length + 1}` }])
  }

  const removeCamera = (id) => {
    setCameras(prev => {
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
              Scan license plates or unplated vehicles — entry is decided automatically based on registration and schedule.
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
                {mode === 'camera' ? 'Live Camera Feed' : 'Upload Plate Image'}
              </span>
              <div className="em-mode-toggle">
                <button
                  className={`em-mode-btn ${mode === 'camera' ? 'active' : ''}`}
                  onClick={() => { setMode('camera'); setCooldown(false); setDisplayResult(null) }}
                >Camera</button>
                <button
                  className={`em-mode-btn ${mode === 'upload' ? 'active' : ''}`}
                  onClick={() => { setMode('upload'); stopCamera(); resetUpload() }}
                >Upload</button>
              </div>
            </div>

            {/* Viewport */}
            {mode === 'camera' ? (
              <div className="em-viewport" style={{ background: cameraOn ? '#1A1D2E' : '#08090F', padding: cameraOn ? '10px 1.25rem' : 0 }}>
                {cameraOn ? (
                  <div className="em-multi-cam-container">
                    <div className="em-primary-cam">
                      {cameras.map(cam => (
                        <div key={`primary-${cam.id}`} style={{ display: activeCamId === cam.id ? 'block' : 'none', width: '100%', height: '100%' }}>
                          <Webcam
                            ref={(el) => {
                              webcamRefs.current[cam.id] = el
                              if (cam.id === activeCamId) videoRef.current = el
                            }}
                            audio={false}
                            screenshotFormat="image/jpeg"
                            screenshotQuality={0.95}
                            className="em-video"
                            videoConstraints={{ facingMode: 'environment' }}
                          />
                        </div>
                      ))}
                      {cameraOn && <BBoxLegend />}
                      <div className="em-scan-frame">
                        <div className="em-scan-bracket">
                          <div className="em-scan-inner" />
                          {!cooldown && <div className="em-scan-line" />}
                        </div>
                      </div>
                      {flash && <div className="em-flash" />}

                      {activeTracks.length > 0 && (
                        <canvas
                          ref={canvasRef}
                          style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', pointerEvents: 'none' }}
                        />
                      )}
                      <div style={{ position: 'absolute', top: 12, left: 12, background: 'rgba(0,0,0,0.6)', color: 'white', padding: '4px 10px', borderRadius: 6, fontSize: 11, fontWeight: 600 }}>
                        {cameras.find(c => c.id === activeCamId)?.name}
                      </div>
                    </div>

                    <div className="em-cam-thumbnails">
                      {cameras.map(cam => (
                        <div
                          key={`thumb-${cam.id}`}
                          className={`em-cam-thumb ${activeCamId === cam.id ? 'active' : ''}`}
                          onClick={() => setActiveCamId(cam.id)}
                        >
                          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: activeCamId === cam.id ? '#60A5FA' : '#5A5F72' }}>
                            <Video size={24} />
                          </div>
                          <div className="em-cam-thumb-label">{cam.name}</div>
                          {cameras.length > 1 && (
                            <div className="em-cam-thumb-actions">
                              <button className="em-cam-delete" onClick={(e) => { e.stopPropagation(); removeCamera(cam.id) }} title="Remove angle">
                                <X size={12} />
                              </button>
                            </div>
                          )}
                        </div>
                      ))}
                      {cameras.length < 4 && (
                        <div
                          className="em-cam-thumb"
                          style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#2A304D', border: '1px dashed #4A5070', cursor: 'pointer' }}
                          onClick={addCamera}
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

      {/* Digital ID modal */}
      {idModalState.open && (
        <DigitalIDModal
          trackId={idModalState.trackId}
          vehicleType={idModalState.vehicleType}
          onVerify={handleIdVerify}
          onClose={handleModalClose}
        />
      )}
    </SecurityLayout>
  )
}
