import { useState, useEffect } from 'react'
import { Settings2, Trash2, Clock, Save, Loader2, ShieldAlert, Megaphone, Send, X, AlertTriangle, DoorOpen, Plus, Database, Download, Upload, Receipt } from 'lucide-react'
import { toast } from 'sonner'
import { getSystemSettings, updateSystemSettings, getNotices, createNotice, deactivateNotice } from '../../api/vehicles'
import { getGates, createGate, updateGate } from '../../api/scanning'
import { usersApi } from '../../api/users'
import './SystemSettings.css'

export default function SystemSettings() {
  const FORM_DEFAULTS = { retention_years: 5, scan_dedup_seconds: 60, vehicle_pass_fee: 300, vehicle_pass_fee_employee: 150 }
  const [form, setForm]       = useState(FORM_DEFAULTS)
  const [saved, setSaved]     = useState(FORM_DEFAULTS)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving]   = useState(false)

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
        const normalized = {
          retention_years:    data.retention_years    ?? 5,
          scan_dedup_seconds: data.scan_dedup_seconds ?? 60,
          vehicle_pass_fee:          data.vehicle_pass_fee          ?? 300,
          vehicle_pass_fee_employee: data.vehicle_pass_fee_employee ?? 150,
        }
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

  const handleDownloadBackup = async () => {
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

  const handleRestorePick = (e) => {
    const file = e.target.files?.[0]
    e.target.value = '' // reset so picking the same file again re-triggers
    if (!file) return
    if (!file.name.toLowerCase().endsWith('.json')) {
      toast.error('Please choose a .json backup file.')
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

  const isDirty      = form.retention_years !== saved.retention_years || form.scan_dedup_seconds !== saved.scan_dedup_seconds
    || form.vehicle_pass_fee !== saved.vehicle_pass_fee || form.vehicle_pass_fee_employee !== saved.vehicle_pass_fee_employee
  const isDedupDirty = form.scan_dedup_seconds !== saved.scan_dedup_seconds

  const handleChange = (e) => {
    const { name, value, type } = e.target
    setForm((prev) => ({ ...prev, [name]: type === 'number' ? Number(value) : value }))
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      const { data } = await updateSystemSettings(form)
      setForm(data)
      setSaved(data)
      toast.success('System settings saved.')
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

        {loading ? (
          <div className="ss-loading">
            <Loader2 size={28} className="ss-spinner" />
            <span>Loading settings…</span>
          </div>
        ) : (
          <div className="ss-cards">

            {/* ── Data Retention ────────────────────────────────────────── */}
            <div className="ss-card">
              <div className="ss-card-head">
                <div className="ss-card-icon ss-icon-red">
                  <Trash2 size={16} />
                </div>
                <div>
                  <h2 className="ss-card-title">Data Retention Policy</h2>
                  <p className="ss-card-desc">
                    Access logs and violation records older than the threshold are automatically
                    deleted at 2:00 AM every day.
                  </p>
                </div>
              </div>

              <div className="ss-field">
                <label className="ss-label" htmlFor="retention_years">
                  Retention period
                </label>
                <div className="ss-input-row">
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
                <p className="ss-hint">Allowed range: 1 – 10 years.</p>
              </div>

              <div className="ss-info-row">
                <ShieldAlert size={13} />
                Records older than <strong>{form.retention_years} year{form.retention_years !== 1 ? 's' : ''}</strong> will be permanently deleted and cannot be recovered.
              </div>
            </div>

            {/* ── Vehicle Pass Fee ──────────────────────────────────────── */}
            <div className="ss-card">
              <div className="ss-card-head">
                <div className="ss-card-icon ss-icon-blue">
                  <Receipt size={16} />
                </div>
                <div>
                  <h2 className="ss-card-title">Vehicle Pass Fee</h2>
                  <p className="ss-card-desc">
                    Amount registrants are asked to pay at the Accounting Office. Shown on the
                    public registration form.
                  </p>
                </div>
              </div>

              <div className="ss-field">
                <label className="ss-label" htmlFor="vehicle_pass_fee">
                  Student / Fetcher fee
                </label>
                <div className="ss-input-row">
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

              <div className="ss-field">
                <label className="ss-label" htmlFor="vehicle_pass_fee_employee">
                  Employee fee
                </label>
                <div className="ss-input-row">
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

            {/* ── Backup & Restore ──────────────────────────────────────── */}
            <div className="ss-card">
              <div className="ss-card-head">
                <div className="ss-card-icon ss-icon-blue">
                  <Database size={16} />
                </div>
                <div>
                  <h2 className="ss-card-title">Backup &amp; Restore</h2>
                  <p className="ss-card-desc">
                    Download a full snapshot of all system data — users, vehicles, registrations,
                    violations and scan logs — as a JSON file you can save anywhere, or restore the
                    system from a previously downloaded backup.
                  </p>
                </div>
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

              <div className="ss-info-row">
                <ShieldAlert size={13} />
                Restoring overwrites current records with those in the backup. A safety snapshot of the
                current data is saved automatically before any restore, and the load is rolled back if it fails.
              </div>
            </div>

            {/* ── Scan Grace Period ─────────────────────────────────────── */}
            <div className="ss-card">
              <div className="ss-card-head">
                <div className="ss-card-icon ss-icon-blue">
                  <Clock size={16} />
                </div>
                <div>
                  <h2 className="ss-card-title">Scan Deduplication Window</h2>
                  <p className="ss-card-desc">
                    If the same plate is read again within this window the second scan is suppressed,
                    preventing duplicate entries in the access log.
                  </p>
                </div>
              </div>

              <div className="ss-field">
                <label className="ss-label" htmlFor="scan_dedup_seconds">
                  Grace period
                </label>
                <div className="ss-input-row">
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
                <p className="ss-hint">Allowed range: 5 – 300 seconds. Takes effect for new WebSocket connections.</p>
              </div>

              <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                <button
                  className="ss-save-btn"
                  style={{ opacity: isDedupDirty ? 1 : 0.45, cursor: isDedupDirty ? 'pointer' : 'default' }}
                  onClick={handleSave}
                  disabled={saving || !isDedupDirty}
                >
                  {saving ? <Loader2 size={15} className="ss-spinner" /> : <Save size={15} />}
                  {saving ? 'Saving…' : 'Save Changes'}
                </button>
              </div>
            </div>

          </div>
        )}

        {/* ── Campus Gates ──────────────────────────────────────────── */}
        <div className="ss-notice-section">
          <div className="ss-card">
            <div className="ss-card-head">
              <div className="ss-card-icon ss-icon-blue">
                <DoorOpen size={16} />
              </div>
              <div>
                <h2 className="ss-card-title">Campus Gates</h2>
                <p className="ss-card-desc">
                  Add new gates as the school expands. Active gates immediately appear on the guard
                  gate-login page and can be assigned entry cameras in Device Management.
                </p>
              </div>
            </div>

            <form className="ss-notice-form" onSubmit={handleAddGate}>
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
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

            <div className="ss-notices-list-head">
              <span>Gates ({gatesLoading ? '…' : gates.length})</span>
            </div>
            {gatesLoading ? (
              <div className="ss-loading"><Loader2 size={18} className="ss-spinner" /><span>Loading gates…</span></div>
            ) : gates.length === 0 ? (
              <p className="ss-no-notices">No gates configured.</p>
            ) : (
              <div className="ss-notices-list">
                {gates.map((g) => (
                  <div
                    key={g.id}
                    style={{
                      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                      padding: '10px 14px', border: '1px solid #D3E1EC', borderRadius: 10, marginBottom: 8,
                      background: g.is_active ? '#fff' : '#F7FAFC',
                    }}
                  >
                    <div>
                      <strong style={{ fontSize: 13.5, color: '#0B2340' }}>{g.label}</strong>
                      <span style={{ marginLeft: 8, fontSize: 12, color: '#64839C', fontFamily: 'monospace' }}>{g.gate_id}</span>
                      {!g.is_active && (
                        <span style={{ marginLeft: 8, fontSize: 11, fontWeight: 600, color: '#8A6B00', background: '#FDF0BE', padding: '2px 8px', borderRadius: 6 }}>
                          Inactive
                        </span>
                      )}
                    </div>
                    <button
                      type="button"
                      className="ss-save-btn"
                      style={{ padding: '6px 12px', fontSize: 12 }}
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

        {/* ── Parking Notices ────────────────────────────────────────── */}
        <div className="ss-notice-section">
          <div className="ss-card ss-notice-card">
            <div className="ss-card-head">
              <div className="ss-card-icon ss-icon-purple">
                <Megaphone size={16} />
              </div>
              <div>
                <h2 className="ss-card-title">Broadcast Parking Notice</h2>
                <p className="ss-card-desc">
                  Send an announcement to all registered vehicle owners via email and the owner portal.
                </p>
              </div>
            </div>

            <form className="ss-notice-form" onSubmit={handleBroadcast}>
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

            {/* Active notices list */}
            <div className="ss-notices-list-head">
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

        {/* ── Save Bar ───────────────────────────────────────────────── */}
        {!loading && (
          <div className={`ss-save-bar ${isDirty ? 'ss-save-bar--visible' : ''}`}>
            <span className="ss-unsaved-label">Unsaved changes</span>
            <button
              className="ss-save-btn"
              onClick={handleSave}
              disabled={saving || !isDirty}
            >
              {saving ? <Loader2 size={15} className="ss-spinner" /> : <Save size={15} />}
              {saving ? 'Saving…' : 'Save Changes'}
            </button>
          </div>
        )}

      </div>

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
              This will overwrite current system data with the contents of{' '}
              <strong>{restoreFile.name}</strong>. A safety snapshot of the current data is saved
              first, and the restore is rolled back automatically if anything goes wrong. Continue?
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
