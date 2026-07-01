import { useState, useEffect, useRef } from 'react'
import {
  Truck, Plus, Trash2, ChevronDown, ChevronUp,
  Loader2, ToggleLeft, ToggleRight, X, AlertTriangle, Tag,
} from 'lucide-react'
import { toast } from 'sonner'
import AdminLayout from '../../components/Layout/AdminLayout'
import {
  getSuppliers, createSupplier, patchSupplier, deleteSupplier,
  addSupplierPlate, deleteSupplierPlate,
} from '../../api/vehicles'
import './SupplierManagement.css'

// ── Add Supplier modal ────────────────────────────────────────────────
function AddSupplierModal({ onClose, onCreated }) {
  const [name, setName]     = useState('')
  const [saving, setSaving] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!name.trim()) return
    setSaving(true)
    try {
      const { data } = await createSupplier({ company_name: name.trim() })
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
  const [toggling, setToggling]     = useState(false)
  const [deleting, setDeleting]     = useState(false)
  const [confirmDel, setConfirmDel] = useState(false)
  const [saving, setSaving]         = useState(false)
  const plateRef = useRef(null)

  const plates = supplier.plates ?? []

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
    const p = plateInput.trim().toUpperCase()
    if (!p) return
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
              className="sp-text-input sp-plate-field"
              placeholder="Enter plate number (e.g. ABC 123)"
              value={plateInput}
              onChange={e => setPlateInput(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addPlate() } }}
              disabled={saving}
            />
            <button className="sp-add-plate-btn" onClick={addPlate} disabled={saving}>
              {saving ? <Loader2 size={13} className="sp-spinner" /> : <Plus size={14} />}
              Add
            </button>
          </div>

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

// ── Main page ─────────────────────────────────────────────────────────
export default function SupplierManagement() {
  const [suppliers, setSuppliers]     = useState([])
  const [pageLoading, setPageLoading] = useState(true)
  const [showAdd, setShowAdd]         = useState(false)

  useEffect(() => {
    getSuppliers()
      .then(({ data }) => setSuppliers(data))
      .catch(() => toast.error('Failed to load suppliers.'))
      .finally(() => setPageLoading(false))
  }, [])

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

      {showAdd && (
        <AddSupplierModal
          onClose={() => setShowAdd(false)}
          onCreated={handleCreated}
        />
      )}
    </AdminLayout>
  )
}
