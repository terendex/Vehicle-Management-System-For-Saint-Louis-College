import ParkingManagement from './ParkingManagement'
import Events from './Events'
import ConfiscatedAccounts from '../../components/ConfiscatedAccounts'
import './ParkingSpaceManagement.css'

// Parking Space Management — the former Parking and Events pages combined
// into one continuous page: parking spaces/zones first, then the accounts
// barred from parking, then event-mode controls and the event list below.
export default function ParkingSpaceManagement() {
  return (
    <>
      <ParkingManagement embedded />
      <div className="psm-section-divider" />
      {/* Confiscated owners may not park. Whoever is watching the bays needs to
          know which plates should not be in them. */}
      <div className="psm-confiscated">
        <ConfiscatedAccounts />
      </div>
      <div className="psm-section-divider" />
      <Events embedded />
    </>
  )
}
