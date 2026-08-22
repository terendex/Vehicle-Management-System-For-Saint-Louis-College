import { useState } from 'react'
import {
  AlertTriangle, Check, Circle, Eye, EyeOff, KeyRound, LogOut,
} from 'lucide-react'
import { usersApi } from '../../api/users'
import useAuthStore from '../../stores/authStore'
import { PW_RULES, pwStrength, STRENGTH_LABELS } from '../../utils/passwordRules'
import notify from '../Feedback/notify'
import { fieldProblems } from '../Feedback/formProblems'
import './ChangePasswordModal.css'

/**
 * "Set a new password" card, in two modes.
 *
 * `forced` is the first login after an account is created from User Management:
 * the account carries a temporary password that was emailed to it, the card
 * cannot be dismissed, and the only way past it is a new password or Log Out.
 * Without `forced` it is a voluntary change and offers Cancel instead.
 *
 * A 2FA step-up, where the account owes one, is handled entirely by the axios
 * interceptor — this form issues the same request either way.
 */
export default function ChangePasswordModal({
  forced = false,
  subtitle,
  onCancel,
  onDone,
}) {
  const { clearMustChangePassword } = useAuthStore()

  const [form, setForm]             = useState({ current: '', new: '', confirm: '' })
  const [show, setShow]             = useState({ current: false, new: false, confirm: false })
  const [submitting, setSubmitting] = useState(false)

  const strength = pwStrength(form.new)
  const toggle   = (field) => setShow(s => ({ ...s, [field]: !s[field] }))

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (submitting) return
    // The form carries noValidate, so the browser's own bubble is gone and
    // its complaints have to be re-raised here.
    if (await notify.validation(fieldProblems(e.currentTarget))) return

    // Checked here rather than by greying out the submit button: a button that
    // will not press tells you it refuses but never tells you why.
    const problems = []
    if (!form.current) problems.push('Enter your current password.')
    PW_RULES.forEach((rule) => {
      if (!rule.test(form.new)) problems.push(`New password: ${rule.label.toLowerCase()}.`)
    })
    if (!form.confirm) problems.push('Re-enter the new password to confirm it.')
    else if (form.new !== form.confirm) problems.push('The two new passwords do not match.')
    if (await notify.validation(problems, { title: 'Password not accepted' })) return

    setSubmitting(true)
    try {
      await usersApi.changePassword(form.current, form.new, form.confirm)
      clearMustChangePassword()
      setForm({ current: '', new: '', confirm: '' })
      await notify.success(
        'For your security you are being signed out. Please log in again with your new password.',
        { title: 'Password Changed' },
      )
      // A fresh login with the new password, rather than carrying on in a
      // session that was opened with the old one.
      onDone?.()
    } catch (err) {
      if (err.stepUpCancelled) {
        notify.error('Verification cancelled — your password was not changed.', {
          title: 'Not changed',
        })
      } else {
        const data = err.response?.data
        if (data?.errors) {
          notify.error('The password was rejected:', {
            title: 'Password not accepted',
            details: data.errors,
          })
        } else {
          notify.error(data?.error || data?.detail || 'Failed to change password.', {
            title: 'Password not changed',
          })
        }
      }
      setSubmitting(false)
    }
  }

  return (
    <div className="cpw-overlay">
      <div className="cpw-modal">
        <div className={`cpw-icon ${forced ? 'warn' : ''}`}>
          {forced ? <AlertTriangle size={26} /> : <KeyRound size={24} />}
        </div>
        <h2 className="cpw-title">Change Your Password</h2>
        <p className="cpw-subtitle">
          {subtitle || (forced
            ? 'You are using a temporary password. Set a new one to continue.'
            : 'Update your account password below.')}
        </p>

        <form onSubmit={handleSubmit} className="cpw-form" noValidate>
          <div className="cpw-group">
            <label htmlFor="cpw-current">
              {forced ? 'Current (Temporary) Password' : 'Current Password'}
            </label>
            <div className="cpw-wrap">
              <input
                id="cpw-current"
                type={show.current ? 'text' : 'password'}
                value={form.current}
                onChange={e => setForm({ ...form, current: e.target.value })}
                placeholder={forced ? 'Enter temporary password' : 'Enter current password'}
                autoComplete="current-password"
                required
              />
              <button type="button" className="cpw-eye" onClick={() => toggle('current')}
                      aria-label={show.current ? 'Hide password' : 'Show password'}>
                {show.current ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
          </div>

          <div className="cpw-group">
            <label htmlFor="cpw-new">New Password</label>
            <div className="cpw-wrap">
              <input
                id="cpw-new"
                type={show.new ? 'text' : 'password'}
                value={form.new}
                onChange={e => setForm({ ...form, new: e.target.value })}
                placeholder="Enter new password"
                autoComplete="new-password"
                required
              />
              <button type="button" className="cpw-eye" onClick={() => toggle('new')}
                      aria-label={show.new ? 'Hide password' : 'Show password'}>
                {show.new ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
            {/* The rules appear once there is something to measure, so the card
                opens calm instead of showing five unmet requirements. */}
            {form.new && (
              <div className="cpw-strength">
                <div className="cpw-strength-track">
                  <div className={`cpw-strength-fill ${strength.level}`}
                       style={{ width: `${strength.score * 20}%` }} />
                </div>
                <span className={`cpw-strength-label ${strength.level}`}>
                  {STRENGTH_LABELS[strength.level]}
                </span>
                <div className="cpw-rules">
                  {PW_RULES.map(rule => {
                    const met = rule.test(form.new)
                    return (
                      <div key={rule.key} className={`cpw-rule ${met ? 'met' : ''}`}>
                        {met ? <Check size={12} /> : <Circle size={12} />}
                        {rule.label}
                      </div>
                    )
                  })}
                </div>
              </div>
            )}
          </div>

          <div className="cpw-group">
            <label htmlFor="cpw-confirm">Confirm New Password</label>
            <div className="cpw-wrap">
              <input
                id="cpw-confirm"
                type={show.confirm ? 'text' : 'password'}
                value={form.confirm}
                onChange={e => setForm({ ...form, confirm: e.target.value })}
                placeholder="Re-enter new password"
                autoComplete="new-password"
                required
              />
              <button type="button" className="cpw-eye" onClick={() => toggle('confirm')}
                      aria-label={show.confirm ? 'Hide password' : 'Show password'}>
                {show.confirm ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
          </div>

          <div className="cpw-actions">
            {forced ? (
              <button type="button" className="cpw-btn-logout" onClick={() => onCancel?.()}>
                <LogOut size={15} /> Log Out
              </button>
            ) : (
              <button type="button" className="cpw-btn-outline" onClick={() => onCancel?.()}>
                Cancel
              </button>
            )}
            <button
              type="submit"
              className="cpw-btn-primary"
              disabled={submitting}
            >
              <KeyRound size={15} />
              {submitting ? 'Saving…' : 'Set New Password'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
