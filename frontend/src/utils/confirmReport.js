import notify from '../components/Feedback/notify'

/**
 * Ask before generating a PDF report. Resolves true to go ahead.
 *
 * One helper rather than a confirm written into each export button, so the
 * three places that produce a branded PDF — the shared report bar, the Audit
 * Log and the Vehicle Log — cannot drift into asking three different
 * questions for the same action.
 *
 * The dialog is worth the extra click because of what it LISTS, not because
 * it asks. A report is generated from filters that live in several controls
 * at once — status buttons, a type drop-down, a search box and a date range —
 * and the file that arrives is easy to read as wrong when one of them was set
 * and forgotten. Restating them at the moment of export is the last chance to
 * notice, and the empty case is called out in words rather than left to
 * arrive as a page with nothing on it.
 *
 * PDFs only. Excel already asks where to save (saveFile.saveReportFile), and
 * that dialog is its own chance to back out; a PDF opens in a new tab, which is
 * the interruption worth confirming.
 */
export function confirmPdfExport({ label = 'This report', summary = '', from = '', to = '', count = null } = {}) {
  const details = []

  if (from || to) {
    // Written out the way the PDF prints it under its title ("October 1, 2026
    // to October 5, 2026"); an open end reads as the earliest record, or today.
    const words = (iso) => new Date(iso + 'T00:00:00')
      .toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' })
    const end = to || new Date().toLocaleDateString('en-CA')
    details.push(from
      ? `Period: ${words(from)} to ${words(end)}`
      : `Period: up to ${words(end)}`)
  }
  if (summary) details.push(`Filters: ${summary}`)

  const known = typeof count === 'number' && Number.isFinite(count)
  if (known) details.push(count === 1 ? '1 entry matches' : `${count} entries match`)

  if (details.length === 0) {
    details.push('No filters applied — the report will cover every record.')
  }

  return notify.confirm({
    title:        'Generate PDF report?',
    message:      `${label} will open as a PDF in a new tab.`,
    // Only when we actually know the count is zero. Left silent when the
    // caller does not track one, rather than guessing at an empty file.
    description:  known && count === 0
      ? 'Nothing matches the current filters, so the report will come back empty.'
      : '',
    details,
    confirmLabel: 'Generate PDF',
    cancelLabel:  'Cancel',
  })
}

export default confirmPdfExport
