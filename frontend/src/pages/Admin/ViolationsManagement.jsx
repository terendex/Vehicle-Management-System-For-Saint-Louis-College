import { useState, useEffect, useMemo } from 'react'
import { useLiveUpdates } from '../../realtime/useLiveUpdates'
import {
  AlertTriangle, CheckCircle, Filter,
  RotateCcw, Search, Bell, X,
  Image, ZoomIn, ChevronLeft, ChevronRight, Loader2,
  FileText, ShieldOff, ClipboardCheck,
} from 'lucide-react'
import notify, { toast } from '../../components/Feedback/notify'
import { formatDistanceToNow, format, parseISO } from 'date-fns'
import {
  getAllViolations, resolveViolation,
  issueCDSOReport, clearViolation, liftViolation, exportViolationsReport,
} from '../../api/violations'
import ReportExportBar from '../../components/ReportExportBar'
import TableLoader from '../../components/TableLoader'
import './ViolationsManagement.css'

const TYPE_LABELS = {
  unauthorized_entry:   'Unauthorized Entry',
  double_parking:       'Double Parking',
  time_exceed:          'Time Exceed',
  no_sticker:           'No Sticker',
  expired_registration: 'Expired Registration',
  unauthorized:         'Unauthorized',
  other:                'Other',
}

const OFFENSE_LABELS = { 1: '1st', 2: '2nd', 3: '3rd' }

const FILTER_OPTIONS = [
  { value: 'all',        label: 'All' },
  { value: 'warning',    label: 'Warnings' },
  { value: 'fee',        label: 'Confiscated (3rd)' },
  { value: 'resolved',   label: 'Cleared / Resolved' },
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


function OffenseBadge({ num }) {
  if (!num) return null
  const cls = num === 3 ? 'vm-offense-3' : num === 2 ? 'vm-offense-2' : 'vm-offense-1'
  return <span className={`vm-offense-badge ${cls}`}>{OFFENSE_LABELS[num] ?? `${num}th`}</span>
}

// Evidence is served by the API, not by a public storage URL, so the request
// has to carry a token. An <img> cannot send an Authorization header — the
// parking camera stream solves it the same way.
function evidenceSrc(url) {
  if (!url) return null
  const token = localStorage.getItem('access_token') || ''
  return token ? `${url}?token=${encodeURIComponent(token)}` : url
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

// ─── OR Entry Modal ────────────────────────────────────────────────────────────
function ORModal({ violation, onClose, onConfirm }) {
  const [or, setOr] = useState('')
  return (
    <div className="vm-overlay" onClick={onClose}>
      <div className="vm-modal" onClick={e => e.stopPropagation()}>
        <button className="vm-modal-close" onClick={onClose}><X size={16} /></button>
        <ClipboardCheck size={32} className="vm-modal-icon vm-modal-icon-success" />
        <h2 className="vm-modal-title">Clear Violation</h2>
        <p className="vm-modal-body">
          Enter the Official Receipt (OR) number issued by Accounting for plate <strong>{violation.plate_number}</strong>.
          This will lift the entry block and reset the warning cycle.
        </p>
        <input
          className="vm-or-input"
          placeholder="Official Receipt number…"
          value={or}
          onChange={e => setOr(e.target.value)}
          autoFocus
        />
        <div className="vm-modal-actions">
          <button className="vm-modal-btn vm-modal-btn-ghost" onClick={onClose}>Cancel</button>
          <button
            className="vm-modal-btn vm-modal-btn-primary"
            onClick={() => {
              if (!or.trim()) {
                notify.error('Enter the Official Receipt number.', { title: 'Violation not cleared' })
                return
              }
              onConfirm(or.trim())
            }}
          >
            Clear Violation
          </button>
        </div>
      </div>
    </div>
  )
}

// ─── Lift (False Alarm) Modal ─────────────────────────────────────────────────
// Lifting is not the same as clearing. Clearing says the offence happened and
// the fee was settled; lifting says it should never have been issued, so it
// stops counting and the owner's remaining violations of that type step back
// down a number. The reason is mandatory — this erases an offence from someone's
// record and the decision has to be answerable later.
function LiftModal({ violation, onClose, onConfirm, busy }) {
  const [reason, setReason] = useState('')
  return (
    <div className="vm-overlay" onClick={onClose}>
      <div className="vm-modal" onClick={e => e.stopPropagation()}>
        <button className="vm-modal-close" onClick={onClose}><X size={16} /></button>
        <ShieldOff size={32} className="vm-modal-icon vm-modal-icon-warn" />
        <h2 className="vm-modal-title">Lift Violation</h2>
        <p className="vm-modal-body">
          Void this <strong>{violation.violation_type_display || violation.violation_type}</strong> for
          plate <strong>{violation.plate_number}</strong> as a false alarm.
          It stops counting toward the offence ladder, and any later violations
          of the same type are renumbered down.
        </p>
        <textarea
          className="vm-or-input"
          rows={3}
          placeholder="Reason — e.g. misread plate, camera artefact, wrong vehicle…"
          value={reason}
          onChange={e => setReason(e.target.value)}
          autoFocus
        />
        <div className="vm-modal-actions">
          <button className="vm-modal-btn vm-modal-btn-ghost" onClick={onClose}>Cancel</button>
          <button
            className="vm-modal-btn vm-modal-btn-primary"
            disabled={busy}
            onClick={() => {
              if (!reason.trim()) {
                notify.error('Give a reason for lifting this violation.', { title: 'Violation not lifted' })
                return
              }
              onConfirm(reason.trim())
            }}
          >
            {busy ? 'Lifting…' : 'Lift Violation'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default function ViolationsManagement() {
  const [violations, setViolations]       = useState([])
  const [liftModal, setLiftModal]         = useState(null)
  const [loading, setLoading]             = useState(true)
  const [filter, setFilter]               = useState('all')
  const [typeFilter, setTypeFilter]       = useState('all')
  const [search, setSearch]               = useState('')
  const [datePeriod, setDatePeriod]       = useState('all')
  const [actionLoading, setActionLoading] = useState(null)
  const [lightboxSrc, setLightboxSrc]     = useState(null)
  // Evidence URLs the browser could not load. A violation photo lives in object
  // storage, and the row must stay readable when that fetch fails — a broken
  // image glyph looks like the app is broken rather than like the file is
  // unreachable.
  const [badEvidence, setBadEvidence]     = useState(() => new Set())
  const [confirmAction, setConfirmAction] = useState(null)
  const [orModal, setOrModal]             = useState(null)   // violation waiting for OR
  const [resultModal, setResultModal]     = useState(null)
  const [page, setPage]                   = useState(1)
  const PAGE_SIZE = 10

  const fetchAll = () => {
    setLoading(true)
    getAllViolations()
      .then(({ data }) => setViolations(data))
      .catch(() => toast.error('Failed to load violations.'))
      .finally(() => setLoading(false))
  }

  useEffect(() => { fetchAll() }, [])

  // Instant refresh when a violation (or its vehicle) changes
  useLiveUpdates(fetchAll, ['violation', 'vehicle'])

  // Live wiring to gate scanning: new auto-issued violations appear without a
  // manual refresh (silent poll — no loading spinner)
  useEffect(() => {
    const t = setInterval(() => {
      getAllViolations().then(({ data }) => setViolations(data)).catch(() => {})
    }, 30000)
    return () => clearInterval(t)
  }, [])

  const filtered = useMemo(() => {
    let list = [...violations]

    if (filter === 'warning')  list = list.filter(v => v.status === 'warning')
    if (filter === 'fee')      list = list.filter(v => v.status === 'fee_imposed')
    if (filter === 'resolved') list = list.filter(v => v.is_resolved || v.status === 'cleared')

    if (typeFilter !== 'all') list = list.filter(v => v.violation_type === typeFilter)

    if (datePeriod !== 'all') {
      const cutoff = getPeriodStart(datePeriod)
      list = list.filter(v => new Date(v.issued_at) >= cutoff)
    }

    const q = search.trim().toLowerCase()
    if (q) {
      list = list.filter(v =>
        v.plate_number?.toLowerCase().includes(q) ||
        v.owner_name?.toLowerCase().includes(q) ||
        v.owner_email?.toLowerCase().includes(q) ||
        v.notes?.toLowerCase().includes(q)
      )
    }

    list.sort((a, b) => new Date(b.issued_at) - new Date(a.issued_at))
    return list
  }, [violations, filter, typeFilter, datePeriod, search])

  useEffect(() => { setPage(1) }, [filter, typeFilter, datePeriod, search])

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const paginated  = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)

  const feeCount     = violations.filter(v => v.status === 'fee_imposed').length
  const warningCount = violations.filter(v => v.status === 'warning').length

  const executeAction = async () => {
    if (!confirmAction) return
    const { type, violation: v } = confirmAction
    setConfirmAction(null)
    setActionLoading(v.id)
    try {
      let data
      if (type === 'resolve')       ({ data } = await resolveViolation(v.id))
      if (type === 'issue_report')  ({ data } = await issueCDSOReport(v.id))
      setViolations(prev => prev.map(x => x.id === v.id ? data : x))
      const msgs = {
        resolve:       'Violation marked as resolved.',
        issue_report:  `CDSO report issued for ${v.plate_number}. Owner may now pay at Accounting.`,
      }
      setResultModal({ type: 'success', message: msgs[type] })
    } catch {
      const msgs = {
        resolve:      'Failed to resolve violation.',
        issue_report: 'Failed to issue CDSO report.',
      }
      setResultModal({ type: 'error', message: msgs[type] })
    } finally {
      setActionLoading(null)
    }
  }

  const executeLift = async (violation, reason) => {
    setActionLoading(violation.id)
    try {
      await liftViolation(violation.id, reason)
      // Lifting renumbers the vehicle's other violations of this type server
      // side, so refetch the list rather than patching the single row — the
      // rows that changed are not the one that was acted on.
      const { data } = await getAllViolations()
      setViolations(data)
      setLiftModal(null)
      setResultModal({
        type: 'success',
        message: `Violation lifted for ${violation.plate_number}. `
               + `Remaining violations of this type have been renumbered.`,
      })
    } catch (err) {
      setResultModal({
        type: 'error',
        message: err?.response?.data?.detail || 'Failed to lift violation.',
      })
    } finally {
      setActionLoading(null)
    }
  }

  const executeClear = async (violation, orNumber) => {
    setOrModal(null)
    setActionLoading(violation.id)
    try {
      const { data } = await clearViolation(violation.id, orNumber)
      setViolations(prev => prev.map(x => x.id === violation.id ? data : x))
      setResultModal({ type: 'success', message: `Violation cleared. Entry access restored for ${violation.plate_number}. Warning cycle reset.` })
    } catch (err) {
      const msg = err?.response?.data?.detail || 'Failed to clear violation.'
      setResultModal({ type: 'error', message: msg })
    } finally {
      setActionLoading(null)
    }
  }

  function rowClass(v) {
    if (v.status === 'cleared' || v.is_resolved) return 'vm-row-resolved'
    if (v.status === 'fee_imposed') return 'vm-row-fee'
    if (v.status === 'warning') return 'vm-row-warning'
    if (v.is_released) return 'vm-row-notified'
    return ''
  }

  function StatusBadge({ v }) {
    // Checked before `cleared`/`is_resolved`: a lifted violation is also
    // resolved, and showing it as "Cleared" would claim a fee was settled.
    if (v.status === 'lifted')
      return (
        <span className="vm-status vm-status-lifted" title={v.lifted_reason || undefined}>
          <ShieldOff size={12} /> Lifted <em>· false alarm</em>
        </span>
      )
    if (v.status === 'cleared' || (v.is_resolved && !v.offense_number))
      return <span className="vm-status vm-status-resolved"><CheckCircle size={12} /> Cleared</span>
    if (v.status === 'fee_imposed') {
      // Legacy rows issued under the old fine system. Nothing sets this now.
      return <span className="vm-status vm-status-fee"><ShieldOff size={12} /> Legacy fee</span>
    }
    if (v.status === 'warning')
      return <span className="vm-status vm-status-warning"><AlertTriangle size={12} /> Warning</span>
    if (v.is_resolved)
      return <span className="vm-status vm-status-resolved"><CheckCircle size={12} /> Resolved</span>
    return <span className="vm-status vm-status-released"><Bell size={12} /> Issued</span>
  }

  function ActionButtons({ v }) {
    const busy = actionLoading === v.id

    // Any violation that is still standing can be lifted as a false alarm.
    // Not offered once cleared (a fee was settled — lifting would imply a
    // refund) or already lifted.
    const LiftBtn = () => (
      <button
        className="vm-btn vm-btn-lift"
        disabled={busy}
        onClick={() => setLiftModal(v)}
        title="Void as a false alarm and renumber the remaining offences"
      >
        {busy ? <Loader2 size={13} className="vm-spin" /> : <ShieldOff size={13} />} Lift
      </button>
    )
    const canLift = v.status !== 'cleared' && v.status !== 'lifted'

    // New-style offense violations
    if (v.offense_number) {
      if (v.status === 'lifted' || v.status === 'cleared') return null
      if (v.status === 'fee_imposed') {
        return (
          <div className="vm-actions">
            <LiftBtn />
            {!v.cdso_report_issued && (
              <button
                className="vm-btn vm-btn-report"
                disabled={busy}
                onClick={() => setConfirmAction({ type: 'issue_report', violation: v })}
                title="Issue CDSO report so owner can pay at Accounting"
              >
                {busy ? <Loader2 size={13} className="vm-spin" /> : <FileText size={13} />} Issue Report
              </button>
            )}
            {v.cdso_report_issued && (
              <button
                className="vm-btn vm-btn-clear"
                disabled={busy}
                onClick={() => setOrModal(v)}
                title="Enter OR number to clear violation and restore entry"
              >
                {busy ? <Loader2 size={13} className="vm-spin" /> : <ClipboardCheck size={13} />} Clear (OR)
              </button>
            )}
          </div>
        )
      }
      // Warning — auto-notified, so the only call left is whether it should
      // have been issued at all.
      return <div className="vm-actions"><LiftBtn /></div>
    }

    // Non-offense violations (owner is auto-emailed at creation) — just Resolve
    if (v.status === 'lifted') return null
    if (v.is_resolved) return null
    return (
      <div className="vm-actions">
        {canLift && <LiftBtn />}
        <button
          className="vm-btn vm-btn-resolve"
          disabled={busy}
          onClick={() => setConfirmAction({ type: 'resolve', violation: v })}
          title="Mark resolved"
        >
          {busy ? <Loader2 size={13} className="vm-spin" /> : <CheckCircle size={13} />} Resolve
        </button>
      </div>
    )
  }

  return (
    <>
      <div className="vm-page">

        {lightboxSrc && <EvidenceLightbox src={lightboxSrc} onClose={() => setLightboxSrc(null)} />}

        <div className="vm-header">
          <div>
            <h1 className="vm-title">Violations</h1>
            <p className="vm-subtitle">
              3-offence escalation: the account is confiscated for 1 week, then
              2 weeks, then the rest of the registration period. A confiscated
              owner cannot enter or park, and being detected counts as a further
              offence.
            </p>
          </div>
          {(feeCount > 0 || warningCount > 0) && (
            <div className="vm-header-stats">
              {feeCount > 0 && (
                <span className="vm-stat-chip vm-stat-fee">
                  <ShieldOff size={13} /> {feeCount} 3rd offence
                </span>
              )}
              {warningCount > 0 && (
                <span className="vm-stat-chip vm-stat-warn">
                  <AlertTriangle size={13} /> {warningCount} Active Warnings
                </span>
              )}
            </div>
          )}
        </div>

        <ReportExportBar
          label="Violations Report"
          fileBase="violations-report"
          fetchBlob={exportViolationsReport}
        />

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
                {opt.value === 'fee' && feeCount > 0 && (
                  <span className="vm-badge vm-badge-red">{feeCount}</span>
                )}
                {opt.value === 'warning' && warningCount > 0 && (
                  <span className="vm-badge vm-badge-yellow">{warningCount}</span>
                )}
              </button>
            ))}
            <span className="vm-filter-sep" />
            <select
              className="vm-type-select"
              value={typeFilter}
              onChange={e => setTypeFilter(e.target.value)}
              title="Filter by violation type"
            >
              <option value="all">All types</option>
              {Object.entries(TYPE_LABELS).map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
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
                placeholder="Search plate, owner, or notes…"
                value={search}
                onChange={e => setSearch(e.target.value)}
              />
              {search && (
                <button className="vm-search-clear" onClick={() => setSearch('')}>
                  <X size={11} />
                </button>
              )}
            </div>
            <button className="vm-refresh-btn" onClick={fetchAll} title="Refresh">
              <RotateCcw size={14} />
            </button>
          </div>
        </div>

        <div className="vm-card">
          {loading ? (
            <TableLoader label="Loading violations…" />
          ) : filtered.length === 0 ? (
            <div className="vm-empty">No violations found.</div>
          ) : (
            <table className="vm-table">
              <colgroup>
                <col className="vm-col-plate" />
                <col className="vm-col-owner" />
                <col className="vm-col-type" />
                <col className="vm-col-fine" />
                <col className="vm-col-notes" />
                <col className="vm-col-evidence" />
                <col className="vm-col-issued" />
                <col className="vm-col-status" />
                <col className="vm-col-actions" />
              </colgroup>
              <thead>
                <tr>
                  <th>Plate</th>
                  <th>Owner</th>
                  <th>Type / Offense</th>
                  <th>Notes</th>
                  <th>Evidence</th>
                  <th>Issued</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {paginated.map((v) => (
                  <tr key={v.id} className={rowClass(v)}>
                    <td className="vm-plate">{v.plate_number}</td>
                    <td>
                      <div className="vm-owner">
                        <span className="vm-owner-name">{v.owner_name || '—'}</span>
                        {v.owner_email && (
                          <span className="vm-owner-email">{v.owner_email}</span>
                        )}
                      </div>
                    </td>
                    <td>
                      <div className="vm-type-cell">
                        <span className={`vm-type-pill vm-type-${v.violation_type}`}>
                          {TYPE_LABELS[v.violation_type] ?? v.violation_type}
                        </span>
                        <OffenseBadge num={v.offense_number} />
                      </div>
                    </td>
                                        <td><div className="vm-notes" title={v.notes || ''}>{v.notes || '—'}</div></td>
                    <td>
                      {v.evidence_url && !badEvidence.has(v.evidence_url) ? (
                        <button
                          className="vm-evidence-thumb-btn"
                          onClick={() => setLightboxSrc(evidenceSrc(v.evidence_url))}
                          title="View evidence"
                        >
                          <img
                            src={evidenceSrc(v.evidence_url)}
                            alt="evidence"
                            className="vm-evidence-thumb"
                            onError={() => setBadEvidence(prev => {
                              const next = new Set(prev)
                              next.add(v.evidence_url)
                              return next
                            })}
                          />
                          <ZoomIn size={12} className="vm-evidence-zoom" />
                        </button>
                      ) : v.evidence_url ? (
                        // The record says a photo was captured but it will not
                        // load. Distinct from "None" on purpose: one means no
                        // evidence was ever taken, the other means it exists and
                        // cannot be reached — only the second is worth chasing.
                        <span
                          className="vm-no-evidence vm-evidence-broken"
                          title={`Evidence could not be loaded from storage:\n${v.evidence_url}`}
                        >
                          <AlertTriangle size={13} /> Unavailable
                        </span>
                      ) : (
                        <span className="vm-no-evidence"><Image size={13} /> None</span>
                      )}
                    </td>
                    <td className="vm-time" title={fmtDate(v.issued_at)}>
                      {timeAgo(v.issued_at)}
                      {(v.on_duty_guard_name || v.issued_by_name) && (
                        <span className="vm-issued-guard">
                          {v.on_duty_guard_name
                            ? `On duty: ${v.on_duty_guard_name}`
                            : `By: ${v.issued_by_name}`}
                        </span>
                      )}
                    </td>
                    <td><StatusBadge v={v} /></td>
                    <td><ActionButtons v={v} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {!loading && totalPages > 1 && (
          <div className="vm-pagination">
            <span className="vm-page-info">
              Showing {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, filtered.length)} of {filtered.length}
            </span>
            <div className="vm-page-controls">
              <button className="vm-page-btn" disabled={page === 1} onClick={() => setPage(p => p - 1)}>
                <ChevronLeft size={15} />
              </button>
              <span className="vm-page-current">Page {page} of {totalPages}</span>
              <button className="vm-page-btn" disabled={page === totalPages} onClick={() => setPage(p => p + 1)}>
                <ChevronRight size={15} />
              </button>
            </div>
          </div>
        )}

      </div>

      {/* OR Entry Modal */}
      {orModal && (
        <ORModal
          violation={orModal}
          onClose={() => setOrModal(null)}
          onConfirm={(or) => executeClear(orModal, or)}
        />
      )}

      {liftModal && (
        <LiftModal
          violation={liftModal}
          busy={actionLoading === liftModal.id}
          onClose={() => setLiftModal(null)}
          onConfirm={(reason) => executeLift(liftModal, reason)}
        />
      )}

      {/* Confirmation Modal */}
      {confirmAction && (() => {
        const { type, violation: v } = confirmAction
        const config = {
          resolve: {
            title: 'Mark as Resolved?',
            body: `This will mark the violation for plate ${v.plate_number} as resolved. This action cannot be undone.`,
            confirm: 'Resolve', cls: 'vm-modal-btn-danger',
          },
          issue_report: {
            title: 'Issue CDSO Report?',
            body: `This marks that you have issued the official violation report to ${v.plate_number}. There is no fee to pay — the penalty is the confiscation already applied to the account.`,
            confirm: 'Issue Report', cls: 'vm-modal-btn-primary',
          },
        }[type]
        return (
          <div className="vm-overlay" onClick={() => setConfirmAction(null)}>
            <div className="vm-modal" onClick={e => e.stopPropagation()}>
              <button className="vm-modal-close" onClick={() => setConfirmAction(null)}><X size={16} /></button>
              <AlertTriangle size={32} className="vm-modal-icon vm-modal-icon-warn" />
              <h2 className="vm-modal-title">{config.title}</h2>
              <p className="vm-modal-body">{config.body}</p>
              <div className="vm-modal-actions">
                <button className="vm-modal-btn vm-modal-btn-ghost" onClick={() => setConfirmAction(null)}>Cancel</button>
                <button className={`vm-modal-btn ${config.cls}`} onClick={executeAction}>{config.confirm}</button>
              </div>
            </div>
          </div>
        )
      })()}

      {/* Result Modal */}
      {resultModal && (
        <div className="vm-overlay" onClick={() => setResultModal(null)}>
          <div className="vm-modal" onClick={e => e.stopPropagation()}>
            <button className="vm-modal-close" onClick={() => setResultModal(null)}><X size={16} /></button>
            {resultModal.type === 'success'
              ? <CheckCircle size={32} className="vm-modal-icon vm-modal-icon-success" />
              : <AlertTriangle size={32} className="vm-modal-icon vm-modal-icon-error" />}
            <h2 className="vm-modal-title">{resultModal.type === 'success' ? 'Success' : 'Error'}</h2>
            <p className="vm-modal-body">{resultModal.message}</p>
            <div className="vm-modal-actions">
              <button className="vm-modal-btn vm-modal-btn-primary" onClick={() => setResultModal(null)}>OK</button>
            </div>
          </div>
        </div>
      )}

    </>
  )
}
