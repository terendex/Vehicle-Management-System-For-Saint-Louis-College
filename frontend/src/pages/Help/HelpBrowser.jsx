import { useState, useMemo, useEffect, useRef } from 'react'
import { Search, X, HelpCircle, Info, ChevronRight, Images } from 'lucide-react'
import HelpFigure from './HelpFigure'
import { HELP_FIGURES } from './helpFigures'
import './HelpPage.css'

// Text a search should match for a figure: its title, caption and callouts.
function figureText(id) {
  const fig = HELP_FIGURES[id]
  if (!fig) return ''
  return [fig.title, fig.caption, ...fig.callouts.flatMap(c => [c.label, c.desc])].join(' ')
}

// Flatten a topic's body into searchable text, pictures included, so a search
// for a button name finds the topic whose screenshot points at it.
function topicText(topic) {
  const parts = [topic.title, topic.category, topic.summary]
  for (const b of topic.body) {
    if (b.text) parts.push(b.text)
    if (b.items) parts.push(...b.items)
    if (b.caption) parts.push(b.caption)
    if (b.type === 'figure') parts.push(figureText(b.id))
  }
  return parts.filter(Boolean).join(' ').toLowerCase()
}

function Block({ block, stepStart }) {
  switch (block.type) {
    case 'p':
      return <p className="help-p">{block.text}</p>
    case 'steps':
      return (
        <ol className="help-steps" start={stepStart}>
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
    case 'figure':
      return <HelpFigure id={block.id} caption={block.caption} />
    default:
      return null
  }
}

/**
 * Topic list + search + reader. Shared by the signed-in manual (/help), which
 * passes the topics for the user's role, and the public guide (/guide), which
 * passes the topics for the audience the reader picked.
 */
export default function HelpBrowser({ topics, title, subtitle, aside, initialTopicId }) {
  const [query, setQuery] = useState('')
  const q = query.trim().toLowerCase()

  const visibleTopics = useMemo(() => {
    if (!q) return topics
    return topics.filter(t => topicText(t).includes(q))
  }, [topics, q])

  // The guide remounts this component (keyed on audience) when the topic list
  // changes, so the initial pick never outlives the list it was chosen from.
  const [activeId, setActiveId] = useState(initialTopicId || topics[0]?.id)

  const active = visibleTopics.find(t => t.id === activeId) || visibleTopics[0] || null

  const grouped = useMemo(() => {
    const map = new Map()
    for (const t of visibleTopics) {
      if (!map.has(t.category)) map.set(t.category, [])
      map.get(t.category).push(t)
    }
    return [...map.entries()]
  }, [visibleTopics])

  // Opening a topic from a long list should land at its title, not wherever
  // the previous article happened to be scrolled to.
  const articleRef = useRef(null)
  const [picks, setPicks] = useState(0)
  const pick = (id) => {
    setActiveId(id)
    setPicks(n => n + 1)
  }
  useEffect(() => {
    const el = articleRef.current
    if (picks && el && el.getBoundingClientRect().top < 0) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }, [picks])

  const pictureCount = active ? active.body.filter(b => b.type === 'figure').length : 0

  // A procedure broken up by pictures is still one procedure: step lists that
  // follow each other with only figures between them keep counting. Any
  // paragraph or note in between starts a new procedure at 1.
  const stepStarts = useMemo(() => {
    const starts = []
    let next = 1
    for (const b of active?.body || []) {
      if (b.type === 'steps') { starts.push(next); next += b.items.length }
      else { starts.push(undefined); if (b.type !== 'figure') next = 1 }
    }
    return starts
  }, [active])

  return (
    <div className="help-page">
      <div className="help-header">
        <div>
          <h1 className="help-title"><HelpCircle size={20} /> {title}</h1>
          {subtitle && <p className="help-subtitle">{subtitle}</p>}
        </div>
        {aside}
      </div>

      <div className="help-search">
        <Search size={16} />
        <input
          type="text"
          placeholder="Search the guide… (e.g. forgot password, violations, backup)"
          value={query}
          onChange={e => setQuery(e.target.value)}
          aria-label="Search the guide"
        />
        {query && (
          <button className="help-search-clear" onClick={() => setQuery('')} aria-label="Clear search">
            <X size={14} />
          </button>
        )}
      </div>

      <div className="help-body">
        <aside className="help-nav">
          {grouped.length === 0 ? (
            <p className="help-nav-empty">No topics match “{query}”.</p>
          ) : (
            grouped.map(([category, list]) => (
              <div key={category} className="help-nav-group">
                <span className="help-nav-cat">{category}</span>
                {list.map(t => (
                  <button
                    key={t.id}
                    className={`help-nav-item${active?.id === t.id ? ' active' : ''}`}
                    onClick={() => pick(t.id)}
                  >
                    <span>{t.title}</span>
                    <ChevronRight size={14} />
                  </button>
                ))}
              </div>
            ))
          )}
        </aside>

        <article className="help-content" ref={articleRef}>
          {active ? (
            <>
              <span className="help-content-cat">{active.category}</span>
              <h2 className="help-content-title">{active.title}</h2>
              {pictureCount > 0 && (
                <p className="help-picture-hint">
                  <Images size={14} />
                  {pictureCount === 1 ? 'This topic has a picture.' : `This topic has ${pictureCount} pictures.`}
                  {' '}Point at a numbered item to highlight it on the screen, or click a picture to enlarge it.
                </p>
              )}
              {active.body.map((block, i) => <Block key={i} block={block} stepStart={stepStarts[i]} />)}
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
}
