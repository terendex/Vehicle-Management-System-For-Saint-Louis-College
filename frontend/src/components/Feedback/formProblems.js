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
// Six strategies, tried in order, and the ORDER is the whole design: each is
// less reliable than the one before, so the first that answers wins. The last
// two are guesses from an attribute rather than a real label, and the final
// fallback still returns a sentence-shaped string rather than nothing.
function labelFor(el) {
  const form = el.form
  // 1. An explicit <label for="…">. The only one the browser itself would
  //    consider authoritative.
  if (el.id && form) {
    // CSS.escape because an id may contain characters that are meaningful in
    // a selector — without it an id like `plate.number` silently matches
    // nothing and the search falls through to a weaker strategy.
    const explicit = form.querySelector(`label[for="${CSS.escape(el.id)}"]`)
    if (explicit?.textContent.trim()) return clean(explicit.textContent)
  }
  // 2. A <label> wrapping the control — equally valid HTML, no id needed.
  const wrapping = el.closest('label')
  if (wrapping?.textContent.trim()) return clean(wrapping.textContent)

  // The layout convention across these pages is a wrapper div holding the
  // label and then the control.
  const group = el.closest('.form-group, .od-form-group, .cpw-group, .ev-field, .sp-field, .rc-field, .um-form-group, .dm-field, .paypage-field')
  const nearby = group?.querySelector('label')
  if (nearby?.textContent.trim()) return clean(nearby.textContent)

  // 4-5. Attributes rather than labels. A placeholder is the weakest of the
  // real options — it is written as a hint ("e.g. ABC 1234"), not as a name.
  if (el.getAttribute('aria-label')) return clean(el.getAttribute('aria-label'))
  if (el.placeholder) return clean(el.placeholder)
  // 6. Nothing to read: derive something from the attribute name, and failing
  //    even that, say "This field" so the sentence still reads.
  return humanise(el.name || el.id || 'This field')
}

// Labels carry a "*" for required and sometimes a parenthetical note; neither
// belongs in a sentence.
function clean(text) {
  // Whitespace first, so the two trailing-character strips below see a
  // predictable string. Both a "*" and a ":" are removed, and separately,
  // because a label may end "Plate Number:*" — one pass would leave the other.
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
  // Duck-typed rather than `instanceof HTMLFormElement`: callers pass a ref's
  // `.current`, which may be null on an early render, and the check has to
  // survive that without throwing.
  if (!form || typeof form.querySelectorAll !== 'function') return []
  const seen = new Set()
  const out = []

  for (const el of form.querySelectorAll('input, select, textarea')) {
    // `willValidate` is already false for disabled, hidden and readonly
    // controls, which is exactly the set a person cannot act on.
    // Both conditions skip, and they mean different things: `willValidate`
    // excludes controls the person cannot act on, `checkValidity()` excludes
    // the ones that are simply fine.
    if (!el.willValidate || el.checkValidity()) continue

    const label = labelFor(el)
    // Radio groups fail once per button; the person sees one choice to make.
    const key = el.type === 'radio' ? `radio:${el.name}` : `${label}:${el.name}`
    if (seen.has(key)) continue
    seen.add(key)

    // A missing value gets this project's own wording; anything else borrows
    // the browser's message, which already explains pattern and range
    // failures better than a generic sentence would.
    out.push(el.validity.valueMissing
      ? `${label} is required.`
      : `${label}: ${el.validationMessage}`)
  }
  return out
}

export default fieldProblems
