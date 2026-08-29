import { useState, useEffect, useCallback } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { CheckCircle, AlertTriangle, Upload, X, FileText, Receipt, ArrowLeft } from 'lucide-react'

import { registrationApi } from '../../api/registration'
import notify from '../../components/Feedback/notify'
import { fieldProblems } from '../../components/Feedback/formProblems'
import { compressImage } from '../../utils/imageCompress'
import {
  IllustratedStep, PayAtAccountingArt, OrNumberArt, ReceiptPhotoArt, CdsoReviewArt,
} from '../../components/Illustrations/RegArt'
import slcLogo from '../../assets/slclogo.jpg'
import './PaymentPage.css'

const RECEIPT_MAX_MB    = 5
const RECEIPT_MAX_BYTES = RECEIPT_MAX_MB * 1024 * 1024
const RECEIPT_TYPES     = ['image/jpeg', 'image/png', 'image/webp', 'image/heic', 'image/heif', 'application/pdf']

function formatFileSize(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

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
   the link in their pending email to file the Official Receipt themselves. CDSO
   verifies the photo against the number at review time instead of re-keying it
   at a counter, which is what the whole flow used to depend on. */
export default function PaymentPage() {
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token')
  const navigate = useNavigate()

  const [loading, setLoading]   = useState(true)
  const [details, setDetails]   = useState(null)
  const [loadError, setLoadError] = useState(null)

  const [orNumber, setOrNumber] = useState('')
  const [receipt, setReceipt]   = useState(null)
  const [preview, setPreview]   = useState(null)

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
      // Pre-fill so a returning applicant correcting a blurry photo does not
      // have to find the receipt number again.
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

  // Release the last object URL when the page unmounts
  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview) }, [preview])

  const handleFileChange = async (e) => {
    const picked = e.target.files?.[0]
    // Let the user re-pick the same file after removing it
    e.target.value = ''
    if (!picked) return

    // Some browsers report an empty type for HEIC; fall back to the extension.
    const extOk = /\.(jpe?g|png|webp|heic|heif|pdf)$/i.test(picked.name)
    if (!RECEIPT_TYPES.includes(picked.type) && !extOk) {
      notify.error('Please choose a JPG, PNG, WEBP, HEIC or PDF file.', { title: 'Unsupported file' })
      return
    }
    if (picked.size > RECEIPT_MAX_BYTES) {
      notify.error(`That file is ${formatFileSize(picked.size)}. Please keep it under ${RECEIPT_MAX_MB}MB.`, { title: 'File too large' })
      return
    }

    // A photographed receipt is the whole payload of this page, so shrinking it
    // here is most of what the applicant feels on Submit. PDFs pass through.
    const file = await compressImage(picked)

    setReceipt(file)
    // PDFs and HEIC won't render — those fall back to the filename chip.
    // Built outside the updater so StrictMode's double-invoke can't leak a URL.
    const nextPreview = /^image\/(jpeg|png|webp)$/.test(file.type)
      ? URL.createObjectURL(file)
      : null
    setPreview((prev) => {
      if (prev) URL.revokeObjectURL(prev)
      return nextPreview
    })
  }

  const clearReceipt = () => {
    setReceipt(null)
    setPreview((prev) => {
      if (prev) URL.revokeObjectURL(prev)
      return null
    })
  }

  // 6-7 digits, matching the CDSO accept panel exactly. A looser rule here let
  // an applicant file a 4-digit number that the reviewer's panel then refused,
  // leaving the application impossible to approve from either side.
  const orValid = /^\d{6,7}$/.test(orNumber)

  const handleSubmit = async (e) => {
    e.preventDefault()
    const problems = [...fieldProblems(e.currentTarget)]
    if (!orValid) problems.push('Enter the Official Receipt number — 6 or 7 digits, numbers only.')
    if (!receipt) problems.push('Attach a photo or scan of your Official Receipt.')
    if (await notify.validation(problems, { title: 'Receipt not submitted' })) return

    setSubmitting(true)
    try {
      await registrationApi.submitPaymentReceipt(token, orNumber, receipt)
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
            <h2 className="paypage-title">Receipt Received</h2>
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
              Keep the physical receipt — the CDSO Office may still ask to see it.
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
              nothing to settle at the Accounting Office and no receipt to upload.
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
              <h1 className="paypage-title paypage-title--left">Upload Your Official Receipt</h1>
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
                A receipt is already on file for this application. Uploading again
                <strong> replaces</strong> it — useful if the first photo was unclear.
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
                Copy it from the receipt exactly as printed — the CDSO checks the number against
                the photo you attach.
              </IllustratedStep>
              <IllustratedStep step={3} art={<ReceiptPhotoArt />} title="Attach a photo of the receipt">
                The whole receipt, flat and in focus, with every corner in frame. A blurred or
                cropped photo has to be retaken.
              </IllustratedStep>
              <IllustratedStep step={4} art={<CdsoReviewArt />} title="The CDSO reviews it">
                Your application is not queued for review until the receipt is filed. You will be
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

            <div className="paypage-field">
              <label className="paypage-label">
                Photo of the Receipt <span className="paypage-req">*</span>
              </label>

              {!receipt ? (
                <label className="paypage-upload">
                  <input
                    type="file"
                    accept={RECEIPT_TYPES.join(',')}
                    onChange={handleFileChange}
                    className="paypage-upload-input"
                    disabled={submitting}
                  />
                  <Upload size={18} className="paypage-upload-icon" />
                  <span className="paypage-upload-text">
                    <strong>Choose a photo or file</strong>
                    <span>JPG, PNG, WEBP, HEIC or PDF · up to {RECEIPT_MAX_MB}MB</span>
                  </span>
                </label>
              ) : (
                <div className="paypage-preview">
                  {preview ? (
                    <img src={preview} alt="Official receipt preview" className="paypage-preview-img" />
                  ) : (
                    <div className="paypage-preview-img paypage-preview-noimg">
                      <FileText size={20} />
                    </div>
                  )}
                  <div className="paypage-preview-meta">
                    <span className="paypage-preview-name" title={receipt.name}>{receipt.name}</span>
                    <span className="paypage-preview-size">{formatFileSize(receipt.size)}</span>
                  </div>
                  <button
                    type="button"
                    className="paypage-preview-remove"
                    onClick={clearReceipt}
                    aria-label="Remove receipt"
                    disabled={submitting}
                  >
                    <X size={15} />
                  </button>
                </div>
              )}

              <span className="paypage-hint">
                Make sure the receipt number and amount are readable — a blurry photo sends
                your application back to you.
              </span>
            </div>


            <button type="submit" className="paypage-btn-submit" disabled={submitting}>
              {submitting ? 'Submitting…' : 'Submit Receipt'}
            </button>
          </form>
        </div>
      </main>
    </div>
  )
}
