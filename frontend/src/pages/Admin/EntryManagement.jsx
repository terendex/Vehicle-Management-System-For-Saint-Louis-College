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
import { getAccessLogs, getOffices, createVisitorPass, scanPlate } from '../../api/scanning'
import { getRuleConstraints, getVehicleTypeAccess } from '../../api/vehicles'
import { useScanStream } from '../../hooks/useScanStream'
import './EntryManagement.css'

// ─── Constants ────────────────────────────────────────────────────────────────

const SCAN_INTERVAL_MS = 500
const PLATE_COOLDOWN_MS = 3000
const LOG_LIMIT = 50

const STATUS_META = {
  authorized: { label: 'Approved for Entry', Icon: CheckCircle, cls: 'authorized', logCls: 'authorized' },
  wrong_day: { label: 'Wrong Schedule Day', Icon: XCircle, cls: 'wrong_day', logCls: 'wrong_day' },
  denied: { label: 'Entry Denied', Icon: XCircle, cls: 'denied', logCls: 'denied' },
  pending: { label: 'Awaiting Approval', Icon: Clock, cls: 'pending', logCls: 'pending' },
  unknown: { label: 'Visitor / Unregistered', Icon: HelpCircle, cls: 'visitor', logCls: 'visitor' },
  no_pass: { label: 'No Visitor Pass', Icon: AlertTriangle, cls: 'visitor', logCls: 'visitor' },
  disabled: { label: 'Access Disabled', Icon: XCircle, cls: 'denied', logCls: 'denied' },
  unreadable: { label: 'Unreadable Plate', Icon: AlertTriangle, cls: 'visitor', logCls: 'visitor' },
}

function getMeta(status) {
  return STATUS_META[status] ?? STATUS_META.unknown
}

function timeAgo(ts) {
  try { return formatDistanceToNow(new Date(ts), { addSuffix: true }) } catch { return '' }
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

// ─── Result Card ──────────────────────────────────────────────────────────────

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
        <p className="em-idle-hint">Point the camera at a license plate and press Scan Plate.</p>
      </div>
    )
  }

  const { Icon, label, cls } = getMeta(result.status)
  const owner = result.vehicle?.owner
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
            <div className="em-constraint-info" style={{ margin: '8px 0', padding: '8px 12px', borderRadius: 8, background: '#fef3c7', border: '1px solid #f59e0b', fontSize: 12, color: '#92400e', display: 'flex', alignItems: 'center', gap: 6 }}>
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
            <button
              className="em-btn em-btn-secondary"
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
  const [result, setResult] = useState(null)
  const [bbox, setBbox] = useState(null)
  const [logs, setLogs] = useState([])
  const [offices, setOffices] = useState([])
  const [rules, setRules] = useState([])
  const [loadingRules, setLoadingRules] = useState(true)
  const [vehicleTypes, setVehicleTypes] = useState([])
  const [loadingVehicles, setLoadingVehicles] = useState(true)

  const [cameras, setCameras] = useState([{ id: 1, name: 'Main Gate - Front' }])
  const [activeCamId, setActiveCamId] = useState(1)

  const webcamRefs = useRef({})
  const fileInputRef = useRef(null)
  const intervalRef = useRef(null)
  const scanningRef = useRef(false)
  const plateCooldownRef = useRef(new Set())

  const getToken = useCallback(() => {
    if (typeof localStorage !== 'undefined') {
      return localStorage.getItem('access_token') || ''
    }
    return ''
  }, [])

  const { scanning: wsScanning, results: wsResults, flash: flashState, videoRef } = useScanStream(
    getToken(),
    cameraOn && mode === 'camera',
  )

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

  const handleScanSuccess = useCallback((results) => {
    if (!results || results.length === 0) return

    const newBboxes = results.map((r) => r.bbox).filter(Boolean)
    setResult(results)
    setBbox(newBboxes)

    setLogs((prev) => {
      const now = Date.now()
      const newLogs = results
        .filter((r) => {
          const cooldowns = plateCooldownRef.current
          if (cooldowns.has(r.plate_number)) return false
          cooldowns.add(r.plate_number)
          setTimeout(() => cooldowns.delete(r.plate_number), PLATE_COOLDOWN_MS)
          return true
        })
        .map((r) => ({
          id: now + Math.random(),
          plate_number: r.plate_number,
          status: r.status,
          scanned_at: new Date().toISOString(),
        }))
      return [...newLogs, ...prev].slice(0, LOG_LIMIT)
    })
  }, [])

  // When the stream returns results, update the UI
  useEffect(() => {
    if (wsResults.length > 0) handleScanSuccess(wsResults)
  }, [wsResults, handleScanSuccess])

  // Re-open WS if token ever changes
  useEffect(() => { scanningRef.current = wsScanning }, [wsScanning])

  const doScan = useCallback(async (blob) => {
    if (scanningRef.current || !cameraOn || mode !== 'camera') return
    scanningRef.current = true
    try {
      const imageBlob = blob || (await new Promise((resolve) => {
        const capture = webcamRefs.current[activeCamId]?.getScreenshot()
        if (capture) resolve(capture)
        else resolve(null)
      }))
      if (!imageBlob) {
        toast.error('Failed to capture image')
        return
      }
      const response = await fetch(imageBlob)
      const file = await response.blob()
      const res = await scanPlate(file)
      const data = res.data?.results ?? res.data ?? []
      if (data.length) {
        handleScanSuccess(data)
      }
    } catch {
      toast.error('Scan failed')
    } finally {
      scanningRef.current = false
    }
  }, [cameraOn, mode, activeCamId, handleScanSuccess])

  // Auto-scan loop — use simple interval + REST POST for upload/manual fallback
  useEffect(() => {
    if (cameraOn && mode === 'camera') {
      intervalRef.current = setInterval(async () => {
        if (scanningRef.current) return
        doScan(null)
      }, SCAN_INTERVAL_MS)
    }
    return () => { clearInterval(intervalRef.current); intervalRef.current = null }
  }, [cameraOn, mode, doScan])

  const stopCamera = () => {
    setCameraOn(false)
    setResult(null)
    setBbox(null)
  }

  const handleUploadScan = useCallback(async () => {
    if (!uploadFile) return
    try {
      const res = await scanPlate(uploadFile.file)
      const data = res.data?.results ?? res.data ?? []
      if (data.length) {
        handleScanSuccess(data)
      }
    } catch {
      toast.error('Upload scan failed')
    }
  }, [uploadFile, handleScanSuccess])

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

  useEffect(() => {
    getAccessLogs({ limit: 20 }).then((r) => setLogs(r.data?.results ?? r.data ?? [])).catch(() => { })
    getOffices().then((r) => setOffices(r.data?.results ?? r.data ?? [])).catch(() => { })
    getRuleConstraints().then((r) => {
      const data = (r.data?.results ?? r.data ?? [])
      setRules(data.filter(rule => rule.enabled))
      setLoadingRules(false)
    }).catch(() => setLoadingRules(false))
    getVehicleTypeAccess().then((r) => {
      const data = (r.data?.results ?? r.data ?? [])
      setVehicleTypes(data.filter(v => v.enabled))
      setLoadingVehicles(false)
    }).catch(() => setLoadingVehicles(false))
  }, [])


  return (
    <AdminLayout>
      <div className="em-page">

        {/* Header */}
        <div className="em-header">
          <div>
            <h1 className="em-title">Vehicle Entry Management</h1>
            <p className="em-subtitle">
              Scan license plates using the camera — entry is decided automatically based on registration and schedule.
            </p>
          </div>
          <div className="em-live-badge">
            <span className="em-live-dot" /> LIVE
          </div>
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
                <button className={`em-mode-btn ${mode === 'camera' ? 'active' : ''}`} onClick={() => { setMode('camera'); setResult(null); setBbox(null) }}>Camera</button>
                <button className={`em-mode-btn ${mode === 'upload' ? 'active' : ''}`} onClick={() => { setMode('upload'); stopCamera(); setResult(null); setBbox(null) }}>Upload</button>
              </div>
            </div>

            {/* Viewport */}
            {mode === 'camera' ? (
              <div className="em-viewport" style={{ background: cameraOn ? '#1A1D2E' : '#08090F', padding: cameraOn ? '10px 1.25rem' : 0 }}>
                {cameraOn ? (
                  <div className="em-multi-cam-container">
                    {/* Primary Camera */}
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
                      <div className="em-scan-frame">
                        <div className="em-scan-bracket">
                          <div className="em-scan-inner" />
                          {!wsScanning && <div className="em-scan-line" />}
                        </div>
                      </div>
                      {flashState && <div className="em-flash" />}

                      {/* Bounding Box overlays */}
                      {bbox && bbox.length > 0 && bbox.map((b, i) => (
                        <div
                          key={`bbox-cam-${i}`}
                          className="em-bounding-box"
                          style={{
                            left: `${b.x * 100}%`,
                            top: `${b.y * 100}%`,
                            width: `${b.width * 100}%`,
                            height: `${b.height * 100}%`,
                          }}
                        />
                      ))}

                      <div style={{ position: 'absolute', top: 12, left: 12, background: 'rgba(0,0,0,0.6)', color: 'white', padding: '4px 10px', borderRadius: 6, fontSize: 11, fontWeight: 600 }}>
                        {cameras.find(c => c.id === activeCamId)?.name}
                      </div>
                    </div>

                    {/* Thumbnails */}
                    <div className="em-cam-thumbnails">
                      {cameras.map(cam => (
                        <div key={`thumb-${cam.id}`} className={`em-cam-thumb ${activeCamId === cam.id ? 'active' : ''}`} onClick={() => setActiveCamId(cam.id)}>
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
                        <div className="em-cam-thumb" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#2A304D', border: '1px dashed #4A5070', cursor: 'pointer' }} onClick={addCamera} title="Add another angle">
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
                  {flashState && <div className="em-flash" />}
                  {/* Bounding Box overlays */}
                  {bbox && bbox.length > 0 && bbox.map((b, i) => (
                    <div
                      key={`bbox-up-${i}`}
                      className="em-bounding-box"
                      style={{
                        left: `${b.x * 100}%`,
                        top: `${b.y * 100}%`,
                        width: `${b.width * 100}%`,
                        height: `${b.height * 100}%`,
                      }}
                    />
                  ))}
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
                    <div className="em-autoscan-status scanning">
                      {wsScanning
                        ? <><div className="em-spinner" style={{ borderTopColor: '#065F46', borderColor: 'rgba(6,95,70,.2)' }} /> Scanning…</>
                        : <><Zap size={13} /> Live Scanning</>
                      }
                    </div>
                    <button id="btn-stop-camera" className="em-btn em-btn-danger" onClick={stopCamera}>
                      <CameraOff size={15} /> Stop
                    </button>
                  </>
                ) : (
                  <button id="btn-start-camera" className="em-btn em-btn-primary em-btn-lg" onClick={() => setCameraOn(true)}>
                    <Camera size={17} /> Start Camera
                  </button>
                )
              ) : (
                uploadFile ? (
                  <>
                    <button id="btn-upload-scan" className="em-btn em-btn-primary em-btn-lg" onClick={handleUploadScan}>
                      <ScanLine size={17} /> Scan Plate
                    </button>
                    <button id="btn-upload-reset" className="em-btn em-btn-secondary em-btn-icon" onClick={resetUpload} title="Choose a different image">
                      <RotateCcw size={15} />
                    </button>
                  </>
                ) : (
                  <button id="btn-upload-choose" className="em-btn em-btn-secondary em-btn-lg" onClick={() => fileInputRef.current?.click()}>
                    <Upload size={15} /> Choose Image
                  </button>
                )
              )}
            </div>
          </div>

          {/* Right panel */}
          <div className="em-right">
            {(result && result.length > 0) ? (
              <div className="em-results-stack">
                {result.map((r, idx) => (
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
                <div style={{ marginBottom: '8px', fontSize: '10px', textTransform: 'uppercase', fontWeight: 700, color: '#6b7280', letterSpacing: '0.5px', paddingLeft: '8px', borderLeft: '2px solid #3b82f6' }}>Schedule Restrictions</div>
                {loadingRules ? (
                  <p className="em-log-empty">Loading rules…</p>
                ) : rules.length === 0 ? (
                  <p className="em-log-empty">No rules configured.</p>
                ) : (
                  rules.map((rule) => {
                    const dotColor = rule.constraint_type === 'employee' ? 'green' :
                                     rule.constraint_type === 'student' ? 'blue' : 'purple'
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

                <div style={{ marginTop: '16px', marginBottom: '8px', fontSize: '10px', textTransform: 'uppercase', fontWeight: 700, color: '#6b7280', letterSpacing: '0.5px', paddingLeft: '8px', borderLeft: '2px solid #f59e0b' }}>Vehicle Access Privileges</div>
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
                          <strong>{v.label}</strong>
                          {' — '}{v.gate}, {hoursDisplay}
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
    </AdminLayout>
  )
}
