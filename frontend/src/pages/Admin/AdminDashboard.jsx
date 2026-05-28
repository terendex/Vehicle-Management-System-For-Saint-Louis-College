import AdminLayout from '../../components/Layout/AdminLayout'

export default function AdminDashboard() {
  return (
    <AdminLayout>
      <div className="bg-white p-6 rounded-xl border border-gray-200 shadow-sm">
        <h1 className="text-2xl font-bold mb-2 text-[#2A2B61]">Dashboard Overview</h1>
        <p className="text-[#5A5F72]">Welcome to the Admin Portal. Select an option from the sidebar to begin.</p>
      </div>
    </AdminLayout>
  )
}
