import { useState, useEffect, useCallback, useRef } from 'react'
import {
  AlertTriangle, CheckCircle, RefreshCw, Camera,
  Upload, X, ScanLine, CalendarDays
} from 'lucide-react'
import { toast } from 'sonner'
import SecurityLayout from '../../components/Layout/SecurityLayout'
import { getGuardViolations, createViolation } from '../../api/violations'
import useAuthStore from '../../stores/authStore'
import './SecurityViolationsView.css'

const TYPE_LABELS = {
  no_sticker:           'No Sticker',
  expired_registration: 'Expired Registration',
  unauthorized:         'Unauthorized',
  other:                'Other',
}

const TYPE_COLORS = {
  no_sticker:           '#7C3AED',
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

// ─── Issue Violation inline form ───────────────────────────────────────────────
function IssueViolationForm({ onIssued }) {
  const [plate, setPlate]       = useState('')
  const [type, setType]         = useState('no_sticker')
  const [notes, setNotes]       = useState('')
  const [evidence, setEvidence] = useState(null)
  const [preview, setPreview]   = useState(null)
  const [loading, setLoading]   = useState(false)
  const fileRef                 = useRef(null)

  const handleFile = (file) => {
    if (!file || !file.type.startsWith('image/')) { toast.error('Please select an image file.'); return }
    setEvidence(file)
    setPreview(URL.createObjectURL(file))
  }

  const removeEvidence = () => {
    if (preview) URL.revokeObjectURL(preview)
    setEvidence(null); setPreview(null)
    if (fileRef.current) fileRef.current.value = ''
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!plate.trim()) { toast.error('Plate number is required.'); return }
    setLoading(true)
    try {
      await createViolation({
        plate_number: plate.trim().toUpperCase(),
        violation_type: type,
        notes,
        ...(evidence ? { evidence } : {}),
      })
      toast.success(`Violation issued for ${plate.trim().toUpperCase()}.`)
      setPlate(''); setNotes(''); removeEvidence()
      onIssued()
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Failed to issue violation.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <form className="sv-issue-form" onSubmit={handleSubmit}>
      <div className="sv-form-row">
        <div className="sv-field">
          <label className="sv-label">License Plate *</label>
          <input className="sv-input" value={plate} onChange={e => setPlate(e.target.value)}
            placeholder="e.g. ABC 123" required />
        </div>
        <div className="sv-field">
          <label className="sv-label">Violation Type</label>
          <select className="sv-select" value={type} onChange={e => setType(e.target.value)}>
            <option value="no_sticker">No Sticker</option>
            <option value="expired_registration">Expired Registration</option>
            <option value="unauthorized">Unauthorized Entry</option>
            <option value="other">Other</option>
          </select>
        </div>
      </div>

      <div className="sv-field">
        <label className="sv-label">Notes</label>
        <textarea className="sv-textarea" placeholder="Optional details…"
          value={notes} onChange={e => setNotes(e.target.value)} rows={2} />
      </div>

      <div className="sv-field">
        <label className="sv-label">Screenshot Evidence</label>
        {preview ? (
          <div className="sv-evidence-preview">
            <img src={preview} alt="evidence" />
            <button type="button" className="sv-remove-btn" onClick={removeEvidence}>
              <X size={12} /> Remove
            </button>
          </div>
        ) : (
          <div
            className="sv-dropzone"
            onClick={() => fileRef.current?.click()}
            onDrop={(e) => { e.preventDefault(); handleFile(e.dataTransfer.files?.[0]) }}
            onDragOver={e => e.preventDefault()}
          >
            <Upload size={20} />
            <span>Drop image or <u>click to browse</u></span>
            <input ref={fileRef} type="file" accept="image/*" style={{ display: 'none' }}
              onChange={e => handleFile(e.target.files?.[0])} />
          </div>
        )}
      </div>

      <button type="submit" className="sv-submit-btn" disabled={loading}>
        <AlertTriangle size={15} />
        {loading ? 'Issuing…' : 'Issue Violation'}
      </button>
    </form>
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
