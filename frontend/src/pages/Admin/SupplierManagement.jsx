import { useState, useEffect, useRef } from 'react'
import { useLiveUpdates } from '../../realtime/useLiveUpdates'
import {
  Truck, Plus, Trash2, ChevronDown, ChevronUp,
  Loader2, ToggleLeft, ToggleRight, X, AlertTriangle, Tag, CalendarClock, Check,
  Printer, Monitor, CalendarDays, Archive, ArchiveRestore,
} from 'lucide-react'
import { QRCodeSVG } from 'qrcode.react'
import notify, { toast } from '../../components/Feedback/notify'
import PageTabs from '../../components/Tabs/PageTabs'
import { lookupSlip, printSlipOnServer } from '../../api/scanning'
import { printSlipInBrowser } from '../../utils/slipPrint'
import { fieldProblems } from '../../components/Feedback/formProblems'
import {
  getSuppliers, createSupplier, patchSupplier, deleteSupplier,
  addSupplierPlate, deleteSupplierPlate,
  getScheduledVisits, createScheduledVisit, patchScheduledVisit,
} from '../../api/vehicles'
import { formatPlateNumber, isValidPlateNumber } from '../../utils/plateFormat'
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

// ── Print Supplier Pass modal ─────────────────────────────────────────
// A standing pass for one plate. Its QR carries the plate (VEHICLE:{plate}),
// so the guard scans it at the gate like a registered vehicle's QR: the first
// scan logs the entry, the next the exit. The slip comes from the server
// (scanning/slips.py) so the preview, the thermal print and the browser print
// all say the same thing.
function PrintPassModal({ supplier, plate, onClose }) {
  const [slip, setSlip]   = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy]   = useState('')   // 'thermal' | 'browser' | ''

  useEffect(() => {
    let cancelled = false
    lookupSlip(`SLC-SUPPLIER-PASS:${plate.id}`)
      .then(({ data }) => { if (!cancelled) setSlip(data) })
      .catch(err => { if (!cancelled) setError(err?.response?.data?.error || 'Could not load the pass.') })
    return () => { cancelled = true }
  }, [plate.id])

  const asking = useRef(false)   // a double-click must not confirm once and print twice

  const printThermal = async () => {
    if (asking.current) return
    asking.current = true
    try {
      const go = await notify.confirm({
        title: 'Print Supplier Pass?',
        message: `Print the Supplier Pass for ${plate.plate_number} (${supplier.company_name}) on the thermal printer?`,
        confirmLabel: 'Print',
      })
      if (!go) return
    } finally { asking.current = false }
    setBusy('thermal')
    try {
      await printSlipOnServer(slip.code)
      onClose()
      await notify.success(`Supplier Pass for ${plate.plate_number} printed on the thermal printer.`, { title: 'Pass printed' })
    } catch (err) {
      const noPrinter = err?.response?.status === 503
      await notify.error(
        noPrinter
          ? 'This server has no thermal printer connected. Use “Print from This Computer” instead.'
          : (err?.response?.data?.error || 'The pass did not print — the printer could not be reached.'),
        { title: 'Pass not printed' },
      )
    } finally { setBusy('') }
  }

  const printBrowser = async () => {
    setBusy('browser')
    const opened = printSlipInBrowser(slip)
    setBusy('')
    if (!opened) {
      await notify.error('The print window was blocked by the browser. Allow pop-ups for this site, then try again.',
        { title: 'Pass not printed' })
    }
  }

  return (
    <div className="sp-overlay" onClick={onClose}>
      <div className="sp-modal sp-modal--print" onClick={e => e.stopPropagation()}>
        <div className="sp-modal-head">
          <h2 className="sp-modal-title">Print Supplier Pass</h2>
          <button className="sp-modal-close" onClick={onClose}><X size={16} /></button>
        </div>
        <div className="sp-modal-form">
          {error ? (
            <p className="sp-modal-body" style={{ color: '#C62828' }}>{error}</p>
          ) : !slip ? (
            <div className="sp-loading"><Loader2 size={22} className="sp-spinner" /><span>Loading pass…</span></div>
          ) : (
            <>
              {!supplier.is_active && (
                <p className="sp-pass-warn">
                  <AlertTriangle size={14} /> {supplier.company_name} is inactive. The pass will print, but the
                  gate will not admit this plate until the supplier is activated.
                </p>
              )}
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
              <p className="sp-field-hint" style={{ margin: 0 }}>
                The guard scans this QR at the gate — first scan records the entry, the next records the exit.
              </p>
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

function ScheduledVisitsSection({ suppliers }) {
  const [visits, setVisits]   = useState([])
  const [loading, setLoading] = useState(true)
  const [form, setForm]       = useState(EMPTY_VISIT)
  const [saving, setSaving]   = useState(false)
  const [view, setView]       = useState('active')   // 'active' | 'archived'
  const [action, setAction]   = useState(null)       // { visit, mode: 'reschedule' | 'archive' }

  const load = () => {
    getScheduledVisits()
      .then(({ data }) => setVisits(data))
      .catch(() => toast.error('Failed to load scheduled visits.'))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])
  useLiveUpdates(load, ['scheduledvisit'])

  const handleAdd = async (e) => {
    e.preventDefault()
    if (!form.visitor_name.trim() || !form.expected_date) return
    setSaving(true)
    try {
      const { data } = await createScheduledVisit({
        ...form,
        plate_number: formatPlateNumber(form.plate_number.trim()),
        supplier: form.supplier || null,
      })
      setVisits(prev => [...prev, data].sort((a, b) => a.expected_date.localeCompare(b.expected_date)))
      setForm(EMPTY_VISIT)
      toast.success('Visit scheduled.')
    } catch (err) {
      const msg = err.response?.data
        ? Object.values(err.response.data).flat().join(' ')
        : 'Failed to schedule visit.'
      toast.error(msg)
    } finally {
      setSaving(false)
    }
  }

  const replaceVisit = (data) => setVisits(prev => prev.map(v => v.id === data.id ? data : v))

  const toggleArrived = async (visit) => {
    try {
      const { data } = await patchScheduledVisit(visit.id, { is_arrived: !visit.is_arrived })
      replaceVisit(data)
    } catch {
      toast.error('Failed to update visit.')
    }
  }

  // Archiving replaced deleting: a cancelled or abandoned booking stays on
  // record under Archived, and comes back with Restore.
  const restore = async (visit) => {
    try {
      replaceVisit((await patchScheduledVisit(visit.id, { archived: false })).data)
      toast.success(`${visit.visitor_name} restored.`)
    } catch {
      toast.error('Failed to restore the visit.')
    }
  }

  const today = localToday()
  const archived = visits.filter(v => v.archived_at)
    .sort((a, b) => b.archived_at.localeCompare(a.archived_at))
  const active   = visits.filter(v => !v.archived_at)
  // Still to come first (today, then later), then what already happened.
  const upcoming = active.filter(v => ['today', 'upcoming'].includes(visitState(v, today)))
  const past     = active.filter(v => ['arrived', 'no_show'].includes(visitState(v, today)))
    .sort((a, b) => b.expected_date.localeCompare(a.expected_date))
  const shown    = view === 'archived' ? archived : [...upcoming, ...past]

  return (
    <>
      <form onSubmit={handleAdd} className="sp-modal-form" noValidate style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10, marginBottom: 20 }}>
        <div className="sp-field">
          <label className="sp-label">Name</label>
          <input className="sp-text-input" value={form.visitor_name} onChange={e => setForm(f => ({ ...f, visitor_name: e.target.value }))} placeholder="Visitor / company name" required />
        </div>
        <div className="sp-field">
          <label className="sp-label">Category</label>
          <select className="sp-text-input" value={form.category} onChange={e => setForm(f => ({ ...f, category: e.target.value }))}>
            {VISIT_CATEGORIES.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
          </select>
        </div>
        <div className="sp-field">
          <label className="sp-label">Linked Supplier <span className="sp-label-optional">(optional)</span></label>
          <select className="sp-text-input" value={form.supplier} onChange={e => setForm(f => ({ ...f, supplier: e.target.value }))}>
            <option value="">— None —</option>
            {suppliers.map(s => <option key={s.id} value={s.id}>{s.company_name}</option>)}
          </select>
        </div>
        <div className="sp-field">
          <label className="sp-label">Expected Date</label>
          <input className="sp-text-input" type="date" min={today} value={form.expected_date} onChange={e => setForm(f => ({ ...f, expected_date: e.target.value }))} required />
        </div>
        <div className="sp-field">
          <label className="sp-label">Plate <span className="sp-label-optional">(optional)</span></label>
          <input className="sp-text-input" value={form.plate_number} onChange={e => setForm(f => ({ ...f, plate_number: e.target.value }))} placeholder="e.g. AAA 0000" />
        </div>
        <div className="sp-field" style={{ gridColumn: 'span 2' }}>
          <label className="sp-label">Purpose <span className="sp-label-optional">(optional)</span></label>
          <input className="sp-text-input" value={form.purpose} onChange={e => setForm(f => ({ ...f, purpose: e.target.value }))} placeholder="e.g. Quarterly AC maintenance" />
        </div>
        <div className="sp-field" style={{ alignSelf: 'end' }}>
          <button type="submit" className="sp-btn sp-btn-primary" disabled={saving}>
            {saving ? <Loader2 size={14} className="sp-spinner" /> : <Plus size={14} />}
            Schedule
          </button>
        </div>
      </form>

      {loading ? (
        <div className="sp-loading"><Loader2 size={24} className="sp-spinner" /><span>Loading scheduled visits…</span></div>
      ) : (
        <>
        <div className="sp-visit-views" role="tablist" aria-label="Scheduled visit views">
          <button type="button" role="tab" aria-selected={view === 'active'}
            className={`sp-visit-view${view === 'active' ? ' sp-visit-view--on' : ''}`} onClick={() => setView('active')}>
            Active <span className="sp-visit-view-count">{active.length}</span>
          </button>
          <button type="button" role="tab" aria-selected={view === 'archived'}
            className={`sp-visit-view${view === 'archived' ? ' sp-visit-view--on' : ''}`} onClick={() => setView('archived')}>
            <Archive size={13} /> Archived <span className="sp-visit-view-count">{archived.length}</span>
          </button>
        </div>
        {shown.length === 0 ? (
        <div className="sp-empty-state">
          <CalendarClock size={36} className="sp-empty-icon" />
          <p>{view === 'archived' ? 'No archived visits.' : 'No visits scheduled yet.'}</p>
        </div>
        ) : (
        <div className="sp-list">
          {shown.map(v => {
            const state = visitState(v, today)
            return (
            <div key={v.id} className={`sp-card sp-visit sp-visit--${state}`}>
              <div className="sp-card-head">
                <div className="sp-card-meta">
                  <div className="sp-card-name-row">
                    <CalendarClock size={16} className="sp-card-icon" />
                    <span className="sp-card-name">{v.visitor_name}</span>
                    {state === 'today' && <span className="sp-visit-badge sp-visit-badge--today">Expected today</span>}
                    {state === 'arrived' && (
                      <span className="sp-visit-badge sp-visit-badge--arrived">
                        Arrived{v.arrived_at && ` ${fmtArrival(v.arrived_at)}`}{v.pass_reference && ` · ${v.pass_reference}`}
                      </span>
                    )}
                    {state === 'no_show' && <span className="sp-visit-badge sp-visit-badge--noshow">No-show</span>}
                    {state === 'archived' && (
                      <span className="sp-visit-badge sp-visit-badge--archived">
                        {v.is_arrived ? 'Arrived · archived' : 'Archived'}
                      </span>
                    )}
                  </div>
                  <div className="sp-card-sub">
                    {categoryLabel(VISIT_CATEGORIES, v.category)} · Expected {v.expected_date}
                    {v.supplier_name && <> · {v.supplier_name}</>}
                    {v.plate_number && <> · {formatPlateNumber(v.plate_number)}</>}
                    {v.purpose && <> · {v.purpose}</>}
                    {v.auto_admit && <> · admitted on plate scan</>}
                    {v.created_by_name && <> · by {v.created_by_name}</>}
                  </div>
                  {state === 'archived' && (
                    <div className="sp-card-sub">
                      Archived {new Date(v.archived_at).toLocaleDateString()}
                      {v.archived_by_name && <> by {v.archived_by_name}</>}
                      {v.archive_reason && <> — {v.archive_reason}</>}
                    </div>
                  )}
                </div>
                <div className="sp-card-actions">
                  {state === 'archived' ? (
                    <button className="sp-status-btn" onClick={() => restore(v)} title="Put it back on the active list">
                      <ArchiveRestore size={13} /> Restore
                    </button>
                  ) : (
                    <>
                      {/* A hand correction — the gate marks arrivals itself. */}
                      <button
                        className={`sp-status-btn${v.is_arrived ? ' sp-status-btn--on' : ''}`}
                        onClick={() => toggleArrived(v)}
                        title={v.is_arrived ? 'Undo — mark as not arrived' : 'Mark arrived by hand (the gate does this when the slip prints)'}
                      >
                        <Check size={13} /> {v.is_arrived ? 'Arrived' : 'Mark Arrived'}
                      </button>
                      {/* Any visit still to happen can move — a no-show, or one
                          whose visitor cannot make the day. */}
                      {!v.is_arrived && (
                        <button className="sp-status-btn" onClick={() => setAction({ visit: v, mode: 'reschedule' })}
                          title="Move this booking to another date">
                          <CalendarDays size={13} /> Reschedule
                        </button>
                      )}
                      <button className="sp-expand-btn" onClick={() => setAction({ visit: v, mode: 'archive' })}
                        title="Archive — keep it on record, off the gate's list" aria-label="Archive">
                        <Archive size={15} />
                      </button>
                    </>
                  )}
                </div>
              </div>
            </div>
            )
          })}
        </div>
        )}
        </>
      )}

      {action && (
        <VisitActionModal visit={action.visit} mode={action.mode}
          onClose={() => setAction(null)} onSaved={replaceVisit} />
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
          {tab === 'suppliers' && (
            <button className="sp-btn sp-btn-primary" onClick={() => setShowAdd(true)}>
              <Plus size={15} /> Add Supplier
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
          <ScheduledVisitsSection suppliers={suppliers} />
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
