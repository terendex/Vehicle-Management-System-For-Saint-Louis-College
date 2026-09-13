/**
 * Measures the SLC + CDSO brand lockups in a real browser and fails if any of
 * them stops fitting.
 *
 * Why this exists: adding the CDSO seal beside the college seal roughly doubled
 * the width of a mark that appears in eleven places, and the first attempt
 * looked fine on the pages anyone thought to open. It was the security
 * sidebar — a narrower rail, a longer name, and a gate label underneath — that
 * broke: "SLC SECURITY" wrapped after "SLC", and because the bar had a fixed
 * height the extra line was drawn straight through its bottom border.
 *
 * Eyeballing does not catch that reliably, and neither does a unit test on CSS
 * text. So the real stylesheets are loaded into a real layout engine and the
 * boxes are measured.
 *
 * What is asserted, per lockup:
 *   - both seals rendered, square, and the same size as each other
 *   - the name sits on ONE line in a sidebar (it is never allowed to break)
 *   - nothing overflows its bar horizontally or vertically
 *   - the seals never overlap the text beside them
 *
 * Run:  node scripts/check-brand-lockups.mjs
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { chromium } from 'playwright'

const here = dirname(fileURLToPath(import.meta.url))
const src = resolve(here, '../src')
const css = (p) => readFileSync(resolve(src, p), 'utf8')

// The real stylesheets, in the order the app loads them.
const STYLES = [
  css('styles/slc-header.css'),
  css('components/Layout/AdminLayout.css'),
  css('components/Layout/SecurityLayout.css'),
  css('components/Layout/OwnerLayout.css'),
].join('\n')

// A 1x1 PNG stands in for each seal: this measures boxes, not artwork, and a
// data URI keeps the harness free of the real image files.
const PIXEL =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='

const logos = (size) => `
  <span class="brand-logos brand-logos--${size}">
    <img src="${PIXEL}" class="brand-logos-img">
    <img src="${PIXEL}" class="brand-logos-img brand-logos-img--cdso">
  </span>`

// Each case mirrors the JSX it is named after, verbatim in structure.
const CASES = [
  {
    name: 'AdminLayout sidebar (SLC CDSO + bell)',
    width: 260,
    kind: 'sidebar',
    html: `
      <aside class="admin-sidebar">
        <div class="sidebar-brand">
          ${logos('sidebar')}
          <span class="brand-text">SLC CDSO</span>
          <span data-bell style="width:24px;height:24px;flex-shrink:0"></span>
        </div>
      </aside>`,
  },
  {
    name: 'SecurityLayout sidebar (SLC Security + gate label)',
    width: 260,
    kind: 'sidebar',
    wrapperClass: 'security-layout',
    html: `
      <aside class="admin-sidebar">
        <div class="sidebar-brand">
          ${logos('sidebar')}
          <div>
            <span class="brand-text">SLC Security</span>
            <div style="display:flex;align-items:center;gap:4px;margin-top:2px">
              <span style="width:10px;height:10px;flex-shrink:0"></span>
              <span style="font-size:10px;font-weight:600;letter-spacing:.3px">Gate 4</span>
            </div>
          </div>
        </div>
      </aside>`,
  },
  // The page-header lockup, at the widths that actually squeeze it. The title
  // and tagline are allowed to wrap here (see .header-subtitle) — what must
  // not happen is the bar overflowing or the seals running into the text.
  ...[320, 360, 430, 768, 1280].map((width) => ({
    name: `Page header @ ${width}px`,
    width,
    kind: 'header',
    html: `
      <header class="login-header" style="padding-left:16px;padding-right:16px">
        <div class="header-content">
          <div class="header-logo-group">
            ${logos('header')}
            <div class="header-text">
              <span class="header-title">SAINT LOUIS COLLEGE</span>
              <span class="header-subtitle">Smart Parking and Vehicle Verification System</span>
            </div>
          </div>
        </div>
      </header>`,
  })),
  // The owner shell's header is the tightest of the page headers: the lockup
  // shares the bar with an identity chip and two buttons, and unlike the
  // others it has no .header-content wrapper.
  ...[360, 768, 1280].map((width) => ({
    name: `OwnerLayout header @ ${width}px`,
    width,
    kind: 'header',
    barSelector: '.owner-header',
    html: `
      <header class="owner-header">
        <div class="header-logo-group">
          ${logos('header')}
          <div class="header-text">
            <span class="header-title">SAINT LOUIS COLLEGE</span>
            <span class="header-subtitle">Smart Parking and Vehicle Verification System</span>
          </div>
        </div>
        <div class="header-actions">
          <div class="owner-identity">
            <span class="owner-avatar">J</span>
            <span class="owner-identity-text">
              <span class="owner-identity-name">DELA CRUZ, JUAN MIGUEL</span>
              <span class="owner-identity-role">Vehicle Owner</span>
            </span>
          </div>
          <button style="width:34px;height:34px;flex-shrink:0"></button>
          <button style="width:34px;height:34px;flex-shrink:0"></button>
        </div>
      </header>`,
  })),
  // The login and guard sign-in headers carry a Help button, and those two
  // pages are the ones opened on the smallest phones.
  ...[320, 360].map((width) => ({
    name: `Login header @ ${width}px with the Help button`,
    width,
    kind: 'header',
    html: `
      <header class="login-header" style="padding-left:16px;padding-right:16px">
        <div class="header-content">
          <div class="header-logo-group">
            ${logos('header')}
            <div class="header-text">
              <span class="header-title">SAINT LOUIS COLLEGE</span>
              <span class="header-subtitle">Smart Parking and Vehicle Verification System</span>
            </div>
          </div>
          <button class="header-back-btn header-back-btn--end"><span style="width:16px;height:16px;display:inline-block"></span><span>Help</span></button>
        </div>
      </header>`,
  })),
  {
    name: 'Page header @ 360px with a back button',
    width: 360,
    kind: 'header',
    html: `
      <header class="policy-header" style="padding-left:16px;padding-right:16px">
        <div class="header-content">
          <div class="header-logo-group">
            ${logos('header')}
            <div class="header-text">
              <span class="header-title">SAINT LOUIS COLLEGE</span>
              <span class="header-subtitle">Smart Parking and Vehicle Verification System</span>
            </div>
          </div>
          <button class="header-back-btn header-back-btn--end"><span>Back to Login</span></button>
        </div>
      </header>`,
  },
]

const failures = []
const report = []

const browser = await chromium.launch()
const page = await browser.newPage()

for (const c of CASES) {
  await page.setViewportSize({ width: c.width, height: 400 })
  await page.setContent(
    `<!doctype html><html><head><meta charset="utf-8"><style>
       *{box-sizing:border-box} body{margin:0;font-family:system-ui,sans-serif}
       :root{--navy:#03396C;--gold:#E0B00C}
       ${STYLES}
     </style></head>
     <body><div class="${c.wrapperClass ?? ''}">${c.html}</div></body></html>`,
  )

  const m = await page.evaluate((barSelector) => {
    const bar = document.querySelector(barSelector)
    const imgs = [...document.querySelectorAll('.brand-logos-img')]
    const text = document.querySelector('.brand-text, .header-text')
    const r = (el) => {
      const b = el.getBoundingClientRect()
      return { x: b.x, y: b.y, w: b.width, h: b.height, right: b.right, bottom: b.bottom }
    }
    // The bar's padding box — inside its borders. Content that reaches past
    // this is content drawn through the bottom rule, which is the bug this
    // whole file exists to catch.
    const cs = getComputedStyle(bar)
    const inner = {
      top:    bar.getBoundingClientRect().top + parseFloat(cs.borderTopWidth),
      bottom: bar.getBoundingClientRect().bottom - parseFloat(cs.borderBottomWidth),
    }
    // Measured per child rather than from scrollHeight: the gold hairline
    // under the brand bar is an ::after positioned 2.5px BELOW it on purpose,
    // and it lands in scrollHeight, so scrollHeight always reads 3px over.
    const spill = [...bar.querySelectorAll('*')]
      .map((el) => ({ el: el.className || el.tagName, b: el.getBoundingClientRect() }))
      .filter(({ b }) => b.height > 0 &&
                         (b.bottom > inner.bottom + 0.5 || b.top < inner.top - 0.5))
      .map(({ el, b }) => `${el} by ${Math.round(Math.max(b.bottom - inner.bottom, inner.top - b.top))}px`)
    const nameEl = document.querySelector('.brand-text, .header-title')
    // Line count, from the element's own line boxes rather than from height
    // arithmetic — a wrapped name is exactly what this is looking for.
    const lines = nameEl ? nameEl.getClientRects().length : 0
    return {
      bar: r(bar),
      spill,
      imgs: imgs.map(r),
      text: text ? r(text) : null,
      nameLines: lines,
      docScrollW: document.documentElement.scrollWidth,
    }
  }, c.barSelector ?? '.sidebar-brand, header')

  const fail = (msg) => failures.push(`${c.name}: ${msg}`)

  // Both seals present, square, identical.
  if (m.imgs.length !== 2) fail(`expected 2 seals, found ${m.imgs.length}`)
  else {
    const [a, b] = m.imgs
    if (Math.abs(a.w - a.h) > 1) fail(`SLC seal is not square (${a.w}x${a.h})`)
    if (Math.abs(b.w - b.h) > 1) fail(`CDSO seal is not square (${b.w}x${b.h})`)
    if (Math.abs(a.w - b.w) > 0.5) fail(`seals differ in size (${a.w} vs ${b.w})`)
    if (b.x < a.right - 0.5) fail('the seals overlap each other')
    if (m.text && m.text.x < b.right - 0.5) fail('the CDSO seal overlaps the text beside it')
  }

  // Nothing spills out of the bar. This is the exact failure from the
  // screenshot: the gate label drawn across the bar's bottom rule.
  if (m.spill.length) {
    fail(`content draws outside the bar: ${m.spill.join(', ')}`)
  }
  if (m.docScrollW > c.width + 1) {
    fail(`overflows horizontally by ${m.docScrollW - c.width}px`)
  }
  if (m.imgs.some((i) => i.right > m.bar.right + 0.5 || i.bottom > m.bar.bottom + 0.5)) {
    fail('a seal sticks out of the bar')
  }

  // In a sidebar the name must never break: fixed rail, known name.
  if (c.kind === 'sidebar' && m.nameLines !== 1) {
    fail(`the system name wrapped onto ${m.nameLines} lines`)
  }

  report.push(
    `  ${m.imgs.length === 2 ? `${Math.round(m.imgs[0].w)}px seals` : '??'}` +
    `  bar ${Math.round(m.bar.h)}px` +
    `  name ${m.nameLines} line${m.nameLines === 1 ? '' : 's'}` +
    `   ${c.name}`,
  )
}

await browser.close()

console.log('\nBrand lockup measurements:')
report.forEach((l) => console.log(l))

if (failures.length) {
  console.error(`\n${failures.length} lockup problem(s):`)
  failures.forEach((f) => console.error(`  ✗ ${f}`))
  process.exit(1)
}
console.log(`\n✓ all ${CASES.length} lockups fit.`)
