import { Navigate, useLocation } from 'react-router-dom'
import { useRoadshow } from './RoadshowContext'
import type { DemoIdentity } from './types'

const landingByRole: Record<DemoIdentity, string> = {
  data_provider: '/data-products',
  model_provider: '/model-products',
  data_requester: '/applications',
  space_operator: '/lifecycle',
}

export function PortalEntry({ role }: { role: DemoIdentity }) {
  const location = useLocation()
  const { authenticated, authLoading, identity } = useRoadshow()
  if (authLoading) return null
  if (!authenticated) return <Navigate to="/demo-login" state={{ from: location.pathname }} replace />
  if (identity !== role) return <Navigate to="/overview" replace />
  return <Navigate to={landingByRole[role]} replace />
}
