import { useState, useEffect, useCallback } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { CheckCircle, AlertTriangle, Loader2 } from 'lucide-react'
import { registrationApi } from '../../api/registration'
import slcLogo from '../../assets/slclogo.jpg'
import './RegisterPage.css'

const SLC_HEADER = (
  <header className="register-header">
    <div className="header-content">
      <div className="header-logo-group">
        <img src={slcLogo} alt="Saint Louis College Logo" className="header-logo" />
        <div className="header-text">
          <span className="header-title">SAINT LOUIS COLLEGE</span>
          <span className="header-subtitle">Vehicle Management System with entry authentication</span>
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

  const [formData, setFormData] = useState({
    full_name: '',
    email: '',
    address: '',
    contact_number: '',
    age: '',
    drivers_license: '',
    campus_days: [],
    student_id: '',
    program_year: '',
    employee_id: '',
    department: '',
    plate_number: '',
    conduction_number: '',
    vehicle_type: '',
    vehicle_color: '',
    body_number: '',
    privacy_consent: false
  })

  const validateToken = useCallback(async () => {
    if (!token) {
      setError("Invalid registration link. Token is missing.")
      setLoading(false)
      return
    }
    try {
      const data = await registrationApi.validateToken(token)
      setRegistrantType(data.registrant_type)
      setLoading(false)
    } catch (err) {
      setError(err.response?.data?.error || "This registration link is invalid, expired, or has already been used.")
      setLoading(false)
    }
  }, [token])

  useEffect(() => {
    validateToken()
  }, [validateToken])

  const handleInputChange = (e) => {
    const { name, value, type, checked } = e.target
    if (type === 'checkbox' && name !== 'privacy_consent') {
      let updatedDays = [...formData.campus_days]
      if (checked) {
        if (updatedDays.length >= 3) {
          e.preventDefault()
          return
        }
        updatedDays.push(value)
      } else {
        updatedDays = updatedDays.filter(day => day !== value)
      }
      setFormData({ ...formData, campus_days: updatedDays })
    } else if (type === 'checkbox') {
      setFormData({ ...formData, [name]: checked })
    } else {
      setFormData({ ...formData, [name]: value })
    }
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!formData.privacy_consent) {
      alert("You must agree to the Data Privacy Consent.")
      return
    }

    setLoading(true)
    try {
      await registrationApi.submitRegistration(token, formData)
      setSubmitted(true)
    } catch (err) {
      alert(err.response?.data?.error || "Failed to submit registration. Please try again.")
      console.error(err)
    } finally {
      setLoading(false)
    }
  }

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
            <p className="card-help">Please contact the administration office for a new registration link.</p>
          </div>
        </main>
      </div>
    )
  }

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
            <p className="card-message">Your vehicle registration application has been submitted successfully and is pending review.</p>
            <p className="card-help">You will receive an email notification once your application has been processed.</p>
            <button className="card-btn" onClick={() => navigate('/login')}>Proceed to Login</button>
          </div>
        </main>
      </div>
    )
  }

  return (
    <div className="register-page">
      {SLC_HEADER}

      <main className="register-main">
        <div className="register-card">
          <div className="register-card-header">
            <div className="card-header-icon">
              <CheckCircle size={28} color="#FFFFFF" />
            </div>
            <h1 className="register-card-title">Vehicle Registration</h1>
            <p className="register-card-subtitle">Saint Louis College</p>
            <div className="registrant-badge">
              {registrantType === 'student' ? 'Student Registration' : 'Employee Registration'}
            </div>
          </div>

          <form onSubmit={handleSubmit} className="register-form">
            <h3 className="section-heading">Personal Information</h3>
            <div className="form-grid">
              <div className="form-group col-span-2">
                <label>Full Name <span className="required">*</span></label>
                <input type="text" name="full_name" value={formData.full_name} onChange={handleInputChange} required placeholder="Last Name, First Name M.I." />
              </div>
              <div className="form-group col-span-2">
                <label>Email Address <span className="required">*</span></label>
                <input type="email" name="email" value={formData.email} onChange={handleInputChange} required />
              </div>
              
              {registrantType === 'student' ? (
                <>
                  <div className="form-group">
                    <label>Student ID Number <span className="required">*</span></label>
                    <input type="text" name="student_id" value={formData.student_id} onChange={handleInputChange} required />
                  </div>
                  <div className="form-group">
                    <label>Program & Year Level <span className="required">*</span></label>
                    <input type="text" name="program_year" value={formData.program_year} onChange={handleInputChange} required placeholder="e.g. BSIT - 3" />
                  </div>
                </>
              ) : (
                <>
                  <div className="form-group">
                    <label>Employee ID <span className="required">*</span></label>
                    <input type="text" name="employee_id" value={formData.employee_id} onChange={handleInputChange} required />
                  </div>
                  <div className="form-group">
                    <label>Department <span className="required">*</span></label>
                    <input type="text" name="department" value={formData.department} onChange={handleInputChange} required />
                  </div>
                </>
              )}

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
                <input type="number" name="age" value={formData.age} onChange={handleInputChange} />
              </div>

              <div className="form-group col-span-2">
                <label>Driver's License Number <span className="required">*</span></label>
                <input type="text" name="drivers_license" value={formData.drivers_license} onChange={handleInputChange} required />
              </div>

              <div className="form-group col-span-2">
                <label>Campus Days <span className="text-sm font-normal text-gray-500">(Check 3 days maximum)</span></label>
                <div className="checkbox-group inline-flex flex-wrap gap-4 mt-2">
                  {['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'].map(day => (
                    <label key={day} className="flex items-center gap-2 cursor-pointer">
                      <input 
                        type="checkbox" 
                        value={day} 
                        checked={formData.campus_days.includes(day)}
                        onChange={handleInputChange}
                        disabled={!formData.campus_days.includes(day) && formData.campus_days.length >= 3}
                      />
                      <span className="text-sm">{day}</span>
                    </label>
                  ))}
                </div>
              </div>
            </div>

            <hr className="divider" />

            <h3 className="section-heading">Vehicle Information</h3>
            <div className="form-grid">
              <div className="form-group">
                <label>Plate Number <span className="required">*</span></label>
                <input type="text" name="plate_number" value={formData.plate_number} onChange={handleInputChange} required />
              </div>
              <div className="form-group">
                <label>Conduction Number <span className="text-sm font-normal text-gray-500">(If newly purchased)</span></label>
                <input type="text" name="conduction_number" value={formData.conduction_number} onChange={handleInputChange} />
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
                <label>Body Number <span className="text-sm font-normal text-gray-500">(For Tricycle only)</span></label>
                <input type="text" name="body_number" value={formData.body_number} onChange={handleInputChange} disabled={formData.vehicle_type !== 'Tricycle'} />
              </div>
            </div>

            <hr className="divider" />

            <div className="consent-section">
              <label className="flex items-start gap-3 cursor-pointer">
                <input 
                  type="checkbox" 
                  name="privacy_consent" 
                  checked={formData.privacy_consent}
                  onChange={handleInputChange}
                  className="mt-1"
                  required
                />
                <span className="text-sm text-gray-600 leading-relaxed">
                  <strong>DATA PRIVACY CONSENT:</strong> By filling-out this form, I agree to the collection and processing of my personal data for the purpose of Vehicle Registration and Management at Saint Louis College, in compliance with the Data Privacy Act of 2012. I understand that my information will be kept strictly confidential and will only be accessed by authorized personnel.
                </span>
              </label>
            </div>

            <div className="form-actions">
              <button type="submit" className="btn-submit" disabled={loading}>
                {loading ? 'Submitting...' : 'Submit Registration'}
              </button>
            </div>
          </form>
        </div>
      </main>
    </div>
  )
}
