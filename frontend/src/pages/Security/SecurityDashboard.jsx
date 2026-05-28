import SecurityLayout from '../../components/Layout/SecurityLayout'

export default function SecurityDashboard() {
  return (
    <SecurityLayout>
      <div className="bg-white p-6 rounded-xl border border-gray-200 shadow-sm">
        <h1 className="text-2xl font-bold mb-2 text-[#2A2B61]">Security Dashboard</h1>
        <p className="text-[#5A5F72]">Welcome to the Security Portal. Select an option from the sidebar to begin.</p>
      </div>
    </SecurityLayout>
  )
}
