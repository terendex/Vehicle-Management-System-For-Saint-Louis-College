import { useState, useEffect, useCallback } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { CheckCircle, AlertTriangle, Car } from 'lucide-react'
import { registrationApi } from '../../api/registration'
import slcLogo from '../../assets/slclogo.jpg'
import './RegisterPage.css'

const DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']

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
  const navigate = useNavigate()

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [registrantType, setRegistrantType] = useState(null)
  const [submitted, setSubmitted] = useState(false)
  const [submitting, setSubmitting] = useState(false)

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

  const validateToken = useCallback(async () => {
    if (!token) {
      setError('Invalid registration link. Token is missing.')
      setLoading(false)
      return
    }
    try {
      const data = await registrationApi.validateToken(token)
      setRegistrantType(data.registrant_type)
      setLoading(false)
    } catch (err) {
      setError(
        err.response?.data?.error ||
        'This registration link is invalid, expired, or has already been used.'
      )
      setLoading(false)
    }
  }, [token])

  useEffect(() => {
    validateToken()
  }, [validateToken])

  const handleInputChange = (e) => {
    const { name, value, type, checked } = e.target
    if (type === 'checkbox' && name !== 'privacy_consent') {
      // handled by day toggle
    } else if (type === 'checkbox') {
      setFormData((prev) => ({ ...prev, [name]: checked }))
    } else {
      setFormData((prev) => ({ ...prev, [name]: value }))
    }
  }

  const toggleDay = (day) => {
    setFormData((prev) => {
      const days = prev.campus_days
      if (days.includes(day)) {
        return { ...prev, campus_days: days.filter((d) => d !== day) }
      }
      if (days.length >= 3) return prev
      return { ...prev, campus_days: [...days, day] }
    })
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!formData.privacy_consent) {
      alert('You must agree to the Data Privacy Consent.')
      return
    }
    if (registrantType === 'student' && formData.campus_days.length !== 3) {
      alert('Please select exactly 3 campus days.')
      return
    }
    setSubmitting(true)
    try {
      await registrationApi.submitRegistration(token, {
        ...formData,
        registrant_type: registrantType,
      })
      setSubmitted(true)
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
              Please contact the administration office for a new registration link.
            </p>
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
              Your vehicle registration application has been submitted successfully and is pending
              review.
            </p>
            <p className="card-help">
              You will receive an email notification once your application has been processed.
            </p>
            <button className="card-btn" onClick={() => navigate('/login')}>
              Proceed to Login
            </button>
          </div>
        </main>
      </div>
    )
  }

  const isStudent = registrantType === 'student'

  /* ─── Form ─── */
  return (
    <div className="register-page">
      {SLC_HEADER}

      <main className="register-main">
        <div className="register-card">
          {/* Official form title */}
          <div className="slc-form-title-block">
            <h1 className="slc-form-title">APPLICATION FORM FOR A VEHICLE PASS</h1>
            <p className="slc-form-subtitle">
              {isStudent ? "STUDENT'S PERSONAL INFORMATION" : "EMPLOYEE'S PERSONAL INFORMATION"}
            </p>
            <p className="slc-form-note">Please write legibly in CAPITAL LETTERS.</p>
            <span className="registrant-badge">
              {isStudent ? 'Student Registration' : 'Employee Registration'}
            </span>
          </div>

          <form onSubmit={handleSubmit} className="register-form">
            {/* ── Personal Information ── */}
            <h3 className="section-heading">Personal Information</h3>
            <div className="form-grid">

              {/* Full Name — always first */}
              <div className="form-group col-span-2">
                <label>
                  Full Name <span className="required">*</span>
                </label>
                <input
                  type="text"
                  name="full_name"
                  value={formData.full_name}
                  onChange={handleInputChange}
                  required
                  placeholder="Last Name, First Name M.I."
                />
              </div>

              {/* Email — needed for account creation */}
              <div className="form-group col-span-2">
                <label>
                  Email Address <span className="required">*</span>
                </label>
                <input
                  type="email"
                  name="email"
                  value={formData.email}
                  onChange={handleInputChange}
                  required
                />
              </div>

              {/* Student-specific */}
              {isStudent ? (
                <>
                  <div className="form-group">
                    <label>
                      Student ID Number <span className="required">*</span>
                    </label>
                    <input
                      type="text"
                      name="student_id"
                      value={formData.student_id}
                      onChange={handleInputChange}
                      required
                    />
                  </div>
                  <div className="form-group">
                    <label>
                      Program &amp; Year Level <span className="required">*</span>
                    </label>
                    <input
                      type="text"
                      name="program_year"
                      value={formData.program_year}
                      onChange={handleInputChange}
                      required
                      placeholder="e.g. BSIT - 3"
                    />
                  </div>
                </>
              ) : (
                /* Employee-specific */
                <>
                  <div className="form-group">
                    <label>
                      Employee ID <span className="required">*</span>
                    </label>
                    <input
                      type="text"
                      name="employee_id"
                      value={formData.employee_id}
                      onChange={handleInputChange}
                      required
                    />
                  </div>
                  <div className="form-group">
                    <label>
                      Department <span className="required">*</span>
                    </label>
                    <input
                      type="text"
                      name="department"
                      value={formData.department}
                      onChange={handleInputChange}
                      required
                    />
                  </div>
                </>
              )}

              {/* Common fields */}
              <div className="form-group col-span-2">
                <label>
                  Address <span className="required">*</span>
                </label>
                <input
                  type="text"
                  name="address"
                  value={formData.address}
                  onChange={handleInputChange}
                  required
                />
              </div>

              <div className="form-group">
                <label>
                  Contact Number/s <span className="required">*</span>
                </label>
                <input
                  type="text"
                  name="contact_number"
                  value={formData.contact_number}
                  onChange={handleInputChange}
                  required
                />
              </div>

              <div className="form-group">
                <label>Age</label>
                <input
                  type="number"
                  name="age"
                  min="15"
                  max="99"
                  value={formData.age}
                  onChange={handleInputChange}
                />
              </div>

              <div className="form-group col-span-2">
                <label>
                  Driver's License Number <span className="required">*</span>
                </label>
                <input
                  type="text"
                  name="drivers_license"
                  value={formData.drivers_license}
                  onChange={handleInputChange}
                  required
                />
              </div>

              {/* Campus Days — Students only */}
              {isStudent && (
                <div className="form-group col-span-2">
                  <label className="days-label">
                    Please identify the days for your vehicle's entry to the campus.
                  </label>
                  <p className="days-instruction">
                    Please encircle three (3) days only.{' '}
                    <span className="days-count">
                      {formData.campus_days.length}/3 selected
                    </span>
                  </p>
                  <div className="day-picker">
                    {DAYS.map((day) => {
                      const selected = formData.campus_days.includes(day)
                      const maxed = !selected && formData.campus_days.length >= 3
                      return (
                        <button
                          key={day}
                          type="button"
                          className={`day-pill${selected ? ' day-pill--selected' : ''}${maxed ? ' day-pill--disabled' : ''}`}
                          onClick={() => toggleDay(day)}
                          disabled={maxed}
                          aria-pressed={selected}
                        >
                          {day}
                        </button>
                      )
                    })}
                  </div>
                </div>
              )}
            </div>

            <hr className="divider" />

            {/* ── Vehicle Identification ── */}
            <h3 className="section-heading">Vehicle Identification</h3>
            <div className="form-grid">
              <div className="form-group">
                <label>
                  Plate Number <span className="required">*</span>
                </label>
                <input
                  type="text"
                  name="plate_number"
                  value={formData.plate_number}
                  onChange={handleInputChange}
                  required
                />
              </div>

              <div className="form-group">
                <label>
                  Conduction Number{' '}
                  <span className="field-note">(for newly purchased vehicles)</span>
                </label>
                <input
                  type="text"
                  name="conduction_number"
                  value={formData.conduction_number}
                  onChange={handleInputChange}
                />
              </div>

              <div className="form-group">
                <label>
                  Vehicle Type <span className="required">*</span>
                </label>
                <select
                  name="vehicle_type"
                  value={formData.vehicle_type}
                  onChange={handleInputChange}
                  required
                >
                  <option value="">Select Type</option>
                  <option value="Sedan">Sedan</option>
                  <option value="SUV">SUV</option>
                  <option value="Motorcycle">Motorcycle</option>
                  <option value="Tricycle">Tricycle</option>
                  <option value="Van">Van</option>
                  <option value="Truck">Truck</option>
                  <option value="Other">Other</option>
                </select>
              </div>

              <div className="form-group">
                <label>
                  Vehicle Color <span className="required">*</span>
                </label>
                <input
                  type="text"
                  name="vehicle_color"
                  value={formData.vehicle_color}
                  onChange={handleInputChange}
                  required
                />
              </div>

              <div className="form-group col-span-2">
                <label>
                  Body Number{' '}
                  <span className="field-note">(for tricycle only)</span>
                </label>
                <input
                  type="text"
                  name="body_number"
                  value={formData.body_number}
                  onChange={handleInputChange}
                  disabled={formData.vehicle_type !== 'Tricycle'}
                  placeholder={
                    formData.vehicle_type !== 'Tricycle' ? 'Only applicable for Tricycle' : ''
                  }
                />
              </div>
            </div>

            <hr className="divider" />

            {/* ── Terms & Consent ── */}
            <div className="terms-section">
              <h3 className="terms-heading">Terms &amp; Conditions</h3>
              <div className="terms-box">
                <p className="terms-bold">
                  I agree and promise to abide by the terms and conditions anent my application for
                  a vehicle pass.
                </p>
                <ul className="terms-list">
                  <li>
                    I understand that the vehicle pass is intended <strong>ONLY TO ALLOW THE ENTRY
                      OF MY VEHICLE IN THE CAMPUS</strong>. The College does not guarantee the availability
                    of parking spaces;
                  </li>
                  <li>
                    The application for a vehicle pass is subject to the approval or disapproval of
                    the Student Affairs Office;
                  </li>
                  <li>To pay the Vehicle Pass fee of ₱350.00 at the Accounting Office.</li>
                  <li>As a responsible individual, I promise to:</li>
                </ul>
                <ol className="terms-alpha-list" type="a">
                  <li>deactivate vehicle alarm while it is parked within the school premises;</li>
                  <li>
                    see to it that my vehicle pass is placed on the dashboard, driver side, upon
                    entry and during the entire stay inside the campus;
                  </li>
                  <li>
                    <strong>
                      recognize the right of the school to decline the entry of my vehicle if the
                      parking area is full;
                    </strong>
                  </li>
                  <li>
                    be courteous to the school security and personnel and fellow parking space users;
                  </li>
                  <li>
                    allow the school security team to inspect my vehicle, as the need arises, before
                    entry and when inside the campus;
                  </li>
                  <li>strictly observe the speed limit of 10 kph within the campus;</li>
                  <li>
                    park my vehicle at the designated parking area only so as not to obstruct the
                    flow of traffic inside the campus. <strong>"NO DOUBLE PARKING"</strong>;
                  </li>
                  <li>
                    not stay inside my vehicle while the engine is on and parked for safety and
                    environmental reasons;
                  </li>
                  <li>
                    observe the <strong>"No blowing of horn inside the campus"</strong> policy;
                  </li>
                  <li>
                    avoid playing loud music or making unnecessary sounds using my vehicle upon
                    entry;
                  </li>
                  <li>
                    strictly observe the <strong>"No Smoking"</strong> policy of the Institution.{' '}
                    <strong>Using e-cigarettes and/or vapes is not allowed</strong>;
                  </li>
                  <li>
                    properly lock and secure my vehicle while inside the campus as the College
                    Administration is <strong>NOT LIABLE</strong> for anything that may happen to the
                    vehicle while it is parked inside the campus;
                  </li>
                  <li>strictly observe traffic and/or coding scheme imposed;</li>
                  <li>
                    follow the above terms and conditions and any violation committed thereto would
                    subject me to the following sanctions:
                    <div className="sanctions-grid">
                      <span className="sanction-label">First Offense:</span>
                      <span>Restriction of vehicle pass for one (1) week.</span>
                      <span className="sanction-label">Second Offense:</span>
                      <span>Restriction of vehicle pass for two (2) weeks.</span>
                      <span className="sanction-label">Third Offense:</span>
                      <span>
                        Restriction of vehicle pass. Prohibition in securing a vehicle pass for
                        the next school year.
                      </span>
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
                    <strong>DATA PRIVACY CONSENT:</strong> By filling-out this form, I give my
                    consent to SLC's collection, processing, storage and retention, and disposal of
                    the provided information pursuant to the provisions of Republic Act No. 10173 or
                    the Data Privacy Act of 2012. I also hereby certify that all information given
                    are true and correct.
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
