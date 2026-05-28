import AdminLayout from '../../components/Layout/AdminLayout'

export default function VehicleRegistration() {
  return (
    <AdminLayout>
      <div className="bg-white p-6 rounded-xl border border-gray-200 shadow-sm">
        <h1 className="text-2xl font-bold mb-2 text-[#2A2B61]">Vehicle Registration</h1>
        <p className="text-[#5A5F72]">Manage all registered vehicles here.</p>
      </div>
    </AdminLayout>
  )
}
