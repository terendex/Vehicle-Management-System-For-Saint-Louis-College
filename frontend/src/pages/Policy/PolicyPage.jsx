import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft, Shield, FileText, Pencil, Save, RotateCcw } from 'lucide-react'
import { registrationApi } from '../../api/registration'
import { policiesApi } from '../../api/policies'
import useAuthStore from '../../stores/authStore'
import notify from '../../components/Feedback/notify'
import PolicyMarkdown from './PolicyMarkdown'
import { POLICY_DEFAULTS, MARKDOWN_HELP } from './policyDefaults'
import './PolicyPage.css'
import BrandLogos from '../../components/BrandLogos'

const TABS = [
  { id: 'privacy', label: 'Privacy Policy', icon: Shield },
  { id: 'terms',   label: 'Vehicle Pass Terms', icon: FileText },
]

const formatDate = (iso) => new Date(iso).toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })

export default function PolicyPage() {
  const navigate = useNavigate()
  const [activeTab, setActiveTab] = useState('privacy')

  // The CDSO (the admin role) edits the wording in place on this page. Anyone
  // else, signed in or not, only reads it. The server enforces the same rule.
  const isAdmin = useAuthStore(s => s.user?.role === 'admin')

  // The terms quote the Vehicle Pass fee, so it has to come from the same
  // settings the registration form charges from. It used to be a hardcoded
  // ₱350.00 here, which contradicted the amount an applicant was actually
  // told to pay. Falls back to the model defaults if the call fails.
  const [fees, setFees] = useState(null)

  // Edited wording, per tab: { content, updated_at, updated_by }. A tab with
  // content null (or a failed load) shows the built-in wording instead.
  const [policies, setPolicies] = useState({})
  const [loaded, setLoaded] = useState(false)

  const [editing, setEditing] = useState(false)
  const [preview, setPreview] = useState(false)
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let cancelled = false
    registrationApi.getRegistrationStatus()
      .then(status => { if (!cancelled) setFees(status) })
      .catch(() => {})   // non-critical — defaults below still render a figure
    policiesApi.list()
      .then(data => { if (!cancelled) setPolicies(data) })
      .catch(() => {})   // the built-in wording still renders
      .finally(() => { if (!cancelled) setLoaded(true) })
    return () => { cancelled = true }
  }, [])

  const studentFee  = fees?.vehicle_pass_fee ?? 300
  const employeeFee = fees?.vehicle_pass_fee_employee ?? 150
  const feeValues = {
    student_fee:  `₱${studentFee.toFixed(2)}`,
    employee_fee: `₱${employeeFee.toFixed(2)}`,
  }

  const stored = policies[activeTab]
  const source = stored?.content ?? POLICY_DEFAULTS[activeTab]
  const dirty  = editing && draft !== source

  // Leaving the page with unsaved wording asks the browser to confirm.
  useEffect(() => {
    if (!dirty) return
    const warn = (e) => { e.preventDefault(); e.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  const confirmDiscard = async () => !dirty || notify.confirm({
    title: 'Discard changes?',
    message: 'Your edits to this policy have not been saved.',
    confirmLabel: 'Discard',
    danger: true,
  })

  const startEditing = () => {
    setDraft(source)
    setPreview(false)
    setEditing(true)
  }

  const cancelEditing = async () => {
    if (await confirmDiscard()) setEditing(false)
  }

  const switchTab = async (id) => {
    if (id === activeTab) return
    if (!(await confirmDiscard())) return
    setEditing(false)
    setActiveTab(id)
  }

  const goBack = async () => {
    if (await confirmDiscard()) navigate(-1)
  }

  const save = async () => {
    if (!draft.trim()) {
      notify.error('The policy cannot be empty.', { title: 'Policy not saved' })
      return
    }
    setSaving(true)
    try {
      const saved = await policiesApi.save(activeTab, draft)
      setPolicies(p => ({ ...p, [activeTab]: saved }))
      setEditing(false)
      notify.success('The policy has been updated.', { title: 'Policy saved' })
    } catch (err) {
      if (err.stepUpCancelled) return   // they backed out of the code prompt
      notify.error(err.response?.data?.content || err.response?.data?.detail || 'Failed to save the policy.',
        { title: 'Policy not saved' })
    } finally { setSaving(false) }
  }

  const restoreDefault = async () => {
    if (!(await notify.confirm({
      title: 'Restore default wording?',
      message: `The ${TABS.find(t => t.id === activeTab).label} goes back to its original text. Your edited version is discarded.`,
      confirmLabel: 'Restore',
      danger: true,
    }))) return
    setSaving(true)
    try {
      const restored = await policiesApi.restoreDefault(activeTab)
      setPolicies(p => ({ ...p, [activeTab]: restored }))
      setEditing(false)
      notify.success('The original wording is back.', { title: 'Policy restored' })
    } catch (err) {
      if (err.stepUpCancelled) return
      notify.error(err.response?.data?.detail || 'Failed to restore the policy.', { title: 'Policy not restored' })
    } finally { setSaving(false) }
  }

  return (
    <div className="policy-page">
      {/* Header */}
      <header className="policy-header">
        {/* Same shape as the registration header: brand lockup on the left,
            the single back control on the right. The Return button used to sit
            in front of the logo, which pushed the lockup off the left edge and
            made this bar read differently from every other page. */}
        <div className="policy-header-inner header-content">
          <div className="header-logo-group">
            <BrandLogos size="header" />
            <div className="header-text">
              <span className="header-title">SAINT LOUIS COLLEGE</span>
              <span className="header-subtitle">Smart Parking and Vehicle Verification System</span>
            </div>
          </div>
          <button
            className="header-back-btn header-back-btn--end"
            onClick={goBack}
          >
            <ArrowLeft size={16} />
            <span>Return</span>
          </button>
        </div>
      </header>

      {/* Page Title */}
      <div className="policy-hero">
        <h1 className="policy-hero-title">Policies &amp; Terms</h1>
        <p className="policy-hero-sub">
          Saint Louis College — Data Privacy Policy and Vehicle Pass Terms &amp; Conditions
        </p>
      </div>

      {/* Tabs */}
      <div className="policy-tabs-bar">
        <div className="policy-tabs-inner">
          {TABS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              className={`policy-tab-btn ${activeTab === id ? 'active' : ''}`}
              onClick={() => switchTab(id)}
            >
              <Icon size={15} />
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      <main className="policy-main">
        <div className="policy-card">

          <div className="policy-card-head">
            <div className={`policy-section-badge ${activeTab === 'terms' ? 'terms' : ''}`}>
              {activeTab === 'terms' ? 'Vehicle Pass Terms' : 'Data Privacy Policy'}
            </div>
            <div className="policy-card-meta">
              {editing ? (
                <span className="policy-updated">Editing. Changes go live for everyone when you save.</span>
              ) : (
                <>
                  {(stored?.updated_at || isAdmin) && (
                    <span className="policy-updated">
                      {stored?.updated_at
                        ? <>Last updated {formatDate(stored.updated_at)}{isAdmin && stored.updated_by ? ` by ${stored.updated_by}` : ''}</>
                        : 'Original wording'}
                    </span>
                  )}
                  {isAdmin && (
                    <button className="policy-edit-btn" onClick={startEditing} disabled={!loaded}>
                      <Pencil size={14} />
                      Edit
                    </button>
                  )}
                </>
              )}
            </div>
          </div>

          {editing ? (
            <div className="policy-editor">
              <div className="policy-editor-modes" role="tablist">
                <button role="tab" aria-selected={!preview} className={!preview ? 'active' : ''} onClick={() => setPreview(false)}>
                  Write
                </button>
                <button role="tab" aria-selected={preview} className={preview ? 'active' : ''} onClick={() => setPreview(true)}>
                  Preview
                </button>
              </div>

              {preview ? (
                <div className="policy-content policy-editor-preview">
                  <PolicyMarkdown source={draft} values={feeValues} />
                </div>
              ) : (
                <>
                  <textarea
                    className="policy-editor-input"
                    value={draft}
                    onChange={e => setDraft(e.target.value)}
                    spellCheck
                    aria-label={`${TABS.find(t => t.id === activeTab).label} text`}
                  />
                  <details className="policy-editor-help">
                    <summary>Formatting</summary>
                    <dl>
                      {MARKDOWN_HELP.map(([code, meaning]) => (
                        <div key={code}><dt><code>{code}</code></dt><dd>{meaning}</dd></div>
                      ))}
                    </dl>
                  </details>
                </>
              )}

              <div className="policy-editor-actions">
                {stored?.content != null && (
                  <button className="policy-editor-btn danger" onClick={restoreDefault} disabled={saving}>
                    <RotateCcw size={14} />
                    Restore default
                  </button>
                )}
                <span className="policy-editor-spacer" />
                <button className="policy-editor-btn" onClick={cancelEditing} disabled={saving}>
                  Cancel
                </button>
                <button className="policy-editor-btn primary" onClick={save} disabled={saving || !dirty}>
                  <Save size={14} />
                  {saving ? 'Saving…' : 'Save'}
                </button>
              </div>
            </div>
          ) : (
            <div className="policy-content">
              <PolicyMarkdown source={source} values={feeValues} />
            </div>
          )}
        </div>

        {/* Back  */}
        <div className="policy-footer">
          <button className="policy-footer-btn" onClick={goBack}>
            <ArrowLeft size={15} />
            Return
          </button>
          <span className="policy-footer-note">
            &copy; {new Date().getFullYear()} Saint Louis College. All rights reserved.
          </span>
        </div>
      </main>
    </div>
  )
}
