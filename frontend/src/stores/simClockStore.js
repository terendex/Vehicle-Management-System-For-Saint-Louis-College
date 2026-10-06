import { create } from 'zustand'
import { setClockOffset } from '../utils/clock'

/* The instructor demo's simulated clock, as /api/deployment/ reports it.

   Only the local demo stack (dev.ps1 -SimClock) reports one; everywhere else
   `sim` stays null, the first check is the only one, and nothing renders. On
   the demo it is re-read every 20 seconds, so a change made from a terminal
   (manage.py sim_clock) reaches every open page, and at once after the Test
   Clock page changes it. */

export const useSimClock = create((set) => ({
  sim: null,          // null: no simulated clock on this server
  setSim: (sim) => {
    // The page's "now" follows the server's date, so "2 days ago", overdue
    // and today's filters agree with the timestamps it sends (utils/clock.js).
    setClockOffset(sim?.enabled ? (sim.offset_seconds || 0) * 1000 : 0)
    set({ sim: sim ?? null })
  },
}))

const POLL_MS = 20000
let timer = null

export function refreshSimClock() {
  return fetch('/api/deployment/')
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => {
      const sim = d?.sim_clock ?? null
      useSimClock.getState().setSim(sim)
      return sim
    })
    .catch(() => null)
}

export function startSimClockWatch() {
  if (timer) return
  refreshSimClock().then((sim) => {
    // A server without the simulated clock never grows one while running,
    // so production makes this request once per page load and stops.
    if (sim && !timer) timer = setInterval(refreshSimClock, POLL_MS)
  })
}
