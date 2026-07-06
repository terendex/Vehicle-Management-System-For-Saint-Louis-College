import AdminLayout from '../../components/Layout/AdminLayout'
import ParkingManagement from './ParkingManagement'
import Events from './Events'
import './ParkingSpaceManagement.css'

// Parking Space Management — the former Parking and Events pages combined
// into one continuous page: parking spaces/zones first, then event-mode
// controls and the event list below.
export default function ParkingSpaceManagement() {
  return (
    <AdminLayout>
      <ParkingManagement embedded />
      <div className="psm-section-divider" />
      <Events embedded />
    </AdminLayout>
  )
}
