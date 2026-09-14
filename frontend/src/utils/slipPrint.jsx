// Printing a gate slip — visitor, supplier entry, no-plate entry, supplier
// pass. Shared by the guard's Entry Management and the admin's Supplier
// Management. `slip` is always the server's description (backend
// scanning/slips.py), the same one the thermal printer renders, so the two
// prints cannot disagree.
import { QRCodeSVG } from 'qrcode.react'
import { renderToStaticMarkup } from 'react-dom/server'
import slcLogo from '../assets/slclogo.jpg'
import cdsoLogo from '../assets/cdsologo.jpg'
import { printSlipOnServer, confirmSlipReprinted } from '../api/scanning'
import notify from '../components/Feedback/notify'

// Mirrors ENTRY_FOOTER in slips.py — the lines under the QR when a slip sets none.
const ENTRY_FOOTER = ['SCAN QR AT THE GATE TO EXIT', 'RETURN THIS SLIP UPON EXIT']

const escapeHtml = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))

// Browser print of a gate slip — the fallback for a server with no thermal
// printer (the cloud site), and the "print from this computer" option.
export function printSlipInBrowser(slip, { reprint = false } = {}) {
  const w = window.open('', '_blank', 'width=320,height=520')
  if (!w) return false
  // What the gate scans: the slip's own code (SLC-VISITOR / SLC-SUPPLIER /
  // SLC-NOPLATE:{id}), or for a supplier pass the plate as VEHICLE:{plate} (slip.qr).
  const qrSvg = renderToStaticMarkup(<QRCodeSVG value={slip.qr || slip.code} size={130} level="M" />)
  // A third element marks the row a guard reads at a glance — bold and larger.
  const sections = slip.sections.map(rows => rows.map(([label, value, key]) =>
    `<div class="row${key ? ' key' : ''}"><span class="label">${escapeHtml(label)}:</span><span>${escapeHtml(value)}</span></div>`,
  ).join('\n')).join('\n<hr/>\n')
  const footer = (slip.footer?.length ? slip.footer : ENTRY_FOOTER)
    .map(line => `<div class="warn">${escapeHtml(line)}</div>`).join('\n')
  w.document.write(`<!DOCTYPE html><html><head>
<meta charset="utf-8"/><title>${escapeHtml(slip.title)}</title>
<style>
  /* JP-58H thermal printer. Its POS58 driver's paper is 48mm wide (the
     printable width of the 58mm roll), and its shortest built-in length is
     210mm — ~90mm of blank tail under this slip. The page is instead a custom
     "Visitor Slip 48x130mm" form added to the guard PC (the driver accepts
     custom sizes); the slip runs ~121mm, leaving room for a wrapped line or
     two. A size the driver does not list gets shrunk and centred on its page,
     so this must match that form exactly. */
  @page { size: 48mm 130mm; margin: 0; }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; background: #fff; }
  /* Thermal heads cannot print grey — every tint dithers into specks — so
     the slip is pure black on white. */
  body { font-family: 'Courier New', monospace; font-size: 10px; line-height: 1.25; color: #000;
         width: 48mm; margin: 0; padding: 1mm 1.5mm 2mm; word-wrap: break-word; overflow-wrap: anywhere; }
  h2 { text-align: center; font-size: 12px; margin: 3px 0 1px; }
  .sub { text-align: center; font-size: 8.5px; margin-bottom: 3px; }
  .sub.title { font-size: 13px; font-weight: bold; margin: 4px 0 2px; }
  .sub.reprint { font-size: 10px; font-weight: bold; margin: 0 0 2px; }
  hr { border: none; border-top: 1px dashed #000; margin: 4px 0; }
  .row { display: flex; justify-content: space-between; align-items: baseline; gap: 4px; margin: 2px 0; }
  .label { flex: none; }
  .row span:last-child { text-align: right; min-width: 0; }
  .row.key .label { font-weight: bold; }
  .row.key span:last-child { font-size: 12px; font-weight: bold; }
  .plate { font-size: 18px; font-weight: bold; text-align: center; letter-spacing: 2px; margin: 5px 0; border: 2px solid #000; padding: 3px 2px; }
  .qr { text-align: center; margin: 6px 0 4px; }
  .qr svg { width: 32mm; height: 32mm; }
  .footer { text-align: center; font-size: 8px; margin-top: 6px; }
  .warn { text-align: center; font-size: 10px; font-weight: bold; margin: 4px 0; }
  /* Both seals head the slip, as they head every screen. Kept small for the
     thermal roll, where anything larger prints as a black smudge. */
  .seals { display: flex; justify-content: center; align-items: center; gap: 6px; margin: 0 0 3px; }
  .seals img { width: 9mm; height: 9mm; object-fit: contain; }
</style></head><body>
<div class="seals">
  <img src="${slcLogo}" alt="Saint Louis College"/>
  <img src="${cdsoLogo}" alt="CDSO"/>
</div>
<h2>SAINT LOUIS COLLEGE</h2>
<div class="sub">Campus Development and Sustainability Office</div>
<div class="sub">Smart Parking and Vehicle Verification System</div>
<div class="sub title">${escapeHtml(slip.title)}</div>
${reprint ? '<div class="sub reprint">** REPRINT **</div>' : ''}
<div class="plate">${escapeHtml(slip.headline)}</div>
<hr/>
${sections}
<hr/>
<div class="qr">${qrSvg}</div>
${footer}
<div class="footer">Unauthorized possession is subject to penalty.</div>
</body></html>`)
  w.document.close(); w.focus()
  setTimeout(() => { w.print(); w.close() }, 400)
  return true
}

// Print a slip: straight to the campus server's thermal printer (no dialog),
// or through the browser dialog — when this server has no printer (503, the
// cloud site), or when `browser` asks for it. Either way the server issues the
// slip first: a visitor slip draws a new serial on every print, so what goes
// on paper is the slip the server hands back, never the one passed in.
// Resolves { how: 'printer' | 'dialog' | 'blocked', slip }. Throws with a
// readable reason when a printer exists but nothing came out.
export async function printSlip(slip, { reprint = false, browser = false } = {}) {
  let printed
  try {
    const { data } = await printSlipOnServer(slip.code, reprint, browser ? 'browser' : undefined)
    printed = data.slip || slip
    if (!browser) return { how: 'printer', slip: printed }
  } catch (err) {
    if (err?.response?.status !== 503) {
      throw new Error(
        err?.response?.data?.error || 'The slip did not print — the printer could not be reached.',
        { cause: err },
      )
    }
    printed = err.response.data?.slip || slip
  }
  // A print window opened without a click (a camera-admitted supplier) can be
  // popup-blocked; the caller then offers a button, which is a click.
  if (!printSlipInBrowser(printed, { reprint })) return { how: 'blocked', slip: printed }
  if (reprint) confirmSlipReprinted(printed.code).catch(() => {})
  return { how: 'dialog', slip: printed }
}

// "Visitor Slip for ABC123 (VP-12)" — a no-plate slip's headline is its reference.
export function slipName(slip) {
  const title = slip.title.toLowerCase().replace(/\b\w/g, c => c.toUpperCase())
  return slip.headline === slip.reference
    ? `${title} ${slip.reference}`
    : `${title} for ${slip.headline} (${slip.reference})`
}

// Slip codes with a print under way from this tab. notify collapses identical
// pending dialogs into one promise, so without this a double-click on Print
// would be confirmed once and print twice.
const printing = new Set()

// A print a person asked for: confirm → print → a success or error dialog to
// acknowledge (Feedback/notify). Each step can be switched off where the
// caller already asks or reports in its own words. `onStart` runs once the
// print is confirmed, for the caller's "Printing…" state.
// Resolves { how: 'printer' | 'dialog' | 'cancelled' | 'busy' | 'failed', reason,
// slip } — `slip` is the copy that printed (a visitor slip's new serial), or
// the one passed in when nothing did.
export async function printSlipWithFeedback(slip, {
  reprint = false, confirm = true, success = true, error = true, onStart,
} = {}) {
  // By pass/entry, not code — a visitor slip's code changes with every print.
  const key = `${slip.kind}:${slip.id}`
  if (printing.has(key)) return { how: 'busy', slip }
  printing.add(key)
  try {
    const name = slipName(slip)
    const notes = [
      reprint && 'The copy is marked REPRINT and the reprint is recorded in the audit log.',
      reprint && slip.kind === 'visitor' && 'The slip the visitor holds now stops working — only the new copy can be used.',
    ].filter(Boolean).join(' ')
    if (confirm && !(await notify.confirm({
      title:        reprint ? 'Reprint slip?' : 'Print slip?',
      message:      `${reprint ? 'Reprint' : 'Print'} the ${name} on the thermal printer?`,
      description:  notes,
      confirmLabel: reprint ? 'Reprint' : 'Print',
    }))) return { how: 'cancelled', slip }
    onStart?.()

    let how, printed = slip, reason = ''
    try {
      ({ how, slip: printed } = await printSlip(slip, { reprint }))
      if (how === 'blocked') reason = 'The print window was blocked by the browser. Allow pop-ups for this site, then try again.'
    } catch (err) {
      reason = err.message
    }
    if (reason) {
      if (error) {
        await notify.error(reason, {
          title: reprint ? 'Slip not reprinted' : 'Slip not printed',
          description: how === 'blocked' ? '' : 'Check that the thermal printer is switched on, has paper, and its lid is closed, then try again.',
        })
      }
      return { how: 'failed', reason, slip: printed }
    }
    // The browser's own print dialog is its confirmation; only a thermal print
    // needs telling that it went through.
    if (success && how === 'printer') {
      await notify.success(
        `${slipName(printed)} ${reprint ? 'reprinted' : 'printed'} on the thermal printer.` +
        (printed.serial ? ` Slip No. ${printed.serial}.` : ''),
        { title: reprint ? 'Slip reprinted' : 'Slip printed' })
    }
    return { how, slip: printed }
  } finally {
    printing.delete(key)
  }
}
