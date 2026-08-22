import { create } from 'zustand'

/**
 * Every confirmation, error and success in the system is a modal the user has
 * to acknowledge.
 *
 * Toasts were missable: they fade on their own, and a guard working a gate
 * lane who looks down at the plate for two seconds has no way to re-read a
 * notice that has already gone. So nothing is transient any more — a dialog
 * stays until it is dismissed by hand.
 *
 * The API deliberately mirrors sonner's `toast` (`.success`, `.error`,
 * `.warning`, `.info`, each taking `(message, options)`) so the call sites
 * that used to raise a toast read the same way, and so a stray `toast.` left
 * anywhere still lands in this system rather than silently doing nothing.
 *
 * The one thing that is NOT routed through here is the camera auto-detect
 * hint in Device Management: it reports on a field inside an already-open
 * modal, and a modal stacked on that modal would cover the very input it is
 * telling you to fix.
 */

let _seq = 0

// Alerts sit in a queue rather than replacing one another. Several failures
// can land at once — a websocket dropping takes its retries with it — and the
// last one to arrive is not automatically the one worth reading.
export const useFeedbackStore = create(() => ({ queue: [] }))

/** Identical pending dialogs collapse into one. A camera that error-loops on
 *  a dead RTSP socket must not be able to stack fifty modals to click through. */
function keyOf(d) {
  return [d.tone, d.title, d.message, d.description, (d.details || []).join('\u001f')].join('\u001e')
}

/**
 * When a dialog was dismissed and asked not to come straight back: key → the
 * moment it may be raised again.
 *
 * Only machine-generated messages set this. A person who presses Submit twice
 * without fixing anything must be told twice — swallowing the second one would
 * read as the button having broken.
 */
const _mutedUntil = new Map()

function enqueue(dialog) {
  const key = keyOf(dialog)
  const existing = useFeedbackStore.getState().queue.find((d) => d.key === key)
  if (existing) return existing.promise

  const muted = _mutedUntil.get(key)
  if (muted !== undefined) {
    if (Date.now() < muted) return Promise.resolve(undefined)
    _mutedUntil.delete(key)
  }

  let resolve
  const promise = new Promise((r) => { resolve = r })
  const entry = { ...dialog, key, id: ++_seq, resolve, promise }
  useFeedbackStore.setState((s) => ({ queue: [...s.queue, entry] }))
  return promise
}

/** Dismiss the dialog on screen and hand `result` back to whoever awaited it. */
export function closeTop(result) {
  const { queue } = useFeedbackStore.getState()
  const top = queue[0]
  if (!top) return
  useFeedbackStore.setState({ queue: queue.slice(1) })
  if (top.throttleMs > 0) {
    _mutedUntil.set(top.key, Date.now() + top.throttleMs)
    // Keep the map from growing without bound over a long shift at a gate.
    if (_mutedUntil.size > 64) {
      const now = Date.now()
      for (const [k, until] of _mutedUntil) if (until < now) _mutedUntil.delete(k)
    }
  }
  top.resolve(result)
}

const DEFAULT_TITLE = {
  success: 'Success',
  error:   'Error',
  warning: 'Warning',
  info:    'Notice',
}

// `message` may arrive as an Error or an axios payload when a catch block
// forwards it straight through, so coerce rather than rendering "[object Object]".
function asText(message) {
  if (message == null) return ''
  if (typeof message === 'string') return message
  if (message instanceof Error) return message.message
  // DRF reports a field as a list of strings.
  if (Array.isArray(message)) return message.map(asText).filter(Boolean).join(' ')
  if (typeof message === 'object') {
    const first = message.detail ?? message.error ?? message.message
    if (first != null) return asText(first)
    try { return JSON.stringify(message) } catch { return String(message) }
  }
  return String(message)
}

function makeAlert(tone) {
  // Not a method — NotificationBell maps severities onto these functions
  // (`{ critical: notify.error, … }`), so they must not depend on `this`.
  return (message, options = {}) => enqueue({
    tone,
    title:        options.title ?? DEFAULT_TITLE[tone],
    message:      asText(message),
    description:  options.description ? asText(options.description) : '',
    details:      options.details || [],
    confirmLabel: options.confirmLabel || 'OK',
    cancelLabel:  null,
    danger:       tone === 'error',
    // Opt-in, for messages a machine raises on a timer or a socket rather than
    // because someone pressed something. See `_mutedUntil`.
    throttleMs:   options.throttleMs || 0,
  })
}

const success = makeAlert('success')
const error   = makeAlert('error')
const warning = makeAlert('warning')
const info    = makeAlert('info')

/**
 * Ask before doing something. Resolves true when confirmed, false when the
 * user cancels, presses Escape, or clicks the backdrop.
 *
 *   if (!(await notify.confirm({ message: 'Delete this camera?', danger: true }))) return
 */
function confirm(options = {}) {
  const opts = typeof options === 'string' ? { message: options } : options
  return enqueue({
    tone:         'confirm',
    title:        opts.title || 'Please confirm',
    message:      asText(opts.message),
    description:  opts.description ? asText(opts.description) : '',
    details:      opts.details || [],
    confirmLabel: opts.confirmLabel || 'Confirm',
    cancelLabel:  opts.cancelLabel || 'Cancel',
    danger:       !!opts.danger,
  })
}

/**
 * Report form validation as one modal instead of hints under each input.
 * Accepts a list of messages, or the `{ field: message }` map the pages
 * already build. Resolves immediately (doing nothing) when there is nothing
 * wrong, so it can be used as the submit guard:
 *
 *   if (await notify.validation(errs)) return   // true = the form is invalid
 */
async function validation(errors, options = {}) {
  const list = (Array.isArray(errors) ? errors : Object.values(errors || {}))
    .map(asText)
    .filter(Boolean)
  if (!list.length) return false
  await enqueue({
    tone:         'error',
    title:        options.title || 'Check the form',
    // Deliberately not "N fields" — the list also carries rules and cross-field
    // checks, which are not fields and would make the count read as wrong.
    message:      options.message || 'Please correct the following:',
    description:  '',
    details:      list,
    confirmLabel: 'OK',
    cancelLabel:  null,
    danger:       true,
  })
  return true
}

export const notify = { success, error, warning, info, confirm, validation }

// Drop-in alias for the call sites that read `toast.success(…)`.
export const toast = notify

export default notify
