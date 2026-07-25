import { useState, useEffect, useRef } from 'react'
import { useLiveUpdates } from '../../realtime/useLiveUpdates'
import {
  Truck, Plus, Trash2, ChevronDown, ChevronUp,
  Loader2, ToggleLeft, ToggleRight, X, AlertTriangle, Tag, CalendarClock, Check,
} from 'lucide-react'
import { toast } from 'sonner'
import AdminLayout from '../../components/Layout/AdminLayout'
import {
  getSuppliers, createSupplier, patchSupplier, deleteSupplier,
  addSupplierPlate, deleteSupplierPlate,
  getScheduledVisits, createScheduledVisit, patchScheduledVisit, deleteScheduledVisit,
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

  const addPlate = () => {
    const p = formatPlateNumber(plateInput.trim())
    if (!p) return
    if (!isValidPlateNumber(p)) { setPlateError('Invalid Philippine plate number format.'); return }
    if (plates.includes(p)) { toast.error('Plate already added.'); return }
    setPlates(prev => [...prev, p])
    setPlateInput('')
    plateRef.current?.focus()
  }

  const removePlate = (p) => setPlates(prev => prev.filter(x => x !== p))

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!name.trim()) return
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

        <form onSubmit={handleSubmit} className="sp-modal-form">
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
                placeholder="e.g. ABC 123"
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
            {plateError
              ? <span className="sp-field-error-msg">{plateError}</span>
              : <span className="sp-field-hint">e.g. ABC 1234 · AB 1234 · N123BC · ABC123</span>
            }
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

// ── Single supplier card ──────────────────────────────────────────────
function SupplierCard({ supplier, onUpdated, onDeleted }) {
  const [expanded, setExpanded]     = useState(false)
  const [plateInput, setPlateInput] = useState('')
  const [plateError, setPlateError] = useState('')
  const [toggling, setToggling]     = useState(false)
  const [deleting, setDeleting]     = useState(false)
  const [confirmDel, setConfirmDel] = useState(false)
  const [saving, setSaving]         = useState(false)
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
      setPlateError('Invalid Philippine plate number format.')
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
              style={{ marginLeft: 8, fontSize: 11, border: '1px solid #E2E6EE', borderRadius: 6, padding: '1px 4px' }}
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
              placeholder="Enter plate number (e.g. ABC 123)"
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
          {plateError
            ? <span className="sp-field-error-msg">{plateError}</span>
            : <span className="sp-field-hint">e.g. ABC 1234 · AB 1234 · N123BC · ABC123</span>
          }

          {plates.length === 0 ? (
            <p className="sp-no-plates">No plates registered yet. Add one above.</p>
          ) : (
            <div className="sp-plate-tags">
              {plates.map(pl => (
                <span key={pl.id} className="sp-plate-tag">
                  {pl.plate_number}
                  <button onClick={() => removePlate(pl)} disabled={saving}>
                    <X size={11} />
                  </button>
                </span>
              ))}
            </div>
          )}
        </div>
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
const EMPTY_VISIT = { visitor_name: '', category: 'guest', supplier: '', plate_number: '', purpose: '', expected_date: '', notes: '' }

function ScheduledVisitsSection({ suppliers }) {
  const [visits, setVisits]   = useState([])
  const [loading, setLoading] = useState(true)
  const [form, setForm]       = useState(EMPTY_VISIT)
  const [saving, setSaving]   = useState(false)

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

  const toggleArrived = async (visit) => {
    try {
      const { data } = await patchScheduledVisit(visit.id, { is_arrived: !visit.is_arrived })
      setVisits(prev => prev.map(v => v.id === data.id ? data : v))
    } catch {
      toast.error('Failed to update visit.')
    }
  }

  const remove = async (visit) => {
    try {
      await deleteScheduledVisit(visit.id)
      setVisits(prev => prev.filter(v => v.id !== visit.id))
      toast.success('Scheduled visit removed.')
    } catch {
      toast.error('Failed to remove visit.')
    }
  }

  const today = new Date().toISOString().slice(0, 10)
  const upcoming = visits.filter(v => !v.is_arrived && v.expected_date >= today)
  const past     = visits.filter(v => v.is_arrived || v.expected_date < today)

  return (
    <div className="sp-page" style={{ marginTop: 32 }}>
      <div className="sp-header">
        <div>
          <h2 className="sp-title" style={{ fontSize: 18 }}>Scheduled Visits</h2>
          <p className="sp-subtitle">
            Coordinate visitors and suppliers ahead of time so gate staff know who to expect.
          </p>
        </div>
      </div>

      <form onSubmit={handleAdd} className="sp-modal-form" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10, marginBottom: 20 }}>
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
          <input className="sp-text-input" type="date" value={form.expected_date} onChange={e => setForm(f => ({ ...f, expected_date: e.target.value }))} required />
        </div>
        <div className="sp-field">
          <label className="sp-label">Plate <span className="sp-label-optional">(optional)</span></label>
          <input className="sp-text-input" value={form.plate_number} onChange={e => setForm(f => ({ ...f, plate_number: e.target.value }))} placeholder="e.g. ABC 1234" />
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
      ) : visits.length === 0 ? (
        <div className="sp-empty-state">
          <CalendarClock size={36} className="sp-empty-icon" />
          <p>No visits scheduled yet.</p>
        </div>
      ) : (
        <div className="sp-list">
          {[...upcoming, ...past].map(v => (
            <div key={v.id} className="sp-card" style={{ opacity: v.is_arrived ? 0.6 : 1 }}>
              <div className="sp-card-head">
                <div className="sp-card-meta">
                  <div className="sp-card-name-row">
                    <CalendarClock size={16} className="sp-card-icon" />
                    <span className="sp-card-name">{v.visitor_name}</span>
                    {v.is_arrived && <span className="sp-inactive-badge">Arrived</span>}
                  </div>
                  <div className="sp-card-sub">
                    {categoryLabel(VISIT_CATEGORIES, v.category)} · Expected {v.expected_date}
                    {v.supplier_name && <> · {v.supplier_name}</>}
                    {v.plate_number && <> · {v.plate_number}</>}
                    {v.purpose && <> · {v.purpose}</>}
                  </div>
                </div>
                <div className="sp-card-actions">
                  <button
                    className={`sp-status-btn${v.is_arrived ? ' sp-status-btn--on' : ''}`}
                    onClick={() => toggleArrived(v)}
                    title={v.is_arrived ? 'Mark as not arrived' : 'Mark as arrived'}
                  >
                    <Check size={13} /> {v.is_arrived ? 'Arrived' : 'Mark Arrived'}
                  </button>
                  <button className="sp-delete-btn" onClick={() => remove(v)} title="Remove">
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────
export default function SupplierManagement() {
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
    <AdminLayout>
      <div className="sp-page">

        {/* ── Header ──────────────────────────────── */}
        <div className="sp-header">
          <div>
            <h1 className="sp-title">Supplier Management</h1>
            <p className="sp-subtitle">
              Register supplier companies and their license plates. Supplier vehicles are automatically
              permitted entry when scanned at the gate.
            </p>
          </div>
          <button className="sp-btn sp-btn-primary" onClick={() => setShowAdd(true)}>
            <Plus size={15} /> Add Supplier
          </button>
        </div>

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

      <ScheduledVisitsSection suppliers={suppliers} />

      {showAdd && (
        <AddSupplierModal
          onClose={() => setShowAdd(false)}
          onCreated={handleCreated}
        />
      )}
    </AdminLayout>
  )
}
