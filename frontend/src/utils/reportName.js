// Readable, filesystem-safe report filename: "<Name> - YYYY-MM-DD HH-MM AM.ext"
// e.g. reportFileName('Audit Log Report', 'pdf') -> "Audit Log Report - 2026-07-22 09-30 PM.pdf"
export function reportFileName(name, ext) {
  const d = new Date()
  const pad = (n) => String(n).padStart(2, '0')
  let h = d.getHours()
  const ampm = h >= 12 ? 'PM' : 'AM'
  h = h % 12 || 12
  const stamp = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(h)}-${pad(d.getMinutes())} ${ampm}`
  return `${name} - ${stamp}.${ext}`
}
