import { useState, useEffect, useCallback } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { CheckCircle, AlertTriangle, Car, Info, Banknote, User, Users, ChevronRight, Mail, Clock, ArrowRight } from 'lucide-react'

import { registrationApi } from '../../api/registration'

function formatRegDate(iso) {
  if (!iso) return null
  try {
    return new Date(iso + 'T00:00:00').toLocaleDateString('en-US', { month: 'long', day: 'numeric' })
  } catch { return iso }
}

// Auto-formats plate as the user types. Only inserts a space for the common
// 2-3 letter prefix + digit patterns (e.g. ABC1234 → ABC 1234, AB1234 → AB 1234).
// Other formats (N123BC, 123ABC, 1234) are left as-is since they have no standard separator.
function formatPlateNumber(raw) {
  const upper = raw.toUpperCase().replace(/[^A-Z0-9\s-]/g, '')
  // Only auto-insert space if the user hasn't already typed one
  if (!/[\s-]/.test(upper)) {
    const m = upper.match(/^([A-Z]{2,3})(\d.*)$/)
    if (m) return m[1] + ' ' + m[2]
  }
  return upper
}

// Auto-inserts dashes for the LTO format: X00-00-000000.
// Strips any existing dashes first so the cursor position doesn't confuse things.
function formatDriversLicense(raw) {
  const clean = raw.replace(/[^A-Za-z0-9]/g, '').toUpperCase().slice(0, 11)
  if (clean.length <= 3) return clean
  if (clean.length <= 5) return `${clean.slice(0, 3)}-${clean.slice(3)}`
  return `${clean.slice(0, 3)}-${clean.slice(3, 5)}-${clean.slice(5)}`
}

const REGISTRATION_TYPES = [
  {
    id: 'student',
    icon: <User size={22} />,
    label: 'Student — Vehicle',
    description: 'Registered SLC student with a car or motorcycle',
  },
  {
    id: 'employee',
    icon: <Car size={22} />,
    label: 'Employee',
    description: 'SLC faculty or staff member',
  },
  {
    id: 'fetcher',
    icon: <Users size={22} />,
    label: 'Fetcher / Drop & Go',
    description: 'Parent or guardian fetching a student',
  },
]
import ComboBox from '../../components/ComboBox'
import slcLogo from '../../assets/slclogo.jpg'
import './RegisterPage.css'

const CAMPUS_DAYS = [
  { key: 'Monday', short: 'Mon' },
  { key: 'Tuesday', short: 'Tue' },
  { key: 'Wednesday', short: 'Wed' },
  { key: 'Thursday', short: 'Thu' },
  { key: 'Friday', short: 'Fri' },
  { key: 'Saturday', short: 'Sat' },
]


const SLC_HEADER = (
  <header className="register-header">
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

export default function RegisterPage() {
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token')
  const directType = searchParams.get('type') // 'student' | 'employee' | 'fetcher'
  const navigate = useNavigate()

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [registrantType, setRegistrantType] = useState(null)
  const [submitted, setSubmitted] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState(null)
  const [showPaymentPopup, setShowPaymentPopup] = useState(false)
  const [regStatus, setRegStatus] = useState(null)
  const [regStatusLoading, setRegStatusLoading] = useState(false)
  const [formErrors, setFormErrors] = useState({})

  // Schedule slots & reference lists
  const [scheduleSlots, setScheduleSlots] = useState(null)
  const [loadingSlots, setLoadingSlots] = useState(false)
  const [departments, setDepartments] = useState([])
  const [programs, setPrograms] = useState([])

  // Philippine address cascading dropdowns
  const [provinces, setProvinces] = useState([])
  const [cities, setCities] = useState([])
  const [barangays, setBarangays] = useState([])
  const [loadingCities, setLoadingCities] = useState(false)
  const [loadingBarangays, setLoadingBarangays] = useState(false)
  const [selectedProvinceCode, setSelectedProvinceCode] = useState('')
  const [selectedCityCode, setSelectedCityCode] = useState('')

  const [formData, setFormData] = useState({
    last_name: '',
    first_name: '',
    middle_name: '',
    email: '',
    student_id: '',
    student_level: '',
    student_strand: '',
    student_grade: '',
    program_year: '',
    employee_id: '',
    department: '',
    house_street: '',
    barangay: '',
    city_municipality: '',
    province: '',
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

  const fetchRegStatus = useCallback(async () => {
    setRegStatusLoading(true)
    try {
      const status = await registrationApi.getRegistrationStatus()
      setRegStatus(status)
    } catch {
      setRegStatus({ is_open: false, open_date: 'TBA', close_date: 'TBA' })
    } finally {
      setRegStatusLoading(false)
    }
  }, [])

  const validateToken = useCallback(async () => {
    fetchRefLists()

    if (directType) {
      setRegistrantType(directType)
      fetchRegStatus()
      setLoading(false)
      if (directType === 'student') fetchScheduleSlots()
      return
    }

    if (!token) {
      fetchRegStatus()
      setLoading(false)
      return
    }

    try {
      const data = await registrationApi.validateToken(token)
      setRegistrantType(data.registrant_type)
      setLoading(false)
      fetchScheduleSlots()
    } catch (err) {
      setError(
        err.response?.data?.error ||
        'This registration link is invalid, expired, or has already been used.'
      )
      setLoading(false)
    }
  }, [token, directType, fetchScheduleSlots, fetchRefLists, fetchRegStatus])

  useEffect(() => {
    validateToken()
  }, [validateToken])

  // Fetch provinces once on mount
  useEffect(() => {
    fetch('https://psgc.gitlab.io/api/provinces/')
      .then(r => r.json())
      .then(data => setProvinces(data.sort((a, b) => a.name.localeCompare(b.name))))
      .catch(() => {})
  }, [])

  // Fetch cities/municipalities when province changes
  useEffect(() => {
    if (!selectedProvinceCode) { setCities([]); setBarangays([]); return }
    setLoadingCities(true)
    setCities([])
    setBarangays([])
    fetch(`https://psgc.gitlab.io/api/provinces/${selectedProvinceCode}/cities-municipalities/`)
      .then(r => r.json())
      .then(data => setCities(data.sort((a, b) => a.name.localeCompare(b.name))))
      .catch(() => {})
      .finally(() => setLoadingCities(false))
  }, [selectedProvinceCode])

  // Fetch barangays when city/municipality changes
  useEffect(() => {
    if (!selectedCityCode) { setBarangays([]); return }
    setLoadingBarangays(true)
    setBarangays([])
    fetch(`https://psgc.gitlab.io/api/cities-municipalities/${selectedCityCode}/barangays/`)
      .then(r => r.json())
      .then(data => setBarangays(data.sort((a, b) => a.name.localeCompare(b.name))))
      .catch(() => {})
      .finally(() => setLoadingBarangays(false))
  }, [selectedCityCode])

  const FIELD_PATTERNS = {
    plate_number: {
      // Mirrors PH_PLATE_PATTERNS in backend/scanning/ml/validator.py.
      // Input is normalized the same way: strip spaces/dashes, uppercase.
      validate: (raw) => {
        const n = raw.replace(/[\s\-_]/g, '').toUpperCase()
        if (!n) return false
        return [
          /^[A-Z]{3}\d{4}$/,             // ABC1234  — standard car (post-2014)
          /^[A-Z]{3}\d{3}$/,             // ABC123   — pre-2014 car
          /^\d{3}[A-Z]{3}$/,             // 123ABC
          /^[A-Z]\d{3}[A-Z]{2}$/,        // N123BC
          /^[A-Z]{2}\d{3}[A-Z]$/,        // NB123C
          /^[A-Z]\d{4}[A-Z]$/,           // N1234C
          /^[A-Z]{1,2}\d{4}[A-Z]{1,2}$/, // AB1234C / A1234BC
          /^[A-Z]{1,2}\d{3}[A-Z]{1,2}$/, // AB123C  / A123BC
          /^\d{7}$/,                      // 0011234  — diplomatic
          /^[A-Z]{2}\d{4}$/,             // AB1234   — motorcycle
          /^[A-Z]{2}\d{5}$/,             // AB12345
          /^\d{3}[A-Z]{1,3}$/,           // 123AB
          /^\d{2}[A-Z]{3,4}$/,           // 12ABCD
          /^\d{4}$/,                      // 1234     — old motorcycle
          /^\d{1,3}[A-Z]{2,4}\d{0,2}$/,
          /^[A-Z]{1,3}\d{1,6}$/,
        ].some(p => p.test(n))
      },
      message: 'Invalid Philippine plate number format',
      hint: 'e.g. ABC 1234 · AB 1234 · N123BC · ABC123',
    },
    email: {
      regex: /^[^\s@]+@[^\s@]+\.[^\s@]+$/,
      message: 'Invalid email address format',
      hint: 'e.g. juan@example.com',
    },
    conduction_number: {
      regex: /^[A-Z0-9]{5,12}$/i,
      message: 'Invalid conduction number. Use 5–12 alphanumeric characters.',
      hint: 'e.g. CS12345A678',
    },
    contact_number: {
      regex: /^\+639\d{9}$/,
      message: 'Invalid number. Use +639XXXXXXXXX',
      hint: 'e.g. +639XXXXXXXXX',
    },
    drivers_license: {
      // LTO format: 1 office letter + 2-digit district + dash + 2-digit year + dash + 6-digit serial
      // e.g. N01-20-123456  (Non-prof, district 01, year 2020, serial 123456)
      regex: /^[A-Z]\d{2}-\d{2}-\d{6}$/i,
      message: 'Invalid LTO license number. Use format: N01-20-123456',
      hint: 'e.g. N01-20-123456',
    },
    student_id: {
      regex: /^\d{8}$/,
      message: 'Invalid student ID. Must be 8 digits (e.g. 23100174)',
      hint: 'e.g. 23100174',
    },
    employee_id: {
      regex: /^\d{8}$/,
      message: 'Invalid employee ID. Must be 8 digits (e.g. 23100174)',
      hint: 'e.g. 23100174',
    },
  }

  const validateField = (name, value) => {
    if (!value || !value.trim()) return null
    const rule = FIELD_PATTERNS[name]
    if (!rule) return null
    const valid = typeof rule.validate === 'function'
      ? rule.validate(value.trim())
      : rule.regex.test(value.trim())
    return valid ? null : rule.message
  }

  const handleInputChange = (e) => {
    const { name, value, type, checked } = e.target
    if (type === 'checkbox') {
      setFormData((prev) => ({ ...prev, [name]: checked }))
    } else {
      let formatted = value
      if (name === 'plate_number') formatted = formatPlateNumber(value)
      else if (name === 'drivers_license') formatted = formatDriversLicense(value)
      else if (['last_name', 'first_name', 'middle_name', 'vehicle_color'].includes(name))
        formatted = formatted.toUpperCase()
      setFormData((prev) => ({ ...prev, [name]: formatted }))
      const errorMsg = validateField(name, formatted)
      setFormErrors((prev) => ({ ...prev, [name]: errorMsg }))
      setSubmitError(null)
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

    setSubmitError(null)

    if (!registrantType) {
      setSubmitError('Please select your registrant type.')
      return
    }
    if (regStatus && !regStatus.is_open) {
      setSubmitError('Registration is currently closed. Please try again during the registration window.')
      return
    }

    // Run all format validations before submitting
    const fieldsToValidate = ['email', 'plate_number', 'conduction_number', 'contact_number', 'drivers_license', 'student_id', 'employee_id']
    const newErrors = {}
    fieldsToValidate.forEach(name => {
      const err = validateField(name, formData[name])
      if (err) newErrors[name] = err
    })
    if (Object.keys(newErrors).length > 0) {
      setFormErrors(prev => ({ ...prev, ...newErrors }))
      setSubmitError('Please fix the format errors highlighted in the form before submitting.')
      return
    }

    if (!formData.privacy_consent) {
      setSubmitError('You must agree to the Data Privacy Consent before submitting.')
      return
    }
    if (registrantType === 'student' && formData.campus_days.length === 0) {
      setSubmitError('Please select at least one campus day.')
      return
    }
    if (registrantType === 'student' && formData.student_level !== 'sped' && formData.campus_days.length > 3) {
      setSubmitError('You may only select up to 3 campus days.')
      return
    }

    if (registrantType === 'student' && scheduleSlots) {
      const fullDays = formData.campus_days.filter(d => scheduleSlots[d]?.available === 0)
      if (fullDays.length > 0) {
        setSubmitError(`The following day(s) are full: ${fullDays.join(', ')}. Please deselect them and try again.`)
        return
      }
    }

    setSubmitting(true)
    try {
      const full_name = [formData.last_name, formData.first_name, formData.middle_name]
        .map(s => s.trim()).filter(Boolean).join(', ')
      const address = [formData.house_street, formData.barangay, formData.city_municipality, formData.province]
        .map(s => s.trim()).filter(Boolean).join(', ')

      // Compose program_year from level-specific fields for non-college students
      let program_year = formData.program_year
      if (registrantType === 'student' && formData.student_level !== 'college') {
        const grade = formData.student_grade ? `Grade ${formData.student_grade}` : ''
        if (formData.student_level === 'shs') {
          program_year = ['SHS', formData.student_strand, grade].filter(Boolean).join(' - ')
        } else if (formData.student_level === 'jhs') {
          program_year = ['JHS', grade].filter(Boolean).join(' - ')
        } else if (formData.student_level === 'elementary') {
          program_year = ['Elementary', grade].filter(Boolean).join(' - ')
        } else if (formData.student_level === 'sped') {
          program_year = formData.student_grade ? `SpEd - Grade ${formData.student_grade}` : 'SpEd'
        }
      }

      const payload = { ...formData, full_name, address, program_year, registrant_type: registrantType }

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
      const errData = err.response?.data
      const msg = errData?.error
        || (typeof errData === 'object' ? Object.entries(errData).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(', ') : v}`).join(' | ') : null)
        || 'Failed to submit registration. Please try again.'
      setSubmitError(msg)
      console.error('Registration error:', errData || err)
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

  /* ─── Type Selector (no token, no directType) ─── */
  if (!registrantType) {
    return (
      <div className="register-page">
        {SLC_HEADER}
        <main className="register-main">
          <div className="register-card reg-type-selector-card">
            <div className="reg-type-selector-header">
              <h2 className="reg-type-selector-title">Vehicle Pass Application</h2>
              <p className="reg-type-selector-subtitle">Select your registrant type to begin</p>
            </div>

            {regStatusLoading ? (
              <div className="reg-status-loading">Checking registration status…</div>
            ) : regStatus ? (
              <div className={`reg-window-notice ${regStatus.is_open ? 'open' : 'closed'}`}>
                <div className="reg-window-indicator" />
                <div className="reg-window-body">
                  <div className="reg-window-status-row">
                    {regStatus.is_open ? <CheckCircle size={13} /> : <AlertTriangle size={13} />}
                    <span>{regStatus.is_open ? 'Registration is currently open' : 'Registration is currently closed'}</span>
                  </div>
                  <div className="reg-window-dates">
                    {(() => {
                      const start = formatRegDate(regStatus.open_date)
                      const end   = formatRegDate(regStatus.close_date)
                      const range = start && end
                        ? <span className="reg-window-range">{start} – {end}</span>
                        : <span className="reg-window-range reg-window-range--tentative">June 1 <em>(tentative)</em> – October 31 <em>(tentative)</em></span>
                      return regStatus.is_open
                        ? <>Window: {range}</>
                        : <>Registration window: {range}. Submissions are not accepted outside the registration period.</>
                    })()}
                  </div>
                </div>
              </div>
            ) : null}

            <div className="reg-type-list">
              {REGISTRATION_TYPES.map(t => (
                <button
                  key={t.id}
                  className="reg-type-item"
                  onClick={() => navigate(`/register?type=${t.id}`)}
                >
                  <div className="reg-type-icon">{t.icon}</div>
                  <div className="reg-type-text">
                    <span className="reg-type-label">{t.label}</span>
                    <span className="reg-type-desc">{t.description}</span>
                  </div>
                  <ChevronRight size={16} className="reg-type-arrow" />
                </button>
              ))}
            </div>

            <p className="reg-modal-note">
              Registration opens 2 months before the school year and closes during the first semester.
              Dates are tentative and subject to change.
            </p>

            <button className="reg-back-btn" onClick={() => navigate('/login')}>
              Back to Login
            </button>
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

            {/* Email sent notice */}
            <div className="popup-email-sent">
              <Mail size={16} />
              <span>A confirmation email was sent to <strong>{formData.email}</strong></span>
            </div>

            <div className="card-icon payment-popup-icon">
              <Banknote size={44} />
            </div>
            <h2 className="card-title" style={{ color: '#D97706' }}>Action Required</h2>
            <p className="payment-popup-intro">
              Your application is <strong>pending review</strong>. Complete these steps to get your registration processed:
            </p>

            <div className="payment-steps">
              <div className="payment-step">
                <div className="payment-step-num">1</div>
                <div className="payment-step-text">
                  <strong>Pay ₱300.00</strong> at the <strong>Accounting Office</strong> for your Vehicle Pass.
                </div>
              </div>
              <div className="payment-step">
                <div className="payment-step-num">2</div>
                <div className="payment-step-text">
                  Present your <strong>Official Receipt (OR)</strong> at the <strong>CDSO Office</strong> for processing.
                </div>
              </div>
              <div className="payment-step">
                <div className="payment-step-num">3</div>
                <div className="payment-step-text">
                  <strong>Check your email</strong> — you will be notified once your registration is approved or declined.
                </div>
              </div>
            </div>

            <div className="payment-popup-note">
              <Info size={14} />
              The CDSO office will verify your Official Receipt number before approving your registration.
            </div>

            <button className="card-btn payment-popup-btn" onClick={() => navigate('/login')}>
              I Understand — Continue
              <ArrowRight size={15} />
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

            <div className="success-icon-wrap">
              <CheckCircle size={52} strokeWidth={1.8} />
            </div>

            <h2 className="success-title">Application Submitted!</h2>
            <p className="success-status-line">
              <Clock size={13} />
              Status: <strong>Pending CDSO Review</strong>
            </p>

            {/* Email prompt — main focus */}
            <div className="success-email-prompt">
              <div className="success-email-icon">
                <Mail size={22} />
              </div>
              <div className="success-email-body">
                <p className="success-email-heading">Check your inbox</p>
                <p className="success-email-address">{formData.email}</p>
                <p className="success-email-sub">
                  A confirmation email with your submitted details and reference number has been sent. Check your <strong>spam or junk folder</strong> if you don't see it within a few minutes.
                </p>
              </div>
            </div>

            {/* Compact next steps */}
            <div className="success-next-steps">
              <p className="success-next-heading">What to do next</p>
              <div className="success-next-list">
                <div className="success-next-item">
                  <span className="success-next-num">1</span>
                  <span>Pay <strong>₱300.00</strong> at the <strong>Accounting Office</strong></span>
                </div>
                <div className="success-next-item">
                  <span className="success-next-num">2</span>
                  <span>Bring your <strong>OR</strong> to the <strong>CDSO Office</strong></span>
                </div>
                <div className="success-next-item">
                  <span className="success-next-num">3</span>
                  <span>Watch for an <strong>approval email</strong> with your portal credentials</span>
                </div>
              </div>
            </div>

            <button className="card-btn success-back-btn" onClick={() => navigate('/login')}>
              Back to Login
            </button>
          </div>
        </main>
      </div>
    )
  }

  const isStudent = registrantType === 'student'
  const isFetcher = registrantType === 'fetcher'
  const isEmployee = registrantType === 'employee'
  const regOpen = regStatus?.is_open ?? true

  const TYPE_OPTIONS = [
    { id: 'student',  icon: <User size={24} />, label: 'Student',           desc: 'Registered SLC student' },
    { id: 'employee', icon: <Car size={24} />,  label: 'Employee',          desc: 'SLC faculty or staff' },
    { id: 'fetcher',  icon: <Users size={24} />, label: 'Fetcher / Drop & Go', desc: 'Parent or guardian' },
  ]

  /* ─── Form ─── */
  return (
    <div className="register-page">
      {SLC_HEADER}

      <main className="register-main">
        <div className="register-card">
          <div className="slc-form-title-block">
            <h1 className="slc-form-title">APPLICATION FORM FOR A VEHICLE PASS</h1>
            <p className="slc-form-subtitle">
              {!registrantType
                ? 'VEHICLE PASS APPLICATION'
                : isStudent
                  ? "STUDENT'S PERSONAL INFORMATION"
                  : isEmployee
                    ? "EMPLOYEE'S PERSONAL INFORMATION"
                    : "FETCHER / DROP & GO PERSONAL INFORMATION"}
            </p>
            <p className="slc-form-note">Please write legibly in CAPITAL LETTERS.</p>
            {registrantType && (
              <span className="registrant-badge">
                {isStudent ? 'Student — Vehicle Registration'
                  : isEmployee ? 'Employee Registration'
                    : 'Fetcher / Drop & Go Registration'}
              </span>
            )}
          </div>

          {/* Registration window notice */}
          {regStatusLoading ? (
            <div className="reg-status-loading">Checking registration status…</div>
          ) : regStatus ? (
            <div className={`reg-window-notice ${regStatus.is_open ? 'open' : 'closed'}`}>
              <div className="reg-window-indicator" />
              <div className="reg-window-body">
                <div className="reg-window-status-row">
                  {regStatus.is_open ? <CheckCircle size={13} /> : <AlertTriangle size={13} />}
                  <span>{regStatus.is_open ? 'Registration is currently open' : 'Registration is currently closed'}</span>
                </div>
                <div className="reg-window-dates">
                  {regStatus.is_open
                    ? <>Window: <span className="reg-window-range">{regStatus.open_date} – {regStatus.close_date}</span></>
                    : <>Next window opens approximately on <span className="reg-window-range">{regStatus.open_date}</span>. Submissions are not accepted outside the registration period.</>}
                </div>
              </div>
            </div>
          ) : null}

          <form onSubmit={handleSubmit} className="register-form">

            {/* ── Registrant Type ── */}
            <h3 className="section-heading">Registrant Type</h3>
            <div className="reg-type-inline">
              {TYPE_OPTIONS.map(t => (
                <button
                  key={t.id}
                  type="button"
                  className={`reg-type-inline-btn${registrantType === t.id ? ' selected' : ''}`}
                  onClick={() => {
                    setRegistrantType(t.id)
                    if (t.id === 'student') fetchScheduleSlots()
                  }}
                >
                  <span className="reg-type-inline-icon">{t.icon}</span>
                  <span className="reg-type-inline-label">{t.label}</span>
                  <span className="reg-type-inline-desc">{t.desc}</span>
                </button>
              ))}
            </div>

            {registrantType && <>
            <hr className="divider" />

            {/* ── Vehicle Identification ── */}
            <h3 className="section-heading">Vehicle Identification</h3>
            <div className="form-grid">
              <div className="form-group">
                <label>Plate Number <span className="required">*</span></label>
                <input
                  type="text"
                  name="plate_number"
                  value={formData.plate_number}
                  onChange={handleInputChange}
                  required
                  placeholder={FIELD_PATTERNS.plate_number.hint}
                  className={formErrors.plate_number ? 'input-error' : ''}
                />
                <span className="field-hint">{FIELD_PATTERNS.plate_number.hint}</span>
                {formErrors.plate_number && <span className="field-error-msg">{formErrors.plate_number}</span>}
              </div>

              <div className="form-group">
                <label>Conduction Number <span className="field-note">(for newly purchased vehicles)</span></label>
                <input
                  type="text"
                  name="conduction_number"
                  value={formData.conduction_number}
                  onChange={handleInputChange}
                  placeholder={FIELD_PATTERNS.conduction_number.hint}
                  className={formErrors.conduction_number ? 'input-error' : ''}
                />
                <span className="field-hint">{FIELD_PATTERNS.conduction_number.hint}</span>
                {formErrors.conduction_number && <span className="field-error-msg">{formErrors.conduction_number}</span>}
              </div>

              <div className="form-group">
                <label>Vehicle Type <span className="required">*</span></label>
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

              <div className="form-subsection col-span-2"><span>Name</span></div>

              <div className="form-group">
                <label>Last Name <span className="required">*</span></label>
                <input
                  type="text"
                  name="last_name"
                  value={formData.last_name}
                  onChange={handleInputChange}
                  required
                  placeholder="e.g. Dela Cruz"
                />
              </div>

              <div className="form-group">
                <label>First Name <span className="required">*</span></label>
                <input
                  type="text"
                  name="first_name"
                  value={formData.first_name}
                  onChange={handleInputChange}
                  required
                  placeholder="e.g. Juan"
                />
              </div>

              <div className="form-group col-span-2">
                <label>Middle Name <span className="field-note">(optional)</span></label>
                <input
                  type="text"
                  name="middle_name"
                  value={formData.middle_name}
                  onChange={handleInputChange}
                  placeholder="e.g. Santos"
                />
              </div>

              {/* Address */}
              <div className="form-subsection col-span-2"><span>Address</span></div>

              <div className="form-group col-span-2">
                <label>House / Unit No. &amp; Street <span className="required">*</span></label>
                <input
                  type="text"
                  name="house_street"
                  value={formData.house_street}
                  onChange={handleInputChange}
                  required
                  placeholder="e.g. 123 Rizal Street"
                />
              </div>

              <div className="form-group">
                <label>Province <span className="required">*</span></label>
                <select
                  value={selectedProvinceCode}
                  onChange={e => {
                    const opt = provinces.find(p => p.code === e.target.value)
                    setSelectedProvinceCode(e.target.value)
                    setSelectedCityCode('')
                    setFormData(prev => ({ ...prev, province: opt?.name ?? '', city_municipality: '', barangay: '' }))
                  }}
                  required
                >
                  <option value="">Select Province</option>
                  {provinces.map(p => <option key={p.code} value={p.code}>{p.name}</option>)}
                </select>
              </div>

              <div className="form-group">
                <label>City / Municipality <span className="required">*</span></label>
                <select
                  value={selectedCityCode}
                  onChange={e => {
                    const opt = cities.find(c => c.code === e.target.value)
                    setSelectedCityCode(e.target.value)
                    setFormData(prev => ({ ...prev, city_municipality: opt?.name ?? '', barangay: '' }))
                  }}
                  required
                  disabled={!selectedProvinceCode || loadingCities}
                >
                  <option value="">
                    {loadingCities ? 'Loading…' : !selectedProvinceCode ? 'Select province first' : 'Select City / Municipality'}
                  </option>
                  {cities.map(c => <option key={c.code} value={c.code}>{c.name}</option>)}
                </select>
              </div>

              <div className="form-group">
                <label>Barangay <span className="required">*</span></label>
                <select
                  name="barangay"
                  value={formData.barangay}
                  onChange={e => setFormData(prev => ({ ...prev, barangay: e.target.value }))}
                  required
                  disabled={!selectedCityCode || loadingBarangays}
                >
                  <option value="">
                    {loadingBarangays ? 'Loading…' : !selectedCityCode ? 'Select city first' : 'Select Barangay'}
                  </option>
                  {barangays.map(b => <option key={b.code} value={b.name}>{b.name}</option>)}
                </select>
              </div>

              {/* Other Information */}
              <div className="form-subsection col-span-2"><span>Other Information</span></div>

              <div className="form-group col-span-2">
                <label>Email Address <span className="required">*</span></label>
                <input
                  type="email"
                  name="email"
                  value={formData.email}
                  onChange={handleInputChange}
                  required
                  placeholder={FIELD_PATTERNS.email.hint}
                  className={formErrors.email ? 'input-error' : ''}
                />
                {formErrors.email && <span className="field-error-msg">{formErrors.email}</span>}
              </div>

              {/* Student-specific */}
              {isStudent && (
                <>
                  {/* Education level picker */}
                  <div className="form-group col-span-2">
                    <label>Education Level <span className="required">*</span></label>
                    <div className="student-level-picker">
                      {[
                        { id: 'college',     label: 'College' },
                        { id: 'shs',         label: 'Senior High School' },
                        { id: 'jhs',         label: 'Junior High School' },
                        { id: 'elementary',  label: 'Elementary' },
                        { id: 'sped',        label: 'Special Education' },
                      ].map(lvl => (
                        <button
                          key={lvl.id}
                          type="button"
                          className={`student-level-btn${formData.student_level === lvl.id ? ' active' : ''}`}
                          onClick={() => setFormData(prev => ({
                            ...prev,
                            student_level: lvl.id,
                            student_strand: '',
                            student_grade: '',
                            program_year: '',
                            campus_days: lvl.id === 'sped'
                              ? CAMPUS_DAYS.map(d => d.key)
                              : prev.student_level === 'sped'
                                ? []
                                : prev.campus_days,
                          }))}
                        >
                          {lvl.label}
                        </button>
                      ))}
                    </div>
                  </div>

                  {/* Student ID — always shown once level is picked */}
                  {formData.student_level && (
                    <div className="form-group">
                      <label>Student ID Number <span className="required">*</span></label>
                      <input
                        type="text"
                        name="student_id"
                        value={formData.student_id}
                        onChange={handleInputChange}
                        required
                        placeholder={FIELD_PATTERNS.student_id.hint}
                        className={formErrors.student_id ? 'input-error' : ''}
                      />
                      <span className="field-hint">{FIELD_PATTERNS.student_id.hint}</span>
                      {formErrors.student_id && <span className="field-error-msg">{formErrors.student_id}</span>}
                    </div>
                  )}

                  {/* College: program + year ComboBox */}
                  {formData.student_level === 'college' && (
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
                  )}

                  {/* SHS: strand + grade level */}
                  {formData.student_level === 'shs' && (
                    <div className="form-group">
                      <label>Track / Strand <span className="required">*</span></label>
                      <select name="student_strand" value={formData.student_strand} onChange={handleInputChange} required>
                        <option value="">Select Strand</option>
                        <option value="ABM">ABM</option>
                        <option value="STEM">STEM</option>
                        <option value="HUMSS">HUMSS</option>
                        <option value="ICT">ICT</option>
                        <option value="HE">HE</option>
                      </select>
                    </div>
                  )}

                  {/* SHS grade level (11/12) — second row */}
                  {formData.student_level === 'shs' && (
                    <div className="form-group">
                      <label>Grade Level <span className="required">*</span></label>
                      <select name="student_grade" value={formData.student_grade} onChange={handleInputChange} required>
                        <option value="">Select Grade</option>
                        <option value="11">Grade 11</option>
                        <option value="12">Grade 12</option>
                      </select>
                    </div>
                  )}

                  {/* JHS: grade level (7–10) */}
                  {formData.student_level === 'jhs' && (
                    <div className="form-group">
                      <label>Grade Level <span className="required">*</span></label>
                      <select name="student_grade" value={formData.student_grade} onChange={handleInputChange} required>
                        <option value="">Select Grade</option>
                        <option value="7">Grade 7</option>
                        <option value="8">Grade 8</option>
                        <option value="9">Grade 9</option>
                        <option value="10">Grade 10</option>
                      </select>
                    </div>
                  )}

                  {/* Elementary: grade level (Kinder–6) */}
                  {formData.student_level === 'elementary' && (
                    <div className="form-group">
                      <label>Grade Level <span className="required">*</span></label>
                      <select name="student_grade" value={formData.student_grade} onChange={handleInputChange} required>
                        <option value="">Select Grade</option>
                        <option value="Kinder 1">Kinder 1</option>
                        <option value="Kinder 2">Kinder 2</option>
                        <option value="1">Grade 1</option>
                        <option value="2">Grade 2</option>
                        <option value="3">Grade 3</option>
                        <option value="4">Grade 4</option>
                        <option value="5">Grade 5</option>
                        <option value="6">Grade 6</option>
                      </select>
                    </div>
                  )}

                  {/* SpEd: optional grade level */}
                  {formData.student_level === 'sped' && (
                    <div className="form-group">
                      <label>Grade Level <span style={{ color: '#7C80A3', fontWeight: 400 }}>(optional)</span></label>
                      <select name="student_grade" value={formData.student_grade} onChange={handleInputChange}>
                        <option value="">Not specified</option>
                        <option value="Kinder 1">Kinder 1</option>
                        <option value="Kinder 2">Kinder 2</option>
                        <option value="1">Grade 1</option>
                        <option value="2">Grade 2</option>
                        <option value="3">Grade 3</option>
                        <option value="4">Grade 4</option>
                        <option value="5">Grade 5</option>
                        <option value="6">Grade 6</option>
                        <option value="7">Grade 7</option>
                        <option value="8">Grade 8</option>
                        <option value="9">Grade 9</option>
                        <option value="10">Grade 10</option>
                        <option value="11">Grade 11</option>
                        <option value="12">Grade 12</option>
                      </select>
                    </div>
                  )}
                </>
              )}

              {/* Employee-specific */}
              {isEmployee && (
                <>
                  <div className="form-group">
                    <label>Employee ID <span className="required">*</span></label>
                    <input
                      type="text"
                      name="employee_id"
                      value={formData.employee_id}
                      onChange={handleInputChange}
                      required
                      placeholder={FIELD_PATTERNS.employee_id.hint}
                      className={formErrors.employee_id ? 'input-error' : ''}
                    />
                    <span className="field-hint">{FIELD_PATTERNS.employee_id.hint}</span>
                    {formErrors.employee_id && <span className="field-error-msg">{formErrors.employee_id}</span>}
                  </div>
                  <div className="form-group">
                    <label>Department <span className="required">*</span></label>
                    <select
                      name="department"
                      value={formData.department}
                      onChange={handleInputChange}
                      required
                    >
                      <option value="">Select Department</option>
                      <option value="Teaching">Teaching</option>
                      <option value="Non-Teaching">Non-Teaching</option>
                    </select>
                  </div>
                </>
              )}

              <div className="form-group">
                <label>Contact Number/s <span className="required">*</span></label>
                <input
                  type="text"
                  name="contact_number"
                  value={formData.contact_number}
                  onChange={handleInputChange}
                  required
                  placeholder={FIELD_PATTERNS.contact_number.hint}
                  className={formErrors.contact_number ? 'input-error' : ''}
                />
                <span className="field-hint">{FIELD_PATTERNS.contact_number.hint}</span>
                {formErrors.contact_number && <span className="field-error-msg">{formErrors.contact_number}</span>}
              </div>

              <div className="form-group">
                <label>Age</label>
                <input type="number" name="age" min="15" max="99" value={formData.age} onChange={handleInputChange} />
              </div>

              <div className="form-group col-span-2">
                <label>Driver's License Number <span className="required">*</span></label>
                <input
                  type="text"
                  name="drivers_license"
                  value={formData.drivers_license}
                  onChange={handleInputChange}
                  required
                  maxLength={13}
                  placeholder={FIELD_PATTERNS.drivers_license.hint}
                  className={formErrors.drivers_license ? 'input-error' : ''}
                />
                <span className="field-hint">{FIELD_PATTERNS.drivers_license.hint}</span>
                {formErrors.drivers_license && <span className="field-error-msg">{formErrors.drivers_license}</span>}
              </div>

              {/* Campus day selector — students only */}
              {isStudent && (
                <div className="form-group col-span-2">
                  <label className="days-label">
                    Campus Days <span className="required">*</span>
                  </label>
                  {formData.student_level === 'sped' ? (
                    <div className="schedule-note schedule-note--sped">
                      <Info size={13} />
                      <span>Special Education students are assigned <strong>all campus days</strong>.</span>
                    </div>
                  ) : (
                    <div className="schedule-note">
                      <Info size={13} />
                      <span>
                        Schedules are <strong>first come, first serve</strong>. Each day has a limited number of slots.
                        Select up to <strong>3 days</strong>. Days that are <strong>full</strong> cannot be selected.
                      </span>
                    </div>
                  )}
                  <div className="campus-day-picker campus-day-picker--per-day">
                    {CAMPUS_DAYS.map(day => {
                      const isSped = formData.student_level === 'sped'
                      const slot = scheduleSlots?.[day.key]
                      const isFull = !isSped && slot?.available === 0
                      const isSelected = formData.campus_days.includes(day.key)
                      const limitReached = !isSped && formData.campus_days.length >= 3 && !isSelected
                      const isDisabled = isSped || isFull || limitReached
                      return (
                        <button
                          key={day.key}
                          type="button"
                          className={[
                            'campus-day-btn campus-day-btn--per-day',
                            isSelected ? 'campus-day-btn--selected' : '',
                            isFull ? 'campus-day-btn--full' : '',
                            limitReached && !isFull ? 'campus-day-btn--limit' : '',
                            isSped ? 'campus-day-btn--sped' : '',
                          ].filter(Boolean).join(' ')}
                          onClick={() => !isDisabled && toggleDay(day.key)}
                          disabled={isDisabled}
                          aria-pressed={isSelected}
                          title={isSped ? 'All days assigned for Special Education' : isFull ? `${day.key} is full` : limitReached ? 'Maximum 3 days selected' : day.key}
                        >
                          <span className="campus-day-short">{day.short}</span>
                          <span className="campus-day-slots">
                            {isSped
                              ? 'All'
                              : loadingSlots
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
                      {formData.student_level === 'sped'
                        ? 'All campus days assigned'
                        : `${formData.campus_days.length}/3 days selected${formData.campus_days.length === 3 ? ' — maximum reached' : ''}`}
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

            </>}

            {submitError && (
              <div className="reg-submit-error">
                <AlertTriangle size={15} />
                {submitError}
              </div>
            )}

            <div className="form-actions">
              <button
                type="submit"
                className="btn-submit"
                disabled={submitting || !registrantType || (regStatus && !regStatus.is_open)}
              >
                {submitting ? 'Submitting...' : 'Submit Registration'}
              </button>
            </div>
          </form>
        </div>
      </main>
    </div>
  )
}
