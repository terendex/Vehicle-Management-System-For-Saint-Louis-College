import { useEffect, Suspense } from 'react'
import { BrowserRouter, Routes, Route, Navigate, useParams, useLocation, Outlet } from 'react-router-dom'
import { Toaster } from 'sonner'
import useAuthStore from './stores/authStore'
import { CameraProvider } from './context/CameraContext'
import { LiveUpdatesProvider } from './realtime/LiveUpdatesProvider'
import LoginPage from './pages/Login/LoginPage'
import ProtectedRoute from './components/Auth/ProtectedRoute'
import StepUpGate from './components/TwoFactor/StepUpGate'
import RouteErrorBoundary from './components/RouteErrorBoundary'
// Every page below goes through lazyWithRetry rather than React's lazy: a
// chunk left behind by an older deploy must not blank the route.
import lazy from './utils/lazyWithRetry'
// Layouts are mounted by the shell routes below, never by the pages themselves,
// so the sidebar survives navigation instead of being torn down and rebuilt.
import AdminLayout from './components/Layout/AdminLayout'
import SecurityLayout from './components/Layout/SecurityLayout'
import OwnerLayout from './components/Layout/OwnerLayout'

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
const PaymentPage             = lazy(() => import('./pages/Payment/PaymentPage'))
const ForgotPasswordPage      = lazy(() => import('./pages/ForgotPassword/ForgotPasswordPage'))
const ResetPasswordPage       = lazy(() => import('./pages/ResetPassword/ResetPasswordPage'))
const PolicyPage              = lazy(() => import('./pages/Policy/PolicyPage'))

function RouteFallback() {
  return (
    <div style={{
      minHeight: '60vh', display: 'flex', alignItems: 'center',
      justifyContent: 'center', color: '#5C7B92', fontSize: 14,
      fontFamily: 'system-ui, sans-serif',
    }}>
      Loading…
    </div>
  )
}

// ── Shell routes ───────────────────────────────────────────────────────────
// The layout renders once for the whole role and stays mounted; only <Outlet />
// swaps as you navigate. Suspense sits *inside* the layout so a lazy page chunk
// loading shows the fallback in the content area — it no longer blanks the
// sidebar, and the layout keeps its open-menu state across clicks.

const LAYOUT_BY_ROLE = {
  admin: AdminLayout,
  security: SecurityLayout,
  vehicle_owner: OwnerLayout,
}

// One shell for every signed-in route. It picks the layout from the user's
// role, which is fixed for the session — so the same component sits at the
// same place in the tree no matter which page is showing, and React keeps it
// mounted instead of rebuilding the sidebar.
//
// It is deliberately a single component rather than one per role: /help is
// shared by all three roles and a path can only be declared once, since
// React Router resolves duplicate paths to whichever was declared first.
function RoleShell() {
  const { user } = useAuthStore()
  const { pathname } = useLocation()
  const Layout = LAYOUT_BY_ROLE[user?.role] || OwnerLayout
  // The security entries screen wants the full-height variant.
  const fillHeight = Layout === SecurityLayout && pathname.endsWith('/entries')
  return (
    <Layout fillHeight={fillHeight}>
      {/* Keyed on the path so recovering from a broken page is a navigation
          away — without the key the boundary would latch and every later
          route would keep showing the error. */}
      <RouteErrorBoundary key={pathname}>
        <Suspense fallback={<RouteFallback />}><Outlet /></Suspense>
      </RouteErrorBoundary>
    </Layout>
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
      {/* Renders nothing until a sensitive request is held for a code. Mounted
          at the root so every screen is covered without knowing it exists. */}
      <StepUpGate />
      {/* Outer Suspense covers only the routes that render no layout (login,
          register, 404). Layout routes have their own boundary inside the
          shell so the sidebar is never unmounted by a chunk load. */}
      <RouteErrorBoundary>
      <Suspense fallback={<RouteFallback />}>
      <Routes>
        <Route path="/" element={<Navigate to="/login" replace />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        {/* Applicant's own proof-of-payment step, reached from the pending email.
            Public: the payment token in the query string is the only key. */}
        <Route path="/registration/payment" element={<PaymentPage />} />
        <Route path="/forgot-password" element={<ForgotPasswordPage />} />
        <Route path="/reset-password" element={<ResetPasswordPage />} />
        <Route path="/policy" element={<PolicyPage />} />
        <Route path="/security/guard-login" element={<SecurityQRLogin />} />
        <Route path="/security/guard-login/:gateParam" element={<SecurityQRLogin />} />
        {/* Legacy URL — redirect old bookmarks/kiosks */}
        <Route path="/security/qr-login" element={<Navigate to="/security/guard-login" replace />} />
        <Route path="/security/qr-login/:gateParam" element={<GuardLoginLegacyRedirect />} />

        {/* Role-specific dashboards */}

        {/* Admin Routes — the shell keeps the sidebar mounted across all of them */}
        <Route element={<ProtectedRoute allowedRoles={['admin']} />}>
          <Route element={<RoleShell />}>
            <Route path="/admin" element={<AdminDashboard />} />
            <Route path="/admin/vehicles" element={<VehicleRegistration />} />
            <Route path="/admin/users" element={<UserManagement />} />
            <Route path="/admin/rules" element={<RuleConstraints />} />
            <Route path="/admin/audit" element={<AuditLog />} />
            <Route path="/admin/devices" element={<DeviceManagement />} />
            <Route path="/admin/suppliers" element={<SupplierManagement />} />
            {/* Admin (CDSO) — settings, operations, parking, violations */}
            <Route path="/admin/settings"    element={<SystemSettings />} />
            <Route path="/admin/entries"     element={<OperationsCenter />} />
            <Route path="/admin/parking"     element={<ParkingSpaceManagement />} />
            <Route path="/admin/violations"  element={<ViolationsManagement />} />
          </Route>
          {/* Legacy URL — Events now lives inside Parking Space Management.
              Outside the shell: a redirect renders no content. */}
          <Route path="/admin/events" element={<Navigate to="/admin/parking" replace />} />
        </Route>

        {/* Security Routes */}
        <Route element={<ProtectedRoute allowedRoles={['security']} />}>
          <Route element={<RoleShell />}>
            <Route path="/security/entries" element={<SecurityEntryManagement />} />
            <Route path="/security/gate/:gate/entries" element={<SecurityEntryManagement />} />
            <Route path="/security/parking" element={<SecurityParkingView />} />
            <Route path="/security/audit" element={<SecurityAuditLogPage />} />
          </Route>
          <Route path="/security" element={<Navigate to="/security/entries" replace />} />
        </Route>

        {/* Owner Routes */}
        <Route element={<ProtectedRoute allowedRoles={['vehicle_owner']} />}>
          <Route element={<RoleShell />}>
            <Route path="/owner" element={<OwnerDashboard />} />
          </Route>
        </Route>

        {/* Help — one declaration for every signed-in role. RoleShell supplies
            the right layout, so this must NOT be repeated per role: React
            Router resolves a duplicate path to the first branch declared,
            which would have bounced security and owner users. */}
        <Route element={<ProtectedRoute allowedRoles={['admin', 'security', 'vehicle_owner']} />}>
          <Route element={<RoleShell />}>
            <Route path="/help" element={<HelpPage />} />
          </Route>
        </Route>

        {/* 404 */}
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
      </Suspense>
      </RouteErrorBoundary>
    </BrowserRouter>
    </CameraProvider>
    </LiveUpdatesProvider>
  )
}
