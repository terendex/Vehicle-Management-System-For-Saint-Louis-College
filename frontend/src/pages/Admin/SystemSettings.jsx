import { useState, useEffect } from 'react'
import { Settings2, Trash2, Clock, Save, Loader2, ShieldAlert, Megaphone, Send, X, AlertTriangle, CheckCircle2, DoorOpen, Plus, Database, Download, Upload, Receipt, CalendarClock, Timer } from 'lucide-react'
import { toast } from 'sonner'
import { getSystemSettings, updateSystemSettings, getNotices, createNotice, deactivateNotice } from '../../api/vehicles'
import { getGates, createGate, updateGate } from '../../api/scanning'
import { invalidateGates } from '../../hooks/useGates'
import { usersApi } from '../../api/users'
import useTwofaStore from '../../stores/twofaStore'
import './SystemSettings.css'

// Expiration cannot be switched off — only shortened or extended. The period
// must total at least 1 (the server rejects 0/0), so there is no on/off control
// and no "never expires" state to render.
const FORM_DEFAULTS = { retention_years: 5, scan_dedup_seconds: 60, vehicle_pass_fee: 300, vehicle_pass_fee_employee: 150,
  account_expiry_months: 12, account_expiry_days: 0,
  parked_after_seconds: 8, double_park_after_seconds: 12 }

const hasExpiryPeriod = (f) => f.account_expiry_months > 0 || f.account_expiry_days > 0

// A car cannot be badly parked before it counts as parked at all. The server
// rejects this pair too; checking here shows it as the admin types rather than
// as a toast after the round trip.
const dwellOrderValid = (f) => f.double_park_after_seconds >= f.parked_after_seconds

function normalizeSettings(data) {
  return {
    retention_years:    data.retention_years    ?? 5,
    scan_dedup_seconds: data.scan_dedup_seconds ?? 60,
    vehicle_pass_fee:          data.vehicle_pass_fee          ?? 300,
    vehicle_pass_fee_employee: data.vehicle_pass_fee_employee ?? 150,
    account_expiry_months: data.account_expiry_months ?? 12,
    account_expiry_days:   data.account_expiry_days   ?? 0,
    parked_after_seconds:      data.parked_after_seconds      ?? 8,
    double_park_after_seconds: data.double_park_after_seconds ?? 12,
  }
}

/** e.g. '12 months', '30 days', '1 month and 15 days' */
function expiryPeriodText(f) {
  const parts = []
  if (f.account_expiry_months > 0) parts.push(`${f.account_expiry_months} month${f.account_expiry_months !== 1 ? 's' : ''}`)
  if (f.account_expiry_days   > 0) parts.push(`${f.account_expiry_days} day${f.account_expiry_days !== 1 ? 's' : ''}`)
  return parts.join(' and ')
}

export default function SystemSettings() {
  const [form, setForm]       = useState(FORM_DEFAULTS)
  const [saved, setSaved]     = useState(FORM_DEFAULTS)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving]   = useState(false)
  const [confirmSave, setConfirmSave] = useState(false)  // review-before-save modal
  const [saveSummary, setSaveSummary] = useState(null)   // success modal contents

  // Notices state
  const [notices, setNotices]               = useState([])
  const [noticesLoading, setNoticesLoading] = useState(true)
  const [noticeForm, setNoticeForm]         = useState({ title: '', body: '' })
  const [broadcasting, setBroadcasting]     = useState(false)
  const [removingId, setRemovingId]         = useState(null)
  const [confirmDeactivate, setConfirmDeactivate] = useState(null) // { id, title }

  // Gates state
  const [gates, setGates]               = useState([])
  const [gatesLoading, setGatesLoading] = useState(true)
  const [gateForm, setGateForm]         = useState({ number: '', label: '' })
  const [addingGate, setAddingGate]     = useState(false)
  const [togglingGateId, setTogglingGateId] = useState(null)

  // Backup & Restore state
  const [downloading, setDownloading] = useState(false)
  const [restoring, setRestoring]     = useState(false)
  const [restoreFile, setRestoreFile] = useState(null) // File pending confirmation
  const [elapsed, setElapsed]         = useState(0)     // seconds spent on the running op

  // Tick an elapsed-seconds counter while a backup/restore is in progress so the
  // long-running restore visibly shows progression.
  useEffect(() => {
    if (!downloading && !restoring) { setElapsed(0); return }
    setElapsed(0)
    const timer = setInterval(() => setElapsed((e) => e + 1), 1000)
    return () => clearInterval(timer)
  }, [downloading, restoring])

  useEffect(() => {
    getSystemSettings()
      .then(({ data }) => {
        const normalized = normalizeSettings(data)
        setForm(normalized)
        setSaved(normalized)
      })
      .catch(() => toast.error('Failed to load system settings.'))
      .finally(() => setLoading(false))
    fetchNotices()
    fetchGates()
  }, [])

  const fetchGates = () => {
    setGatesLoading(true)
    getGates(true)
      .then(({ data }) => setGates(data))
      .catch(() => toast.error('Failed to load gates.'))
      .finally(() => setGatesLoading(false))
  }

  const handleAddGate = async (e) => {
    e.preventDefault()
    const num = gateForm.number.trim().replace(/^gate/i, '')
    if (!/^\d{1,3}$/.test(num)) {
      toast.error('Gate number must be numeric, e.g. 2.')
      return
    }
    if (!gateForm.label.trim()) return
    setAddingGate(true)
    try {
      const { data } = await createGate({ gate_id: `gate${num}`, label: gateForm.label.trim() })
      setGates((prev) => [...prev, data].sort((a, b) => a.gate_id.localeCompare(b.gate_id, undefined, { numeric: true })))
      setGateForm({ number: '', label: '' })
      invalidateGates()   // Operations Center, camera assignment, gate labels
      toast.success(`${data.label} created. It is now available on the guard gate-login page.`)
    } catch (err) {
      toast.error(err.response?.data?.error || 'Failed to create gate.')
    } finally {
      setAddingGate(false)
    }
  }

  const handleToggleGate = async (gate) => {
    setTogglingGateId(gate.id)
    try {
      const { data } = await updateGate(gate.id, { is_active: !gate.is_active })
      setGates((prev) => prev.map((g) => (g.id === data.id ? data : g)))
      invalidateGates()
      toast.success(`${data.label} ${data.is_active ? 'activated' : 'deactivated'}.`)
    } catch (err) {
      toast.error(err.response?.data?.error || 'Failed to update gate.')
    } finally {
      setTogglingGateId(null)
    }
  }

  const fetchNotices = () => {
    setNoticesLoading(true)
    getNotices()
      .then(({ data }) => setNotices(data))
      .catch(() => toast.error('Failed to load notices.'))
      .finally(() => setNoticesLoading(false))
  }

  const handleBroadcast = async (e) => {
    e.preventDefault()
    if (!noticeForm.title.trim() || !noticeForm.body.trim()) return
    setBroadcasting(true)
    try {
      const { data } = await createNotice(noticeForm)
      setNotices((prev) => [data, ...prev])
      setNoticeForm({ title: '', body: '' })
      if (data.email_status === 'sent') {
        toast.success(`Notice posted and emailed to ${data.recipient_count} vehicle owner${data.recipient_count !== 1 ? 's' : ''}.`)
      } else if (data.email_status === 'no_recipients') {
        toast.info('Notice posted to the owner portal — no active owners to email.')
      } else {
        toast.warning('Notice posted to the owner portal, but the email blast failed. Check SMTP settings.')
      }
    } catch {
      toast.error('Failed to send notice.')
    } finally {
      setBroadcasting(false)
    }
  }

  const handleDeactivate = async (id) => {
    setRemovingId(id)
    try {
      await deactivateNotice(id)
      setNotices((prev) => prev.filter((n) => n.id !== id))
      toast.success('Notice removed.')
    } catch {
      toast.error('Failed to remove notice.')
    } finally {
      setRemovingId(null)
    }
  }

  // Verify before the work, not after. The server demands a code either way,
  // but being asked up front beats being interrupted once a file is already
  // chosen or a form already filled. Cancelling just abandons the action.
  const ensureStepUp = useTwofaStore((s) => s.ensureStepUp)

  const handleDownloadBackup = async () => {
    try {
      await ensureStepUp('Confirm it’s you before downloading a full backup.')
    } catch {
      return
    }
    setDownloading(true)
    try {
      const blob = await usersApi.downloadBackup()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `slc-vms-backup-${new Date().toISOString().slice(0, 10)}.json`
      a.click()
      URL.revokeObjectURL(url)
      toast.success('Backup downloaded successfully.')
    } catch {
      toast.error('Failed to generate backup.')
    } finally {
      setDownloading(false)
    }
  }

  const handleRestorePick = async (e) => {
    const file = e.target.files?.[0]
    e.target.value = '' // reset so picking the same file again re-triggers
    if (!file) return
    if (!file.name.toLowerCase().endsWith('.json')) {
      toast.error('Please choose a .json backup file.')
      return
    }
    try {
      await ensureStepUp('Confirm it’s you before restoring over live data.')
    } catch {
      return
    }
    setRestoreFile(file) // opens the confirmation modal
  }

  const handleRestoreConfirm = async () => {
    const file = restoreFile
    setRestoreFile(null)
    if (!file) return
    setRestoring(true)
    try {
      const res = await usersApi.restoreBackup(file)
      toast.success(`Restore complete — ${res.restored} records loaded. A safety snapshot was saved first.`)
      fetchNotices()
      fetchGates()
    } catch (err) {
      toast.error(err.response?.data?.error || 'Restore failed. No changes were applied.')
    } finally {
      setRestoring(false)
    }
  }

  const isDwellDirty = form.parked_after_seconds !== saved.parked_after_seconds
    || form.double_park_after_seconds !== saved.double_park_after_seconds
  const isDirty      = form.retention_years !== saved.retention_years || form.scan_dedup_seconds !== saved.scan_dedup_seconds
    || form.vehicle_pass_fee !== saved.vehicle_pass_fee || form.vehicle_pass_fee_employee !== saved.vehicle_pass_fee_employee
    || form.account_expiry_months !== saved.account_expiry_months
    || form.account_expiry_days !== saved.account_expiry_days
    || isDwellDirty
  const isDedupDirty = form.scan_dedup_seconds !== saved.scan_dedup_seconds
  const expiryChanged = form.account_expiry_months !== saved.account_expiry_months
    || form.account_expiry_days !== saved.account_expiry_days
  // The server rejects a zero period; block the save here too so the admin sees
  // it as they type rather than as a toast after the round trip.
  const expiryInvalid = !hasExpiryPeriod(form)
  const dwellInvalid  = !dwellOrderValid(form)

  const handleChange = (e) => {
    const { name, value, type, checked } = e.target
    // No field on this form accepts a negative. Clearing a box gives '' → 0,
    // which for the expiry pair is caught by expiryInvalid rather than sent.
    const num = (v) => Math.max(0, Number(v) || 0)
    setForm((prev) => ({ ...prev, [name]: type === 'checkbox' ? checked : type === 'number' ? num(value) : value }))
  }

  const handleSave = async () => {
    try {
      await ensureStepUp('Confirm it’s you before changing system settings.')
    } catch {
      return
    }
    setConfirmSave(false)
    setSaving(true)
    try {
      const { data } = await updateSystemSettings(form)
      const normalized = normalizeSettings(data)
      setForm(normalized)
      setSaved(normalized)
      setSaveSummary({
        expiryPeriod:   expiryPeriodText(normalized),
        expiryChanged,
      })
    } catch (err) {
      const msg = err.response?.data
        ? Object.values(err.response.data).join(' ')
        : 'Failed to save settings.'
      toast.error(msg)
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      <div className="ss-page">

        {/* ── Header ─────────────────────────────────────────────────── */}
        <div className="ss-header">
          <div>
            <h1 className="ss-title">System Settings</h1>
            <p className="ss-subtitle">Configure system-wide policies for data retention and scan behaviour.</p>
          </div>
          <span className="ss-badge">
            <Settings2 size={13} />
            CDSO
          </span>
        </div>

        <div className="ss-panel">

          {loading ? (
            <div className="ss-section">
              <div className="ss-loading">
                <Loader2 size={28} className="ss-spinner" />
                <span>Loading settings…</span>
              </div>
            </div>
          ) : (
            <>
              {/* ── Data Retention ──────────────────────────────────────── */}
              <section className="ss-section">
                <div className="ss-section-head">
                  <div className="ss-section-icon ss-icon-red">
                    <Trash2 size={15} />
                  </div>
                  <div>
                    <h2 className="ss-section-title">Data Retention</h2>
                    <p className="ss-section-desc">
                      Gate access logs, violation records, and archived accounts older than the
                      threshold are deleted automatically each day. Only <em>archived</em> accounts
                      are deleted — an account has to expire and be archived first, which is set
                      under Account Expiration. Active accounts are never touched.
                    </p>
                  </div>
                </div>

                <div className="ss-rows">
                  <div className="ss-row">
                    <div className="ss-row-text">
                      <label className="ss-row-label" htmlFor="retention_years">Retention period</label>
                      <span className="ss-row-hint">Allowed range: 1 – 10 years.</span>
                    </div>
                    <div className="ss-row-control">
                      <input
                        id="retention_years"
                        name="retention_years"
                        type="number"
                        min={1}
                        max={10}
                        value={form.retention_years}
                        onChange={handleChange}
                        className="ss-input"
                      />
                      <span className="ss-unit">years</span>
                    </div>
                  </div>

                  <div className="ss-note">
                    <ShieldAlert size={13} />
                    <span>
                      Access logs, violations, and accounts archived more than <strong>{form.retention_years} year{form.retention_years !== 1 ? 's' : ''}</strong> ago
                      will be permanently deleted and cannot be recovered. Audit log history is kept.
                    </span>
                  </div>
                </div>
              </section>

              {/* ── Vehicle Pass Fee ────────────────────────────────────── */}
              <section className="ss-section">
                <div className="ss-section-head">
                  <div className="ss-section-icon ss-icon-blue">
                    <Receipt size={15} />
                  </div>
                  <div>
                    <h2 className="ss-section-title">Vehicle Pass Fee</h2>
                    <p className="ss-section-desc">
                      Amount registrants are asked to pay at the Accounting Office. Shown on the
                      public registration form and in the Vehicle Pass Terms. Registration only —
                      violation fees are set separately.
                    </p>
                  </div>
                </div>

                <div className="ss-rows">
                  <div className="ss-row">
                    <div className="ss-row-text">
                      <label className="ss-row-label" htmlFor="vehicle_pass_fee">Student / Fetcher fee</label>
                      <span className="ss-row-hint">Charged to student and fetcher registrations.</span>
                    </div>
                    <div className="ss-row-control">
                      <span className="ss-unit">₱</span>
                      <input
                        id="vehicle_pass_fee"
                        name="vehicle_pass_fee"
                        type="number"
                        min={0}
                        step="0.01"
                        value={form.vehicle_pass_fee}
                        onChange={handleChange}
                        className="ss-input"
                      />
                    </div>
                  </div>

                  <div className="ss-row">
                    <div className="ss-row-text">
                      <label className="ss-row-label" htmlFor="vehicle_pass_fee_employee">Employee fee</label>
                      <span className="ss-row-hint">Charged to faculty and staff registrations.</span>
                    </div>
                    <div className="ss-row-control">
                      <span className="ss-unit">₱</span>
                      <input
                        id="vehicle_pass_fee_employee"
                        name="vehicle_pass_fee_employee"
                        type="number"
                        min={0}
                        step="0.01"
                        value={form.vehicle_pass_fee_employee}
                        onChange={handleChange}
                        className="ss-input"
                      />
                    </div>
                  </div>
                </div>
              </section>

              {/* ── Account Expiration ──────────────────────────────────── */}
              <section className="ss-section">
                <div className="ss-section-head">
                  <div className="ss-section-icon ss-icon-red">
                    <CalendarClock size={15} />
                  </div>
                  <div>
                    <h2 className="ss-section-title">Vehicle-Owner Account Expiration</h2>
                    <p className="ss-section-desc">
                      Each vehicle-owner account expires this long after it is created and is then
                      archived automatically. The owner is emailed and can register again — their
                      email, ID and plate are freed for reuse. Expiration cannot be switched off;
                      the period below is the only control.
                    </p>
                  </div>
                </div>

                <div className="ss-rows">
                  <div className="ss-row">
                    <div className="ss-row-text">
                      <label className="ss-row-label" htmlFor="account_expiry_months">Expiry period</label>
                      <span className="ss-row-hint">
                        Months and days must total at least 1 — 0 and 0 is not accepted. Existing
                        owners keep the expiry date they already have; changing the period applies
                        to accounts created from now on.
                      </span>
                    </div>
                    <div className="ss-row-control">
                      <input
                        id="account_expiry_months"
                        name="account_expiry_months"
                        type="number"
                        min={0}
                        max={120}
                        value={form.account_expiry_months}
                        onChange={handleChange}
                        className="ss-input"
                      />
                      <span className="ss-unit">months</span>
                      <input
                        id="account_expiry_days"
                        name="account_expiry_days"
                        type="number"
                        min={0}
                        max={365}
                        value={form.account_expiry_days}
                        onChange={handleChange}
                        className="ss-input"
                        aria-label="Expiry period — days"
                      />
                      <span className="ss-unit">days</span>
                    </div>
                  </div>

                  <div className="ss-note">
                    <ShieldAlert size={13} />
                    <span>
                      {expiryInvalid
                        ? <>Enter at least 1 month or 1 day. Expiration cannot be switched off.</>
                        : <>Owner accounts archive <strong>{expiryPeriodText(form)}</strong> after creation,
                           then are deleted {form.retention_years} year{form.retention_years !== 1 ? 's' : ''} later
                           under the retention policy.</>}
                    </span>
                  </div>
                </div>
              </section>

              {/* ── Scan Grace Period ───────────────────────────────────── */}
              <section className="ss-section">
                <div className="ss-section-head">
                  <div className="ss-section-icon ss-icon-blue">
                    <Clock size={15} />
                  </div>
                  <div>
                    <h2 className="ss-section-title">Scan Deduplication</h2>
                    <p className="ss-section-desc">
                      If the same plate is read again within this window the second scan is suppressed,
                      preventing duplicate entries in the access log.
                    </p>
                  </div>
                </div>

                <div className="ss-rows">
                  <div className="ss-row">
                    <div className="ss-row-text">
                      <label className="ss-row-label" htmlFor="scan_dedup_seconds">Grace period</label>
                      <span className="ss-row-hint">
                        Allowed range: 5 – 300 seconds. Takes effect for new WebSocket connections.
                      </span>
                    </div>
                    <div className="ss-row-control">
                      <input
                        id="scan_dedup_seconds"
                        name="scan_dedup_seconds"
                        type="number"
                        min={5}
                        max={300}
                        value={form.scan_dedup_seconds}
                        onChange={handleChange}
                        className="ss-input"
                      />
                      <span className="ss-unit">seconds</span>
                    </div>
                  </div>
                </div>
              </section>

              {/* ── Parking Detection Delays ────────────────────────────── */}
              <section className="ss-section">
                <div className="ss-section-head">
                  <div className="ss-section-icon ss-icon-blue">
                    <Timer size={15} />
                  </div>
                  <div>
                    <h2 className="ss-section-title">Parking Detection Delays</h2>
                    <p className="ss-section-desc">
                      Parking cameras follow each vehicle and time how long it has stayed still. These
                      say how long a vehicle must be stopped before the camera commits — long enough
                      that a car driving past a bay, or reversing into one, is not mistaken for a
                      parked one.
                    </p>
                  </div>
                </div>

                <div className="ss-rows">
                  <div className="ss-row">
                    <div className="ss-row-text">
                      <label className="ss-row-label" htmlFor="parked_after_seconds">Counts as parked after</label>
                      <span className="ss-row-hint">
                        Allowed range: 1 – 120 seconds. Until this elapses the bay stays free, so
                        someone else can still be directed to it.
                      </span>
                    </div>
                    <div className="ss-row-control">
                      <input
                        id="parked_after_seconds"
                        name="parked_after_seconds"
                        type="number"
                        min={1}
                        max={120}
                        value={form.parked_after_seconds}
                        onChange={handleChange}
                        className="ss-input"
                      />
                      <span className="ss-unit">seconds</span>
                    </div>
                  </div>

                  <div className="ss-row">
                    <div className="ss-row-text">
                      <label className="ss-row-label" htmlFor="double_park_after_seconds">Reports double parking after</label>
                      <span className="ss-row-hint">
                        Allowed range: 1 – 300 seconds, and never shorter than the parked delay. This
                        one issues a violation, so it is worth leaving longer.
                      </span>
                    </div>
                    <div className="ss-row-control">
                      <input
                        id="double_park_after_seconds"
                        name="double_park_after_seconds"
                        type="number"
                        min={1}
                        max={300}
                        value={form.double_park_after_seconds}
                        onChange={handleChange}
                        className="ss-input"
                      />
                      <span className="ss-unit">seconds</span>
                    </div>
                  </div>

                  <div className="ss-note">
                    <ShieldAlert size={13} />
                    <span>
                      {dwellInvalid
                        ? <>The double-parking delay cannot be shorter than the parked delay — a car
                           cannot be badly parked before it counts as parked at all.</>
                        : <>A vehicle across two bays is reported once it has been still
                           for <strong>{form.double_park_after_seconds} seconds</strong>. Running zones
                           pick up a change within a few seconds; no restart needed.</>}
                    </span>
                  </div>
                </div>
              </section>
            </>
          )}

          {/* ── Campus Gates ──────────────────────────────────────────── */}
          <section className="ss-section">
            <div className="ss-section-head">
              <div className="ss-section-icon ss-icon-blue">
                <DoorOpen size={15} />
              </div>
              <div>
                <h2 className="ss-section-title">Campus Gates</h2>
                <p className="ss-section-desc">
                  Add new gates as the school expands. Active gates immediately appear on the guard
                  gate-login page and can be assigned entry cameras in Device Management.
                </p>
              </div>
            </div>

            <div className="ss-rows">
              <div className="ss-row ss-row--stacked">
                <form className="ss-inline-form" onSubmit={handleAddGate}>
                  <div className="ss-form-grid">
                    <div className="ss-field" style={{ flex: '0 0 140px' }}>
                      <label className="ss-label" htmlFor="gate-number">Gate Number</label>
                      <input
                        id="gate-number"
                        className="ss-text-input"
                        type="text"
                        inputMode="numeric"
                        placeholder="e.g. 2"
                        maxLength={3}
                        value={gateForm.number}
                        onChange={(e) => setGateForm((p) => ({ ...p, number: e.target.value }))}
                        required
                      />
                    </div>
                    <div className="ss-field" style={{ flex: 1, minWidth: 220 }}>
                      <label className="ss-label" htmlFor="gate-label">Display Label</label>
                      <input
                        id="gate-label"
                        className="ss-text-input"
                        type="text"
                        placeholder="e.g. Gate 2 — North Entrance"
                        maxLength={100}
                        value={gateForm.label}
                        onChange={(e) => setGateForm((p) => ({ ...p, label: e.target.value }))}
                        required
                      />
                    </div>
                  </div>
                  <button
                    type="submit"
                    className="ss-broadcast-btn"
                    disabled={addingGate || !gateForm.number.trim() || !gateForm.label.trim()}
                  >
                    {addingGate ? <Loader2 size={15} className="ss-spinner" /> : <Plus size={15} />}
                    {addingGate ? 'Adding…' : 'Add Gate'}
                  </button>
                </form>
              </div>

              <div className="ss-row ss-row--stacked">
                <div className="ss-list-head">
                  <span>Gates ({gatesLoading ? '…' : gates.length})</span>
                </div>
                {gatesLoading ? (
                  <div className="ss-loading"><Loader2 size={18} className="ss-spinner" /><span>Loading gates…</span></div>
                ) : gates.length === 0 ? (
                  <p className="ss-no-notices">No gates configured.</p>
                ) : (
                  <div className="ss-notices-list">
                    {gates.map((g) => (
                      <div key={g.id} className={`ss-gate-item${g.is_active ? '' : ' is-inactive'}`}>
                        <div>
                          <strong className="ss-gate-label">{g.label}</strong>
                          <span className="ss-gate-id">{g.gate_id}</span>
                          {!g.is_active && <span className="ss-gate-tag">Inactive</span>}
                        </div>
                        <button
                          type="button"
                          className="ss-gate-btn"
                          disabled={togglingGateId === g.id}
                          onClick={() => handleToggleGate(g)}
                        >
                          {togglingGateId === g.id ? 'Saving…' : g.is_active ? 'Deactivate' : 'Activate'}
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </section>

          {/* ── Parking Notices ───────────────────────────────────────── */}
          <section className="ss-section">
            <div className="ss-section-head">
              <div className="ss-section-icon ss-icon-purple">
                <Megaphone size={15} />
              </div>
              <div>
                <h2 className="ss-section-title">Broadcast Parking Notice</h2>
                <p className="ss-section-desc">
                  Send an announcement to all registered vehicle owners via email and the owner portal.
                </p>
              </div>
            </div>

            <div className="ss-rows">
              <div className="ss-row ss-row--stacked">
                <form className="ss-inline-form" onSubmit={handleBroadcast}>
                  <div className="ss-field">
                    <label className="ss-label" htmlFor="notice-title">Subject / Title</label>
                    <input
                      id="notice-title"
                      className="ss-text-input"
                      type="text"
                      placeholder="e.g. Parking suspension on June 28"
                      maxLength={200}
                      value={noticeForm.title}
                      onChange={(e) => setNoticeForm((p) => ({ ...p, title: e.target.value }))}
                      required
                    />
                  </div>
                  <div className="ss-field">
                    <label className="ss-label" htmlFor="notice-body">Message</label>
                    <textarea
                      id="notice-body"
                      className="ss-textarea"
                      rows={4}
                      placeholder="Write the full notice text here…"
                      value={noticeForm.body}
                      onChange={(e) => setNoticeForm((p) => ({ ...p, body: e.target.value }))}
                      required
                    />
                  </div>
                  <button
                    type="submit"
                    className="ss-broadcast-btn"
                    disabled={broadcasting || !noticeForm.title.trim() || !noticeForm.body.trim()}
                  >
                    {broadcasting ? <Loader2 size={15} className="ss-spinner" /> : <Send size={15} />}
                    {broadcasting ? 'Sending…' : 'Broadcast to All Owners'}
                  </button>
                </form>
              </div>

              {/* Active notices list */}
              <div className="ss-row ss-row--stacked">
                <div className="ss-list-head">
                  <span>Active Notices ({noticesLoading ? '…' : notices.length})</span>
                </div>
                {noticesLoading ? (
                  <div className="ss-loading"><Loader2 size={18} className="ss-spinner" /><span>Loading notices…</span></div>
                ) : notices.length === 0 ? (
                  <p className="ss-no-notices">No active notices.</p>
                ) : (
                  <div className="ss-notices-list">
                    {notices.map((n) => (
                      <div key={n.id} className="ss-notice-item">
                        <div className="ss-notice-item-body">
                          <span className="ss-notice-title">{n.title}</span>
                          <span className="ss-notice-meta">
                            {new Date(n.created_at).toLocaleDateString('en-PH', { year: 'numeric', month: 'short', day: 'numeric' })}
                            {n.created_by_name && <> · by {n.created_by_name}</>}
                          </span>
                          <p className="ss-notice-body">{n.body}</p>
                        </div>
                        <button
                          className="ss-notice-remove"
                          title="Deactivate notice"
                          disabled={removingId === n.id}
                          onClick={() => setConfirmDeactivate({ id: n.id, title: n.title })}
                        >
                          {removingId === n.id ? <Loader2 size={14} className="ss-spinner" /> : <X size={14} />}
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </section>

          {/* ── Backup & Restore ──────────────────────────────────────── */}
          <section className="ss-section">
            <div className="ss-section-head">
              <div className="ss-section-icon ss-icon-blue">
                <Database size={15} />
              </div>
              <div>
                <h2 className="ss-section-title">Backup &amp; Restore</h2>
                <p className="ss-section-desc">
                  Download a snapshot of system data — users, vehicles, registrations,
                  violations and scan logs — as a JSON file you can save anywhere, or restore the
                  system from a previously downloaded backup.
                </p>
              </div>
            </div>

            <div className="ss-rows">
              <div className="ss-row">
                <div className="ss-row-text">
                  <span className="ss-row-label">Full system snapshot</span>
                  <span className="ss-row-hint">
                    The file downloads to this computer — keep it somewhere safe, it holds
                    every account and plate in the system. Uploaded images (owner photos,
                    licence scans, snapshots) are <strong>not</strong> included — only their
                    filenames — so back those up separately. Authenticator pairings are also
                    left out, so a restore never breaks anyone&rsquo;s 2FA.
                  </span>
                </div>
                <div className="ss-backup-actions">
                  <button
                    type="button"
                    className="ss-broadcast-btn"
                    onClick={handleDownloadBackup}
                    disabled={downloading || restoring}
                  >
                    {downloading ? <Loader2 size={15} className="ss-spinner" /> : <Download size={15} />}
                    {downloading ? 'Preparing…' : 'Download Backup'}
                  </button>

                  <label className={`ss-restore-btn${restoring ? ' is-busy' : ''}`}>
                    {restoring ? <Loader2 size={15} className="ss-spinner" /> : <Upload size={15} />}
                    {restoring ? 'Restoring…' : 'Restore from Backup'}
                    <input
                      type="file"
                      accept="application/json,.json"
                      hidden
                      disabled={downloading || restoring}
                      onChange={handleRestorePick}
                    />
                  </label>
                </div>
              </div>

              <div className="ss-note">
                <ShieldAlert size={13} />
                <span>
                  Restoring overwrites current records with those in the backup. A safety snapshot of the
                  current data is saved automatically before any restore, and the load is rolled back if it fails.
                </span>
              </div>
            </div>
          </section>

        </div>

        {/* ── Save Bar ───────────────────────────────────────────────── */}
        {!loading && (
          <div className={`ss-save-bar ${isDirty ? 'ss-save-bar--visible' : ''}`}>
            <span className="ss-unsaved-label">
              {expiryInvalid
                ? 'Account expiration needs at least 1 month or 1 day'
                : dwellInvalid
                  ? 'Double-parking delay cannot be shorter than the parked delay'
                  : 'Unsaved changes'}
            </span>
            <button
              className="ss-save-btn"
              onClick={() => setConfirmSave(true)}
              disabled={saving || !isDirty || expiryInvalid || dwellInvalid}
            >
              {saving ? <Loader2 size={15} className="ss-spinner" /> : <Save size={15} />}
              {saving ? 'Saving…' : 'Save Changes'}
            </button>
          </div>
        )}

      </div>

      {/* ── Confirm Save Modal ─── */}
      {confirmSave && (
        <div className="ss-overlay" onClick={() => setConfirmSave(false)}>
          <div className="ss-modal" onClick={e => e.stopPropagation()}>
            <button className="ss-modal-close" onClick={() => setConfirmSave(false)}><X size={16} /></button>
            <AlertTriangle size={32} className="ss-modal-icon-warn" />
            <h2 className="ss-modal-title">Save System Settings?</h2>
            <p className="ss-modal-body">
              These settings apply system-wide and take effect immediately.
            </p>

            <ul className="ss-confirm-list">
              {form.retention_years !== saved.retention_years && (
                <li>
                  Records are kept for <strong>{form.retention_years} year{form.retention_years !== 1 ? 's' : ''}</strong>.
                  Access logs, violations, and accounts archived longer ago than that are
                  permanently deleted on the next daily purge.
                  {form.retention_years < saved.retention_years && (
                    <> Shortening the window means the next purge deletes more than the last one did.</>
                  )}
                </li>
              )}
              {isDedupDirty && (
                <li>Repeat scans of the same plate are ignored for <strong>{form.scan_dedup_seconds} second{form.scan_dedup_seconds !== 1 ? 's' : ''}</strong>.</li>
              )}
              {form.vehicle_pass_fee !== saved.vehicle_pass_fee && (
                <li>Student vehicle-pass fee becomes <strong>₱{form.vehicle_pass_fee}</strong>.</li>
              )}
              {form.vehicle_pass_fee_employee !== saved.vehicle_pass_fee_employee && (
                <li>Employee vehicle-pass fee becomes <strong>₱{form.vehicle_pass_fee_employee}</strong>.</li>
              )}
              {expiryChanged && (
                <li>
                  Owner-account expiry period changes to <strong>{expiryPeriodText(form)}</strong> for
                  newly created accounts. Owners that already have an expiry date keep it.
                </li>
              )}
              {form.parked_after_seconds !== saved.parked_after_seconds && (
                <li>
                  A vehicle must stand still for <strong>{form.parked_after_seconds} second{form.parked_after_seconds !== 1 ? 's' : ''}</strong> before
                  a parking camera claims its bay.
                </li>
              )}
              {form.double_park_after_seconds !== saved.double_park_after_seconds && (
                <li>
                  A vehicle across two bays is reported as double parking after
                  standing still for <strong>{form.double_park_after_seconds} second{form.double_park_after_seconds !== 1 ? 's' : ''}</strong>.
                  {form.double_park_after_seconds < saved.double_park_after_seconds && (
                    <> Shortening this makes violations more likely to be issued while a driver is
                       still manoeuvring.</>
                  )}
                </li>
              )}
            </ul>

            <div className="ss-modal-actions">
              <button className="ss-modal-btn ss-modal-btn-ghost" onClick={() => setConfirmSave(false)}>Cancel</button>
              <button className="ss-modal-btn ss-modal-btn-primary" onClick={handleSave}>Save Changes</button>
            </div>
          </div>
        </div>
      )}

      {/* ── Save Success Modal ─── */}
      {saveSummary && (
        <div className="ss-overlay" onClick={() => setSaveSummary(null)}>
          <div className="ss-modal" onClick={e => e.stopPropagation()}>
            <button className="ss-modal-close" onClick={() => setSaveSummary(null)}><X size={16} /></button>
            <CheckCircle2 size={32} className="ss-modal-icon-ok" />
            <h2 className="ss-modal-title">Settings Saved</h2>
            <p className="ss-modal-body">
              Your changes are live.
              {saveSummary.expiryChanged
                ? ` New owner accounts now expire ${saveSummary.expiryPeriod} after creation, and expired ones are archived automatically.`
                : ''}
            </p>
            <div className="ss-modal-actions">
              <button className="ss-modal-btn ss-modal-btn-primary" onClick={() => setSaveSummary(null)}>Done</button>
            </div>
          </div>
        </div>
      )}

      {/* ── Backup / Restore progress overlay ─── */}
      {(downloading || restoring) && (
        <div className="ss-overlay">
          <div className="ss-modal ss-loading-modal">
            <Loader2 size={46} className="ss-spinner ss-loading-spinner" />
            <h2 className="ss-modal-title">
              {restoring ? 'Restoring data…' : 'Preparing backup…'}
            </h2>
            <p className="ss-modal-body">
              {restoring
                ? 'Loading records into the database. This can take a few minutes — please keep this page open and do not refresh.'
                : 'Generating your backup file. It will download automatically when ready.'}
            </p>
            <span className="ss-loading-elapsed">
              {Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, '0')} elapsed
            </span>
          </div>
        </div>
      )}

      {/* ── Confirm Restore Modal ─── */}
      {restoreFile && (
        <div className="ss-overlay" onClick={() => setRestoreFile(null)}>
          <div className="ss-modal" onClick={e => e.stopPropagation()}>
            <button className="ss-modal-close" onClick={() => setRestoreFile(null)}><X size={16} /></button>
            <AlertTriangle size={32} className="ss-modal-icon-warn" />
            <h2 className="ss-modal-title">Restore from Backup?</h2>
            <p className="ss-modal-body">
              This loads <strong>{restoreFile.name}</strong> over your current data. Records in
              the file replace the matching ones, but anything created since the backup was
              taken <strong>stays</strong> &mdash; this is a merge, not a rewind to that date.
            </p>
            <p className="ss-modal-body">
              Passwords are part of the file, so anyone who has changed theirs since will need
              the older one. Authenticator pairings are not touched. A safety snapshot of the
              current data is saved first, and the whole restore is rolled back automatically if
              anything fails. Continue?
            </p>
            <div className="ss-modal-actions">
              <button className="ss-modal-btn ss-modal-btn-ghost" onClick={() => setRestoreFile(null)}>Cancel</button>
              <button className="ss-modal-btn ss-modal-btn-danger" onClick={handleRestoreConfirm}>Restore</button>
            </div>
          </div>
        </div>
      )}

      {/* ── Confirm Deactivate Notice Modal ─── */}
      {confirmDeactivate && (
        <div className="ss-overlay" onClick={() => setConfirmDeactivate(null)}>
          <div className="ss-modal" onClick={e => e.stopPropagation()}>
            <button className="ss-modal-close" onClick={() => setConfirmDeactivate(null)}><X size={16} /></button>
            <AlertTriangle size={32} className="ss-modal-icon-warn" />
            <h2 className="ss-modal-title">Remove Notice?</h2>
            <p className="ss-modal-body">
              This will deactivate <strong>"{confirmDeactivate.title}"</strong> and hide it from all owners. This cannot be undone.
            </p>
            <div className="ss-modal-actions">
              <button className="ss-modal-btn ss-modal-btn-ghost" onClick={() => setConfirmDeactivate(null)}>Cancel</button>
              <button
                className="ss-modal-btn ss-modal-btn-danger"
                onClick={() => { const id = confirmDeactivate.id; setConfirmDeactivate(null); handleDeactivate(id) }}
              >
                Remove
              </button>
            </div>
          </div>
        </div>
      )}

    </>
  )
}
