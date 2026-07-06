import { useState } from 'react'
import { ParkingCircle, CalendarDays } from 'lucide-react'
import AdminLayout from '../../components/Layout/AdminLayout'
import ParkingManagement from './ParkingManagement'
import Events from './Events'
import './ParkingSpaceManagement.css'

// Parking Space Management — combines the former Parking and Events pages
// into one place: physical parking spaces/zones plus event-mode controls.
const TABS = [
  { key: 'spaces', label: 'Parking Spaces', Icon: ParkingCircle },
  { key: 'events', label: 'Events',         Icon: CalendarDays },
]

export default function ParkingSpaceManagement() {
  const [tab, setTab] = useState('spaces')

  return (
    <AdminLayout>
      <div className="psm-tabs">
        {TABS.map(({ key, label, Icon }) => (
          <button
            key={key}
            className={`psm-tab${tab === key ? ' active' : ''}`}
            onClick={() => setTab(key)}
          >
            <Icon size={16} /> {label}
          </button>
        ))}
      </div>

      {tab === 'spaces' ? <ParkingManagement embedded /> : <Events embedded />}
    </AdminLayout>
  )
}
