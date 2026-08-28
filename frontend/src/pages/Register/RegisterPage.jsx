import { useState, useEffect, useCallback } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { CheckCircle, AlertTriangle, Car, Info, User, Users, ChevronRight, Mail, Clock, Upload, X, ArrowLeft, FileText } from 'lucide-react'

import { registrationApi } from '../../api/registration'
import notify from '../../components/Feedback/notify'
import { fieldProblems } from '../../components/Feedback/formProblems'
import { formatPlateNumber, isValidPlateNumber } from '../../utils/plateFormat'
import {
  IllustratedStep,
  PayAtAccountingArt, NoFeeArt, UploadOrArt, ApprovalMailArt, CdsoOfficeArt,
} from '../../components/Illustrations/RegArt'

const LICENSE_IMAGE_MAX_MB    = 5
const LICENSE_IMAGE_MAX_BYTES = LICENSE_IMAGE_MAX_MB * 1024 * 1024
const LICENSE_IMAGE_TYPES     = ['image/jpeg', 'image/png', 'image/webp', 'image/heic', 'image/heif']

/* ── Assessment form ──
   The registrar's assessment form is what proves the applicant is genuinely
   enrolled — a student number alone is trivial to make up. PDF is accepted
   alongside the image types because that is what the student portal hands out;
   most applicants photograph the printed copy instead. */
const ASSESSMENT_FILE_MAX_MB    = 5
const ASSESSMENT_FILE_MAX_BYTES = ASSESSMENT_FILE_MAX_MB * 1024 * 1024
const ASSESSMENT_FILE_TYPES     = [...LICENSE_IMAGE_TYPES, 'application/pdf']

/* ── Email address ──
   Which rule applies follows who the school actually issues an account to:

     SCHOOL_ID  College students. They are issued <8-digit ID>@slc-sflu.edu.ph,
                so theirs is checked all the way down to the ID.
     SCHOOL     Employees and fetchers. They get named accounts instead, so the
                domain is the whole rule and the local part is left alone.
     PERSONAL   Students below college — SHS, JHS, Elementary and SpEd. The
                school issues them no address at all, so demanding one would
                lock out every pupil whose parent registers for them. A working
                personal address is what the CDSO's approval mail needs, and any
                provider will do.

   For everyone but that last group the domain is still the cheapest check that
   the applicant belongs to SLC, and it keeps the address the approval mail goes
   to one the school controls. */
const SCHOOL_EMAIL_DOMAIN   = 'slc-sflu.edu.ph'
const STUDENT_EMAIL_REGEX   = /^\d{8}@slc-sflu\.edu\.ph$/
const SCHOOL_EMAIL_REGEX    = /^[^\s@]+@slc-sflu\.edu\.ph$/
const PERSONAL_EMAIL_REGEX  = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

const EMAIL_MODE = { SCHOOL_ID: 'school-id', SCHOOL: 'school', PERSONAL: 'personal' }

/* Why the address was refused. Problems are reported in one modal on submit,
   where a bare "invalid email" leaves the applicant guessing which half of the
   address is at fault — so name the actual fault instead. Only ever called on
   an address the rule's regex has already rejected. */
function describeEmail(value, { mode }) {
  const email = value.trim()
  const needsId     = mode === EMAIL_MODE.SCHOOL_ID
  const needsDomain = mode !== EMAIL_MODE.PERSONAL

  if (/\s/.test(email)) return 'Email address can’t contain spaces — remove them.'

  const parts = email.split('@')
  if (parts.length === 1) {
    if (needsId) return `Email address is missing the @ — enter your 8-digit ID followed by @${SCHOOL_EMAIL_DOMAIN}.`
    if (needsDomain) return `Email address is missing the @ — e.g. juan.delacruz@${SCHOOL_EMAIL_DOMAIN}.`
    return 'Email address is missing the @ — e.g. juandelacruz@gmail.com.'
  }
  if (parts.length > 2) return 'Email address has more than one @ — keep only the one before the domain.'

  const [local, domain] = parts
  if (!local) {
    if (needsId) return 'Enter your 8-digit school ID before the @.'
    if (needsDomain) return 'Enter the name part of your school email before the @.'
    return 'Enter a name before the @ — e.g. juandelacruz@gmail.com.'
  }

  if (!needsDomain) {
    if (!domain) return 'Add a domain after the @ — e.g. gmail.com.'
    if (domain.startsWith('.') || domain.endsWith('.')) return `“${domain}” can’t start or end with a dot.`
    if (!domain.includes('.')) return `“${domain}” is missing its ending — e.g. ${domain}.com.`
    return 'Invalid email address format — e.g. juandelacruz@gmail.com.'
  }

  if (!domain) return `Add the school domain after the @: ${SCHOOL_EMAIL_DOMAIN}.`
  if (domain !== SCHOOL_EMAIL_DOMAIN) {
    return `Registration needs your SLC school email — replace @${domain} with @${SCHOOL_EMAIL_DOMAIN}.`
  }
  if (needsId) {
    if (!/^\d+$/.test(local)) {
      return 'The part before the @ must be your 8-digit school ID — digits only, no letters or dots.'
    }
    return `Your school ID must be 8 digits — “${local}” has ${local.length}.`
  }
  return `Invalid email address format — e.g. juan.delacruz@${SCHOOL_EMAIL_DOMAIN}.`
}

function formatFileSize(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatRegDate(iso) {
  if (!iso) return null
  const d = new Date(iso + 'T00:00:00')
  if (isNaN(d)) return iso
  return d.toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' })
}

// Today as a local "YYYY-MM-DD" string. The registration dates come from the
// backend in this same zero-padded format, so they compare correctly as
// strings without pulling timezones into a Date subtraction.
function todayISO() {
  const n = new Date()
  const p = x => String(x).padStart(2, '0')
  return `${n.getFullYear()}-${p(n.getMonth() + 1)}-${p(n.getDate())}`
}

// Auto-inserts dashes for the LTO format: X00-00-000000.
// Strips any existing dashes first so the cursor position doesn't confuse things.
function formatDriversLicense(raw) {
  const clean = raw.replace(/[^A-Za-z0-9]/g, '').toUpperCase().slice(0, 11)
  if (clean.length <= 3) return clean
  if (clean.length <= 5) return `${clean.slice(0, 3)}-${clean.slice(3)}`
  return `${clean.slice(0, 3)}-${clean.slice(3, 5)}-${clean.slice(5)}`
}

/* ── Philippine mobile numbers ──
   The form stores the full +639XXXXXXXXX the API expects, but the field only
   ever shows the 10 local digits — the "+63" sits beside the input as fixed
   chrome so nobody has to guess whether to type 0917…, +63917… or 63917…. */
const PH_DIAL_CODE = '+63'

// Keeps only the 10 local digits, tolerating 0917…, 63917… and +63 917… pastes.
function toLocalMobileDigits(raw) {
  let d = String(raw).replace(/\D/g, '')
  if (d.startsWith('63')) d = d.slice(2)
  if (d.startsWith('0')) d = d.slice(1)
  return d.slice(0, 10)
}

// '' stays '' so the required check still fires on an untouched field.
const toStoredMobile = (raw) => {
  const d = toLocalMobileDigits(raw)
  return d ? PH_DIAL_CODE + d : ''
}

const toDisplayMobile = (stored) => toLocalMobileDigits(stored)

// Ages offered in the dropdown. Guardian-driven students can be as young as 3;
// anyone driving themselves starts at 15. Mirrors the old min/max exactly.
function ageOptions(min) {
  return Array.from({ length: 99 - min + 1 }, (_, i) => min + i)
}

/* Employee departments. `free` marks the one exempt from the vehicle pass fee
   outright — Cleaning and Services pay nothing, an exemption rather than the
   50% employee rate. The backend is the authority (see
   VehicleRegistration.FEE_EXEMPT_DEPARTMENTS) and sends the same list on the
   registration-status payload; this is the fallback when that has not loaded.

   `free` deliberately does NOT surface in the picker. Labelling an option
   "free" invites people outside that department to choose it, and the CDSO
   ends up unpicking false registrations. The exemption is shown after the
   application is submitted instead. */
const DEPARTMENT_OPTIONS = [
  { value: 'teaching',          label: 'Teaching',              free: false },
  { value: 'non_teaching',      label: 'Non-Teaching',          free: false },
  { value: 'cleaning_services', label: 'Cleaning and Services', free: true  },
]

const FEE_EXEMPT_LABELS = new Set(
  DEPARTMENT_OPTIONS.filter(d => d.free).map(d => d.label)
)

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

const ALL_CAMPUS_DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

/* An applicant registers for a whole rotation, not for days of their own
   choosing — a pass issued as "MWF" has to mean all three of those days.
   Mirrors SCHEDULE_GROUP_DAYS in backend/vehicles/campus_days.py, Friday's
   double membership included. */
const SCHEDULE_GROUPS = [
  {
    code: 'MWF',
    short: 'Mon · Wed · Fri',
    days: ['Monday', 'Wednesday', 'Friday'],
    caption: 'Monday, Wednesday and Friday',
  },
  {
    code: 'TTHF',
    short: 'Tue · Thu · Fri',
    days: ['Tuesday', 'Thursday', 'Friday'],
    caption: 'Tuesday, Thursday and Friday',
  },
]

// Fetcher registrations must list at least one student being fetched.
// Same info as a student registration except email, contact number and age.
// assessment is the student's own enrolment proof (a File, never sent in the
// JSON payload) — a fetcher is not enrolled, so their application proves nothing
// about the students they collect.
const EMPTY_FETCHER_STUDENT = { full_name: '', student_id: '', student_level: '', program_year: '', assessment: null }

/* Levels that can never be self-driven — the "who drives" choice is skipped and a
   parent, guardian, or authorized driver is always registered as the driver.
   JHS/Elementary because those students are minors; SpEd because those students
   are accompanied regardless of age. */
const GUARDIAN_ONLY_LEVELS = ['jhs', 'elementary', 'sped']

const GUARDIAN_ONLY_REASON = {
  jhs:        'Junior High School students are minors and are not allowed to drive.',
  elementary: 'Elementary students are minors and are not allowed to drive.',
  sped:       'Special Education students are always accompanied and do not drive themselves.',
}

const FETCHER_STUDENT_LEVELS = [
  { id: 'college',    label: 'College' },
  { id: 'shs',        label: 'Senior High School' },
  { id: 'jhs',        label: 'Junior High School' },
  { id: 'elementary', label: 'Elementary' },
  { id: 'sped',       label: 'Special Education' },
]


/* Applying for a pass is a dead end otherwise — the only way back to the login
   page was the browser's back button. `onBack` is optional so the header can
   still render on states where leaving makes no sense. */
function SlcHeader({ onBack }) {
  return (
    <header className="register-header">
      <div className="header-content">
        <div className="header-logo-group">
          <img src={slcLogo} alt="Saint Louis College Logo" className="header-logo" />
          <div className="header-text">
            <span className="header-title">SAINT LOUIS COLLEGE</span>
            <span className="header-subtitle">Smart Parking and Vehicle Verification System</span>
          </div>
        </div>
        {onBack && (
          <button type="button" className="header-back-btn header-back-btn--end" onClick={onBack}>
            <ArrowLeft size={16} />
            <span>Back to Login</span>
          </button>
        )}
      </div>
    </header>
  )
}

export default function RegisterPage() {
  const [searchParams] = useSearchParams()
  const directType = searchParams.get('type') // 'student' | 'employee' | 'fetcher'
  const navigate = useNavigate()

  const [loading, setLoading] = useState(true)
  const [registrantType, setRegistrantType] = useState(null)
  const [submitted, setSubmitted] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [regStatus, setRegStatus] = useState(null)
  const [regStatusLoading, setRegStatusLoading] = useState(false)
  // vehiclePassFee is derived below, once formData exists — it depends on the
  // chosen department.
  const [formErrors, setFormErrors] = useState({})
  const [dupErrors, setDupErrors] = useState({}) // live "already registered" hints for plate_number/student_id/employee_id
  const [banned, setBanned] = useState(null)     // set if the applicant reached max violations and may not register
  const [isNewVehicle, setIsNewVehicle] = useState(false) // brand-new car → conduction number instead of plate
  const [dupChecking, setDupChecking] = useState({})
  const [licenseImage, setLicenseImage] = useState(null)
  const [licensePreview, setLicensePreview] = useState(null)
  const [assessmentFile, setAssessmentFile] = useState(null)
  // Set when the registration row saved but its documents didn't. The driver's
  // license photo is required, so this is an unfinished application rather than
  // a footnote — and the row already exists, so the applicant cannot simply
  // submit again (the backend would reject it as a duplicate). Holds exactly
  // what a retry needs: the row it attaches to, and the files themselves.
  const [pendingDocUpload, setPendingDocUpload] = useState(null)
  const [retryingDocUpload, setRetryingDocUpload] = useState(false)

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
    student_program: '',
    student_year: '',
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
    who_drives: '',            // 'self' | 'guardian' — form-only, not sent to the API
    driver_name: '',
    driver_relationship: '',
    driver_contact: '',
    // schedule is the rotation the applicant picks (MWF / TTHF); campus_days is
    // the week it expands to. Both are sent — the backend re-derives one from
    // the other, so they can never disagree.
    schedule: '',
    campus_days: [],
    plate_number: '',
    conduction_number: '',
    vehicle_type: '',
    // vehicle_color holds the submitted value; vehicle_color_choice tracks the
    // dropdown selection so "Other" can reveal a free-text field.
    vehicle_color: '',
    vehicle_color_choice: '',
    body_number: '',
    privacy_consent: false,
    // Form-only attestation, stripped from the payload before submitting.
    details_confirmed: false,
  })

  // What this applicant actually owes. Mirrors VehicleRegistration.pass_fee on
  // the backend — Services and Cleaning staff are exempt outright, not given
  // the 50% employee rate, so the figure quoted here is 0 for them.
  const feeExempt = registrantType === 'employee'
    && (regStatus?.fee_exempt_departments
          ? regStatus.fee_exempt_departments.includes(
              DEPARTMENT_OPTIONS.find(d => d.label === formData.department)?.value)
          : FEE_EXEMPT_LABELS.has(formData.department))
  const vehiclePassFee = feeExempt
    ? 0
    : registrantType === 'employee'
      ? (regStatus?.vehicle_pass_fee_employee ?? 150)
      : (regStatus?.vehicle_pass_fee ?? 300)

  // Fetcher-specific: classification + the students being fetched (at least one)
  const [fetcherType, setFetcherType] = useState('')
  const [fetcherStudents, setFetcherStudents] = useState([{ ...EMPTY_FETCHER_STUDENT }])

  const updateFetcherStudent = (index, field, value) => {
    setFetcherStudents(prev => prev.map((s, i) => (i === index ? { ...s, [field]: value } : s)))
  }

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

  useEffect(() => {
    fetchRefLists()
    fetchRegStatus()
    if (directType) {
      setRegistrantType(directType)
      if (directType === 'student') fetchScheduleSlots()
    }
    setLoading(false)
  }, [directType, fetchScheduleSlots, fetchRefLists, fetchRegStatus])

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

  /* Which email rule this applicant falls under. College is the only student
     level the school issues an address to; the rest register with a personal
     one. Recomputed every render, so switching registrant type or education
     level re-points the field at the right rule immediately. */
  const emailMode =
    registrantType !== 'student'           ? EMAIL_MODE.SCHOOL
    : formData.student_level === 'college' ? EMAIL_MODE.SCHOOL_ID
    : EMAIL_MODE.PERSONAL

  const FIELD_PATTERNS = {
    plate_number: {
      validate: isValidPlateNumber,
      message: 'Invalid Philippine plate number format',
      hint: 'e.g. AAA 0000 · AA 0000 · A000AA · AAA000',
    },
    // See EMAIL_MODE above for why each group gets the rule it does. A student
    // who has not picked a level yet falls to the lenient rule rather than the
    // strict one: guessing College at them would flag a perfectly good personal
    // address before the form has any idea which they are. They cannot submit
    // without choosing a level, and the rule tightens the moment they do.
    email: emailMode === EMAIL_MODE.SCHOOL_ID
      ? {
          regex: STUDENT_EMAIL_REGEX,
          describe: (value) => describeEmail(value, { mode: emailMode }),
          message: `Use your SLC school email — your 8-digit ID followed by @${SCHOOL_EMAIL_DOMAIN}`,
          hint: `e.g. 12345678@${SCHOOL_EMAIL_DOMAIN}`,
        }
      : emailMode === EMAIL_MODE.SCHOOL
        ? {
            regex: SCHOOL_EMAIL_REGEX,
            describe: (value) => describeEmail(value, { mode: emailMode }),
            message: `Use your SLC school email — any name followed by @${SCHOOL_EMAIL_DOMAIN}`,
            hint: `e.g. juan.delacruz@${SCHOOL_EMAIL_DOMAIN}`,
          }
        : {
            regex: PERSONAL_EMAIL_REGEX,
            describe: (value) => describeEmail(value, { mode: emailMode }),
            message: 'Invalid email address format',
            hint: 'e.g. juandelacruz@gmail.com',
          },
    conduction_number: {
      regex: /^[A-Z0-9]{5,12}$/i,
      message: 'Invalid conduction number. Use 5–12 alphanumeric characters.',
      hint: 'e.g. CS12345A678',
    },
    contact_number: {
      regex: /^\+639\d{9}$/,
      message: 'Enter the 10 digits after +63, starting with 9',
      hint: 'e.g. 9123456789',
    },
    driver_contact: {
      regex: /^\+639\d{9}$/,
      message: 'Enter the 10 digits after +63, starting with 9',
      hint: 'e.g. 9123456789',
    },
    drivers_license: {
      // LTO format: 1 office letter + 2-digit district + dash + 2-digit year + dash + 6-digit serial
      // Mask shown to the user: A00-00-000000
      regex: /^[A-Z]\d{2}-\d{2}-\d{6}$/i,
      message: 'Invalid LTO license number. Use format: A00-00-000000',
      hint: 'e.g. A00-00-000000',
    },
    student_id: {
      regex: /^\d{8}$/,
      message: 'Invalid student ID. Must be 8 digits (e.g. 12345678)',
      hint: 'e.g. 12345678',
    },
    employee_id: {
      regex: /^\d{8}$/,
      message: 'Invalid employee ID. Must be 8 digits (e.g. 12345678)',
      hint: 'e.g. 12345678',
    },
  }

  const validateField = (name, value) => {
    if (!value || !value.trim()) return null
    const rule = FIELD_PATTERNS[name]
    if (!rule) return null
    const valid = typeof rule.validate === 'function'
      ? rule.validate(value.trim())
      : rule.regex.test(value.trim())
    if (valid) return null
    // A rule may explain the specific fault; otherwise its one flat message stands.
    return rule.describe ? rule.describe(value) : rule.message
  }

  /* The email is judged by a rule that changes with registrant type and
     education level, so a remembered error goes stale the moment either moves:
     a College applicant who switches to SHS should not still be told to use
     their 8-digit ID, and the personal address that was fine as SHS has to be
     flagged the moment they switch back. Derived on every render rather than
     stored, so the field can never disagree with the rule currently in force.
     `formErrors.email` is still written by the shared change handler; it is
     simply not what the field reads. */
  const emailError = validateField('email', formData.email)

  const handleInputChange = (e) => {
    const { name, value, type, checked } = e.target
    if (type === 'checkbox') {
      setFormData((prev) => ({ ...prev, [name]: checked }))
    } else {
      let formatted = value
      if (name === 'plate_number') formatted = formatPlateNumber(value)
      else if (name === 'drivers_license') formatted = formatDriversLicense(value)
      else if (name === 'contact_number' || name === 'driver_contact') formatted = toStoredMobile(value)
      else if (name === 'email') formatted = formatted.toLowerCase()
      else if (['last_name', 'first_name', 'middle_name', 'vehicle_color', 'driver_name'].includes(name))
        formatted = formatted.toUpperCase()
      setFormData((prev) => ({
        ...prev,
        [name]: formatted,
        // Body number only applies to tricycles — drop it if the type changes
        ...(name === 'vehicle_type' && formatted !== 'Tricycle' ? { body_number: '' } : {}),
      }))
      const errorMsg = validateField(name, formatted)
      setFormErrors((prev) => ({ ...prev, [name]: errorMsg }))
      // Stale duplicate hint no longer applies to the value being typed — the debounced
      // check below will repopulate it once the new value settles
      if (['plate_number', 'email', 'drivers_license', 'student_id', 'employee_id'].includes(name))
        setDupErrors((prev) => ({ ...prev, [name]: null }))
    }
  }

  /* ── Vehicle colour dropdown ──
     A preset choice fills vehicle_color directly (uppercased, matching the
     form's naming convention); "Other" clears it and reveals a free-text field
     so an uncommon colour can be typed. */
  const handleColorChoice = (e) => {
    const choice = e.target.value
    const value = choice === 'Other' ? '' : choice.toUpperCase()
    setFormData((prev) => ({ ...prev, vehicle_color_choice: choice, vehicle_color: value }))
    // A preset is immediately valid; "Other" waits for the text field's own input.
    setFormErrors((prev) => ({
      ...prev,
      vehicle_color: choice === 'Other' ? '' : validateField('vehicle_color', value),
    }))
  }

  /* ── Supporting documents ──
     Posted after the registration row exists, so a failure here leaves an
     application on file with no driver's license photo attached — and CDSO
     cannot review it without one. The single automatic retry absorbs the usual
     transient blip; anything that survives it is handed back to the applicant
     as a button they can press, not a warning they can only read. */
  const uploadDocuments = async (registrationId, email, files) => {
    try {
      await registrationApi.uploadRegistrationDocuments(registrationId, email, files)
      return true
    } catch (firstErr) {
      console.error('Registration document upload failed, retrying:', firstErr)
      try {
        await registrationApi.uploadRegistrationDocuments(registrationId, email, files)
        return true
      } catch (retryErr) {
        console.error('Registration document upload failed again:', retryErr)
        return false
      }
    }
  }

  const handleRetryDocUpload = async () => {
    if (!pendingDocUpload || retryingDocUpload) return
    setRetryingDocUpload(true)
    const { registrationId, email, files } = pendingDocUpload
    const ok = await uploadDocuments(registrationId, email, files)
    setRetryingDocUpload(false)
    if (ok) {
      setPendingDocUpload(null)
      notify.success(
        'Your documents are now on file — nothing else is needed from you.',
        { title: 'Upload complete' },
      )
    } else {
      notify.error(
        'The upload still did not go through. Check your connection and try again, or '
        + 'bring the physical driver’s license to the CDSO Office.',
        { title: 'Upload failed' },
      )
    }
  }

  /* ── Driver's license photo ── */
  const handleLicenseImageChange = (e) => {
    const file = e.target.files?.[0]
    // Let the user re-pick the same file after removing it
    e.target.value = ''
    if (!file) return

    if (!LICENSE_IMAGE_TYPES.includes(file.type)) {
      notify.error('Please choose a JPG, PNG, WEBP or HEIC image.', { title: 'Unsupported file' })
      return
    }
    if (file.size > LICENSE_IMAGE_MAX_BYTES) {
      notify.error(`That image is ${formatFileSize(file.size)}. Please keep it under ${LICENSE_IMAGE_MAX_MB}MB.`, { title: 'Image too large' })
      return
    }

    setLicenseImage(file)
    // HEIC won't render in most browsers — fall back to the filename-only chip.
    // Built outside the updater so StrictMode's double-invoke can't leak a second URL.
    const nextPreview = file.type === 'image/heic' || file.type === 'image/heif'
      ? null
      : URL.createObjectURL(file)
    setLicensePreview((prev) => {
      if (prev) URL.revokeObjectURL(prev)
      return nextPreview
    })
  }

  const clearLicenseImage = () => {
    setLicenseImage(null)
    setLicensePreview((prev) => {
      if (prev) URL.revokeObjectURL(prev)
      return null
    })
  }

  /* ── Assessment form ──
     No preview: half of these are PDFs, and the ones that aren't are dense
     scans that read as noise at thumbnail size. The filename chip is enough
     for the applicant to confirm they picked the right file. */
  // Shared by the applicant's own assessment and by each fetched student's:
  // same file rules, same messages, one place to change them.
  const pickAssessmentFile = (e) => {
    const file = e.target.files?.[0]
    // Let the user re-pick the same file after removing it
    e.target.value = ''
    if (!file) return null

    // Some browsers report an empty type for HEIC; fall back to the extension.
    const extOk = /\.(jpe?g|png|webp|heic|heif|pdf)$/i.test(file.name)
    if (!ASSESSMENT_FILE_TYPES.includes(file.type) && !extOk) {
      notify.error('Please choose a JPG, PNG, WEBP, HEIC or PDF file.', { title: 'Unsupported file' })
      return null
    }
    if (file.size > ASSESSMENT_FILE_MAX_BYTES) {
      notify.error(`That file is ${formatFileSize(file.size)}. Please keep it under ${ASSESSMENT_FILE_MAX_MB}MB.`, { title: 'File too large' })
      return null
    }

    return file
  }

  const handleAssessmentChange = (e) => {
    const file = pickAssessmentFile(e)
    if (file) setAssessmentFile(file)
  }

  const clearAssessmentFile = () => {
    setAssessmentFile(null)
  }

  const handleFetcherAssessmentChange = (index, e) => {
    const file = pickAssessmentFile(e)
    if (file) updateFetcherStudent(index, 'assessment', file)
  }

  // Release the last object URL when the form unmounts
  useEffect(() => () => { if (licensePreview) URL.revokeObjectURL(licensePreview) }, [licensePreview])

  // Debounced live duplicate check — warns in the field hint before the user submits
  useEffect(() => {
    const plate = formData.plate_number?.trim()
    const conduction = formData.conduction_number?.trim()
    const email = formData.email?.trim()
    const license = formData.drivers_license?.trim()
    const studentId = formData.student_id?.trim()
    const employeeId = formData.employee_id?.trim()

    const plateValid = !isNewVehicle && plate && isValidPlateNumber(plate)
    const conductionValid = isNewVehicle && !!conduction && FIELD_PATTERNS.conduction_number.regex.test(conduction)
    const emailValid = !!email && FIELD_PATTERNS.email.regex.test(email)
    const licenseValid = !!license && FIELD_PATTERNS.drivers_license.regex.test(license)
    const studentIdValid = registrantType === 'student' && /^\d{8}$/.test(studentId || '')
    const employeeIdValid = registrantType === 'employee' && /^\d{8}$/.test(employeeId || '')

    if (!plateValid && !conductionValid && !emailValid && !licenseValid && !studentIdValid && !employeeIdValid) return

    setDupChecking(prev => ({
      ...prev,
      plate_number: plateValid || prev.plate_number,
      conduction_number: conductionValid || prev.conduction_number,
      email: emailValid || prev.email,
      drivers_license: licenseValid || prev.drivers_license,
      student_id: studentIdValid || prev.student_id,
      employee_id: employeeIdValid || prev.employee_id,
    }))

    const timer = setTimeout(async () => {
      try {
        const result = await registrationApi.checkAvailability({
          plate_number: plateValid ? plate : '',
          conduction_number: conductionValid ? conduction : '',
          email: emailValid ? email : '',
          drivers_license: licenseValid ? license : '',
          student_id: studentIdValid ? studentId : '',
          employee_id: employeeIdValid ? employeeId : '',
        })
        setDupErrors(prev => ({
          ...prev,
          ...(plateValid && { plate_number: result.plate_number }),
          ...(conductionValid && { conduction_number: result.conduction_number }),
          ...(emailValid && { email: result.email }),
          ...(licenseValid && { drivers_license: result.drivers_license }),
          ...(studentIdValid && { student_id: result.student_id }),
          ...(employeeIdValid && { employee_id: result.employee_id }),
        }))
        setBanned(result.banned || null)
      } catch {
        // Network hiccup — the backend still enforces this on submit, so fail silently here
      } finally {
        setDupChecking(prev => ({
          ...prev,
          ...(plateValid && { plate_number: false }),
          ...(conductionValid && { conduction_number: false }),
          ...(emailValid && { email: false }),
          ...(licenseValid && { drivers_license: false }),
          ...(studentIdValid && { student_id: false }),
          ...(employeeIdValid && { employee_id: false }),
        }))
      }
    }, 500)
    return () => clearTimeout(timer)
  }, [formData.plate_number, formData.conduction_number, isNewVehicle, formData.email, formData.drivers_license, formData.student_id, formData.employee_id, registrantType, emailMode])

  const selectSchedule = (group) => {
    setFormData(prev => ({ ...prev, schedule: group.code, campus_days: [...group.days] }))
  }

  // Remaining places on a rotation = its tightest day, since a registration
  // takes a slot on every day of the schedule.
  const groupSlots = (group) =>
    scheduleSlots?.groups?.[group.code]
      ?? (scheduleSlots
            ? { available: Math.min(...group.days.map(d => scheduleSlots[d]?.available ?? 0)) }
            : null)

  const handleSubmit = async (e) => {
    e.preventDefault()
    // Grabbed before the first await — React clears currentTarget once the
    // handler returns, and everything below yields.
    const formEl = e.currentTarget

    // Two things stop the application outright rather than being something to
    // correct on the form, so they are said on their own.
    if (regStatus && !regStatus.is_open) {
      await notify.error(
        'Registration is currently closed. Please try again during the registration window.',
        { title: 'Registration closed' },
      )
      return
    }
    if (banned) {
      await notify.error(banned, { title: 'Application blocked' })
      return
    }

    // Everything else is gathered into one list. The form is long enough that
    // reporting the first problem, then the next one after another submit, is
    // its own small ordeal — so say all of it at once.
    const problems = []

    if (!registrantType) problems.push('Select your registrant type.')

    // Whatever the browser would have refused on its own. The form carries
    // noValidate, so this is the only thing standing in for it.
    problems.push(...fieldProblems(formEl))

    // Format checks. These still mark their fields red, so the list and the
    // form agree on what to look at.
    const fieldsToValidate = ['email', 'plate_number', 'conduction_number', 'contact_number', 'drivers_license', 'driver_contact', 'student_id', 'employee_id']
    const newErrors = {}
    fieldsToValidate.forEach(name => {
      const err = validateField(name, formData[name])
      if (err) newErrors[name] = err
    })
    if (Object.keys(newErrors).length > 0) {
      setFormErrors(prev => ({ ...prev, ...newErrors }))
      problems.push(...Object.values(newErrors))
    }

    // Already-known duplicates from the live check (the backend re-checks regardless)
    ;['plate_number', 'conduction_number', 'email', 'drivers_license', 'student_id', 'employee_id']
      .forEach((name) => { if (dupErrors[name]) problems.push(dupErrors[name]) })

    // The license number on its own is just typed text — the photo is what CDSO
    // checks it against, so an application without one cannot be reviewed.
    if (!licenseImage) {
      problems.push("Attach a photo of the driver's license so CDSO can verify it.")
    }

    if (!formData.privacy_consent) problems.push('Agree to the Data Privacy Consent.')
    if (!formData.details_confirmed) {
      problems.push('Confirm that all the details you entered are true, complete and correct.')
    }

    if (registrantType === 'student') {
      // The whole point of the attachment is proving enrolment, so a student
      // application without it can’t be reviewed — blocked here rather than
      // letting CDSO chase it down after the fact.
      if (!assessmentFile) {
        problems.push('Attach your assessment form so CDSO can verify your enrolment.')
      }
      if (!formData.student_level) {
        problems.push('Select your education level.')
      } else if (formData.student_level === 'college' && (!formData.student_program.trim() || !formData.student_year)) {
        problems.push('Select your program and year level.')
      } else if (formData.student_level === 'shs' && (!formData.student_strand || !formData.student_grade)) {
        problems.push('Select your track/strand and grade level.')
      } else if (['jhs', 'elementary'].includes(formData.student_level) && !formData.student_grade) {
        problems.push('Select your grade level.')
      }
      // A guardian-driven registration always needs the driver’s details.
      if (formData.who_drives === 'guardian') {
        if (!formData.driver_name.trim()) problems.push("Enter the authorized driver's full name.")
        if (!formData.driver_relationship) problems.push("Select the driver's relationship to the student.")
      }
      if (formData.student_level !== 'sped') {
        const chosen = SCHEDULE_GROUPS.find(g => g.code === formData.schedule)
        if (!chosen) {
          problems.push('Choose your campus schedule: Mon · Wed · Fri or Tue · Thu · Fri.')
        } else if (groupSlots(chosen)?.available === 0) {
          problems.push(`The ${chosen.short} schedule is full — choose the other schedule.`)
        }
      }
    }

    if (registrantType === 'fetcher') {
      if (!fetcherType) {
        problems.push('Choose your fetcher classification: Fetcher/Drop & Go or Standby.')
      }
      fetcherStudents.forEach((st, i) => {
        if (!st.full_name.trim() || !st.student_id.trim() || !st.student_level) {
          problems.push(`Student #${i + 1}: full name, student ID and education level are required.`)
        }
        // Same reason a student applicant cannot skip theirs: without it there
        // is nothing for CDSO to check the enrolment against.
        if (!st.assessment) {
          problems.push(`Student #${i + 1}: attach their assessment form.`)
        }
      })
    }

    if (await notify.validation(problems, { title: 'Check your application' })) return

    setSubmitting(true)
    try {
      const full_name = [formData.last_name, formData.first_name, formData.middle_name]
        .map(s => s.trim()).filter(Boolean).join(', ')
      const address = [formData.house_street, formData.barangay, formData.city_municipality, formData.province]
        .map(s => s.trim()).filter(Boolean).join(', ')

      // Compose program_year from the level-specific fields
      let program_year = formData.program_year
      if (registrantType === 'student' && formData.student_level === 'college') {
        program_year = `${formData.student_program.trim()} - ${formData.student_year}`
      } else if (registrantType === 'student' && formData.student_level !== 'college') {
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

      const guardian = registrantType === 'student' && formData.who_drives === 'guardian'
      const payload = {
        ...formData,
        // Either/or: send only the identifier that applies, never both.
        plate_number:      isNewVehicle ? '' : formData.plate_number,
        conduction_number: isNewVehicle ? formData.conduction_number : '',
        full_name,
        address,
        program_year,
        registrant_type: registrantType,
        student_level: registrantType === 'student' ? formData.student_level : '',
        // Driver fields only apply to guardian-driven student registrations
        driver_name:         guardian ? formData.driver_name.trim() : '',
        driver_relationship: guardian ? formData.driver_relationship : '',
        driver_contact:      guardian ? formData.driver_contact.trim() : '',
        // Fetcher classification + students being fetched
        fetcher_type:     registrantType === 'fetcher' ? fetcherType : '',
        fetcher_students: registrantType === 'fetcher'
          // Text only: the assessment File is uploaded separately, against
          // this same index, by uploadRegistrationDocuments below.
          ? fetcherStudents.map(s => ({
              full_name:     s.full_name.trim(),
              student_id:    s.student_id.trim(),
              student_level: s.student_level,
              program_year:  s.program_year.trim(),
            }))
          : [],
      }
      delete payload.who_drives
      // Form-only attestation — the backend has no column for it.
      delete payload.details_confirmed
      // UI-only helper for the colour dropdown — the backend stores vehicle_color.
      delete payload.vehicle_color_choice

      const result = await registrationApi.submitOpenRegistration(payload)

      // Only students have an assessment form; if one was attached before the
      // applicant switched registrant type, it is dropped rather than filed
      // against a registration nobody will look for it on.
      const assessment = registrantType === 'student' ? assessmentFile : null
      // One per fetched student, keyed by their position in fetcher_students —
      // that index is what pairs the file with the student on the review screen.
      const fetcherAssessments = registrantType === 'fetcher'
        ? fetcherStudents.map(st => st.assessment || null)
        : []
      const hasFetcherAssessment = fetcherAssessments.some(Boolean)
      if ((licenseImage || assessment || hasFetcherAssessment) && result?.id) {
        const files = { license: licenseImage, assessment, fetcherAssessments }
        if (!await uploadDocuments(result.id, payload.email, files)) {
          // The registration itself is saved and cannot be submitted again, so
          // the upload is parked for retry rather than lost. Raised as a modal
          // too: the license photo is required, and a notice sitting on the
          // success screen is exactly the kind of thing a relieved applicant
          // scrolls straight past.
          setPendingDocUpload({ registrationId: result.id, email: payload.email, files })
          notify.error(
            'Your application was submitted, but the driver’s license photo did not upload — '
            + 'and CDSO needs it to review your application. Use “Retry upload” on the next screen.',
            { title: 'Documents not uploaded' },
          )
        }
      }

      // Go straight to the success screen
      setSubmitted(true)
    } catch (err) {
      const errData = err.response?.data
      const msg = errData?.error
        || (typeof errData === 'object' ? Object.entries(errData).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(', ') : v}`).join(' | ') : null)
        || 'Failed to submit registration. Please try again.'
      notify.error(msg, { title: 'Registration not submitted' })
      console.error('Registration error:', errData || err)
    } finally {
      setSubmitting(false)
    }
  }

  /* ── Back to login ──
     Only the fields a person actually types are checked for "dirty"; several
     others get defaults the moment a registrant type is picked, and warning
     about those would fire on an empty form. */
  const TYPED_FIELDS = [
    'last_name', 'first_name', 'middle_name', 'email', 'contact_number',
    'plate_number', 'conduction_number', 'student_id', 'employee_id',
    'drivers_license', 'house_street', 'driver_name', 'driver_contact',
  ]

  const handleBackToLogin = async () => {
    const started = TYPED_FIELDS.some(f => (formData[f] || '').trim() !== '')
    if (started) {
      const leave = await notify.confirm({
        title: 'Leave this application?',
        message: 'Anything you have filled in will be lost.',
        confirmLabel: 'Leave',
        cancelLabel: 'Stay',
        danger: true,
      })
      if (!leave) return
    }
    navigate('/login')
  }

  /* ─── Loading ─── */
  if (loading) {
    return (
      <div className="register-page">
        <SlcHeader />
        <main className="register-main">
          <div className="register-container">
            <div className="loading-spinner"></div>
          </div>
        </main>
      </div>
    )
  }

  /* ─── Type Selector (no directType) ─── */
  if (!registrantType) {
    return (
      <div className="register-page">
        <SlcHeader onBack={handleBackToLogin} />
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
                      if (regStatus.is_open)
                        return <>Window: {range}</>
                      // Window already ended — state that instead of showing a stale past range.
                      if (regStatus.close_date && regStatus.close_date < todayISO())
                        return <>The registration period ended on <span className="reg-window-range">{end}</span>. Please check back for the next window.</>
                      return <>Registration window: {range}. Submissions are not accepted outside the registration period.</>
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

  /* ─── Success ─── */
  if (submitted) {
    return (
      <div className="register-page">
        <SlcHeader />
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

            {pendingDocUpload && (
              <div className="success-license-warn">
                <AlertTriangle size={16} />
                <div className="success-license-warn-body">
                  <span>
                    Your application was submitted, but your supporting documents could not be
                    uploaded. The driver's license photo is required before CDSO can review your
                    application — please retry, or bring the physical copies to the CDSO Office.
                  </span>
                  <button
                    type="button"
                    className="success-license-retry"
                    onClick={handleRetryDocUpload}
                    disabled={retryingDocUpload}
                  >
                    {retryingDocUpload ? 'Uploading…' : 'Retry upload'}
                  </button>
                </div>
              </div>
            )}

            {/* Next steps. Each one is drawn as well as written — this screen is
                read once, on a phone, and the applicant has to remember the
                errand for days afterwards. */}
            <div className="success-next-steps">
              <p className="success-next-heading">What to do next</p>
              <div className="reg-step-list">
                {/* Telling an exempt applicant to "Pay ₱0.00" would send them
                    to Accounting for nothing — so their first two steps are a
                    different pair, and the numbering closes up rather than
                    skipping over the payment they never have to make. */}
                {feeExempt ? (
                  <>
                    <IllustratedStep step={1} tone="ok" art={<NoFeeArt />} title="No fee to pay">
                      {formData.department} staff are exempt from the vehicle pass fee, so there is
                      nothing to settle at the Accounting Office.
                    </IllustratedStep>
                    <IllustratedStep step={2} art={<CdsoOfficeArt />} title="Go to the CDSO Office">
                      Bring a valid ID. The CDSO reviews your application and the documents you
                      attached, then releases your vehicle pass.
                    </IllustratedStep>
                  </>
                ) : (
                  <>
                    <IllustratedStep
                      step={1}
                      art={<PayAtAccountingArt />}
                      title={`Pay ₱${vehiclePassFee.toFixed(2)} at the Accounting Office`}
                    >
                      Settle the vehicle pass fee at the counter and keep the Official Receipt (OR)
                      they hand you — you will need both its number and a photo of it.
                    </IllustratedStep>
                    <IllustratedStep step={2} art={<UploadOrArt />} title="Upload your Official Receipt">
                      Open the link in the email we just sent, enter the OR number and attach a
                      clear photo of the receipt. Your application is not queued for review until
                      this is done.
                    </IllustratedStep>
                  </>
                )}
                <IllustratedStep step={3} art={<ApprovalMailArt />} title="Watch for the approval email">
                  The CDSO emails you the outcome. If approved, that email carries the credentials
                  for your vehicle owner portal.
                </IllustratedStep>
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

  // JHS/Elementary/SpEd always register a parent, guardian, or authorized driver.
  const isGuardianOnlyLevel = isStudent && GUARDIAN_ONLY_LEVELS.includes(formData.student_level)
  const guardianDriven = isStudent && formData.who_drives === 'guardian'

  // The backend program list stores combined "BSIT - 3" entries; split them into
  // a unique program list and per-program year options for the two separate fields.
  const stripYear = (p) => p.replace(/\s*-\s*\d+\s*$/, '').trim()
  const programOptions = [...new Set(programs.map(stripYear))]
  const yearOptions = (() => {
    const years = [...new Set(
      programs
        .filter(p => stripYear(p) === formData.student_program.trim())
        .map(p => (p.match(/-\s*(\d+)\s*$/) || [])[1])
        .filter(Boolean)
    )]
    return years.length ? years.sort((a, b) => a - b) : ['1', '2', '3', '4']
  })()

  const TYPE_OPTIONS = [
    { id: 'student',  icon: <User size={24} />, label: 'Student',           desc: 'Registered SLC student' },
    { id: 'employee', icon: <Car size={24} />,  label: 'Employee',          desc: 'SLC faculty or staff' },
    { id: 'fetcher',  icon: <Users size={24} />, label: 'Fetcher / Drop & Go', desc: 'Parent or guardian' },
  ]

  /* ─── Form ─── */
  return (
    <div className="register-page">
      <SlcHeader onBack={handleBackToLogin} />

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
                    ? <>Window: <span className="reg-window-range">{formatRegDate(regStatus.open_date)} – {formatRegDate(regStatus.close_date)}</span></>
                    : (() => {
                        const today = todayISO()
                        // Closed & still upcoming — the start date is genuinely in the future.
                        if (regStatus.open_date && regStatus.open_date > today)
                          return <>Next window opens approximately on <span className="reg-window-range">{formatRegDate(regStatus.open_date)}</span>. Submissions are not accepted outside the registration period.</>
                        // Closed & already ended — don't advertise a past start date as the "next" window.
                        if (regStatus.close_date && regStatus.close_date < today)
                          return <>The registration period ended on <span className="reg-window-range">{formatRegDate(regStatus.close_date)}</span>. Please check back for the next window.</>
                        // No period scheduled (open_date is null), or an edge with no future date to promise.
                        return <>Registration is not open at this time. Please check back for the next window.</>
                      })()}
                </div>
              </div>
            </div>
          ) : null}

          <form onSubmit={handleSubmit} className="register-form" noValidate>

            {/* ── Campus Schedule notice ──
                Ahead of every other section on purpose: the applicant knows
                which days their pass covers — and, for students, that slots are
                first come, first serve — before working through the rest of the
                form. Employees and fetchers get every campus day, so the notice
                is all there is; the student picker sits further down, with the
                rest of the student details.
                A rotation is taken whole — picking loose days produced passes
                whose stored days did not match the schedule printed on them. */}
            {registrantType && (
              <>
                <div className="form-grid">
                  <div className="form-group col-span-2">
                    <label className="days-label">
                      Campus Schedule {isStudent && <span className="required">*</span>}
                    </label>
                    {isStudent ? (
                      formData.student_level === 'sped' ? (
                        <div className="schedule-note schedule-note--sped">
                          <Info size={13} />
                          <span>
                            Special Education students are assigned <strong>all campus days
                            (Monday to Saturday)</strong>.
                          </span>
                        </div>
                      ) : (
                        <div className="schedule-note">
                          <Info size={13} />
                          <span>
                            Choose <strong>one</strong> schedule — it covers all three of its days.
                            Slots are <strong>first come, first serve</strong>; a schedule that is
                            <strong> full</strong> cannot be selected.
                          </span>
                        </div>
                      )
                    ) : isEmployee ? (
                      /* Spelled out as Monday–Saturday: "any day" reads as Sunday
                         included, and the campus is closed then. */
                      <p className="campus-day-anyday-note">
                        <Info size={13} />
                        Employees are permitted to enter and park on <strong>any campus day
                        (Monday to Saturday)</strong>.
                      </p>
                    ) : (
                      /* Fetcher — every campus day; entry rules depend on classification */
                      <p className="campus-day-anyday-note fetcher-note">
                        <Info size={13} />
                        {fetcherType === 'standby'
                          ? <>Standby fetchers may enter on <strong>any campus day (Monday to Saturday)</strong> and are allowed to park inside the campus while waiting.</>
                          : <>Fetchers / Drop &amp; Go may enter on <strong>any campus day (Monday to Saturday)</strong> during designated drop-off and pick-up hours only. Entry outside these hours will be restricted.</>}
                      </p>
                    )}
                  </div>
                </div>
                <hr className="divider" />
              </>
            )}

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

            {/* Brand-new cars have no plate yet — they register with a conduction
                sticker instead. Ask up front so only the relevant field shows. */}
            <label className="reg-newcar-toggle">
              <input
                type="checkbox"
                checked={isNewVehicle}
                onChange={(e) => {
                  const nv = e.target.checked
                  setIsNewVehicle(nv)
                  // Clear whichever identifier no longer applies + its errors.
                  setFormData(prev => ({ ...prev,
                    plate_number: nv ? '' : prev.plate_number,
                    conduction_number: nv ? prev.conduction_number : '',
                  }))
                  setFormErrors(prev => ({ ...prev, plate_number: '', conduction_number: '' }))
                  setDupErrors(prev => ({ ...prev, plate_number: null, conduction_number: null }))
                }}
              />
              <span>My vehicle is brand-new and does not have a plate number yet (I have a conduction number).</span>
            </label>

            <div className="form-grid">
              {!isNewVehicle ? (
                <div className="form-group">
                  <label>Plate Number <span className="required">*</span></label>
                  <input
                    type="text"
                    name="plate_number"
                    value={formData.plate_number}
                    onChange={handleInputChange}
                    required
                    placeholder={FIELD_PATTERNS.plate_number.hint}
                    className={formErrors.plate_number || dupErrors.plate_number ? 'input-error' : ''}
                  />
                  <span className="field-hint">{FIELD_PATTERNS.plate_number.hint}</span>
                  {!formErrors.plate_number && dupChecking.plate_number && <span className="field-checking-msg">Checking availability…</span>}
                </div>
              ) : (
                <div className="form-group">
                  <label>Conduction Number <span className="required">*</span></label>
                  <input
                    type="text"
                    name="conduction_number"
                    value={formData.conduction_number}
                    onChange={handleInputChange}
                    required
                    placeholder={FIELD_PATTERNS.conduction_number.hint}
                    className={formErrors.conduction_number || dupErrors.conduction_number ? 'input-error' : ''}
                  />
                  <span className="field-hint">For newly purchased vehicles without a plate yet. {FIELD_PATTERNS.conduction_number.hint}</span>
                </div>
              )}

              <div className="form-group">
                <label>Vehicle Type <span className="required">*</span></label>
                <select name="vehicle_type" value={formData.vehicle_type} onChange={handleInputChange} required>
                  <option value="">Select Type</option>
                  <option value="Sedan">Sedan</option>
                  <option value="SUV">SUV</option>
                  <option value="Motorcycle">Motorcycle</option>
                  <option value="Tricycle">Tricycle</option>
                  <option value="E-Bike">E-Bike</option>
                  <option value="Van">Van</option>
                  <option value="Truck">Truck</option>
                  <option value="Other">Other</option>
                </select>
              </div>

              <div className="form-group">
                <label>Vehicle Color <span className="required">*</span></label>
                <select
                  name="vehicle_color_choice"
                  value={formData.vehicle_color_choice}
                  onChange={handleColorChoice}
                  required
                >
                  <option value="">Select Color</option>
                  <option value="White">White</option>
                  <option value="Black">Black</option>
                  <option value="Silver">Silver</option>
                  <option value="Gray">Gray</option>
                  <option value="Red">Red</option>
                  <option value="Blue">Blue</option>
                  <option value="Green">Green</option>
                  <option value="Yellow">Yellow</option>
                  <option value="Orange">Orange</option>
                  <option value="Brown">Brown</option>
                  <option value="Beige">Beige</option>
                  <option value="Gold">Gold</option>
                  <option value="Maroon">Maroon</option>
                  <option value="Other">Other</option>
                </select>
                {formData.vehicle_color_choice === 'Other' && (
                  <input
                    type="text"
                    name="vehicle_color"
                    value={formData.vehicle_color}
                    onChange={handleInputChange}
                    required
                    autoFocus
                    placeholder="Enter vehicle color"
                    style={{ marginTop: 8 }}
                  />
                )}
              </div>

              {formData.vehicle_type === 'Tricycle' && (
                <div className="form-group col-span-2">
                  <label>Body Number <span className="required">*</span></label>
                  <input
                    type="text"
                    name="body_number"
                    value={formData.body_number}
                    onChange={handleInputChange}
                    required
                    placeholder="e.g. 0123"
                  />
                </div>
              )}
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
                  className={emailError || dupErrors.email ? 'input-error' : ''}
                />
                {(emailError || dupErrors.email) && (
                  <span className="field-error-msg">{emailError || dupErrors.email}</span>
                )}
                {!emailError && !dupErrors.email && dupChecking.email && <span className="field-checking-msg">Checking availability…</span>}
                {/* Spelled out because the rule is not the same for everyone — an
                    Elementary parent seeing only a school-address placeholder has
                    no way to tell whether their own Gmail is allowed. */}
                <span className="field-hint">
                  {emailMode === EMAIL_MODE.SCHOOL_ID
                    ? `College students use their SLC school email — 8-digit ID followed by @${SCHOOL_EMAIL_DOMAIN}.`
                    : emailMode === EMAIL_MODE.SCHOOL
                      ? `Use your SLC school email — any name followed by @${SCHOOL_EMAIL_DOMAIN}.`
                      : 'Use a personal email address you actually check — a Gmail account is fine.'}
                </span>
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
                            student_program: '',
                            student_year: '',
                            program_year: '',
                            // Minors and SpEd are locked to a guardian driver;
                            // College/SHS default to self-driving.
                            who_drives: GUARDIAN_ONLY_LEVELS.includes(lvl.id) ? 'guardian' : 'self',
                            // SpEd students attend every campus day; leaving
                            // that level clears the assignment so a rotation
                            // has to be chosen deliberately.
                            campus_days: lvl.id === 'sped'
                              ? [...ALL_CAMPUS_DAYS]
                              : prev.student_level === 'sped'
                                ? []
                                : prev.campus_days,
                            schedule: lvl.id === 'sped' || prev.student_level === 'sped'
                              ? ''
                              : prev.schedule,
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
                        className={formErrors.student_id || dupErrors.student_id ? 'input-error' : ''}
                      />
                      <span className="field-hint">{FIELD_PATTERNS.student_id.hint}</span>
                      {!formErrors.student_id && dupChecking.student_id && <span className="field-checking-msg">Checking availability…</span>}
                    </div>
                  )}

                  {/* College: separate program + year level fields */}
                  {formData.student_level === 'college' && (
                    <>
                      <div className="form-group">
                        <label>Program <span className="required">*</span></label>
                        <ComboBox
                          name="student_program"
                          value={formData.student_program}
                          onChange={(e) => setFormData(prev => ({
                            ...prev,
                            student_program: e.target.value,
                            // Year options depend on the program — reset stale picks
                            ...(stripYear(e.target.value) !== stripYear(prev.student_program) ? { student_year: '' } : {}),
                          }))}
                          options={programOptions}
                          placeholder="e.g. BSIT"
                          required
                        />
                      </div>
                      <div className="form-group">
                        <label>Year Level <span className="required">*</span></label>
                        <select name="student_year" value={formData.student_year} onChange={handleInputChange} required>
                          <option value="">Select Year</option>
                          {yearOptions.map(y => <option key={y} value={y}>{`Year ${y}`}</option>)}
                        </select>
                      </div>
                    </>

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
                      <label>Grade Level <span style={{ color: '#6B8CA6', fontWeight: 400 }}>(optional)</span></label>
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
                      className={formErrors.employee_id || dupErrors.employee_id ? 'input-error' : ''}
                    />
                    <span className="field-hint">{FIELD_PATTERNS.employee_id.hint}</span>
                    {!formErrors.employee_id && dupChecking.employee_id && <span className="field-checking-msg">Checking availability…</span>}
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
                      {/* No fee hint here on purpose — see DEPARTMENT_OPTIONS */}
                      {DEPARTMENT_OPTIONS.map(d => (
                        <option key={d.value} value={d.label}>{d.label}</option>
                      ))}
                    </select>
                  </div>
                </>
              )}

              <div className="form-group">
                <label>Contact Number <span className="required">*</span></label>
                <div className={`phone-field${formErrors.contact_number ? ' input-error' : ''}`}>
                  <span className="phone-prefix">{PH_DIAL_CODE}</span>
                  <input
                    type="tel"
                    inputMode="numeric"
                    autoComplete="tel-national"
                    maxLength={10}
                    name="contact_number"
                    value={toDisplayMobile(formData.contact_number)}
                    onChange={handleInputChange}
                    required
                    placeholder="9123456789"
                  />
                </div>
                <span className="field-hint">10 digits after +63 — e.g. 9123456789</span>
              </div>

              <div className="form-group">
                <label>{guardianDriven ? "Student's Age" : 'Age'}</label>
                <select name="age" value={formData.age} onChange={handleInputChange}>
                  <option value="">Select Age</option>
                  {ageOptions(guardianDriven ? 3 : 15).map(a => (
                    <option key={a} value={a}>{a}</option>
                  ))}
                </select>
              </div>

              {/* Who drives — students only. JHS/Elementary/SpEd skip the choice. */}
              {isStudent && formData.student_level && (
                <div className="form-group col-span-2">
                  <label>Who will drive this vehicle? <span className="required">*</span></label>
                  {isGuardianOnlyLevel ? (
                    <div className="schedule-note driver-minor-note">
                      <Info size={13} />
                      <span>
                        {GUARDIAN_ONLY_REASON[formData.student_level]} A{' '}
                        <strong>parent, guardian, or authorized driver</strong> must
                        be registered as this vehicle's driver.
                      </span>
                    </div>
                  ) : (
                    <>
                      <div className="student-level-picker">
                        {[
                          { id: 'self',     label: 'Student drives (self)' },
                          { id: 'guardian', label: 'Parent / Guardian / Authorized driver' },
                        ].map(opt => (
                          <button
                            key={opt.id}
                            type="button"
                            className={`student-level-btn${formData.who_drives === opt.id ? ' active' : ''}`}
                            onClick={() => setFormData(prev => ({
                              ...prev,
                              who_drives: opt.id,
                              ...(opt.id === 'self' ? { driver_name: '', driver_relationship: '', driver_contact: '' } : {}),
                            }))}
                          >
                            {opt.label}
                          </button>
                        ))}
                      </div>
                      {formData.student_level === 'sped' && (
                        <span className="field-hint">Select “Student drives” only if the student holds a valid driver's license.</span>
                      )}
                    </>
                  )}
                </div>
              )}

              {/* ── Campus Schedule picker ──
                  The first-come-first-serve notice sits at the top of the form;
                  the rotation itself is claimed here, right after the driver is
                  settled, so the whole student block reads in one pass. */}
              {isStudent && (
                <div className="form-group col-span-2">
                  <label className="days-label">
                    Select Your Campus Schedule <span className="required">*</span>
                  </label>
                  {formData.student_level === 'sped' ? (
                    <div className="schedule-group-picker">
                      <div className="schedule-group-card schedule-group-card--sped">
                        <span className="schedule-group-days">Monday – Saturday</span>
                        <span className="schedule-group-caption">All campus days assigned</span>
                      </div>
                    </div>
                  ) : (
                    <div className="schedule-group-picker">
                      {SCHEDULE_GROUPS.map(group => {
                        const slot = groupSlots(group)
                        const isFull = slot?.available === 0
                        const isSelected = formData.schedule === group.code
                        return (
                          <button
                            key={group.code}
                            type="button"
                            className={[
                              'schedule-group-card',
                              isSelected ? 'schedule-group-card--selected' : '',
                              isFull ? 'schedule-group-card--full' : '',
                            ].filter(Boolean).join(' ')}
                            onClick={() => !isFull && selectSchedule(group)}
                            disabled={isFull}
                            aria-pressed={isSelected}
                            title={isFull ? `The ${group.short} schedule is full` : group.caption}
                          >
                            <span className="schedule-group-days">{group.short}</span>
                            <span className="schedule-group-caption">{group.caption}</span>
                            <span className="schedule-group-slots">
                              {loadingSlots
                                ? '···'
                                : slot
                                  ? (isFull ? 'FULL' : `${slot.available} slot${slot.available !== 1 ? 's' : ''} left`)
                                  : '—'}
                            </span>
                          </button>
                        )
                      })}
                    </div>
                  )}

                  <div className="campus-day-summary">
                    <span className="campus-day-counter">
                      {formData.student_level === 'sped'
                        ? 'Entry is allowed Monday to Saturday.'
                        : formData.schedule
                          ? `You may enter on ${formData.campus_days.join(', ')}.`
                          : 'No schedule selected yet.'}
                    </span>
                  </div>
                </div>
              )}

              {guardianDriven ? (
                <>
                  <div className="form-group">
                    <label>Driver's Full Name <span className="required">*</span></label>
                    <input
                      type="text"
                      name="driver_name"
                      value={formData.driver_name}
                      onChange={handleInputChange}
                      required
                      placeholder="e.g. DELA CRUZ, JUAN"
                    />
                  </div>
                  <div className="form-group">
                    <label>Relationship to Student <span className="required">*</span></label>
                    <select name="driver_relationship" value={formData.driver_relationship} onChange={handleInputChange} required>
                      <option value="">Select Relationship</option>
                      <option value="parent">Parent</option>
                      <option value="guardian">Guardian</option>
                      <option value="authorized_driver">Authorized Driver</option>
                    </select>
                  </div>
                  <div className="form-group">
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
                    <span className="field-hint">The authorized driver's LTO license — {FIELD_PATTERNS.drivers_license.hint}</span>
                  </div>
                  <div className="form-group">
                    <label>Driver's Contact Number</label>
                    <div className={`phone-field${formErrors.driver_contact ? ' input-error' : ''}`}>
                      <span className="phone-prefix">{PH_DIAL_CODE}</span>
                      <input
                        type="tel"
                        inputMode="numeric"
                        autoComplete="tel-national"
                        maxLength={10}
                        name="driver_contact"
                        value={toDisplayMobile(formData.driver_contact)}
                        onChange={handleInputChange}
                        placeholder="9123456789"
                      />
                    </div>
                    <span className="field-hint">10 digits after +63 — e.g. 9123456789</span>
                  </div>
                </>
              ) : (
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
                </div>
              )}

              <div className="form-group col-span-2">
                <label>Driver's License Photo <span className="required">*</span></label>

                {!licenseImage ? (
                  <label className="license-upload">
                    <input
                      type="file"
                      accept={LICENSE_IMAGE_TYPES.join(',')}
                      onChange={handleLicenseImageChange}
                      className="license-upload-input"
                    />
                    <Upload size={18} className="license-upload-icon" />
                    <span className="license-upload-text">
                      <strong>Choose a photo</strong>
                      <span>JPG, PNG, WEBP or HEIC · up to {LICENSE_IMAGE_MAX_MB}MB</span>
                    </span>
                  </label>
                ) : (
                  <div className="license-preview">
                    {licensePreview ? (
                      <img src={licensePreview} alt="Driver's license preview" className="license-preview-img" />
                    ) : (
                      <div className="license-preview-img license-preview-noimg">HEIC</div>
                    )}
                    <div className="license-preview-meta">
                      <span className="license-preview-name" title={licenseImage.name}>{licenseImage.name}</span>
                      <span className="license-preview-size">{formatFileSize(licenseImage.size)}</span>
                    </div>
                    <button
                      type="button"
                      className="license-preview-remove"
                      onClick={clearLicenseImage}
                      aria-label="Remove driver's license photo"
                    >
                      <X size={15} />
                    </button>
                  </div>
                )}

                <span className="field-hint">
                  Attach a clear, readable photo of the driver's license — CDSO checks it against
                  the license number above before approving the application.
                </span>
              </div>

              {/* Assessment form — the enrolment proof. Students only: an employee
                  or a fetching parent has no assessment to show. */}
              {isStudent && (
                <div className="form-group col-span-2">
                  <label>Assessment Form <span className="required">*</span></label>

                  {!assessmentFile ? (
                    <label className="license-upload">
                      <input
                        type="file"
                        accept={ASSESSMENT_FILE_TYPES.join(',')}
                        onChange={handleAssessmentChange}
                        className="license-upload-input"
                      />
                      <Upload size={18} className="license-upload-icon" />
                      <span className="license-upload-text">
                        <strong>Choose a file</strong>
                        <span>JPG, PNG, WEBP, HEIC or PDF · up to {ASSESSMENT_FILE_MAX_MB}MB</span>
                      </span>
                    </label>
                  ) : (
                    <div className="license-preview">
                      <div className="license-preview-img license-preview-noimg">
                        <FileText size={20} />
                      </div>
                      <div className="license-preview-meta">
                        <span className="license-preview-name" title={assessmentFile.name}>{assessmentFile.name}</span>
                        <span className="license-preview-size">{formatFileSize(assessmentFile.size)}</span>
                      </div>
                      <button
                        type="button"
                        className="license-preview-remove"
                        onClick={clearAssessmentFile}
                        aria-label="Remove assessment form"
                      >
                        <X size={15} />
                      </button>
                    </div>
                  )}

                  <span className="field-hint">
                    Your latest registrar's assessment form — this is what confirms you are an
                    enrolled SLC student. A clear photo or the PDF from the student portal both work.
                  </span>
                </div>
              )}
            </div>

            {/* ── Fetcher Classification & Students ── */}
            {isFetcher && (
              <>
                <hr className="divider" />
                <h3 className="section-heading">Fetcher Classification <span className="required">*</span></h3>
                <div className="reg-type-inline">
                  <button
                    type="button"
                    className={`reg-type-inline-btn${fetcherType === 'drop_and_go' ? ' selected' : ''}`}
                    onClick={() => setFetcherType('drop_and_go')}
                  >
                    <span className="reg-type-inline-icon"><Clock size={24} /></span>
                    <span className="reg-type-inline-label">Fetcher / Drop &amp; Go</span>
                    <span className="reg-type-inline-desc">Entry only during the allotted drop-off &amp; pick-up times</span>
                  </button>
                  <button
                    type="button"
                    className={`reg-type-inline-btn${fetcherType === 'standby' ? ' selected' : ''}`}
                    onClick={() => setFetcherType('standby')}
                  >
                    <span className="reg-type-inline-icon"><Car size={24} /></span>
                    <span className="reg-type-inline-label">Standby</span>
                    <span className="reg-type-inline-desc">Allowed to park inside the campus while waiting</span>
                  </button>
                </div>

                <hr className="divider" />
                <h3 className="section-heading">Students to Fetch <span className="required">*</span></h3>
                <p className="field-hint" style={{ display: 'block', marginBottom: 12 }}>
                  List at least one student you will be fetching, and attach each one's assessment
                  form. Use "Add another student" if you fetch more than one.
                </p>
                {fetcherStudents.map((s, i) => (
                  <div key={i} className="fetcher-student-card">
                    <div className="fetcher-student-head">
                      <span>Student #{i + 1}</span>
                      {fetcherStudents.length > 1 && (
                        <button
                          type="button"
                          className="fetcher-student-remove"
                          onClick={() => setFetcherStudents(prev => prev.filter((_, j) => j !== i))}
                        >
                          Remove
                        </button>
                      )}
                    </div>
                    <div className="form-grid">
                      <div className="form-group">
                        <label>Full Name <span className="required">*</span></label>
                        <input
                          type="text"
                          value={s.full_name}
                          onChange={e => updateFetcherStudent(i, 'full_name', e.target.value)}
                          placeholder="Last Name, First Name, Middle Name"
                        />
                      </div>
                      <div className="form-group">
                        <label>Student ID <span className="required">*</span></label>
                        <input
                          type="text"
                          value={s.student_id}
                          onChange={e => updateFetcherStudent(i, 'student_id', e.target.value)}
                          placeholder={FIELD_PATTERNS.student_id.hint}
                        />
                      </div>
                      <div className="form-group">
                        <label>Education Level <span className="required">*</span></label>
                        <select
                          value={s.student_level}
                          onChange={e => updateFetcherStudent(i, 'student_level', e.target.value)}
                        >
                          <option value="">Select level…</option>
                          {FETCHER_STUDENT_LEVELS.map(lvl => (
                            <option key={lvl.id} value={lvl.id}>{lvl.label}</option>
                          ))}
                        </select>
                      </div>
                      <div className="form-group">
                        <label>Program / Grade Level</label>
                        <input
                          type="text"
                          value={s.program_year}
                          onChange={e => updateFetcherStudent(i, 'program_year', e.target.value)}
                          placeholder="e.g. BSIT - 3 or Grade 7"
                        />
                      </div>

                      {/* Enrolment proof for this student. No preview, for the
                          same reason as the applicant's own: most are PDFs and
                          the rest are dense scans. */}
                      <div className="form-group col-span-2">
                        <label>Assessment Form <span className="required">*</span></label>
                        {!s.assessment ? (
                          <label className="license-upload">
                            <input
                              type="file"
                              accept={ASSESSMENT_FILE_TYPES.join(',')}
                              onChange={e => handleFetcherAssessmentChange(i, e)}
                              className="license-upload-input"
                            />
                            <Upload size={18} className="license-upload-icon" />
                            <span className="license-upload-text">
                              <strong>Choose a file</strong>
                              <span>JPG, PNG, WEBP, HEIC or PDF · up to {ASSESSMENT_FILE_MAX_MB}MB</span>
                            </span>
                          </label>
                        ) : (
                          <div className="license-preview">
                            <div className="license-preview-img license-preview-noimg">
                              <FileText size={20} />
                            </div>
                            <div className="license-preview-meta">
                              <span className="license-preview-name" title={s.assessment.name}>{s.assessment.name}</span>
                              <span className="license-preview-size">{formatFileSize(s.assessment.size)}</span>
                            </div>
                            <button
                              type="button"
                              className="license-preview-remove"
                              onClick={() => updateFetcherStudent(i, 'assessment', null)}
                              aria-label={`Remove assessment form for student #${i + 1}`}
                            >
                              <X size={15} />
                            </button>
                          </div>
                        )}
                        <span className="field-hint">
                          This student's latest registrar's assessment form — what confirms they
                          are enrolled at SLC.
                        </span>
                      </div>
                    </div>
                  </div>
                ))}
                <button
                  type="button"
                  className="fetcher-add-student-btn"
                  onClick={() => setFetcherStudents(prev => [...prev, { ...EMPTY_FETCHER_STUDENT }])}
                >
                  + Add another student
                </button>
              </>
            )}

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
                  {/* The exemption is not stated here. Naming it on the form
                      lets anyone discover it by trying each department, which
                      is the false-registration problem this wording avoids.
                      Worded so it is still true for exempt staff — their
                      assessed fee is simply zero — and they are told plainly
                      on the confirmation screen after submitting. */}
                  {feeExempt ? (
                    <li>To settle the Vehicle Pass fee assessed for your department at the
                      <strong> Accounting Office</strong>, where one applies, and to upload the
                      Official Receipt (OR) using the link sent to my email.</li>
                  ) : (
                    <li>To pay the Vehicle Pass fee of <strong>₱{vehiclePassFee.toFixed(2)}</strong>{isEmployee && ' (50% employee discount applied)'} at the <strong>Accounting Office</strong>, and to upload the Official Receipt (OR) using the link sent to my email.</li>
                  )}
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

              {/* Attestation — sits between the terms and the privacy consent so the
                  applicant ticks it while the terms are still on screen. Separate
                  from privacy_consent on purpose: agreeing to be bound by the rules
                  and vouching for the data are two different promises, and CDSO
                  rejects applications for the second far more often than the first. */}
              <div className="consent-section">
                <label className="consent-label">
                  <input
                    type="checkbox"
                    name="details_confirmed"
                    checked={formData.details_confirmed}
                    onChange={handleInputChange}
                    required
                    className="consent-checkbox"
                  />
                  <span>
                    <strong>CONFIRMATION OF DETAILS:</strong> I confirm that all the details I
                    have entered in this form — my personal information, vehicle details and the
                    documents I attached — are <strong>true, complete and correct</strong>. I
                    understand that any false or misleading information is grounds for the denial
                    or revocation of my vehicle pass.
                  </span>
                </label>
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



            <div className="form-actions">
              <button
                type="submit"
                className="btn-submit"
                disabled={submitting}
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
