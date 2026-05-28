import SecurityLayout from '../../components/Layout/SecurityLayout'

export default function SecurityEntryManagement() {
  return (
    <SecurityLayout>
      <div className="bg-white p-6 rounded-xl border border-gray-200 shadow-sm">
        <h1 className="text-2xl font-bold mb-2 text-[#2A2B61]">Vehicle Entry Management</h1>
        <p className="text-[#5A5F72]">Scan plates and approve or reject visitor passes.</p>
      </div>
    </SecurityLayout>
  )
}
