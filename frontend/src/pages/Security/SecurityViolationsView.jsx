import { useState, useEffect, useCallback, useRef } from 'react'
import Webcam from 'react-webcam'
import {
  AlertTriangle, CheckCircle, RefreshCw, Camera, CameraOff,
  ScanLine, CalendarDays, X
} from 'lucide-react'
import { toast } from 'sonner'
import SecurityLayout from '../../components/Layout/SecurityLayout'
import { getGuardViolations, createViolation } from '../../api/violations'
import { scanPlate } from '../../api/scanning'
import useAuthStore from '../../stores/authStore'
import './SecurityViolationsView.css'

const TYPE_LABELS = {
  expired_registration: 'Expired Registration',
  unauthorized:         'Unauthorized',
  other:                'Other',
}

const TYPE_COLORS = {
  expired_registration: '#D97706',
  unauthorized:         '#DC2626',
  other:                '#6B7280',
}

function ViolationRow({ v }) {
  const color = TYPE_COLORS[v.violation_type] || '#6B7280'
  const label = TYPE_LABELS[v.violation_type] || v.violation_type
  const time  = new Date(v.issued_at).toLocaleString('en-US', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
  })

  return (
    <div className="sv-row">
      <div className="sv-row-left">
        <span className="sv-type-dot" style={{ background: color }} />
        <div className="sv-row-info">
          <span className="sv-plate">{v.plate_number || '—'}</span>
          <span className="sv-type-label" style={{ color }}>{label}</span>
          {v.owner_name && <span className="sv-owner">{v.owner_name}</span>}
          {v.notes && <span className="sv-notes">{v.notes}</span>}
        </div>
      </div>
      <div className="sv-row-right">
        {v.is_resolved ? (
          <span className="sv-badge sv-resolved"><CheckCircle size={11} /> Resolved</span>
        ) : (
          <span className="sv-badge sv-open">Open</span>
        )}
        {v.evidence_url && (
          <a href={v.evidence_url} target="_blank" rel="noopener noreferrer" className="sv-evidence-link">
            <Camera size={12} /> Evidence
          </a>
        )}
        <span className="sv-time">{time}</span>
        <span className="sv-fine">₱{Number(v.fine_amount).toFixed(2)}</span>
      </div>
    </div>
  )
}

// ─── Issue Violation — two-step: scan plate → issue ───────────────────────────
function IssueViolationForm({ onIssued }) {
  const [type, setType]       = useState('unauthorized')
  const [cameraOn, setCameraOn] = useState(false)
  const [detecting, setDetecting] = useState(false)
  const [issuing, setIssuing]   = useState(false)
  // After plate detected: { plate, preview, file }
  const [scanned, setScanned]   = useState(null)
  const webcamRef               = useRef(null)

  const handleDetect = async () => {
    if (!webcamRef.current) return
    const dataUrl = webcamRef.current.getScreenshot()
    if (!dataUrl) { toast.error('Failed to capture frame.'); return }
    setDetecting(true)
    try {
      const blob = await (await fetch(dataUrl)).blob()
      const file = new File([blob], `violation_${Date.now()}.jpg`, { type: 'image/jpeg' })
      const res  = await scanPlate(file)
      const results = res.data?.results ?? res.data ?? []
      const plate = results[0]?.plate_number
      if (!plate) { toast.error('No plate detected — adjust the camera and try again.'); return }
      setCameraOn(false)
      setScanned({ plate: plate.toUpperCase(), preview: dataUrl, file })
    } catch {
      toast.error('Scan failed. Try again.')
    } finally {
      setDetecting(false)
    }
  }

  const handleIssue = async () => {
    if (!scanned) return
    setIssuing(true)
    try {
      await createViolation({
        plate_number:   scanned.plate,
        violation_type: type,
        notes:          `Gate scan violation`,
        evidence:       scanned.file,
      })
      toast.success(`Violation issued for ${scanned.plate}.`)
      setScanned(null)
      onIssued()
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Failed to issue violation.')
    } finally {
      setIssuing(false)
    }
  }

  const reset = () => { setScanned(null); setCameraOn(false) }

  return (
    <div className="sv-issue-form">

      {/* Step 1 — detect plate */}
      {!scanned && (
        <>
          <div className="sv-cam-viewport">
            {cameraOn ? (
              <Webcam
                ref={webcamRef}
                audio={false}
                screenshotFormat="image/jpeg"
                screenshotQuality={0.95}
                videoConstraints={{ facingMode: { ideal: 'environment' }, width: { ideal: 1280 } }}
                onUserMediaError={() => { toast.error('Could not access camera.'); setCameraOn(false) }}
                style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
              />
            ) : (
              <div className="sv-cam-off">
                <Camera size={36} color="#9CA3AF" />
                <p>Point the camera at the license plate</p>
              </div>
            )}
          </div>

          <div className="sv-cam-controls">
            <button type="button" className="sv-cam-toggle-btn"
              onClick={() => setCameraOn(v => !v)} disabled={detecting}>
              {cameraOn ? <><CameraOff size={14} /> Stop</> : <><Camera size={14} /> Start Camera</>}
            </button>
            {cameraOn && (
              <button type="button" className="sv-submit-btn" onClick={handleDetect}
                disabled={detecting} style={{ flex: 1 }}>
                {detecting
                  ? <><div className="sv-spinner sv-spinner-sm" /> Detecting…</>
                  : <><ScanLine size={15} /> Scan Plate</>}
              </button>
            )}
          </div>
        </>
      )}

      {/* Step 2 — confirm and issue */}
      {scanned && (
        <>
          <div className="sv-cam-viewport">
            <div className="sv-cam-result">
              <img src={scanned.preview} alt="captured" className="sv-cam-result-img" />
              <div className="sv-cam-result-badge">
                <ScanLine size={13} /> {scanned.plate}
              </div>
            </div>
          </div>

          <div className="sv-field">
            <label className="sv-label">Violation Type</label>
            <select className="sv-select" value={type} onChange={e => setType(e.target.value)} disabled={issuing}>
              <option value="unauthorized">Unauthorized Entry</option>
              <option value="expired_registration">Expired Registration</option>
              <option value="other">Other</option>
            </select>
          </div>

          <div className="sv-cam-controls">
            <button type="button" className="sv-cam-toggle-btn" onClick={reset} disabled={issuing}>
              <Camera size={14} /> Re-scan
            </button>
            <button type="button" className="sv-submit-btn" onClick={handleIssue}
              disabled={issuing} style={{ flex: 1 }}>
              {issuing
                ? <><div className="sv-spinner sv-spinner-sm" /> Issuing…</>
                : <><AlertTriangle size={15} /> Issue Violation</>}
            </button>
          </div>
        </>
      )}
    </div>
  )
}

// ─── Main Page ─────────────────────────────────────────────────────────────────
export default function SecurityViolationsView() {
  const { user } = useAuthStore()
  const today = new Date().toISOString().split('T')[0]

  const [violations, setViolations] = useState([])
  const [loading, setLoading]       = useState(true)
  const [date, setDate]             = useState(today)
  const [showForm, setShowForm]     = useState(false)

  const fetchViolations = useCallback(async () => {
    setLoading(true)
    try {
      const res = await getGuardViolations(date)
      setViolations(res.data ?? res)
    } catch {
      toast.error('Failed to load violations.')
    } finally {
      setLoading(false)
    }
  }, [date])

  useEffect(() => { fetchViolations() }, [fetchViolations])

  const totalFine = violations.reduce((s, v) => s + parseFloat(v.fine_amount || 0), 0)

  return (
    <SecurityLayout>
      <div className="sv-page">
        <div className="sv-header">
          <div>
            <h1 className="sv-title">Violations Issued</h1>
            <p className="sv-sub">
              {user?.full_name
                ? `Violations logged by ${user.full_name} (${user.user_code})`
                : 'Your issued violations'}
            </p>
          </div>
          <div className="sv-header-actions">
            <div className="sv-date-wrap">
              <CalendarDays size={14} />
              <input
                type="date"
                className="sv-date-input"
                value={date}
                max={today}
                onChange={e => setDate(e.target.value)}
              />
            </div>
            <button className="sv-refresh-btn" onClick={fetchViolations} disabled={loading} title="Refresh">
              <RefreshCw size={14} />
            </button>
            <button className="sv-issue-btn" onClick={() => setShowForm(v => !v)}>
              <AlertTriangle size={14} />
              {showForm ? 'Hide Form' : 'Issue Violation'}
            </button>
          </div>
        </div>

        {/* Issue form */}
        {showForm && (
          <div className="sv-card sv-form-card">
            <div className="sv-card-head">
              <AlertTriangle size={15} style={{ color: '#dc2626' }} />
              <span>Issue New Violation</span>
            </div>
            <IssueViolationForm onIssued={() => { fetchViolations(); setShowForm(false) }} />
          </div>
        )}

        {/* Summary */}
        <div className="sv-summary-row">
          <div className="sv-summary-chip">
            <span className="sv-summary-num">{violations.length}</span>
            <span className="sv-summary-label">Violations</span>
          </div>
          <div className="sv-summary-chip">
            <span className="sv-summary-num">{violations.filter(v => !v.is_resolved).length}</span>
            <span className="sv-summary-label">Open</span>
          </div>
          <div className="sv-summary-chip">
            <span className="sv-summary-num">₱{totalFine.toFixed(2)}</span>
            <span className="sv-summary-label">Total Fines</span>
          </div>
        </div>

        {/* List */}
        <div className="sv-card">
          <div className="sv-card-head">
            <ScanLine size={14} />
            <span>
              {date === today ? "Today's Violations" : `Violations on ${date}`}
            </span>
            <span className="sv-count">{violations.length}</span>
          </div>

          {loading ? (
            <div className="sv-loading">
              <div className="sv-spinner" />
              <p>Loading violations…</p>
            </div>
          ) : violations.length === 0 ? (
            <div className="sv-empty">
              <CheckCircle size={28} color="#16a34a" />
              <p>No violations issued {date === today ? 'today' : `on ${date}`}.</p>
            </div>
          ) : (
            <div className="sv-list">
              {violations.map(v => <ViolationRow key={v.id} v={v} />)}
            </div>
          )}
        </div>
      </div>
    </SecurityLayout>
  )
}
