import { useEffect, useRef } from 'react'

/**
 * The 6-digit code entry, shared by the login challenge and the step-up dialog.
 *
 * Two behaviours worth keeping: `autoComplete="one-time-code"` lets phones
 * offer the code straight from the notification, and non-digits are stripped on
 * the way in so a pasted "492 107" is accepted rather than silently rejected as
 * the wrong length.
 */
export default function CodeField({
  value,
  onChange,
  onComplete,
  invalid = false,
  disabled = false,
  autoFocus = true,
  id = 'tfa-code',
  label = 'Verification code',
}) {
  const ref = useRef(null)
  const firedFor = useRef('')

  useEffect(() => {
    if (autoFocus) ref.current?.focus()
  }, [autoFocus])

  // Submit as soon as the sixth digit lands — a TOTP code has a fixed length,
  // so making the user reach for a button after typing it is pure friction.
  // Guarded so backspacing to five and retyping the same code can fire again,
  // but a re-render with an unchanged value cannot.
  useEffect(() => {
    if (value.length === 6 && firedFor.current !== value) {
      firedFor.current = value
      onComplete?.(value)
    } else if (value.length < 6) {
      firedFor.current = ''
    }
  }, [value, onComplete])

  return (
    <>
      <label className="tfa-field-label" htmlFor={id}>{label}</label>
      <input
        ref={ref}
        id={id}
        className={`tfa-code-input${invalid ? ' tfa-invalid' : ''}`}
        type="text"
        inputMode="numeric"
        autoComplete="one-time-code"
        placeholder="000000"
        maxLength={6}
        disabled={disabled}
        value={value}
        onChange={(e) => onChange(e.target.value.replace(/\D/g, '').slice(0, 6))}
        aria-invalid={invalid}
      />
    </>
  )
}
