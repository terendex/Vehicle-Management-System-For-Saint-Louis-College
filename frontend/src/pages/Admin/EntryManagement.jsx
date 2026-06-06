import { useState, useRef, useCallback, useEffect } from 'react'
import Webcam from 'react-webcam'
import {
  Camera, CameraOff, ScanLine, Upload, RotateCcw,
  CheckCircle, XCircle, Clock, HelpCircle, AlertTriangle,
  ClipboardList, UserPlus, X, ShieldCheck, Zap, Video, Plus
} from 'lucide-react'
import { toast } from 'sonner'
import { formatDistanceToNow } from 'date-fns'
import AdminLayout from '../../components/Layout/AdminLayout'
import { scanPlate, getAccessLogs, getOffices, createVisitorPass } from '../../api/scanning'
// Reuse the same styles as the security dashboard
import './EntryManagement.css'

// ─── Constants ────────────────────────────────────────────────────────────────

const SCAN_INTERVAL_MS = 2000
const COOLDOWN_MS = 5000


const STATUS_META = {
  authorized: { label: 'Approved for Entry', Icon: CheckCircle, cls: 'authorized', logCls: 'authorized' },
  wrong_day: { label: 'Wrong Schedule Day', Icon: XCircle, cls: 'wrong_day', logCls: 'wrong_day' },
  denied: { label: 'Entry Denied', Icon: XCircle, cls: 'denied', logCls: 'denied' },
  pending: { label: 'Awaiting Approval', Icon: Clock, cls: 'pending', logCls: 'pending' },
  unknown: { label: 'Visitor / Unregistered', Icon: HelpCircle, cls: 'visitor', logCls: 'visitor' },
  no_pass: { label: 'No Visitor Pass', Icon: AlertTriangle, cls: 'visitor', logCls: 'visitor' },
}

function getMeta(status) {
  return STATUS_META[status] ?? STATUS_META.unknown
}

function timeAgo(ts) {
  try { return formatDistanceToNow(new Date(ts), { addSuffix: true }) }
  catch { return '' }
}

// ─── Visitor Pass Modal ────────────────────────────────────────────────────────

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
    <div className="sd-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="sd-modal">
        <div className="sd-modal-head">
          <span className="sd-modal-title"><UserPlus size={17} /> Create Visitor Pass</span>
          <button className="sd-modal-close" onClick={onClose}><X size={15} /></button>
        </div>
        <form onSubmit={handleSubmit}>
          <div className="sd-modal-body">
            <div className="sd-field">
              <label className="sd-label">License Plate</label>
              <input className="sd-input" value={plate} readOnly />
            </div>
            <div className="sd-field">
              <label className="sd-label">Destination Office</label>
              <select className="sd-select" value={officeId} onChange={(e) => setOfficeId(e.target.value)} required>
                <option value="">Select office…</option>
                {offices.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
              </select>
            </div>
            <div className="sd-field">
              <label className="sd-label">Purpose of Visit</label>
              <textarea
                className="sd-textarea"
                placeholder="e.g. Enrollment inquiry, document pick-up…"
                value={purpose}
                onChange={(e) => setPurpose(e.target.value)}
                required
              />
            </div>
          </div>
          <div className="sd-modal-foot">
            <button type="button" className="sd-btn sd-btn-secondary" onClick={onClose}>Cancel</button>
            <button type="submit" className="sd-btn sd-btn-primary" disabled={loading}>
              {loading ? <><div className="sd-spinner" /> Creating…</> : 'Create Pass'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ─── Result Card ──────────────────────────────────────────────────────────────

function ResultCard({ result, offices, onPassCreated }) {
  const [showModal, setShowModal] = useState(false)

  if (!result) {
    return (
      <div className="sd-card sd-result">
        <div className="sd-result-banner idle">
          <div className="sd-result-icon idle"><ScanLine size={20} /></div>
          <div className="sd-result-text">
            <p className="sd-result-status" style={{ color: '#9BA3BF' }}>Awaiting scan</p>
            <p className="sd-result-plate" style={{ color: '#C8CCDE', fontSize: 15, letterSpacing: 1 }}>— — — — —</p>
          </div>
        </div>
        <p className="sd-idle-hint">Point the camera at a license plate and press Scan Plate.</p>
      </div>
    )
  }

  const { Icon, label, cls } = getMeta(result.status)
  const owner = result.vehicle?.owner
  const isVisitor = result.status === 'unknown' || result.status === 'no_pass'

  return (
    <>
      <div className={`sd-card sd-result ${cls}`}>
        <div className={`sd-result-banner ${cls}`}>
          <div className="sd-result-icon"><Icon size={20} /></div>
          <div className="sd-result-text">
            <p className="sd-result-status">{label}</p>
            <p className="sd-result-plate">{result.plate_number || '—'}</p>
          </div>
        </div>
        <div className="sd-result-body">
          <p className="sd-result-msg">{result.message}</p>
          {owner && (
            <div className="sd-result-rows">
              {owner.full_name && (
                <div className="sd-result-row">
                  <span className="sd-result-row-label">Owner</span>
                  <span className="sd-result-row-value">{owner.full_name}</span>
                </div>
              )}
              {owner.owner_type && (
                <div className="sd-result-row">
                  <span className="sd-result-row-label">Type</span>
                  <span className="sd-result-row-value" style={{ textTransform: 'capitalize' }}>
                    {owner.owner_type.replace('_', ' ')}
                  </span>
                </div>
              )}
              {owner.schedule && (
                <div className="sd-result-row">
                  <span className="sd-result-row-label">Schedule</span>
                  <span className="sd-result-row-value">{owner.schedule}</span>
                </div>
              )}
              {result.has_violations && (
                <div className="sd-result-row">
                  <span className="sd-result-row-label">Violations</span>
                  <span className="sd-violation-pill">
                    <AlertTriangle size={10} /> Unresolved violations
                  </span>
                </div>
              )}
            </div>
          )}
          {isVisitor && (
            <button
              className="sd-btn sd-btn-secondary"
              style={{ width: '100%', marginTop: 4 }}
              onClick={() => setShowModal(true)}
            >
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

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function EntryManagement() {
  const [mode, setMode] = useState('camera')
  const [cameraOn, setCameraOn] = useState(false)
  const [uploadFile, setUploadFile] = useState(null)
  const [dragOver, setDragOver] = useState(false)
  const [scanning, setScanning] = useState(false)
  const [cooldown, setCooldown] = useState(false)
  const [flash, setFlash] = useState(false)
  const [result, setResult] = useState(null)
  const [bbox, setBbox] = useState(null)
  const [logs, setLogs] = useState([])
  const [offices, setOffices] = useState([])

  const [cameras, setCameras] = useState([{ id: 1, name: 'Main Gate - Front' }])
  const [activeCamId, setActiveCamId] = useState(1)

  const webcamRefs = useRef({})
  const fileInputRef = useRef(null)
  const intervalRef = useRef(null)
  const scanningRef = useRef(false)
  const cooldownRef = useRef(false)

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

  useEffect(() => { scanningRef.current = scanning }, [scanning])
  useEffect(() => { cooldownRef.current = cooldown }, [cooldown])

  useEffect(() => {
    getAccessLogs({ limit: 20 }).then((r) => setLogs(r.data?.results ?? r.data ?? [])).catch(() => { })
    getOffices().then((r) => setOffices(r.data?.results ?? r.data ?? [])).catch(() => { })
  }, [])

  const handleScanSuccess = useCallback((data) => {
    setResult(data)
    setBbox(data.bbox || null)
    setLogs((prev) => [
      { id: Date.now(), plate_number: data.plate_number, status: data.status, scanned_at: new Date().toISOString() },
      ...prev,
    ].slice(0, 20))
    setCooldown(true)
    cooldownRef.current = true
    setTimeout(() => {
      setCooldown(false)
      cooldownRef.current = false
    }, COOLDOWN_MS)
  }, [])

  const doScan = useCallback(async (blob) => {
    setScanning(true)
    scanningRef.current = true
    setFlash(true)
    setTimeout(() => setFlash(false), 450)
    
    try {
      if (blob) {
        const { data } = await scanPlate(blob)
        if (data?.plate_number) handleScanSuccess(data)
      } else {
        // Try to scan from all active cameras
        let foundResult = null
        for (const cam of cameras) {
          const ref = webcamRefs.current[cam.id]
          if (!ref) continue
          const imageSrc = ref.getScreenshot()
          if (!imageSrc) continue
          try {
            const imgBlob = await fetch(imageSrc).then((r) => r.blob())
            const { data } = await scanPlate(imgBlob)
            if (data?.plate_number) {
              foundResult = data
              break
            }
          } catch {
            // silently ignore errors per camera
          }
        }
        if (foundResult) handleScanSuccess(foundResult)
      }
    } catch {
      // Silently ignore if no plate found in manual upload
    } finally {
      setScanning(false)
      scanningRef.current = false
    }
  }, [cameras, handleScanSuccess])

  // Auto-scan loop
  useEffect(() => {
    if (cameraOn && mode === 'camera') {
      intervalRef.current = setInterval(async () => {
        if (scanningRef.current || cooldownRef.current) return
        doScan(null)
      }, SCAN_INTERVAL_MS)
    }
    return () => { clearInterval(intervalRef.current); intervalRef.current = null }
  }, [cameraOn, mode, doScan])

  const stopCamera = () => {
    setCameraOn(false)
    setResult(null)
    setBbox(null)
    setCooldown(false)
  }

  const handleUploadScan = useCallback(async () => {
    if (!uploadFile) return
    doScan(uploadFile.file)
  }, [uploadFile, doScan])

  const handleFileChange = (file) => {
    if (!file || !file.type.startsWith('image/')) { toast.error('Please select an image file.'); return }
    setUploadFile({ file, url: URL.createObjectURL(file) })
    setResult(null)
    setBbox(null)
  }

  const handleDrop = (e) => {
    e.preventDefault()
    setDragOver(false)
    handleFileChange(e.dataTransfer.files?.[0])
  }

  const resetUpload = () => {
    if (uploadFile?.url) URL.revokeObjectURL(uploadFile.url)
    setUploadFile(null)
    setResult(null)
    setBbox(null)
  }

  const handlePassCreated = () => {
    getAccessLogs({ limit: 20 }).then((r) => setLogs(r.data?.results ?? r.data ?? [])).catch(() => { })
  }


  return (
    <AdminLayout>
      <div className="sd-page">

        {/* Header */}
        <div className="sd-header">
          <div>
            <h1 className="sd-title">Vehicle Entry Management</h1>
            <p className="sd-subtitle">
              Scan license plates using the camera — entry is decided automatically based on registration and schedule.
            </p>
          </div>
          <div className="sd-live-badge">
            <span className="sd-live-dot" /> LIVE
          </div>
        </div>

        {/* Main grid */}
        <div className="sd-grid">

          {/* Camera / Upload card */}
          <div className="sd-card">
            <div className="sd-card-head">
              <span className="sd-card-label">
                <Camera size={15} />
                {mode === 'camera' ? 'Live Camera Feed' : 'Upload Plate Image'}
              </span>
              <div className="sd-mode-toggle">
                <button className={`sd-mode-btn ${mode === 'camera' ? 'active' : ''}`} onClick={() => { setMode('camera'); setResult(null); setBbox(null) }}>Camera</button>
                <button className={`sd-mode-btn ${mode === 'upload' ? 'active' : ''}`} onClick={() => { setMode('upload'); stopCamera(); setResult(null); setBbox(null) }}>Upload</button>
              </div>
            </div>

            {/* Viewport */}
            {mode === 'camera' ? (
              <div className="sd-viewport" style={{ background: cameraOn ? '#1A1D2E' : '#08090F', padding: cameraOn ? '10px 1.25rem' : 0 }}>
                {cameraOn ? (
                  <div className="sd-multi-cam-container">
                    {/* Primary Camera */}
                    <div className="sd-primary-cam">
                      {cameras.map(cam => (
                        <div key={`primary-${cam.id}`} style={{ display: activeCamId === cam.id ? 'block' : 'none', width: '100%', height: '100%' }}>
                          <Webcam
                            ref={(el) => webcamRefs.current[cam.id] = el}
                            audio={false}
                            screenshotFormat="image/jpeg"
                            screenshotQuality={0.95}
                            className="sd-video"
                            videoConstraints={{ facingMode: 'environment' }}
                          />
                        </div>
                      ))}
                      <div className="sd-scan-frame">
                        <div className="sd-scan-bracket">
                          <div className="sd-scan-inner" />
                          {!cooldown && <div className="sd-scan-line" />}
                        </div>
                      </div>
                      {flash && <div className="sd-flash" />}
                      
                      {/* Bounding Box overlay */}
                      {bbox && !scanning && (
                        <div 
                          className="sd-bounding-box"
                          style={{
                            left: `${bbox.x * 100}%`,
                            top: `${bbox.y * 100}%`,
                            width: `${bbox.width * 100}%`,
                            height: `${bbox.height * 100}%`,
                          }}
                        />
                      )}
                      
                      <div style={{ position: 'absolute', top: 10, left: 10, background: 'rgba(0,0,0,0.6)', color: 'white', padding: '4px 10px', borderRadius: 6, fontSize: 12, fontWeight: 600 }}>
                        {cameras.find(c => c.id === activeCamId)?.name}
                      </div>
                    </div>

                    {/* Thumbnails */}
                    <div className="sd-cam-thumbnails">
                      {cameras.map(cam => (
                        <div key={`thumb-${cam.id}`} className={`sd-cam-thumb ${activeCamId === cam.id ? 'active' : ''}`} onClick={() => setActiveCamId(cam.id)}>
                          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: activeCamId === cam.id ? '#60A5FA' : '#5A5F72' }}>
                            <Video size={24} />
                          </div>
                          <div className="sd-cam-thumb-label">{cam.name}</div>
                          {cameras.length > 1 && (
                            <div className="sd-cam-thumb-actions">
                              <button className="sd-cam-delete" onClick={(e) => { e.stopPropagation(); removeCamera(cam.id) }} title="Remove angle">
                                <X size={12} />
                              </button>
                            </div>
                          )}
                        </div>
                      ))}
                      {cameras.length < 4 && (
                        <div className="sd-cam-thumb" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#2A304D', border: '1px dashed #4A5070', cursor: 'pointer' }} onClick={addCamera} title="Add another angle">
                          <Plus size={20} color="#9BA3BF" />
                        </div>
                      )}
                    </div>
                  </div>
                ) : (
                  <div className="sd-cam-off">
                    <CameraOff size={52} />
                    <p>Camera is off — press Start Camera to begin</p>
                  </div>
                )}
              </div>
            ) : (
              uploadFile ? (
                <div className="sd-upload-preview">
                  <img src={uploadFile.url} alt="Plate capture" style={{ width: '100%', height: '100%', objectFit: 'contain' }} />
                  {flash && <div className="sd-flash" />}
                  {/* Bounding Box overlay */}
                  {bbox && !scanning && (
                    <div 
                      className="sd-bounding-box"
                      style={{
                        left: `${bbox.x * 100}%`,
                        top: `${bbox.y * 100}%`,
                        width: `${bbox.width * 100}%`,
                        height: `${bbox.height * 100}%`,
                      }}
                    />
                  )}
                </div>
              ) : (
                <div
                  className={`sd-upload-zone ${dragOver ? 'drag-over' : ''}`}
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
            <div className="sd-controls">
              {mode === 'camera' ? (
                cameraOn ? (
                  <>
                    <div className={`sd-autoscan-status ${cooldown ? 'cooldown' : ''}`}>
                      {cooldown
                        ? <><Clock size={13} /> Cooldown…</>
                        : scanning
                          ? <><div className="sd-spinner" style={{ borderTopColor: '#065F46', borderColor: 'rgba(6,95,70,.2)' }} /> Scanning…</>
                          : <><Zap size={13} /> Auto-scanning</>
                      }
                    </div>
                    <button id="btn-stop-camera" className="sd-btn sd-btn-danger" onClick={stopCamera}>
                      <CameraOff size={15} /> Stop
                    </button>
                  </>
                ) : (
                  <button id="btn-start-camera" className="sd-btn sd-btn-primary sd-btn-lg" onClick={() => setCameraOn(true)}>
                    <Camera size={17} /> Start Camera
                  </button>
                )
              ) : (
                uploadFile ? (
                  <>
                    <button id="btn-upload-scan" className="sd-btn sd-btn-primary sd-btn-lg" onClick={handleUploadScan} disabled={scanning}>
                      {scanning ? <><div className="sd-spinner" /> Scanning…</> : <><ScanLine size={17} /> Scan Plate</>}
                    </button>
                    <button id="btn-upload-reset" className="sd-btn sd-btn-secondary sd-btn-icon" onClick={resetUpload} title="Choose a different image" disabled={scanning}>
                      <RotateCcw size={15} />
                    </button>
                  </>
                ) : (
                  <button id="btn-upload-choose" className="sd-btn sd-btn-secondary sd-btn-lg" onClick={() => fileInputRef.current?.click()}>
                    <Upload size={15} /> Choose Image
                  </button>
                )
              )}
            </div>
          </div>

          {/* Right panel */}
          <div className="sd-right">

            <ResultCard result={result} offices={offices} onPassCreated={handlePassCreated} />

            {/* Entry rules */}
            <div className="sd-card sd-rules">
              <div className="sd-card-head">
                <span className="sd-card-label"><ShieldCheck size={14} /> Entry Rules</span>
              </div>
              <div className="sd-rules-body">
                <div className="sd-rule-row">
                  <span className="sd-rule-dot green" />
                  <span className="sd-rule-text"><strong>Employees</strong> — allowed every day, anytime</span>
                </div>
                <div className="sd-rule-row">
                  <span className="sd-rule-dot blue" />
                  <span className="sd-rule-text"><strong>Students (MWF)</strong> — Mon, Wed, Fri only</span>
                </div>
                <div className="sd-rule-row">
                  <span className="sd-rule-dot blue" />
                  <span className="sd-rule-text"><strong>Students (TTHS)</strong> — Tue, Thu, Sat only</span>
                </div>
                <div className="sd-rule-row">
                  <span className="sd-rule-dot purple" />
                  <span className="sd-rule-text"><strong>Visitors</strong> — guard creates pass; destination office must confirm</span>
                </div>
              </div>
            </div>

            {/* Recent scans */}
            <div className="sd-card">
              <div className="sd-card-head">
                <span className="sd-card-label"><ClipboardList size={14} /> Recent Scans</span>
                <span className="sd-logs-count">{logs.length}</span>
              </div>
              {logs.length === 0 ? (
                <p className="sd-log-empty">No scans yet today.</p>
              ) : (
                <div className="sd-log-list">
                  {logs.map((log, i) => {
                    const m = getMeta(log.status)
                    return (
                      <div key={log.id ?? i} className="sd-log-item">
                        <span className={`sd-log-dot ${m.logCls}`} />
                        <span className="sd-log-plate">{log.plate_number}</span>
                        <span className={`sd-log-badge ${m.logCls}`}>{m.label}</span>
                        <span className="sd-log-time">{timeAgo(log.scanned_at)}</span>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>

          </div>
        </div>
      </div>
    </AdminLayout>
  )
}
