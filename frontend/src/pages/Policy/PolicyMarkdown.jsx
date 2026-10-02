// Renders a policy written in the small Markdown dialect the CDSO edits on
// /policy, onto the page's existing classes (PolicyPage.css):
//
//   # Title                 the document title; the paragraph after it is the subtitle
//   ## Heading              starts a section
//   ### Heading             starts a sub-section inside it
//   - item                  a bulleted list
//   a. item                 a lettered list (any single letter)
//   1. item                 a numbered list
//   Label :: text           a sanction row (the rows are tinted 1st, 2nd, 3rd)
//   > line                  the highlighted acknowledgement box
//   **bold**  *italic*  __underline__  [text](https://…)  emails become links
//   {student_fee} {employee_fee}   the fees from System Settings
//
// Everything is built as React elements, never as HTML, so nothing an editor
// types can inject markup or script into a public page.
import { Fragment } from 'react'

const BULLET  = /^[-*]\s+(.*)$/
const LETTER  = /^([a-zA-Z])[.)]\s+(.*)$/
const NUMBER  = /^(\d+)[.)]\s+(.*)$/
const SANCTION = /^(.+?)\s+::\s+(.*)$/
const HEADING = /^(#{1,4})\s+(.*)$/

/** Lines → flat blocks. A list is one block holding all its consecutive items. */
function parseBlocks(lines) {
  const blocks = []
  let para = null
  const endPara = () => { para = null }

  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i]
    const line = raw.trim()
    if (!line) { endPara(); continue }

    if (line.startsWith('>')) {
      endPara()
      const inner = []
      while (i < lines.length && lines[i].trim().startsWith('>')) {
        inner.push(lines[i].trim().replace(/^>\s?/, ''))
        i++
      }
      i--
      blocks.push({ type: 'box', children: parseBlocks(inner) })
      continue
    }

    let m
    if ((m = line.match(HEADING))) {
      endPara()
      blocks.push({ type: `h${m[1].length}`, text: m[2] })
      continue
    }

    const listItem =
      ((m = line.match(BULLET))   && { kind: 'bullet', text: m[1] }) ||
      ((m = line.match(LETTER))   && { kind: 'letter', mark: m[1], text: m[2] }) ||
      ((m = line.match(NUMBER))   && { kind: 'number', mark: m[1], text: m[2] }) ||
      ((m = line.match(SANCTION)) && { kind: 'sanction', mark: m[1], text: m[2] })
    if (listItem) {
      endPara()
      const last = blocks[blocks.length - 1]
      if (last?.type === 'list' && last.kind === listItem.kind) last.items.push(listItem)
      else blocks.push({ type: 'list', kind: listItem.kind, items: [listItem] })
      continue
    }

    // An indented line straight after a list item continues that item.
    const last = blocks[blocks.length - 1]
    if (!para && last?.type === 'list' && /^\s/.test(raw) && lines[i - 1]?.trim()) {
      last.items[last.items.length - 1].text += ' ' + line
      continue
    }

    if (para) para.text += ' ' + line
    else { para = { type: 'p', text: line }; blocks.push(para) }
  }
  return blocks
}

// ── Inline ────────────────────────────────────────────────────────────────────

const INLINE = /(\*\*(.+?)\*\*|__(.+?)__|\*(.+?)\*|\[([^\]]+)\]\(([^)\s]+)\)|[\w.+-]+@[\w-]+(?:\.[\w-]+)+)/

function safeHref(url) {
  return /^(https?:|mailto:)/i.test(url) ? url : null
}

function inline(text, key = 'i') {
  const out = []
  let rest = text
  let n = 0
  while (rest) {
    const m = rest.match(INLINE)
    if (!m) { out.push(rest); break }
    if (m.index) out.push(rest.slice(0, m.index))
    const k = `${key}-${n++}`
    const [whole, , bold, under, italic, linkText, linkUrl] = m
    if (bold !== undefined)        out.push(<strong key={k}>{inline(bold, k)}</strong>)
    else if (under !== undefined)  out.push(<u key={k}>{inline(under, k)}</u>)
    else if (italic !== undefined) out.push(<em key={k}>{inline(italic, k)}</em>)
    else if (linkText !== undefined) {
      const href = safeHref(linkUrl)
      out.push(href
        ? <a key={k} href={href} className="policy-email-link" target="_blank" rel="noreferrer">{linkText}</a>
        : linkText)
    } else {
      out.push(<a key={k} href={`mailto:${whole}`} className="policy-email-link">{whole}</a>)
    }
    rest = rest.slice(m.index + whole.length)
  }
  return out
}

// ── Blocks ────────────────────────────────────────────────────────────────────

const SANCTION_TONES = ['first', 'second', 'third']

function renderList(block, key) {
  if (block.kind === 'letter' || block.kind === 'number') {
    return (
      <div key={key} className="policy-lettered-list">
        {block.items.map((item, i) => (
          <div key={i} className="policy-lettered-item">
            <span className="policy-letter">{item.mark}.</span>
            <p>{inline(item.text, `${key}-${i}`)}</p>
          </div>
        ))}
      </div>
    )
  }
  if (block.kind === 'sanction') {
    return (
      <div key={key} className="policy-sanctions">
        {block.items.map((item, i) => (
          <div key={i} className={`policy-sanction-row ${SANCTION_TONES[Math.min(i, 2)]}`}>
            <span className="policy-sanction-label">{inline(item.mark, `${key}-${i}l`)}</span>
            <span className="policy-sanction-desc">{inline(item.text, `${key}-${i}`)}</span>
          </div>
        ))}
      </div>
    )
  }
  return (
    <ul key={key}>
      {block.items.map((item, i) => <li key={i}>{inline(item.text, `${key}-${i}`)}</li>)}
    </ul>
  )
}

/** A run of blocks with no sectioning: paragraphs, lists, minor headings. */
function renderFlat(blocks, key) {
  return blocks.map((b, i) => {
    const k = `${key}-${i}`
    if (b.type === 'p')    return <p key={k}>{inline(b.text, k)}</p>
    if (b.type === 'list') return renderList(b, k)
    if (b.type === 'box')  return <div key={k} className="policy-consent-box">{renderFlat(b.children, k)}</div>
    if (b.type === 'h1' || b.type === 'h2') return <h3 key={k}>{inline(b.text, k)}</h3>
    return <h4 key={k}>{inline(b.text, k)}</h4>
  })
}

/** Groups blocks into the page's title / section / sub-section structure. */
export default function PolicyMarkdown({ source, values = {} }) {
  const filled = source.replace(/\{(\w+)\}/g, (whole, name) => (name in values ? values[name] : whole))
  const blocks = parseBlocks(filled.split(/\r?\n/))

  const out = []
  let section = null      // { heading, body: [], subs: [{ heading, body }] }
  const flush = () => {
    if (!section) return
    const k = `s${out.length}`
    out.push(
      <section key={k} className="policy-section">
        {section.heading && <h3>{inline(section.heading, k)}</h3>}
        {renderFlat(section.body, k)}
        {section.subs.map((sub, j) => (
          <div key={j} className="policy-sub-section">
            <h4>{inline(sub.heading, `${k}-${j}`)}</h4>
            {renderFlat(sub.body, `${k}-${j}`)}
          </div>
        ))}
      </section>,
    )
    section = null
  }

  blocks.forEach((b, i) => {
    const k = `b${i}`
    if (b.type === 'h1') {
      flush()
      out.push(<h2 key={k} className="policy-doc-title">{inline(b.text, k)}</h2>)
      // The paragraph right under the title is its subtitle.
      if (blocks[i + 1]?.type === 'p') blocks[i + 1].subtitle = true
    } else if (b.subtitle) {
      out.push(<p key={k} className="policy-effective">{inline(b.text, k)}</p>)
    } else if (b.type === 'h2') {
      flush()
      section = { heading: b.text, body: [], subs: [] }
    } else if (b.type === 'box') {
      // A box closes the section above it, so it stands on its own like the
      // acknowledgement at the end of each policy.
      flush()
      out.push(<Fragment key={k}>{renderFlat([b], k)}</Fragment>)
    } else {
      if (!section) section = { heading: null, body: [], subs: [] }
      if (b.type === 'h3' || b.type === 'h4') section.subs.push({ heading: b.text, body: [] })
      else if (section.subs.length) section.subs[section.subs.length - 1].body.push(b)
      else section.body.push(b)
    }
  })
  flush()

  return <>{out}</>
}
