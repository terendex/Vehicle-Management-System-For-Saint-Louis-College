import { useContext, useEffect, useRef } from 'react'
import { LiveUpdatesContext } from './LiveUpdatesContext'

/**
 * Re-run `handler` (typically a data refetch) whenever the server signals that
 * data changed.
 *
 *   useLiveUpdates(fetchRegistrations)                      // any change
 *   useLiveUpdates(fetchRegistrations, 'vehicleregistration')
 *   useLiveUpdates(refetch, ['user', 'vehicle'])
 *
 * @param handler   called with the change message; usually your fetch function.
 * @param resources optional resource name or array to filter on. Omit for all.
 * @param options   { debounce } ms to coalesce bursts (default 250).
 */
export function useLiveUpdates(handler, resources, options = {}) {
  const { subscribe } = useContext(LiveUpdatesContext)
  const handlerRef = useRef(handler)
  // Keep the latest handler without re-subscribing (synced after each render)
  //
  // THE pattern to notice. Callers pass an inline arrow, so `handler` is a new
  // function every render; listing it as a dependency below would tear down
  // and rebuild the subscription on every render. The ref holds the latest
  // one instead. (The same rule the QR camera helper follows — putting a
  // callback in the deps there restarted the webcam and locked it.)
  useEffect(() => { handlerRef.current = handler })   // no dep array on purpose: sync after EVERY render

  const debounce = options.debounce ?? 250     // ?? not ||, so an explicit 0 means "no debounce" rather than falling back to 250
  // Stable key so the effect doesn't re-subscribe on every render.
  // An array literal would be a new reference each render; joining it to a
  // string gives the dep array something it can compare by value.
  const filterKey = resources == null
    ? '*'
    : (Array.isArray(resources) ? resources : [resources]).join(',')   // accepts one name or many, normalised to one string

  useEffect(() => {
    const filter = filterKey === '*' ? null : filterKey.split(',')
    let timer = null

    const unsubscribe = subscribe((msg) => {
      if (filter && !filter.includes(msg.resource)) return   // null filter = subscribe to everything
      // Coalesce a burst: a restore broadcasts one message per model, and
      // without this a page would refetch twenty times in a second.
      if (timer) clearTimeout(timer)
      timer = setTimeout(() => handlerRef.current?.(msg), debounce)   // read through the ref, so the LATEST handler runs
    })

    return () => {
      if (timer) clearTimeout(timer)
      unsubscribe()
    }
  }, [subscribe, filterKey, debounce])
}

export default useLiveUpdates
