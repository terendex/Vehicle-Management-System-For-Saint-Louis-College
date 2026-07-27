import { Navigate, Outlet } from 'react-router-dom'
import useAuthStore from '../../stores/authStore'

export default function ProtectedRoute({ allowedRoles }) {
  const { isAuthenticated, user } = useAuthStore()

  // 1. If not authenticated, go to login
  if (!isAuthenticated || !user) {
    return <Navigate to="/login" replace />
  }

  // 2. If the user's role is not in the allowed list, redirect them
  // to their specific dashboard based on their role.
  if (allowedRoles && !allowedRoles.includes(user.role)) {
    if (user.role === 'admin') {
      return <Navigate to="/admin" replace />
    } else if (user.role === 'security') {
      return <Navigate to="/security" replace />
    } else if (user.role === 'vehicle_owner') {
      return <Navigate to="/owner" replace />
    } else {
      return <Navigate to="/login" replace />
    }
  }

  // 3. Otherwise, render the child routes
  return <Outlet />
}
