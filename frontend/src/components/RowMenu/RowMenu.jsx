import { useState, useEffect, useRef, useCallback } from 'react'
import { createPortal } from 'react-dom'
import { MoreVertical } from 'lucide-react'
import './rowMenu.css'

/* A table row's actions behind one ⋮ button — the menu User Management's
   table established, shared so every admin table that adopts it looks and
   behaves the same.

   The menu is portalled to <body> at fixed coordinates rather than rendered
   inside the table, whose scrolling card used to clip it on the last rows. It
   opens below the button, or above when the viewport runs out, and closes on
   an outside click, Escape, scroll or resize — a fixed-position menu left
   open through a scroll would float away from its row.

   items: [{ key, label, Icon, tone?, onSelect, disabled?, title? }]
     tone   'view' | 'edit' | 'enable' | 'danger' — the hover colour, matching
            User Management's view / edit / enable / disable items. */
export default function RowMenu({ items, label = 'Actions', width = 200 }) {
  const [style, setStyle] = useState(null)   // null = closed
  const buttonRef = useRef(null)
  const menuRef   = useRef(null)
  const close     = useCallback(() => setStyle(null), [])

  useEffect(() => {
    if (!style) return
    const onClick = (e) => {
      // The click that opened the menu, or one inside it, leaves it open.
      if (menuRef.current?.contains(e.target) || buttonRef.current?.contains(e.target)) return
      close()
    }
    const onKey = (e) => { if (e.key === 'Escape') close() }
    window.addEventListener('click', onClick)
    window.addEventListener('keydown', onKey)
    window.addEventListener('resize', close)
    window.addEventListener('scroll', close, true)
    return () => {
      window.removeEventListener('click', onClick)
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('resize', close)
      window.removeEventListener('scroll', close, true)
    }
  }, [style, close])

  const toggle = () => {
    if (style) { close(); return }
    const rect   = buttonRef.current.getBoundingClientRect()
    const height = items.length * 38 + (items.length - 1) * 4 + 16   // item + gap + padding
    const gap    = 8
    const openUp = rect.bottom + gap + height > window.innerHeight - 8 && rect.top - gap - height > 8
    const left   = Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8))
    setStyle({
      left: `${left}px`,
      width: `${width}px`,
      ...(openUp
        ? { bottom: `${window.innerHeight - rect.top + gap}px`, transformOrigin: 'bottom right' }
        : { top: `${rect.bottom + gap}px`, transformOrigin: 'top right' }),
    })
  }

  return (
    <>
      <button ref={buttonRef} type="button" className={`rm-trigger${style ? ' rm-trigger--open' : ''}`}
        aria-label={label} aria-haspopup="menu" aria-expanded={!!style} onClick={toggle}>
        <MoreVertical size={16} />
      </button>
      {style && createPortal(
        <div ref={menuRef} className="rm-menu" role="menu" style={style}>
          {items.map(({ key, label: text, Icon, tone = 'view', onSelect, disabled, title }) => (
            <button key={key} type="button" role="menuitem" className={`rm-item rm-item--${tone}`}
              disabled={disabled} title={title}
              onClick={() => { close(); onSelect() }}>
              {Icon && <Icon size={15} />} {text}
            </button>
          ))}
        </div>,
        document.body,
      )}
    </>
  )
}
