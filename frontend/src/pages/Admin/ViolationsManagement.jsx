import { useState, useEffect, useMemo, useRef } from 'react'
import {
  AlertTriangle, CheckCircle, EyeOff, Filter,
  RotateCcw, Search, Bell, BellOff, X, Plus,
  Image, Upload, ZoomIn,
} from 'lucide-react'
import { toast } from 'sonner'
import { formatDistanceToNow, format, parseISO } from 'date-fns'
import AdminLayout from '../../components/Layout/AdminLayout'
import {
  getAllViolations, releaseViolation, unreleaseViolation, resolveViolation, createViolation,
} from '../../api/violations'
import './ViolationsManagement.css'

const TYPE_LABELS = {
  no_sticker:           'No Sticker',
  expired_registration: 'Expired Registration',
  unauthorized:         'Unauthorized Entry',
  other:                'Other',
}

const FILTER_OPTIONS = [
  { value: 'all',        label: 'All' },
  { value: 'pending',    label: 'Pending' },
  { value: 'notified',   label: 'Notified' },
  { value: 'resolved',   label: 'Resolved' },
]

const DATE_PERIODS = [
  { value: 'all',   label: 'All' },
  { value: 'day',   label: 'Today' },
  { value: 'week',  label: 'Week' },
  { value: 'month', label: 'Month' },
  { value: 'year',  label: 'Year' },
]

function getPeriodStart(period) {
  const d = new Date()
  if (period === 'day') { d.setHours(0, 0, 0, 0) }
  else if (period === 'week') {
    const dow = d.getDay()
    d.setDate(d.getDate() - (dow === 0 ? 6 : dow - 1))
    d.setHours(0, 0, 0, 0)
  } else if (period === 'month') { d.setDate(1); d.setHours(0, 0, 0, 0) }
  else if (period === 'year')  { d.setMonth(0, 1); d.setHours(0, 0, 0, 0) }
  return d
}

function timeAgo(ts) {
  try { return formatDistanceToNow(new Date(ts), { addSuffix: true }) } catch { return '' }
}

function fmtDate(ts) {
  try { return format(parseISO(ts), 'MMM d, yyyy') } catch { return '—' }
}

function FineTag({ amount }) {
  return (
    <span className="vm-fine-tag">
      ₱{parseFloat(amount).toFixed(2)}
    </span>
  )
}

// ─── Issue Violation Modal ────────────────────────────────────────────────────
function IssueViolationModal({ onClose, onCreated }) {
  const [plate, setPlate]           = useState('')
  const [type, setType]             = useState('no_sticker')
  const [notes, setNotes]           = useState('')
  const [evidence, setEvidence]     = useState(null)
  const [preview, setPreview]       = useState(null)
  const [loading, setLoading]       = useState(false)
  const fileRef                     = useRef(null)

  const handleFile = (file) => {
    if (!file || !file.type.startsWith('image/')) { toast.error('Please select an image file.'); return }
    setEvidence(file)
    setPreview(URL.createObjectURL(file))
  }

  const handleDrop = (e) => {
    e.preventDefault()
    handleFile(e.dataTransfer.files?.[0])
  }

  const removeEvidence = () => {
    if (preview) URL.revokeObjectURL(preview)
    setEvidence(null)
    setPreview(null)
    if (fileRef.current) fileRef.current.value = ''
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!plate.trim()) { toast.error('Plate number is required.'); return }
    setLoading(true)
    try {
      const { data } = await createViolation({
        plate_number:   plate.trim().toUpperCase(),
        violation_type: type,
        notes,
        ...(evidence ? { evidence } : {}),
      })
      toast.success(`Violation issued for ${data.plate_number || plate.trim().toUpperCase()}.`)
      onCreated(data)
      onClose()
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Failed to issue violation.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="vm-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="vm-modal">
        <div className="vm-modal-head">
          <span className="vm-modal-title"><AlertTriangle size={16} /> Issue Violation</span>
          <button className="vm-modal-close" onClick={onClose}><X size={15} /></button>
        </div>
        <form onSubmit={handleSubmit}>
          <div className="vm-modal-body">
            <div className="vm-field">
              <label className="vm-label">License Plate <span style={{ color: '#ef4444' }}>*</span></label>
              <input
                className="vm-input"
                placeholder="e.g. ABC 123"
                value={plate}
                onChange={e => setPlate(e.target.value)}
                required
              />
            </div>
            <div className="vm-field">
              <label className="vm-label">Violation Type</label>
              <select className="vm-select" value={type} onChange={e => setType(e.target.value)}>
                <option value="no_sticker">No Sticker</option>
                <option value="expired_registration">Expired Registration</option>
                <option value="unauthorized">Unauthorized Entry</option>
                <option value="other">Other</option>
              </select>
            </div>
            <div className="vm-field">
              <label className="vm-label">Notes</label>
              <textarea
                className="vm-textarea"
                placeholder="Optional additional details…"
                value={notes}
                onChange={e => setNotes(e.target.value)}
                rows={3}
              />
            </div>
            <div className="vm-field">
              <label className="vm-label">Screenshot Evidence</label>
              {preview ? (
                <div className="vm-evidence-preview">
                  <img src={preview} alt="evidence preview" className="vm-evidence-img" />
                  <button type="button" className="vm-evidence-remove" onClick={removeEvidence}>
                    <X size={12} /> Remove
                  </button>
                </div>
              ) : (
                <div
                  className="vm-drop-zone"
                  onDrop={handleDrop}
                  onDragOver={e => e.preventDefault()}
                  onClick={() => fileRef.current?.click()}
                >
                  <Upload size={22} className="vm-drop-icon" />
                  <p className="vm-drop-text">Drop image here or <span>click to browse</span></p>
                  <input
                    ref={fileRef}
                    type="file"
                    accept="image/*"
                    style={{ display: 'none' }}
                    onChange={e => handleFile(e.target.files?.[0])}
                  />
                </div>
              )}
            </div>
          </div>
          <div className="vm-modal-foot">
            <button type="button" className="vm-btn-cancel" onClick={onClose}>Cancel</button>
            <button type="submit" className="vm-btn-submit" disabled={loading}>
              {loading ? 'Issuing…' : 'Issue Violation'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ─── Evidence Lightbox ─────────────────────────────────────────────────────────
function EvidenceLightbox({ src, onClose }) {
  return (
    <div className="vm-overlay" onClick={onClose} style={{ zIndex: 1100 }}>
      <div style={{ position: 'relative', maxWidth: '90vw', maxHeight: '90vh' }} onClick={e => e.stopPropagation()}>
        <img src={src} alt="violation evidence" style={{ maxWidth: '90vw', maxHeight: '85vh', borderRadius: 10, display: 'block', boxShadow: '0 20px 60px rgba(0,0,0,0.6)' }} />
        <button
          onClick={onClose}
          style={{ position: 'absolute', top: -12, right: -12, width: 30, height: 30, borderRadius: '50%', border: 'none', background: '#fff', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: '0 2px 8px rgba(0,0,0,0.2)' }}
        >
          <X size={14} />
        </button>
      </div>
    </div>
  )
}

export default function ViolationsManagement() {
  const [violations, setViolations]       = useState([])
  const [loading, setLoading]             = useState(true)
  const [filter, setFilter]               = useState('all')
  const [search, setSearch]               = useState('')
  const [datePeriod, setDatePeriod]       = useState('all')
  const [actionLoading, setActionLoading] = useState(null)
  const [showIssueModal, setShowIssueModal] = useState(false)
  const [lightboxSrc, setLightboxSrc]     = useState(null)

  const fetchAll = () => {
    setLoading(true)
    getAllViolations()
      .then(({ data }) => setViolations(data))
      .catch(() => toast.error('Failed to load violations.'))
      .finally(() => setLoading(false))
  }

  useEffect(() => { fetchAll() }, [])

  const handleViolationCreated = (newViolation) => {
    setViolations(prev => [newViolation, ...prev])
  }

  const filtered = useMemo(() => {
    let list = violations

    // Status filter
    if (filter === 'pending')  list = list.filter(v => !v.is_released && !v.is_resolved)
    if (filter === 'notified') list = list.filter(v =>  v.is_released && !v.is_resolved)
    if (filter === 'resolved') list = list.filter(v =>  v.is_resolved)

    // Date period filter
    if (datePeriod !== 'all') {
      const cutoff = getPeriodStart(datePeriod)
      list = list.filter(v => new Date(v.issued_at) >= cutoff)
    }

    // Search filter
    const q = search.trim().toLowerCase()
    if (q) {
      list = list.filter(v =>
        v.plate_number?.toLowerCase().includes(q) ||
        v.owner_name?.toLowerCase().includes(q) ||
        v.owner_email?.toLowerCase().includes(q)
      )
    }

    return list
  }, [violations, filter, datePeriod, search])

  // Summary stats
  const pendingCount    = violations.filter(v => !v.is_released && !v.is_resolved).length
  const notifiedCount   = violations.filter(v =>  v.is_released && !v.is_resolved).length
  const outstandingFine = violations
    .filter(v => !v.is_resolved)
    .reduce((sum, v) => sum + parseFloat(v.fine_amount || 0), 0)

  const handleNotify = async (v) => {
    setActionLoading(v.id)
    try {
      const { data } = await releaseViolation(v.id)
      setViolations(prev => prev.map(x => x.id === v.id ? data : x))
      toast.success(`${v.plate_number} has been notified of this violation.`)
    } catch {
      toast.error('Failed to notify owner.')
    } finally {
      setActionLoading(null)
    }
  }

  const handleUnnotify = async (v) => {
    setActionLoading(v.id)
    try {
      const { data } = await unreleaseViolation(v.id)
      setViolations(prev => prev.map(x => x.id === v.id ? data : x))
      toast.success('Violation notification withdrawn.')
    } catch {
      toast.error('Failed to withdraw notification.')
    } finally {
      setActionLoading(null)
    }
  }

  const handleResolve = async (v) => {
    setActionLoading(v.id)
    try {
      const { data } = await resolveViolation(v.id)
      setViolations(prev => prev.map(x => x.id === v.id ? data : x))
      toast.success('Violation marked as resolved.')
    } catch {
      toast.error('Failed to resolve violation.')
    } finally {
      setActionLoading(null)
    }
  }

  return (
    <AdminLayout>
      <div className="vm-page">

        {/* Modals */}
        {showIssueModal && (
          <IssueViolationModal
            onClose={() => setShowIssueModal(false)}
            onCreated={handleViolationCreated}
          />
        )}
        {lightboxSrc && <EvidenceLightbox src={lightboxSrc} onClose={() => setLightboxSrc(null)} />}

        {/* Header */}
        <div className="vm-header">
          <div>
            <h1 className="vm-title">Violations</h1>
            <p className="vm-subtitle">
              All vehicle violations — owners can see their records in the portal. Use <em>Notify</em> to officially flag a violation to the owner.
            </p>
          </div>
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
          <button className="vm-issue-btn" onClick={() => setShowIssueModal(true)}>
            <Plus size={14} /> Issue Violation
          </button>
          <div className="vm-stats-row">
            <div className="vm-stat">
              <span className="vm-stat-num vm-stat-pending">{pendingCount}</span>
              <span className="vm-stat-label">Pending</span>
            </div>
            <div className="vm-stat">
              <span className="vm-stat-num vm-stat-notified">{notifiedCount}</span>
              <span className="vm-stat-label">Notified</span>
            </div>
            <div className="vm-stat">
              <span className="vm-stat-num vm-stat-fine">₱{outstandingFine.toFixed(2)}</span>
              <span className="vm-stat-label">Outstanding</span>
            </div>
          </div>
          </div>
        </div>

        {/* Toolbar */}
        <div className="vm-toolbar">
          <div className="vm-filters">
            <Filter size={14} className="vm-filter-icon" />
            {FILTER_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                className={`vm-filter-btn ${filter === opt.value ? 'active' : ''}`}
                onClick={() => setFilter(opt.value)}
              >
                {opt.label}
                {opt.value === 'pending' && pendingCount > 0 && (
                  <span className="vm-badge">{pendingCount}</span>
                )}
              </button>
            ))}
            <span className="vm-filter-sep" />
            <div className="vm-period-btns">
              {DATE_PERIODS.map(p => (
                <button
                  key={p.value}
                  className={`vm-period-btn ${datePeriod === p.value ? 'active' : ''}`}
                  onClick={() => setDatePeriod(p.value)}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>

          <div className="vm-search-group">
            <div className="vm-search-wrap">
              <Search size={13} className="vm-search-icon" />
              <input
                className="vm-search-input"
                placeholder="Search plate or owner…"
                value={search}
                onChange={e => setSearch(e.target.value)}
              />
              {search && (
                <button className="vm-search-clear" onClick={() => setSearch('')}>
                  <X size={11} />
                </button>
              )}
            </div>
            {(filter !== 'all' || datePeriod !== 'all' || search) && (
              <button className="vm-clear-btn" onClick={() => { setFilter('all'); setDatePeriod('all'); setSearch('') }} title="Clear filters">
                <X size={13} /> Clear
              </button>
            )}
            <button className="vm-refresh-btn" onClick={fetchAll} title="Refresh">
              <RotateCcw size={14} />
            </button>
          </div>
        </div>

        {/* Table */}
        <div className="vm-card">
          {loading ? (
            <div className="vm-empty">Loading violations…</div>
          ) : filtered.length === 0 ? (
            <div className="vm-empty">No violations found.</div>
          ) : (
            <table className="vm-table">
              <thead>
                <tr>
                  <th>Plate</th>
                  <th>Owner</th>
                  <th>Type</th>
                  <th>Fine</th>
                  <th>Notes</th>
                  <th>Evidence</th>
                  <th>Issued</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((v) => (
                  <tr key={v.id} className={v.is_resolved ? 'vm-row-resolved' : v.is_released ? 'vm-row-notified' : ''}>
                    <td className="vm-plate">{v.plate_number}</td>
                    <td className="vm-owner">
                      <span>{v.owner_name || '—'}</span>
                      {v.owner_email && (
                        <span className="vm-owner-email">{v.owner_email}</span>
                      )}
                    </td>
                    <td>
                      <span className={`vm-type-pill vm-type-${v.violation_type}`}>
                        {TYPE_LABELS[v.violation_type] ?? v.violation_type}
                      </span>
                    </td>
                    <td><FineTag amount={v.fine_amount} /></td>
                    <td className="vm-notes">{v.notes || '—'}</td>
                    <td>
                      {v.evidence_url ? (
                        <button
                          className="vm-evidence-thumb-btn"
                          onClick={() => setLightboxSrc(v.evidence_url)}
                          title="View evidence"
                        >
                          <img src={v.evidence_url} alt="evidence" className="vm-evidence-thumb" />
                          <ZoomIn size={12} className="vm-evidence-zoom" />
                        </button>
                      ) : (
                        <span className="vm-no-evidence"><Image size={13} /> None</span>
                      )}
                    </td>
                    <td className="vm-time" title={fmtDate(v.issued_at)}>{timeAgo(v.issued_at)}</td>
                    <td>
                      {v.is_resolved ? (
                        <span className="vm-status vm-status-resolved"><CheckCircle size={12} /> Resolved</span>
                      ) : v.is_released ? (
                        <span className="vm-status vm-status-released"><Bell size={12} /> Notified</span>
                      ) : (
                        <span className="vm-status vm-status-pending"><EyeOff size={12} /> Pending</span>
                      )}
                    </td>
                    <td>
                      <div className="vm-actions">
                        {!v.is_resolved && (
                          <>
                            {!v.is_released ? (
                              <button
                                className="vm-btn vm-btn-release"
                                disabled={actionLoading === v.id}
                                onClick={() => handleNotify(v)}
                                title="Officially notify owner"
                              >
                                <Bell size={13} /> Notify
                              </button>
                            ) : (
                              <button
                                className="vm-btn vm-btn-hide"
                                disabled={actionLoading === v.id}
                                onClick={() => handleUnnotify(v)}
                                title="Withdraw notification"
                              >
                                <BellOff size={13} /> Unnotify
                              </button>
                            )}
                            <button
                              className="vm-btn vm-btn-resolve"
                              disabled={actionLoading === v.id}
                              onClick={() => handleResolve(v)}
                              title="Mark resolved"
                            >
                              <CheckCircle size={13} /> Resolve
                            </button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

      </div>
    </AdminLayout>
  )
}
