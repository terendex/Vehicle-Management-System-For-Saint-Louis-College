// Auto-formats plate as the user types. Only inserts a space for the common
// 2-3 letter prefix + digit patterns (e.g. ABC1234 → ABC 1234, AB1234 → AB 1234).
// Other formats (N123BC, 123ABC, 1234) are left as-is since they have no standard separator.
// =============================================================================
// Plate identifiers, and the three kinds this system recognises.
//
//   a PLATE            ABC 1234 and eleven other Philippine shapes
//   a CONDUCTION no.   what a brand-new car carries until its plate arrives
//   a CONTROL no.      FM-001…, issued by this system to an e-bike and stored
//                      in the plate_number column
//
// Two of the three mirror backend rules and say so at their definitions. Where
// they differ in STRICTNESS is deliberate and explained at each: the plate
// patterns are exact because OCR fragments were passing as plates, while the
// conduction check is loose because the server is the authority and an
// over-strict client locks a guard out of a car that is genuinely registered.
// =============================================================================
export function formatPlateNumber(raw) {
  // Spaces and dashes survive the strip, because the next test needs to know
  // whether the user has already typed a separator.
  const upper = raw.toUpperCase().replace(/[^A-Z0-9\s-]/g, '')
  // Only auto-insert space if the user hasn't already typed one
  // Only auto-insert space if the user hasn't already typed one
  // — otherwise typing "ABC 1234" would become "ABC  1234", and a user
  // correcting their own spacing would fight the field.
  if (!/[\s-]/.test(upper)) {
    // 2-3 letters followed by a digit. `.*` rather than a digit count, so the
    // space appears as soon as the shape is recognisable and does not wait
    // for the plate to be finished.
    const m = upper.match(/^([A-Z]{2,3})(\d.*)$/)
    if (m) return m[1] + ' ' + m[2]
  }
  return upper                                  // every other shape is returned untouched — see the note above
}

// Mirrors PH_PLATE_PATTERNS in backend/scanning/ml/validator.py.
const PH_PLATE_PATTERNS = [
  /^[A-Z]{3}\d{4}$/,             // ABC1234  — standard car (post-2014)
  /^[A-Z]{3}\d{3}$/,             // ABC123   — pre-2014 car
  /^\d{3}[A-Z]{3}$/,             // 123ABC
  /^[A-Z]\d{3}[A-Z]{2}$/,        // N123BC
  /^[A-Z]{2}\d{3}[A-Z]$/,        // NB123C
  /^[A-Z]\d{4}[A-Z]$/,           // N1234C
  /^[A-Z]{2}\d{4}[A-Z]$/,        // AB1234C — exact shapes only; the old flexible
  /^[A-Z]\d{4}[A-Z]{2}$/,        // A1234BC   {1,2} variants also matched OCR fragments like B194G
  /^\d{7}$/,                      // 0011234  — diplomatic
  /^[A-Z]{2}\d{4}$/,             // AB1234   — motorcycle
  /^[A-Z]{2}\d{5}$/,             // AB12345
  /^\d{2}[A-Z]{3,4}$/,           // 12ABCD
  // Catch-all patterns removed (mirrors backend validator.py): they accepted
  // nearly any letters+digits string and let invalid plates through. The
  // pure-4-digit pattern (old motorcycle) was removed too — OCR partials of a
  // plate's digit block (AEB946 → "1946") passed as valid plates.
]

// Checks a plate against known Philippine plate formats (ignoring spaces/dashes).
export function isValidPlateNumber(raw) {
  // Separators removed before matching, so the patterns above describe the
  // CHARACTERS of a plate and none of them has to account for spacing. This
  // is why "ABC 1234" and "ABC-1234" both validate against /^[A-Z]{3}\d{4}$/.
  const n = raw.replace(/[\s\-_]/g, '').toUpperCase()
  if (!n) return false                          // empty is not valid; without this the .some() below would simply return false anyway, but the intent is clearer stated
  return PH_PLATE_PATTERNS.some(p => p.test(n))   // any one shape matching is enough
}

// A conduction sticker is what a brand-new car carries until its plate arrives,
// so by definition it is not a valid plate and never will match the patterns
// above. It has no national format either, so this mirrors the shape the
// registration form accepts (5–12 alphanumerics) rather than inventing a second
// rule for the gate to disagree with.
//
// Deliberately permissive: the server is the authority. ManualEntryView
// resolves the typed identifier against real vehicles first and only rejects
// what matches nothing, so a client-side guess that is too strict does not
// filter garbage — it locks a guard out of a car that is genuinely registered.
export function isValidConductionNumber(raw) {
  // `raw || ''` where isValidPlateNumber above takes raw directly — this one
  // is called with values that may be null (a registration's blank
  // conduction_number), the other only with typed input.
  const n = (raw || '').replace(/[\s\-_]/g, '').toUpperCase()
  return /^[A-Z0-9]{5,12}$/.test(n)             // a length range, not a shape: there is no national format to check against
}

// An e-bike's system-issued control number (FM-001, FM-002, ...). It is stored
// in plate_number, so this is how a screen knows to call it a control number.
// Mirrors backend/vehicles/control_numbers.py.
export function isControlNumber(raw) {
  return /^FM-\d+$/.test((raw || '').trim().toUpperCase())
}

export function plateLabel(raw) {
  return isControlNumber(raw) ? 'Control Number' : 'Plate Number'
}

// The identifier a vehicle is actually known by at the gate. A brand-new car is
// registered on a conduction sticker and its `plate_number` stays empty until
// the plate arrives (see the one-time plate swap), so reading `plate_number`
// alone yields '' for exactly those owners. An e-bike's FM- control number
// already lives in plate_number, so it needs no case of its own here.
export function vehicleIdentifier(reg) {
  return (reg?.plate_number || reg?.conduction_number || '').trim()
}

// The gate QR payload. Parsed by plateFromVehicleQr() in
// SecurityEntryManagement.jsx and mirrored by
// backend/vehicles/control_numbers.py gate_qr_payload(); the shape must stay
// `VEHICLE:{identifier}|ID:{registration id}`.
//
// Returns '' when there is no identifier at all, so a caller renders no QR
// rather than one encoding `VEHICLE:|ID:n` — which carries nothing to look the
// vehicle up by and makes the guard's scanner answer "Unrecognized QR".
export function vehicleQrPayload(reg) {
  const identifier = vehicleIdentifier(reg)
  return identifier && reg?.id ? `VEHICLE:${identifier}|ID:${reg.id}` : ''
}
