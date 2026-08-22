import React, { useState, useEffect, useRef } from 'react'
import { registrationApi } from '../../api/registration'
import notify from '../../components/Feedback/notify'
import { fieldProblems } from '../../components/Feedback/formProblems'
import { QRCodeSVG } from 'qrcode.react'
import { format } from 'date-fns'
import { Copy, Check, X, Eye, ShieldCheck, Mail, User, Car, KeyRound, Receipt, CalendarDays, AlertCircle, Search, ChevronLeft, ChevronRight, AlertTriangle, QrCode, Printer, Maximize2, SlidersHorizontal, FileText, Paperclip } from 'lucide-react'
import { useLiveUpdates } from '../../realtime/useLiveUpdates'
import ReportExportBar from '../../components/ReportExportBar'
import { TableLoaderRow } from '../../components/TableLoader'
import './VehicleRegistration.css'

// "Any Day" reads as Sunday included; the campus is closed then, so the ANY
// pass is labelled with the week it actually admits. TTHS is the pre-rename
// rotation (Tue/Thu/Sat) — kept so a row that missed the migration still reads
// as the days it was actually issued for.
const SCHEDULE_LABELS = {
  MWF: 'Mon · Wed · Fri',
  TTHF: 'Tue · Thu · Fri',
  TTHS: 'Tue · Thu · Sat',
  ANY: 'Mon – Sat',
  MIXED: 'Mixed Days',
}
const ALL_DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
const DAY_SHORT = { Monday: 'Mon', Tuesday: 'Tue', Wednesday: 'Wed', Thursday: 'Thu', Friday: 'Fri', Saturday: 'Sat' }

/* Payment is a separate axis from review status: an application can be rejected
   after the applicant already paid, and a fee-exempt one is neither paid nor
   owing. The table shows both badges rather than folding them into one word. */
const PAYMENT_LABELS = {
  unpaid: 'Unpaid',
  paid:   'Paid',
  exempt: 'Exempt',
}

function formatSchedule(entity) {
  if (!entity?.schedule) return '—'
  if (entity.schedule === 'MIXED' && entity.campus_days?.length > 0) {
    return entity.campus_days.map(day => DAY_SHORT[day] || day).join(' · ')
  }
  return SCHEDULE_LABELS[entity.schedule] || entity.schedule
}

// The backend reports an "other" bucket only when a row carries a registrant
// type or status that is no longer in either enum. It is shown so the counts
// still add up to the total, but it is not a filter: the status dropdown has no
// such option and the type filter would match nothing, so a click would land
// the page on an empty table it could not explain.
const OTHER_KEY = 'other'

function StatTile({ variant, count, label, active, onSelect, title }) {
  // A bucket holding nothing is greyed out: on a line of ten tiles the empty
  // ones would otherwise shout their zero as loudly as the one real count.
  const className = [
    'vr-stat', 'vr-stat--sm', `vr-stat--${variant}`,
    count === 0 ? 'vr-stat--zero' : '',
    active ? 'active' : '',
  ].filter(Boolean).join(' ')
  if (!onSelect) {
    return (
      <div className={`${className} vr-stat--static`} title={`${label} — not a filter`}>
        <span className="vr-stat-value">{count}</span>
        <span className="vr-stat-label">{label}</span>
      </div>
    )
  }
  return (
    <button
      type="button"
      className={className}
      onClick={onSelect}
      title={title}
      aria-pressed={active}
    >
      <span className="vr-stat-value">{count}</span>
      <span className="vr-stat-label">{label}</span>
    </button>
  )
}

/* An upload the applicant attached, shown as the thing it is.

   Reviewing an application means reading the licence against the name and the
   receipt against the OR number — a filename shows the reviewer neither, so
   anything the browser can draw is drawn inline and links to its full size.
   PDFs keep the named link (a first-page thumbnail is not worth an embed), and
   so does any picture the browser turns out not to decode: an iPhone HEIC is
   accepted at upload and renders nowhere but Safari, which used to leave a
   broken-image icon with no way to reach the file. */
function AttachmentPreview({ url, alt, emptyText = 'Not provided' }) {
  // Keyed by url, not a bare flag: the modal reuses this component across
  // registrations, and a stale failure would hide the next applicant's photo.
  const [failedUrl, setFailedUrl] = useState(null)

  // An empty slot keeps the tile. Three documents that line up whether or not
  // they arrived is what makes a gap read as a gap — the same fact written as a
  // bare line of italics just leaves a hole in the row.
  if (!url) {
    return (
      <div className="vr-attach vr-attach--empty">
        <span className="vr-attach-box vr-attach-box--empty">{emptyText}</span>
      </div>
    )
  }

  const path     = url.split('?')[0]
  const fileName = decodeURIComponent(path.split('/').pop())
  const asLink   = /\.pdf$/i.test(path) || failedUrl === url

  return (
    <a
      className="vr-attach"
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      title={`Open ${fileName} full size in a new tab`}
    >
      {asLink ? (
        <span className="vr-attach-box vr-attach-file">
          <FileText size={22} />
          <span className="vr-attach-name">{fileName}</span>
        </span>
      ) : (
        <img
          className="vr-attach-box vr-attach-img"
          src={url}
          alt={alt}
          loading="lazy"
          onError={() => setFailedUrl(url)}
        />
      )}
      <span className="vr-attach-hint">
        <Maximize2 size={11} />
        {asLink ? 'Open in a new tab' : 'Click to view full size'}
      </span>
    </a>
  )
}

export default function VehicleRegistration() {
  // Registrations State
  const [registrations, setRegistrations] = useState([])
  const [loading, setLoading] = useState(true)
  const [statusFilter, setStatusFilter] = useState('pending')
  const [typeFilter, setTypeFilter] = useState('all')
  const [paymentFilter, setPaymentFilter] = useState('all')
  const [search, setSearch] = useState('')

  // Counts across every status and registrant type. The table only ever holds
  // one status at a time, so these cannot be tallied from the rows on screen.
  const [summary, setSummary] = useState(null)

  // Pagination
  const [regPage, setRegPage] = useState(1)
  const itemsPerPage = 10

  // Registration confirmation → PDF, for the CDSO's filed copy and for an
  // owner who lost the one emailed to them on approval.
  const [printingRegId, setPrintingRegId] = useState(null)

  // Modals
  const [selectedReg, setSelectedReg] = useState(null)
  const [isViewModalOpen, setIsViewModalOpen] = useState(false)
  const [isRejectModalOpen, setIsRejectModalOpen] = useState(false)
  const [rejectReason, setRejectReason] = useState('')

  // Accept flow — OR number + free day-picker (inline in details modal)
  const [orNumber, setOrNumber] = useState('')
  const [daysOverride, setDaysOverride] = useState([])   // admin-chosen campus days
  const [specialCaseReason, setSpecialCaseReason] = useState('')
  // Required by the backend when the application has no Official Receipt on
  // file at all — a pass may still be granted, but never without a reason.
  const [unpaidReason, setUnpaidReason] = useState('')
  const [scheduleSlots, setScheduleSlots] = useState(null) // per-day remaining student slots

  const orValid = orNumber.trim().length >= 6 && orNumber.trim().length <= 7

  const [resultModal, setResultModal] = useState(null)
  const [showAcceptConfirm, setShowAcceptConfirm] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [isQRModalOpen, setIsQRModalOpen] = useState(false)
  const [qrDisplayData, setQrDisplayData] = useState(null)
  const [qrViewerCopied, setQrViewerCopied] = useState(false)
  const [accountModal, setAccountModal] = useState(null)
  const [blockPrompt, setBlockPrompt] = useState(null)  // registration-block 409 payload

  useEffect(() => {
    fetchRegistrations()
  }, [statusFilter])

  useEffect(() => { fetchSummary() }, [])

  const fetchSummary = async () => {
    try {
      setSummary(await registrationApi.getRegistrationSummary())
    } catch (error) {
      // The counts are a read-out, not a gate — a failed fetch just leaves the
      // strip on its placeholder dashes rather than blocking the table.
      console.error('Failed to fetch registration summary:', error)
    }
  }

  const fetchRegistrations = async () => {
    setLoading(true)
    try {
      const data = await registrationApi.getPendingRegistrations(statusFilter)
      setRegistrations(data)
    } catch (error) {
      console.error('Failed to fetch registrations:', error)
    } finally {
      // finally, so a failed request still clears the spinner instead of
      // leaving the table loading forever.
      setLoading(false)
    }
  }

  // Live-refresh when a registration is created/approved/rejected anywhere
  const refreshAll = () => { fetchRegistrations(); fetchSummary() }
  useLiveUpdates(refreshAll, ['vehicleregistration', 'vehicle'])

  const qrPrintRef = useRef(null)

  // QR of the public registration form URL — shown/printed at CDSO so
  // walk-in applicants can scan it and register on their own phone
  const handleViewRegistrationFormQR = () => {
    const link = `${window.location.origin}/register`
    setQrDisplayData({
      type: 'register-link',
      payload: link,
      title: 'Registration Form QR',
      subtitle: 'Walk-in applicants scan this to open the vehicle registration form',
    })
    setIsQRModalOpen(true)
  }

  const handlePrintQR = () => {
    const svg = qrPrintRef.current?.querySelector('svg')
    if (!svg || !qrDisplayData) return
    const win = window.open('', '_blank', 'width=480,height=640')
    if (!win) return
    win.document.write(`<!DOCTYPE html><html><head><title>${qrDisplayData.title}</title>
      <style>
        body { font-family: Arial, sans-serif; text-align: center; padding: 40px; }
        h1 { font-size: 20px; color: #03396C; margin-bottom: 8px; }
        p { color: #3E5B72; font-size: 13px; margin: 4px 0; }
        svg { width: 300px; height: 300px; margin: 24px 0; }
        .link { font-size: 12px; word-break: break-all; color: #2E4C63; margin-top: 8px; }
      </style></head><body>
      <h1>Vehicle Registration — Saint Louis College</h1>
      <p>Scan this QR code with your phone camera to open the vehicle registration form.</p>
      ${svg.outerHTML}
      <p class="link">${qrDisplayData.payload}</p>
      <script>window.onload = function () { window.print() }</script>
    </body></html>`)
    win.document.close()
  }

  const handleViewVehicleQR = () => {
    if (!selectedReg) return
    const qrData = `VEHICLE:${selectedReg.plate_number}|ID:${selectedReg.id}`
    setQrDisplayData({
      type: 'vehicle',
      payload: qrData,
      title: 'Vehicle Access QR Code',
      subtitle: `${selectedReg.full_name} — ${selectedReg.plate_number}`,
    })
    setIsQRModalOpen(true)
  }

  const handleCopyQRData = async () => {
    if (!qrDisplayData) return
    try {
      await navigator.clipboard.writeText(qrDisplayData.payload)
      setQrViewerCopied(true)
      setTimeout(() => setQrViewerCopied(false), 2000)
    } catch {
      showResult('Failed to copy to clipboard.', 'error')
    }
  }

  // ── Accept flow ──
  // acknowledgeBlock is set true after CDSO confirms a plate flagged by a prior
  // 3rd-offense violation (backend returns 409 registration_blocked otherwise)
  const confirmAccept = async (acknowledgeBlock = false) => {
    if (!selectedReg) return
    // Up to 3 campus days is the normal allowance; more than 3 is a special case
    const tooManyDays = daysOverride.length > 3
    setSubmitting(true)
    try {
      const result = await registrationApi.acceptRegistration(
        selectedReg.id,
        orNumber.trim(),
        daysOverride.length > 0 ? daysOverride : undefined,
        tooManyDays && specialCaseReason.trim() ? specialCaseReason.trim() : undefined,
        acknowledgeBlock,
        unpaidReason.trim() || undefined,
      )
      setBlockPrompt(null)
      setIsViewModalOpen(false)
      refreshAll()
      if (result?.account) {
        setAccountModal(result.account)
      } else {
        showResult('Registration accepted successfully!', 'success')
      }
    } catch (error) {
      if (error.response?.status === 409 && error.response?.data?.error === 'registration_blocked') {
        // Plate is flagged — ask CDSO to confirm additional review before proceeding
        setBlockPrompt(error.response.data)
      } else {
        console.error('Failed to accept registration:', error)
        showResult(error.response?.data?.error || 'Failed to accept registration.', 'error')
      }
    } finally {
      setSubmitting(false)
    }
  }

  const handleReject = async (e) => {
    e.preventDefault()
    if (!selectedReg) return
    // The form carries noValidate, so the browser's own bubble is gone and
    // its complaints have to be re-raised here.
    if (await notify.validation(fieldProblems(e.currentTarget))) return
    setSubmitting(true)
    try {
      await registrationApi.rejectRegistration(selectedReg.id, rejectReason)
      setIsRejectModalOpen(false)
      setIsViewModalOpen(false)
      setRejectReason('')
      refreshAll()
      showResult('Registration rejected successfully.', 'success')
    } catch (error) {
      showResult(error.response?.data?.error || 'Failed to reject registration.', 'error')
    } finally {
      setSubmitting(false)
    }
  }

  const showResult = (message, type = 'success') => setResultModal({ message, type })

  /* The server rebuilds the same document the approval email carried, so a
     reprint is never a different document from the original — the CDSO copy
     just also carries the applicant's uploaded scans.

     Takes the registration explicitly: the table row calls this without
     opening the modal, so there is no selected registration to read back. */
  const printRegistration = async (reg) => {
    if (!reg) return
    setPrintingRegId(reg.id)
    try {
      const blob = await registrationApi.getRegistrationPdf(reg.id)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `SLC Vehicle Registration - ${reg.plate_number || reg.full_name}.pdf`
      a.click()
      URL.revokeObjectURL(url)
      showResult('Registration PDF downloaded.', 'success')
    } catch (err) {
      // responseType 'blob' means an error body arrives as a Blob, not JSON —
      // read it back or the message would only ever be the generic one.
      let message = 'Failed to generate the registration PDF.'
      try {
        const body = err.response?.data
        if (body instanceof Blob) {
          const parsed = JSON.parse(await body.text())
          if (parsed?.detail) message = parsed.detail
        } else if (body?.detail) {
          message = body.detail
        }
      } catch { /* keep the generic message */ }
      showResult(message, 'error')
    } finally {
      setPrintingRegId(null)
    }
  }
  const openViewModal = (reg) => {
    setSelectedReg(reg)
    setIsViewModalOpen(true)
    // Prefilled from the receipt the applicant uploaded, so the reviewer is
    // confirming a number against the image rather than re-typing it.
    setOrNumber(reg.or_number || '')
    setDaysOverride(reg.campus_days?.length > 0 ? [...reg.campus_days] : [])
    setSpecialCaseReason('')
    setUnpaidReason('')
    // Per-day remaining student slots — so the admin can see capacity before
    // assigning campus days (same slot counts shown on the public register form).
    setScheduleSlots(null)
    if (reg.registrant_type === 'student') {
      registrationApi.getScheduleSlots().then(setScheduleSlots).catch(() => {})
    }
  }
  const openRejectModal = () => setIsRejectModalOpen(true)

  const filteredRegistrations = registrations.filter(r => {
    if (typeFilter !== 'all' && r.registrant_type !== typeFilter) return false
    if (paymentFilter !== 'all' && r.payment_status !== paymentFilter) return false
    if (search.trim()) {
      const q = search.trim().toLowerCase()
      if (
        !r.full_name?.toLowerCase().includes(q) &&
        !r.plate_number?.toLowerCase().includes(q) &&
        !r.registrant_type?.toLowerCase().includes(q)
      ) return false
    }
    return true
  })
  const paginatedRegistrations = filteredRegistrations.slice((regPage - 1) * itemsPerPage, regPage * itemsPerPage)
  const totalRegPages = Math.ceil(filteredRegistrations.length / itemsPerPage)
  /* The table only ever loads one status, but the summary counts every row in
     the system. So the payment and type tiles are scoped to the status on
     screen: their counts are what clicking them actually reveals. Without this
     an "Unpaid 120" tile sat above a table showing 8 rows, which reads as a
     broken page rather than as two different questions. The status tiles stay
     global — they are navigation, and their count is what you get after the
     click, because clicking one reloads the table for that status. */
  const activeStatus = (summary?.by_status ?? []).find(st => st.key === statusFilter)
  const scopedCount = (axis, key, fallback) =>
    activeStatus?.[axis]?.[key] ?? (activeStatus ? 0 : fallback)

  // An empty status leaves the refine panel with nothing to say, so it collapses
  // to a single line instead of holding open a full-height band for one sentence.
  const isRefineEmpty = !!activeStatus && activeStatus.count === 0

  /* Fee payment is a question about applications waiting on a decision: the
     reviewer is deciding whether to approve one, and whether the fee is settled
     is part of that. Once a registration is accepted, rejected or expired the
     answer is already baked into the outcome, so the axis only adds three tiles
     nobody acts on. It is shown for pending and hidden everywhere else. */
  const showPaymentFilter = statusFilter === 'pending'

  /* Changing status must drop the payment filter with it, not just hide the
     control. A reviewer who narrows pending to "Unpaid" and then clicks
     Accepted would otherwise land on a table silently trimmed by a filter that
     is no longer on screen to explain the missing rows. */
  const selectStatus = (key) => {
    setStatusFilter(key)
    if (key !== 'pending') setPaymentFilter('all')
    setRegPage(1)
  }

  const hasActiveFilters = typeFilter !== 'all' || paymentFilter !== 'all' || search.trim() !== ''
  const clearFilters = () => { setTypeFilter('all'); setPaymentFilter('all'); setSearch(''); setRegPage(1) }

  return (
    <>
      <div className="vehicle-registration-page">
        <div className="page-header vr-header-row">
          <div>
            <h1 className="page-title">Vehicle Registration Management</h1>
            <p className="page-subtitle">Review and process vehicle pass applications.</p>
          </div>
          <button className="btn-primary" onClick={handleViewRegistrationFormQR}>
            <QrCode size={18} /> Registration Form QR
          </button>
        </div>

        <ReportExportBar
          label="Registrations Report"
          fileBase="registrations-report"
          fetchBlob={registrationApi.exportRegistrationsReport}
          extraReports={[{
            key: 'summary',
            label: 'Summary PDF',
            fileBase: 'Registration Summary Report',
            fetch: registrationApi.exportRegistrationSummaryReport,
          }]}
        />

        {/* Status is navigation — it decides which rows the table loads at all —
            and the toolbar's status select is where that choice lives. Payment
            and registrant type only narrow whatever status is already on
            screen, so they stay here as tiles in one panel that states that
            scope once instead of tagging each heading with it. */}
        <div className="vr-stats">
          <div className="vr-stats-primary">
            <div className="vr-stat vr-stat--total">
              <span className="vr-stat-value">{summary ? summary.total : '—'}</span>
              <span className="vr-stat-label">Total Registrations</span>
            </div>
          </div>

          <div className={`vr-stats-refine${isRefineEmpty ? ' vr-stats-refine--empty' : ''}`}>
            <p className="vr-stats-refine-head">
              <SlidersHorizontal size={12} />
              Refine
              {activeStatus
                ? <> within <strong>{activeStatus.label.toLowerCase()}</strong></>
                : <> the table</>}
            </p>

            {/* Six zeroes under an empty status is noise, not information. */}
            {isRefineEmpty ? (
              <p className="vr-stats-refine-empty">
                Nothing to break down — no registrations at this stage.
              </p>
            ) : (
              <div className="vr-stats-refine-body">
                {showPaymentFilter && (
                  <div className="vr-stat-group">
                    <p className="vr-stat-group-title">Fee Payment</p>
                    <div className="vr-stat-row">
                      {(summary?.by_payment ?? []).map(pm => (
                        <StatTile
                          key={pm.key}
                          variant={`pay-${pm.key}`}
                          count={scopedCount('by_payment', pm.key, pm.count)}
                          label={pm.label}
                          active={paymentFilter === pm.key}
                          onSelect={pm.key === OTHER_KEY ? null : () => {
                            setPaymentFilter(paymentFilter === pm.key ? 'all' : pm.key); setRegPage(1)
                          }}
                          title={activeStatus
                            ? `Filter to ${pm.label.toLowerCase()} among ${activeStatus.label.toLowerCase()} registrations`
                            : `Filter the table to ${pm.label.toLowerCase()} registrations`}
                        />
                      ))}
                      {!summary && <span className="vr-stat-placeholder">Loading counts…</span>}
                    </div>
                  </div>
                )}

                {showPaymentFilter && <div className="vr-stat-divider" aria-hidden="true" />}

                <div className="vr-stat-group">
                  <p className="vr-stat-group-title">Registrant Type</p>
                  <div className="vr-stat-row">
                    {(summary?.by_type ?? []).map(t => (
                      <StatTile
                        key={t.key}
                        variant="type"
                        count={scopedCount('by_type', t.key, t.count)}
                        label={t.label}
                        active={typeFilter === t.key}
                        onSelect={t.key === OTHER_KEY ? null : () => {
                          setTypeFilter(typeFilter === t.key ? 'all' : t.key); setRegPage(1)
                        }}
                        title={activeStatus
                          ? `Filter to ${t.label.toLowerCase()} among ${activeStatus.label.toLowerCase()} registrations`
                          : `Filter the table to ${t.label.toLowerCase()} registrations`}
                      />
                    ))}
                    {!summary && <span className="vr-stat-placeholder">Loading counts…</span>}
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* SECTION: Registrations */}
        <div className="section-container">
          <h2 className="section-title" style={{ marginBottom: 14 }}>Applications</h2>
          <div className="vr-toolbar">
            <div className="vr-toolbar-left">
              <div className="vr-search-wrap">
                <Search size={14} className="vr-search-icon" />
                <input
                  className="vr-search-input"
                  placeholder="Search name, plate, type…"
                  value={search}
                  onChange={e => { setSearch(e.target.value); setRegPage(1) }}
                />
                {search && (
                  <button className="vr-search-clear" onClick={() => { setSearch(''); setRegPage(1) }}>
                    <X size={12} />
                  </button>
                )}
              </div>
            </div>
            <div className="vr-toolbar-right">
              <select
                className="filter-select"
                value={typeFilter}
                onChange={(e) => { setTypeFilter(e.target.value); setRegPage(1) }}
              >
                <option value="all">All Types</option>
                <option value="student">Student</option>
                <option value="employee">Employee</option>
                <option value="fetcher">Fetcher / Drop &amp; Go</option>
              </select>
              {/* Same rule as the tiles above — one axis, shown in one place or
                  neither, so the toolbar never offers a filter the counts strip
                  has already dropped. */}
              {showPaymentFilter && (
                <select
                  className="filter-select"
                  value={paymentFilter}
                  onChange={(e) => { setPaymentFilter(e.target.value); setRegPage(1) }}
                  title="Filter by whether the Vehicle Pass fee has been settled"
                >
                  <option value="all">All Payments</option>
                  <option value="paid">Paid</option>
                  <option value="unpaid">Unpaid</option>
                  <option value="exempt">Fee Exempt</option>
                </select>
              )}
              <select
                className="filter-select"
                value={statusFilter}
                onChange={(e) => selectStatus(e.target.value)}
              >
                <option value="pending">Pending Review</option>
                <option value="accepted">Accepted</option>
                <option value="rejected">Rejected</option>
                {/* The counts strip includes expired, and its tile drives this
                    select — without the option the dropdown would blank out. */}
                <option value="expired">Expired</option>
              </select>
              {hasActiveFilters && (
                <button className="vr-clear-btn" onClick={clearFilters} title="Clear filters">
                  <X size={13} /> Clear
                </button>
              )}
            </div>
          </div>
          <div className="table-container">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Type</th>
                  <th>Plate Number</th>
                  <th>Schedule</th>
                  <th>Submitted</th>
                  <th>Payment</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {loading && <TableLoaderRow colSpan={8} label="Loading registrations…" />}
                {!loading && paginatedRegistrations.map(r => (
                  <tr key={r.id}>
                    <td>{r.full_name}</td>
                    <td className="capitalize">{r.registrant_type}</td>
                    <td className="token-link">{r.plate_number}</td>
                    <td>{formatSchedule(r)}</td>
                    <td>{format(new Date(r.created_at), 'PP')}</td>
                    <td>
                      <span className={`payment-badge payment-${r.payment_status || 'unpaid'}`}>
                        {PAYMENT_LABELS[r.payment_status] || 'Unpaid'}
                      </span>
                    </td>
                    <td>
                      <span className={`status-badge status-${r.status}`}>
                        {r.status.charAt(0).toUpperCase() + r.status.slice(1)}
                      </span>
                      {r.is_special_case && (
                        <span className="special-case-badge">Special Case</span>
                      )}
                    </td>
                    <td>
                      <button className="view-btn" onClick={() => openViewModal(r)} title="View Details">
                        <Eye size={18} />
                      </button>
                      {r.status === 'accepted' && (
                        <button
                          className="view-btn"
                          disabled={printingRegId === r.id}
                          onClick={() => printRegistration(r)}
                          title="Print the registration confirmation, with the submitted documents"
                        >
                          {printingRegId === r.id
                            ? <span className="btn-spinner" />
                            : <Printer size={18} />}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
                {!loading && filteredRegistrations.length === 0 && (
                  <tr className="empty-row">
                    <td colSpan="8">No {statusFilter} registrations found{hasActiveFilters ? ' for these filters' : ''}.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {totalRegPages > 1 && (
            <div className="pagination-bar">
              <span className="pagination-info">
                Showing {(regPage - 1) * itemsPerPage + 1}–{Math.min(regPage * itemsPerPage, filteredRegistrations.length)} of {filteredRegistrations.length}
              </span>
              <div className="pagination-buttons">
                <button className="pagination-btn icon-only" disabled={regPage === 1} onClick={() => setRegPage(p => Math.max(1, p - 1))}>
                  <ChevronLeft size={16} />
                </button>
                <span className="pagination-info">Page {regPage} of {totalRegPages}</span>
                <button className="pagination-btn icon-only" disabled={regPage === totalRegPages} onClick={() => setRegPage(p => Math.min(totalRegPages, p + 1))}>
                  <ChevronRight size={16} />
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* MODAL: View Registration Details */}
      {isViewModalOpen && selectedReg && (
        <div className="modal-overlay">
          <div className="modal-content modal-lg">
            <div className="modal-header">
              <h2 className="modal-title">Registration Details</h2>
              <button className="modal-close-btn" onClick={() => setIsViewModalOpen(false)}><X size={24} /></button>
            </div>

            <div className="details-grid detail-divider">
              <div className="detail-item">
                <div className="detail-label">Full Name</div>
                <div className="detail-value">{selectedReg.full_name}</div>
              </div>
              <div className="detail-item">
                <div className="detail-label">Email</div>
                <div className="detail-value">{selectedReg.email}</div>
              </div>
              <div className="detail-item">
                <div className="detail-label">Type</div>
                <div className="detail-value capitalize">{selectedReg.registrant_type}</div>
              </div>
              <div className="detail-item">
                <div className="detail-label">Schedule</div>
                <div className="detail-value">{formatSchedule(selectedReg)}</div>
              </div>

              {selectedReg.registrant_type === 'student' ? (
                <>
                  <div className="detail-item">
                    <div className="detail-label">Student ID</div>
                    <div className="detail-value">{selectedReg.student_id}</div>
                  </div>
                  <div className="detail-item">
                    <div className="detail-label">Program &amp; Year</div>
                    <div className="detail-value">{selectedReg.program_year}</div>
                  </div>
                </>
              ) : selectedReg.registrant_type === 'employee' ? (
                <>
                  <div className="detail-item">
                    <div className="detail-label">Employee ID</div>
                    <div className="detail-value">{selectedReg.employee_id}</div>
                  </div>
                  <div className="detail-item">
                    <div className="detail-label">Department</div>
                    <div className="detail-value">{selectedReg.department}</div>
                  </div>
                </>
              ) : selectedReg.registrant_type === 'fetcher' ? (
                <>
                  <div className="detail-item">
                    <div className="detail-label">Fetcher Classification</div>
                    <div className="detail-value">
                      {selectedReg.fetcher_type === 'standby'
                        ? 'Standby — may park inside campus'
                        : selectedReg.fetcher_type === 'drop_and_go'
                          ? 'Fetcher / Drop & Go — allotted times only'
                          : '—'}
                    </div>
                  </div>
                  {(selectedReg.fetcher_students || []).length > 0 && (
                    <div className="detail-item" style={{ gridColumn: 'span 2' }}>
                      <div className="detail-label">Students to Fetch ({selectedReg.fetcher_students.length})</div>
                      <div className="detail-value" style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 4 }}>
                        {selectedReg.fetcher_students.map((s, i) => (
                          <div key={i} style={{ padding: '6px 12px', background: '#F7FAFC', border: '1px solid #D3E1EC', borderRadius: 8, fontSize: 13 }}>
                            <strong>{s.full_name}</strong>
                            {s.student_id && <span style={{ color: '#6B8CA6' }}> · ID: {s.student_id}</span>}
                            {s.student_level && <span style={{ color: '#6B8CA6' }}> · {s.student_level.toUpperCase()}</span>}
                            {s.program_year && <span style={{ color: '#6B8CA6' }}> · {s.program_year}</span>}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </>
              ) : null}

              <div className="detail-item">
                <div className="detail-label">Address</div>
                <div className="detail-value">{selectedReg.address || 'N/A'}</div>
              </div>
              <div className="detail-item">
                <div className="detail-label">Age</div>
                <div className="detail-value">{selectedReg.age || 'N/A'}</div>
              </div>
              <div className="detail-item">
                <div className="detail-label">Contact Number</div>
                <div className="detail-value">{selectedReg.contact_number}</div>
              </div>
              <div className="detail-item">
                <div className="detail-label">Driver's License</div>
                <div className="detail-value">{selectedReg.drivers_license || 'N/A'}</div>
              </div>
              {/* Attachments sit together and are always rendered: "nothing was
                  submitted" is itself a review finding, and an omitted block
                  reads as a missing field instead. */}
              <div className="detail-item vr-attach-block">
                <div className="detail-section-title vr-attach-heading">
                  <Paperclip size={14} /> Submitted Documents
                </div>
                <div className="vr-attach-grid">
                  <div className="vr-attach-cell">
                    <div className="detail-label">Driver's License Photo</div>
                    <AttachmentPreview
                      url={selectedReg.drivers_license_image}
                      alt={`Driver's license of ${selectedReg.full_name}`}
                    />
                  </div>

                  {/* Students must attach the enrolment proof; anyone else who
                      attached one still gets it shown. */}
                  {(selectedReg.registrant_type === 'student' || selectedReg.assessment_form) && (
                    <div className="vr-attach-cell">
                      <div className="detail-label">Assessment Form</div>
                      <AttachmentPreview
                        url={selectedReg.assessment_form}
                        alt={`Assessment form of ${selectedReg.full_name}`}
                      />
                    </div>
                  )}

                  {/* A fetcher proves nothing about their own enrolment — the
                      documents that matter are the ones for the students they
                      collect, one tile each so a missing form is visible against
                      the name it belongs to. */}
                  {selectedReg.registrant_type === 'fetcher'
                    && (selectedReg.fetcher_students || []).map((s, i) => (
                      <div className="vr-attach-cell" key={i}>
                        <div className="detail-label">
                          Assessment Form — {s.full_name || `Student #${i + 1}`}
                        </div>
                        <AttachmentPreview
                          url={s.assessment_form}
                          alt={`Assessment form of ${s.full_name || `student #${i + 1}`}`}
                        />
                      </div>
                    ))}

                  {/* The receipt is an upload like the other two and belongs on
                      the same row. What it means for the fee stays in Payment
                      below — the badge and OR number are the reviewer's answer
                      to "is this settled", the picture is only the proof. */}
                  <div className="vr-attach-cell">
                    <div className="detail-label">Official Receipt</div>
                    <AttachmentPreview
                      url={selectedReg.or_receipt_image}
                      alt={`Official receipt of ${selectedReg.full_name}`}
                      emptyText={selectedReg.payment_status === 'exempt'
                        ? 'No fee due — exempt'
                        : 'Not uploaded'}
                    />
                  </div>
                </div>
              </div>

              {/* ── Payment ──
                  Always rendered: "no receipt submitted" is the single most
                  useful thing this panel can tell a reviewer, and an absent
                  section reads as a missing field rather than an unpaid fee. */}
              <div className="detail-item" style={{ gridColumn: 'span 2' }}>
                <div className="detail-label">Payment</div>
                <div className="detail-value" style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 10, marginTop: 4 }}>
                  <span className={`payment-badge payment-${selectedReg.payment_status || 'unpaid'}`}>
                    {PAYMENT_LABELS[selectedReg.payment_status] || 'Unpaid'}
                  </span>
                  {selectedReg.or_number && (
                    <span style={{ fontSize: 12, color: '#35576F' }}>
                      OR No. <strong style={{ fontFamily: 'monospace' }}>{selectedReg.or_number}</strong>
                    </span>
                  )}
                  {selectedReg.amount_paid != null && (
                    <span style={{ fontSize: 12, color: '#35576F' }}>
                      ₱{Number(selectedReg.amount_paid).toFixed(2)}
                    </span>
                  )}
                  {selectedReg.paid_at && (
                    <span style={{ fontSize: 12, color: '#64839C' }}>
                      {format(new Date(selectedReg.paid_at), 'PP')}
                    </span>
                  )}
                </div>

                {selectedReg.payment_status === 'exempt' && (
                  <div className="detail-value vr-attach-empty" style={{ marginTop: 6 }}>
                    No fee due — this department is exempt.
                  </div>
                )}

                {selectedReg.unpaid_accept_reason && (
                  <div style={{ marginTop: 8, fontSize: 12, color: '#B45309', background: '#FEF3C7', border: '1px solid #FDE68A', borderRadius: 8, padding: '8px 10px' }}>
                    <strong>Approved unpaid:</strong> {selectedReg.unpaid_accept_reason}
                  </div>
                )}
              </div>

              {/* Authorized driver — set when the student is a minor / non-driver */}
              {selectedReg.driver_name && (
                <div className="detail-item" style={{ gridColumn: 'span 2' }}>
                  <div className="detail-label">Authorized Driver (student does not drive)</div>
                  <div className="detail-value">
                    {selectedReg.driver_name}
                    {selectedReg.driver_relationship && (
                      <span style={{ color: '#6B8CA6', fontWeight: 500 }}>
                        {' '}— {selectedReg.driver_relationship.replace('_', ' ')}
                      </span>
                    )}
                    {selectedReg.driver_contact && (
                      <span style={{ color: '#6B8CA6', fontWeight: 500 }}> · {selectedReg.driver_contact}</span>
                    )}
                  </div>
                </div>
              )}

              {selectedReg.campus_days?.length > 0 && (
                <div className="detail-item" style={{ gridColumn: 'span 2' }}>
                  <div className="detail-label">Campus Days</div>
                  <div className="detail-value" style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginTop: '4px' }}>
                    {selectedReg.campus_days.map(day => (
                      <span key={day} style={{ display: 'inline-block', padding: '3px 12px', borderRadius: '50px', background: '#03396C', color: '#fff', fontSize: '12px', fontWeight: 600 }}>{day}</span>
                    ))}
                  </div>
                </div>
              )}

              {selectedReg.or_number && (
                <div className="detail-item" style={{ gridColumn: 'span 2' }}>
                  <div className="detail-label">Official Receipt (OR) No.</div>
                  <div className="detail-value token-link" style={{ fontWeight: 700, color: '#0F7A5A' }}>{selectedReg.or_number}</div>
                </div>
              )}

              {selectedReg.status === 'accepted' && (
                <div className="detail-item" style={{ gridColumn: 'span 2' }}>
                  <div className="detail-label">System ID (Assigned)</div>
                  <div className="detail-value token-link" style={{ fontSize: '15px', fontWeight: 700, color: '#0F7A5A', letterSpacing: '0.5px' }}>
                    {selectedReg.registrant_type === 'student'
                      ? selectedReg.system_student_id || '—'
                      : selectedReg.system_employee_id || '—'}
                  </div>
                </div>
              )}
            </div>

            <h3 className="detail-section-title">Vehicle Information</h3>
            <div className="details-grid" style={{ marginBottom: '24px' }}>
              <div className="detail-item">
                <div className="detail-label">Plate Number</div>
                <div className="detail-value token-link" style={{ fontSize: '14px', fontWeight: 600 }}>{selectedReg.plate_number}</div>
              </div>
              <div className="detail-item">
                <div className="detail-label">Vehicle Type</div>
                <div className="detail-value">{selectedReg.vehicle_type}</div>
              </div>
              <div className="detail-item">
                <div className="detail-label">Color</div>
                <div className="detail-value">{selectedReg.vehicle_color}</div>
              </div>
              <div className="detail-item">
                <div className="detail-label">Conduction Number</div>
                <div className="detail-value">{selectedReg.conduction_number || 'N/A'}</div>
              </div>
            </div>

            {selectedReg.status === 'accepted' && selectedReg.is_special_case && (
              <div className="special-case-section">
                <div className="special-case-section-head">
                  <AlertCircle size={15} />
                  Special Case
                </div>
                <p className="special-case-reason">{selectedReg.special_case_reason}</p>
              </div>
            )}

            {selectedReg.status === 'pending' && (() => {
              const originalDays = selectedReg.campus_days || []
              // Up to 3 campus days is the normal allowance; more than 3 is a special case
              const tooManyDays  = daysOverride.length > 3
              // Fee-exempt applicants were never issued a receipt, so demanding
              // an OR number used to mean inventing one to enable the button.
              const feeExempt    = selectedReg.payment_status === 'exempt'
              // No receipt at all: the pass can still be granted, but only with
              // a stated reason, and the row keeps saying unpaid afterwards.
              // The backend falls back to the OR already on the row when the
              // field is left blank, so a cleared box is not an unpaid approval.
              const storedOr     = (selectedReg.or_number || '').trim()
              const acceptUnpaid = !feeExempt && !orNumber.trim() && !storedOr
              // Leaving the prefilled number untouched is always allowed. Older
              // rows (and walk-ins) can carry an OR shorter than 6 digits, which
              // orValid rejects — without this the reviewer could neither fix it
              // nor approve it, and the application was stuck for good.
              const orUnchanged  = !!storedOr && orNumber.trim() === storedOr
              const orAcceptable = orValid || orUnchanged
              const canAccept    = (feeExempt || orAcceptable || acceptUnpaid)
                && (!acceptUnpaid || unpaidReason.trim())
                && (!tooManyDays || specialCaseReason.trim())
              return (
                <div className="accept-inline-section">
                  <h3 className="accept-inline-title">
                    <Receipt size={15} /> Accept Registration
                  </h3>
                  <p className="accept-inline-desc">
                    {feeExempt
                      ? <>No Vehicle Pass fee is due from <strong>{selectedReg.full_name}</strong> — their department is exempt.</>
                      : selectedReg.payment_status === 'paid'
                        ? <>Check the Official Receipt against the photo <strong>{selectedReg.full_name}</strong> uploaded under Submitted Documents, then approve.</>
                        : <><strong>{selectedReg.full_name}</strong> has not submitted a receipt. Enter their OR number if they brought it to the counter.</>}
                  </p>

                  {!feeExempt && (
                    <div className="form-group">
                      <label className="form-label">
                        Official Receipt (OR) Number {!acceptUnpaid && <span className="required">*</span>}
                      </label>
                      <input
                        type="text"
                        inputMode="numeric"
                        maxLength={7}
                        className={`form-input${orValid ? ' input-valid' : ''}`}
                        value={orNumber}
                        onChange={(e) => setOrNumber(e.target.value.replace(/\D/g, '').slice(0, 7))}
                        placeholder="e.g. 1380093"
                        disabled={submitting}
                      />
                      <p className="form-hint">
                        {selectedReg.payment_status === 'paid'
                          ? 'Prefilled from the receipt the applicant uploaded — correct it only if it does not match the receipt under Submitted Documents.'
                          : `Issued by the Accounting Office upon payment of ₱${selectedReg.registrant_type === 'employee' ? '150.00 (50% employee discount)' : '300.00'}`}
                      </p>
                    </div>
                  )}

                  {acceptUnpaid && (
                    <div className="form-group special-case-reason-group">
                      <label className="form-label special-case-reason-label">
                        <AlertCircle size={13} />
                        Reason for Approving Unpaid <span className="required">*</span>
                      </label>
                      <p className="form-hint special-case-added-hint">
                        No Official Receipt is on file. The pass will be issued, but the
                        application stays marked <strong>Unpaid</strong> so the fee can still be
                        collected — give a reason for approving it now.
                      </p>
                      <textarea
                        className="form-textarea"
                        rows={2}
                        value={unpaidReason}
                        onChange={(e) => setUnpaidReason(e.target.value)}
                        placeholder="e.g. Accounting Office closed; OR to be presented on Monday…"
                        disabled={submitting}
                      />
                    </div>
                  )}

                  {selectedReg.registrant_type === 'student' && (
                    <div className="form-group">
                      <label className="form-label">
                        <CalendarDays size={14} style={{ marginRight: 6, verticalAlign: 'middle' }} />
                        Campus Schedule
                      </label>
                      {selectedReg.campus_days?.length > 0 && (
                        <p className="form-hint" style={{ marginBottom: 8 }}>
                          Applicant chose:{' '}
                          {selectedReg.campus_days.map(d => (
                            <span key={d} className="day-chip">{d}</span>
                          ))}
                        </p>
                      )}
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 6 }}>
                        {ALL_DAYS.map(day => {
                          const sel = daysOverride.includes(day)
                          const isAdded = sel && !originalDays.includes(day)
                          const slot = scheduleSlots?.[day]
                          const isFull = slot && slot.available === 0 && !sel
                          return (
                            <button
                              key={day}
                              type="button"
                              disabled={submitting}
                              onClick={() => setDaysOverride(prev =>
                                prev.includes(day) ? prev.filter(d => d !== day) : [...prev, day]
                              )}
                              className={`day-override-btn${sel ? ' selected' : ''}${isAdded ? ' added' : ''}${isFull ? ' full' : ''}`}
                              title={slot ? `${slot.used}/${slot.limit} slots used — ${slot.available} available` : undefined}
                            >
                              <span>{DAY_SHORT[day]}</span>
                              <span className="day-override-slots">
                                {slot ? (isFull ? 'FULL' : `${slot.available} left`) : '—'}
                              </span>
                            </button>
                          )
                        })}
                      </div>
                      <p className="form-hint" style={{ marginTop: 0, marginBottom: 6 }}>
                        Numbers show remaining student slots per day. Assigning a <strong>full</strong> day
                        is allowed for special cases but exceeds the day's capacity.
                      </p>
                      {daysOverride.length > 0 ? (
                        <p className="form-hint">Assigned: <strong>{daysOverride.join(', ')}</strong></p>
                      ) : (
                        <p className="form-hint" style={{ color: '#C62828' }}>No days selected — original choice will be kept.</p>
                      )}
                    </div>
                  )}

                  {tooManyDays && (
                    <div className="form-group special-case-reason-group">
                      <label className="form-label special-case-reason-label">
                        <AlertCircle size={13} />
                        Reason for Extra Days <span className="required">*</span>
                      </label>
                      <p className="form-hint special-case-added-hint">
                        You assigned <strong>{daysOverride.length} days</strong> — more than the standard 3-day allowance. Provide a reason; this registration will be flagged as a Special Case.
                      </p>
                      <textarea
                        className="form-textarea"
                        rows={2}
                        value={specialCaseReason}
                        onChange={(e) => setSpecialCaseReason(e.target.value)}
                        placeholder="e.g. Faculty clearance for make-up sessions on Friday…"
                        disabled={submitting}
                      />
                    </div>
                  )}

                  <div className="detail-actions">
                    <button className="btn-danger" onClick={openRejectModal} disabled={submitting}>
                      <X size={18} /> Reject
                    </button>
                    <button
                      className="btn-success"
                      onClick={() => setShowAcceptConfirm(true)}
                      disabled={submitting || !canAccept}
                      title={
                        acceptUnpaid && !unpaidReason.trim()
                          ? 'Enter the OR number, or give a reason for approving this application unpaid'
                          : !feeExempt && !acceptUnpaid && !orAcceptable
                            ? 'Enter a valid OR number to enable'
                            : tooManyDays && !specialCaseReason.trim()
                              ? 'Provide a reason for granting more than 3 days'
                              : ''
                      }
                    >
                      {submitting ? 'Processing…' : <><Check size={16} /> Confirm &amp; Accept</>}
                    </button>
                  </div>
                </div>
              )
            })()}

            {selectedReg.status === 'accepted' && (
              <div className="detail-actions">
                <button className="btn-outline" onClick={handleViewVehicleQR}>
                  <Eye size={18} /> View Vehicle QR
                </button>
                <button
                  className="btn-outline"
                  disabled={printingRegId === selectedReg.id}
                  onClick={() => printRegistration(selectedReg)}
                  title="The confirmation emailed on approval, plus the documents the applicant uploaded"
                >
                  <Printer size={18} />
                  {printingRegId === selectedReg.id ? 'Preparing…' : 'Print Registration'}
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* MODAL: Reject Reason */}
      {isRejectModalOpen && (
        <div className="modal-overlay">
          <div className="modal-content">
            <div className="modal-header">
              <h2 className="modal-title danger">Reject Registration</h2>
              <button className="modal-close-btn" onClick={() => setIsRejectModalOpen(false)}><X size={20} /></button>
            </div>
            <form onSubmit={handleReject} noValidate>
              <div className="form-group">
                <label className="form-label">Reason for Rejection <span className="required">*</span></label>
                <textarea
                  className="form-textarea"
                  rows={4}
                  value={rejectReason}
                  onChange={(e) => setRejectReason(e.target.value)}
                  placeholder="Explain why this registration is being rejected..."
                  required
                />
              </div>
              <div className="modal-actions">
                <button type="button" className="btn-outline" onClick={() => setIsRejectModalOpen(false)} disabled={submitting}>Cancel</button>
                <button type="submit" className="btn-danger" disabled={submitting}>
                  {submitting ? 'Processing...' : 'Confirm Rejection'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL: Accept Confirmation */}
      {showAcceptConfirm && selectedReg && (
        <div className="modal-overlay">
          <div className="modal-content confirm-modal">
            <button className="modal-close-btn" style={{ position: 'absolute', top: 12, right: 12 }} onClick={() => setShowAcceptConfirm(false)}><X size={18} /></button>
            <div className="confirm-icon success" style={{ marginBottom: 8 }}>
              <Check size={24} />
            </div>
            <h2 className="confirm-title">Accept Registration?</h2>
            <p className="confirm-message">
              You are accepting <strong>{selectedReg.full_name}</strong>'s registration for plate <strong>{selectedReg.plate_number}</strong> with OR No. <strong>{orNumber}</strong>.
              {daysOverride.length > 0 && <> Campus days: <strong>{daysOverride.join(', ')}</strong>.</>}
            </p>
            <div className="confirm-actions" style={{ gap: 10 }}>
              <button className="btn-outline" onClick={() => setShowAcceptConfirm(false)} disabled={submitting}>Cancel</button>
              <button
                className="btn-success"
                disabled={submitting}
                onClick={() => { setShowAcceptConfirm(false); confirmAccept() }}
              >
                {submitting ? 'Processing…' : <><Check size={15} /> Yes, Accept</>}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* MODAL: QR Viewer */}
      {isQRModalOpen && qrDisplayData && (
        <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && setIsQRModalOpen(false)}>
          <div className="modal-content qr-viewer-modal">
            <div className="modal-header">
              <h2 className="modal-title">{qrDisplayData.title}</h2>
              <button className="modal-close-btn" onClick={() => setIsQRModalOpen(false)}><X size={24} /></button>
            </div>
            <p className="qr-viewer-subtitle">{qrDisplayData.subtitle}</p>
            <div className="qr-display-wrapper" ref={qrPrintRef}>
              <QRCodeSVG value={qrDisplayData.payload} size={220} level="H" includeMargin={true} />
            </div>
            <div className="qr-data-box">
              <p className="qr-label">{qrDisplayData.type === 'register-link' ? 'Registration Link' : 'Encoded Data'}</p>
              <code className="qr-code-data">{qrDisplayData.payload}</code>
            </div>
            <div style={{ display: 'flex', gap: 10 }}>
              <button className="btn-primary" onClick={handleCopyQRData} style={{ flex: 1, justifyContent: 'center' }}>
                {qrViewerCopied
                  ? <><Check size={16} />Copied!</>
                  : <><Copy size={16} /> {qrDisplayData.type === 'register-link' ? 'Copy Link' : 'Copy Data'}</>}
              </button>
              {qrDisplayData.type === 'register-link' && (
                <button className="btn-outline" onClick={handlePrintQR} style={{ flex: 1, justifyContent: 'center' }}>
                  <Printer size={16} /> Print
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* MODAL: Result/Success/Error */}
      {/* MODAL: Registration block — plate flagged by prior 3rd-offense violation */}
      {blockPrompt && (
        <div className="modal-overlay">
          <div className="modal-content confirm-modal">
            <div className="confirm-icon error"><AlertTriangle size={24} /></div>
            <h2 className="confirm-title">Plate Flagged for Review</h2>
            <p className="confirm-message">
              {blockPrompt.detail}
            </p>
            {blockPrompt.registration_block && (
              <div style={{ background: '#FCEDED', border: '1px solid #F3C0C0', borderRadius: 8, padding: '10px 14px', margin: '4px 0 12px', fontSize: 13, textAlign: 'left' }}>
                <div><strong>Flagged violations:</strong> {blockPrompt.registration_block.count}</div>
                <div><strong>Most recent:</strong> {blockPrompt.registration_block.latest_type} ({blockPrompt.registration_block.latest_status})</div>
              </div>
            )}
            <div className="confirm-actions" style={{ display: 'flex', gap: 8 }}>
              <button className="btn-secondary" onClick={() => setBlockPrompt(null)} disabled={submitting} style={{ flex: 1, justifyContent: 'center' }}>Cancel</button>
              <button className="btn-primary" onClick={() => confirmAccept(true)} disabled={submitting} style={{ flex: 1, justifyContent: 'center', background: '#C62828', borderColor: '#C62828' }}>
                {submitting ? 'Accepting…' : 'Reviewed — Accept Anyway'}
              </button>
            </div>
          </div>
        </div>
      )}

      {resultModal && (
        <div className="modal-overlay">
          <div className="modal-content confirm-modal">
            <div className={`confirm-icon ${resultModal.type === 'success' ? 'success' : 'error'}`}>
              {resultModal.type === 'success' ? <Check size={24} /> : <X size={24} />}
            </div>
            <h2 className="confirm-title">{resultModal.type === 'success' ? 'Success' : 'Error'}</h2>
            <p className="confirm-message">{resultModal.message}</p>
            <div className="confirm-actions">
              <button className="btn-primary" onClick={() => setResultModal(null)} style={{ width: '100%', justifyContent: 'center' }}>Close</button>
            </div>
          </div>
        </div>
      )}

      {/* MODAL: Account Created */}
      {accountModal && (
        <div className="modal-overlay">
          <div className="modal-content modal-account">
            <div className="account-modal-header">
              <div className="account-success-badge"><ShieldCheck size={22} /></div>
              <div>
                <h2 className="modal-title" style={{ marginBottom: 4 }}>Account Created Successfully</h2>
                <p className="account-modal-subtitle">The vehicle owner account has been provisioned and credentials sent via email.</p>
              </div>
              <button className="modal-close-btn" onClick={() => setAccountModal(null)}><X size={22} /></button>
            </div>

            <div className="account-id-banner">
              <div className="account-id-item">
                <span className="account-id-label">Portal Account ID</span>
                <span className="account-id-value">{accountModal.user_code || '—'}</span>
              </div>
              <div className="account-id-divider" />
              <div className="account-id-item">
                <span className="account-id-label">System Registration ID</span>
                <span className="account-id-value">{accountModal.system_id || '—'}</span>
              </div>
            </div>

            <div className="account-credentials-box">
              <div className="account-cred-header">
                <KeyRound size={16} />
                Login Credentials
                <span className="account-cred-note">Sent to owner's email</span>
              </div>
              <div className="account-cred-row">
                <Mail size={14} />
                <span className="account-cred-field">Email</span>
                <span className="account-cred-val">{accountModal.email}</span>
              </div>
              <div className="account-cred-row">
                <KeyRound size={14} />
                <span className="account-cred-field">Password</span>
                <span className="account-cred-val account-cred-muted">Sent securely to owner's email</span>
              </div>
              {/* The send is queued rather than awaited, so approving no longer
                  waits on the mail server. Delivery therefore is not known yet;
                  a failure raises a warning in Notifications instead of here. */}
              <p className="account-cred-warning is-ok">
                ✓ Credentials are on their way to the vehicle owner. If delivery fails,
                a warning appears in <strong>Notifications</strong> — share the details
                directly if that happens.
              </p>
            </div>

            <div className="account-sections">
              <div className="account-info-section">
                <div className="account-section-head"><User size={14} /> Personal Information</div>
                <div className="account-info-grid">
                  <div className="account-info-item">
                    <span className="account-info-label">Full Name</span>
                    <span className="account-info-val">{accountModal.full_name}</span>
                  </div>
                  <div className="account-info-item">
                    <span className="account-info-label">Type</span>
                    <span className="account-info-val account-info-cap">{accountModal.registrant_type}</span>
                  </div>
                  <div className="account-info-item">
                    <span className="account-info-label">Contact</span>
                    <span className="account-info-val">{accountModal.contact_number || '—'}</span>
                  </div>
                  <div className="account-info-item">
                    <span className="account-info-label">Schedule</span>
                    <span className="account-info-val">{formatSchedule(accountModal)}</span>
                  </div>
                </div>
              </div>
              <div className="account-info-section">
                <div className="account-section-head"><Car size={14} /> Vehicle Information</div>
                <div className="account-info-grid">
                  <div className="account-info-item">
                    <span className="account-info-label">Plate Number</span>
                    <span className="account-info-val account-plate">{accountModal.plate_number}</span>
                  </div>
                  <div className="account-info-item">
                    <span className="account-info-label">Vehicle Type</span>
                    <span className="account-info-val">{accountModal.vehicle_type}</span>
                  </div>
                </div>
              </div>
            </div>

            <div className="modal-actions">
              <button className="btn-primary account-done-btn" onClick={() => setAccountModal(null)}>
                <Check size={16} /> Done
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
