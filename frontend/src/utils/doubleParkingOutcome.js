import notify from '../components/Feedback/notify'

// What a double-parking report actually did, told the guard in one dialog.
// Shared by the alert card (a camera caught it) and the parking screen's
// Issue Violation (the guard saw it): both go through the server's one
// violation path, which issues at most one strike per owner per day — so a
// second report the same day records nothing new, and the guard must be told
// that rather than "violation issued".
export function announceDoubleParking(plate, data) {
  if (data?.already_recorded) {
    return notify.info(
      `${plate} already has a violation recorded today. One offence per owner per day, `
      + 'so no new violation was issued.',
      { title: 'Already recorded today' },
    )
  }
  const v = data?.violation || {}
  const offence = v.offense_number ? `Offence ${v.offense_number} of 3. ` : ''
  const until = v.confiscated_until
    ? `Campus access confiscated until ${new Date(v.confiscated_until).toLocaleDateString()}.`
    : v.confiscated ? 'Campus access confiscated until the CDSO lifts it.' : ''
  return notify.success(`${offence}${until}`.trim() || `Double parking recorded for ${plate}.`,
    { title: `Double parking violation issued — ${plate}` })
}
