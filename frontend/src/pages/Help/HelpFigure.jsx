import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Maximize2, X } from 'lucide-react'
import { HELP_FIGURES } from './helpFigures'
import usePhoneLayout from './usePhoneLayout'

// Which callout is lit: a hovered item wins while the mouse is over it,
// otherwise the selected one (clicked, tapped or tabbed to) stays lit.
function useHighlight() {
  const [hovered, hover] = useState(null)
  const [selected, select] = useState(null)
  return [hovered ?? selected, { hover, select }]
}

// The picture with its numbered callouts drawn on top. Positions are
// percentages of the image, so the boxes line up at any width.
function Stage({ view, active, hl, onOpen, stageRef, large = false, phone = false }) {
  const Tag = onOpen ? 'button' : 'div'
  const classes = [
    'hf-stage',
    large && 'hf-stage--large',
    view.isPhoneShot && 'hf-stage--phone-shot',
    active && 'has-active',
  ].filter(Boolean).join(' ')
  return (
    <Tag
      ref={stageRef}
      type={onOpen ? 'button' : undefined}
      className={classes}
      style={{ aspectRatio: `${view.width} / ${view.height}` }}
      onClick={onOpen}
      aria-label={onOpen ? `Enlarge picture: ${view.title}` : undefined}
    >
      <img src={view.src} alt={view.title} width={view.width} height={view.height} loading="lazy" draggable={false} />
      {view.callouts.map(c => (
        <span
          key={`box-${c.n}`}
          data-n={c.n}
          className={`hf-box${active === c.n ? ' is-active' : ''}`}
          style={{ left: `${c.box.x}%`, top: `${c.box.y}%`, width: `${c.box.w}%`, height: `${c.box.h}%` }}
          aria-hidden="true"
        />
      ))}
      {view.callouts.map(c => (
        <span
          key={`badge-${c.n}`}
          className={`hf-badge${active === c.n ? ' is-active' : ''}`}
          style={{ left: `${c.badge.x}%`, top: `${c.badge.y}%` }}
          onPointerEnter={(e) => { if (e.pointerType === 'mouse') hl.hover(c.n) }}
          onPointerLeave={(e) => { if (e.pointerType === 'mouse') hl.hover(null) }}
          aria-hidden="true"
        >
          {c.n}
        </span>
      ))}
      {onOpen && (
        <span className="hf-zoom" aria-hidden="true">
          <Maximize2 size={13} /> {phone ? 'Tap to enlarge' : 'Click to enlarge'}
        </span>
      )}
    </Tag>
  )
}

// What each number means. Pointing at (or tabbing to) an item lights up its
// box on the picture and dims the rest. On a touch screen there is no
// pointing, so a tap does the same — and if the box is scrolled out of view,
// as it often is on a tall phone screenshot, the page brings it back.
//
// Two layers: hovering previews an item, and clicking, tapping or tabbing to
// one selects it. They are kept apart because bringing a box into view
// scrolls the page, and the pointer then drifts off the item (or across other
// badges) — a single hover state would switch the highlight straight back off.
// Hover also follows a real mouse only; touch browsers fake mouse events.
function Legend({ view, active, hl, stageRef }) {
  if (!view.callouts.length) return null

  const tap = (n) => {
    hl.select(n)
    const box = stageRef.current?.querySelector(`.hf-box[data-n="${n}"]`)
    if (!box) return
    const r = box.getBoundingClientRect()
    const scroller = box.closest('.hf-lightbox') || null
    const viewTop = scroller ? scroller.getBoundingClientRect().top : 0
    const viewBottom = scroller ? scroller.getBoundingClientRect().bottom : window.innerHeight
    if (r.bottom < viewTop + 40 || r.top > viewBottom - 40) {
      box.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }

  return (
    <ol className="hf-legend">
      {view.callouts.map(c => (
        <li key={c.n}>
          <button
            type="button"
            className={`hf-legend-item${active === c.n ? ' is-active' : ''}`}
            onPointerEnter={(e) => { if (e.pointerType === 'mouse') hl.hover(c.n) }}
            onPointerLeave={(e) => { if (e.pointerType === 'mouse') hl.hover(null) }}
            onFocus={() => hl.select(c.n)}
            onBlur={() => hl.select(null)}
            onClick={() => tap(c.n)}
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

function Lightbox({ view, phone, onClose }) {
  const [active, hl] = useHighlight()
  const scrollRef = useRef(null)
  const stageRef = useRef(null)
  // A desktop screenshot on a phone is kept at a readable width and scrolls
  // sideways; a phone screenshot already fits and needs none of that.
  const sideways = phone && !view.isPhoneShot

  // The part of a wide screen that matters (a dialog, a form) is almost
  // always in the middle, so open there.
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
    <div className="hf-lightbox" role="dialog" aria-modal="true" aria-label={view.title}
         onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`hf-lightbox-panel${view.isPhoneShot ? ' hf-lightbox-panel--phone-shot' : ''}`}>
        <div className="hf-lightbox-head">
          <strong>{view.title}</strong>
          <button type="button" className="hf-lightbox-close" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>
        {sideways && <p className="hf-lightbox-hint">Scroll sideways to see the whole screen.</p>}
        <div className={`hf-lightbox-scroll${sideways ? ' is-sideways' : ''}`} ref={scrollRef}>
          <Stage view={view} active={active} hl={hl} stageRef={stageRef} large phone={phone} />
        </div>
        <Legend view={view} active={active} hl={hl} stageRef={stageRef} />
      </div>
    </div>,
    document.body,
  )
}

export default function HelpFigure({ id, caption }) {
  const fig = HELP_FIGURES[id]
  const phone = usePhoneLayout()
  const [active, hl] = useHighlight()
  const [open, setOpen] = useState(false)
  const stageRef = useRef(null)
  if (!fig) return null

  // On a phone, show the phone photograph of the screen where there is one,
  // so the callouts point at buttons where the reader actually sees them.
  const view = phone && fig.mobile
    ? { ...fig, ...fig.mobile, isPhoneShot: true }
    : fig

  return (
    <figure className={`hf${view.isPhoneShot ? ' hf--phone-shot' : ''}`}>
      <Stage view={view} active={active} hl={hl} stageRef={stageRef}
             onOpen={() => setOpen(true)} phone={phone} />
      <figcaption className="hf-caption">{caption || fig.caption}</figcaption>
      <Legend view={view} active={active} hl={hl} stageRef={stageRef} />
      {open && <Lightbox view={view} phone={phone} onClose={() => setOpen(false)} />}
    </figure>
  )
}
