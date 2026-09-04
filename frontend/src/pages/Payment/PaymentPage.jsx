import { useState, useEffect, useCallback } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { CheckCircle, AlertTriangle, Receipt, ArrowLeft } from 'lucide-react'

import { registrationApi } from '../../api/registration'
import notify from '../../components/Feedback/notify'
import { fieldProblems } from '../../components/Feedback/formProblems'
import {
  IllustratedStep, PayAtAccountingArt, OrNumberArt, CdsoReviewArt,
} from '../../components/Illustrations/RegArt'
import slcLogo from '../../assets/slclogo.jpg'
import './PaymentPage.css'

/* TEMPORARY — Data Privacy Office trial.
   The receipt photo is no longer collected: this page files the OR number
   alone, and the CDSO checks the paper receipt at the counter instead of an
   image on the review screen. */

const TYPE_LABEL = {
  student:  'Student',
  employee: 'Employee',
  fetcher:  'Fetcher / Drop & Go',
}

function SlcHeader() {
  return (
    <header className="paypage-header">
      <div className="header-content">
        <div className="header-logo-group">
          <img src={slcLogo} alt="Saint Louis College Logo" className="header-logo" />
          <div className="header-text">
            <span className="header-title">SAINT LOUIS COLLEGE</span>
            <span className="header-subtitle">Smart Parking and Vehicle Verification System</span>
          </div>
        </div>
      </div>
    </header>
  )
}

/* The applicant's own proof-of-payment step.

   They pay the Vehicle Pass fee at the Accounting Office, then land here from
   the link in their pending email to file the Official Receipt number
   themselves, rather than having CDSO re-key it at a counter. */
export default function PaymentPage() {
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token')
  const navigate = useNavigate()

  const [loading, setLoading]   = useState(true)
  const [details, setDetails]   = useState(null)
  const [loadError, setLoadError] = useState(null)

  const [orNumber, setOrNumber] = useState('')

  const [submitting, setSubmitting] = useState(false)
  const [submitted, setSubmitted]   = useState(false)

  const load = useCallback(async () => {
    if (!token) {
      setLoadError('This link is missing its access code. Please open the link from your registration email exactly as it was sent.')
      setLoading(false)
      return
    }
    try {
      const data = await registrationApi.getPaymentDetails(token)
      setDetails(data)
      // Pre-fill so a returning applicant correcting a mistyped number does not
      // have to find the receipt again.
      if (data.or_number) setOrNumber(data.or_number)
    } catch (err) {
      setLoadError(
        err.response?.data?.error
        || 'This payment link is no longer valid. It may have expired, or your application may already have been reviewed.'
      )
    } finally {
      setLoading(false)
    }
  }, [token])

  useEffect(() => { load() }, [load])

  // 6-7 digits, matching the CDSO accept panel exactly. A looser rule here let
  // an applicant file a 4-digit number that the reviewer's panel then refused,
  // leaving the application impossible to approve from either side.
  const orValid = /^\d{6,7}$/.test(orNumber)

  const handleSubmit = async (e) => {
    e.preventDefault()
    const problems = [...fieldProblems(e.currentTarget)]
    if (!orValid) problems.push('Enter the Official Receipt number — 6 or 7 digits, numbers only.')
    if (await notify.validation(problems, { title: 'Receipt not submitted' })) return

    setSubmitting(true)
    try {
      await registrationApi.submitPaymentReceipt(token, orNumber)
      setSubmitted(true)
    } catch (err) {
      notify.error(err.response?.data?.error || err.message || 'Failed to submit the receipt. Please try again.', { title: 'Receipt not submitted' })
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
            <p className="paypage-muted">Checking your application…</p>
          </div>
        </main>
      </div>
    )
  }

  /* ─── Dead link ─── */
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
              If you have already paid, bring your Official Receipt to the <strong>CDSO Office</strong> and
              they will record it for you.
            </p>
            <button className="paypage-btn-ghost" onClick={() => navigate('/login')}>
              <ArrowLeft size={15} /> Back to Login
            </button>
          </div>
        </main>
      </div>
    )
  }

  /* ─── Done ─── */
  if (submitted) {
    return (
      <div className="paypage">
        <SlcHeader />
        <main className="paypage-main">
          <div className="paypage-card paypage-card--center">
            <div className="paypage-icon paypage-icon--ok">
              <CheckCircle size={48} strokeWidth={1.8} />
            </div>
            <h2 className="paypage-title">Receipt Number Filed</h2>
            <p className="paypage-muted">
              Your application is now marked <strong>paid</strong> and queued for CDSO review.
              You will get an email once a decision has been made.
            </p>
            <div className="paypage-summary">
              <div className="paypage-summary-row">
                <span>Official Receipt No.</span>
                <strong>{orNumber}</strong>
              </div>
              <div className="paypage-summary-row">
                <span>Applicant</span>
                <strong>{details?.full_name}</strong>
              </div>
              <div className="paypage-summary-row">
                <span>Plate Number</span>
                <strong>{details?.plate_number || '—'}</strong>
              </div>
            </div>
            <p className="paypage-muted paypage-muted--small">
              Keep the physical receipt — the CDSO Office checks it against this number when
              you collect your pass.
            </p>
            <button className="paypage-btn-ghost" onClick={() => navigate('/login')}>
              <ArrowLeft size={15} /> Back to Login
            </button>
          </div>
        </main>
      </div>
    )
  }

  /* ─── Nothing to pay ───
     Exempt applicants are never sent this link, but the URL can still be
     reached — shared, bookmarked, forwarded. Showing them a ₱0.00 form they
     cannot submit would just send them looking for a receipt that was never
     issued, so the page says so outright instead. */
  if (details?.payment_status === 'exempt') {
    return (
      <div className="paypage">
        <SlcHeader />
        <main className="paypage-main">
          <div className="paypage-card paypage-card--center">
            <div className="paypage-icon paypage-icon--ok">
              <CheckCircle size={48} strokeWidth={1.8} />
            </div>
            <h2 className="paypage-title">No Payment Required</h2>
            <p className="paypage-muted">
              Your department is <strong>exempt</strong> from the Vehicle Pass fee, so there is
              nothing to settle at the Accounting Office and no receipt number to file.
            </p>
            <p className="paypage-muted paypage-muted--small">
              Your application is already queued for CDSO review. Watch for the approval email.
            </p>
            <button className="paypage-btn-ghost" onClick={() => navigate('/login')}>
              <ArrowLeft size={15} /> Back to Login
            </button>
          </div>
        </main>
      </div>
    )
  }

  /* ─── Already on file ─── */
  const alreadyPaid = details?.payment_status === 'paid'

  /* ─── Form ─── */
  return (
    <div className="paypage">
      <SlcHeader />
      <main className="paypage-main">
        <div className="paypage-card">

          <div className="paypage-head">
            <div className="paypage-icon paypage-icon--brand">
              <Receipt size={26} strokeWidth={1.9} />
            </div>
            <div>
              <h1 className="paypage-title paypage-title--left">File Your Official Receipt Number</h1>
              <p className="paypage-muted paypage-muted--small">
                For <strong>{details?.full_name}</strong>
                {details?.registrant_type && <> · {TYPE_LABEL[details.registrant_type] || details.registrant_type}</>}
                {details?.plate_number && <> · {details.plate_number}</>}
              </p>
            </div>
          </div>

          <div className="paypage-amount">
            <span className="paypage-amount-label">Vehicle Pass Fee</span>
            <span className="paypage-amount-value">₱{Number(details?.amount_due ?? 0).toFixed(2)}</span>
            <span className="paypage-amount-note">Payable at the Accounting Office</span>
          </div>

          {alreadyPaid && (
            <div className="paypage-note paypage-note--ok">
              <CheckCircle size={14} />
              <span>
                A receipt number is already on file for this application. Filing again
                <strong> replaces</strong> it — useful if the first one was mistyped.
              </span>
            </div>
          )}

          {/* The same drawn steps the applicant already met on the confirmation
              screen, continued here. Most arrive days later from a link in an
              email, with no memory of what the errand was. */}
          <div className="paypage-steps">
            <p className="paypage-steps-heading">How this works</p>
            <div className="reg-step-list">
              <IllustratedStep step={1} art={<PayAtAccountingArt />} title="Pay at the Accounting Office">
                Settle the vehicle pass fee at the counter first, and keep the Official Receipt
                they hand you.
              </IllustratedStep>
              <IllustratedStep step={2} art={<OrNumberArt />} title="Enter the OR number">
                Copy it from the receipt exactly as printed. Keep the receipt itself — the CDSO
                checks the paper copy against this number when you collect your pass.
              </IllustratedStep>
              <IllustratedStep step={3} art={<CdsoReviewArt />} title="The CDSO reviews it">
                Your application is not queued for review until the number is filed. You will be
                emailed the outcome.
              </IllustratedStep>
            </div>
          </div>

          <form noValidate onSubmit={handleSubmit} className="paypage-form">

            <div className="paypage-field">
              <label className="paypage-label" htmlFor="or-number">
                Official Receipt (OR) Number <span className="paypage-req">*</span>
              </label>
              <input
                id="or-number"
                type="text"
                inputMode="numeric"
                maxLength={7}
                value={orNumber}
                onChange={(e) => setOrNumber(e.target.value.replace(/\D/g, '').slice(0, 7))}
                placeholder="e.g. 1380093"
                disabled={submitting}
                className={`paypage-input${orNumber && !orValid ? ' paypage-input--error' : ''}`}
              />
              <span className="paypage-hint">
                The number printed on the receipt the Accounting Office issued — 6 or 7 digits.
              </span>
            </div>

            <button type="submit" className="paypage-btn-submit" disabled={submitting}>
              {submitting ? 'Submitting…' : 'Submit Receipt Number'}
            </button>
          </form>
        </div>
      </main>
    </div>
  )
}
