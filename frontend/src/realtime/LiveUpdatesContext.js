import { createContext } from 'react'

// Shared context for the app-wide live-updates WebSocket. Kept in its own file
// (no component export) so React Fast Refresh works for the provider/hook.
export const LiveUpdatesContext = createContext({ subscribe: () => () => {} })
