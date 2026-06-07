import { useState, useEffect } from 'react'
import {
  CalendarDays, Clock, Plus, Pencil, Trash2, X,
  ShieldCheck, Car, Bike, Truck,
  BrainCircuit, Info, CheckCircle, AlertTriangle,
  Settings2, Loader2,
  Zap,
} from 'lucide-react'
import { toast } from 'sonner'
import AdminLayout from '../../components/Layout/AdminLayout'
import { getRuleConstraints, createRuleConstraint, updateRuleConstraint, deleteRuleConstraint, getVehicleTypeAccess, createVehicleTypeAccess, updateVehicleTypeAccess, deleteVehicleTypeAccess } from '../../api/vehicles'
import './RuleConstraints.css'

// ─── Constants ────────────────────────────────────────────────────────────────

const DAY_LABELS = [
  { key: 'mon', label: 'Mon' },
  { key: 'tue', label: 'Tue' },
  { key: 'wed', label: 'Wed' },
  { key: 'thu', label: 'Thu' },
  { key: 'fri', label: 'Fri' },
  { key: 'sat', label: 'Sat' },
]

const INITIAL_RULES = [
  {
    id: 1,
    name: 'Standard Employee Shift',
    type: 'employee',
    days: ['mon', 'tue', 'wed', 'thu', 'fri', 'sat'],
    startTime: '06:00',
    endTime: '20:00',
    enabled: true,
  },
  {
    id: 2,
    name: 'Student MWF Schedule',
    type: 'student',
    days: ['mon', 'wed', 'fri'],
    startTime: '06:00',
    endTime: '19:00',
    enabled: true,
  },
  {
    id: 3,
    name: 'Student TTHS Schedule',
    type: 'student',
    days: ['tue', 'thu', 'sat'],
    startTime: '06:00',
    endTime: '19:00',
    enabled: true,
  },
  {
    id: 4,
    name: 'Visitor Access',
    type: 'visitor',
    days: ['mon', 'tue', 'wed', 'thu', 'fri', 'sat'],
    startTime: '06:00',
    endTime: '20:00',
    enabled: false,
  },
]

const INITIAL_VEHICLE_TYPES = [
  {
    category_key: 'four-wheel',
    label: '4-Wheel Vehicles',
    sub: 'Cars, SUVs, Vans',
    icon: 'Car',
    gate: 'Main Gate 1',
    is_all_hours: true,
    hours_start: '06:00',
    hours_end: '20:00',
    status: 'allowed',
    enabled: true,
  },
  {
    category_key: 'three-wheel',
    label: '3-Wheel Vehicles',
    sub: 'Tricycles',
    icon: 'Bike',
    gate: 'Main Gate 1',
    is_all_hours: true,
    hours_start: '06:00',
    hours_end: '20:00',
    status: 'allowed',
    enabled: true,
  },
  {
    category_key: 'two-wheel',
    label: '2-Wheel Vehicles',
    sub: 'Motorcycles, Scooters',
    icon: 'Bike',
    gate: 'Main Gate 2',
    is_all_hours: true,
    hours_start: '06:00',
    hours_end: '20:00',
    status: 'allowed',
    enabled: true,
  },
  {
    category_key: 'ebike',
    label: 'E-Bike',
    sub: 'Electric Bicycles',
    icon: 'Zap',
    gate: 'Main Gate 1',
    is_all_hours: true,
    hours_start: '06:00',
    hours_end: '20:00',
    status: 'allowed',
    enabled: true,
  },
  {
    category_key: 'escooter',
    label: 'E-Scooter',
    sub: 'Electric Scooters',
    icon: 'Zap',
    gate: 'Main Gate 1',
    is_all_hours: true,
    hours_start: '06:00',
    hours_end: '20:00',
    status: 'allowed',
    enabled: true,
  },
  {
    category_key: 'heavy',
    label: 'Heavy Vehicles',
    sub: 'Delivery Trucks, Service Vehicles, Trucks',
    icon: 'Truck',
    gate: 'Main Gate 2',
    is_all_hours: false,
    hours_start: '06:00',
    hours_end: '08:00',
    status: 'restricted',
    enabled: true,
  },
]

const ICON_COMPONENTS = { Car, Bike, Truck, Zap }

const VEHICLE_CATEGORIES = {
  'four-wheel': { label: '4-Wheel Vehicles', icon: 'Car' },
  'three-wheel': { label: '3-Wheel Vehicles', icon: 'Bike' },
  'two-wheel': { label: '2-Wheel Vehicles', icon: 'Bike' },
  'ebike': { label: 'E-Bike', icon: 'Zap' },
  'escooter': { label: 'E-Scooter', icon: 'Zap' },
  'heavy': { label: 'Heavy Vehicles', icon: 'Truck' },
}

function formatTime12(t) {
  const [h, m] = t.split(':').map(Number)
  const ampm = h >= 12 ? 'PM' : 'AM'
  const h12 = h % 12 || 12
  return `${h12}:${String(m).padStart(2, '0')} ${ampm}`
}

function getSliderFill(val) {
  const pct = val
  return `linear-gradient(to right, #DC2626, #F59E0B ${Math.min(pct, 40)}%, #10B981 ${Math.min(pct, 75)}%, #059669 100%) 0 0 / ${pct}% 100% no-repeat, #E2E6EE`
}

function getThresholdLevel(val) {
  if (val < 60) return 'low'
  if (val < 80) return 'medium'
  return 'high'
}

// ─── Schedule Rule Modal ──────────────────────────────────────────────────────

function RuleModal({ rule, onSave, onClose }) {
  const isEdit = Boolean(rule)
  const [name, setName] = useState(rule?.name ?? '')
  const [type, setType] = useState(rule?.type ?? 'student')
  const [days, setDays] = useState(rule?.days ?? [])
  const [startTime, setStartTime] = useState(rule?.startTime ?? '06:00')
  const [endTime, setEndTime] = useState(rule?.endTime ?? '19:00')

  const toggleDay = (key) => {
    setDays((prev) =>
      prev.includes(key) ? prev.filter((d) => d !== key) : [...prev, key]
    )
  }

  const handleSubmit = (e) => {
    e.preventDefault()
    if (!name.trim() || days.length === 0) return
    onSave({ name: name.trim(), type, days, startTime, endTime, enabled: rule?.enabled ?? true })
    onClose()
  }

  return (
    <div className="rc-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="rc-modal">
        <div className="rc-modal-head">
          <span className="rc-modal-title">
            {isEdit ? <Pencil size={15} /> : <Plus size={15} />}
            {isEdit ? 'Edit Schedule Rule' : 'Add Schedule Rule'}
          </span>
          <button className="rc-modal-close" onClick={onClose}><X size={15} /></button>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="rc-modal-body">
            <div className="rc-field">
              <label className="rc-field-label">Rule Name</label>
              <input
                className="rc-field-input"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Weekend Faculty Access"
                required
              />
            </div>

            <div className="rc-field">
              <label className="rc-field-label">Applies To</label>
              <select
                className="rc-field-select"
                value={type}
                onChange={(e) => setType(e.target.value)}
              >
                <option value="student">Student</option>
                <option value="employee">Employee</option>
                <option value="visitor">Visitor</option>
              </select>
            </div>

            <div className="rc-field">
              <label className="rc-field-label">Allowed Days</label>
              <div className="rc-day-selector">
                {DAY_LABELS.map((d) => (
                  <button
                    key={d.key}
                    type="button"
                    className={`rc-day-chip editable ${days.includes(d.key) ? 'active' : ''}`}
                    onClick={() => toggleDay(d.key)}
                  >
                    {d.label}
                  </button>
                ))}
              </div>
              <span className="rc-field-hint">Click to toggle each day on or off</span>
            </div>

            <div className="rc-field-row">
              <div className="rc-field">
                <label className="rc-field-label">From</label>
                <input
                  className="rc-field-input"
                  type="time"
                  value={startTime}
                  onChange={(e) => setStartTime(e.target.value)}
                  required
                />
              </div>
              <div className="rc-field">
                <label className="rc-field-label">To</label>
                <input
                  className="rc-field-input"
                  type="time"
                  value={endTime}
                  onChange={(e) => setEndTime(e.target.value)}
                  required
                />
              </div>
            </div>
          </div>

          <div className="rc-modal-foot">
            <button type="button" className="rc-btn rc-btn-secondary" onClick={onClose}>Cancel</button>
            <button type="submit" className="rc-btn rc-btn-primary">
              {isEdit ? 'Save Changes' : 'Create Rule'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ─── Vehicle Type Modal ──────────────────────────────────────────────────────

function VehicleModal({ vehicle, onSave, onClose }) {
  const isEdit = Boolean(vehicle)
  
  const getInitialCategory = () => {
    if (vehicle?.label === '3-Wheel Vehicles') return 'three-wheel'
    if (vehicle?.label === 'E-Bike') return 'ebike'
    if (vehicle?.label === 'E-Scooter') return 'escooter'
    if (vehicle?.icon === 'Bike') return 'two-wheel'
    if (vehicle?.icon === 'Truck') return 'heavy'
    return 'four-wheel'
  }

  const [categoryKey, setCategoryKey] = useState(getInitialCategory())
  const [sub, setSub] = useState(vehicle?.sub ?? '')
  const [gate, setGate] = useState(vehicle?.gate ?? 'Main Gate 1')
  const [status, setStatus] = useState(vehicle?.status ?? 'allowed')

  // Flexible time state
  const initialIsAllHours = vehicle ? vehicle.is_all_hours : true
  const initialStart = vehicle && !vehicle.is_all_hours ? vehicle.hours_start : '06:00 AM'
  const initialEnd = vehicle && !vehicle.is_all_hours ? vehicle.hours_end : '08:00 PM'

  const [isAllHours, setIsAllHours] = useState(initialIsAllHours)
  
  const parseTime = (timeStr) => {
    if (!timeStr) return '06:00'
    const match = timeStr.match(/(\d+):(\d+)\s*(AM|PM)/i)
    if (!match) return '06:00'
    let h = parseInt(match[1], 10)
    const m = match[2]
    const ampm = match[3].toUpperCase()
    if (ampm === 'PM' && h < 12) h += 12
    if (ampm === 'AM' && h === 12) h = 0
    return `${String(h).padStart(2, '0')}:${m}`
  }

  const [startTime, setStartTime] = useState(parseTime(initialStart))
  const [endTime, setEndTime] = useState(parseTime(initialEnd))

  const handleSubmit = (e) => {
    e.preventDefault()
    const cat = VEHICLE_CATEGORIES[categoryKey]
    onSave({ 
      category_key: categoryKey,
      label: cat.label, 
      sub: sub.trim(), 
      icon: cat.icon, 
      gate, 
      is_all_hours: isAllHours,
      hours_start: formatTime12(startTime), 
      hours_end: formatTime12(endTime),
      status, 
      enabled: vehicle?.enabled ?? true 
    })
    onClose()
  }

  return (
    <div className="rc-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="rc-modal">
        <div className="rc-modal-head">
          <span className="rc-modal-title">
            {isEdit ? <Pencil size={15} /> : <Plus size={15} />}
            {isEdit ? 'Edit Vehicle Privilege' : 'Add Vehicle Privilege'}
          </span>
          <button className="rc-modal-close" onClick={onClose}><X size={15} /></button>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="rc-modal-body">
            <div className="rc-field-row">
              <div className="rc-field" style={{ flex: 2 }}>
                <label className="rc-field-label">Vehicle Type</label>
<select
                       className="rc-field-select"
                       value={categoryKey}
                       onChange={(e) => setCategoryKey(e.target.value)}
                     >
                       <option value="four-wheel">4-Wheel Vehicles (Cars, SUVs)</option>
                       <option value="three-wheel">3-Wheel Vehicles (Tricycles)</option>
                       <option value="two-wheel">2-Wheel Vehicles (Motorcycles, Scooters)</option>
                       <option value="ebike">E-Bike (Electric Bicycles)</option>
                       <option value="escooter">E-Scooter (Electric Scooters)</option>
                       <option value="heavy">Heavy Vehicles (Trucks)</option>
                     </select>
              </div>
              <div className="rc-field" style={{ flex: 1 }}>
                <label className="rc-field-label">Status Level</label>
                <select
                  className="rc-field-select"
                  value={status}
                  onChange={(e) => setStatus(e.target.value)}
                >
                  <option value="allowed">Allowed</option>
                  <option value="restricted">Restricted Hours</option>
                </select>
              </div>
            </div>
            
            <div className="rc-field">
              <label className="rc-field-label">Description / Subtext</label>
              <input
                className="rc-field-input"
                value={sub}
                onChange={(e) => setSub(e.target.value)}
                placeholder="e.g. Cars, SUVs, Vans"
              />
            </div>

            <div className="rc-field-row" style={{ alignItems: 'flex-start' }}>
              <div className="rc-field">
                <label className="rc-field-label">Assigned Gate</label>
                <select
                  className="rc-field-select"
                  value={gate}
                  onChange={(e) => setGate(e.target.value)}
                >
                  <option value="Main Gate 1">Main Gate 1</option>
                  <option value="Main Gate 2">Main Gate 2</option>
                </select>
              </div>
              <div className="rc-field">
                <label className="rc-field-label">Allowed Hours</label>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
<label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' }}>
                     <label className="rc-toggle" style={{ margin: 0 }}>
                       <input
                         type="checkbox"
                         checked={isAllHours}
                         onChange={(e) => setIsAllHours(e.target.checked)}
                       />
                       <span className="rc-toggle-track" />
                     </label>
                     <span style={{ fontSize: '13px', color: '#4B5563', fontWeight: 500 }}>All hours</span>
                   </label>
                  
                  {!isAllHours && (
                    <div className="rc-field-row" style={{ marginTop: '4px' }}>
                      <input
                        className="rc-field-input"
                        type="time"
                        value={startTime}
                        onChange={(e) => setStartTime(e.target.value)}
                        required
                      />
                      <input
                        className="rc-field-input"
                        type="time"
                        value={endTime}
                        onChange={(e) => setEndTime(e.target.value)}
                        required
                      />
                    </div>
                  )}
                </div>
              </div>
            </div>

          </div>

          <div className="rc-modal-foot">
            <button type="button" className="rc-btn rc-btn-secondary" onClick={onClose}>Cancel</button>
            <button type="submit" className="rc-btn rc-btn-primary">
              {isEdit ? 'Save Changes' : 'Create Type'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function RuleConstraints() {
// Section 1 — Schedule Rules
   const [rules, setRules] = useState(INITIAL_RULES)
   const [loadingRules, setLoadingRules] = useState(true)
   const [modalRule, setModalRule] = useState(null)   // null = closed, {} = new, {id,...} = edit
   const [showModal, setShowModal] = useState(false)

   const [vehicleTypes, setVehicleTypes] = useState(INITIAL_VEHICLE_TYPES)
   const [loadingVehicles, setLoadingVehicles] = useState(true)
   const [modalVehicle, setModalVehicle] = useState(null)
   const [showVehicleModal, setShowVehicleModal] = useState(false)
   const [threshold, setThreshold] = useState(85)

  useEffect(() => {
    let cancelled = false
    setLoadingRules(true)
    setLoadingVehicles(true)
    getRuleConstraints()
      .then((res) => {
        if (cancelled) return
        const data = (res.data?.results ?? res.data ?? []).map((r) => ({
          id: r.id,
          name: r.name,
          type: r.constraint_type,
          days: r.days || [],
          startTime: r.start_time || '06:00',
          endTime: r.end_time || '19:00',
          enabled: r.enabled,
        }))
        if (data.length > 0) {
          setRules(data)
        } else {
          setRules(INITIAL_RULES)
          INITIAL_RULES.forEach((r) => {
            createRuleConstraint({
              name: r.name,
              constraint_type: r.type,
              days: r.days,
              start_time: r.startTime,
              end_time: r.endTime,
              enabled: r.enabled,
            }).catch(() => {})
          })
        }
        setLoadingRules(false)
      })
      .catch(() => {
        if (!cancelled) setLoadingRules(false)
      })

    getVehicleTypeAccess()
      .then((res) => {
        if (cancelled) return
        const data = (res.data?.results ?? res.data ?? [])
        if (data.length > 0) {
          setVehicleTypes(data)
        } else {
          setVehicleTypes(INITIAL_VEHICLE_TYPES)
          INITIAL_VEHICLE_TYPES.forEach((v, index) => {
            createVehicleTypeAccess({
              category_key: v.category_key,
              label: v.label,
              sub: v.sub,
              icon: v.icon,
              gate: v.gate,
              is_all_hours: v.is_all_hours,
              hours_start: v.hours_start,
              hours_end: v.hours_end,
              status: v.status,
              enabled: v.enabled,
              ordering: index,
            }).catch(() => {})
          })
        }
        setLoadingVehicles(false)
      })
      .catch(() => {
        if (!cancelled) setLoadingVehicles(false)
      })

    return () => { cancelled = true }
  }, [])

  // ── Rule CRUD ───────────────────────────────────────────────

  const openAdd = () => { setModalRule(null); setShowModal(true) }

  const openEdit = (rule) => { setModalRule(rule); setShowModal(true) }

  const closeModal = () => setShowModal(false)

  const handleSave = async (data) => {
    try {
      if (modalRule?.id) {
        const { data: saved } = await updateRuleConstraint(modalRule.id, {
          name: data.name,
          constraint_type: data.type,
          days: data.days,
          start_time: data.startTime,
          end_time: data.endTime,
          enabled: data.enabled,
        })
        setRules((prev) =>
          prev.map((r) => (r.id === modalRule.id ? { ...saved } : r))
        )
      } else {
        const { data: saved } = await createRuleConstraint({
          name: data.name,
          constraint_type: data.type,
          days: data.days,
          start_time: data.startTime,
          end_time: data.endTime,
          enabled: data.enabled ?? true,
        })
        setRules((prev) => [...prev, saved])
      }
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Failed to save rule constraint.')
    }
  }

  const handleDeleteRule = async (id) => {
    try {
      await deleteRuleConstraint(id)
      setRules((prev) => prev.filter((r) => r.id !== id))
      toast.success('Rule deleted successfully.')
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Failed to delete rule.')
    }
  }

  const handleToggleRule = async (id) => {
    const rule = rules.find((r) => r.id === id)
    if (!rule) return
    try {
      const { data: saved } = await updateRuleConstraint(id, { enabled: !rule.enabled })
      setRules((prev) =>
        prev.map((r) => (r.id === id ? { ...r, enabled: saved.enabled } : r))
      )
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Failed to update rule.')
    }
  }

  const openAddVehicle = () => { setModalVehicle(null); setShowVehicleModal(true) }
  const openEditVehicle = (vehicle) => { setModalVehicle(vehicle); setShowVehicleModal(true) }
  const closeVehicleModal = () => setShowVehicleModal(false)

  const handleSaveVehicle = async (data) => {
    try {
      if (modalVehicle?.id) {
        const { data: saved } = await updateVehicleTypeAccess(modalVehicle.id, data)
        setVehicleTypes((prev) =>
          prev.map((v) => (v.id === modalVehicle.id ? { ...v, ...saved } : v))
        )
      } else {
        const { data: saved } = await createVehicleTypeAccess(data)
        setVehicleTypes((prev) => [
          ...prev,
          saved,
        ])
      }
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Failed to save vehicle type.')
    }
  }

  const handleDeleteVehicle = async (id) => {
    try {
      await deleteVehicleTypeAccess(id)
      setVehicleTypes((prev) => prev.filter((v) => v.id !== id))
      toast.success('Vehicle type deleted successfully.')
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Failed to delete vehicle type.')
    }
  }

  const handleToggleVehicle = async (id) => {
    const v = vehicleTypes.find((vt) => vt.id === id)
    if (!v) return
    try {
      const { data: saved } = await updateVehicleTypeAccess(id, { enabled: !v.enabled })
      setVehicleTypes((prev) =>
        prev.map((vt) => (vt.id === id ? { ...vt, enabled: saved.enabled } : vt))
      )
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Failed to update vehicle type.')
    }
  }


  // ── Slider helpers ──────────────────────────────────────────

  const level = getThresholdLevel(threshold)

  const infoMap = {
    low: {
      title: 'Low Confidence — Relaxed Matching',
      desc: 'The system will accept partial plate matches. Useful during heavy rain, fog, or poor lighting conditions, but significantly increases the chance of false positives. Security staff may need to verify entries manually.',
    },
    medium: {
      title: 'Balanced — Standard Matching',
      desc: 'Good balance between recognition accuracy and environmental tolerance. Suitable for most daytime conditions. Occasional misreads may still occur during dawn or dusk.',
    },
    high: {
      title: 'High Confidence — Strict Matching',
      desc: 'Requires near-perfect plate clarity for a match. Ensures the highest accuracy with minimal false positives, but may require more manual overrides during adverse weather or nighttime.',
    },
  }

  return (
    <AdminLayout>
      <div className="rc-page">

        {/* ── Header ──────────────────────────────────────────── */}
        <div className="rc-header">
          <div>
            <h1 className="rc-title">Rule Constraints</h1>
            <p className="rc-subtitle">
              Configure entry schedules, vehicle access privileges, and ML recognition parameters.
            </p>
          </div>
          <div className="rc-config-badge">
            <Settings2 /> CONFIGURATION
          </div>
        </div>

        {/* ══════════════════════════════════════════════════════
            SECTION 1 — Active Schedule Restrictions
            ══════════════════════════════════════════════════════ */}
        <div className="rc-section">
          <div className="rc-section-head">
            <span className="rc-section-label">
              <CalendarDays size={17} />
              Active Schedule Restrictions
            </span>
            <button
              id="btn-add-schedule-rule"
              className="rc-btn rc-btn-primary rc-btn-sm"
              onClick={openAdd}
            >
              <Plus size={14} /> Add Rule
            </button>
          </div>

          <div className="rc-section-body" style={{ padding: 0 }}>
            {loadingRules ? (
              <div className="rc-empty">
                <Loader2 size={36} className="rc-spin" />
                <p>Loading rule constraints…</p>
              </div>
            ) : rules.length === 0 ? (
              <div className="rc-empty">
                <CalendarDays size={36} />
                <p>No schedule rules configured yet.<br />Click "Add Rule" to create one.</p>
              </div>
            ) : (
              <div className="rc-table-wrap">
                <table className="rc-table">
                  <thead>
                    <tr>
                      <th>Rule Name</th>
                      <th>Type</th>
                      <th>Days</th>
                      <th>Time Window</th>
                      <th>Status</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rules.map((rule) => (
                      <tr key={rule.id} style={{ opacity: rule.enabled ? 1 : 0.55 }}>
                        <td>
                          <span className="rc-rule-name">{rule.name}</span>
                        </td>
                        <td>
                          <span className={`rc-rule-type-badge ${rule.type}`}>
                            {rule.type.charAt(0).toUpperCase() + rule.type.slice(1)}
                          </span>
                        </td>
                        <td>
                          <div className="rc-days-row">
                            {DAY_LABELS.map((d) => (
                              <span
                                key={d.key}
                                className={`rc-day-chip ${rule.days.includes(d.key) ? 'active' : ''}`}
                              >
                                {d.label.charAt(0)}
                              </span>
                            ))}
                          </div>
                        </td>
                        <td>
                          <span className="rc-time-window">
                            <Clock size={13} />
                            {formatTime12(rule.startTime)} – {formatTime12(rule.endTime)}
                          </span>
                        </td>
                        <td>
                          <label className="rc-toggle">
                            <input
                              type="checkbox"
                              checked={rule.enabled}
                              onChange={() => handleToggleRule(rule.id)}
                            />
                            <span className="rc-toggle-track" />
                          </label>
                        </td>
                        <td>
                          <div className="rc-row-actions">
                            <button
                              className="rc-btn rc-btn-icon"
                              title="Edit rule"
                              onClick={() => openEdit(rule)}
                            >
                              <Pencil size={14} />
                            </button>
                              <button
                                className="rc-btn rc-btn-icon delete"
                                title="Delete rule"
                                onClick={() => handleDeleteRule(rule.id)}
                              >
                              <Trash2 size={14} />
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>

        {/* ══════════════════════════════════════════════════════
            SECTION 2 — Vehicle Type Access Privileges
            ══════════════════════════════════════════════════════ */}
        <div className="rc-section">
          <div className="rc-section-head">
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
              <span className="rc-section-label">
                <ShieldCheck size={17} />
                Vehicle Type Access Privileges
              </span>
              <span className="rc-section-desc" style={{ marginLeft: 0 }}>Map vehicle categories to campus entry points (Main Gate 1 &amp; 2)</span>
            </div>
            <button
              className="rc-btn rc-btn-primary rc-btn-sm"
              onClick={openAddVehicle}
            >
              <Plus size={14} /> Add Type
            </button>
          </div>

<div className="rc-section-body">
             {loadingVehicles ? (
               <div className="rc-empty">
                 <Loader2 size={36} className="rc-spin" />
                 <p>Loading vehicle types…</p>
               </div>
             ) : vehicleTypes.length === 0 ? (
               <div className="rc-empty">
                 <Car size={36} />
                 <p>No vehicle types configured yet.<br />Click "Add Type" to create one.</p>
               </div>
             ) : (
               <div className="rc-access-grid">
{vehicleTypes.map((v) => {
                   const IconComp = ICON_COMPONENTS[v.icon] || Car
                   const colorClass = v.category_key === 'four-wheel' ? 'four-wheel' : v.category_key === 'heavy' ? 'heavy' : v.category_key === 'ebike' ? 'ebike' : v.category_key === 'escooter' ? 'escooter' : 'two-wheel'
                   return (
                     <div key={v.id} className={`rc-access-card ${colorClass}`} style={{ opacity: v.enabled ? 1 : 0.55 }}>
                      <div className="rc-access-card-top">
                        <div>
                          <p className="rc-access-card-title">{v.label}</p>
                          <p className="rc-access-card-sub">{v.sub}</p>
                        </div>
                        <div className="rc-access-icon">
                          <IconComp size={22} />
                        </div>
                      </div>

                      <div className="rc-access-detail">
                        <div className="rc-access-row">
                          <span className="rc-access-row-label">Assigned Gate</span>
                          <span className="rc-access-gate-badge">{v.gate}</span>
                        </div>
                        <div className="rc-access-row">
                          <span className="rc-access-row-label">Allowed Hours</span>
                          <span className="rc-access-row-value">{v.is_all_hours ? 'All hours' : `${v.hours_start} – ${v.hours_end}`}</span>
                        </div>
                      </div>

                      <div className="rc-access-status">
                        <span className={`rc-access-status-pill ${v.status}`}>
                          {v.status === 'allowed' ? <CheckCircle size={12} /> : <AlertTriangle size={12} />}
                          {v.status === 'allowed' ? 'Allowed' : 'Restricted Hours'}
                        </span>
                        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                          <button
                            className="rc-btn rc-btn-icon"
                            style={{ width: '28px', height: '28px', background: 'none', border: '1px solid #E2E6EE', color: '#7C80A3', borderRadius: '6px' }}
                            onClick={() => openEditVehicle(v)}
                            title="Edit"
                          >
                            <Pencil size={12} />
                          </button>
                          <button
                            className="rc-btn rc-btn-icon"
                            style={{ width: '28px', height: '28px', background: 'none', border: '1px solid #E2E6EE', color: '#DC2626', borderRadius: '6px' }}
                            onClick={() => handleDeleteVehicle(v.id)}
                            title="Delete"
                          >
                            <Trash2 size={12} />
                          </button>
                          <label className="rc-toggle" style={{ marginLeft: '4px' }}>
                            <input
                              type="checkbox"
                              checked={v.enabled}
                              onChange={() => handleToggleVehicle(v.id)}
                            />
                            <span className="rc-toggle-track" />
                          </label>
                        </div>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        </div>

        {/* ══════════════════════════════════════════════════════
            SECTION 3 — ML Recognition Sensitivity Threshold
            ══════════════════════════════════════════════════════ */}
        <div className="rc-section">
          <div className="rc-section-head">
            <span className="rc-section-label">
              <BrainCircuit size={17} />
              ML Recognition Sensitivity Threshold
            </span>
            <span className="rc-section-desc">Adjust plate recognition confidence</span>
          </div>

          <div className="rc-slider-section">
            {/* Value ring */}
            <div className="rc-slider-display">
              <div className={`rc-slider-value-ring ${level}`}>
                <span className="rc-slider-value-number">
                  {threshold}<span className="rc-slider-value-unit">%</span>
                </span>
              </div>
              <span className="rc-slider-value-label">Match Confidence Threshold</span>
            </div>

            {/* Slider */}
            <div className="rc-slider-track-wrap">
              <input
                id="ml-threshold-slider"
                type="range"
                min="0"
                max="100"
                value={threshold}
                onChange={(e) => setThreshold(Number(e.target.value))}
                className="rc-slider-input"
                style={{ background: getSliderFill(threshold) }}
              />
            </div>
            <div className="rc-slider-labels">
              <span>0% — Very Loose</span>
              <span>100% — Very Strict</span>
            </div>

            {/* Info card */}
            <div className="rc-slider-info">
              <div className={`rc-slider-info-icon ${level}`}>
                <Info size={18} />
              </div>
              <div className="rc-slider-info-text">
                <p className="rc-slider-info-title">{infoMap[level].title}</p>
                <p className="rc-slider-info-desc">{infoMap[level].desc}</p>
              </div>
            </div>

            {/* Quick presets */}
            <div className="rc-slider-presets">
              <button
                className={`rc-btn rc-btn-secondary rc-btn-sm ${threshold === 60 ? 'rc-preset-active' : ''}`}
                onClick={() => setThreshold(60)}
              >
                Rain / Night Mode
              </button>
              <button
                className={`rc-btn rc-btn-secondary rc-btn-sm ${threshold === 85 ? 'rc-preset-active' : ''}`}
                onClick={() => setThreshold(85)}
              >
                Standard
              </button>
              <button
                className={`rc-btn rc-btn-secondary rc-btn-sm ${threshold === 95 ? 'rc-preset-active' : ''}`}
                onClick={() => setThreshold(95)}
              >
                Maximum Accuracy
              </button>
            </div>
          </div>
        </div>

      </div>

      {/* ── Modal ─────────────────────────────────────────────── */}
      {showModal && (
        <RuleModal
          rule={modalRule}
          onSave={handleSave}
          onClose={closeModal}
        />
      )}
      {showVehicleModal && (
        <VehicleModal
          vehicle={modalVehicle}
          onSave={handleSaveVehicle}
          onClose={closeVehicleModal}
        />
      )}
    </AdminLayout>
  )
}
