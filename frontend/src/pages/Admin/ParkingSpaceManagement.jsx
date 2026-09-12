import { useState } from 'react'
import { CalendarDays, SquareParking } from 'lucide-react'

import ParkingManagement from './ParkingManagement'
import Events from './Events'
import ConfiscatedAccounts from '../../components/ConfiscatedAccounts'
import PageTabs from '../../components/Tabs/PageTabs'
import './ParkingSpaceManagement.css'

/* Parking Space Management — the former Parking and Events pages.

   They used to be stacked into one continuous scroll: the bay layout and its
   camera controls, then the accounts barred from parking, then event mode and
   the event list. Two unrelated jobs, one of which involves drawing boxes on a
   live camera frame, and reaching the second meant scrolling past all of the
   first.

   Tabbed instead, the same way System Settings splits its four groups — and
   split where the work actually divides: laying out and watching the bays is
   one job, running an event is another, and nobody does both in the same
   sitting. Confiscated accounts sit with the bays because that is the list you
   check against the cars in them. */

const TABS = [
  { id: 'spaces', label: 'Parking Spaces', icon: SquareParking },
  { id: 'events', label: 'Events',         icon: CalendarDays },
]

const BLURB = {
  spaces: 'Draw and edit bay layouts, watch live occupancy, and see which accounts are '
        + 'barred from parking.',
  events: 'Activate event mode, override parking capacity, and track organizer vehicles.',
}

export default function ParkingSpaceManagement() {
  const [tab, setTab] = useState('spaces')

  return (
    <div className="psm-page">
      <div className="psm-header">
        <h1 className="psm-title">Parking Space Management</h1>
        {/* One line that changes with the tab, rather than a paragraph trying
            to describe both halves at once. */}
        <p className="psm-subtitle">{BLURB[tab]}</p>
      </div>

      <PageTabs
        id="psm"
        ariaLabel="Parking sections"
        tabs={TABS}
        active={tab}
        onChange={setTab}
      />

      {/* Both panels stay mounted, hidden with `hidden` rather than unmounted.
          The parking tab holds a live camera stream and an editable layout with
          unsaved boxes in it; unmounting on a tab switch would drop the stream
          and the edits, so switching to Events and back would cost the admin
          their work and a reconnect. */}
      <div className="psm-panel" hidden={tab !== 'spaces'}>
        <ParkingManagement embedded />
        {/* Confiscated owners may not park. Whoever is watching the bays needs
            to know which plates should not be in them. */}
        <div className="psm-confiscated">
          <ConfiscatedAccounts />
        </div>
      </div>

      <div className="psm-panel" hidden={tab !== 'events'}>
        <Events embedded />
      </div>
    </div>
  )
}
