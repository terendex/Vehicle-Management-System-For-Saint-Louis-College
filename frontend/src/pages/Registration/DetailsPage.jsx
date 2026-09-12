import { useState, useEffect, useCallback, useMemo } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { CheckCircle, AlertTriangle, ArrowLeft, Pencil, Info } from 'lucide-react'

import { registrationApi } from '../../api/registration'
import notify from '../../components/Feedback/notify'
import BrandLogos from '../../components/BrandLogos'
import DetailFields from '../../components/RegistrationDetails/DetailFields'
import {
  changedValues, detailFormProblems,
} from '../../components/RegistrationDetails/detailRules'
import ChangeDiff from '../../components/RegistrationDetails/ChangeDiff'
import '../../components/RegistrationDetails/detailFields.css'
import '../Payment/PaymentPage.css'
import './DetailsPage.css'

/* The applicant's own correction step, for an application still awaiting CDSO.

   Reached from the "Edit My Details" button in the acknowledgement email, on
   the same token the receipt step uses. Nobody has reviewed the application
   yet, so a detail they mistyped is still theirs to fix and the change applies
   straight away — the approval-gated flow is the other one, on the owner
   dashboard, and it only exists because an *approved* registration has already
   issued a pass.

   Borrows PaymentPage.css deliberately: the two pages are the same errand from
   the applicant's side (a link in an email, one short form, a confirmation) and
   arriving at a second, differently-shaped page reads as a different system. */

function SlcHeader() {
  return (
    <header className="paypage-header">
      <div className="header-content">
        <div className="header-logo-group">
          <BrandLogos size="header" />
          <div className="header-text">
            <span className="header-title">SAINT LOUIS COLLEGE</span>
            <span className="header-subtitle">Smart Parking and Vehicle Verification System</span>
          </div>
        </div>
      </div>
    </header>
  )
}

export default function DetailsPage() {
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token')
  const navigate = useNavigate()

  const [loading, setLoading]     = useState(true)
  const [loadError, setLoadError] = useState(null)
  const [details, setDetails]     = useState(null)

  // `original` is what is on file; `values` is what is in the boxes. Keeping
  // both is what lets the page post only what actually moved, and lets the
  // submit button stay disabled until something has.
  const [original, setOriginal] = useState({})
  const [values, setValues]     = useState({})
  const [errors, setErrors]     = useState({})

  const [submitting, setSubmitting] = useState(false)
  const [applied, setApplied]       = useState(null)   // the change set, once saved

  const load = useCallback(async () => {
    if (!token) {
      setLoadError('This link is missing its access code. Please open the link from your '
                 + 'registration email exactly as it was sent.')
      setLoading(false)
      return
    }
    try {
      const data = await registrationApi.getEditableDetails(token)
      setDetails(data)
      setOriginal(data.values || {})
      setValues(data.values || {})
    } catch (err) {
      setLoadError(err.response?.data?.error
        || 'This link is no longer valid. It may have expired, or your application may '
         + 'already have been reviewed.')
    } finally {
      setLoading(false)
    }
  }, [token])

  useEffect(() => { load() }, [load])

  const changed = useMemo(() => changedValues(values, original), [values, original])
  const hasChanges = Object.keys(changed).length > 0

  const handleChange = (field, value) => {
    setValues(prev => ({ ...prev, [field]: value }))
    // Clear the field's error as soon as it is edited — a message about the
    // previous value is worse than none, because it describes text that is no
    // longer on screen.
    setErrors(prev => (prev[field] ? { ...prev, [field]: null } : prev))
  }

  const handleReset = () => {
    setValues(original)
    setErrors({})
  }

  const handleSubmit = async (event) => {
    event.preventDefault()
    if (!hasChanges) return

    const problems = detailFormProblems(values, original, details?.editable || [])
    if (await notify.validation(problems, { title: 'Check your details' })) return

    setSubmitting(true)
    try {
      const result = await registrationApi.submitDetailChanges(token, changed)
      setApplied(result.changed || [])
      // Reload from the response rather than from a second request: it carries
      // the server's normalised values (ABC1234, not "abc 1234"), so the boxes
      // show what was actually stored.
      setOriginal(result.values || values)
      setValues(result.values || values)
      setErrors({})
    } catch (err) {
      const fieldErrors = err.response?.data?.errors
      if (fieldErrors) {
        setErrors(fieldErrors)
        await notify.error(
          'Some of your details could not be saved — see the notes on each field.',
          { title: 'Details not saved' })
      } else {
        await notify.error(err.response?.data?.error
          || 'Could not save your details. Please try again.',
          { title: 'Details not saved' })
      }
    } finally {
      setSubmitting(false)
    }
  }

  /* ─── Loading ─── */
  if (loading) {
    return (
      <div className="paypage">
        <SlcHeader />
        <main className="paypage-main">
          <div className="paypage-card paypage-card--center">
            <div className="paypage-spinner" />
            <p className="paypage-muted">Opening your application…</p>
          </div>
        </main>
      </div>
    )
  }

  /* ─── Dead link ───
     The most likely cause by far is that CDSO has since reviewed the
     application, so the copy names the way forward rather than only the fault. */
  if (loadError) {
    return (
      <div className="paypage">
        <SlcHeader />
        <main className="paypage-main">
          <div className="paypage-card paypage-card--center">
            <div className="paypage-icon paypage-icon--warn">
              <AlertTriangle size={44} strokeWidth={1.8} />
            </div>
            <h2 className="paypage-title">Link Unavailable</h2>
            <p className="paypage-muted">{loadError}</p>
            <p className="paypage-muted paypage-muted--small">
              If your application has already been reviewed, the <strong>CDSO Office</strong> can
              still correct your details — and if it was approved, you can ask for a change from
              your portal dashboard.
            </p>
            <button className="paypage-btn-ghost" onClick={() => navigate('/login')}>
              <ArrowLeft size={15} /> Back to Login
            </button>
          </div>
        </main>
      </div>
    )
  }

  /* ─── Saved ───
     Editing stays open, so this is a confirmation rather than an end state: the
     form is still below it, and the applicant can correct something else
     without going back to their email for the link again. */
  return (
    <div className="paypage">
      <SlcHeader />
      <main className="paypage-main">
        <div className="paypage-card">

          <div className="paypage-head">
            <div className="paypage-icon paypage-icon--brand">
              <Pencil size={26} strokeWidth={1.9} />
            </div>
            <div>
              <h1 className="paypage-title paypage-title--left">Correct Your Details</h1>
              <p className="paypage-muted paypage-muted--small">
                {details?.reference}
                {details?.locked?.registrant_type && <> · {details.locked.registrant_type}</>}
                {details?.locked?.email && <> · {details.locked.email}</>}
              </p>
            </div>
          </div>

          {applied !== null && (
            <div className="paypage-note paypage-note--ok">
              <CheckCircle size={14} />
              <span>
                {applied.length > 0
                  ? <>Saved. A new acknowledgement has been emailed to you, and it replaces
                      the previous one.</>
                  : <>Nothing had changed, so nothing was saved.</>}
              </span>
            </div>
          )}

          {applied?.length > 0 && (
            <div className="rdpage-block">
              <p className="rdpage-block-heading">What you just changed</p>
              <ChangeDiff changes={applied} />
            </div>
          )}

          <div className="paypage-note">
            <Info size={14} />
            <span>
              You can keep correcting these for as long as your application is awaiting
              review. Once the CDSO approves it, changes need their approval instead.
            </span>
          </div>

          <form noValidate onSubmit={handleSubmit} className="rdpage-form">
            <DetailFields
              editable={details?.editable || []}
              values={values}
              onChange={handleChange}
              errors={errors}
              disabled={submitting}
              idPrefix="rd"
            />

            <div className="rdpage-locked">
              <p className="rdpage-block-heading">Not changeable here</p>
              <ul>
                <li>
                  <strong>Email address</strong> — this is where every notice about your
                  application is sent, including this link. The CDSO Office can change it.
                </li>
                <li>
                  <strong>Registrant type and campus schedule</strong> — the CDSO assigns
                  your campus days, and changing type means a new application.
                </li>
              </ul>
            </div>

            <div className="rdpage-actions">
              <button
                type="button"
                className="paypage-btn-ghost"
                onClick={handleReset}
                disabled={submitting || !hasChanges}
              >
                Undo my edits
              </button>
              <button
                type="submit"
                className="paypage-btn-submit"
                disabled={submitting || !hasChanges}
              >
                {submitting ? 'Saving…'
                  : hasChanges ? `Save ${Object.keys(changed).length} change${Object.keys(changed).length === 1 ? '' : 's'}`
                  : 'No changes to save'}
              </button>
            </div>
          </form>
        </div>
      </main>
    </div>
  )
}
