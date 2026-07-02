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
]

// Checks a plate against known Philippine plate formats (ignoring spaces/dashes).
export function isValidPlateNumber(raw) {
  const n = raw.replace(/[\s\-_]/g, '').toUpperCase()
  if (!n) return false
  return PH_PLATE_PATTERNS.some(p => p.test(n))
}
