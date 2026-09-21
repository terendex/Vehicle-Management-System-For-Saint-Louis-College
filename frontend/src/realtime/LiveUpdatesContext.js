import { createContext } from 'react'

// Shared context for the app-wide live-updates WebSocket. Kept in its own file
// (no component export) so React Fast Refresh works for the provider/hook.
// The default is a no-op that returns a no-op: `subscribe()` hands back an
// unsubscribe function. A component rendered outside the provider (a test, a
// stray render) therefore subscribes to nothing and unsubscribes cleanly,
// instead of crashing on `subscribe is not a function`.
export const LiveUpdatesContext = createContext({ subscribe: () => () => {} })
