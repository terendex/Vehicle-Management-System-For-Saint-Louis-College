import { formatPlateNumber, isValidPlateNumber } from '../../utils/plateFormat'
import { formatDriversLicense, isValidDriversLicense } from '../../utils/licenseFormat'

/* The vocabularies and rules behind the editable-detail form.

   Split from DetailFields.jsx rather than living beside the component: a module
   that exports both a component and plain helpers breaks Fast Refresh, and both
   halves are imported by the two screens anyway (the public correction page and
   the owner dashboard's modal). Named `detailRules`, not `detailFields`, because
   the latter resolves to DetailFields.jsx on a case-insensitive filesystem.

   Validation here is only the immediate kind — the shape of a plate, the shape
   of a licence. Everything that needs the database (is this plate free, does
   this department exist) is the server's answer, and it gives the same answer to
   both screens. */

export const VEHICLE_TYPES = [
  'Sedan', 'SUV', 'Motorcycle', 'Tricycle', 'E-Bike', 'Van', 'Truck', 'Other',
]

/* A–Z, and matching the registration form's list exactly. "Other" is the
   escape hatch that reveals the free-text box, so it stays pinned last rather
   than being alphabetised in between Orange and Red. */
export const VEHICLE_COLORS = [
  'Beige', 'Black', 'Blue', 'Brown', 'Gold', 'Gray', 'Green',
  'Maroon', 'Orange', 'Red', 'Silver', 'White', 'Yellow',
]

export const DEPARTMENTS = ['Teaching', 'Non-Teaching', 'Cleaning and Services']

export const DRIVER_RELATIONSHIPS = [
  { value: 'parent',            label: 'Parent' },
  { value: 'guardian',          label: 'Guardian' },
  { value: 'authorized_driver', label: 'Authorized Driver' },
]

const CONDUCTION_RE = /^[A-Z0-9]{5,12}$/

/* One field's value, formatted the way its column is stored. Uppercase for the
   name/colour fields is the form's own convention, not a whim — the stored
   values are uppercase, so typing lowercase here would show as a change that
   the server then normalises away. */
export function formatDetailValue(field, value) {
  if (field === 'plate_number') return formatPlateNumber(value)
  if (field === 'drivers_license') return formatDriversLicense(value)
  if (field === 'conduction_number') return (value || '').toUpperCase().replace(/\s/g, '')
  if (['full_name', 'vehicle_color', 'body_number', 'driver_name'].includes(field)) {
    return (value || '').toUpperCase()
  }
  return value
}

/* What is wrong with this one value, or null. Only the checks that need no
   database — the rest is the server's to answer. */
export function detailFieldProblem(field, value, { editable = [] } = {}) {
  const text = (value || '').trim()
  const required = editable.some(f => f.field === field)
  if (!required) return null

  switch (field) {
    case 'full_name':
      return text ? null : 'Enter your full name.'
    case 'drivers_license':
      if (!text) return "Enter your driver's license number."
      return isValidDriversLicense(text) ? null : "Driver's license must look like A00-00-000000."
    case 'plate_number':
      if (!text) return 'Enter your plate number.'
      return isValidPlateNumber(text) ? null : 'Enter a valid Philippine plate number.'
    case 'conduction_number':
      if (!text) return 'Enter your conduction number.'
      return CONDUCTION_RE.test(text) ? null : 'Conduction number must be 5–12 letters or digits.'
    case 'program_year':
      return text ? null : 'Enter your program and year.'
    case 'department':
      return text ? null : 'Choose your department.'
    case 'vehicle_type':
      return text ? null : 'Choose your vehicle type.'
    case 'vehicle_color':
      return text ? null : 'Choose or enter your vehicle colour.'
    default:
      // body_number and the authorized-driver pair may legitimately be blank;
      // the pairing rule between the two is the server's, which sees both.
      return null
  }
}

/* Every problem across the whole form, for a single validation pass on submit.
   Only the fields the person actually touched are checked: a blank they never
   opened is the value already on file, and the server would refuse it anyway
   with a better message than a client guess. */
export function detailFormProblems(values, original, editable) {
  const problems = []
  editable.forEach(({ field }) => {
    if ((values[field] ?? '') === (original[field] ?? '')) return
    const problem = detailFieldProblem(field, values[field], { editable })
    if (problem) problems.push(problem)
  })
  return problems
}

/* The fields whose value differs from what is on file — what actually gets
   posted. Sending the untouched ones too would work (the server discards
   no-ops) but it would also mean an owner's change request listed every field
   they looked at, which is not what a reviewer needs to read. */
export function changedValues(values, original) {
  const changed = {}
  Object.keys(values).forEach(field => {
    if ((values[field] ?? '') !== (original[field] ?? '')) changed[field] = values[field]
  })
  return changed
}
