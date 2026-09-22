import { useState, useEffect } from 'react'
import { QRCodeSVG } from 'qrcode.react'
import { useLiveUpdates } from '../../realtime/useLiveUpdates'
import {
  User, Car, KeyRound, ShieldCheck, Eye, EyeOff, Check,
  Circle, AlertTriangle, Copy, LogOut, RefreshCw, AlertCircle,
  ParkingCircle, Bike, Loader2, Megaphone, X, Maximize2, CalendarDays,
  Pencil, Clock3, Hourglass
} from 'lucide-react'
import useAuthStore from '../../stores/authStore'
import SecurityPanel from '../../components/TwoFactor/SecurityPanel'
import useTwofaStore from '../../stores/twofaStore'
import { usersApi } from '../../api/users'
import notify from '../../components/Feedback/notify'
import { fieldProblems } from '../../components/Feedback/formProblems'
import { violationsApi } from '../../api/violations'
import { registrationApi } from '../../api/registration'
import DetailFields from '../../components/RegistrationDetails/DetailFields'
import {
  changedValues, detailFormProblems,
} from '../../components/RegistrationDetails/detailRules'
import ChangeDiff from '../../components/RegistrationDetails/ChangeDiff'
import '../../components/RegistrationDetails/detailFields.css'
import { getNotices } from '../../api/vehicles'
import './OwnerDashboard.css'
import { PW_RULES, pwStrength, STRENGTH_LABELS } from '../../utils/passwordRules'
import { isControlNumber, vehicleQrPayload } from '../../utils/plateFormat'

/* What each schedule code admits, spelled out — a bare 'ANY' told the owner
   nothing, and "any day" would overstate it (the campus is closed on Sunday). */
const SCHEDULE_LABELS = {
  MWF:   'Mon · Wed · Fri',
  TTHF:  'Tue · Thu · Fri',
  TTHS:  'Tue · Thu · Sat',   // pre-rename rotation
  MIXED: 'Mon – Sat',
  ANY:   'Mon – Sat',
  ALL:   'Mon – Sat',
}


const VIOLATION_TYPE_LABELS = {
  unauthorized_entry:   'Unauthorized Entry',
  double_parking:       'Double Parking',
  time_exceed:          'Time Exceed',
  no_sticker:           'No Sticker',
  expired_registration: 'Expired Registration',
  unauthorized:         'Unauthorized (Legacy)',
  other:                'Other',
}

const OFFENSE_LABELS = { 1: '1st offense', 2: '2nd offense', 3: '3rd offense' }

const MOTORCYCLE_TYPES = ['Motorcycle', 'motorcycle']

// How often the parking card re-reads availability while the tab is visible.
// Bays themselves take ~5s to claim, so polling much faster than this only
// costs requests without making the card any more current.
const PARKING_POLL_MS = 10_000
const isMotorcycle = (vtype) => MOTORCYCLE_TYPES.some(m => vtype?.toLowerCase().includes(m.toLowerCase()))

export default function OwnerDashboard() {
  const { user, logout, clearMustChangePassword, syncDisplayName } = useAuthStore()
  const [securityModal, setSecurityModal] = useState(false)
  const ensureStepUp = useTwofaStore((s) => s.ensureStepUp)

  /** Prove it's you, then open the form — not the other way round.
   *
   *  The server would ask anyway when the form is submitted, but being stopped
   *  after typing a password three times is a poor way to find out. Cancelling
   *  the prompt simply leaves the modal closed.
   *
   *  The forced first-time change is exempt: that user enrolled seconds ago and
   *  is already carrying a step-up, so `ensureStepUp` returns it without a
   *  prompt — and if it ever did prompt, they have nowhere else to go. */
  const openPasswordModal = async () => {
    try {
      await ensureStepUp('Confirm it’s you before changing your password.')
      setPwModal(true)
    } catch {
      /* prompt dismissed — leave the form closed */
    }
  }

  /* ── registration data ── */
  const [reg, setReg] = useState(null)
  const [loading, setLoading] = useState(true)
  const [fetchError, setFetchError] = useState(null)

  /* ── violations ── */
  const [violations, setViolations] = useState([])
  const [violationsLoading, setViolationsLoading] = useState(false)
  const [violationsError, setViolationsError] = useState(null)

  /* ── parking availability ── */
  const [parking, setParking] = useState(null)  // { spaces, summary, zones }
  const [parkingLoading, setParkingLoading] = useState(false)
  const [parkingError, setParkingError] = useState(null)
  const [parkingCategory, setParkingCategory] = useState(null)

  /* ── notices ── */
  const [notices, setNotices] = useState([])
  const [noticesLoading, setNoticesLoading] = useState(false)

  /* ── password change modal ── */
  const mustChange = user?.must_change_password === true
  const [pwModal, setPwModal] = useState(mustChange)
  const [pwForm, setPwForm] = useState({ current: '', new: '', confirm: '' })
  const [showCurrent, setShowCurrent] = useState(false)
  const [showNew, setShowNew] = useState(false)
  const [showConfirm, setShowConfirm] = useState(false)
  const [pwSubmitting, setPwSubmitting] = useState(false)

  /* ── qr copy ── */
  const [qrCopied, setQrCopied] = useState(false)

  /* ── fullscreen qr ──
     The card-sized code is hard for a guard to scan from a phone held at arm's
     length, so the owner can blow it up to fill the screen on a white sheet. */
  const [qrFull, setQrFull] = useState(false)

  /* Escape closes the fullscreen code, and the page behind it stays put so the
     owner does not lose their scroll position while the guard scans. */
  useEffect(() => {
    if (!qrFull) return
    const onKey = e => { if (e.key === 'Escape') setQrFull(false) }
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = prevOverflow
    }
  }, [qrFull])

  /* ── plate swap (conduction → real plate, one-time) ── */
  const [swapOpen, setSwapOpen]           = useState(false)
  const [swapValue, setSwapValue]         = useState('')
  const [swapSubmitting, setSwapSubmitting] = useState(false)
  const [swapSuccess, setSwapSuccess]     = useState(false)

  const isConductionOnly = !!reg && !!reg.conduction_number && !reg.plate_number

  const handlePlateSwap = async () => {
    const plate = swapValue.trim().toUpperCase()
    if (!plate) {
      await notify.error('Enter your new plate number.', { title: 'Plate not saved' })
      return
    }
    setSwapSubmitting(true)
    try {
      await usersApi.swapPlate(plate)
      setSwapOpen(false)
      setSwapSuccess(true)
      setSwapValue('')
      await fetchReg()  // reflect the new plate + hide the option
    } catch (err) {
      notify.error(err.response?.data?.plate_number || err.response?.data?.error
        || 'Could not update your plate. Please try again.', { title: 'Plate not saved' })
    } finally {
      setSwapSubmitting(false)
    }
  }

  /* ── Approval-gated detail changes ──
     An approved registration is not the owner's to rewrite: it issued their
     pass, their gate QR, a Vehicle row a guard resolves against, and this
     account. So an edit here is a *request* — CDSO approves it and only then
     does anything move. (An application still awaiting review is different:
     the applicant edits it directly from the link in their email.)

     `changeInfo` is the server's answer to "what may this owner edit, and what
     have they asked for already" — the whitelist filtered for their registrant
     type, plus their request history. Driving the form off it rather than
     hard-coding fields is what keeps this modal and the public correction page
     showing the same thing. */
  const [changeInfo, setChangeInfo]       = useState(null)
  const [changeModal, setChangeModal]     = useState(false)
  const [changeValues, setChangeValues]   = useState({})
  const [changeErrors, setChangeErrors]   = useState({})
  const [changeSubmitting, setChangeSubmitting] = useState(false)
  const [changeFiled, setChangeFiled]     = useState(null)   // the just-filed request

  // The one request that is still waiting. At most one can exist — the server
  // refuses a second, because approving stacked requests in any order would
  // leave the row holding whichever was decided last rather than what the
  // reviewer read.
  const pendingChange = changeInfo?.requests?.find(r => r.status === 'pending') || null

  // The most recent decision, so a declined request does not simply vanish.
  // `requests` is newest-first from the server, and a withdrawn one is the
  // owner's own doing — they do not need telling about it.
  const lastDecidedChange = changeInfo?.requests
    ?.find(r => r.status === 'approved' || r.status === 'rejected') || null

  // Only what actually differs from what is on file, so the reviewer reads the
  // change rather than every field the owner happened to open.
  const changeDiff = changedValues(changeValues, changeInfo?.values || {})
  const hasChangeEdits = Object.keys(changeDiff).length > 0

  const fetchChangeInfo = async () => {
    try {
      setChangeInfo(await registrationApi.getMyChangeRequests())
    } catch {
      // Non-critical: the dashboard's own data is already on screen, and the
      // button simply stays hidden rather than the page failing.
    }
  }

  const openChangeModal = () => {
    setChangeValues(changeInfo?.values || {})
    setChangeErrors({})
    setChangeModal(true)
  }

  const handleChangeField = (field, value) => {
    setChangeValues(prev => ({ ...prev, [field]: value }))
    // A message about the previous value is worse than none — it describes text
    // that is no longer on screen.
    setChangeErrors(prev => (prev[field] ? { ...prev, [field]: null } : prev))
  }

  const submitChangeRequest = async (event) => {
    event.preventDefault()
    if (!hasChangeEdits) return

    const problems = detailFormProblems(
      changeValues, changeInfo?.values || {}, changeInfo?.editable || [])
    if (await notify.validation(problems, { title: 'Check your details' })) return

    setChangeSubmitting(true)
    try {
      const filed = await registrationApi.requestDetailChange(changeDiff)
      setChangeModal(false)
      setChangeFiled(filed)
      await fetchChangeInfo()
    } catch (err) {
      const fieldErrors = err.response?.data?.errors
      if (fieldErrors) {
        setChangeErrors(fieldErrors)
        await notify.error(
          'Some of your details could not be submitted — see the notes on each field.',
          { title: 'Change not submitted' })
      } else {
        await notify.error(err.response?.data?.error
          || 'Could not submit your change. Please try again.',
          { title: 'Change not submitted' })
      }
    } finally {
      setChangeSubmitting(false)
    }
  }

  const cancelChangeRequest = async (id) => {
    // Asked first: withdrawing is reversible (they can file a corrected request
    // immediately) but it does take the request out of the CDSO queue, and the
    // button sits right under the diff someone may only be reading.
    if (!(await notify.confirm({
      title: 'Withdraw this request?',
      message: 'Your request will be taken out of the CDSO queue.',
      description: 'Your registration details stay as they are now. You can file a '
                 + 'corrected request straight afterwards.',
      confirmLabel: 'Withdraw it',
      danger: true,
    }))) return
    try {
      await registrationApi.cancelMyChangeRequest(id)
      await fetchChangeInfo()
      await notify.success('Your pending change was withdrawn.',
        { title: 'Change withdrawn' })
    } catch (err) {
      await notify.error(err.response?.data?.error
        || 'Could not withdraw your change. Please try again.',
        { title: 'Change not withdrawn' })
    }
  }

  useEffect(() => {
    fetchReg()
    fetchViolations()
    fetchNotices()
    fetchChangeInfo()
  }, [])

  // Live-refresh when the owner's registration, violations or notices change
  useLiveUpdates(
    () => { fetchReg(); fetchViolations(); fetchNotices(); fetchChangeInfo() },
    ['vehicleregistration', 'violation', 'parkingnotice', 'vehicle',
     // A CDSO decision on a change request has to land here without a reload:
     // the owner is often watching this page waiting for exactly that.
     'registrationchangerequest'],
  )

  const fetchNotices = async () => {
    setNoticesLoading(true)
    try {
      const { data } = await getNotices()
      setNotices(data)
    } catch {
      // non-critical, fail silently
    } finally {
      setNoticesLoading(false)
    }
  }

  const fetchReg = async () => {
    setLoading(true)
    setFetchError(null)
    try {
      const data = await usersApi.getMyRegistration()
      setReg(data)
      // An approved name change moves the record but not the login claim the
      // greeting reads, so the two would disagree on the same screen.
      syncDisplayName(data?.full_name)
      // Determine parking category from vehicle type
      const cat = isMotorcycle(data?.vehicle_type) ? 'motorcycle' : 'car'
      setParkingCategory(cat)
      fetchParking(cat)
    } catch (err) {
      setFetchError(err.response?.data?.error || 'Failed to load registration data.')
    } finally {
      setLoading(false)
    }
  }

  const fetchViolations = async () => {
    setViolationsLoading(true)
    setViolationsError(null)
    try {
      const data = await violationsApi.getMyViolations()
      setViolations(data)
    } catch (err) {
      setViolationsError('Could not load violations.')
    } finally {
      setViolationsLoading(false)
    }
  }

  // `silent` refreshes in place: a live update must not blank the card to a
  // spinner every time a car parks, or a busy lot would never stop flashing.
  const fetchParking = async (category, { silent = false } = {}) => {
    if (!silent) {
      setParkingLoading(true)
      setParkingError(null)
    }
    try {
      const data = await registrationApi.getParkingAvailability(category)
      setParking(data)
      setParkingError(null)
    } catch {
      if (!silent) setParkingError('Could not load parking availability.')
    } finally {
      if (!silent) setParkingLoading(false)
    }
  }

  // The parking card follows the lots themselves: a bay flipping (parkingspace),
  // a zone's setup or capacity changing (parkingzone), a gate scan moving the
  // on-campus count (accesslog), or an event holding spaces back (event).
  useLiveUpdates(
    () => { if (parkingCategory) fetchParking(parkingCategory, { silent: true }) },
    ['parkingspace', 'parkingzone', 'accesslog', 'event'],
    { debounce: 1000 },
  )

  // Owners use the Railway site, while bays are scored by the campus machine.
  // A live update only reaches browsers connected to the server that made the
  // change, so without a Redis shared by both (REDIS_URL) the push above never
  // arrives here. This poll is what keeps the card current regardless: every
  // PARKING_POLL_MS while the tab is visible, nothing while it is hidden, and
  // straight away on coming back to the tab. The endpoint is a flat four
  // queries, and a hidden tab costs nothing.
  useEffect(() => {
    if (!parkingCategory) return
    const refresh = () => {
      if (document.visibilityState !== 'visible') return
      fetchParking(parkingCategory, { silent: true })
    }
    const timer = setInterval(refresh, PARKING_POLL_MS)
    document.addEventListener('visibilitychange', refresh)
    return () => {
      clearInterval(timer)
      document.removeEventListener('visibilitychange', refresh)
    }
  }, [parkingCategory])

  /* ── password change ── */
  const handlePwChange = async (e) => {
    e.preventDefault()
    // The form carries noValidate, so the browser's own bubble is gone and
    // its complaints have to be re-raised here.
    if (await notify.validation(fieldProblems(e.currentTarget))) return

    // Said outright rather than left to a dead submit button and a line of red
    // under one field.
    const problems = []
    if (!pwForm.current) problems.push('Enter your current password.')
    PW_RULES.forEach((rule) => {
      if (!rule.test(pwForm.new)) problems.push(`New password: ${rule.label.toLowerCase()}.`)
    })
    if (!pwForm.confirm) problems.push('Re-enter the new password to confirm it.')
    else if (pwForm.new !== pwForm.confirm) problems.push('The two new passwords do not match.')
    if (await notify.validation(problems, { title: 'Password not accepted' })) return

    setPwSubmitting(true)
    try {
      await usersApi.changePassword(pwForm.current, pwForm.new, pwForm.confirm)
      clearMustChangePassword()
      setPwForm({ current: '', new: '', confirm: '' })
      await notify.success(
        "For your security you're being signed out. Please log in again with your new password.",
        { title: 'Password Changed' },
      )
      // Force a fresh login with the new password instead of keeping the old session active.
      // logout() handles the redirect itself, so hand it the destination.
      logout('/login?passwordChanged=1')
    } catch (err) {
      const data = err.response?.data
      if (data?.errors) {
        notify.error('The password was rejected:', {
          title: 'Password not accepted',
          details: data.errors,
        })
      } else {
        notify.error(data?.error || 'Failed to change password.', {
          title: 'Password not changed',
        })
      }
    } finally {
      setPwSubmitting(false)
    }
  }

  const systemId = reg?.system_student_id || reg?.system_employee_id || '—'
  // An e-bike's FM- control number lives in plate_number, so its QR is the same
  // VEHICLE:{identifier}|ID:{id} shape the guard scanner already reads. An owner
  // still on a conduction sticker has no plate_number yet, so the payload is
  // built from whichever identifier the record actually carries.
  const qrPayload = vehicleQrPayload(reg)
  const hasControlNumber = isControlNumber(reg?.plate_number)
  const strength  = pwStrength(pwForm.new)

  const handleLogout = () => {
    logout()
    window.location.href = '/login'
  }

  /* ── Parking grid helpers ── */
  const parkingSummary = parking?.summary?.[parkingCategory] || null
  // The event holding bays back right now, if any. Named on the page rather
  // than left implicit: without it the free count simply drops and reads as a
  // miscount, and the driver sets off for a space that was never going to be
  // there.
  const parkingEvent   = parking?.event || null
  const parkingSpaces  = (parking?.spaces || []).filter(s => s.vehicle_category === parkingCategory)
  const parkingZones   = (parking?.zones || []).filter(z => z.category === parkingCategory)

  return (
    <>
      {/* Force password change modal */}
      {/* Plate-swap confirm modal */}
      {swapOpen && (
        <div className="od-modal-overlay" onClick={() => !swapSubmitting && setSwapOpen(false)}>
          <div className="od-modal" onClick={e => e.stopPropagation()}>
            <div className="od-modal-icon"><Car size={26} /></div>
            <h2 className="od-modal-title">Enter Your Plate Number</h2>
            <p className="od-modal-subtitle">
              This replaces your conduction number <strong>{reg?.conduction_number}</strong> with your
              official plate. It can only be done once, so please double-check it.
            </p>
            <input
              className="od-swap-input"
              type="text"
              value={swapValue}
              onChange={e => setSwapValue(e.target.value.toUpperCase())}
              placeholder="e.g. AAA 0000"
              autoFocus
            />
            <div className="od-modal-actions">
              <button className="od-btn-ghost" onClick={() => setSwapOpen(false)} disabled={swapSubmitting}>Cancel</button>
              <button className="od-btn-primary" onClick={handlePlateSwap} disabled={swapSubmitting}>
                {swapSubmitting ? 'Saving…' : 'Confirm & Save'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Detail-change request form. Wide, because it holds a field grid
          rather than the one input the confirm-style dialogs were sized for. */}
      {changeModal && (
        <div className="od-modal-overlay" onClick={() => !changeSubmitting && setChangeModal(false)}>
          <div className="od-modal od-modal-wide" onClick={e => e.stopPropagation()}>
            <div className="od-modal-icon"><Pencil size={26} /></div>
            <h2 className="od-modal-title">Request a Detail Change</h2>
            <p className="od-modal-subtitle">
              Your registration already issued your vehicle pass and gate QR, so a change
              needs <strong>CDSO approval</strong> before it takes effect. Edit what is wrong
              below and submit it for review — nothing changes until they approve it.
            </p>

            <form noValidate onSubmit={submitChangeRequest} className="od-change-form">
              <DetailFields
                editable={changeInfo?.editable || []}
                values={changeValues}
                onChange={handleChangeField}
                errors={changeErrors}
                disabled={changeSubmitting}
                idPrefix="odc"
              />

              {hasChangeEdits && (
                <div className="od-change-preview">
                  <p className="od-change-preview-heading">
                    What you are asking CDSO to change
                  </p>
                  <ChangeDiff changes={changeInfo.editable
                    .filter(f => f.field in changeDiff)
                    .map(f => ({
                      field: f.field,
                      label: f.label,
                      old:   changeInfo.values?.[f.field] || '',
                      new:   changeDiff[f.field],
                    }))} />
                </div>
              )}

              <p className="od-change-locked">
                Your <strong>email address</strong>, <strong>registrant type</strong> and
                <strong> campus schedule</strong> cannot be changed here — the CDSO Office
                assigns those. Ask them directly.
              </p>

              <div className="od-modal-actions">
                <button
                  type="button"
                  className="od-btn-ghost"
                  onClick={() => setChangeModal(false)}
                  disabled={changeSubmitting}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="od-btn-primary"
                  disabled={changeSubmitting || !hasChangeEdits}
                >
                  {changeSubmitting ? 'Submitting…'
                    : hasChangeEdits ? 'Submit for CDSO approval'
                    : 'Change something first'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Filed confirmation. Its own modal rather than a toast: "this is not
          applied yet" is the single most important thing to land, and a
          message that disappears on its own is the wrong way to say it. */}
      {changeFiled && (
        <div className="od-modal-overlay" onClick={() => setChangeFiled(null)}>
          <div className="od-modal" onClick={e => e.stopPropagation()}>
            <div className="od-pw-success">
              <div className="od-pw-success-icon"><Hourglass size={36} /></div>
              <h3>Sent for CDSO Approval</h3>
              <p>
                Your request is with the CDSO Office. <strong>Nothing has changed yet</strong> —
                your pass and QR code still show your current details, and we will email you
                as soon as a decision is made.
              </p>
              <button
                className="od-btn-primary"
                style={{ marginTop: 14 }}
                onClick={() => setChangeFiled(null)}
              >
                Got it
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Fullscreen QR — a white sheet edge to edge so the guard's scanner gets
          the biggest, brightest target the phone can show. */}
      {qrFull && qrPayload && (
        <div
          className="od-qrfs"
          role="dialog"
          aria-modal="true"
          aria-label="Vehicle access QR code, fullscreen"
          onClick={() => setQrFull(false)}
        >
          <button className="od-qrfs-close" onClick={() => setQrFull(false)} aria-label="Close fullscreen code">
            <X size={22} />
          </button>

          <div className="od-qrfs-inner" onClick={e => e.stopPropagation()}>
            <span className="od-qrfs-plate">{reg?.plate_number || reg?.conduction_number || 'Vehicle Pass'}</span>
            <span className="od-qrfs-sub">Present this code to security personnel</span>

            <div className="od-qrfs-code">
              <QRCodeSVG value={qrPayload} size={640} level="H" includeMargin={true} />
            </div>

            <code className="od-qrfs-data">{qrPayload}</code>
            <p className="od-qrfs-tip">Raise your screen brightness for a faster scan. Tap outside the code or press Esc to close.</p>
          </div>
        </div>
      )}

      {/* Plate-swap success modal */}
      {swapSuccess && (
        <div className="od-modal-overlay" onClick={() => setSwapSuccess(false)}>
          <div className="od-modal" onClick={e => e.stopPropagation()}>
            <div className="od-pw-success">
              <div className="od-pw-success-icon"><ShieldCheck size={36} /></div>
              <h3>Plate Number Saved!</h3>
              <p>Your vehicle is now verified with plate <strong>{reg?.plate_number}</strong>.
                 Your conduction number has been replaced.</p>
              <button className="od-btn-primary" style={{ marginTop: 14 }} onClick={() => setSwapSuccess(false)}>Done</button>
            </div>
          </div>
        </div>
      )}

      {pwModal && (
        <div className="od-modal-overlay">
          <div className="od-modal">
            <div className="od-modal-icon warn"><AlertTriangle size={26} /></div>
            <h2 className="od-modal-title">Change Your Password</h2>
            <p className="od-modal-subtitle">
              {mustChange
                ? 'You are using a temporary password. Please set a new password before continuing.'
                : 'Update your account password below.'}
            </p>

            <form noValidate onSubmit={handlePwChange} className="od-pw-form">
              <div className="od-form-group">
                <label>{mustChange ? 'Current (Temporary) Password' : 'Current Password'}</label>
                <div className="od-pw-wrap">
                  <input type={showCurrent ? 'text' : 'password'} value={pwForm.current} onChange={e => setPwForm({ ...pwForm, current: e.target.value })} placeholder="Enter current password" required autoComplete="current-password" />
                  <button type="button" className="od-pw-eye" onClick={() => setShowCurrent(v => !v)}>{showCurrent ? <EyeOff size={16} /> : <Eye size={16} />}</button>
                </div>
              </div>

              <div className="od-form-group">
                <label>New Password</label>
                <div className="od-pw-wrap">
                  <input type={showNew ? 'text' : 'password'} value={pwForm.new} onChange={e => setPwForm({ ...pwForm, new: e.target.value })} placeholder="Enter new password" required autoComplete="new-password" />
                  <button type="button" className="od-pw-eye" onClick={() => setShowNew(v => !v)}>{showNew ? <EyeOff size={16} /> : <Eye size={16} />}</button>
                </div>
                {pwForm.new && (
                  <div className="od-strength-wrap">
                    <div className="od-strength-bar-bg">
                      <div className={`od-strength-bar ${strength.level}`} style={{ width: `${strength.score * 20}%` }} />
                    </div>
                    <span className={`od-strength-label ${strength.level}`}>{STRENGTH_LABELS[strength.level]}</span>
                    <div className="od-pw-rules">
                      {PW_RULES.map(rule => (
                        <div key={rule.key} className={`od-pw-rule ${rule.test(pwForm.new) ? 'met' : ''}`}>
                          {rule.test(pwForm.new) ? <Check size={12} /> : <Circle size={12} />}
                          {rule.label}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>

              <div className="od-form-group">
                <label>Confirm New Password</label>
                <div className="od-pw-wrap">
                  <input type={showConfirm ? 'text' : 'password'} value={pwForm.confirm} onChange={e => setPwForm({ ...pwForm, confirm: e.target.value })} placeholder="Re-enter new password" required autoComplete="new-password" />
                  <button type="button" className="od-pw-eye" onClick={() => setShowConfirm(v => !v)}>{showConfirm ? <EyeOff size={16} /> : <Eye size={16} />}</button>
                </div>
              </div>


              <div className="od-pw-actions">
                {!mustChange && <button type="button" className="od-btn-outline" onClick={() => setPwModal(false)}>Cancel</button>}
                {mustChange && <button type="button" className="od-btn-logout" onClick={handleLogout}><LogOut size={15} /> Log Out</button>}
                <button type="submit" className="od-btn-primary" disabled={pwSubmitting}>
                  {pwSubmitting ? 'Saving…' : <><KeyRound size={15} /> Set New Password</>}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* ── Security (two-factor) ── */}
      {securityModal && (
        <div className="od-modal-overlay" onClick={() => setSecurityModal(false)}>
          <div className="od-modal od-modal-wide" onClick={e => e.stopPropagation()}>
            <h2 className="od-modal-title">Account Security</h2>
            <p className="od-modal-subtitle">
              Manage your authenticator app and backup codes.
            </p>
            <SecurityPanel compact />
            <div className="od-modal-actions" style={{ marginTop: 18 }}>
              <button className="od-btn-ghost" onClick={() => setSecurityModal(false)}>Close</button>
            </div>
          </div>
        </div>
      )}

      {/* ── Main dashboard ── */}
      <div className="od-page">

        {/* Welcome Banner */}
        <div className="od-welcome-banner">
          <div className="od-welcome-avatar">
            {user?.full_name?.charAt(0)?.toUpperCase() || 'V'}
          </div>
          <div className="od-welcome-text">
            <h1>Welcome, {user?.full_name || 'Vehicle Owner'}!</h1>
            <p>Here is your registration summary and vehicle access details.</p>
          </div>
          <div className="od-welcome-actions">
            <button className="od-change-pw-btn" onClick={() => setSecurityModal(true)} title="Two-factor authentication and backup codes">
              <ShieldCheck size={15} />
              Security
            </button>
            <button className="od-change-pw-btn" onClick={openPasswordModal} title="Change Password">
              <KeyRound size={15} />
              Change Password
            </button>
          </div>
        </div>

        {/* ID Cards */}
        <div className="od-id-cards">
          <div className="od-id-card portal">
            <div className="od-id-card-label">Portal Account ID</div>
            <div className="od-id-card-value">{user?.user_code || '—'}</div>
            <div className="od-id-card-sub">Vehicle Owner Account</div>
          </div>
          <div className="od-id-card system">
            <div className="od-id-card-label">System Registration ID</div>
            <div className="od-id-card-value">{systemId}</div>
            <div className="od-id-card-sub">
              {reg?.registrant_type === 'student' ? 'Student Registration' : reg?.registrant_type === 'employee' ? 'Employee Registration' : 'Registration'}
            </div>
          </div>
        </div>

        {loading ? (
          <div className="od-loading"><div className="od-spinner" /><p>Loading your registration details…</p></div>
        ) : fetchError ? (
          <div className="od-error-card">
            <AlertTriangle size={24} />
            <p>{fetchError}</p>
            <button className="od-btn-primary" onClick={fetchReg}><RefreshCw size={14} /> Retry</button>
          </div>
        ) : reg && (
          <>
            {/* ── Main info + QR grid ── */}
            <div className="od-grid">

              {/* Left: Personal + Vehicle Info */}
              <div className="od-info-col">
                <div className="od-card">
                  <div className="od-card-head"><User size={16} /> Personal Information</div>
                  <div className="od-details-grid">
                    <div className="od-detail"><span className="od-detail-label">Full Name</span><span className="od-detail-val">{reg.full_name}</span></div>
                    <div className="od-detail"><span className="od-detail-label">Email</span><span className="od-detail-val">{reg.email}</span></div>
                    <div className="od-detail"><span className="od-detail-label">Type</span><span className="od-detail-val od-capitalize">{reg.registrant_type}</span></div>
                    {/* DPO: Student/Employee ID, contact
                        number, age and address are not collected any more, so
                        the rows that showed them are gone. */}
                    {reg.registrant_type === 'student' ? (
                      <div className="od-detail" style={{ gridColumn: 'span 2' }}><span className="od-detail-label">Program &amp; Year</span><span className="od-detail-val">{reg.program_year || '—'}</span></div>
                    ) : reg.registrant_type === 'employee' ? (
                      <div className="od-detail" style={{ gridColumn: 'span 2' }}><span className="od-detail-label">Department</span><span className="od-detail-val">{reg.department || '—'}</span></div>
                    ) : null}
                    <div className="od-detail" style={{ gridColumn: 'span 2' }}><span className="od-detail-label">Driver's License</span><span className="od-detail-val">{reg.drivers_license || '—'}</span></div>
                    {(reg.campus_days?.length > 0 || reg.schedule) && (
                      <div className="od-detail" style={{ gridColumn: 'span 2' }}>
                        <span className="od-detail-label">Assigned Schedule</span>
                        {reg.campus_days?.length > 0 ? (
                          <div className="od-day-badges">
                            {reg.campus_days.map(d => (
                              <span key={d} className="od-day-badge">{d}</span>
                            ))}
                          </div>
                        ) : (
                          <div className="od-day-badges">
                            {/* No stored days — an employee or fetcher pass.
                                Showing the bare code ('ANY') told the owner
                                nothing about which days they may come in. */}
                            <span className="od-day-badge">{SCHEDULE_LABELS[reg.schedule] || reg.schedule}</span>
                          </div>
                        )}
                      </div>
                    )}
                  </div>

                  {/* Correcting these details.

                      Put at the foot of the Personal Information card rather
                      than in the page header: this is where the owner is
                      looking when they notice the mistake, and a button up by
                      "Change Password" would not be read as being about any of
                      the rows above it.

                      The pending state replaces the button entirely. Offering
                      "request a change" while one is already queued invites a
                      second request the server will refuse with a 409, and
                      "what happened to the last one" is the actual question at
                      that moment. */}
                  {changeInfo && (
                    pendingChange ? (
                      <div className="od-change-pending">
                        <div className="od-change-pending-head">
                          <Clock3 size={15} />
                          <span>Waiting for CDSO approval</span>
                        </div>
                        <ChangeDiff changes={pendingChange.changes} />
                        <p className="od-change-pending-note">
                          Nothing on your registration changes until the CDSO approves this
                          &mdash; you will be emailed either way.
                        </p>
                        <button
                          type="button"
                          className="od-btn-ghost od-change-withdraw"
                          onClick={() => cancelChangeRequest(pendingChange.id)}
                        >
                          Withdraw this request
                        </button>
                      </div>
                    ) : (
                      <div className="od-change-cta">
                        <p>
                          Spotted a mistake in your details or your vehicle? You can ask the
                          CDSO Office to correct it. Changes take effect once they approve them.
                        </p>
                        <button
                          type="button"
                          className="od-change-btn"
                          onClick={openChangeModal}
                        >
                          <Pencil size={14} /> Request a detail change
                        </button>
                      </div>
                    )
                  )}

                  {/* The last decision, so an owner who was declined is not
                      left wondering whether the request simply vanished. Only
                      shown while there is nothing pending — a fresh request is
                      the more relevant thing to look at. */}
                  {!pendingChange && lastDecidedChange && (
                    <div className={`od-change-decided od-change-decided--${lastDecidedChange.status}`}>
                      <div className="od-change-decided-head">
                        {lastDecidedChange.status === 'approved'
                          ? <><Check size={14} /> <span>Your last change was approved</span></>
                          : <><AlertCircle size={14} /> <span>Your last change was declined</span></>}
                      </div>
                      <ChangeDiff changes={lastDecidedChange.changes} />
                      {lastDecidedChange.decision_note && (
                        <p className="od-change-decided-note">
                          <strong>CDSO:</strong> {lastDecidedChange.decision_note}
                        </p>
                      )}
                    </div>
                  )}
                </div>

                <div className="od-card">
                  <div className="od-card-head"><Car size={16} /> Vehicle Information</div>
                  <div className="od-details-grid">
                    <div className="od-detail">
                      <span className="od-detail-label">{hasControlNumber ? 'Control Number' : 'Plate Number'}</span>
                      <span className="od-detail-val od-plate">{reg.plate_number || 'Not assigned yet'}</span>
                    </div>
                    <div className="od-detail"><span className="od-detail-label">Vehicle Type</span><span className="od-detail-val od-capitalize">{reg.vehicle_type}</span></div>
                    <div className="od-detail"><span className="od-detail-label">Color</span><span className="od-detail-val">{reg.vehicle_color || '—'}</span></div>
                    {/* An e-bike's control number stands in for both identifiers */}
                    {!hasControlNumber && (
                      <div className="od-detail"><span className="od-detail-label">Conduction Number</span><span className="od-detail-val">{reg.conduction_number || '—'}</span></div>
                    )}
                    {reg.body_number && (
                      <div className="od-detail" style={{ gridColumn: 'span 2' }}><span className="od-detail-label">Body Number</span><span className="od-detail-val">{reg.body_number}</span></div>
                    )}
                  </div>

                  {/* One-time: replace the conduction number with the real plate once received */}
                  {isConductionOnly && (
                    <div className="od-plate-swap-cta">
                      <p>Registered with a conduction number. Received your official plate? Enter it to
                         verify your vehicle — this replaces your conduction number and can only be done once.</p>
                      <button type="button" className="od-plate-swap-btn" onClick={() => setSwapOpen(true)}>
                        <Car size={14} /> I received my plate number
                      </button>
                    </div>
                  )}
                </div>
              </div>

              {/* Right: QR + Status */}
              <div className="od-qr-col">
                <div className="od-card od-qr-card">
                  <div className="od-card-head"><ShieldCheck size={16} /> Vehicle Access QR Code</div>
                  <p className="od-qr-hint">Present this code to security personnel upon entry.</p>
                  {/* No identifier on file means no QR a gate could resolve. Said
                      plainly, because a QR rendered from an empty payload looks
                      perfectly scannable and only fails at the gate. */}
                  {!qrPayload ? (
                    <p className="od-qr-hint">
                      Your record has no plate or conduction number on file yet, so no gate QR can be
                      issued. Please contact the CDSO office.
                    </p>
                  ) : (
                  <>
                  <button
                    type="button"
                    className="od-qr-display od-qr-display--btn"
                    onClick={() => setQrFull(true)}
                    title="Show this code fullscreen for scanning"
                  >
                    <QRCodeSVG value={qrPayload} size={200} level="H" includeMargin={true} />
                    <span className="od-qr-expand-hint"><Maximize2 size={11} /> Tap to enlarge</span>
                  </button>
                  <div className="od-qr-data-box">
                    <span className="od-qr-data-label">QR Data</span>
                    <code className="od-qr-data-code">{qrPayload}</code>
                  </div>
                  <button className="od-qr-full-btn" onClick={() => setQrFull(true)}>
                    <Maximize2 size={14} /> Show Fullscreen
                  </button>
                  <button
                    className="od-copy-btn"
                    onClick={async () => {
                      await navigator.clipboard.writeText(qrPayload)
                      setQrCopied(true)
                      setTimeout(() => setQrCopied(false), 2000)
                    }}
                  >
                    {qrCopied ? <><Check size={14} /> Copied!</> : <><Copy size={14} /> Copy QR Data</>}
                  </button>
                  </>
                  )}
                </div>

                <div className="od-card od-status-card">
                  <div className="od-card-head"><ShieldCheck size={16} /> Registration Status</div>
                  <div className="od-status-badge accepted"><Check size={16} /> Accepted &amp; Authorized</div>
                  <p className="od-status-note">
                    Your vehicle is authorized to enter the Saint Louis College campus premises.
                    Always carry your QR code for scanning at the gate.
                  </p>
                </div>
              </div>
            </div>

            {/* ── Violations Section ── */}
            <div className="od-section-title-row">
              <AlertCircle size={17} />
              <h2 className="od-section-title">Violations</h2>
            </div>
            <div className="od-card od-violations-card">
              <div className="od-card-head"><AlertCircle size={16} /> My Violation Record</div>
              {violationsLoading ? (
                <div className="od-inner-loading"><Loader2 size={20} className="od-spin-icon" /> Loading violations…</div>
              ) : violationsError ? (
                <div className="od-inner-error">
                  <AlertTriangle size={16} /> {violationsError}
                  <button className="od-retry-link" onClick={fetchViolations}>Retry</button>
                </div>
              ) : violations.length === 0 ? (
                <div className="od-clean-record">
                  <div className="od-clean-icon"><ShieldCheck size={32} /></div>
                  <p className="od-clean-text">No violations on record.</p>
                  <span className="od-clean-sub">Keep up your good driving behavior on campus!</span>
                </div>
              ) : (
                <>
                  {/* ── Active violations ── */}
                  {(() => {
                    const active = violations.filter(v => !v.is_resolved && v.status !== 'cleared')
                    const hasFee = active.some(v => v.status === 'fee_imposed')
                    const totalOutstanding = active.reduce((sum, v) => sum + parseFloat(v.fine_amount || 0), 0)
                    return (
                      <>
                        {hasFee && (
                          <div className="od-outstanding-banner od-outstanding-banner--blocked">
                            <AlertTriangle size={15} />
                            <span>
                              <strong>Entry Denied.</strong> You have an outstanding ₱{totalOutstanding.toFixed(2)} violation fee.
                              Report to the <strong>CDSO office</strong> to process payment and restore access.
                            </span>
                          </div>
                        )}
                        {!hasFee && totalOutstanding > 0 && (
                          <div className="od-outstanding-banner">
                            <AlertTriangle size={15} />
                            <span>Outstanding fines: <strong>₱{totalOutstanding.toFixed(2)}</strong> — please settle at the CDSO office.</span>
                          </div>
                        )}
                        {active.length > 0 && (
                          <div className="od-violations-table-wrap">
                            <table className="od-violations-table">
                              <thead>
                                <tr>
                                  <th>Violation</th>
                                  <th>Offense</th>
                                  <th>Notes</th>
                                  <th>Fine</th>
                                  <th>Date Issued</th>
                                  <th>Status</th>
                                </tr>
                              </thead>
                              <tbody>
                                {active.map(v => (
                                  <tr key={v.id} className={`od-viol-active${v.status === 'fee_imposed' ? ' od-viol-fee' : ''}`}>
                                    <td className="od-viol-type">{VIOLATION_TYPE_LABELS[v.violation_type] || v.violation_type}</td>
                                    <td className="od-viol-offense">
                                      {v.offense_number
                                        ? <span className={`od-offense-badge od-offense-${v.offense_number}`}>{OFFENSE_LABELS[v.offense_number] || `${v.offense_number}th offense`}</span>
                                        : '—'
                                      }
                                    </td>
                                    <td className="od-viol-notes">{v.notes || '—'}</td>
                                    <td className="od-viol-fine">
                                      {parseFloat(v.fine_amount) > 0 ? `₱${parseFloat(v.fine_amount).toFixed(2)}` : <span style={{ color: '#64839C' }}>₱0</span>}
                                    </td>
                                    <td className="od-viol-date">{new Date(v.issued_at).toLocaleDateString('en-PH', { year: 'numeric', month: 'short', day: 'numeric' })}</td>
                                    <td>
                                      {v.status === 'fee_imposed'
                                        ? <span className="od-viol-badge od-viol-badge--fee">Entry Denied · Pay ₱150</span>
                                        : v.status === 'warning'
                                        ? <span className="od-viol-badge od-viol-badge--warning">Warning</span>
                                        : <span className="od-viol-badge notified">Active</span>
                                      }
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        )}
                      </>
                    )
                  })()}

                  {/* ── Resolved / Cleared history ── */}
                  {(() => {
                    const resolved = violations.filter(v => v.is_resolved || v.status === 'cleared')
                    if (resolved.length === 0) return null
                    return (
                      <div className="od-history-section">
                        <div className="od-history-label">
                          <ShieldCheck size={13} /> Violation History — Resolved
                        </div>
                        <div className="od-violations-table-wrap">
                          <table className="od-violations-table od-violations-table--history">
                            <thead>
                              <tr>
                                <th>Violation</th>
                                <th>Notes</th>
                                <th>Fine</th>
                                <th>Date Issued</th>
                                <th>Status</th>
                              </tr>
                            </thead>
                            <tbody>
                              {resolved.map(v => (
                                <tr key={v.id} className="od-viol-resolved">
                                  <td className="od-viol-type">{VIOLATION_TYPE_LABELS[v.violation_type] || v.violation_type}</td>
                                  <td className="od-viol-notes">{v.notes || '—'}</td>
                                  <td className="od-viol-fine">₱{parseFloat(v.fine_amount || 0).toFixed(2)}</td>
                                  <td className="od-viol-date">{new Date(v.issued_at).toLocaleDateString('en-PH', { year: 'numeric', month: 'short', day: 'numeric' })}</td>
                                  <td>
                                    <span className="od-viol-badge resolved">Resolved</span>
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    )
                  })()}
                </>
              )}
            </div>

            {/* ── Announcements Section ── */}
            {(noticesLoading || notices.length > 0) && (
              <>
                <div className="od-section-title-row">
                  <Megaphone size={17} />
                  <h2 className="od-section-title">Announcements</h2>
                  {noticesLoading && <Loader2 size={15} className="od-spin-icon" />}
                </div>
                {!noticesLoading && (
                  <div className="od-notices-list">
                    {notices.map(n => (
                      <div key={n.id} className="od-notice-item">
                        <div className="od-notice-icon"><Megaphone size={15} /></div>
                        <div className="od-notice-content">
                          <span className="od-notice-title">{n.title}</span>
                          <p className="od-notice-body">{n.body}</p>
                          <span className="od-notice-date">
                            {new Date(n.created_at).toLocaleDateString('en-PH', { year: 'numeric', month: 'long', day: 'numeric' })}
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </>
            )}

            {/* ── Parking Availability Section ── */}
            <div className="od-section-title-row">
              {parkingCategory === 'motorcycle' ? <Bike size={17} /> : <ParkingCircle size={17} />}
              <h2 className="od-section-title">
                Live Parking Availability — {parkingCategory === 'motorcycle' ? 'Motorcycle' : 'Car'} Area
              </h2>
              <button className="od-refresh-btn" onClick={() => fetchParking(parkingCategory)} title="Refresh">
                <RefreshCw size={14} />
              </button>
            </div>
            <div className="od-card od-parking-card">
              <div className="od-card-head">
                {parkingCategory === 'motorcycle' ? <Bike size={16} /> : <ParkingCircle size={16} />}
                {parkingCategory === 'motorcycle' ? 'Motorcycle' : 'Car'} Parking Spaces
              </div>

              {parkingLoading ? (
                <div className="od-inner-loading"><Loader2 size={20} className="od-spin-icon" /> Loading parking data…</div>
              ) : parkingError ? (
                <div className="od-inner-error">
                  <AlertTriangle size={16} /> {parkingError}
                  <button className="od-retry-link" onClick={() => fetchParking(parkingCategory)}>Retry</button>
                </div>
              ) : (
                <>
                  {/* An event under way has spoken for part of the car park.
                      Above the counters, because it explains them. */}
                  {parkingEvent && (
                    <div className="od-parking-event">
                      <CalendarDays size={14} />
                      <span>
                        <strong>{parkingEvent.name}</strong> is on
                        {parkingEvent.time_display !== 'All day'
                          ? ` (${parkingEvent.time_display})`
                          : ' today'}
                        {' — '}
                        {parkingEvent.share_label.replace(/^About /, 'about ').toLowerCase()
                          .replace('of parking', 'of the car park')} is set aside for it.
                      </span>
                    </div>
                  )}

                  {/* Summary counters */}
                  {parkingSummary ? (
                    <div className="od-parking-summary">
                      <div className="od-parking-stat available">
                        <span className="od-parking-stat-num">{parkingSummary.available}</span>
                        <span className="od-parking-stat-label">Available</span>
                      </div>
                      {parkingSummary.reserved > 0 && (
                        <div className="od-parking-stat reserved">
                          <span className="od-parking-stat-num">{parkingSummary.reserved}</span>
                          <span className="od-parking-stat-label">Held for event</span>
                        </div>
                      )}
                      <div className="od-parking-stat occupied">
                        <span className="od-parking-stat-num">{parkingSummary.occupied}</span>
                        <span className="od-parking-stat-label">Parked</span>
                      </div>
                      <div className="od-parking-stat total">
                        <span className="od-parking-stat-num">{parkingSummary.total}</span>
                        <span className="od-parking-stat-label">Total</span>
                      </div>
                    </div>
                  ) : null}

                  {/* A separate question from the counters above: how many are
                      inside the gates, parked or not. Said in a sentence rather
                      than as a fourth counter so nobody subtracts it from Total. */}
                  {parkingSummary && (
                    <p className="od-parking-oncampus">
                      <strong>{parkingSummary.on_campus ?? 0}</strong>{' '}
                      {parkingCategory === 'motorcycle' ? 'motorcycle' : 'car'}
                      {(parkingSummary.on_campus ?? 0) === 1 ? ' is' : 's are'} on campus right now,
                      counted at the gate. Some may still be looking for a space.
                    </p>
                  )}
                  {parkingSummary?.unmonitored > 0 && (
                    <p className="od-parking-unmonitored">
                      <AlertTriangle size={14} />
                      {parkingSummary.unmonitored} parking area{parkingSummary.unmonitored === 1 ? ' is' : 's are'} not
                      monitored yet, so some spaces shown as available may be taken.
                    </p>
                  )}

                  {/* Per-zone fill percentage */}
                  {parkingZones.length > 0 && (
                    <div className="od-zone-fill-grid">
                      {parkingZones.map(z => (
                        <div key={z.zone_id} className="od-zone-fill-card">
                          <div className="od-zone-fill-header">
                            <span className="od-zone-fill-name">{z.zone_name}</span>
                            {/* "0%" alone sits directly above "1 of 1 spaces
                                available" and reads just as easily as 0%
                                *available* — the opposite of what it means. */}
                            <span className={`od-zone-fill-pct ${z.fill_pct >= 90 ? 'critical' : z.fill_pct >= 70 ? 'high' : 'normal'}`}>
                              {z.fill_pct}%<span className="od-zone-fill-pct-unit">full</span>
                            </span>
                          </div>
                          <div className="od-zone-fill-bar-bg">
                            <div
                              className={`od-zone-fill-bar ${z.fill_pct >= 90 ? 'critical' : z.fill_pct >= 70 ? 'high' : 'normal'}`}
                              style={{ width: `${z.fill_pct}%` }}
                            />
                          </div>
                          <div className="od-zone-fill-sub">
                            {z.available} of {z.total} spaces available
                          </div>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Parking grid */}
                  {parkingSpaces.length > 0 ? (
                    <div className="od-parking-grid">
                      {parkingSpaces.map(space => (
                        <div
                          key={space.id}
                          className={`od-parking-space ${space.is_occupied ? 'occupied' : 'free'}`}
                          title={space.is_occupied ? `Occupied${space.occupied_by ? ` by ${space.occupied_by}` : ''}` : 'Available'}
                        >
                          <span className="od-space-num">{space.space_number}</span>
                          {space.is_occupied && space.occupied_by && (
                            <span className="od-space-plate">{space.occupied_by}</span>
                          )}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="od-parking-empty">
                      <p>No parking spaces configured yet.</p>
                      <span className="od-parking-cctv-note">
                        Spaces appear here once campus security sets up the parking cameras for this area.
                      </span>
                    </div>
                  )}

                  <div className="od-parking-legend">
                    <span className="od-legend-item free"><span className="od-legend-dot free" />Available</span>
                    <span className="od-legend-item occupied"><span className="od-legend-dot occupied" />Occupied</span>
                  </div>
                  <p className="od-parking-note">
                    Spaces are watched by the parking cameras and update on their own.
                    Only {parkingCategory === 'motorcycle' ? 'motorcycle' : 'car/van/SUV'} spaces are shown for your vehicle type.
                  </p>
                </>
              )}
            </div>
          </>
        )}
      </div>
    </>
  )
}
