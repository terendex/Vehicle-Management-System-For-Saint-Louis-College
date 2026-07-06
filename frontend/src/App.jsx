import { useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate, useParams } from 'react-router-dom'
import { Toaster } from 'sonner'
import useAuthStore from './stores/authStore'
import { CameraProvider } from './context/CameraContext'
import { LiveUpdatesProvider } from './realtime/LiveUpdatesProvider'
import LoginPage from './pages/Login/LoginPage'
import NotFoundPage from './pages/NotFoundPage'
import ProtectedRoute from './components/Auth/ProtectedRoute'
import AdminDashboard from './pages/Admin/AdminDashboard'
import VehicleRegistration from './pages/Admin/VehicleRegistration'
import UserManagement from './pages/Admin/UserManagement'
import OperationsCenter from './pages/Admin/OperationsCenter'
import RuleConstraints from './pages/Admin/RuleConstraints'
import AuditLog from './pages/Admin/AuditLog'
import ParkingSpaceManagement from './pages/Admin/ParkingSpaceManagement'
import DeviceManagement from './pages/Admin/DeviceManagement'
import SystemSettings from './pages/Admin/SystemSettings'
import ViolationsManagement from './pages/Admin/ViolationsManagement'
import SupplierManagement from './pages/Admin/SupplierManagement'
import SecurityEntryManagement from './pages/Security/SecurityEntryManagement'
import SecurityParkingView from './pages/Security/SecurityParkingView'
import SecurityAuditLogPage from './pages/Security/SecurityAuditLogPage'
import SecurityQRLogin from './pages/Security/SecurityQRLogin'
import OwnerDashboard from './pages/VehicleOwner/OwnerDashboard'
import RegisterPage from './pages/Register/RegisterPage'
import ForgotPasswordPage from './pages/ForgotPassword/ForgotPasswordPage'
import ResetPasswordPage from './pages/ResetPassword/ResetPasswordPage'
import PolicyPage from './pages/Policy/PolicyPage'

// Old /security/qr-login/:gateParam kiosk URLs → new /security/guard-login/:gateParam
function GuardLoginLegacyRedirect() {
  const { gateParam } = useParams()
  return <Navigate to={`/security/guard-login/${gateParam}`} replace />
}

export default function App() {
  const initAutoLogout = useAuthStore((s) => s.initAutoLogout)

  useEffect(() => {
    initAutoLogout()
  }, [])

  return (
    <LiveUpdatesProvider>
    <CameraProvider>
    <BrowserRouter>
      <Toaster richColors position="top-right" />
      <Routes>
        <Route path="/" element={<Navigate to="/login" replace />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/forgot-password" element={<ForgotPasswordPage />} />
        <Route path="/reset-password" element={<ResetPasswordPage />} />
        <Route path="/policy" element={<PolicyPage />} />
        <Route path="/security/guard-login" element={<SecurityQRLogin />} />
        <Route path="/security/guard-login/:gateParam" element={<SecurityQRLogin />} />
        {/* Legacy URL — redirect old bookmarks/kiosks */}
        <Route path="/security/qr-login" element={<Navigate to="/security/guard-login" replace />} />
        <Route path="/security/qr-login/:gateParam" element={<GuardLoginLegacyRedirect />} />

        {/* Role-specific dashboards */}

        {/* Admin Routes */}
        <Route element={<ProtectedRoute allowedRoles={['admin']} />}>
          <Route path="/admin" element={<AdminDashboard />} />
          <Route path="/admin/vehicles" element={<VehicleRegistration />} />
          <Route path="/admin/users" element={<UserManagement />} />
          <Route path="/admin/rules" element={<RuleConstraints />} />
          <Route path="/admin/audit" element={<AuditLog />} />
          <Route path="/admin/devices" element={<DeviceManagement />} />
          <Route path="/admin/suppliers" element={<SupplierManagement />} />
        </Route>

        {/* Admin + CDSO shared routes */}
        <Route element={<ProtectedRoute allowedRoles={['admin', 'cdso']} />}>
          <Route path="/admin/settings"    element={<SystemSettings />} />
          <Route path="/admin/entries"     element={<OperationsCenter />} />
          <Route path="/admin/parking"     element={<ParkingSpaceManagement />} />
          <Route path="/admin/violations"  element={<ViolationsManagement />} />
          {/* Legacy URL — Events now lives inside Parking Space Management */}
          <Route path="/admin/events"      element={<Navigate to="/admin/parking" replace />} />
        </Route>

        {/* CDSO Routes — landing redirects to settings */}
        <Route element={<ProtectedRoute allowedRoles={['cdso']} />}>
          <Route path="/cdso" element={<Navigate to="/admin/settings" replace />} />
        </Route>

        {/* Security Routes */}
        <Route element={<ProtectedRoute allowedRoles={['security']} />}>
          <Route path="/security" element={<Navigate to="/security/entries" replace />} />
          <Route path="/security/entries" element={<SecurityEntryManagement />} />
          <Route path="/security/gate/:gate/entries" element={<SecurityEntryManagement />} />
          <Route path="/security/parking" element={<SecurityParkingView />} />
          <Route path="/security/audit" element={<SecurityAuditLogPage />} />
        </Route>

        {/* Owner Routes */}
        <Route element={<ProtectedRoute allowedRoles={['vehicle_owner']} />}>
          <Route path="/owner" element={<OwnerDashboard />} />
        </Route>

        {/* 404 */}
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </BrowserRouter>
    </CameraProvider>
    </LiveUpdatesProvider>
  )
}
