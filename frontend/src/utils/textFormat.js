// Shared input-formatting helpers so every form standardises text the same way.
// App-wide convention (matches the registration form): person/label names are
// UPPERCASE, emails are lowercase, plates are uppercased in ./plateFormat.js.

// Collapses runs of whitespace and trims the ends.
export function collapseSpaces(raw) {
  return raw.replace(/\s+/g, ' ').trim()
}

// Names/labels: collapse spaces and uppercase (e.g. "juan  dela cruz" → "JUAN DELA CRUZ").
export function toUpperName(raw) {
  return collapseSpaces(raw).toUpperCase()
}

// Emails are stored/compared lowercase — trim and lowercase.
export function normalizeEmail(raw) {
  return raw.trim().toLowerCase()
}
