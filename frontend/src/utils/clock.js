/* The page's clock, and the instructor demo's way of moving it.

   The demo (dev.ps1 -SimClock) moves the server's date. Every timestamp the
   server sends is on that date, but the browser keeps the real one, so
   anything the page works out from "now" (1 day ago, overdue, today's filter,
   a countdown) would be measured against the wrong day. Under the demo the
   page's Date is shifted by the same offset, so every one of those follows
   the simulated date without each having to know about it.

   Nothing changes anywhere else: the shift is only installed once a server
   reports a moved date (simClockStore), which only the local demo does.

   realNow() is the actual time, for the one thing the server still runs on
   real time: the login token's expiry (SimpleJWT reads the OS clock). */

const RealDate = Date
let offsetMs = 0
let installed = false

/** The actual time in ms, whatever the demo's clock says. */
export const realNow = () => RealDate.now()

/** Shift the page's clock by `ms` (0 puts it back on the real time). */
export function setClockOffset(ms) {
  offsetMs = Math.round(ms || 0)
  if (installed || !offsetMs) return
  installed = true
  // `new Date()` and Date.now() follow the offset; a Date built from a value
  // (a server timestamp, a picked date) is left exactly as given.
  class SimDate extends RealDate {
    constructor(...args) {
      if (args.length) super(...args)
      else super(RealDate.now() + offsetMs)
    }

    static now() {
      return RealDate.now() + offsetMs
    }
  }
  globalThis.Date = SimDate
}
