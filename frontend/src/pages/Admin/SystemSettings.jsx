import { useState, useEffect } from 'react'
import { Settings2, Trash2, Clock, Save, Loader2, ShieldAlert, Megaphone, Send, X, AlertTriangle } from 'lucide-react'
import { toast } from 'sonner'
import AdminLayout from '../../components/Layout/AdminLayout'
import { getSystemSettings, updateSystemSettings, getNotices, createNotice, deactivateNotice } from '../../api/vehicles'
import './SystemSettings.css'

export default function SystemSettings() {
  const FORM_DEFAULTS = { retention_years: 5, scan_dedup_seconds: 60 }
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

  useEffect(() => {
    getSystemSettings()
      .then(({ data }) => {
        const normalized = {
          retention_years:    data.retention_years    ?? 5,
          scan_dedup_seconds: data.scan_dedup_seconds ?? 60,
        }
        setForm(normalized)
        setSaved(normalized)
      })
      .catch(() => toast.error('Failed to load system settings.'))
      .finally(() => setLoading(false))
    fetchNotices()
  }, [])

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
      toast.success('Notice broadcast to all vehicle owners.')
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

  const isDirty      = form.retention_years !== saved.retention_years || form.scan_dedup_seconds !== saved.scan_dedup_seconds
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
    <AdminLayout>
      <div className="ss-page">

        {/* ── Header ─────────────────────────────────────────────────── */}
        <div className="ss-header">
          <div>
            <h1 className="ss-title">System Settings</h1>
            <p className="ss-subtitle">Configure system-wide policies for data retention and scan behaviour.</p>
          </div>
          <span className="ss-badge">
            <Settings2 size={13} />
            CDSO / Admin
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

    </AdminLayout>
  )
}
