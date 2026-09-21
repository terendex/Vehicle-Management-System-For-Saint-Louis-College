// Readable, filesystem-safe report filename: "<Name> - YYYY-MM-DD HH-MM AM.ext"
// e.g. reportFileName('Audit Log Report', 'pdf') -> "Audit Log Report - 2026-07-22 09-30 PM.pdf"
export function reportFileName(name, ext) {
  const d = new Date()
  const pad = (n) => String(n).padStart(2, '0')
  let h = d.getHours()                          // 0-23
  const ampm = h >= 12 ? 'PM' : 'AM'            // decided BEFORE the conversion below, which destroys the distinction
  h = h % 12 || 12                              // `|| 12` turns both 0 and 12 into 12, so midnight is "12 AM" not "0 AM"
  // getMonth() is 0-based, hence the +1. Hyphens between hour and minute
  // rather than a colon: a colon is illegal in a Windows filename, and these
  // strings become downloads.
  const stamp = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(h)}-${pad(d.getMinutes())} ${ampm}`
  return `${name} - ${stamp}.${ext}`
}
