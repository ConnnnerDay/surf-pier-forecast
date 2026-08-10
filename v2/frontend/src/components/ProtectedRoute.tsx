import type { ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

export function ProtectedRoute({ children }: { children: ReactNode }) {
  const { user, isLoading } = useAuth()

  if (isLoading) return <div className="page">Loading…</div>
  if (!user) return <Navigate to="/login" replace />
  return <>{children}</>
}
