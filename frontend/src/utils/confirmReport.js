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
 * PDFs only. Excel downloads silently to disk and can simply be deleted;
 * a PDF opens in a new tab, which is the interruption worth confirming.
 */
export function confirmPdfExport({ label = 'This report', summary = '', from = '', to = '', count = null } = {}) {
  const details = []

  if (from || to) {
    // 'the earliest record' / 'today' rather than a blank: an open-ended bound
    // is a real choice, and printing "Period:  to 2026-09-23" reads as a value
    // that failed to load.
    details.push(`Period: ${from || 'the earliest record'} to ${to || 'today'}`)
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
