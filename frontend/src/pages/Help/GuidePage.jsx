import { useMemo, useState } from 'react'
import { useNavigate, useLocation, useSearchParams } from 'react-router-dom'
import { ArrowLeft, BookOpen } from 'lucide-react'
import useAuthStore from '../../stores/authStore'
import BrandLogos from '../../components/BrandLogos'
import { HELP_TOPICS, GUIDE_AUDIENCES } from './helpContent'
import HelpBrowser from './HelpBrowser'
import './GuidePage.css'

/**
 * The public help page, opened from a login screen before anyone is signed in.
 *
 * Each login screen opens its own audience and nothing else: the main login's
 * help (/guide) covers students, employees and fetchers; the gate sign-in
 * page's (/guide?for=guard) covers guards. There is no picker between them and
 * no CDSO audience at all — how the administration console works is only
 * shown inside it, in the CDSO's own signed-in manual at /help.
 */
export default function GuidePage() {
  const navigate = useNavigate()
  const location = useLocation()
  const [params] = useSearchParams()
  const { isAuthenticated } = useAuthStore()
  // Read once: a directly-opened link has no in-app history to go back to.
  const [cameFromApp] = useState(() => location.key !== 'default')

  // Anything but the guard page's own link falls back to the owner guide.
  const audience = params.get('for') === 'guard' ? 'guard' : 'owner'
  const copy = GUIDE_AUDIENCES[audience]
  const topics = useMemo(() => {
    const mine = HELP_TOPICS.filter(t => t.guide?.includes(audience))
    // What only this audience does comes first; shared steps follow.
    return [...mine.filter(t => t.guide.length === 1), ...mine.filter(t => t.guide.length > 1)]
  }, [audience])

  return (
    <div className="guide-page">
      <header className="guide-header">
        <div className="guide-header-inner header-content">
          <div className="header-logo-group">
            <BrandLogos size="header" />
            <div className="header-text">
              <span className="header-title">SAINT LOUIS COLLEGE</span>
              <span className="header-subtitle">Smart Parking and Vehicle Verification System</span>
            </div>
          </div>
          <button
            className="header-back-btn header-back-btn--end"
            // Back where the reader came from when there is one — a kiosk's
            // /security/guard-login/gate4 URL must survive the detour — or
            // the matching sign-in page for a link opened directly.
            onClick={() => (cameFromApp ? navigate(-1) : navigate(copy.returnTo))}
          >
            <ArrowLeft size={16} />
            <span>Back to Login</span>
          </button>
        </div>
      </header>

      <section className="guide-hero">
        <h1 className="guide-hero-title">{copy.title}</h1>
        <p className="guide-hero-sub">{copy.blurb}</p>
      </section>

      <main className="guide-main">
        <HelpBrowser
          key={audience}
          topics={topics}
          title="Getting started"
          subtitle="Pick a topic, or search. Once you are signed in, Help inside the system has the full guide for your account."
          initialTopicId={params.get('topic')}
          searchExamples={copy.searchExamples}
          aside={isAuthenticated ? (
            <button className="guide-full-manual" onClick={() => navigate('/help')}>
              <BookOpen size={16} />
              Open the full manual
            </button>
          ) : null}
        />
      </main>
    </div>
  )
}
