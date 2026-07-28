import { useState, useMemo } from 'react'
import { Search, X, HelpCircle, Info, ChevronRight } from 'lucide-react'
import useAuthStore from '../../stores/authStore'
import { HELP_TOPICS } from './helpContent'
import './HelpPage.css'

// Flatten a topic's body into searchable text.
function topicText(topic) {
  const parts = [topic.title, topic.category]
  for (const b of topic.body) {
    if (b.text) parts.push(b.text)
    if (b.items) parts.push(...b.items)
    if (b.caption) parts.push(b.caption)
  }
  return parts.join(' ').toLowerCase()
}

function Block({ block }) {
  switch (block.type) {
    case 'p':
      return <p className="help-p">{block.text}</p>
    case 'steps':
      return (
        <ol className="help-steps">
          {block.items.map((it, i) => <li key={i}>{it}</li>)}
        </ol>
      )
    case 'list':
      return (
        <ul className="help-list">
          {block.items.map((it, i) => <li key={i}>{it}</li>)}
        </ul>
      )
    case 'note':
      return (
        <div className="help-note">
          <Info size={15} />
          <span>{block.text}</span>
        </div>
      )
    case 'image':
      // Screenshots are added in a later pass; render only when a src is set.
      return block.src ? (
        <figure className="help-figure">
          <img src={block.src} alt={block.alt || ''} loading="lazy" />
          {block.caption && <figcaption>{block.caption}</figcaption>}
        </figure>
      ) : null
    default:
      return null
  }
}

export default function HelpPage() {
  const { user } = useAuthStore()
  const role = user?.role || 'vehicle_owner'

  // Topics this role is allowed to see.
  const roleTopics = useMemo(
    () => HELP_TOPICS.filter(t => t.roles.includes(role)),
    [role]
  )

  const [query, setQuery] = useState('')
  const q = query.trim().toLowerCase()

  const visibleTopics = useMemo(() => {
    if (!q) return roleTopics
    return roleTopics.filter(t => topicText(t).includes(q))
  }, [roleTopics, q])

  const [activeId, setActiveId] = useState(roleTopics[0]?.id)

  // Keep the selected topic valid as the filtered list changes.
  const active =
    visibleTopics.find(t => t.id === activeId) || visibleTopics[0] || null

  // Group visible topics by category for the sidebar.
  const grouped = useMemo(() => {
    const map = new Map()
    for (const t of visibleTopics) {
      if (!map.has(t.category)) map.set(t.category, [])
      map.get(t.category).push(t)
    }
    return [...map.entries()]
  }, [visibleTopics])

  const content = (
    <div className="help-page">
        <div className="help-header">
          <div>
            <h1 className="help-title"><HelpCircle size={20} /> Help &amp; User Manual</h1>
            <p className="help-subtitle">Search the guide or browse topics for your role.</p>
          </div>
        </div>

        <div className="help-search">
          <Search size={16} />
          <input
            type="text"
            placeholder="Search the manual… (e.g. backup, violations, scanning)"
            value={query}
            onChange={e => setQuery(e.target.value)}
          />
          {query && (
            <button className="help-search-clear" onClick={() => setQuery('')} aria-label="Clear search">
              <X size={14} />
            </button>
          )}
        </div>

        <div className="help-body">
          {/* Topic navigation */}
          <aside className="help-nav">
            {grouped.length === 0 ? (
              <p className="help-nav-empty">No topics match “{query}”.</p>
            ) : (
              grouped.map(([category, topics]) => (
                <div key={category} className="help-nav-group">
                  <span className="help-nav-cat">{category}</span>
                  {topics.map(t => (
                    <button
                      key={t.id}
                      className={`help-nav-item${active?.id === t.id ? ' active' : ''}`}
                      onClick={() => setActiveId(t.id)}
                    >
                      <span>{t.title}</span>
                      <ChevronRight size={14} />
                    </button>
                  ))}
                </div>
              ))
            )}
          </aside>

          {/* Topic content */}
          <article className="help-content">
            {active ? (
              <>
                <span className="help-content-cat">{active.category}</span>
                <h2 className="help-content-title">{active.title}</h2>
                {active.body.map((block, i) => <Block key={i} block={block} />)}
              </>
            ) : (
              <div className="help-empty">
                <HelpCircle size={40} strokeWidth={1.4} />
                <p>Select a topic to read the guide.</p>
              </div>
            )}
          </article>
        </div>
    </div>
  )

  // The layout comes from the role's shell route in App.jsx — /help is
  // registered inside each one — so this just returns the content.
  return content
}
