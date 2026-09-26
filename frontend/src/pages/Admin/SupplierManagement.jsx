import { useState, useEffect, useRef } from 'react'
import { useLiveUpdates } from '../../realtime/useLiveUpdates'
import {
  Truck, Plus, Trash2, ChevronDown, ChevronUp,
  Loader2, ToggleLeft, ToggleRight, X, AlertTriangle, Tag, CalendarClock, Check,
  Printer, Monitor, CalendarDays, Archive, ArchiveRestore, Search, ChevronLeft, ChevronRight,
  RotateCcw,
} from 'lucide-react'
import { QRCodeSVG } from 'qrcode.react'
import notify, { toast } from '../../components/Feedback/notify'
import PageTabs from '../../components/Tabs/PageTabs'
import ReportExportBar from '../../components/ReportExportBar'
import RowMenu from '../../components/RowMenu/RowMenu'
import { lookupSlip, printSlipOnServer } from '../../api/scanning'
import { printSlipInBrowser } from '../../utils/slipPrint'
import { fieldProblems } from '../../components/Feedback/formProblems'
import {
  getSuppliers, createSupplier, patchSupplier, deleteSupplier,
  addSupplierPlate, deleteSupplierPlate,
  getScheduledVisits, createScheduledVisit, patchScheduledVisit, exportScheduledVisitsReport,
} from '../../api/vehicles'
import { formatPlateNumber, isValidPlateNumber } from '../../utils/plateFormat'
// The Scheduled Visits table is User Management's table (stats, tabs, toolbar,
// pager), so it takes that page's styles rather than a copy of them.
import './UserManagement.css'
import './SupplierManagement.css'

const SUPPLIER_CATEGORIES = [
  { value: 'delivery',    label: 'Delivery' },
  { value: 'maintenance', label: 'Maintenance' },
  { value: 'vendor',      label: 'Vendor' },
  { value: 'contractor',  label: 'Contractor' },
  { value: 'other',       label: 'Other' },
]

const VISIT_CATEGORIES = [
  ...SUPPLIER_CATEGORIES,
  { value: 'guest', label: 'Guest / Visitor' },
]

const categoryLabel = (list, value) => list.find(c => c.value === value)?.label || value

// ── Add Supplier modal ────────────────────────────────────────────────
function AddSupplierModal({ onClose, onCreated }) {
  const [name, setName]             = useState('')
  const [category, setCategory]     = useState('other')
  const [plateInput, setPlateInput] = useState('')
  const [plateError, setPlateError] = useState('')
  const [plates, setPlates]         = useState([])
  const [saving, setSaving]         = useState(false)
  const plateRef = useRef(null)

  const addPlate = async () => {
    const p = formatPlateNumber(plateInput.trim())
    if (!p) {
      await notify.error('Enter a plate number first.', { title: 'Nothing to add' })
      return
    }
    if (!isValidPlateNumber(p)) {
      await notify.error('Invalid Philippine plate number format.', { title: 'Plate not added' })
      return
    }
    if (plates.includes(p)) { toast.error('Plate already added.'); return }
    setPlates(prev => [...prev, p])
    setPlateInput('')
    plateRef.current?.focus()
  }

  const removePlate = (p) => setPlates(prev => prev.filter(x => x !== p))

  const handleSubmit = async (e) => {
    e.preventDefault()
    // The form carries noValidate, so the browser's own bubble is gone and
    // its complaints have to be re-raised here.
    if (await notify.validation(fieldProblems(e.currentTarget))) return
    setSaving(true)
    try {
      const { data } = await createSupplier({ company_name: name.trim(), category, plates })
      onCreated(data)
      toast.success('Supplier added.')
      onClose()
    } catch (err) {
      const msg = err.response?.data
        ? Object.values(err.response.data).flat().join(' ')
        : 'Failed to add supplier.'
      toast.error(msg)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="sp-overlay" onClick={onClose}>
      <div className="sp-modal" onClick={e => e.stopPropagation()}>
        <div className="sp-modal-head">
          <h2 className="sp-modal-title">Add Supplier</h2>
          <button className="sp-modal-close" onClick={onClose}><X size={16} /></button>
        </div>

        <form onSubmit={handleSubmit} className="sp-modal-form" noValidate>
          <div className="sp-field">
            <label className="sp-label">Company Name</label>
            <input
              className="sp-text-input"
              placeholder="e.g. ABC Supplies Co."
              value={name}
              onChange={e => setName(e.target.value)}
              autoFocus
              required
            />
          </div>

          <div className="sp-field">
            <label className="sp-label">Category</label>
            <select className="sp-text-input" value={category} onChange={e => setCategory(e.target.value)}>
              {SUPPLIER_CATEGORIES.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
            </select>
          </div>

          <div className="sp-field">
            <label className="sp-label">License Plates <span className="sp-label-optional">(optional)</span></label>
            <div className="sp-plate-input-row">
              <input
                ref={plateRef}
                className={`sp-text-input sp-plate-field${plateError ? ' sp-input-error' : ''}`}
                placeholder="e.g. AAA 000"
                value={plateInput}
                onChange={e => {
                  const formatted = formatPlateNumber(e.target.value)
                  setPlateInput(formatted)
                  setPlateError(formatted && !isValidPlateNumber(formatted) ? 'Invalid Philippine plate number format.' : '')
                }}
                onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addPlate() } }}
              />
              <button type="button" className="sp-add-plate-btn" onClick={addPlate}>
                <Plus size={15} /> Add
              </button>
            </div>
            <span className="sp-field-hint">e.g. AAA 0000 · AA 0000 · A000AA · AAA000</span>
            {plates.length > 0 && (
              <div className="sp-plate-tags">
                {plates.map(p => (
                  <span key={p} className="sp-plate-tag">
                    {p}
                    <button type="button" onClick={() => removePlate(p)}><X size={11} /></button>
                  </span>
                ))}
              </div>
            )}
          </div>

          <div className="sp-modal-actions">
            <button type="button" className="sp-btn sp-btn-ghost" onClick={onClose}>Cancel</button>
            <button type="submit" className="sp-btn sp-btn-primary" disabled={saving}>
              {saving ? <Loader2 size={14} className="sp-spinner" /> : <Plus size={14} />}
              {saving ? 'Adding…' : 'Add Supplier'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ── Print a thermal slip — Supplier Pass or Expected Visit card ────────
// The slip comes from the server (scanning/slips.py) so the preview, the
// thermal print and the browser print all say the same thing. `noun` names it
// in every message ("Supplier Pass"), `subject` says whose it is.
function PrintSlipModal({ code, noun, subject, warning, hint, onClose }) {
  const [slip, setSlip]   = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy]   = useState('')   // 'thermal' | 'browser' | ''

  useEffect(() => {
    let cancelled = false
    lookupSlip(code)
      .then(({ data }) => { if (!cancelled) setSlip(data) })
      .catch(err => { if (!cancelled) setError(err?.response?.data?.error || `Could not load the ${noun}.`) })
    return () => { cancelled = true }
  }, [code, noun])

  const asking = useRef(false)   // a double-click must not confirm once and print twice

  const printThermal = async () => {
    if (asking.current) return
    asking.current = true
    try {
      const go = await notify.confirm({
        title: `Print ${noun}?`,
        message: `Print the ${noun} for ${subject} on the thermal printer?`,
        confirmLabel: 'Print',
      })
      if (!go) return
    } finally { asking.current = false }
    setBusy('thermal')
    try {
      await printSlipOnServer(slip.code)
      onClose()
      await notify.success(`${noun} for ${subject} printed on the thermal printer.`, { title: `${noun} printed` })
    } catch (err) {
      const noPrinter = err?.response?.status === 503
      await notify.error(
        noPrinter
          ? 'This server has no thermal printer connected. Use “Print from This Computer” instead.'
          : (err?.response?.data?.error || `The ${noun} did not print — the printer could not be reached.`),
        { title: `${noun} not printed` },
      )
    } finally { setBusy('') }
  }

  const printBrowser = async () => {
    setBusy('browser')
    const opened = printSlipInBrowser(slip)
    setBusy('')
    if (!opened) {
      await notify.error('The print window was blocked by the browser. Allow pop-ups for this site, then try again.',
        { title: `${noun} not printed` })
    }
  }

  return (
    <div className="sp-overlay" onClick={onClose}>
      <div className="sp-modal sp-modal--print" onClick={e => e.stopPropagation()}>
        <div className="sp-modal-head">
          <h2 className="sp-modal-title">Print {noun}</h2>
          <button className="sp-modal-close" onClick={onClose}><X size={16} /></button>
        </div>
        <div className="sp-modal-form">
          {error ? (
            <p className="sp-modal-body" style={{ color: '#C62828' }}>{error}</p>
          ) : !slip ? (
            <div className="sp-loading"><Loader2 size={22} className="sp-spinner" /><span>Loading {noun}…</span></div>
          ) : (
            <>
              {warning && <p className="sp-pass-warn"><AlertTriangle size={14} /> {warning}</p>}
              <div className="sp-pass-preview" aria-label="Pass preview">
                <div className="sp-pass-title">{slip.title}</div>
                <div className="sp-pass-plate">{slip.headline}</div>
                {slip.sections.flat().map(([label, value, key]) => (
                  <div key={label} className={`sp-pass-row${key ? ' sp-pass-row--key' : ''}`}>
                    <span>{label}:</span><span>{value}</span>
                  </div>
                ))}
                <div className="sp-pass-qr"><QRCodeSVG value={slip.qr || slip.code} size={116} level="M" /></div>
                {(slip.footer || []).map(line => <div key={line} className="sp-pass-foot">{line}</div>)}
              </div>
              <p className="sp-field-hint" style={{ margin: 0 }}>{hint}</p>
            </>
          )}
          <div className="sp-modal-actions">
            <button type="button" className="sp-btn sp-btn-ghost" onClick={onClose}>Cancel</button>
            <button type="button" className="sp-btn sp-btn-ghost" onClick={printBrowser} disabled={!slip || !!busy}>
              <Monitor size={14} /> Print from This Computer
            </button>
            <button type="button" className="sp-btn sp-btn-primary" onClick={printThermal} disabled={!slip || !!busy}>
              {busy === 'thermal' ? <Loader2 size={14} className="sp-spinner" /> : <Printer size={14} />}
              {busy === 'thermal' ? 'Printing…' : 'Print on Thermal Printer'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

// A standing pass for one plate. Its QR carries the plate (VEHICLE:{plate}),
// so the guard scans it at the gate like a registered vehicle's QR: the first
// scan logs the entry, the next the exit.
function PrintPassModal({ supplier, plate, onClose }) {
  return (
    <PrintSlipModal
      code={`SLC-SUPPLIER-PASS:${plate.id}`}
      noun="Supplier Pass"
      subject={`${plate.plate_number} (${supplier.company_name})`}
      warning={supplier.is_active ? '' : `${supplier.company_name} is inactive. The pass will print, but the `
        + 'gate will not admit this plate until the supplier is activated.'}
      hint="The guard scans this QR at the gate — first scan records the entry, the next records the exit."
      onClose={onClose}
    />
  )
}

// ── Single supplier card ──────────────────────────────────────────────
function SupplierCard({ supplier, onUpdated, onDeleted }) {
  const [expanded, setExpanded]     = useState(false)
  const [plateInput, setPlateInput] = useState('')
  const [plateError, setPlateError] = useState('')
  const [toggling, setToggling]     = useState(false)
  const [deleting, setDeleting]     = useState(false)
  const [confirmDel, setConfirmDel] = useState(false)
  const [saving, setSaving]         = useState(false)
  const [printPlate, setPrintPlate] = useState(null)
  const plateRef = useRef(null)

  const plates = supplier.plates ?? []

  const [changingCategory, setChangingCategory] = useState(false)
  const handleCategoryChange = async (e) => {
    const category = e.target.value
    setChangingCategory(true)
    try {
      const { data } = await patchSupplier(supplier.id, { category })
      onUpdated(data)
    } catch {
      toast.error('Failed to update category.')
    } finally {
      setChangingCategory(false)
    }
  }

  const handleToggleActive = async () => {
    setToggling(true)
    try {
      const { data } = await patchSupplier(supplier.id, { is_active: !supplier.is_active })
      onUpdated(data)
      toast.success(data.is_active ? 'Supplier activated.' : 'Supplier deactivated.')
    } catch {
      toast.error('Failed to update supplier.')
    } finally {
      setToggling(false)
    }
  }

  const addPlate = async () => {
    const p = formatPlateNumber(plateInput.trim())
    if (!p) return
    if (!isValidPlateNumber(p)) {
      await notify.error('Invalid Philippine plate number format.', { title: 'Plate not added' })
      return
    }
    if (plates.some(pl => pl.plate_number === p)) {
      toast.error('Plate already listed for this supplier.')
      return
    }
    setSaving(true)
    try {
      const { data: newPlate } = await addSupplierPlate(supplier.id, { plate_number: p })
      onUpdated({ ...supplier, plates: [...plates, newPlate], plate_count: plates.length + 1 })
      setPlateInput('')
      plateRef.current?.focus()
      toast.success(`Plate ${p} added.`)
    } catch (err) {
      const msg = err.response?.data
        ? Object.values(err.response.data).flat().join(' ')
        : 'Failed to add plate.'
      toast.error(msg)
    } finally {
      setSaving(false)
    }
  }

  const removePlate = async (plateObj) => {
    setSaving(true)
    try {
      await deleteSupplierPlate(supplier.id, plateObj.id)
      onUpdated({
        ...supplier,
        plates: plates.filter(pl => pl.id !== plateObj.id),
        plate_count: plates.length - 1,
      })
      toast.success(`Plate ${plateObj.plate_number} removed.`)
    } catch {
      toast.error('Failed to remove plate.')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async () => {
    setDeleting(true)
    try {
      await deleteSupplier(supplier.id)
      onDeleted(supplier.id)
      toast.success('Supplier deleted.')
    } catch {
      toast.error('Failed to delete supplier.')
    } finally {
      setDeleting(false)
      setConfirmDel(false)
    }
  }

  return (
    <div className={`sp-card${supplier.is_active ? ' sp-card--active' : ''}`}>
      <div className="sp-card-head">
        <div className="sp-card-meta">
          <div className="sp-card-name-row">
            <Truck size={16} className="sp-card-icon" />
            <span className="sp-card-name">{supplier.company_name}</span>
            {!supplier.is_active && <span className="sp-inactive-badge">Inactive</span>}
          </div>
          <div className="sp-card-sub">
            <Tag size={12} />
            {plates.length} plate{plates.length !== 1 ? 's' : ''} registered
            <select
              value={supplier.category || 'other'}
              onChange={handleCategoryChange}
              disabled={changingCategory}
              style={{ marginLeft: 8, fontSize: 11, border: '1px solid #D3E1EC', borderRadius: 6, padding: '1px 4px' }}
              onClick={e => e.stopPropagation()}
            >
              {SUPPLIER_CATEGORIES.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
            </select>
          </div>
        </div>

        <div className="sp-card-actions">
          <button
            className={`sp-status-btn${supplier.is_active ? ' sp-status-btn--on' : ''}`}
            onClick={handleToggleActive}
            disabled={toggling}
            title={supplier.is_active ? 'Deactivate' : 'Activate'}
          >
            {toggling
              ? <Loader2 size={13} className="sp-spinner" />
              : supplier.is_active ? <ToggleRight size={15} /> : <ToggleLeft size={15} />
            }
            {supplier.is_active ? 'Active' : 'Inactive'}
          </button>

          <button
            className="sp-expand-btn"
            onClick={() => setExpanded(p => !p)}
            title="Manage plates"
          >
            {expanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
          </button>

          <button
            className="sp-delete-btn"
            onClick={() => setConfirmDel(true)}
            disabled={deleting}
            title="Delete supplier"
          >
            {deleting ? <Loader2 size={14} className="sp-spinner" /> : <Trash2 size={14} />}
          </button>
        </div>
      </div>

      {expanded && (
        <div className="sp-plates-section">
          <div className="sp-plates-label">Registered Plates</div>

          <div className="sp-plate-input-row">
            <input
              ref={plateRef}
              className={`sp-text-input sp-plate-field${plateError ? ' sp-input-error' : ''}`}
              placeholder="Enter plate number (e.g. AAA 000)"
              value={plateInput}
              onChange={e => {
                const formatted = formatPlateNumber(e.target.value)
                setPlateInput(formatted)
                setPlateError(formatted && !isValidPlateNumber(formatted) ? 'Invalid Philippine plate number format.' : '')
              }}
              onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addPlate() } }}
              disabled={saving}
            />
            <button className="sp-add-plate-btn" onClick={addPlate} disabled={saving}>
              {saving ? <Loader2 size={13} className="sp-spinner" /> : <Plus size={14} />}
              Add
            </button>
          </div>
            <span className="sp-field-hint">e.g. AAA 0000 · AA 0000 · A000AA · AAA000</span>

          {plates.length === 0 ? (
            <p className="sp-no-plates">No plates registered yet. Add one above.</p>
          ) : (
            <div className="sp-plate-tags">
              {plates.map(pl => (
                <span key={pl.id} className="sp-plate-tag">
                  {pl.plate_number}
                  <button className="sp-plate-print" onClick={() => setPrintPlate(pl)}
                    title={`Print Supplier Pass for ${pl.plate_number}`} aria-label={`Print Supplier Pass for ${pl.plate_number}`}>
                    <Printer size={12} /> Print Pass
                  </button>
                  <button onClick={() => removePlate(pl)} disabled={saving}
                    title={`Remove ${pl.plate_number}`} aria-label={`Remove ${pl.plate_number}`}>
                    <X size={11} />
                  </button>
                </span>
              ))}
            </div>
          )}
          {plates.length > 0 && (
            <span className="sp-field-hint">
              Print a Supplier Pass for a plate — the guard scans its QR at the gate to record entry and exit.
            </span>
          )}
        </div>
      )}

      {printPlate && (
        <PrintPassModal supplier={supplier} plate={printPlate} onClose={() => setPrintPlate(null)} />
      )}

      {confirmDel && (
        <div className="sp-overlay" onClick={() => setConfirmDel(false)}>
          <div className="sp-modal sp-modal--sm" onClick={e => e.stopPropagation()}>
            <AlertTriangle size={30} className="sp-warn-icon" />
            <h2 className="sp-modal-title">Delete Supplier?</h2>
            <p className="sp-modal-body">
              <strong>"{supplier.company_name}"</strong> and all its registered plates will be permanently removed.
              These plates will no longer be automatically permitted entry.
            </p>
            <div className="sp-modal-actions">
              <button className="sp-btn sp-btn-ghost" onClick={() => setConfirmDel(false)}>Cancel</button>
              <button className="sp-btn sp-btn-danger" onClick={handleDelete} disabled={deleting}>
                {deleting ? <Loader2 size={14} className="sp-spinner" /> : <Trash2 size={14} />}
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Scheduled Visits section — advance coordination for visitors/suppliers ──
/* How a booking reaches the gate: it appears in the guard's Expected Today
   panel on its date; the guard checks the visitor in, which issues the visitor
   pass against it and prints the slip with the booking on it; the slip
   printing marks it arrived. A supplier plate on the roster is admitted on a
   scan and marked arrived the same way. Mark Arrived here is only a hand
   correction. */

// The campus-local calendar date. toISOString() is UTC, which in Manila is
// still yesterday until 8 AM — the list read today's visits as no-shows.
const localToday = () => {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

// Where a visit stands, for its badge and its place in the list.
function visitState(v, today) {
  if (v.archived_at)          return 'archived'
  if (v.is_arrived)           return 'arrived'
  if (v.expected_date < today) return 'no_show'
  if (v.expected_date === today) return 'today'
  return 'upcoming'
}

const fmtArrival = (ts) => {
  try { return new Date(ts).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }) }
  catch { return '' }
}

// Reschedule (a new date) or archive (an optional reason) — one small dialog,
// since both are "one field, then confirm" and both need something typed.
function VisitActionModal({ visit, mode, onClose, onSaved }) {
  const today = localToday()
  const rescheduling = mode === 'reschedule'
  const [date, setDate]     = useState(() => (visit.expected_date >= today ? visit.expected_date : today))
  const [reason, setReason] = useState('')
  const [saving, setSaving] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    if (rescheduling && (!date || date < today)) {
      await notify.validation(['Pick today or a later date.'], { title: 'Not rescheduled' })
      return
    }
    if (rescheduling && date === visit.expected_date) { onClose(); return }
    setSaving(true)
    try {
      const { data } = await patchScheduledVisit(visit.id, rescheduling
        ? { expected_date: date }
        : { archived: true, archive_reason: reason.trim() })
      toast.success(rescheduling
        ? `${visit.visitor_name} rescheduled to ${data.expected_date}.`
        : `${visit.visitor_name} archived.`)
      onSaved(data)
      onClose()
    } catch (err) {
      const msg = err.response?.data
        ? Object.values(err.response.data).flat().join(' ')
        : (rescheduling ? 'Failed to reschedule the visit.' : 'Failed to archive the visit.')
      toast.error(msg)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="sp-overlay" onClick={onClose}>
      <div className="sp-modal sp-modal--sm sp-visit-modal" onClick={e => e.stopPropagation()}>
        <div className="sp-modal-head">
          <h2 className="sp-modal-title">{rescheduling ? 'Reschedule Visit' : 'Archive Visit'}</h2>
          <button className="sp-modal-close" onClick={onClose}><X size={16} /></button>
        </div>
        <form onSubmit={submit} className="sp-modal-form" noValidate>
          <p className="sp-modal-body" style={{ margin: 0, textAlign: 'left' }}>
            <strong>{visit.visitor_name}</strong>, expected {visit.expected_date}
            {rescheduling
              ? '. The same booking moves to the new date — gate staff see it in Expected Today on that day.'
              : '. It stays on record under Archived, and gate staff stop seeing it. You can restore it later.'}
          </p>
          {rescheduling ? (
            <div className="sp-field">
              <label className="sp-label">New Date</label>
              <input className="sp-text-input" type="date" min={today} value={date} autoFocus required
                onChange={e => setDate(e.target.value)} />
            </div>
          ) : (
            <div className="sp-field">
              <label className="sp-label">Reason <span className="sp-label-optional">(optional)</span></label>
              <input className="sp-text-input" value={reason} maxLength={255} autoFocus
                placeholder="e.g. Visitor cancelled" onChange={e => setReason(e.target.value)} />
            </div>
          )}
          <div className="sp-modal-actions">
            <button type="button" className="sp-btn sp-btn-ghost" onClick={onClose}>Cancel</button>
            <button type="submit" className="sp-btn sp-btn-primary" disabled={saving}>
              {saving ? <Loader2 size={14} className="sp-spinner" />
                : rescheduling ? <CalendarDays size={14} /> : <Archive size={14} />}
              {rescheduling ? 'Reschedule' : 'Archive'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

const EMPTY_VISIT = { visitor_name: '', category: 'guest', supplier: '', plate_number: '', purpose: '', expected_date: '', notes: '' }

// Booking a visit — opened from the page header's Schedule Visit button, the
// way User Management opens Add User, so the table below keeps the page.
function ScheduleVisitModal({ suppliers, onClose, onCreated }) {
  const today = localToday()
  const [form, setForm]     = useState(EMPTY_VISIT)
  const [saving, setSaving] = useState(false)
  const set = (key) => (e) => setForm(f => ({ ...f, [key]: e.target.value }))

  const submit = async (e) => {
    e.preventDefault()
    const problems = []
    if (!form.visitor_name.trim()) problems.push("Enter the visitor's or company's name.")
    if (!form.expected_date) problems.push('Pick the expected date.')
    else if (form.expected_date < today) problems.push('The expected date cannot be in the past.')
    if (form.plate_number.trim() && !isValidPlateNumber(form.plate_number)) {
      problems.push('The plate should look like ABC 1234, or be left blank.')
    }
    if (await notify.validation(problems, { title: 'Visit not scheduled' })) return
    setSaving(true)
    try {
      const { data } = await createScheduledVisit({
        ...form,
        plate_number: formatPlateNumber(form.plate_number.trim()),
        supplier: form.supplier || null,
      })
      toast.success(`${data.visitor_name} scheduled for ${data.expected_date}.`)
      onCreated(data)
      onClose()
    } catch (err) {
      const msg = err.response?.data
        ? Object.values(err.response.data).flat().join(' ')
        : 'Failed to schedule the visit.'
      toast.error(msg)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="sp-overlay" onClick={onClose}>
      <div className="sp-modal" onClick={e => e.stopPropagation()}>
        <div className="sp-modal-head">
          <h2 className="sp-modal-title">Schedule Visit</h2>
          <button className="sp-modal-close" onClick={onClose}><X size={16} /></button>
        </div>
        <form onSubmit={submit} className="sp-modal-form" noValidate>
          <div className="sp-field">
            <label className="sp-label">Name</label>
            <input className="sp-text-input" value={form.visitor_name} onChange={set('visitor_name')}
              placeholder="Visitor / company name" maxLength={200} autoFocus />
          </div>
          <div className="sv-form-row">
            <div className="sp-field">
              <label className="sp-label">Category</label>
              <select className="sp-text-input" value={form.category} onChange={set('category')}>
                {VISIT_CATEGORIES.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
              </select>
            </div>
            <div className="sp-field">
              <label className="sp-label">Expected Date</label>
              <input className="sp-text-input" type="date" min={today} value={form.expected_date} onChange={set('expected_date')} />
            </div>
          </div>
          <div className="sv-form-row">
            <div className="sp-field">
              <label className="sp-label">Plate <span className="sp-label-optional">(optional)</span></label>
              <input className="sp-text-input" value={form.plate_number} placeholder="e.g. ABC 1234" maxLength={20}
                onChange={e => setForm(f => ({ ...f, plate_number: formatPlateNumber(e.target.value) }))} />
            </div>
            <div className="sp-field">
              <label className="sp-label">Linked Supplier <span className="sp-label-optional">(optional)</span></label>
              <select className="sp-text-input" value={form.supplier} onChange={set('supplier')}>
                <option value="">— None —</option>
                {suppliers.map(s => <option key={s.id} value={s.id}>{s.company_name}</option>)}
              </select>
            </div>
          </div>
          <div className="sp-field">
            <label className="sp-label">Purpose <span className="sp-label-optional">(optional)</span></label>
            <input className="sp-text-input" value={form.purpose} onChange={set('purpose')}
              placeholder="e.g. Quarterly AC maintenance" maxLength={255} />
          </div>
          <span className="sp-field-hint">
            Gate staff see this visit in Expected Today on its date. No plate yet? The guard records it at check-in.
          </span>
          <div className="sp-modal-actions">
            <button type="button" className="sp-btn sp-btn-ghost" onClick={onClose}>Cancel</button>
            <button type="submit" className="sp-btn sp-btn-primary" disabled={saving}>
              {saving ? <Loader2 size={14} className="sp-spinner" /> : <Plus size={14} />} Schedule
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// The table's status tabs. The keys are the server's `status` filter, so the
// table, the tab counts and the PDF report all mean the same thing by each.
const VISIT_TABS = [
  { key: '',         label: 'All' },
  { key: 'today',    label: 'Expected Today' },
  { key: 'upcoming', label: 'Upcoming' },
  { key: 'arrived',  label: 'Arrived' },
  { key: 'no_show',  label: 'No-show' },
  { key: 'archived', label: 'Archived' },
]
const VISIT_STATE_LABEL = {
  today: 'Expected today', upcoming: 'Upcoming', arrived: 'Arrived', no_show: 'No-show', archived: 'Archived',
}
const VISITS_PER_PAGE = 10

const fmtDate = (iso) => {
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(y, m - 1, d).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

function ScheduledVisitsSection({ suppliers, showSchedule, onCloseSchedule }) {
  // One page of the table, from the server — like User Management, since
  // archived visits are kept for good and the list only grows.
  const [rows, setRows]         = useState([])
  const [total, setTotal]       = useState(0)    // rows matching the filters, across every page
  const [counts, setCounts]     = useState({})   // per status tab, over the same search/category/dates
  const [totals, setTotals]     = useState({})   // per status over every visit, for the stat tiles
  const [loading, setLoading]   = useState(true)
  const [status, setStatus]     = useState('')
  const [search, setSearch]     = useState('')
  const [category, setCategory] = useState('')
  const [range, setRange]       = useState({ from: '', to: '' })
  const [page, setPage]         = useState(1)
  const [action, setAction]     = useState(null)  // { visit, mode: 'reschedule' | 'archive' }
  const [printing, setPrinting] = useState(null)  // the visit whose Expected Visit card is being printed

  // Everything that narrows the table, as the server takes it. The report bar
  // adds its own date range on top, so it is left out of `filters`.
  const filters = {}
  if (status) filters.status = status
  if (search.trim()) filters.q = search.trim()
  if (category) filters.category = category
  const params = { ...filters }
  if (range.from) params.date_from = range.from
  if (range.to)   params.date_to   = range.to
  const paramsKey = JSON.stringify(params)

  const loadRows = () =>
    getScheduledVisits({ ...params, page, page_size: VISITS_PER_PAGE })
      .then(({ data }) => {
        // Archiving or restoring the last row of the last page empties it —
        // step back a page rather than show an empty table.
        if (!data.results.length && page > 1) { setPage(page - 1); return }
        setRows(data.results)
        setTotal(data.count)
        setCounts(data.counts || {})
        setTotals(data.totals || {})
      })
      .catch(err => {
        // A page past the end (the list shrank under us) is a 404 from DRF.
        if (err?.response?.status === 404 && page > 1) { setPage(1); return }
        toast.error('Failed to load scheduled visits.')
      })
      .finally(() => setLoading(false))
  const reload = () => loadRows()

  // Debounced, so typing a name is one request rather than one per key.
  useEffect(() => {
    const t = setTimeout(loadRows, 250)
    return () => clearTimeout(t)
  }, [paramsKey, page]) // eslint-disable-line react-hooks/exhaustive-deps
  useLiveUpdates(reload, ['scheduledvisit', 'visitorpass'])

  // Any filter change starts back on page 1.
  const narrow = (setter) => (value) => { setter(value); setPage(1) }

  const toggleArrived = async (visit) => {
    try {
      await patchScheduledVisit(visit.id, { is_arrived: !visit.is_arrived })
      reload()
    } catch {
      toast.error('Failed to update the visit.')
    }
  }

  // Archiving replaced deleting: a cancelled or abandoned booking stays on
  // record under Archived, and comes back with Restore.
  const restore = async (visit) => {
    try {
      await patchScheduledVisit(visit.id, { archived: false })
      toast.success(`${visit.visitor_name} restored.`)
      reload()
    } catch {
      toast.error('Failed to restore the visit.')
    }
  }

  const today = localToday()

  // What a row's menu offers, by where the visit stands.
  const menuItems = (v, st) => {
    if (st === 'archived') {
      return [{ key: 'restore', label: 'Restore', Icon: ArchiveRestore, tone: 'enable', onSelect: () => restore(v) }]
    }
    const items = []
    // A card for someone still to come. A no-show's date has passed, so it
    // is rescheduled first and printed for the new day.
    if (st === 'today' || st === 'upcoming') {
      items.push({ key: 'print', label: 'Print Expected Visit Card', Icon: Printer, tone: 'view', onSelect: () => setPrinting(v) })
    }
    // A hand correction — the gate marks arrivals itself.
    items.push(v.is_arrived
      ? { key: 'unarrive', label: 'Undo Arrived', Icon: RotateCcw, tone: 'edit', onSelect: () => toggleArrived(v) }
      : { key: 'arrive', label: 'Mark Arrived', Icon: Check, tone: 'enable', onSelect: () => toggleArrived(v) })
    if (!v.is_arrived) {
      items.push({ key: 'reschedule', label: 'Reschedule', Icon: CalendarDays, tone: 'view',
        onSelect: () => setAction({ visit: v, mode: 'reschedule' }) })
    }
    items.push({ key: 'archive', label: 'Archive', Icon: Archive, tone: 'edit',
      onSelect: () => setAction({ visit: v, mode: 'archive' }) })
    return items
  }

  const totalPages = Math.max(1, Math.ceil(total / VISITS_PER_PAGE))
  const current    = Math.min(page, totalPages)
  const shown      = rows

  const tabLabel = VISIT_TABS.find(t => t.key === status)?.label
  const reportSummary = [
    status && tabLabel,
    category && categoryLabel(VISIT_CATEGORIES, category),
    search.trim() && `“${search.trim()}”`,
  ].filter(Boolean).join(' · ')

  return (
    <>
      <div className="um-stats-bar sv-stats">
        <div className="um-stat-card">
          <div className="um-stat-icon sv-stat-today"><CalendarClock size={20} /></div>
          <div className="um-stat-info"><h4>Expected Today</h4><span>{totals.today ?? 0}</span></div>
        </div>
        <div className="um-stat-card">
          <div className="um-stat-icon sv-stat-upcoming"><CalendarDays size={20} /></div>
          <div className="um-stat-info"><h4>Upcoming</h4><span>{totals.upcoming ?? 0}</span></div>
        </div>
        <div className="um-stat-card">
          <div className="um-stat-icon sv-stat-arrived"><Check size={20} /></div>
          <div className="um-stat-info"><h4>Arrived</h4><span>{totals.arrived ?? 0}</span></div>
        </div>
        <div className="um-stat-card">
          <div className="um-stat-icon sv-stat-noshow"><AlertTriangle size={20} /></div>
          <div className="um-stat-info"><h4>No-show</h4><span>{totals.no_show ?? 0}</span></div>
        </div>
      </div>

      <ReportExportBar
        label="Scheduled Visits Report"
        filters={filters}
        activeFilterSummary={reportSummary}
        fetchBlob={exportScheduledVisitsReport}
        onRangeChange={narrow(setRange)}
        recordCount={total}
        allowFuture
      />

      <div className="um-table-container">
        <div className="um-role-tabs">
          {VISIT_TABS.map(t => (
            <button key={t.key || 'all'} className={`um-role-tab ${status === t.key ? 'active' : ''}`}
              onClick={() => narrow(setStatus)(t.key)}>
              {t.label}
              <span className="sv-tab-count">{counts[t.key || 'all'] ?? 0}</span>
            </button>
          ))}
        </div>

        <div className="um-table-toolbar">
          <div className="um-search-wrapper">
            <Search size={16} />
            <input className="um-search-input" type="text" value={search}
              placeholder="Search by name, plate, purpose, supplier or SV number…"
              onChange={e => narrow(setSearch)(e.target.value)} />
          </div>
          <div className="um-filter-group">
            <select className="um-form-select" value={category} onChange={e => narrow(setCategory)(e.target.value)}>
              <option value="">All Categories</option>
              {VISIT_CATEGORIES.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
            </select>
          </div>
        </div>

        {loading ? (
          <div className="um-loading"><div className="um-spinner" /><p>Loading scheduled visits…</p></div>
        ) : rows.length === 0 ? (
          <div className="um-empty">
            <CalendarClock size={48} />
            <h3>{status === 'archived' ? 'No archived visits' : 'No visits found'}</h3>
            <p>
              {search || category || status || range.from || range.to
                ? 'Try a different filter.'
                : 'Click "Schedule Visit" to book the first one.'}
            </p>
          </div>
        ) : (
          <div className="um-table-scroll">
            <table className="um-table sv-table">
              <thead>
                <tr>
                  <th>Ref</th>
                  <th>Visitor</th>
                  <th>Category</th>
                  <th>Expected</th>
                  <th>Plate</th>
                  <th>Purpose</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {shown.map(v => {
                  const st = visitState(v, today)
                  return (
                    <tr key={v.id} className={`sv-row sv-row--${st}`}>
                      <td><span className="sv-ref">SV-{v.id}</span></td>
                      <td>
                        <span className="sv-name">{v.visitor_name}</span>
                        <span className="sv-sub">
                          {v.supplier_name && v.supplier_name !== v.visitor_name ? `${v.supplier_name} · ` : ''}
                          by {v.created_by_name || 'CDSO'}
                        </span>
                      </td>
                      <td>{categoryLabel(VISIT_CATEGORIES, v.category)}</td>
                      <td style={{ whiteSpace: 'nowrap' }}>{fmtDate(v.expected_date)}</td>
                      <td>
                        {v.plate_number
                          ? <span className="sv-plate">{formatPlateNumber(v.plate_number)}</span>
                          : <span className="sv-muted">—</span>}
                        {v.auto_admit && <span className="sv-sub">admitted on scan</span>}
                      </td>
                      <td className="sv-purpose">{v.purpose || <span className="sv-muted">—</span>}</td>
                      <td>
                        <span className={`sp-visit-badge sp-visit-badge--${st === 'no_show' ? 'noshow' : st}`}>
                          {VISIT_STATE_LABEL[st]}
                        </span>
                        {v.is_arrived && v.arrived_at && (
                          <span className="sv-sub">
                            {fmtArrival(v.arrived_at)}{v.pass_reference && ` · ${v.pass_reference}`}
                          </span>
                        )}
                        {st === 'archived' && v.archive_reason && <span className="sv-sub">{v.archive_reason}</span>}
                      </td>
                      <td>
                        <RowMenu label={`Actions for ${v.visitor_name}`} items={menuItems(v, st)} width={220} />
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}

        {!loading && totalPages > 1 && (
          <div className="um-pagination">
            <span className="um-pagination-info">
              Showing {(current - 1) * VISITS_PER_PAGE + 1} to {Math.min(current * VISITS_PER_PAGE, total)} of {total} visits
            </span>
            <div className="um-pagination-controls">
              <button className="um-page-btn" disabled={current === 1} onClick={() => setPage(current - 1)}>
                <ChevronLeft size={16} />
              </button>
              <span className="um-page-current">Page {current} of {totalPages}</span>
              <button className="um-page-btn" disabled={current === totalPages} onClick={() => setPage(current + 1)}>
                <ChevronRight size={16} />
              </button>
            </div>
          </div>
        )}
      </div>

      {showSchedule && (
        <ScheduleVisitModal suppliers={suppliers} onClose={onCloseSchedule} onCreated={reload} />
      )}
      {action && (
        <VisitActionModal visit={action.visit} mode={action.mode}
          onClose={() => setAction(null)} onSaved={reload} />
      )}
      {printing && (
        <PrintSlipModal
          code={`SLC-SCHEDULED:${printing.id}`}
          noun="Expected Visit Card"
          subject={`${printing.visitor_name} (SV-${printing.id})`}
          hint="Give it to the visitor, or keep it at the gate. On the day, the guard scans its QR to open the check-in — the visitor pass is issued from there."
          onClose={() => setPrinting(null)}
        />
      )}
    </>
  )
}

// ── Main page ─────────────────────────────────────────────────────────
/* Tabbed the same way System Settings and Parking Space Management are:
   managing the supplier roster and booking a visit are separate jobs, and the
   visits used to sit below the whole supplier list. */
const PAGE_TABS = [
  { id: 'suppliers', label: 'Suppliers',        icon: Truck },
  { id: 'visits',    label: 'Scheduled Visits', icon: CalendarClock },
]

const PAGE_BLURB = {
  suppliers: 'Register supplier companies and their license plates. Supplier vehicles are '
           + 'automatically permitted entry when scanned at the gate.',
  visits:    'Coordinate visitors and suppliers ahead of time so gate staff know who to expect.',
}

export default function SupplierManagement() {
  const [tab, setTab]                 = useState('suppliers')
  const [suppliers, setSuppliers]     = useState([])
  const [pageLoading, setPageLoading] = useState(true)
  const [showAdd, setShowAdd]         = useState(false)
  const [showSchedule, setShowSchedule] = useState(false)

  const loadSuppliers = () => {
    getSuppliers()
      .then(({ data }) => setSuppliers(data))
      .catch(() => toast.error('Failed to load suppliers.'))
      .finally(() => setPageLoading(false))
  }

  useEffect(() => { loadSuppliers() }, [])

  // Live-refresh supplier list on supplier/plate changes
  useLiveUpdates(loadSuppliers, ['supplier', 'supplierplate'])

  const handleCreated  = (s)  => setSuppliers(prev => [s, ...prev])
  const handleUpdated  = (s)  => setSuppliers(prev => prev.map(x => x.id === s.id ? s : x))
  const handleDeleted  = (id) => setSuppliers(prev => prev.filter(x => x.id !== id))

  return (
    <>
      <div className="sp-page">

        {/* ── Header ──────────────────────────────── */}
        <div className="sp-header">
          <div>
            <h1 className="sp-title">Visits and Suppliers</h1>
            <p className="sp-subtitle">{PAGE_BLURB[tab]}</p>
          </div>
          {tab === 'suppliers' ? (
            <button className="sp-btn sp-btn-primary" onClick={() => setShowAdd(true)}>
              <Plus size={15} /> Add Supplier
            </button>
          ) : (
            <button className="sp-btn sp-btn-primary" onClick={() => setShowSchedule(true)}>
              <Plus size={15} /> Schedule Visit
            </button>
          )}
        </div>

        <PageTabs
          id="sp"
          ariaLabel="Supplier sections"
          tabs={PAGE_TABS}
          active={tab}
          onChange={setTab}
        />

        {/* Both panels stay mounted and are hidden, so a half-filled visit form
            survives a trip to the Suppliers tab and back. */}
        <div className="sp-panel" hidden={tab !== 'suppliers'}>
          {pageLoading ? (
            <div className="sp-loading">
              <Loader2 size={28} className="sp-spinner" />
              <span>Loading suppliers…</span>
            </div>
          ) : suppliers.length === 0 ? (
            <div className="sp-empty-state">
              <Truck size={40} className="sp-empty-icon" />
              <p>No suppliers registered yet.</p>
              <p className="sp-empty-hint">Add a supplier to allow their vehicles automatic entry.</p>
            </div>
          ) : (
            <div className="sp-list">
              {suppliers.map(s => (
                <SupplierCard
                  key={s.id}
                  supplier={s}
                  onUpdated={handleUpdated}
                  onDeleted={handleDeleted}
                />
              ))}
            </div>
          )}
        </div>

        <div className="sp-panel" hidden={tab !== 'visits'}>
          <ScheduledVisitsSection suppliers={suppliers}
            showSchedule={showSchedule} onCloseSchedule={() => setShowSchedule(false)} />
        </div>
      </div>

      {showAdd && (
        <AddSupplierModal
          onClose={() => setShowAdd(false)}
          onCreated={handleCreated}
        />
      )}
    </>
  )
}
