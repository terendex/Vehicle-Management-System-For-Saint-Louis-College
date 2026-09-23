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

// =============================================================================
// Read this file as a QUEUE with two suppression rules, not as a toast library.
//
// The API looks like sonner's on purpose (see the docstring above), but the
// behaviour is the opposite: nothing is transient, and everything is awaited.
// Every call returns a promise that settles when the user acknowledges.
//
// Two separate mechanisms stop a machine burying the screen, and they are
// easy to confuse:
//
//   COLLAPSE (keyOf + the `existing` check) — identical dialogs that are
//     pending RIGHT NOW share one entry and one promise. Always on.
//   MUTE (_mutedUntil + throttleMs) — after a dialog is dismissed, suppress
//     an identical one for a while. Opt-in, and only for machine-raised
//     messages; a person pressing Submit twice must be answered twice.
//
// Everything below serves one of those two, or turns whatever a caller threw
// at it into displayable text (asText).
// =============================================================================

let _seq = 0                                    // monotonic dialog id; only used as a React key

// Alerts sit in a queue rather than replacing one another. Several failures
// can land at once — a websocket dropping takes its retries with it — and the
// last one to arrive is not automatically the one worth reading.
export const useFeedbackStore = create(() => ({ queue: [] }))

/** Identical pending dialogs collapse into one. A camera that error-loops on
 *  a dead RTSP socket must not be able to stack fifty modals to click through. */
function keyOf(d) {
  // Every displayed field participates, so two dialogs collapse only when they
  // would look identical. The separators are ASCII unit (\u001f) and record
  // (\u001e) characters precisely because they cannot occur in a message — a
  // plain '|' would let two different dialogs collide into one key.
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
  // COLLAPSE. Returning the EXISTING promise is what makes fifty callers share
  // one dialog and all settle together when it is dismissed once.
  const existing = useFeedbackStore.getState().queue.find((d) => d.key === key)
  if (existing) return existing.promise

  // MUTE. Note it resolves `undefined` rather than rejecting or hanging: a
  // caller awaiting a suppressed alert continues immediately, and a suppressed
  // confirm reads as "not confirmed" without ever appearing.
  const muted = _mutedUntil.get(key)
  if (muted !== undefined) {
    if (Date.now() < muted) return Promise.resolve(undefined)
    _mutedUntil.delete(key)                     // window passed — drop the entry so the map does not accumulate
  }

  // The same park-the-resolver shape as twofaStore: the promise is created
  // here and settled later by closeTop, which is how a React dialog drives an
  // `await` in code that knows nothing about React.
  let resolve
  const promise = new Promise((r) => { resolve = r })
  const entry = { ...dialog, key, id: ++_seq, resolve, promise }   // `promise` is stored too, so a collapse can hand back the same one
  useFeedbackStore.setState((s) => ({ queue: [...s.queue, entry] }))
  return promise
}

/** Dismiss the dialog on screen and hand `result` back to whoever awaited it. */
export function closeTop(result) {
  const { queue } = useFeedbackStore.getState()
  const top = queue[0]
  if (!top) return
  // slice(1), so the queue is strictly first-in-first-out — the oldest
  // unacknowledged message is the one on screen, not the newest.
  useFeedbackStore.setState({ queue: queue.slice(1) })
  if (top.throttleMs > 0) {
    _mutedUntil.set(top.key, Date.now() + top.throttleMs)
    // Keep the map from growing without bound over a long shift at a gate.
    // Swept lazily, only once the map is large, and only of entries that have
    // already expired. A gate terminal runs for a whole shift, and an
    // error-looping camera would otherwise add a key per distinct message.
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
// Callers forward whatever they caught, so this has to handle five shapes.
// Ordered cheapest-first, and RECURSIVE for the two container cases.
function asText(message) {
  if (message == null) return ''                // null/undefined -> empty, never the string "null"
  if (typeof message === 'string') return message
  if (message instanceof Error) return message.message
  // DRF reports a field as a list of strings.
  if (Array.isArray(message)) return message.map(asText).filter(Boolean).join(' ')
  if (typeof message === 'object') {
    // The three keys this project's APIs actually use, in the order DRF and
    // the custom views prefer them. `??` not `||`, so an empty string still
    // counts as a present value rather than falling through.
    const first = message.detail ?? message.error ?? message.message
    if (first != null) return asText(first)     // recursive: the value may itself be a list
    // Last resort. try/catch because a circular object throws here, and a
    // feedback helper must never be the thing that crashes the page.
    try { return JSON.stringify(message) } catch { return String(message) }
  }
  return String(message)
}

// A factory rather than four near-identical functions, so the four tones
// cannot drift apart in their defaults.
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
  // Accepts a bare string as well as an options object, so the common case
  // reads as `notify.confirm('Delete this camera?')`.
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
  // Resolves FALSE and shows nothing when the form is clean — which is what
  // makes `if (await notify.validation(errs)) return` a correct submit guard
  // rather than something that has to be wrapped in its own check.
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
// Deliberate alias, not a leftover: a stray `toast.error(...)` anywhere in the
// codebase lands in this modal system rather than silently doing nothing.
export const toast = notify

export default notify
