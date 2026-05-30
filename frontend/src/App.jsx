import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import LoginPage from './pages/Login/LoginPage'
import ProtectedRoute from './components/Auth/ProtectedRoute'
import AdminDashboard from './pages/Admin/AdminDashboard'
import VehicleRegistration from './pages/Admin/VehicleRegistration'
import UserManagement from './pages/Admin/UserManagement'
import EntryManagement from './pages/Admin/EntryManagement'
import RuleConstraints from './pages/Admin/RuleConstraints'
import AuditLog from './pages/Admin/AuditLog'
import SecurityDashboard from './pages/Security/SecurityDashboard'
import SecurityEntryManagement from './pages/Security/SecurityEntryManagement'
import SecurityAuditLog from './pages/Security/SecurityAuditLog'
import OwnerDashboard from './pages/VehicleOwner/OwnerDashboard'
import RegisterPage from './pages/Register/RegisterPage'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        
        {/* Role-specific dashboards */}
        
        {/* Admin Routes */}
        <Route element={<ProtectedRoute allowedRoles={['admin']} />}>
          <Route path="/admin" element={<AdminDashboard />} />
          <Route path="/admin/vehicles" element={<VehicleRegistration />} />
          <Route path="/admin/users" element={<UserManagement />} />
          <Route path="/admin/entries" element={<EntryManagement />} />
          <Route path="/admin/rules" element={<RuleConstraints />} />
          <Route path="/admin/audit" element={<AuditLog />} />
        </Route>
        
        {/* Security Routes */}
        <Route element={<ProtectedRoute allowedRoles={['security']} />}>
          <Route path="/security" element={<SecurityDashboard />} />
          <Route path="/security/entries" element={<SecurityEntryManagement />} />
          <Route path="/security/audit" element={<SecurityAuditLog />} />
        </Route>
        
        {/* Owner Routes */}
        <Route element={<ProtectedRoute allowedRoles={['vehicle_owner']} />}>
          <Route path="/owner" element={<OwnerDashboard />} />
        </Route>

        {/* Fallback */}
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
