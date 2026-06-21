import { useState, useRef, useEffect, useCallback } from 'react'
import { ChevronDown, Search } from 'lucide-react'
import './ComboBox.css'

export default function ComboBox({
  options = [],
  value = '',
  onChange,
  placeholder = 'Type or select…',
  name,
  required,
}) {
  const [open, setOpen]          = useState(false)
  const [query, setQuery]        = useState(value)
  const [activeIdx, setActiveIdx] = useState(-1)
  const containerRef = useRef(null)
  const inputRef     = useRef(null)
  const listRef      = useRef(null)

  const filtered = query.trim()
    ? options.filter(o => o.toLowerCase().includes(query.toLowerCase()))
    : options

  // Keep local query in sync if parent resets value
  useEffect(() => { setQuery(value || '') }, [value])

  // Close on outside click
  useEffect(() => {
    const handler = (e) => {
      if (containerRef.current && !containerRef.current.contains(e.target)) {
        setOpen(false)
        setActiveIdx(-1)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  // Scroll active item into view
  useEffect(() => {
    if (activeIdx < 0 || !listRef.current) return
    const item = listRef.current.children[activeIdx]
    item?.scrollIntoView({ block: 'nearest' })
  }, [activeIdx])

  const emit = useCallback((val) => {
    onChange({ target: { name, value: val } })
  }, [name, onChange])

  const handleInputChange = (e) => {
    setQuery(e.target.value)
    emit(e.target.value)
    setOpen(true)
    setActiveIdx(-1)
  }

  const handleSelect = (opt) => {
    setQuery(opt)
    emit(opt)
    setOpen(false)
    setActiveIdx(-1)
  }

  const handleKeyDown = (e) => {
    if (!open && (e.key === 'ArrowDown' || e.key === 'Enter')) {
      setOpen(true)
      return
    }
    if (e.key === 'Escape')    { setOpen(false); setActiveIdx(-1); return }
    if (e.key === 'ArrowDown') { e.preventDefault(); setActiveIdx(i => Math.min(i + 1, filtered.length - 1)) }
    if (e.key === 'ArrowUp')   { e.preventDefault(); setActiveIdx(i => Math.max(i - 1, 0)) }
    if (e.key === 'Enter' && activeIdx >= 0 && filtered[activeIdx]) {
      e.preventDefault()
      handleSelect(filtered[activeIdx])
    }
    if (e.key === 'Tab') { setOpen(false); setActiveIdx(-1) }
  }

  return (
    <div className="cb-wrap" ref={containerRef}>
      <div className={`cb-field${open ? ' cb-field--open' : ''}`}>
        <Search size={14} className="cb-search-icon" />
        <input
          ref={inputRef}
          type="text"
          name={name}
          value={query}
          onChange={handleInputChange}
          onFocus={() => setOpen(true)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          required={required}
          autoComplete="off"
          className="cb-input"
        />
        <button
          type="button"
          className={`cb-chevron${open ? ' cb-chevron--open' : ''}`}
          onClick={() => { setOpen(o => !o); inputRef.current?.focus() }}
          tabIndex={-1}
          aria-label="Toggle dropdown"
        >
          <ChevronDown size={15} />
        </button>
      </div>

      {open && (
        <ul className="cb-list" ref={listRef} role="listbox">
          {filtered.length === 0 ? (
            <li className="cb-empty">No matches — you can still type freely</li>
          ) : (
            filtered.map((opt, i) => (
              <li
                key={opt}
                role="option"
                aria-selected={opt === query}
                className={[
                  'cb-option',
                  opt === query   ? 'cb-option--selected' : '',
                  i === activeIdx ? 'cb-option--active'   : '',
                ].filter(Boolean).join(' ')}
                onMouseDown={() => handleSelect(opt)}
                onMouseEnter={() => setActiveIdx(i)}
              >
                {opt}
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  )
}
