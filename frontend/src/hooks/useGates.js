import { useEffect, useState } from 'react'
import { getGates } from '../api/scanning'

// Gates are rows in scanning.Gate, not a fixed pair — admins add them from
// System Settings as the school expands. Every screen that names or lists a
// gate goes through this hook so a new gate shows up without a code change.
//
// The list is cached module-wide: gates change perhaps once a year, and each
// admin page used to fire its own request on mount.

// Shown until the fetch lands (or if it fails) so a screen is never blank.
const FALLBACK = [
  { gate_id: 'gate1', label: 'Gate 1 — Main Entrance' },
  { gate_id: 'gate4', label: 'Gate 4 — Side Entrance' },
]

// Module scope, so the cache outlives any single component and is shared by
// every screen. The three pieces work together: `cache` is the answer,
// `inflight` collapses simultaneous first-mounts into one request, and
// `subscribers` lets an invalidation reach components already rendered.
let cache = null          // gates once fetched
let inflight = null       // shared promise, so N mounts make 1 request
const subscribers = new Set()

function load() {
  if (cache) return Promise.resolve(cache)
  if (!inflight) {
    inflight = getGates()
      .then(({ data }) => {
        cache = Array.isArray(data) && data.length > 0 ? data : FALLBACK
        subscribers.forEach(fn => fn(cache))
        return cache
      })
      .catch(() => FALLBACK)                  // a failed fetch still yields a usable list; cache stays null so the next mount retries
      .finally(() => { inflight = null })      // released either way, so one failure does not permanently block reloading
  }
  return inflight
}

/** Drop the cache so the next mount refetches — call after adding or
 *  deactivating a gate in System Settings. */
export function invalidateGates() {
  cache = null
  subscribers.forEach(fn => fn(null))
}

/** Short display name, e.g. 'Gate 2' from 'Gate 2 — North Entrance'. */
export function shortGateLabel(label) {
  return label.split('—')[0].trim()
}

export function useGates() {
  const [gates, setGates] = useState(cache ?? FALLBACK)

  useEffect(() => {
    let alive = true
    // Two signals on one channel: a list means "here are the new gates", and
    // null means "the cache was invalidated, go and fetch again".
    const onChange = (next) => {
      if (!alive) return                        // the guard that stops a setState after unmount
      if (next) setGates(next)
      else load().then(g => { if (alive) setGates(g) })
    }
    subscribers.add(onChange)
    load().then(g => { if (alive) setGates(g) })
    return () => { alive = false; subscribers.delete(onChange) }
  }, [])

  return {
    gates,
    /** Slugs only, in display order. */
    gateIds: gates.map(g => g.gate_id),
    /** 'gate2' → 'Gate 2'. Unknown slugs return themselves rather than a
     *  generic 'Gate', so a log line never loses which gate it came from. */
    gateLabel: (id) => {
      if (!id) return ''
      const g = gates.find(x => x.gate_id === id)
      return g ? shortGateLabel(g.label) : id   // the slug itself for an unknown gate — see the docstring on why
    },
    /** Full label including the entrance description. */
    gateFullLabel: (id) => {
      if (!id) return ''
      return gates.find(x => x.gate_id === id)?.label ?? id
    },
  }
}
