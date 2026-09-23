// Captures raw screenshots plus callout rectangles for every web figure.
// Output: raw/<id>.png + raw/<id>.json   (compose.mjs turns them into figures)
import { chromium } from 'playwright'
import fs from 'node:fs'
import { execFileSync } from 'node:child_process'
import { BASE, login, settle } from './lib.mjs'
import { SHOTS } from './shots.mjs'

const only = process.argv.slice(2)

// Demo-database housekeeping. Only ever touches slc_manual_demo.
export function demoSql(sql) {
  if (!process.env.PGPASSWORD) throw new Error('dot-source demo-env.ps1 first (PGPASSWORD missing)')
  execFileSync('C:\\Program Files\\PostgreSQL\\18\\bin\\psql.exe',
    ['-h', '127.0.0.1', '-U', process.env.DEMO_PG_USER, '-d', 'slc_manual_demo', '-q', '-c', sql], { stdio: 'inherit' })
}
// Every capture run signs the guard in again; drop the closed shifts those
// sign-ins leave behind so the shift history reads like a normal day.
demoSql(`delete from tbl_guard_shift where clocked_out_at is not null and clocked_in_at > now() - interval '20 hours'
         and clocked_in_at::date = now()::date;
         delete from tbl_audit_log where action = 'guard_login' and created_at > now() - interval '6 hours'`)
fs.mkdirSync('raw', { recursive: true })
const DPR = 1.5
const frames = {
  gate: fs.readFileSync('assets/gate_frame.jpg').toString('base64'),
  car: fs.readFileSync('assets/car_frame.jpg').toString('base64'),
  moto: fs.readFileSync('assets/moto_frame.jpg').toString('base64'),
}

// Every camera socket gets a steady sample picture instead of a real RTSP feed.
async function mockCameras(ctx) {
  await ctx.routeWebSocket(/\/ws\/scan\/rtsp\//, (ws) => {
    let timer = null
    ws.onMessage((raw) => {
      let msg; try { msg = JSON.parse(String(raw)) } catch { return }
      if (msg.type !== 'start') return
      const url = msg.rtsp_url || ''
      const kind = /192\.0\.2\.1[14]/.test(url) ? 'gate' : /192\.0\.2\.22/.test(url) ? 'moto' : 'car'
      ws.send(JSON.stringify({ type: 'status', connected: true, message: 'Connected' }))
      const send = () => ws.send(JSON.stringify({ type: 'frame', image_b64: frames[kind] }))
      send()
      timer = setInterval(send, 400)
      if (kind === 'gate') {
        ws.send(JSON.stringify({ type: 'tracks', tracks: [
          { track_id: 1, bbox: [0.30, 0.43, 0.70, 0.90], class_name: 'car', plate_text: 'NBC1234' },
        ] }))
      }
    })
    ws.onClose(() => clearInterval(timer))
  })
}

// A figure may need an API answer this machine cannot produce. The parking
// screens are the case: their status badge asks the server whether the
// detector is running for a zone, and DISABLE_PARKING_AUTODETECT (which the
// capture needs, or the bays would be rewritten mid-run) means the honest
// answer here is always "no". Stubbing it is the same bargain already struck
// for the camera feeds: show the campus install's steady state rather than
// document this laptop.
async function applyRoutes(ctx, routes) {
  for (const [pattern, body] of Object.entries(routes || {})) {
    await ctx.route((url) => url.pathname.includes(pattern), (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }))
  }
}

const browser = await chromium.launch()
const sessions = {}

// Phone figures render as a phone would: touch, mobile viewport, 2x pixels.
const contextOptions = (shot, viewport) => shot.phone
  ? { viewport, deviceScaleFactor: 2, isMobile: true, hasTouch: true }
  : { viewport, deviceScaleFactor: DPR }

async function sessionFor(who, viewport, shot) {
  const key = `${who || 'public'}${shot.phone ? '-phone' : ''}`
  if (!sessions[key]) {
    const ctx = await browser.newContext(contextOptions(shot, viewport))
    await mockCameras(ctx)
    const page = await ctx.newPage()
    if (who) await login(page, who)
    sessions[key] = { ctx, page }
  }
  const s = sessions[key]
  await s.page.setViewportSize(viewport)
  return s
}

async function boxOf(page, target) {
  if (!target) return null
  if (typeof target === 'object' && 'x' in target) return target
  const locs = [].concat(await target(page))
  let r = null
  for (const l of locs) {
    if (!l) continue
    const b = typeof l.first === 'function'
      ? await l.first().boundingBox({ timeout: 4000 }).catch(() => null)
      : await l.boundingBox().catch(() => null)
    if (!b) continue
    r = r ? {
      x: Math.min(r.x, b.x), y: Math.min(r.y, b.y),
      w: Math.max(r.x + r.w, b.x + b.width) - Math.min(r.x, b.x),
      h: Math.max(r.y + r.h, b.y + b.height) - Math.min(r.y, b.y),
    } : { x: b.x, y: b.y, w: b.width, h: b.height }
  }
  return r
}

for (const shot of SHOTS) {
  if (only.length && !only.some((o) => shot.id.includes(o))) continue
  const viewport = shot.viewport || { width: 1440, height: 900 }
  let page
  try {
    // A stubbed route must not leak into the next figure sharing the session,
    // so a shot that stubs anything gets a context of its own and closes it.
    if (shot.fresh || shot.routes) {
      const ctx = await browser.newContext(contextOptions(shot, viewport))
      await mockCameras(ctx)
      await applyRoutes(ctx, shot.routes)
      page = await ctx.newPage()
      shot._ctx = ctx
      if (shot.who) await login(page, shot.who)
    } else {
      page = (await sessionFor(shot.who, viewport, shot)).page
    }
    if (shot.path) await page.goto(BASE + shot.path)
    await settle(page, 1200)
    // Clear any alert the app raised on load (they are acknowledge-to-dismiss).
    for (let i = 0; i < 3; i++) {
      const ok = page.getByRole('button', { name: 'OK', exact: true })
      if (!(await ok.isVisible().catch(() => false))) break
      await ok.click(); await page.waitForTimeout(400)
    }
    if (shot.prepare) await shot.prepare(page)
    await settle(page, shot.wait ?? 1800)
    // Keep the moving parts (spinners, carets) out of the picture.
    await page.addStyleTag({ content: '*{caret-color:transparent!important}' }).catch(() => {})
    await page.mouse.move(1, viewport.height - 1)

    // clipFn measures the page (for a slice of a long phone page); fullPage
    // lets that slice extend below the first screen.
    const clip = shot.clipFn ? await shot.clipFn(page)
      : shot.clip || { x: 0, y: 0, width: viewport.width, height: viewport.height }
    const marks = []
    for (const m of shot.marks || []) {
      const b = await boxOf(page, m.at)
      if (!b) { console.log(`  ! ${shot.id}: mark ${m.n} "${m.label}" not found`); continue }
      marks.push({ ...m, at: undefined, box: { x: b.x - clip.x, y: b.y - clip.y, w: b.w, h: b.h } })
    }
    await page.screenshot({ path: `raw/${shot.id}.png`, clip, fullPage: !!(shot.fullPage || shot.clipFn) })
    fs.writeFileSync(`raw/${shot.id}.json`, JSON.stringify({
      id: shot.id, out: shot.out, section: shot.section, title: shot.title, caption: shot.caption,
      width: clip.width, height: clip.height, scale: DPR, marks, redact: shot.redact || [], patches: shot.patches || [],
      layout: shot.layout || 'below',
    }, null, 2))
    console.log('ok', shot.id, `(${marks.length}/${(shot.marks || []).length} marks)`)
    if (shot.after) await shot.after(page)
    if (shot.afterSql) demoSql(shot.afterSql)
  } catch (e) {
    console.log('FAIL', shot.id, e.message.split('\n')[0])
  } finally {
    if (shot._ctx) await shot._ctx.close()
  }
}
await browser.close()
