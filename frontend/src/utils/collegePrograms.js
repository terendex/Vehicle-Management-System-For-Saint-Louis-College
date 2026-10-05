/* The college programs an applicant may choose, grouped by college.

   Bundled with the app rather than fetched, so the registration form's
   Program picker is there the instant the page loads, even on a slow
   connection. The server holds the same list (backend/vehicles/
   college_programs.py) to refuse anything else, and a backend test fails if the
   two ever differ — change both together.

   Stored on a registration as one string, "<code> - <year>", e.g. "BSIT - 3" or
   "BS Arch - 5": the same shape program_year has always had. */
import COLLEGES from '../data/collegePrograms.json'

export { COLLEGES }

const BY_CODE = new Map(COLLEGES.flatMap(c => c.programs.map(p => [p.code, { ...p, college: c.college }])))

export const findProgram = (code) => BY_CODE.get(code) || null

/* The year levels a program offers: 1–4, 1–5 for BS Arch, 1–2 for JD and the masteral programs. */
export const yearsFor = (code) => {
  const p = findProgram(code)
  return p ? Array.from({ length: p.years }, (_, i) => String(i + 1)) : []
}

export const composeProgramYear = (code, year) => `${code} - ${year}`

/* "BSIT - 3" -> { code: 'BSIT', year: '3' }, or null when the text is not a
   program/year from the list (an older registration's free-typed value). */
export function parseProgramYear(text) {
  const m = /^(.*\S)\s*-\s*(\d+)$/.exec((text || '').trim())
  if (!m) return null
  const program = findProgram(m[1])
  if (!program || Number(m[2]) < 1 || Number(m[2]) > program.years) return null
  return { code: program.code, year: m[2] }
}

export const isValidProgramYear = (text) => parseProgramYear(text) !== null

/* Loose split for a form still being filled in: "BSIT - " (program picked,
   year not yet) -> { code: 'BSIT', year: '' }. A code not on the list comes
   back as '' so the picker shows nothing chosen. */
export function splitProgramYear(text) {
  const m = /^(.*?)\s*-\s*(\d*)\s*$/.exec(text || '')
  const code = m && findProgram(m[1]) ? m[1] : ''
  const year = code && yearsFor(code).includes(m[2]) ? m[2] : ''
  return { code, year }
}
