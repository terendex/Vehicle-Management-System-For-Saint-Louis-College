import { useEffect } from 'react'
import { useSimClock, startSimClockWatch } from '../../stores/simClockStore'
import './SimClockBanner.css'

const PREFIX = '[SIM] '

/* The red bar on every page of the instructor demo while the date is moved,
   and "[SIM]" in front of the tab title. It cannot be dismissed: a screen
   showing a moved date must never be mistaken for the real system. With the
   clock off, or on at the real time (offset 0), there is nothing to warn
   about and it renders nothing, as everywhere the server has no simulated
   clock at all. */
export default function SimClockBanner() {
  const sim = useSimClock((s) => s.sim)
  const on = !!sim?.enabled && Math.round(sim.offset_seconds || 0) !== 0

  useEffect(() => { startSimClockWatch() }, [])

  useEffect(() => {
    document.documentElement.classList.toggle('sim-clock-on', on)
    if (!on) {
      if (document.title.startsWith(PREFIX)) document.title = document.title.slice(PREFIX.length)
      return undefined
    }
    // Pages set their own titles; put the prefix back whenever one does.
    const mark = () => {
      if (!document.title.startsWith(PREFIX)) document.title = PREFIX + document.title
    }
    mark()
    const titleEl = document.querySelector('title')
    const observer = titleEl ? new MutationObserver(mark) : null
    observer?.observe(titleEl, { childList: true, characterData: true, subtree: true })
    return () => observer?.disconnect()
  }, [on])

  if (!on) return null
  return (
    <div className="sim-clock-banner" role="status">
      <strong>SIMULATED DATE: {sim.now_display}</strong>
      <span className="sim-clock-banner-real">(real: {sim.real_now_display})</span>
    </div>
  )
}
