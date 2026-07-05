import { useContext, useEffect, useRef } from 'react'
import { LiveUpdatesContext } from './LiveUpdatesProvider'

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
  handlerRef.current = handler

  const debounce = options.debounce ?? 250
  // Stable key so the effect doesn't re-subscribe on every render.
  const filterKey = resources == null
    ? '*'
    : (Array.isArray(resources) ? resources : [resources]).join(',')

  useEffect(() => {
    const filter = filterKey === '*' ? null : filterKey.split(',')
    let timer = null

    const unsubscribe = subscribe((msg) => {
      if (filter && !filter.includes(msg.resource)) return
      if (timer) clearTimeout(timer)
      timer = setTimeout(() => handlerRef.current?.(msg), debounce)
    })

    return () => {
      if (timer) clearTimeout(timer)
      unsubscribe()
    }
  }, [subscribe, filterKey, debounce])
}

export default useLiveUpdates
