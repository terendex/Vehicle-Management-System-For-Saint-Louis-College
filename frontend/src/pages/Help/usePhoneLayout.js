import { useSyncExternalStore } from 'react'

// The width at which the app's pages collapse to their one-column phone
// layout. The help pictures switch at the same point, so a reader always sees
// the version of a screen that matches what their own device shows.
const QUERY = '(max-width: 760px)'

const subscribe = (onChange) => {
  const mq = window.matchMedia(QUERY)
  mq.addEventListener('change', onChange)
  return () => mq.removeEventListener('change', onChange)
}

export default function usePhoneLayout() {
  return useSyncExternalStore(subscribe, () => window.matchMedia(QUERY).matches, () => false)
}
