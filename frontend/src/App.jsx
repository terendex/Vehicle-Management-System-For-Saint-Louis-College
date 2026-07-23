import { useEffect, lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route, Navigate, useParams } from 'react-router-dom'
import { Toaster } from 'sonner'
import useAuthStore from './stores/authStore'
import { CameraProvider } from './context/CameraContext'
import { LiveUpdatesProvider } from './realtime/LiveUpdatesProvider'
import LoginPage from './pages/Login/LoginPage'
import ProtectedRoute from './components/Auth/ProtectedRoute'

// Route-level code splitting. Without this every page — plus recharts, jsPDF
// and html2canvas — is downloaded before the login screen can render, which is
// slow over a tunnel. Each page now loads only when its route is visited.
const NotFoundPage            = lazy(() => import('./pages/NotFoundPage'))
const AdminDashboard          = lazy(() => import('./pages/Admin/AdminDashboard'))
const VehicleRegistration     = lazy(() => import('./pages/Admin/VehicleRegistration'))
const UserManagement          = lazy(() => import('./pages/Admin/UserManagement'))
const OperationsCenter        = lazy(() => import('./pages/Admin/OperationsCenter'))
const RuleConstraints         = lazy(() => import('./pages/Admin/RuleConstraints'))
const AuditLog                = lazy(() => import('./pages/Admin/AuditLog'))
const ParkingSpaceManagement  = lazy(() => import('./pages/Admin/ParkingSpaceManagement'))
const DeviceManagement        = lazy(() => import('./pages/Admin/DeviceManagement'))
const SystemSettings          = lazy(() => import('./pages/Admin/SystemSettings'))
const ViolationsManagement    = lazy(() => import('./pages/Admin/ViolationsManagement'))
const SupplierManagement      = lazy(() => import('./pages/Admin/SupplierManagement'))
const HelpPage                = lazy(() => import('./pages/Help/HelpPage'))
const SecurityEntryManagement = lazy(() => import('./pages/Security/SecurityEntryManagement'))
const SecurityParkingView     = lazy(() => import('./pages/Security/SecurityParkingView'))
const SecurityAuditLogPage    = lazy(() => import('./pages/Security/SecurityAuditLogPage'))
const SecurityQRLogin         = lazy(() => import('./pages/Security/SecurityQRLogin'))
const OwnerDashboard          = lazy(() => import('./pages/VehicleOwner/OwnerDashboard'))
const RegisterPage            = lazy(() => import('./pages/Register/RegisterPage'))
const ForgotPasswordPage      = lazy(() => import('./pages/ForgotPassword/ForgotPasswordPage'))
const ResetPasswordPage       = lazy(() => import('./pages/ResetPassword/ResetPasswordPage'))
const PolicyPage              = lazy(() => import('./pages/Policy/PolicyPage'))

function RouteFallback() {
  return (
    <div style={{
      minHeight: '60vh', display: 'flex', alignItems: 'center',
      justifyContent: 'center', color: '#8892A4', fontSize: 14,
      fontFamily: 'system-ui, sans-serif',
    }}>
      Loading…
    </div>
  )
}

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
      <Suspense fallback={<RouteFallback />}>
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

        {/* Admin (CDSO) — settings, operations, parking, violations */}
        <Route element={<ProtectedRoute allowedRoles={['admin']} />}>
          <Route path="/admin/settings"    element={<SystemSettings />} />
          <Route path="/admin/entries"     element={<OperationsCenter />} />
          <Route path="/admin/parking"     element={<ParkingSpaceManagement />} />
          <Route path="/admin/violations"  element={<ViolationsManagement />} />
          {/* Legacy URL — Events now lives inside Parking Space Management */}
          <Route path="/admin/events"      element={<Navigate to="/admin/parking" replace />} />
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

        {/* Help — available to every signed-in role */}
        <Route element={<ProtectedRoute allowedRoles={['admin', 'security', 'vehicle_owner']} />}>
          <Route path="/help" element={<HelpPage />} />
        </Route>

        {/* 404 */}
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
      </Suspense>
    </BrowserRouter>
    </CameraProvider>
    </LiveUpdatesProvider>
  )
}
