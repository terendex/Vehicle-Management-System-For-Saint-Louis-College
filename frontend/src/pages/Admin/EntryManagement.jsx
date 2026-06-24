import { useState, useRef, useCallback, useEffect } from 'react'
import Webcam from 'react-webcam'
import {
  Camera, CameraOff, ScanLine, Upload, RotateCcw,
  CheckCircle, XCircle, Clock, HelpCircle, AlertTriangle,
  ClipboardList, UserPlus, X, ShieldCheck, Zap, Video, Plus, Wifi, Link2
} from 'lucide-react'
import { toast } from 'sonner'
import { formatDistanceToNow } from 'date-fns'
import AdminLayout from '../../components/Layout/AdminLayout'
import { getAccessLogs, getOffices, createVisitorPass, scanPlate } from '../../api/scanning'
import { getRuleConstraints } from '../../api/vehicles'
import { useScanStream } from '../../hooks/useScanStream'
import { useMultiRtspStream } from '../../hooks/useMultiRtspStream'
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
  cooldown: { label: 'Recently Scanned', Icon: Clock, cls: 'pending', logCls: 'pending' },
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

  const [webcams, setCameras] = useState([{ id: 1, name: 'Main Gate - Front' }])
  const [activeCamId, setActiveCamId] = useState(1)
  const [rtspAddName, setRtspAddName] = useState('')
  const [rtspAddUrl,  setRtspAddUrl]  = useState('')

  const RTSP_LS_KEY = 'rtsp_cams_admin_entry'

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

  const { scanning: wsScanning, connected: wsConnected, results: wsResults, flash: flashState, videoRef, canvasRef, activeTracks } = useScanStream(
    getToken(),
    cameraOn && mode === 'camera',
  )

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
  } = useMultiRtspStream(getToken())

  const addWebcam = () => {
    if (webcams.length >= 4) { toast.error('Maximum of 4 cameras allowed.'); return }
    const id = Date.now()
    setCameras(prev => [...prev, { id, name: `Angle ${prev.length + 1}` }])
  }

  const removeWebcam = (id) => {
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

  // When RTSP stream returns results, update the UI
  useEffect(() => {
    if (rtspResults?.length > 0) handleScanSuccess(rtspResults)
  }, [rtspResults, handleScanSuccess])

  // Load saved RTSP cameras on mount
  useEffect(() => {
    const saved = JSON.parse(localStorage.getItem(RTSP_LS_KEY) || '[]')
    saved.forEach(c => addRtspCamera(c.name, c.url))
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

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

  const handleRtspAdd = () => {
    if (!rtspAddUrl.trim()) return
    const name = rtspAddName.trim()
    const url  = rtspAddUrl.trim()
    addRtspCamera(name, url)
    const saved = JSON.parse(localStorage.getItem(RTSP_LS_KEY) || '[]')
    saved.push({ name: name || `Camera ${saved.length + 1}`, url })
    localStorage.setItem(RTSP_LS_KEY, JSON.stringify(saved))
    setRtspAddName('')
    setRtspAddUrl('')
  }

  const handleRtspDisconnectAll = () => {
    disconnectAllRtsp()
    localStorage.removeItem(RTSP_LS_KEY)
    setResult(null)
    setBbox(null)
  }

  const handleRemoveRtspCam = (camId) => {
    const cam = rtspCameras.find(c => c.id === camId)
    removeRtspCamera(camId)
    if (cam) {
      const saved = JSON.parse(localStorage.getItem(RTSP_LS_KEY) || '[]')
      localStorage.setItem(RTSP_LS_KEY, JSON.stringify(saved.filter(s => s.url !== cam.url)))
    }
  }

  const stopAllAndSwitchMode = (nextMode) => {
    if (nextMode !== 'camera') { setCameraOn(false) }
    if (nextMode !== 'rtsp')   { disconnectAllRtsp(); setResult(null); setBbox(null) }
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
          {cameraOn ? (
            <div className={`em-live-badge ${wsConnected ? '' : 'connecting'}`}>
              <span className="em-live-dot" />
              {wsConnected ? 'LIVE' : 'CONNECTING…'}
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
                {mode === 'camera' ? 'Live Camera Feed' : mode === 'rtsp' ? 'IP Camera (RTSP)' : 'Upload Plate Image'}
              </span>
              <div className="em-mode-toggle">
                <button className={`em-mode-btn ${mode === 'camera' ? 'active' : ''}`} onClick={() => { stopAllAndSwitchMode('camera'); setMode('camera') }}>Camera</button>
                <button className={`em-mode-btn ${mode === 'rtsp' ? 'active' : ''}`} onClick={() => { stopAllAndSwitchMode('rtsp'); setMode('rtsp') }}><Wifi size={12} style={{ marginRight: 4 }} />RTSP {rtspCameras.length > 0 && `(${rtspCameras.length})`}</button>
                <button className={`em-mode-btn ${mode === 'upload' ? 'active' : ''}`} onClick={() => { stopAllAndSwitchMode('upload'); resetUpload(); setMode('upload') }}>Upload</button>
              </div>
            </div>

            {/* Viewport */}
            {mode === 'camera' ? (
              <div className="em-viewport" style={{ background: cameraOn ? '#1A1D2E' : '#08090F', padding: cameraOn ? '10px 1.25rem' : 0, minHeight: cameraOn ? 340 : undefined }}>
                {cameraOn ? (
                  <div className="em-multi-cam-container">
                    {/* Primary Camera */}
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

                      {/* Canvas overlay — 60fps smooth bounding boxes with labels */}
                      <canvas
                        ref={canvasRef}
                        style={{
                          position: 'absolute', top: 0, left: 0,
                          width: '100%', height: '100%',
                          pointerEvents: 'none', zIndex: 10,
                        }}
                      />

                      {/* CSS fallback bounding boxes from live WebSocket tracks */}
                      {activeTracks.map((track) => (
                        <div
                          key={`track-${track.track_id}`}
                          className="em-bounding-box"
                          style={{
                            left:   `${track.bbox[0] * 100}%`,
                            top:    `${track.bbox[1] * 100}%`,
                            width:  `${(track.bbox[2] - track.bbox[0]) * 100}%`,
                            height: `${(track.bbox[3] - track.bbox[1]) * 100}%`,
                          }}
                        />
                      ))}

                      <div className="em-scan-frame">
                          <div className="em-scan-inner" />
                          {!wsScanning && <div className="em-scan-line" />}
                      </div>
                      {flashState && <div className="em-flash" />}

                      <div style={{ position: 'absolute', top: 12, left: 12, background: 'rgba(0,0,0,0.6)', color: 'white', padding: '4px 10px', borderRadius: 6, fontSize: 11, fontWeight: 600 }}>
                        {webcams.find(c => c.id === activeCamId)?.name}
                      </div>
                    </div>

                    {/* Thumbnails */}
                    <div className="em-cam-thumbnails">
                      {webcams.map(cam => (
                        <div key={`thumb-${cam.id}`} className={`em-cam-thumb ${activeCamId === cam.id ? 'active' : ''}`} onClick={() => setActiveCamId(cam.id)}>
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
                        <div className="em-cam-thumb" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#2A304D', border: '1px dashed #4A5070', cursor: 'pointer' }} onClick={addWebcam} title="Add another angle">
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
                <div className="em-viewport" style={{ background: '#0d1117', minHeight: 340, position: 'relative' }}>
                  {rtspCameras.length > 0 ? (
                    <div style={{ position: 'relative', width: '100%', minHeight: 300 }}>
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
                      {rtspFlash && <div className="em-flash" style={{ position: 'absolute', inset: 0 }} />}
                      {rtspActiveCam && !rtspActiveCam.streamConnected && rtspActiveCam.wsActive && (
                        <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.7)', gap: 12, pointerEvents: 'none' }}>
                          <div className="em-spinner" style={{ width: 36, height: 36, borderWidth: 3, borderTopColor: '#60a5fa', borderColor: 'rgba(96,165,250,0.15)' }} />
                          <p style={{ color: '#93c5fd', fontSize: 13, margin: 0 }}>{rtspActiveCam.statusMsg || 'Connecting…'}</p>
                        </div>
                      )}
                      <div style={{ position: 'absolute', top: 12, left: 12, background: 'rgba(0,0,0,0.65)', color: '#fff', padding: '4px 10px', borderRadius: 6, fontSize: 11, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 5, pointerEvents: 'none' }}>
                        <span style={{ width: 7, height: 7, borderRadius: '50%', background: rtspActiveCam?.streamConnected ? '#22c55e' : '#f59e0b', display: 'inline-block' }} />
                        {rtspActiveCam?.name || 'IP Camera'}
                      </div>
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

                {/* Camera thumbnail strip */}
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
                  {flashState && <div className="em-flash" />}
                  {/* Bounding Box overlays */}
                  {bbox && bbox.length > 0 && bbox.map((b, i) => {
                    const isAbsolute = b.x > 1 || b.y > 1;
                    const x = isAbsolute ? b.x / 640 : b.x;
                    const y = isAbsolute ? b.y / 480 : b.y;
                    const w = isAbsolute ? b.width / 640 : b.width;
                    const h = isAbsolute ? b.height / 480 : b.height;
                    return (
                      <div
                        key={`bbox-up-${i}`}
                        className="em-bounding-box"
                        style={{
                          left: `${x * 100}%`,
                          top: `${y * 100}%`,
                          width: `${w * 100}%`,
                          height: `${h * 100}%`,
                        }}
                      />
                    );
                  })}
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
              ) : mode === 'rtsp' ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8, width: '100%', alignItems: 'stretch' }}>
                  <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                    <input
                      className="em-input"
                      style={{ width: 120, flexShrink: 0, fontSize: 12, padding: '7px 10px' }}
                      placeholder="Name (optional)"
                      value={rtspAddName}
                      onChange={e => setRtspAddName(e.target.value)}
                    />
                    <input
                      className="em-input"
                      style={{ flex: 1, fontFamily: 'monospace', fontSize: 12, padding: '7px 10px' }}
                      placeholder="rtsp://user:pass@192.168.x.x:554/stream1"
                      value={rtspAddUrl}
                      onChange={e => setRtspAddUrl(e.target.value)}
                      onKeyDown={e => { if (e.key === 'Enter') handleRtspAdd() }}
                    />
                    <button
                      className="em-btn em-btn-primary"
                      onClick={handleRtspAdd}
                      disabled={!rtspAddUrl.trim()}
                    >
                      <Wifi size={14} /> Add
                    </button>
                  </div>
                  {rtspCameras.length > 0 && (
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      <div className={`em-autoscan-status ${rtspCameras.some(c => c.streamConnected) ? 'scanning' : ''}`} style={{ flex: 1 }}>
                        {rtspCameras.some(c => c.streamConnected)
                          ? <><Zap size={13} /> {rtspCameras.filter(c => c.streamConnected).length}/{rtspCameras.length} camera{rtspCameras.length !== 1 ? 's' : ''} live</>
                          : <><div className="em-spinner" style={{ borderTopColor: '#3b82f6', borderColor: 'rgba(59,130,246,.2)' }} /> Connecting…</>}
                      </div>
                      <button className="em-btn em-btn-danger" onClick={handleRtspDisconnectAll}>
                        <Link2 size={14} /> Disconnect All
                      </button>
                    </div>
                  )}
                </div>
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
