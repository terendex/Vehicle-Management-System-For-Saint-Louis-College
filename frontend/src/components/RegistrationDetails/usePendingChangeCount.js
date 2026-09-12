import { useCallback, useEffect, useState } from 'react'

import { registrationApi } from '../../api/registration'
import { useLiveUpdates } from '../../realtime/useLiveUpdates'

/** How many detail-change requests are waiting — for a count badge on a tab.
 *
 *  In its own module rather than beside the queue component: a file exporting
 *  both a component and a hook breaks Fast Refresh. Kept out of the host page
 *  too, so a screen that wants the badge does not have to learn the endpoint.
 */
export function usePendingChangeCount() {
  const [count, setCount] = useState(0)

  const load = useCallback(async () => {
    try {
      const rows = await registrationApi.getChangeRequests('pending')
      setCount(Array.isArray(rows) ? rows.length : 0)
    } catch {
      // A badge is a read-out, not a gate: a failed fetch leaves it at zero
      // rather than blocking the page it sits on.
    }
  }, [])

  useEffect(() => { load() }, [load])
  useLiveUpdates(load, ['registrationchangerequest'])
  return count
}

export default usePendingChangeCount
