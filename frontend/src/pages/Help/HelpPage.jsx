import { useMemo } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import useAuthStore from '../../stores/authStore'
import { HELP_TOPICS, HELP_ROLE_LABELS } from './helpContent'
import HelpBrowser from './HelpBrowser'

// The signed-in manual. Every topic is tagged with the roles it is for, so a
// guard never wades through CDSO settings and an owner never sees the gate
// terminal — the list below is only what this account can actually use.
export default function HelpPage() {
  const { user } = useAuthStore()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const role = user?.role || 'vehicle_owner'

  const topics = useMemo(() => HELP_TOPICS.filter(t => t.roles.includes(role)), [role])

  // Owners reach /help from the header icon and their shell has no sidebar to
  // navigate back with — admin and security shells do, so they don't need one.
  const aside = role === 'vehicle_owner' ? (
    <button className="header-back-btn header-back-btn--end" onClick={() => navigate('/owner')}>
      <ArrowLeft size={16} />
      <span>Return</span>
    </button>
  ) : null

  return (
    <HelpBrowser
      topics={topics}
      title="Help & User Manual"
      subtitle={`Topics for your role: ${HELP_ROLE_LABELS[role] || 'User'}. Search, or pick a topic on the left.`}
      aside={aside}
      initialTopicId={params.get('topic')}
    />
  )
}
