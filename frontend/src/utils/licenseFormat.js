// Auto-inserts dashes for the LTO licence format: X00-00-000000.
// Strips any existing dashes first so the cursor position doesn't confuse things.
//
// Shared rather than duplicated: the registration form and the two detail-edit
// screens all type into this field, and a second implementation that grouped
// the digits differently would produce a value the server's LICENSE_RE refuses
// on one screen and accepts on another.
export function formatDriversLicense(raw) {
  const clean = String(raw || '').replace(/[^A-Za-z0-9]/g, '').toUpperCase().slice(0, 11)
  if (clean.length <= 3) return clean
  if (clean.length <= 5) return `${clean.slice(0, 3)}-${clean.slice(3)}`
  return `${clean.slice(0, 3)}-${clean.slice(3, 5)}-${clean.slice(5)}`
}

// Mirrors LICENSE_RE in backend/vehicles/registration_edits.py.
export const LICENSE_RE = /^[A-Z]\d{2}-\d{2}-\d{6}$/

export function isValidDriversLicense(raw) {
  return LICENSE_RE.test(String(raw || '').trim().toUpperCase())
}
