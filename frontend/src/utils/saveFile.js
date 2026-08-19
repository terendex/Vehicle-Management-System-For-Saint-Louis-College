/**
 * Let the person choose where a downloaded file goes.
 *
 * A plain `<a download>` drops the file into whatever the browser calls the
 * download folder and tells nobody where that was. For a system backup that is
 * the wrong default — it is a file someone means to put on a specific drive and
 * keep — so where the browser supports it we open the real "Save as" dialog
 * first and write straight to the place they picked.
 *
 * `showSaveFilePicker` is Chromium-only (Chrome, Edge — what the campus runs)
 * and needs two things the caller has to respect:
 *
 *   1. It must be called from a user gesture. Anything awaited before it — a
 *      two-factor prompt, a fetch — spends that gesture and the dialog is
 *      refused. So callers ask for the location *first*, then do the work.
 *   2. Picking creates the file immediately, empty. If the work is then
 *      abandoned, `discardSaveLocation` cleans the stray file up.
 *
 * Firefox and Safari have no picker; there the fallback is the ordinary
 * download, which still works — it just does not ask.
 */

const supportsPicker = () => typeof window !== 'undefined' && 'showSaveFilePicker' in window

/** The ordinary browser download, used when there is no picker. */
export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

/**
 * Ask where to save. Call this synchronously from the click handler.
 *
 * Returns a target to hand to `saveBlobTo`, or `null` if the person cancelled
 * the dialog — which means "don't do the work at all", not "save it somewhere
 * else". A browser without a picker returns a target with no handle, so the
 * caller carries on and gets the fallback download at the end.
 */
export async function pickSaveLocation(suggestedName, { description = 'JSON file', accept = { 'application/json': ['.json'] } } = {}) {
  if (!supportsPicker()) return { handle: null }
  try {
    const handle = await window.showSaveFilePicker({
      suggestedName,
      types: [{ description, accept }],
    })
    return { handle }
  } catch (err) {
    // AbortError is the person closing the dialog — a real cancellation.
    if (err?.name === 'AbortError') return null
    // Anything else (a lost user gesture, a locked-down policy) is not worth
    // failing the download over; fall back to the browser's own download.
    return { handle: null }
  }
}

/**
 * Write the blob to the chosen location, or download it normally.
 * Resolves true when it went where the person picked, false for the fallback.
 */
export async function saveBlobTo(target, blob, fallbackName) {
  if (target?.handle) {
    const writable = await target.handle.createWritable()
    await writable.write(blob)
    await writable.close()
    return true
  }
  downloadBlob(blob, fallbackName)
  return false
}

/**
 * Best-effort removal of the empty file the picker created, for when the
 * download is abandoned after the location was chosen. `remove()` is recent
 * Chromium; where it is missing the worst case is a 0-byte file the person can
 * delete, which is better than blocking on it.
 */
export async function discardSaveLocation(target) {
  try {
    await target?.handle?.remove?.()
  } catch {
    /* nothing to do — an empty leftover file is not worth surfacing */
  }
}
