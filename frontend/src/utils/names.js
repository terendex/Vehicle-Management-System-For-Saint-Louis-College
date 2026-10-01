/* A person's name in three parts — last name, first name, middle initial —
   the way accounts and registrations store it (backend/accounts/names.py).

   The API still sends a ready-made `full_name` for display, so screens that
   only show a name read that. These helpers are for the forms that WRITE one. */

export const NAME_KEYS = ['last_name', 'first_name', 'middle_initial']

export const EMPTY_NAME = { last_name: '', first_name: '', middle_initial: '' }

/* "santos" / "S." -> "S"; '' when there is no letter to take. */
export function cleanInitial(value) {
  const m = /\p{L}/u.exec(String(value || ''))
  return m ? m[0].toUpperCase() : ''
}

const squash = (s) => String(s || '').trim().replace(/\s+/g, ' ')

/* The display form the server computes: "DELA CRUZ, JUAN S." */
export function composeFullName({ last_name, first_name, middle_initial } = {}) {
  const name = [squash(last_name), squash(first_name)].filter(Boolean).join(', ')
  const initial = cleanInitial(middle_initial)
  return name && initial ? `${name} ${initial}.` : name
}

/* The three parts of a user/registration object, for prefilling a form. */
export const namePartsOf = (obj = {}) => ({
  last_name: obj.last_name || '',
  first_name: obj.first_name || '',
  middle_initial: obj.middle_initial || '',
})

/* The parts ready to post: trimmed, single-spaced, initial reduced to a letter. */
export const namePayload = (form) => ({
  last_name: squash(form.last_name),
  first_name: squash(form.first_name),
  middle_initial: cleanInitial(form.middle_initial),
})

/* Field-keyed problems, for the forms' validation pass. */
export function nameProblems(form) {
  const errors = {}
  if (!squash(form.last_name)) errors.last_name = 'Last name is required.'
  if (!squash(form.first_name)) errors.first_name = 'First name is required.'
  return errors
}
