// Auto-formats plate as the user types. Only inserts a space for the common
// 2-3 letter prefix + digit patterns (e.g. ABC1234 → ABC 1234, AB1234 → AB 1234).
// Other formats (N123BC, 123ABC, 1234) are left as-is since they have no standard separator.
export function formatPlateNumber(raw) {
  const upper = raw.toUpperCase().replace(/[^A-Z0-9\s-]/g, '')
  // Only auto-insert space if the user hasn't already typed one
  if (!/[\s-]/.test(upper)) {
    const m = upper.match(/^([A-Z]{2,3})(\d.*)$/)
    if (m) return m[1] + ' ' + m[2]
  }
  return upper
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
  const n = raw.replace(/[\s\-_]/g, '').toUpperCase()
  if (!n) return false
  return PH_PLATE_PATTERNS.some(p => p.test(n))
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
  const n = (raw || '').replace(/[\s\-_]/g, '').toUpperCase()
  return /^[A-Z0-9]{5,12}$/.test(n)
}
