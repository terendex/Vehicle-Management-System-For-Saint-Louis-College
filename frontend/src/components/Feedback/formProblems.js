/**
 * Turns a form's native constraint failures into plain sentences.
 *
 * The browser's own `required` handling is a feedback surface of its own: it
 * blocks submit before any handler runs and floats a grey bubble beside the
 * field, which is neither a modal nor something the page can restyle. So every
 * form that reports through a dialog carries `noValidate`, and calls this at
 * submit time to get the same information back as text.
 *
 * Reading it off the DOM rather than re-listing each field by hand means the
 * dialog cannot drift out of step with the form: mark an input `required` and
 * it is covered.
 */

/** Best available human name for a control, in decreasing order of trust. */
function labelFor(el) {
  const form = el.form
  if (el.id && form) {
    const explicit = form.querySelector(`label[for="${CSS.escape(el.id)}"]`)
    if (explicit?.textContent.trim()) return clean(explicit.textContent)
  }
  const wrapping = el.closest('label')
  if (wrapping?.textContent.trim()) return clean(wrapping.textContent)

  // The layout convention across these pages is a wrapper div holding the
  // label and then the control.
  const group = el.closest('.form-group, .od-form-group, .cpw-group, .ev-field, .sp-field, .rc-field, .um-form-group, .dm-field, .paypage-field')
  const nearby = group?.querySelector('label')
  if (nearby?.textContent.trim()) return clean(nearby.textContent)

  if (el.getAttribute('aria-label')) return clean(el.getAttribute('aria-label'))
  if (el.placeholder) return clean(el.placeholder)
  return humanise(el.name || el.id || 'This field')
}

// Labels carry a "*" for required and sometimes a parenthetical note; neither
// belongs in a sentence.
function clean(text) {
  return text.replace(/\s+/g, ' ').replace(/\s*\*\s*$/, '').replace(/[:*]\s*$/, '').trim()
}

function humanise(name) {
  return name.replace(/[_-]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()).trim()
}

/**
 * @param {HTMLFormElement} form
 * @returns {string[]} one sentence per failing control, in document order
 */
export function fieldProblems(form) {
  if (!form || typeof form.querySelectorAll !== 'function') return []
  const seen = new Set()
  const out = []

  for (const el of form.querySelectorAll('input, select, textarea')) {
    // `willValidate` is already false for disabled, hidden and readonly
    // controls, which is exactly the set a person cannot act on.
    if (!el.willValidate || el.checkValidity()) continue

    const label = labelFor(el)
    // Radio groups fail once per button; the person sees one choice to make.
    const key = el.type === 'radio' ? `radio:${el.name}` : `${label}:${el.name}`
    if (seen.has(key)) continue
    seen.add(key)

    out.push(el.validity.valueMissing
      ? `${label} is required.`
      : `${label}: ${el.validationMessage}`)
  }
  return out
}

export default fieldProblems
