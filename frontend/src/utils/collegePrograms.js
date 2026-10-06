/* The college programs an applicant may choose, grouped by college.

   Bundled with the app rather than fetched, so the registration form's
   Program picker is there the instant the page loads, even on a slow
   connection. The server holds the same list (backend/vehicles/
   college_programs.py) to refuse anything else, and a backend test fails if the
   two ever differ — change both together.

   Stored on a registration as one string, "<code> - <year>", e.g. "BSIT - 3" or
   "BS Arch - 5": the same shape program_year has always had. A School of
   Advanced Studies student past coursework is on Residency instead of a year:
   "MBA - Residency". */
import COLLEGES from '../data/collegePrograms.json'

export { COLLEGES }

const BY_CODE = new Map(COLLEGES.flatMap(c => c.programs.map(p => [p.code, { ...p, college: c.college }])))

export const findProgram = (code) => BY_CODE.get(code) || null

export const RESIDENCY = 'Residency'

/* The year levels a program offers: 1–4, 1–5 for BS Arch, 1–2 for JD and the
   School of Advanced Studies, whose programs then offer Residency as well. */
export const yearsFor = (code) => {
  const p = findProgram(code)
  if (!p) return []
  const years = Array.from({ length: p.years }, (_, i) => String(i + 1))
  return p.residency ? [...years, RESIDENCY] : years
}

/* How a level reads in the picker: "Year 2", or "Residency". */
export const yearLabel = (level) => (level === RESIDENCY ? RESIDENCY : `Year ${level}`)

export const composeProgramYear = (code, year) => `${code} - ${year}`

/* "BSIT - 3" -> { code: 'BSIT', year: '3' }, or null when the text is not a
   program/year from the list (an older registration's free-typed value). */
export function parseProgramYear(text) {
  const m = /^(.*\S)\s*-\s*(\d+|Residency)$/.exec((text || '').trim())
  if (!m) return null
  const program = findProgram(m[1])
  if (!program || !yearsFor(program.code).includes(m[2])) return null
  return { code: program.code, year: m[2] }
}

export const isValidProgramYear = (text) => parseProgramYear(text) !== null

/* Loose split for a form still being filled in: "BSIT - " (program picked,
   year not yet) -> { code: 'BSIT', year: '' }. A code not on the list comes
   back as '' so the picker shows nothing chosen. */
export function splitProgramYear(text) {
  const m = /^(.*?)\s*-\s*(\d*|Residency)\s*$/.exec(text || '')
  const code = m && findProgram(m[1]) ? m[1] : ''
  const year = code && yearsFor(code).includes(m[2]) ? m[2] : ''
  return { code, year }
}
