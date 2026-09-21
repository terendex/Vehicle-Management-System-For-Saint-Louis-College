// Auto-inserts dashes for the LTO licence format: X00-00-000000.
// Strips any existing dashes first so the cursor position doesn't confuse things.
//
// Shared rather than duplicated: the registration form and the two detail-edit
// screens all type into this field, and a second implementation that grouped
// the digits differently would produce a value the server's LICENSE_RE refuses
// on one screen and accepts on another.
export function formatDriversLicense(raw) {
  // Strip, upper, then CAP AT 11 — the number of real characters in
  // X00-00-000000 once the two dashes are removed. Capping here rather than on
  // the formatted string is what makes typing past the end simply stop,
  // instead of pushing digits through the groups.
  const clean = String(raw || '').replace(/[^A-Za-z0-9]/g, '').toUpperCase().slice(0, 11)
  // Dashes appear only once there is something after them, so a half-typed
  // licence never shows a trailing '-' the user has to delete.
  if (clean.length <= 3) return clean
  if (clean.length <= 5) return `${clean.slice(0, 3)}-${clean.slice(3)}`
  return `${clean.slice(0, 3)}-${clean.slice(3, 5)}-${clean.slice(5)}`
}

// Mirrors LICENSE_RE in backend/vehicles/registration_edits.py.
export const LICENSE_RE = /^[A-Z]\d{2}-\d{2}-\d{6}$/

// Upper-cased before testing, because LICENSE_RE only accepts a capital
// letter — a value typed in lower case is valid, just not yet normalised.
export function isValidDriversLicense(raw) {
  return LICENSE_RE.test(String(raw || '').trim().toUpperCase())
}
