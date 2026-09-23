// Turns raw/<id>.png + raw/<id>.json into finished, annotated manual figures.
import { chromium } from 'playwright'
import fs from 'node:fs'
import path from 'node:path'

const OUT_ROOT = process.argv[2]
const only = process.argv.slice(3)
if (!OUT_ROOT) throw new Error('usage: node compose.mjs <outRoot> [filter...]')

const ACCENT = '#E8590C'
// BARE=1: screenshot and numbered callouts only; the Word document supplies
// the title, caption and legend as editable text.
const BARE = process.env.BARE === '1'
// CLEAN=1: the screenshot alone (patches and redactions applied), plus a
// .json of callout positions as fractions, for the in-app help page to draw.
const CLEAN = process.env.CLEAN === '1'
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))

// Badge positions: try each corner of the box, keep the first that clears the
// badges already placed and stays inside the picture.
function placeBadges(marks, W, H, R) {
  const placed = []
  for (const mk of marks) {
    const b = mk.box
    const pref = mk.badge ? [mk.badge] : []
    const order = [...pref, 'tl', 'tr', 'bl', 'br', 'l', 'r', 't', 'b']
    const at = {
      tl: [b.x, b.y], tr: [b.x + b.w, b.y], bl: [b.x, b.y + b.h], br: [b.x + b.w, b.y + b.h],
      l: [b.x, b.y + b.h / 2], r: [b.x + b.w, b.y + b.h / 2], t: [b.x + b.w / 2, b.y], b: [b.x + b.w / 2, b.y + b.h],
    }
    let best = null
    for (const k of order) {
      let [cx, cy] = at[k]
      cx = Math.min(Math.max(cx, R + 2), W - R - 2)
      cy = Math.min(Math.max(cy, R + 2), H - R - 2)
      const clear = placed.every((q) => Math.hypot(q.cx - cx, q.cy - cy) > R * 2.3)
      if (clear) { best = { cx, cy }; break }
      if (!best) best = { cx, cy }
    }
    placed.push(best)
    mk.badgePos = best
  }
}

function figureHtml(meta, imgB64) {
  const k = meta.display ?? 1                       // CSS px per source px
  const W = meta.width * k, H = meta.height * k
  const R = 15
  const pad = 6 * (meta.boxPad ?? 1)
  const marks = meta.marks.map((mk) => ({
    ...mk,
    box: { x: mk.box.x * k - pad, y: mk.box.y * k - pad, w: mk.box.w * k + pad * 2, h: mk.box.h * k + pad * 2 },
  }))
  placeBadges(marks, W, H, R)
  const side = meta.layout === 'side'
  const legendCols = side ? 1 : marks.length > 4 ? 2 : 1

  const boxes = marks.map((mk) => {
    const b = mk.box
    return `<div class="box" style="left:${b.x}px;top:${b.y}px;width:${b.w}px;height:${b.h}px"></div>`
  }).join('')
  const badges = marks.map((mk) =>
    `<div class="badge on-shot" style="left:${mk.badgePos.cx - R}px;top:${mk.badgePos.cy - R}px">${mk.n}</div>`).join('')
  const patches = (meta.patches || []).map((pt) =>
    `<div class="patch" style="left:${pt.x * k}px;top:${pt.y * k}px;width:${pt.w * k}px;height:${pt.h * k}px;` +
    `background:${pt.bg};color:${pt.color};font:${pt.fontSize * k}px 'Segoe UI',sans-serif;padding-left:${(pt.padLeft ?? 2) * k}px">${pt.highlight
      ? `<span style="background:${pt.highlight};color:#fff;padding:0 ${k}px">${esc(pt.text)}</span>` : esc(pt.text)}</div>`).join('')
  const redactLabels = (meta.redact || []).map((r) =>
    r.label ? `<div class="redact-label" style="left:${r.x * k}px;top:${(r.y + r.h / 2) * k - 16}px;width:${r.w * k}px">${esc(r.label)}</div>` : '').join('')
  if (CLEAN) {
    const pct = (v, of) => +(100 * v / of).toFixed(3)
    meta.__callouts = marks.map((mk) => ({
      n: mk.n, label: mk.label, desc: mk.desc,
      box: { x: pct(mk.box.x, W), y: pct(mk.box.y, H), w: pct(mk.box.w, W), h: pct(mk.box.h, H) },
      badge: { x: pct(mk.badgePos.cx, W), y: pct(mk.badgePos.cy, H) },
    }))
    meta.__size = { w: W, h: H }
  }
  const legend = marks.map((mk) =>
    `<li><span class="badge">${mk.n}</span><div><b>${esc(mk.label)}</b><span class="desc">${esc(mk.desc)}</span></div></li>`).join('')

  return `<!doctype html><html><head><meta charset="utf-8"><style>
  @font-face { font-family: InterLocal; src: local('Inter'), local('Segoe UI'); }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #ffffff; font-family: 'Segoe UI', Inter, system-ui, sans-serif; color: #0f2233; }
  .fig { display: inline-block; padding: 34px 40px 26px; background: #fff; }
  header { border-left: 5px solid #0B3A6E; padding: 2px 0 2px 16px; margin-bottom: 22px; max-width: ${side ? W + 460 : W}px; }
  .kicker { font-size: 13px; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; color: #9a6b00; }
  h1 { font-size: 27px; font-weight: 700; color: #0B3A6E; margin-top: 3px; }
  header p { font-size: 15.5px; color: #4a5d6e; margin-top: 5px; }
  .body { display: flex; gap: 30px; flex-direction: ${side ? 'row' : 'column'}; align-items: flex-start; }
  .shot { position: relative; width: ${W}px; height: ${H}px; border-radius: 10px; overflow: hidden;
          box-shadow: 0 0 0 1px #c9d4de, 0 6px 22px rgba(15,34,51,.14); flex: none; }
  .shot img { position: absolute; inset: 0; width: ${W}px; height: ${H}px; }
  .shot canvas { position: absolute; }
  .box { position: absolute; border: 3px solid ${ACCENT}; border-radius: 9px;
         box-shadow: 0 0 0 2px rgba(255,255,255,.9), inset 0 0 0 1px rgba(255,255,255,.55); }
  .badge { display: inline-flex; align-items: center; justify-content: center; flex: none;
           width: ${R * 2}px; height: ${R * 2}px; border-radius: 50%; background: ${ACCENT}; color: #fff;
           font-weight: 800; font-size: 15px; box-shadow: 0 0 0 2.5px #fff, 0 2px 6px rgba(0,0,0,.35); }
  .badge.on-shot { position: absolute; }
  .patch { position: absolute; display: flex; align-items: center; white-space: nowrap; overflow: hidden; }
  .redact-label { position: absolute; text-align: center; }
  .redact-label::after { content: attr(data-x); }
  .redact-label { font-size: 15px; font-weight: 700; color: #fff; }
  .redact-label { text-shadow: 0 1px 3px rgba(0,0,0,.8); }
  ol.legend { list-style: none; display: grid; grid-template-columns: repeat(${legendCols}, minmax(0, 1fr));
              gap: 12px 26px; width: ${side ? 430 : W}px; }
  ol.legend li { display: flex; gap: 12px; align-items: flex-start; padding: 11px 14px; background: #f4f7fa;
                 border: 1px solid #e0e8ef; border-radius: 10px; }
  ol.legend li .badge { box-shadow: none; width: 26px; height: 26px; font-size: 13.5px; margin-top: 1px; }
  ol.legend b { display: block; font-size: 15.5px; color: #0f2233; }
  ol.legend span.desc { display: block; font-size: 14px; line-height: 1.42; color: #43586a; margin-top: 2px; }
  footer { margin-top: 18px; font-size: 12px; color: #8595a3; display: flex; justify-content: space-between; }
  ${BARE ? '.fig { padding: 14px; } header, footer, ol.legend { display: none !important; }' : ''}
  ${CLEAN ? '.fig { padding: 0; } header, footer, ol.legend, .box, .badge.on-shot { display: none !important; } .shot { border-radius: 0; box-shadow: none; }' : ''}
  </style></head><body><div class="fig">
    <header><div class="kicker">${esc(meta.section)}</div><h1>${esc(meta.title)}</h1>${meta.caption ? `<p>${esc(meta.caption)}</p>` : ''}</header>
    <div class="body">
      <div class="shot" id="shot"><img id="img" src="data:image/png;base64,${imgB64}">${patches}${boxes}${redactLabels}${badges}</div>
      ${marks.length ? `<ol class="legend">${legend}</ol>` : ''}
    </div>
    <footer><span>Saint Louis College · Smart Parking and Vehicle Verification System</span><span>User Manual</span></footer>
  </div>
  <script>
    window.__redact = ${JSON.stringify(meta.redact || [])};
    window.__k = ${k};
  </script></body></html>`
}

const browser = await chromium.launch()
// Render at the scale the screenshot was captured at, so a 2x phone capture
// is not resampled down to 1.5x.
const pages = {}
const pageFor = async (scale) => {
  const s = CLEAN ? (scale || 1.5) : 1.5
  if (!pages[s]) {
    const ctx = await browser.newContext({ viewport: { width: 800, height: 600 }, deviceScaleFactor: s })
    pages[s] = await ctx.newPage()
  }
  return pages[s]
}

const metas = fs.readdirSync('raw').filter((f) => f.endsWith('.json'))
  .map((f) => JSON.parse(fs.readFileSync(path.join('raw', f), 'utf8')))
  .filter((mt) => !only.length || only.some((o) => mt.id.includes(o)))

for (const meta of metas) {
  const raw = fs.readFileSync(path.join('raw', `${meta.id}.png`))
  // Web captures are stored at device scale and shown at their CSS size;
  // desktop captures carry their own display factor.
  meta.display = meta.display ?? 1
  const html = figureHtml(meta, raw.toString('base64'))
  const page = await pageFor(raw.readUInt32BE(16) / meta.width / (meta.display ?? 1))
  await page.setContent(html)
  await page.waitForFunction(() => document.getElementById('img').complete)
  // Pixelate redacted areas straight from the source image, so nothing under
  // them survives in the output at any zoom.
  await page.evaluate(() => {
    const img = document.getElementById('img')
    const shot = document.getElementById('shot')
    const k = window.__k
    const sx = img.naturalWidth / img.width
    for (const r of window.__redact) {
      const block = r.block || 14
      const small = document.createElement('canvas')
      small.width = Math.max(1, Math.round((r.w * k) / block))
      small.height = Math.max(1, Math.round((r.h * k) / block))
      const sctx = small.getContext('2d')
      sctx.drawImage(img, r.x * k * sx, r.y * k * sx, r.w * k * sx, r.h * k * sx, 0, 0, small.width, small.height)
      const c = document.createElement('canvas')
      c.width = r.w * k * 2; c.height = r.h * k * 2
      c.style.left = r.x * k + 'px'; c.style.top = r.y * k + 'px'
      c.style.width = r.w * k + 'px'; c.style.height = r.h * k + 'px'
      const cctx = c.getContext('2d')
      cctx.imageSmoothingEnabled = false
      cctx.drawImage(small, 0, 0, c.width, c.height)
      cctx.fillStyle = 'rgba(15,34,51,0.35)'
      cctx.fillRect(0, 0, c.width, c.height)
      shot.insertBefore(c, shot.children[1])
    }
  })
  const el = await page.$(CLEAN ? '#shot' : '.fig')
  const outFile = path.join(OUT_ROOT, meta.out)
  fs.mkdirSync(path.dirname(outFile), { recursive: true })
  await page.setViewportSize({ width: Math.ceil((await el.boundingBox()).width) + 10, height: 600 })
  await el.screenshot({ path: outFile })
  if (CLEAN) {
    fs.writeFileSync(outFile.replace(/\.png$/, '.json'), JSON.stringify({
      id: meta.id, title: meta.title, caption: meta.caption, size: meta.__size, callouts: meta.__callouts,
    }, null, 2))
  }
  console.log('figure', meta.out)
}
await browser.close()
