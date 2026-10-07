import { useState, useEffect, useMemo, useRef } from 'react'
import { useLiveUpdates } from '../../realtime/useLiveUpdates'
import {
  AlertTriangle, CheckCircle, Filter,
  RotateCcw, Search, Bell, X,
  ChevronLeft, ChevronRight, Loader2, Eye,
  FileText, ShieldOff, ClipboardCheck, Timer, Archive,
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
import { VIOLATION_TYPES, violationLabel, violationTypeKey, violationTypeName } from '../../utils/violationTypes'


const OFFENSE_LABELS = { 1: '1st', 2: '2nd', 3: '3rd' }

const FILTER_OPTIONS = [
  { value: 'all',        label: 'All' },
  { value: 'warning',    label: 'Warnings' },
  { value: 'confiscated', label: 'Confiscated (3rd)' },
  { value: 'resolved',   label: 'Cleared / Resolved' },
]

// One definition per status bucket, used by the filter list, the counts on
// the buttons and the report query alike — a badge that disagrees with the
// list it opens reads as the page being wrong.
//
// `settled` spans several things because several paths end a violation:
// clearing sets CLEARED, lifting sets LIFTED, the expiry job sets ARCHIVED
// when the owner's account expires unbanned, and the plain resolve PATCH only
// sets is_resolved and leaves the status at 'warning'. Checking status alone
// left a resolved warning counted as an active one.
const ENDED_STATUSES = ['cleared', 'lifted', 'archived']
const isSettled     = v => v.is_resolved || ENDED_STATUSES.includes(v.status)
const isActiveWarn  = v => !isSettled(v) && v.status === 'warning'
// The penalty keys on the offense number, not on a status. 'fee_imposed' is
// kept in the test only so any legacy row that escaped migration 0016 still
// lands somewhere.
const isConfiscated = v => !isSettled(v) && (v.offense_number === 3 || v.status === 'fee_imposed')

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

// The table shows one entry per person rather than one per violation. A
// registered owner is keyed by email, so offenses on each of their vehicles
// land together; anyone with no account behind the plate is keyed by the plate.
function groupKey(v) {
  const email = v.owner_email?.trim().toLowerCase()
  return email ? `owner:${email}` : `plate:${v.plate_number || v.id}`
}

function timeAgo(ts) {
  try { return formatDistanceToNow(new Date(ts), { addSuffix: true }) } catch { return '' }
}

function fmtDate(ts) {
  try { return format(parseISO(ts), 'MMM d, yyyy') } catch { return '—' }
}

function fmtDateTime(ts) {
  try { return format(parseISO(ts), 'MMM d, yyyy h:mm a') } catch { return '—' }
}


function OffenseBadge({ num }) {
  if (!num) return null
  const cls = num === 3 ? 'vm-offense-3' : num === 2 ? 'vm-offense-2' : 'vm-offense-1'
  return <span className={`vm-offense-badge ${cls}`}>{OFFENSE_LABELS[num] ?? `${num}th`} offense</span>
}

// ─── Confiscation column ──────────────────────────────────────────────────────
// `c` is the server's penalty_state for the person behind the row. The clock
// ticks here, per cell, rather than in the page: a page-level tick would
// re-render the whole table (and remount its inline components) every second.
function countdownText(ms) {
  const s = Math.floor(ms / 1000)
  const d = Math.floor(s / 86400)
  const hh = String(Math.floor((s % 86400) / 3600)).padStart(2, '0')
  const mm = String(Math.floor((s % 3600) / 60)).padStart(2, '0')
  const ss = String(s % 60).padStart(2, '0')
  return `${d ? `${d}d ` : ''}${hh}:${mm}:${ss}`
}

function ConfiscationCell({ c }) {
  const endsAt = c?.ends_at ? new Date(c.ends_at).getTime() : null
  const [now, setNow] = useState(() => Date.now())
  const running = c?.state === 'active' && endsAt !== null && now < endsAt

  useEffect(() => {
    if (!running) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [running])

  if (!c) return <span className="vm-conf-none">—</span>

  if (c.state === 'lifted') {
    return (
      <span className="vm-conf vm-conf-ended">
        <span className="vm-conf-label"><CheckCircle size={12} /> Lifted early</span>
        <small>by the CDSO</small>
      </span>
    )
  }
  if (c.state === 'active' && c.indefinite) {
    return (
      <span className="vm-conf vm-conf-active">
        <span className="vm-conf-label"><ShieldOff size={12} /> Confiscated</span>
        <small>Indefinite — until the CDSO lifts it</small>
      </span>
    )
  }
  // 'ended' from the server, or an 'active' one whose clock has run out
  // while the page was open — the cell flips on its own, no refresh needed.
  if (!running) {
    return (
      <span className="vm-conf vm-conf-ended">
        <span className="vm-conf-label"><CheckCircle size={12} /> Confiscation ended</span>
        {endsAt && <small>{format(endsAt, 'MMM d, yyyy h:mm a')}</small>}
      </span>
    )
  }
  return (
    <span className="vm-conf vm-conf-active">
      <span className="vm-conf-label"><Timer size={12} /> Confiscated</span>
      {/* The digits never split; only "left" may drop to the next line. */}
      <strong className="vm-conf-clock"><span>{countdownText(endsAt - now)}</span> left</strong>
      <small>Ends {format(endsAt, 'MMM d, yyyy h:mm a')}</small>
    </span>
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
// Lifting is not the same as clearing. Clearing says the offense happened and
// the fee was settled; lifting says it should never have been issued, so it
// stops counting and the owner's remaining violations of that type step back
// down a number. The reason is mandatory — this erases an offense from someone's
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
          It stops counting toward the offense ladder, and any later violations
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
  // The report bar's own Date From / Date To. Held here, not just inside the
  // bar, because the table has to narrow to the same range the export will -
  // otherwise the screen shows rows and hands back an empty file.
  const [exportRange, setExportRange]     = useState({ from: '', to: '' })
  const [actionLoading, setActionLoading] = useState(null)
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

    if (filter === 'warning')     list = list.filter(isActiveWarn)
    if (filter === 'confiscated') list = list.filter(isConfiscated)
    if (filter === 'resolved')    list = list.filter(isSettled)

    if (typeFilter !== 'all') list = list.filter(v => violationTypeKey(v.violation_type) === typeFilter)

    // Same precedence the export uses: an explicit date in the report bar wins
    // over the period buttons, and the buttons apply when the box is empty.
    // Parsed as `T00:00:00` / `T23:59:59.999` so the bounds are local days -
    // `new Date('2026-09-22')` is UTC midnight, which in Manila drops eight
    // hours of the day the admin picked. The server reads them as local dates
    // too (filter_local_date_range), so both sides cut at the same instant.
    if (exportRange.from) {
      const start = new Date(`${exportRange.from}T00:00:00`)
      list = list.filter(v => new Date(v.issued_at) >= start)
    } else if (datePeriod !== 'all') {
      const cutoff = getPeriodStart(datePeriod)
      list = list.filter(v => new Date(v.issued_at) >= cutoff)
    }
    if (exportRange.to) {
      const end = new Date(`${exportRange.to}T23:59:59.999`)
      list = list.filter(v => new Date(v.issued_at) <= end)
    }

    const q = search.trim().toLowerCase()
    if (q) {
      // conduction_number is searched separately from plate_number even though
      // the serialized plate_number already falls back to it: that fallback
      // only applies when there is no plate, so a vehicle carrying both would
      // be findable by its conduction number in the exported file and not in
      // this table. The report endpoint searches both columns, and the two
      // have to narrow to the same rows.
      list = list.filter(v =>
        v.plate_number?.toLowerCase().includes(q) ||
        v.conduction_number?.toLowerCase().includes(q) ||
        v.owner_name?.toLowerCase().includes(q) ||
        v.owner_email?.toLowerCase().includes(q) ||
        v.notes?.toLowerCase().includes(q)
      )
    }

    list.sort((a, b) => new Date(b.issued_at) - new Date(a.issued_at))
    return list
  }, [violations, filter, typeFilter, datePeriod, search, exportRange])

  // Grouped after filtering, so a group holds only the offenses that match
  // the filters on screen. `filtered` is newest first and a Map keeps insertion
  // order, so groups are ordered by their latest offense and items[0] is it.
  const groups = useMemo(() => {
    const map = new Map()
    for (const v of filtered) {
      const key = groupKey(v)
      let g = map.get(key)
      if (!g) { g = { key, items: [], plates: [] }; map.set(key, g) }
      g.items.push(v)
      if (v.plate_number && !g.plates.includes(v.plate_number)) g.plates.push(v.plate_number)
    }
    return [...map.values()]
  }, [filtered])

  // The person whose profile is open, by groupKey. Their rows are looked up
  // from the live list on every render, so a lift or a background refresh
  // shows up in the open profile straight away.
  const [profileKey, setProfileKey] = useState(null)
  const profileItems = useMemo(
    () => profileKey
      ? violations.filter(v => groupKey(v) === profileKey)
          .sort((a, b) => new Date(b.issued_at) - new Date(a.issued_at))
      : [],
    [violations, profileKey],
  )

  // Escape closes the profile, unless a dialog opened from it is on top.
  const dialogOnTop = !!(liftModal || orModal || confirmAction || resultModal)
  useEffect(() => {
    if (!profileKey || dialogOnTop) return
    const onKey = e => { if (e.key === 'Escape') setProfileKey(null) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [profileKey, dialogOnTop])

  useEffect(() => { setPage(1) }, [filter, typeFilter, datePeriod, search, exportRange])

  // Marks the card when the table is wider than it, so the pinned Actions
  // column draws its edge shadow only while there is something beneath it.
  const cardRef = useRef(null)
  const [overflowing, setOverflowing] = useState(false)
  useEffect(() => {
    const card = cardRef.current
    if (!card || typeof ResizeObserver === 'undefined') return
    const check = () => setOverflowing(card.scrollWidth > card.clientWidth + 1)
    const ro = new ResizeObserver(check)
    ro.observe(card)
    if (card.firstElementChild) ro.observe(card.firstElementChild)
    return () => ro.disconnect()
  }, [loading])

  const totalPages = Math.max(1, Math.ceil(groups.length / PAGE_SIZE))
  const paginated  = groups.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)

  // The screen's filters, in the query-parameter names the report endpoint
  // reads. ReportExportBar drops anything that is 'all' or empty, and its own
  // Date From box overrides date_from below when it is filled — the period
  // buttons are a date range like any other, so they travel the same way.
  const reportFilters = {
    status:         filter,
    violation_type: typeFilter,
    search:         search.trim(),
    date_from:      datePeriod === 'all' ? '' : format(getPeriodStart(datePeriod), 'yyyy-MM-dd'),
  }

  // Named on the bar so it is clear before clicking what the file will hold.
  const reportFilterSummary = [
    filter     !== 'all' ? FILTER_OPTIONS.find(o => o.value === filter)?.label : '',
    typeFilter !== 'all' ? violationTypeName(typeFilter) : '',
    datePeriod !== 'all' ? DATE_PERIODS.find(d => d.value === datePeriod)?.label : '',
    search.trim() ? `“${search.trim()}”` : '',
  ].filter(Boolean).join(' · ')

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

  function StatusBadge({ v }) {
    // Checked before `cleared`/`is_resolved`: a lifted violation is also
    // resolved, and showing it as "Cleared" would claim a fee was settled.
    if (v.status === 'lifted')
      return (
        <span className="vm-status vm-status-lifted" title={v.lifted_reason || undefined}>
          <ShieldOff size={12} /> Lifted <em>· false alarm</em>
        </span>
      )
    // Also resolved, so checked before the cleared test for the same reason.
    if (v.status === 'archived')
      return (
        <span className="vm-status vm-status-archived" title="Closed when the owner's account expired">
          <Archive size={12} /> Archived <em>· account expired</em>
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
        title="Void as a false alarm and renumber the remaining offenses"
      >
        {busy ? <Loader2 size={13} className="vm-spin" /> : <ShieldOff size={13} />} Lift
      </button>
    )
    const canLift = !ENDED_STATUSES.includes(v.status)

    // New-style offense violations
    if (v.offense_number) {
      if (ENDED_STATUSES.includes(v.status)) return null
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
    if (ENDED_STATUSES.includes(v.status)) return null
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

  // One summary per person (see groupKey), whether they have one violation
  // or several. Shared by the table row and the profile.
  function summarize(items) {
    const latest = items[0]
    const oldest = items[items.length - 1]
    const open   = items.filter(v => !isSettled(v))
    const typeCounts = {}
    for (const v of items) {
      const key = violationTypeKey(v.violation_type)
      typeCounts[key] = (typeCounts[key] || 0) + 1
    }
    // The highest offense still standing is where the person sits on the
    // ladder; with nothing standing, the highest they ever reached.
    const ranked = open.length ? open : items
    const topOffense = Math.max(0, ...ranked.map(v => v.offense_number || 0))
    // Penalty state is per person, so every row carries the same one; the
    // newest row's is the freshest.
    const confiscation = items.find(v => v.confiscation)?.confiscation ?? null
    return { latest, oldest, open, typeCounts, topOffense, confiscation }
  }

  // Plain render functions, not components: declared in here, a component
  // would be a new type on every render and remount the whole table body.
  //
  // One row per person. Nothing is decided from the table: the eye button
  // (or a click anywhere on the row) opens the profile, which lists every
  // violation in full and carries Lift / Resolve / Clear for each one.
  function renderPersonRow(g) {
    const { latest, oldest, open, typeCounts, topOffense, confiscation } = summarize(g.items)
    const single = g.items.length === 1
    const who = latest.owner_name || g.plates[0] || 'this owner'
    return (
      <tr
        key={g.key}
        className={`vm-person-row ${open.length ? '' : 'vm-row-resolved'}`}
        onClick={() => setProfileKey(g.key)}
      >
        <td className="vm-plate vm-cell-plate" data-label="Plate">
          <div className="vm-group-plates">
            {g.plates.map(p => <span key={p}>{p}</span>)}
          </div>
        </td>
        <td className="vm-cell-owner" data-label="Owner">
          <div className="vm-owner">
            <span className="vm-owner-name">{latest.owner_name || '—'}</span>
            {latest.owner_email && (
              <span className="vm-owner-email">{latest.owner_email}</span>
            )}
          </div>
        </td>
        <td className="vm-cell-type" data-label="Type / Offense">
          <div className="vm-type-cell">
            {single ? (
              <span className={`vm-type-pill vm-type-${violationTypeKey(latest.violation_type)}`}>
                {violationLabel(latest)}
              </span>
            ) : Object.entries(typeCounts).map(([type, n]) => (
              <span key={type} className={`vm-type-pill vm-type-${type}`}>
                {violationTypeName(type)}{n > 1 && <b className="vm-type-count">×{n}</b>}
              </span>
            ))}
            {topOffense > 0 && <OffenseBadge num={topOffense} />}
          </div>
        </td>
        <td className="vm-cell-notes" data-label="Notes">
          <div className="vm-notes" title={latest.notes || ''}>
            {latest.notes
              ? <>{!single && <em className="vm-group-latest">Latest:</em>} {latest.notes}</>
              : '—'}
          </div>
        </td>
        <td className="vm-time vm-cell-issued" data-label="Issued" title={fmtDate(latest.issued_at)}>
          {timeAgo(latest.issued_at)}
          {single ? (
            (latest.on_duty_guard_name || latest.issued_by_name) && (
              <span className="vm-issued-guard">
                {latest.on_duty_guard_name
                  ? `On duty: ${latest.on_duty_guard_name}`
                  : `By: ${latest.issued_by_name}`}
              </span>
            )
          ) : (
            <span className="vm-issued-guard">First: {fmtDate(oldest.issued_at)}</span>
          )}
        </td>
        <td className="vm-cell-conf" data-label="Confiscation"><ConfiscationCell c={confiscation} /></td>
        <td className="vm-cell-status" data-label="Status">
          {single
            ? <StatusBadge v={latest} />
            : open.length
              ? <span className="vm-status vm-status-warning"><AlertTriangle size={12} /> {open.length} open</span>
              : <span className="vm-status vm-status-resolved"><CheckCircle size={12} /> All settled</span>}
        </td>
        <td className="vm-cell-actions" data-label="Actions">
          <button
            className="vm-btn vm-btn-view"
            title={single ? 'View violation' : `View all ${g.items.length} violations`}
            aria-label={`View violations for ${who}`}
            onClick={e => { e.stopPropagation(); setProfileKey(g.key) }}
          >
            <Eye size={15} />
            {!single && <span className="vm-view-count">{g.items.length}</span>}
          </button>
        </td>
      </tr>
    )
  }

  // ─── Violation profile ────────────────────────────────────────────────────
  // Laid out like User Management's View Profile: who the person is and where
  // they stand, then every violation on record stacked in full, newest first.
  // Built from the whole list, not the filtered one, so a filter on the table
  // never hides part of someone's record here. Each card carries its own
  // actions; the Lift and result dialogs open on top and the profile stays.
  function renderProfile(items) {
    const { latest, oldest, open, topOffense, confiscation } = summarize(items)
    const plates = [...new Set(items.map(v => v.plate_number).filter(Boolean))]
    const name = latest.owner_name || plates[0] || 'Unknown owner'
    const close = () => setProfileKey(null)
    return (
      <div className="vm-overlay" onClick={close}>
        <div
          className="vm-profile"
          role="dialog"
          aria-modal="true"
          aria-labelledby="vm-profile-title"
          onClick={e => e.stopPropagation()}
        >
          <div className="vm-profile-header">
            <h2 id="vm-profile-title">Violation Profile</h2>
            <button className="vm-profile-close" onClick={close} aria-label="Close"><X size={18} /></button>
          </div>

          <div className="vm-profile-body">
            <div className="vm-profile-hero">
              <div className="vm-profile-avatar">{name.charAt(0).toUpperCase()}</div>
              <h3 className="vm-profile-name">{name}</h3>
              {latest.owner_email && <span className="vm-profile-email">{latest.owner_email}</span>}
              <div className="vm-profile-plates">
                {plates.map(p => <span key={p} className="vm-profile-plate">{p}</span>)}
              </div>
            </div>

            <div className="vm-profile-grid">
              <div className="vm-profile-item">
                <span className="vm-profile-label">Confiscation</span>
                <ConfiscationCell c={confiscation} />
              </div>
              <div className="vm-profile-item">
                <span className="vm-profile-label">Standing</span>
                {topOffense > 0
                  ? <span className="vm-profile-value"><OffenseBadge num={topOffense} /> of 3</span>
                  : <span className="vm-profile-value">No offense counted</span>}
              </div>
              <div className="vm-profile-item">
                <span className="vm-profile-label">Open</span>
                <span className="vm-profile-value">{open.length} of {items.length}</span>
              </div>
              <div className="vm-profile-item">
                <span className="vm-profile-label">On record since</span>
                <span className="vm-profile-value">{fmtDate(oldest.issued_at)}</span>
              </div>
            </div>

            <h4 className="vm-profile-section">
              Violations <span>{items.length}</span>
            </h4>
            <ol className="vm-vlist">
              {items.map(v => (
                <li key={v.id} className={`vm-vcard ${isSettled(v) ? 'is-settled' : ''}`}>
                  <div className="vm-vcard-head">
                    <div className="vm-vcard-tags">
                      <span className={`vm-type-pill vm-type-${violationTypeKey(v.violation_type)}`}>
                        {violationLabel(v)}
                      </span>
                      <OffenseBadge num={v.offense_number} />
                      {plates.length > 1 && <span className="vm-vcard-plate">{v.plate_number}</span>}
                    </div>
                    <StatusBadge v={v} />
                  </div>
                  <div className="vm-vcard-meta">
                    <span title={timeAgo(v.issued_at)}>{fmtDateTime(v.issued_at)}</span>
                    {(v.on_duty_guard_name || v.issued_by_name) && (
                      <span>
                        {v.on_duty_guard_name
                          ? `On duty: ${v.on_duty_guard_name}`
                          : `By: ${v.issued_by_name}`}
                      </span>
                    )}
                  </div>
                  {v.notes && <p className="vm-vcard-notes">{v.notes}</p>}
                  {v.status === 'lifted' && v.lifted_reason && (
                    <p className="vm-vcard-extra"><strong>Lift reason:</strong> {v.lifted_reason}</p>
                  )}
                  {v.official_receipt && (
                    <p className="vm-vcard-extra"><strong>OR number:</strong> {v.official_receipt}</p>
                  )}
                  <ActionButtons v={v} />
                </li>
              ))}
            </ol>
          </div>

          <div className="vm-profile-footer">
            <button className="vm-profile-btn" onClick={close}>Close</button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <>
      <div className="vm-page">

        <div className="vm-header">
          <div>
            <h1 className="vm-title">Violations</h1>
            <p className="vm-subtitle">
              3-offense escalation: the account is confiscated for 1 week, then
              2 weeks, then the rest of the registration period. A confiscated
              owner cannot enter or park, and being detected counts as a further
              offense.
            </p>
          </div>
        </div>

        <ReportExportBar
          label="Violations Report"
          fileBase="violations-report"
          filters={reportFilters}
          activeFilterSummary={reportFilterSummary}
          fetchBlob={exportViolationsReport}
          onRangeChange={setExportRange}
          recordCount={filtered.length}
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
              {VIOLATION_TYPES.map(({ value, label }) => (
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

        <div className={`vm-card ${overflowing ? 'is-overflowing' : ''}`} ref={cardRef}>
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
                <col className="vm-col-notes" />
                <col className="vm-col-issued" />
                <col className="vm-col-conf" />
                <col className="vm-col-status" />
                <col className="vm-col-actions" />
              </colgroup>
              <thead>
                <tr>
                  <th>Plate</th>
                  <th>Owner</th>
                  <th>Type / Offense</th>
                  <th>Notes</th>
                  <th>Issued</th>
                  <th>Confiscation</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {paginated.map(renderPersonRow)}
              </tbody>
            </table>
          )}
        </div>

        {!loading && totalPages > 1 && (
          <div className="vm-pagination">
            <span className="vm-page-info">
              Showing {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, groups.length)} of {groups.length} owners / plates
              ({filtered.length} violations)
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

      {/* Rendered before the dialogs below, which open on top of it. */}
      {profileItems.length > 0 && renderProfile(profileItems)}

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
