import { useState, useEffect, useCallback } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { CheckCircle, AlertTriangle, Car, Info, X, Banknote } from 'lucide-react'
import { registrationApi } from '../../api/registration'
import ComboBox from '../../components/ComboBox'
import slcLogo from '../../assets/slclogo.jpg'
import './RegisterPage.css'

const CAMPUS_DAYS = [
  { key: 'Monday',    short: 'Mon' },
  { key: 'Tuesday',   short: 'Tue' },
  { key: 'Wednesday', short: 'Wed' },
  { key: 'Thursday',  short: 'Thu' },
  { key: 'Friday',    short: 'Fri' },
  { key: 'Saturday',  short: 'Sat' },
]


const SLC_HEADER = (
  <header className="register-header">
    <div className="header-content">
      <div className="header-logo-group">
        <img src={slcLogo} alt="Saint Louis College Logo" className="header-logo" />
        <div className="header-text">
          <span className="header-title">SAINT LOUIS COLLEGE</span>
          <span className="header-subtitle">City of San Fernando, La Union</span>
        </div>
      </div>
    </div>
  </header>
)

export default function RegisterPage() {
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token')
  const directType = searchParams.get('type') // 'student' | 'student_ebike' | 'employee' | 'fetcher'
  const navigate = useNavigate()

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [registrantType, setRegistrantType] = useState(null)
  const [isEbike, setIsEbike] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [showPaymentPopup, setShowPaymentPopup] = useState(false)

  // Schedule slots & reference lists
  const [scheduleSlots,    setScheduleSlots]    = useState(null)
  const [loadingSlots,     setLoadingSlots]     = useState(false)
  const [departments,      setDepartments]      = useState([])
  const [programs,         setPrograms]         = useState([])

  const [formData, setFormData] = useState({
    full_name: '',
    email: '',
    student_id: '',
    program_year: '',
    employee_id: '',
    department: '',
    address: '',
    contact_number: '',
    age: '',
    drivers_license: '',
    campus_days: [],
    plate_number: '',
    conduction_number: '',
    vehicle_type: '',
    vehicle_color: '',
    body_number: '',
    privacy_consent: false,
  })

  const fetchScheduleSlots = useCallback(async () => {
    setLoadingSlots(true)
    try {
      const slots = await registrationApi.getScheduleSlots()
      setScheduleSlots(slots)
    } catch {
      // non-critical — buttons still usable without slot counts
    } finally {
      setLoadingSlots(false)
    }
  }, [])

  const fetchRefLists = useCallback(async () => {
    try {
      const [deps, progs] = await Promise.all([
        registrationApi.getDepartments(),
        registrationApi.getPrograms(),
      ])
      setDepartments(deps)
      setPrograms(progs)
    } catch {
      // non-critical — ComboBox still works with empty options
    }
  }, [])

  const validateToken = useCallback(async () => {
    if (directType) {
      // Direct registration from login page — no token needed
      const baseType = directType === 'student_ebike' ? 'student' : directType
      setRegistrantType(baseType)
      setIsEbike(directType === 'student_ebike')
      if (directType === 'student_ebike') {
        setFormData(prev => ({ ...prev, vehicle_type: 'E-Bike', drivers_license: 'N/A' }))
      }
      setLoading(false)
      fetchScheduleSlots()
      fetchRefLists()
      return
    }

    if (!token) {
      setError('Invalid registration link. Token is missing.')
      setLoading(false)
      return
    }

    try {
      const data = await registrationApi.validateToken(token)
      setRegistrantType(data.registrant_type)
      setLoading(false)
      fetchScheduleSlots()
      fetchRefLists()
    } catch (err) {
      setError(
        err.response?.data?.error ||
        'This registration link is invalid, expired, or has already been used.'
      )
      setLoading(false)
    }
  }, [token, directType, fetchScheduleSlots, fetchRefLists])

  useEffect(() => {
    validateToken()
  }, [validateToken])

  const handleInputChange = (e) => {
    const { name, value, type, checked } = e.target
    if (type === 'checkbox') {
      setFormData((prev) => ({ ...prev, [name]: checked }))
    } else {
      setFormData((prev) => ({ ...prev, [name]: value }))
    }
  }

  const toggleDay = (dayKey) => {
    setFormData(prev => {
      if (prev.campus_days.includes(dayKey))
        return { ...prev, campus_days: prev.campus_days.filter(d => d !== dayKey) }
      if (prev.campus_days.length >= 3) return prev
      return { ...prev, campus_days: [...prev.campus_days, dayKey] }
    })
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!formData.privacy_consent) {
      alert('You must agree to the Data Privacy Consent.')
      return
    }
    if (registrantType === 'student' && formData.campus_days.length === 0) {
      alert('Please select at least one campus day.')
      return
    }
    if (registrantType === 'student' && formData.campus_days.length > 3) {
      alert('You may only select up to 3 campus days.')
      return
    }

    if (registrantType === 'student' && scheduleSlots) {
      const fullDays = formData.campus_days.filter(d => scheduleSlots[d]?.available === 0)
      if (fullDays.length > 0) {
        alert(`The following day(s) are full: ${fullDays.join(', ')}. Please deselect them and try again.`)
        return
      }
    }

    setSubmitting(true)
    try {
      const payload = { ...formData, registrant_type: registrantType }

      if (directType) {
        // Direct open registration
        await registrationApi.submitOpenRegistration(payload)
      } else {
        // Token-based registration
        await registrationApi.submitRegistration(token, payload)
      }

      // Show payment instructions popup before success screen
      setShowPaymentPopup(true)
    } catch (err) {
      alert(err.response?.data?.error || 'Failed to submit registration. Please try again.')
      console.error(err)
    } finally {
      setSubmitting(false)
    }
  }

  /* ─── Loading ─── */
  if (loading) {
    return (
      <div className="register-page">
        {SLC_HEADER}
        <main className="register-main">
          <div className="register-container">
            <div className="loading-spinner"></div>
          </div>
        </main>
      </div>
    )
  }

  /* ─── Error ─── */
  if (error) {
    return (
      <div className="register-page">
        {SLC_HEADER}
        <main className="register-main">
          <div className="register-card error-card">
            <div className="card-icon error-card-icon">
              <AlertTriangle size={48} />
            </div>
            <h2 className="card-title">Registration Unavailable</h2>
            <p className="card-message">{error}</p>
            <p className="card-help">
              Please contact the administration office for assistance.
            </p>
          </div>
        </main>
      </div>
    )
  }

  /* ─── Payment popup modal (shown after successful submit, before success screen) ─── */
  if (showPaymentPopup) {
    return (
      <div className="register-page">
        {SLC_HEADER}
        <main className="register-main">
          <div className="register-card payment-popup-card">
            <div className="card-icon payment-popup-icon">
              <Banknote size={48} />
            </div>
            <h2 className="card-title" style={{ color: '#D97706' }}>Action Required</h2>
            <div className="payment-popup-body">
              <p className="payment-popup-intro">
                Your application has been submitted and is now <strong>pending review</strong>.
                Before your registration can be processed, you must complete the following steps:
              </p>
              <div className="payment-steps">
                <div className="payment-step">
                  <div className="payment-step-num">1</div>
                  <div className="payment-step-text">
                    <strong>Pay ₱300.00</strong> at the <strong>Accounting Office</strong> for your <em>Vehicle Pass</em>.
                  </div>
                </div>
                <div className="payment-step">
                  <div className="payment-step-num">2</div>
                  <div className="payment-step-text">
                    Present your <strong>Official Receipt (OR)</strong> at the <strong>CDSO (Community Development & Student Office)</strong> for processing.
                  </div>
                </div>
                <div className="payment-step">
                  <div className="payment-step-num">3</div>
                  <div className="payment-step-text">
                    Wait for your email notification once your registration has been reviewed and approved.
                  </div>
                </div>
              </div>
              <div className="payment-popup-note">
                <Info size={14} />
                The CDSO office will verify your Official Receipt number before approving your registration.
              </div>
            </div>
            <button className="card-btn payment-popup-btn" onClick={() => setSubmitted(true)}>
              I Understand — Proceed
            </button>
          </div>
        </main>
      </div>
    )
  }

  /* ─── Success ─── */
  if (submitted) {
    return (
      <div className="register-page">
        {SLC_HEADER}
        <main className="register-main">
          <div className="register-card success-card">
            <div className="card-icon success-card-icon">
              <CheckCircle size={48} />
            </div>
            <h2 className="card-title">Registration Submitted!</h2>
            <p className="card-message">
              Your vehicle registration application has been submitted and is pending review.
            </p>
            <p className="card-help">
              Remember to pay ₱300.00 at the Accounting Office and present your OR at the CDSO Office.
              You will receive an email notification once your application has been processed.
            </p>
            <button className="card-btn" onClick={() => navigate('/login')}>
              Back to Login
            </button>
          </div>
        </main>
      </div>
    )
  }

  const isStudent  = registrantType === 'student'
  const isFetcher  = registrantType === 'fetcher'
  const isEmployee = registrantType === 'employee'

  /* ─── Form ─── */
  return (
    <div className="register-page">
      {SLC_HEADER}

      <main className="register-main">
        <div className="register-card">
          <div className="slc-form-title-block">
            <h1 className="slc-form-title">APPLICATION FORM FOR A VEHICLE PASS</h1>
            <p className="slc-form-subtitle">
              {isStudent
                ? (isEbike ? "STUDENT'S PERSONAL INFORMATION (E-BIKE)" : "STUDENT'S PERSONAL INFORMATION")
                : isEmployee
                  ? "EMPLOYEE'S PERSONAL INFORMATION"
                  : "FETCHER / DROP & GO PERSONAL INFORMATION"}
            </p>
            <p className="slc-form-note">Please write legibly in CAPITAL LETTERS.</p>
            <span className="registrant-badge">
              {isStudent
                ? (isEbike ? 'Student — E-Bike Registration' : 'Student — Vehicle Registration')
                : isEmployee ? 'Employee Registration'
                : 'Fetcher / Drop & Go Registration'}
            </span>
          </div>

          <form onSubmit={handleSubmit} className="register-form">

            {/* ── Vehicle Identification ── */}
            <h3 className="section-heading">Vehicle Identification</h3>
            <div className="form-grid">
              <div className="form-group">
                <label>
                  {isEbike ? 'Student ID Number' : 'Plate Number'}
                  {' '}<span className="required">*</span>
                </label>
                <input
                  type="text"
                  name="plate_number"
                  value={formData.plate_number}
                  onChange={handleInputChange}
                  required
                  placeholder={isEbike ? 'Used as your e-bike identifier' : ''}
                />
              </div>

              {!isEbike && (
                <div className="form-group">
                  <label>Conduction Number <span className="field-note">(for newly purchased vehicles)</span></label>
                  <input type="text" name="conduction_number" value={formData.conduction_number} onChange={handleInputChange} />
                </div>
              )}

              <div className="form-group">
                <label>Vehicle Type <span className="required">*</span></label>
                {isEbike ? (
                  <input type="text" value="E-Bike" readOnly className="input-readonly" />
                ) : (
                  <select name="vehicle_type" value={formData.vehicle_type} onChange={handleInputChange} required>
                    <option value="">Select Type</option>
                    <option value="Sedan">Sedan</option>
                    <option value="SUV">SUV</option>
                    <option value="Motorcycle">Motorcycle</option>
                    <option value="Tricycle">Tricycle</option>
                    <option value="Van">Van</option>
                    <option value="Truck">Truck</option>
                    <option value="Other">Other</option>
                  </select>
                )}
              </div>

              <div className="form-group">
                <label>Vehicle Color <span className="required">*</span></label>
                <input type="text" name="vehicle_color" value={formData.vehicle_color} onChange={handleInputChange} required />
              </div>

              <div className="form-group col-span-2">
                <label>Body Number <span className="field-note">(for tricycle only)</span></label>
                <input
                  type="text"
                  name="body_number"
                  value={formData.body_number}
                  onChange={handleInputChange}
                  disabled={formData.vehicle_type !== 'Tricycle'}
                  placeholder={formData.vehicle_type !== 'Tricycle' ? 'Only applicable for Tricycle' : ''}
                />
              </div>
            </div>

            <hr className="divider" />

            {/* ── Personal Information ── */}
            <h3 className="section-heading">Personal Information</h3>
            <div className="form-grid">

              <div className="form-group col-span-2">
                <label>Full Name <span className="required">*</span></label>
                <input
                  type="text"
                  name="full_name"
                  value={formData.full_name}
                  onChange={handleInputChange}
                  required
                  placeholder="Last Name, First Name M.I."
                />
              </div>

              <div className="form-group col-span-2">
                <label>Email Address <span className="required">*</span></label>
                <input
                  type="email"
                  name="email"
                  value={formData.email}
                  onChange={handleInputChange}
                  required
                />
              </div>

              {/* Student-specific */}
              {isStudent && (
                <>
                  <div className="form-group">
                    <label>Student ID Number <span className="required">*</span></label>
                    <input type="text" name="student_id" value={formData.student_id} onChange={handleInputChange} required />
                  </div>
                  <div className="form-group">
                    <label>Program &amp; Year Level <span className="required">*</span></label>
                    <ComboBox
                      name="program_year"
                      value={formData.program_year}
                      onChange={handleInputChange}
                      options={programs}
                      placeholder="e.g. BSIT - 3"
                      required
                    />
                  </div>
                </>
              )}

              {/* Employee-specific */}
              {isEmployee && (
                <>
                  <div className="form-group">
                    <label>Employee ID <span className="required">*</span></label>
                    <input type="text" name="employee_id" value={formData.employee_id} onChange={handleInputChange} required />
                  </div>
                  <div className="form-group">
                    <label>Department <span className="required">*</span></label>
                    <ComboBox
                      name="department"
                      value={formData.department}
                      onChange={handleInputChange}
                      options={departments}
                      placeholder="Type or select department"
                      required
                    />
                  </div>
                </>
              )}

              {/* Common fields */}
              <div className="form-group col-span-2">
                <label>Address <span className="required">*</span></label>
                <input type="text" name="address" value={formData.address} onChange={handleInputChange} required />
              </div>

              <div className="form-group">
                <label>Contact Number/s <span className="required">*</span></label>
                <input type="text" name="contact_number" value={formData.contact_number} onChange={handleInputChange} required />
              </div>

              <div className="form-group">
                <label>Age</label>
                <input type="number" name="age" min="15" max="99" value={formData.age} onChange={handleInputChange} />
              </div>

              {!isEbike && (
                <div className="form-group col-span-2">
                  <label>Driver's License Number <span className="required">*</span></label>
                  <input type="text" name="drivers_license" value={formData.drivers_license} onChange={handleInputChange} required={!isEbike} />
                </div>
              )}

              {/* Campus day selector — students only */}
              {isStudent && (
                <div className="form-group col-span-2">
                  <label className="days-label">
                    Campus Days <span className="required">*</span>
                  </label>
                  <div className="schedule-note">
                    <Info size={13} />
                    <span>
                      Schedules are <strong>first come, first serve</strong>. Each day has a limited number of slots.
                      Select up to <strong>3 days</strong>. Days that are <strong>full</strong> cannot be selected.
                    </span>
                  </div>
                  <div className="campus-day-picker campus-day-picker--per-day">
                    {CAMPUS_DAYS.map(day => {
                      const slot         = scheduleSlots?.[day.key]
                      const isFull       = slot?.available === 0
                      const isSelected   = formData.campus_days.includes(day.key)
                      const limitReached = formData.campus_days.length >= 3 && !isSelected
                      const isDisabled   = isFull || limitReached
                      return (
                        <button
                          key={day.key}
                          type="button"
                          className={[
                            'campus-day-btn campus-day-btn--per-day',
                            isSelected   ? 'campus-day-btn--selected' : '',
                            isFull       ? 'campus-day-btn--full'     : '',
                            limitReached && !isFull ? 'campus-day-btn--limit' : '',
                          ].filter(Boolean).join(' ')}
                          onClick={() => !isDisabled && toggleDay(day.key)}
                          disabled={isDisabled}
                          aria-pressed={isSelected}
                          title={isFull ? `${day.key} is full` : limitReached ? 'Maximum 3 days selected' : day.key}
                        >
                          <span className="campus-day-short">{day.short}</span>
                          <span className="campus-day-slots">
                            {loadingSlots
                              ? '···'
                              : slot
                                ? (isFull ? 'FULL' : `${slot.available} left`)
                                : '—'}
                          </span>
                        </button>
                      )
                    })}
                  </div>
                  <div className="campus-day-summary">
                    <span className="campus-day-counter">
                      {formData.campus_days.length}/3 days selected
                      {formData.campus_days.length === 3 && ' — maximum reached'}
                    </span>
                    {formData.campus_days.length > 0 && scheduleSlots && (
                      <div className="campus-day-selected-list">
                        {formData.campus_days.map(d => {
                          const slot = scheduleSlots[d]
                          return (
                            <span key={d} className="campus-day-selected-chip">
                              {d.slice(0, 3)}
                              {slot && (
                                <em>{slot.available} slot{slot.available !== 1 ? 's' : ''} left</em>
                              )}
                            </span>
                          )
                        })}
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Employee — all days, no picker */}
              {isEmployee && (
                <div className="form-group col-span-2">
                  <p className="campus-day-anyday-note">
                    <Info size={13} />
                    Employees are permitted to enter and park on any day of the week.
                  </p>
                </div>
              )}

              {/* Fetcher — all days but time-limited */}
              {isFetcher && (
                <div className="form-group col-span-2">
                  <p className="campus-day-anyday-note fetcher-note">
                    <Info size={13} />
                    Fetchers / Drop &amp; Go may enter on <strong>any day</strong> during designated drop-off and pick-up hours only.
                    Entry outside these hours will be restricted.
                  </p>
                </div>
              )}
            </div>

            <hr className="divider" />

            {/* ── Terms & Consent ── */}
            <div className="terms-section">
              <h3 className="terms-heading">Terms &amp; Conditions</h3>
              <div className="terms-box">
                <p className="terms-bold">
                  I agree and promise to abide by the terms and conditions anent my application for a vehicle pass.
                </p>
                <ul className="terms-list">
                  <li>
                    I understand that the vehicle pass is intended <strong>ONLY TO ALLOW THE ENTRY OF MY VEHICLE IN THE CAMPUS</strong>. The College does not guarantee the availability of parking spaces;
                  </li>
                  <li>The application for a vehicle pass is subject to the approval or disapproval of the Student Affairs Office;</li>
                  <li>To pay the Vehicle Pass fee of <strong>₱300.00</strong> at the <strong>Accounting Office</strong> and present the Official Receipt (OR) at the CDSO Office.</li>
                  <li>As a responsible individual, I promise to:</li>
                </ul>
                <ol className="terms-alpha-list" type="a">
                  <li>deactivate vehicle alarm while it is parked within the school premises;</li>
                  <li>see to it that my vehicle pass is placed on the dashboard, driver side, upon entry and during the entire stay inside the campus;</li>
                  <li><strong>recognize the right of the school to decline the entry of my vehicle if the parking area is full;</strong></li>
                  <li>be courteous to the school security and personnel and fellow parking space users;</li>
                  <li>allow the school security team to inspect my vehicle, as the need arises, before entry and when inside the campus;</li>
                  <li>strictly observe the speed limit of 10 kph within the campus;</li>
                  <li>park my vehicle at the designated parking area only so as not to obstruct the flow of traffic inside the campus. <strong>"NO DOUBLE PARKING"</strong>;</li>
                  <li>not stay inside my vehicle while the engine is on and parked for safety and environmental reasons;</li>
                  <li>observe the <strong>"No blowing of horn inside the campus"</strong> policy;</li>
                  <li>avoid playing loud music or making unnecessary sounds using my vehicle upon entry;</li>
                  <li>strictly observe the <strong>"No Smoking"</strong> policy of the Institution. <strong>Using e-cigarettes and/or vapes is not allowed</strong>;</li>
                  <li>properly lock and secure my vehicle while inside the campus as the College Administration is <strong>NOT LIABLE</strong> for anything that may happen to the vehicle while it is parked inside the campus;</li>
                  <li>strictly observe traffic and/or coding scheme imposed;</li>
                  <li>
                    follow the above terms and conditions and any violation committed thereto would subject me to the following sanctions:
                    <div className="sanctions-grid">
                      <span className="sanction-label">First Offense:</span>
                      <span>Restriction of vehicle pass for one (1) week.</span>
                      <span className="sanction-label">Second Offense:</span>
                      <span>Restriction of vehicle pass for two (2) weeks.</span>
                      <span className="sanction-label">Third Offense:</span>
                      <span>Restriction of vehicle pass. Prohibition in securing a vehicle pass for the next school year.</span>
                    </div>
                  </li>
                </ol>
              </div>

              <div className="consent-section">
                <label className="consent-label">
                  <input
                    type="checkbox"
                    name="privacy_consent"
                    checked={formData.privacy_consent}
                    onChange={handleInputChange}
                    required
                    className="consent-checkbox"
                  />
                  <span>
                    <strong>DATA PRIVACY CONSENT:</strong> By filling-out this form, I give my consent to SLC's collection, processing, storage and retention, and disposal of the provided information pursuant to the provisions of Republic Act No. 10173 or the Data Privacy Act of 2012. I also hereby certify that all information given are true and correct.
                  </span>
                </label>
              </div>
            </div>

            <div className="form-actions">
              <button type="submit" className="btn-submit" disabled={submitting || loading}>
                {submitting ? 'Submitting...' : 'Submit Registration'}
              </button>
            </div>
          </form>
        </div>
      </main>
    </div>
  )
}
