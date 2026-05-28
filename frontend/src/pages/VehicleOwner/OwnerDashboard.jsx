import OwnerLayout from '../../components/Layout/OwnerLayout'

export default function OwnerDashboard() {
  return (
    <OwnerLayout>
      <div className="bg-white p-6 rounded-xl border border-gray-200 shadow-sm">
        <h1 className="text-2xl font-bold mb-2 text-[#2A2B61]">Vehicle Owner Dashboard</h1>
        <p className="text-[#5A5F72]">Welcome! View your registered vehicles and entry schedules here.</p>
      </div>
    </OwnerLayout>
  )
}
