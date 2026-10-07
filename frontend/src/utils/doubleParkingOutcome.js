import notify from '../components/Feedback/notify'

// Asked before either path issues anything — the parking screen's Issue
// Violation form and a camera alert's plate box. Issuing costs the owner
// campus access (a first offense is a week), so it is not a single unconfirmed
// click; the overstay Acknowledge asks the same way. Resolves true to go on.
export function confirmDoubleParking(plate) {
  return notify.confirm({
    title: 'Issue a double-parking violation?',
    message: `Issue a Double Parking violation to ${plate.replace(/\s+/g, '')}?`,
    description: 'This counts as an offense against the owner: 1st — a week without campus '
               + 'access, 2nd — two weeks, 3rd — the rest of the registration period. '
               + 'Only one offense is counted per owner per day.',
    confirmLabel: 'Issue violation',
    danger: true,
  })
}

// What a double-parking report actually did, told the guard in one dialog.
// Shared by the alert card (a camera caught it) and the parking screen's
// Issue Violation (the guard saw it): both go through the server's one
// violation path, which issues at most one strike per owner per day — so a
// second report the same day records nothing new, and the guard must be told
// that rather than "violation issued".
export function announceDoubleParking(plate, data) {
  if (data?.already_recorded) {
    return notify.info(
      `${plate} already has a violation recorded today. One offense per owner per day, `
      + 'so no new violation was issued.',
      { title: 'Already recorded today' },
    )
  }
  const v = data?.violation || {}
  const offense = v.offense_number ? `Offense ${v.offense_number} of 3. ` : ''
  const until = v.confiscated_until
    ? `Campus access confiscated until ${new Date(v.confiscated_until).toLocaleDateString()}.`
    : v.confiscated ? 'Campus access confiscated until the CDSO lifts it.' : ''
  return notify.success(`${offense}${until}`.trim() || `Double parking recorded for ${plate}.`,
    { title: `Double parking violation issued — ${plate}` })
}
