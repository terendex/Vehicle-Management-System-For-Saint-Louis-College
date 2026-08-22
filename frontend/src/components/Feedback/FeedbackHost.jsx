import { useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { CheckCircle2, XCircle, AlertTriangle, Info, HelpCircle } from 'lucide-react'
import { useFeedbackStore, closeTop } from './notify'
import './feedback.css'

const ICON = {
  success: CheckCircle2,
  error:   XCircle,
  warning: AlertTriangle,
  info:    Info,
  confirm: HelpCircle,
}

// When one dialog is replaced by the next in the queue, the new one ignores
// input for this long. Otherwise a double-click on OK, or a held Enter, walks
// straight through the second message without it ever being read.
const SETTLE_MS = 350

/**
 * Renders the one dialog at the head of the queue. Mounted once, at the root,
 * so any module — pages, hooks, websocket handlers — can raise a modal without
 * knowing where it will appear.
 *
 * It portals to <body> rather than rendering in place: several of these fire
 * from inside form modals, and a dialog nested in that DOM would inherit the
 * parent's stacking context and open *behind* it.
 */
export default function FeedbackHost() {
  const dialog = useFeedbackStore((s) => s.queue[0])
  const queued = useFeedbackStore((s) => s.queue.length)

  const confirmRef = useRef(null)
  const cancelRef  = useRef(null)
  // What had focus before the dialog opened, so it can be handed back.
  const returnRef  = useRef(null)
  // The moment this dialog starts accepting input, and whether one was already
  // on screen when it arrived.
  const settledAt = useRef(0)
  const wasOpen   = useRef(false)

  const isConfirm = dialog?.cancelLabel != null
  // A confirm that destroys something defaults to Cancel: the safe answer is
  // the one that should be one reflex keypress away, not the irreversible one.
  const defaultsToCancel = isConfirm && dialog.danger

  const isOpen = !!dialog
  const dialogId = dialog?.id ?? null

  const dismiss = (result) => {
    if (Date.now() < settledAt.current) return
    closeTop(result)
  }

  // Keyboard. Registered on the capture phase and stopped immediately, because
  // the surfaces this dialog opens on top of listen for these keys too — the
  // step-up gate cancels the held request on Escape, form modals close on it.
  // Without this, one Escape would dismiss the message *and* tear down the
  // screen that raised it. Capture runs before those window listeners
  // regardless of which mounted first, so this is the only handler that sees
  // the key.
  useEffect(() => {
    if (!isOpen) return
    const onKey = (e) => {
      if (e.key === 'Tab') {
        // Hold focus inside the dialog. Without this, Tab walks into the page
        // underneath — which is inert to the eye but not to the keyboard.
        const stops = [cancelRef.current, confirmRef.current].filter(Boolean)
        if (!stops.length) return
        e.preventDefault()
        e.stopImmediatePropagation()
        const at = stops.indexOf(document.activeElement)
        const next = e.shiftKey
          ? stops[(at <= 0 ? stops.length : at) - 1]
          : stops[(at + 1) % stops.length]
        next.focus()
        return
      }
      if (e.key !== 'Escape' && e.key !== 'Enter') return
      e.preventDefault()
      e.stopImmediatePropagation()
      if (e.key === 'Escape') { dismiss(false); return }
      // Enter activates whatever is focused, so it agrees with what the eye
      // sees — on a destructive confirm that is Cancel, not the red button.
      const active = document.activeElement
      if (active === confirmRef.current) dismiss(true)
      else if (active === cancelRef.current) dismiss(false)
      // Focus ended up somewhere unexpected: take the safe answer.
      else dismiss(!defaultsToCancel)
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [isOpen, defaultsToCancel])

  // Remember where focus came from, and put it back on the way out, so the
  // keyboard is not dumped at the top of the document.
  //
  // Declared BEFORE the effect that moves focus into the dialog: effects run in
  // declaration order, and the other way round this captures the dialog's own
  // button, which is gone by the time the cleanup wants to restore it.
  useEffect(() => {
    if (!isOpen) return
    returnRef.current = document.activeElement
    return () => {
      const el = returnRef.current
      if (el && el.isConnected && typeof el.focus === 'function') el.focus()
    }
  }, [isOpen])

  // Focus the button the person is most likely to want, as each dialog comes
  // up — acknowledging is then Enter or Space, without reaching for the mouse.
  // The gate lanes are worked one-handed. Keyed on id so a queued dialog takes
  // focus when its turn comes.
  useEffect(() => {
    if (dialogId === null) { wasOpen.current = false; return }
    // Promoted while another dialog was already up, so hold off on input for a
    // moment — a double-click on the last OK must not carry through to this one.
    settledAt.current = wasOpen.current ? Date.now() + SETTLE_MS : 0
    wasOpen.current = true
    const target = defaultsToCancel ? cancelRef.current : confirmRef.current
    target?.focus()
  }, [dialogId, defaultsToCancel])

  // Hold the page still underneath. Without this the list behind a dialog
  // scrolls when the wheel is nudged, and the row the message refers to moves.
  useEffect(() => {
    if (!isOpen) return
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = prev }
  }, [isOpen])

  if (!dialog) return null

  const Icon = ICON[dialog.tone] || Info
  const hasDetails = dialog.details.length > 0
  const describedBy = [
    dialog.message ? `fb-msg-${dialog.id}` : null,
    hasDetails ? `fb-details-${dialog.id}` : null,
  ].filter(Boolean).join(' ')

  return createPortal(
    <div
      className="fb-overlay"
      // Only a confirm can be dismissed by the backdrop. An error must be
      // acknowledged deliberately — a stray click outside it is not "I read that".
      onMouseDown={(e) => {
        if (isConfirm && e.target === e.currentTarget) dismiss(false)
      }}
    >
      <div
        className={`fb-dialog fb-${dialog.tone}`}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={`fb-title-${dialog.id}`}
        aria-describedby={describedBy || undefined}
      >
        <div className="fb-icon"><Icon size={30} strokeWidth={2} /></div>

        <h2 className="fb-title" id={`fb-title-${dialog.id}`}>{dialog.title}</h2>

        {dialog.message && (
          <p className="fb-message" id={`fb-msg-${dialog.id}`}>{dialog.message}</p>
        )}

        {dialog.description && <p className="fb-description">{dialog.description}</p>}

        {hasDetails && (
          <ul className="fb-details" id={`fb-details-${dialog.id}`}>
            {dialog.details.map((d, i) => <li key={i}>{d}</li>)}
          </ul>
        )}

        <div className="fb-actions">
          {isConfirm && (
            <button
              type="button"
              ref={cancelRef}
              className="fb-btn fb-btn-ghost"
              onClick={() => dismiss(false)}
            >
              {dialog.cancelLabel}
            </button>
          )}
          <button
            type="button"
            ref={confirmRef}
            className={`fb-btn ${dialog.danger ? 'fb-btn-danger' : 'fb-btn-primary'}`}
            onClick={() => dismiss(true)}
          >
            {dialog.confirmLabel}
          </button>
        </div>

        {/* Several things went wrong at once — say how many are still waiting,
            so the last one does not arrive as a surprise. */}
        {queued > 1 && (
          <p className="fb-queue" aria-live="polite">
            {queued - 1} more message{queued > 2 ? 's' : ''} after this
          </p>
        )}
      </div>
    </div>,
    document.body,
  )
}
