// The status a gate-log row is SHOWN with. An entry that came in under a CDSO
// booking (Expected Today) is stored as an ordinary authorized entry that
// names the booking; every screen that lists the log shows it as a Scheduled
// Entry, the same as the result card the guard saw at the barrier.
export function displayStatus(log) {
  return log?.status === 'authorized' && log?.scheduled_visit_ref ? 'scheduled_entry' : log?.status
}
