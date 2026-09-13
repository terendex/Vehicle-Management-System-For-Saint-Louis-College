import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Maximize2, X } from 'lucide-react'
import { HELP_FIGURES } from './helpFigures'

// The picture with its numbered callouts drawn on top. Positions are
// percentages of the image, so the boxes line up at any width.
function Stage({ fig, active, setActive, onOpen, large = false }) {
  const Tag = onOpen ? 'button' : 'div'
  return (
    <Tag
      type={onOpen ? 'button' : undefined}
      className={`hf-stage${large ? ' hf-stage--large' : ''}${active ? ' has-active' : ''}`}
      style={{ aspectRatio: `${fig.width} / ${fig.height}` }}
      onClick={onOpen}
      aria-label={onOpen ? `Enlarge picture: ${fig.title}` : undefined}
    >
      <img src={fig.src} alt={fig.title} width={fig.width} height={fig.height} loading="lazy" draggable={false} />
      {fig.callouts.map(c => (
        <span
          key={`box-${c.n}`}
          className={`hf-box${active === c.n ? ' is-active' : ''}`}
          style={{ left: `${c.box.x}%`, top: `${c.box.y}%`, width: `${c.box.w}%`, height: `${c.box.h}%` }}
          aria-hidden="true"
        />
      ))}
      {fig.callouts.map(c => (
        <span
          key={`badge-${c.n}`}
          className={`hf-badge${active === c.n ? ' is-active' : ''}`}
          style={{ left: `${c.badge.x}%`, top: `${c.badge.y}%` }}
          onMouseEnter={() => setActive(c.n)}
          onMouseLeave={() => setActive(null)}
          aria-hidden="true"
        >
          {c.n}
        </span>
      ))}
      {onOpen && (
        <span className="hf-zoom" aria-hidden="true"><Maximize2 size={13} /> Click to enlarge</span>
      )}
    </Tag>
  )
}

// What each number means. Pointing at (or tabbing to) an item lights up its
// box on the picture and dims the rest, so the reader never has to hunt.
function Legend({ fig, active, setActive }) {
  if (!fig.callouts.length) return null
  return (
    <ol className="hf-legend">
      {fig.callouts.map(c => (
        <li key={c.n}>
          <button
            type="button"
            className={`hf-legend-item${active === c.n ? ' is-active' : ''}`}
            onMouseEnter={() => setActive(c.n)}
            onMouseLeave={() => setActive(null)}
            onFocus={() => setActive(c.n)}
            onBlur={() => setActive(null)}
          >
            <span className="hf-legend-num">{c.n}</span>
            <span className="hf-legend-text">
              <strong>{c.label}</strong>
              <span>{c.desc}</span>
            </span>
          </button>
        </li>
      ))}
    </ol>
  )
}

function Lightbox({ fig, onClose }) {
  const [active, setActive] = useState(null)
  const scrollRef = useRef(null)

  // On a phone the screen is wider than the viewport; the part that matters
  // (a dialog, a form) is almost always in the middle, so open there.
  useEffect(() => {
    const el = scrollRef.current
    if (el && el.scrollWidth > el.clientWidth) el.scrollLeft = (el.scrollWidth - el.clientWidth) / 2
  }, [])

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    window.addEventListener('keydown', onKey)
    return () => {
      document.body.style.overflow = prev
      window.removeEventListener('keydown', onKey)
    }
  }, [onClose])

  return createPortal(
    <div className="hf-lightbox" role="dialog" aria-modal="true" aria-label={fig.title}
         onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="hf-lightbox-panel">
        <div className="hf-lightbox-head">
          <strong>{fig.title}</strong>
          <button type="button" className="hf-lightbox-close" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>
        <p className="hf-lightbox-hint">Scroll sideways to see the whole screen.</p>
        <div className="hf-lightbox-scroll" ref={scrollRef}>
          <Stage fig={fig} active={active} setActive={setActive} large />
        </div>
        <Legend fig={fig} active={active} setActive={setActive} />
      </div>
    </div>,
    document.body,
  )
}

export default function HelpFigure({ id, caption }) {
  const fig = HELP_FIGURES[id]
  const [active, setActive] = useState(null)
  const [open, setOpen] = useState(false)
  if (!fig) return null

  return (
    <figure className="hf">
      <Stage fig={fig} active={active} setActive={setActive} onOpen={() => setOpen(true)} />
      <figcaption className="hf-caption">{caption || fig.caption}</figcaption>
      <Legend fig={fig} active={active} setActive={setActive} />
      {open && <Lightbox fig={fig} onClose={() => setOpen(false)} />}
    </figure>
  )
}
