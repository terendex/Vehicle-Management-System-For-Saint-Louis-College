import { useState, useEffect } from 'react'
import {
  AlertTriangle, CheckCircle, Eye, EyeOff, Filter,
  RotateCcw, PhilippinePeso
} from 'lucide-react'
import { toast } from 'sonner'
import { formatDistanceToNow } from 'date-fns'
import AdminLayout from '../../components/Layout/AdminLayout'
import {
  getAllViolations, releaseViolation, unreleaseViolation, resolveViolation,
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
  { value: 'unreleased', label: 'Pending Review' },
  { value: 'released',   label: 'Released' },
  { value: 'resolved',   label: 'Resolved' },
]

function timeAgo(ts) {
  try { return formatDistanceToNow(new Date(ts), { addSuffix: true }) } catch { return '' }
}

function FineTag({ amount }) {
  return (
    <span className="vm-fine-tag">
      ₱{parseFloat(amount).toFixed(2)}
    </span>
  )
}

export default function ViolationsManagement() {
  const [violations, setViolations]   = useState([])
  const [loading, setLoading]         = useState(true)
  const [filter, setFilter]           = useState('all')
  const [actionLoading, setActionLoading] = useState(null)

  const fetch = () => {
    setLoading(true)
    getAllViolations()
      .then(({ data }) => setViolations(data))
      .catch(() => toast.error('Failed to load violations.'))
      .finally(() => setLoading(false))
  }

  useEffect(() => { fetch() }, [])

  const filtered = violations.filter((v) => {
    if (filter === 'unreleased') return !v.is_released && !v.is_resolved
    if (filter === 'released')   return v.is_released  && !v.is_resolved
    if (filter === 'resolved')   return v.is_resolved
    return true
  })

  const pendingCount = violations.filter((v) => !v.is_released && !v.is_resolved).length

  const handleRelease = async (v) => {
    setActionLoading(v.id)
    try {
      const { data } = await releaseViolation(v.id)
      setViolations((prev) => prev.map((x) => x.id === v.id ? data : x))
      toast.success(`Violation released — ${v.plate_number} can now see it.`)
    } catch {
      toast.error('Failed to release violation.')
    } finally {
      setActionLoading(null)
    }
  }

  const handleUnrelease = async (v) => {
    setActionLoading(v.id)
    try {
      const { data } = await unreleaseViolation(v.id)
      setViolations((prev) => prev.map((x) => x.id === v.id ? data : x))
      toast.success('Violation hidden from owner.')
    } catch {
      toast.error('Failed to unrelease violation.')
    } finally {
      setActionLoading(null)
    }
  }

  const handleResolve = async (v) => {
    setActionLoading(v.id)
    try {
      const { data } = await resolveViolation(v.id)
      setViolations((prev) => prev.map((x) => x.id === v.id ? data : x))
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

        <div className="vm-header">
          <div>
            <h1 className="vm-title">Violations</h1>
            <p className="vm-subtitle">
              Review violations before releasing them to vehicle owners.
            </p>
          </div>
          {pendingCount > 0 && (
            <span className="vm-pending-badge">
              <AlertTriangle size={13} /> {pendingCount} pending review
            </span>
          )}
        </div>

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
                {opt.value === 'unreleased' && pendingCount > 0 && (
                  <span className="vm-badge">{pendingCount}</span>
                )}
              </button>
            ))}
          </div>
          <button className="vm-refresh-btn" onClick={fetch} title="Refresh">
            <RotateCcw size={14} />
          </button>
        </div>

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
                  <th>Issued</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((v) => (
                  <tr key={v.id} className={v.is_resolved ? 'vm-row-resolved' : v.is_released ? '' : 'vm-row-unreleased'}>
                    <td className="vm-plate">{v.plate_number}</td>
                    <td className="vm-owner">{v.owner_name || '—'}</td>
                    <td>
                      <span className={`vm-type-pill vm-type-${v.violation_type}`}>
                        {TYPE_LABELS[v.violation_type] ?? v.violation_type}
                      </span>
                    </td>
                    <td><FineTag amount={v.fine_amount} /></td>
                    <td className="vm-notes">{v.notes || '—'}</td>
                    <td className="vm-time">{timeAgo(v.issued_at)}</td>
                    <td>
                      {v.is_resolved ? (
                        <span className="vm-status vm-status-resolved"><CheckCircle size={12} /> Resolved</span>
                      ) : v.is_released ? (
                        <span className="vm-status vm-status-released"><Eye size={12} /> Released</span>
                      ) : (
                        <span className="vm-status vm-status-pending"><EyeOff size={12} /> Pending Review</span>
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
                                onClick={() => handleRelease(v)}
                                title="Release to owner"
                              >
                                <Eye size={13} /> Release
                              </button>
                            ) : (
                              <button
                                className="vm-btn vm-btn-hide"
                                disabled={actionLoading === v.id}
                                onClick={() => handleUnrelease(v)}
                                title="Hide from owner"
                              >
                                <EyeOff size={13} /> Hide
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
