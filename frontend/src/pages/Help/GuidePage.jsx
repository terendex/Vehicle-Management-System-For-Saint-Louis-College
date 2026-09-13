import { useMemo, useState } from 'react'
import { useNavigate, useLocation, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Car, ShieldCheck, LayoutDashboard, BookOpen } from 'lucide-react'
import useAuthStore from '../../stores/authStore'
import BrandLogos from '../../components/BrandLogos'
import { HELP_TOPICS, GUIDE_AUDIENCES } from './helpContent'
import HelpBrowser from './HelpBrowser'
import './GuidePage.css'

const ICONS = { owner: Car, guard: ShieldCheck, cdso: LayoutDashboard }

// Where "Return" goes for each audience: guards came from the gate sign-in
// page, everyone else from the main login.
const RETURN_TO = { guard: '/security/guard-login' }

/**
 * The public help page, reached from the login screens before anyone has an
 * account open. It covers only what a person needs to get IN — signing in,
 * two-factor, a forgotten password, applying for a pass, gate sign-in — split
 * by who is reading. The full manual for a role opens after sign-in at /help,
 * where it is filtered by the account's role.
 */
export default function GuidePage() {
  const navigate = useNavigate()
  const location = useLocation()
  // Read once: switching audience replaces the URL and gets a fresh key, which
  // would otherwise make a directly-opened link look like in-app navigation.
  const [cameFromApp] = useState(() => location.key !== 'default')
  const [params, setParams] = useSearchParams()
  const { isAuthenticated } = useAuthStore()

  const audience = GUIDE_AUDIENCES.some(a => a.id === params.get('for')) ? params.get('for') : 'owner'
  // What only this audience does (a guard's gate sign-in, an applicant's form)
  // comes first; the steps everyone shares follow.
  const topics = useMemo(() => {
    const mine = HELP_TOPICS.filter(t => t.guide?.includes(audience))
    return [...mine.filter(t => t.guide.length === 1), ...mine.filter(t => t.guide.length > 1)]
  }, [audience])
  const current = GUIDE_AUDIENCES.find(a => a.id === audience)

  const choose = (id) => setParams({ for: id }, { replace: true })

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
            // the right sign-in page for a link opened directly.
            onClick={() => (cameFromApp ? navigate(-1) : navigate(RETURN_TO[audience] || '/login'))}
          >
            <ArrowLeft size={16} />
            <span>Back to Login</span>
          </button>
        </div>
      </header>

      <section className="guide-hero">
        <h1 className="guide-hero-title">How can we help?</h1>
        <p className="guide-hero-sub">Choose who you are to see the steps that apply to you.</p>

        <div className="guide-audiences" role="tablist" aria-label="Who is this help for?">
          {GUIDE_AUDIENCES.map(a => {
            const Icon = ICONS[a.id]
            const selected = a.id === audience
            return (
              <button
                key={a.id}
                type="button"
                role="tab"
                aria-selected={selected}
                className={`guide-audience${selected ? ' is-selected' : ''}`}
                onClick={() => choose(a.id)}
              >
                <span className="guide-audience-icon"><Icon size={22} /></span>
                <span className="guide-audience-text">
                  <strong>{a.label}</strong>
                  <span>{a.blurb}</span>
                </span>
              </button>
            )
          })}
        </div>
      </section>

      <main className="guide-main">
        <HelpBrowser
          key={audience}
          topics={topics}
          title={`Getting started: ${current.short}`}
          subtitle="Signed in already? The full manual for your role is under Help inside the system."
          initialTopicId={params.get('topic')}
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
