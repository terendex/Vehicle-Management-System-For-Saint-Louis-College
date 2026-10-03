// The CDSO's rich-text editor for one Policies tab, in the manner of Google
// Docs: the policy is edited where it reads, with the same styles as the public
// page, under a toolbar. Built on TipTap (ProseMirror); it reads and writes
// HTML, which the server cleans to an allowlist (backend vehicles/policy_html.py)
// before storing it.
//
// The toolbar only offers what that allowlist keeps — add a button here and
// the server will strip its output unless policy_html.py allows it too.
import { useRef, useState } from 'react'
import { useEditor, useEditorState, EditorContent } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import TextAlign from '@tiptap/extension-text-align'
import { TextStyleKit } from '@tiptap/extension-text-style'
import Highlight from '@tiptap/extension-highlight'
import { OrderedList } from '@tiptap/extension-list'
import { Table, TableRow, TableHeader, TableCell } from '@tiptap/extension-table'
import {
  Undo2, Redo2, Bold, Italic, Underline, Strikethrough, Baseline, Highlighter,
  Link as LinkIcon, AlignLeft, AlignCenter, AlignRight, AlignJustify, List, ListOrdered,
  IndentDecrease, IndentIncrease, Quote, Minus, Table as TableIcon, RemoveFormatting,
  PaintBucket, Trash2, BetweenHorizontalEnd, BetweenVerticalEnd, Rows3, Columns3, Plus,
} from 'lucide-react'

// A table cell with a shading colour, like Docs' cell background. Stored both
// as a data attribute (what the editor reads back) and as inline style (what
// the public page shows).
const ShadedCell = TableCell.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      backgroundColor: {
        default: null,
        parseHTML: el => el.getAttribute('data-background-color') || el.style.backgroundColor || null,
        renderHTML: attrs => attrs.backgroundColor
          ? { 'data-background-color': attrs.backgroundColor, style: `background-color: ${attrs.backgroundColor}` }
          : {},
      },
    }
  },
})

// Numbered lists that can count a, b, c or i, ii, iii. The `type` attribute
// alone loses to the site's global list reset, so it is also written as an
// inline list-style-type, which the public page honours.
const LIST_STYLE = { 1: 'decimal', a: 'lower-alpha', A: 'upper-alpha', i: 'lower-roman', I: 'upper-roman' }
const StyledOrderedList = OrderedList.extend({
  addAttributes() {
    const parent = this.parent?.() ?? {}
    return {
      ...parent,
      type: {
        ...parent.type,
        renderHTML: attrs => (attrs.type && attrs.type !== '1'
          ? { type: attrs.type, style: `list-style-type: ${LIST_STYLE[attrs.type] || 'decimal'}` }
          : {}),
      },
    }
  },
})

const EXTENSIONS = [
  StarterKit.configure({
    heading: { levels: [1, 2, 3] },
    code: false,
    codeBlock: false,
    orderedList: false,                           // replaced by StyledOrderedList
    link: { openOnClick: false, autolink: true, defaultProtocol: 'https',
            HTMLAttributes: { target: '_blank', rel: 'noopener noreferrer nofollow' } },
  }),
  StyledOrderedList,
  TextStyleKit,                                   // colour, background, font family, size, line height
  Highlight.configure({ multicolor: true }),
  TextAlign.configure({ types: ['heading', 'paragraph'], alignments: ['left', 'center', 'right', 'justify'] }),
  Table.configure({ resizable: true }),
  TableRow, TableHeader, ShadedCell,
]

const STYLES = [
  { value: 'p',  label: 'Normal text' },
  { value: 'h1', label: 'Title' },
  { value: 'h2', label: 'Heading' },
  { value: 'h3', label: 'Subheading' },
]

const FONTS = [
  { value: '',                                 label: 'Default' },
  { value: 'Arial, sans-serif',                label: 'Arial' },
  { value: 'Georgia, serif',                   label: 'Georgia' },
  { value: '"Times New Roman", Times, serif',  label: 'Times New Roman' },
  { value: 'Verdana, sans-serif',              label: 'Verdana' },
  { value: '"Courier New", monospace',         label: 'Courier New' },
]

const LINE_SPACING = ['1', '1.15', '1.5', '1.75', '2']

const LIST_TYPES = [
  { value: '1', label: '1, 2, 3' },
  { value: 'a', label: 'a, b, c' },
  { value: 'A', label: 'A, B, C' },
  { value: 'i', label: 'i, ii, iii' },
]

const DEFAULT_SIZE = 13.5   // px — the body text size in PolicyPage.css

function Btn({ title, active, disabled, onClick, children }) {
  return (
    <button
      type="button"
      className={`pe-btn ${active ? 'is-active' : ''}`}
      title={title}
      aria-label={title}
      aria-pressed={active ?? undefined}
      disabled={disabled}
      // Cancelling mousedown keeps the selection in the document; the action
      // runs on click, so Enter and Space work for keyboard users too.
      onMouseDown={e => e.preventDefault()}
      onClick={onClick}
    >
      {children}
    </button>
  )
}

/** A colour swatch that opens the native picker. */
function ColorBtn({ title, icon: Icon, value, onPick, onClear }) {
  const input = useRef(null)
  return (
    <span className="pe-color">
      <button type="button" className="pe-btn" title={title} aria-label={title}
              onMouseDown={e => e.preventDefault()} onClick={() => input.current?.click()}>
        <Icon size={16} />
        <span className="pe-color-bar" style={{ background: value || 'transparent' }} />
      </button>
      <input ref={input} type="color" tabIndex={-1} aria-hidden="true"
             value={value && /^#[0-9a-f]{6}$/i.test(value) ? value : '#000000'}
             onChange={e => onPick(e.target.value)} />
      {value && (
        <button type="button" className="pe-color-clear" title={`Remove ${title.toLowerCase()}`}
                onMouseDown={e => e.preventDefault()} onClick={onClear}>×</button>
      )}
    </span>
  )
}

/** The font size box. It keeps its own text while being typed in, and the
 *  size is applied on Enter or when the box loses focus; applying on every
 *  keystroke would take "16" as "1" and hand focus back to the document. */
function SizeInput({ value, onApply }) {
  const [text, setText] = useState(null)        // null = not being edited: show the selection's size
  const apply = () => {
    const n = parseFloat(text)
    setText(null)
    if (n && n !== value) onApply(n)
  }
  return (
    <input
      className="pe-size-input"
      aria-label="Font size"
      inputMode="decimal"
      value={text ?? (value ?? '')}
      placeholder={String(DEFAULT_SIZE)}
      onFocus={e => { setText(String(value ?? DEFAULT_SIZE)); e.target.select() }}
      onChange={e => setText(e.target.value)}
      onBlur={apply}
      onKeyDown={e => {
        if (e.key === 'Enter') { e.preventDefault(); apply() }
        if (e.key === 'Escape') setText(null)
      }}
    />
  )
}

export default function PolicyEditor({ initialHtml, onChange, onReady }) {
  const [linkOpen, setLinkOpen] = useState(false)
  const [linkUrl, setLinkUrl] = useState('')

  const editor = useEditor({
    extensions: EXTENSIONS,
    content: initialHtml,
    editorProps: { attributes: { class: 'policy-content policy-doc pe-surface', spellcheck: 'true' } },
    onCreate: ({ editor }) => onReady?.(editor.getHTML()),
    onUpdate: ({ editor }) => onChange(editor.getHTML()),
  })

  // Everything the toolbar shows, recomputed only when the selection or the
  // document changes.
  const s = useEditorState({
    editor,
    selector: ({ editor: e }) => {
      if (!e) return null
      const ts = e.getAttributes('textStyle')
      return {
        canUndo: e.can().undo(),
        canRedo: e.can().redo(),
        style: e.isActive('heading', { level: 1 }) ? 'h1'
          : e.isActive('heading', { level: 2 }) ? 'h2'
          : e.isActive('heading', { level: 3 }) ? 'h3' : 'p',
        font: ts.fontFamily || '',
        size: ts.fontSize ? parseFloat(ts.fontSize) : null,
        lineHeight: ts.lineHeight || '',
        color: ts.color || '',
        highlight: e.getAttributes('highlight').color || '',
        bold: e.isActive('bold'),
        italic: e.isActive('italic'),
        underline: e.isActive('underline'),
        strike: e.isActive('strike'),
        link: e.isActive('link'),
        align: ['left', 'center', 'right', 'justify'].find(a => e.isActive({ textAlign: a })) || '',
        bullet: e.isActive('bulletList'),
        ordered: e.isActive('orderedList'),
        listType: e.getAttributes('orderedList').type || '1',
        inList: e.isActive('listItem'),
        quote: e.isActive('blockquote'),
        inTable: e.isActive('table'),
        cellColor: e.getAttributes('tableCell').backgroundColor || '',
      }
    },
  })

  if (!editor || !s) return null
  const chain = () => editor.chain().focus()

  const setStyle = (v) => {
    if (v === 'p') chain().setParagraph().run()
    else chain().toggleHeading({ level: Number(v[1]) }).run()
  }
  const size = s.size ?? DEFAULT_SIZE
  const setSize = (n) => {
    const px = Math.min(72, Math.max(8, n))
    chain().setFontSize(`${px}px`).run()
  }

  // A line with a fresh paragraph under it, so typing carries on below the
  // line (as in Docs) rather than at the start of whatever block follows.
  const insertLine = () => {
    chain().insertContent([{ type: 'horizontalRule' }, { type: 'paragraph' }]).run()
  }

  const openLink = () => {
    setLinkUrl(editor.getAttributes('link').href || '')
    setLinkOpen(true)
  }
  const applyLink = () => {
    const url = linkUrl.trim()
    if (!url) chain().extendMarkRange('link').unsetLink().run()
    else {
      const href = /^(https?:|mailto:|tel:)/i.test(url) ? url
        : url.includes('@') ? `mailto:${url}` : `https://${url}`
      if (editor.state.selection.empty && !editor.isActive('link')) {
        chain().insertContent({ type: 'text', text: url, marks: [{ type: 'link', attrs: { href } }] }).run()
      } else {
        chain().extendMarkRange('link').setLink({ href }).run()
      }
    }
    setLinkOpen(false)
  }

  return (
    <div className="pe">
      {/* Sticky as a whole. The buttons sit in a row that wraps on a wide
          screen and scrolls sideways on a phone; the link field is a row of
          its own beneath, so the scrolling row cannot clip it. */}
      <div className="pe-toolbar">
       <div className="pe-toolbar-row" role="toolbar" aria-label="Formatting">
        <div className="pe-group">
          <Btn title="Undo (Ctrl+Z)" disabled={!s.canUndo} onClick={() => chain().undo().run()}><Undo2 size={16} /></Btn>
          <Btn title="Redo (Ctrl+Y)" disabled={!s.canRedo} onClick={() => chain().redo().run()}><Redo2 size={16} /></Btn>
        </div>

        <div className="pe-group">
          <select className="pe-select" title="Text style" value={s.style} onChange={e => setStyle(e.target.value)}>
            {STYLES.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
          <select className="pe-select pe-select-font" title="Font" value={s.font}
                  onChange={e => e.target.value ? chain().setFontFamily(e.target.value).run() : chain().unsetFontFamily().run()}>
            {FONTS.map(o => <option key={o.label} value={o.value} style={{ fontFamily: o.value || undefined }}>{o.label}</option>)}
          </select>
        </div>

        <div className="pe-group pe-size" title="Font size">
          <Btn title="Decrease font size" onClick={() => setSize(size - 1)}><Minus size={14} /></Btn>
          <SizeInput value={s.size} onApply={setSize} />
          <Btn title="Increase font size" onClick={() => setSize(size + 1)}><Plus size={14} /></Btn>
        </div>

        <div className="pe-group">
          <Btn title="Bold (Ctrl+B)" active={s.bold} onClick={() => chain().toggleBold().run()}><Bold size={16} /></Btn>
          <Btn title="Italic (Ctrl+I)" active={s.italic} onClick={() => chain().toggleItalic().run()}><Italic size={16} /></Btn>
          <Btn title="Underline (Ctrl+U)" active={s.underline} onClick={() => chain().toggleUnderline().run()}><Underline size={16} /></Btn>
          <Btn title="Strikethrough" active={s.strike} onClick={() => chain().toggleStrike().run()}><Strikethrough size={16} /></Btn>
          <ColorBtn title="Text colour" icon={Baseline} value={s.color}
                    onPick={c => chain().setColor(c).run()} onClear={() => chain().unsetColor().run()} />
          <ColorBtn title="Highlight colour" icon={Highlighter} value={s.highlight}
                    onPick={c => chain().setHighlight({ color: c }).run()} onClear={() => chain().unsetHighlight().run()} />
          <Btn title="Link" active={s.link || linkOpen} onClick={openLink}><LinkIcon size={16} /></Btn>
        </div>

        <div className="pe-group">
          <Btn title="Align left" active={s.align === 'left'} onClick={() => chain().setTextAlign('left').run()}><AlignLeft size={16} /></Btn>
          <Btn title="Centre" active={s.align === 'center'} onClick={() => chain().setTextAlign('center').run()}><AlignCenter size={16} /></Btn>
          <Btn title="Align right" active={s.align === 'right'} onClick={() => chain().setTextAlign('right').run()}><AlignRight size={16} /></Btn>
          <Btn title="Justify" active={s.align === 'justify'} onClick={() => chain().setTextAlign('justify').run()}><AlignJustify size={16} /></Btn>
          <select className="pe-select pe-select-narrow" title="Line spacing" value={s.lineHeight}
                  onChange={e => e.target.value ? chain().setLineHeight(e.target.value).run() : chain().unsetLineHeight().run()}>
            <option value="">Spacing</option>
            {LINE_SPACING.map(v => <option key={v} value={v}>{v}</option>)}
          </select>
        </div>

        <div className="pe-group">
          <Btn title="Bulleted list" active={s.bullet} onClick={() => chain().toggleBulletList().run()}><List size={16} /></Btn>
          <Btn title="Numbered list" active={s.ordered} onClick={() => chain().toggleOrderedList().run()}><ListOrdered size={16} /></Btn>
          {s.ordered && (
            <select className="pe-select pe-select-narrow" title="Numbering style" value={s.listType}
                    onChange={e => chain().updateAttributes('orderedList', { type: e.target.value }).run()}>
              {LIST_TYPES.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          )}
          <Btn title="Decrease indent" disabled={!s.inList} onClick={() => chain().liftListItem('listItem').run()}><IndentDecrease size={16} /></Btn>
          <Btn title="Increase indent" disabled={!s.inList} onClick={() => chain().sinkListItem('listItem').run()}><IndentIncrease size={16} /></Btn>
        </div>

        <div className="pe-group">
          <Btn title="Highlighted box" active={s.quote} onClick={() => chain().toggleBlockquote().run()}><Quote size={16} /></Btn>
          <Btn title="Horizontal line" onClick={insertLine}><Minus size={16} /></Btn>
          <Btn title="Insert table" onClick={() => chain().insertTable({ rows: 3, cols: 2, withHeaderRow: false }).run()}><TableIcon size={16} /></Btn>
          <select className="pe-select pe-select-narrow" title="Insert the current fee" value=""
                  onChange={e => { if (e.target.value) chain().insertContent(e.target.value).run() }}>
            <option value="">Insert fee</option>
            <option value="{student_fee}">Student / fetcher fee</option>
            <option value="{employee_fee}">Employee fee</option>
          </select>
          <Btn title="Clear formatting" onClick={() => chain().unsetAllMarks().unsetTextAlign().run()}><RemoveFormatting size={16} /></Btn>
        </div>

        {s.inTable && (
          <div className="pe-group pe-table-tools">
            <Btn title="Add row below" onClick={() => chain().addRowAfter().run()}><BetweenHorizontalEnd size={16} /></Btn>
            <Btn title="Add column right" onClick={() => chain().addColumnAfter().run()}><BetweenVerticalEnd size={16} /></Btn>
            <Btn title="Delete row" onClick={() => chain().deleteRow().run()}><Rows3 size={16} /></Btn>
            <Btn title="Delete column" onClick={() => chain().deleteColumn().run()}><Columns3 size={16} /></Btn>
            <ColorBtn title="Cell shading" icon={PaintBucket} value={s.cellColor}
                      onPick={c => chain().setCellAttribute('backgroundColor', c).run()}
                      onClear={() => chain().setCellAttribute('backgroundColor', null).run()} />
            <Btn title="Delete table" onClick={() => chain().deleteTable().run()}><Trash2 size={16} /></Btn>
          </div>
        )}
       </div>

        {linkOpen && (
          <div className="pe-linkbar">
            <LinkIcon size={14} />
            <input
              autoFocus
              className="pe-linkbar-input"
              aria-label="Link address"
              placeholder="Paste a link or email"
              value={linkUrl}
              onChange={e => setLinkUrl(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter') { e.preventDefault(); applyLink() }
                if (e.key === 'Escape') setLinkOpen(false)
              }}
            />
            <button type="button" className="pe-linkbar-btn primary" onClick={applyLink}>Apply</button>
            {s.link && (
              <button type="button" className="pe-linkbar-btn"
                      onClick={() => { chain().extendMarkRange('link').unsetLink().run(); setLinkOpen(false) }}>
                Remove
              </button>
            )}
            <button type="button" className="pe-linkbar-btn" onClick={() => setLinkOpen(false)}>Cancel</button>
          </div>
        )}
      </div>

      <p className="pe-hint">
        <code>{'{student_fee}'}</code> and <code>{'{employee_fee}'}</code> show the current
        fees from System Settings on the public page.
      </p>

      <EditorContent editor={editor} />
    </div>
  )
}
