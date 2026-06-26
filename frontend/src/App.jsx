import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import LoginPage from './pages/Login/LoginPage'
import NotFoundPage from './pages/NotFoundPage'
import ProtectedRoute from './components/Auth/ProtectedRoute'
import AdminDashboard from './pages/Admin/AdminDashboard'
import VehicleRegistration from './pages/Admin/VehicleRegistration'
import UserManagement from './pages/Admin/UserManagement'
import EntryManagement from './pages/Admin/EntryManagement'
import RuleConstraints from './pages/Admin/RuleConstraints'
import AuditLog from './pages/Admin/AuditLog'
import ParkingManagement from './pages/Admin/ParkingManagement'
import SystemSettings from './pages/Admin/SystemSettings'
import ViolationsManagement from './pages/Admin/ViolationsManagement'
import SecurityDashboard from './pages/Security/SecurityDashboard'
import SecurityEntryManagement from './pages/Security/SecurityEntryManagement'
import SecurityAuditLog from './pages/Security/SecurityAuditLog'
import SecurityParkingView from './pages/Security/SecurityParkingView'
import OwnerDashboard from './pages/VehicleOwner/OwnerDashboard'
import RegisterPage from './pages/Register/RegisterPage'
import ForgotPasswordPage from './pages/ForgotPassword/ForgotPasswordPage'
import ResetPasswordPage from './pages/ResetPassword/ResetPasswordPage'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Navigate to="/login" replace />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/forgot-password" element={<ForgotPasswordPage />} />
        <Route path="/reset-password" element={<ResetPasswordPage />} />

        {/* Role-specific dashboards */}

        {/* Admin Routes */}
        <Route element={<ProtectedRoute allowedRoles={['admin']} />}>
          <Route path="/admin" element={<AdminDashboard />} />
          <Route path="/admin/vehicles" element={<VehicleRegistration />} />
          <Route path="/admin/users" element={<UserManagement />} />
          <Route path="/admin/rules" element={<RuleConstraints />} />
          <Route path="/admin/audit" element={<AuditLog />} />
        </Route>

        {/* Admin + CDSO shared routes */}
        <Route element={<ProtectedRoute allowedRoles={['admin', 'cdso']} />}>
          <Route path="/admin/settings"    element={<SystemSettings />} />
          <Route path="/admin/entries"     element={<EntryManagement />} />
          <Route path="/admin/parking"     element={<ParkingManagement />} />
          <Route path="/admin/violations"  element={<ViolationsManagement />} />
        </Route>

        {/* CDSO Routes — landing redirects to settings */}
        <Route element={<ProtectedRoute allowedRoles={['cdso']} />}>
          <Route path="/cdso" element={<Navigate to="/admin/settings" replace />} />
        </Route>

        {/* Security Routes */}
        <Route element={<ProtectedRoute allowedRoles={['security']} />}>
          <Route path="/security" element={<SecurityDashboard />} />
          <Route path="/security/entries" element={<SecurityEntryManagement />} />
          <Route path="/security/audit" element={<SecurityAuditLog />} />
          <Route path="/security/parking" element={<SecurityParkingView />} />
        </Route>

        {/* Owner Routes */}
        <Route element={<ProtectedRoute allowedRoles={['vehicle_owner']} />}>
          <Route path="/owner" element={<OwnerDashboard />} />
        </Route>

        {/* 404 */}
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </BrowserRouter>
  )
}
