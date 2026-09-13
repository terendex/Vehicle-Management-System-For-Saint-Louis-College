import { useState, useEffect, useCallback, useRef } from 'react'
import { useLiveUpdates } from '../../realtime/useLiveUpdates'
import {
  CheckCircle, XCircle, HelpCircle, AlertTriangle,
  ClipboardList, UserPlus, X, Shield, Search, LogOut, Video, Wifi, Star, Clock,
  DoorOpen, Ban, ScanLine, Maximize2, Minimize2, Users, FileQuestion,
  VideoOff, RefreshCw,
} from 'lucide-react'
import notify, { toast } from '../../components/Feedback/notify'
import { fieldProblems } from '../../components/Feedback/formProblems'
import { formatDistanceToNow } from 'date-fns'
import { QRCodeSVG } from 'qrcode.react'
import { renderToStaticMarkup } from 'react-dom/server'
import QrScanModal from '../../components/QrScanModal'
import slcLogo from '../../assets/slclogo.jpg'
import cdsoLogo from '../../assets/cdsologo.jpg'
import ConfiscatedAccounts from '../../components/ConfiscatedAccounts'
import { useFullscreen } from '../../hooks/useFullscreen'
import {
  manualEntry, getAccessLogs, getOffices,
  createVisitorPass, overrideEntry, denyEntry,
  getVisitorPasses, extendVisitorPass,
  confirmVisitorSlipPrinted, visitorQrExit,
  lookupOwner, getUnrecognizedInside, recordUnrecognizedEntry, recordUnrecognizedExit,
} from '../../api/scanning'
import { getSystemSettings } from '../../api/vehicles'
import { camerasApi } from '../../api/cameras'
import { useCameraContext } from '../../context/CameraContext'
import useAuthStore from '../../stores/authStore'
import { useGates } from '../../hooks/useGates'
import { formatPlateNumber, isValidPlateNumber, isValidConductionNumber } from '../../utils/plateFormat'
import { feedState, FEED_LABEL, FEED_DOT } from '../../utils/feedState'
import '../../styles/camera-monitor.css'
import './SecurityEntryManagement.css'


const STATUS_META = {
  authorized: { label: 'Approved for Entry',     Icon: CheckCircle,   cls: 'authorized', logCls: 'authorized' },
  open_entry: { label: 'Open Entry',             Icon: DoorOpen,      cls: 'authorized', logCls: 'authorized' },
  wrong_day:  { label: 'Wrong Schedule Day',     Icon: XCircle,       cls: 'wrong_day',  logCls: 'wrong_day'  },
  denied:     { label: 'Entry Denied',           Icon: XCircle,       cls: 'denied',     logCls: 'denied'     },
  unknown:    { label: 'Visitor / Unregistered', Icon: HelpCircle,    cls: 'visitor',    logCls: 'visitor'    },
  no_pass:    { label: 'No Visitor Pass',        Icon: AlertTriangle, cls: 'visitor',    logCls: 'visitor'    },
  disabled:   { label: 'Access Disabled',        Icon: XCircle,       cls: 'denied',     logCls: 'denied'     },
  unreadable: { label: 'Unreadable Plate',       Icon: AlertTriangle, cls: 'visitor',    logCls: 'visitor'    },
  exited:     { label: 'Exited',                 Icon: LogOut,        cls: 'exited',     logCls: 'exited'     },
  duplicate:      { label: 'Duplicate Scan', Icon: Clock,       cls: 'exited',    logCls: 'exited'    },
  already_inside: { label: 'Previously Scanned', Icon: CheckCircle, cls: 'wrong_day', logCls: 'wrong_day' },
  visitor_pass_required: { label: 'Scan Visitor Slip QR', Icon: AlertTriangle, cls: 'visitor', logCls: 'visitor' },
}
function getMeta(status) { return STATUS_META[status] ?? STATUS_META.unknown }

// WHO is coming in, as opposed to what was decided about them — the second
// thing a guard has to know at the barrier and the one the log could not
// answer before. Same vocabulary and the same chip colours as the admin Entry
// Management screen; those .cls-* rules arrive through this page's stylesheet,
// which imports that one.
const CLASSIFICATION_META = {
  student:  { label: 'Student',              cls: 'cls-student'  },
  employee: { label: 'Employee',             cls: 'cls-employee' },
  fetcher:  { label: 'Drop & Go / Fetcher',  cls: 'cls-fetcher'  },
  supplier: { label: 'Supplier',             cls: 'cls-supplier' },
  visitor:  { label: 'Visitor',              cls: 'cls-visitor'  },
  unknown:  { label: 'Unregistered',         cls: 'cls-unknown'  },
}
function getClassMeta(c) { return CLASSIFICATION_META[c] ?? CLASSIFICATION_META.unknown }

// What a guard may file a walk-up under. 'supplier' is absent on purpose: a
// supplier is identified by a plate on the supplier roster, and a vehicle with
// no plate has nothing to check against it.
const MANUAL_CATEGORIES = ['student', 'employee', 'fetcher', 'visitor', 'unknown']

// Mirrors Vehicle.Type on the server. Kept as labels rather than raw values so
// the guard picks "E-Bike", not "ebike".
const VEHICLE_TYPES = [
  { value: 'car',        label: 'Car' },
  { value: 'motorcycle', label: 'Motorcycle' },
  { value: 'ebike',      label: 'E-Bike' },
  { value: 'truck',      label: 'Truck' },
  { value: 'van',        label: 'Van' },
  { value: 'bus',        label: 'Bus' },
]

// A Philippine plate and a conduction sticker both always carry digits; a
// person's name never does. That single rule is what sends what the guard
// typed down the plate-check path or the name-search path, and it is simple
// enough to state in the hint under the field.
const looksLikeIdentifier = (raw) => /\d/.test(raw || '')

// A gate with a handful of cameras is picked from the cards at a glance; the
// search box is only worth its row once the cards stop fitting.
const CAM_SEARCH_MIN = 4

// The category for a scan result, for the dialog that reports it. The log rows
// carry a real `classification` from the server; a fresh scan response carries
// the owner instead, so it is read off that.
function resultClassification(result) {
  if (result.classification) return result.classification
  const ownerType = result.vehicle?.user?.owner_type
  if (ownerType && CLASSIFICATION_META[ownerType]) return ownerType
  if (result.is_supplier) return 'supplier'
  if (result.status === 'unknown' || result.status === 'unreadable') return 'unknown'
  return 'visitor'
}


function timeAgo(ts) {
  try { return formatDistanceToNow(new Date(ts), { addSuffix: true }) }
  catch { return '' }
}

// Active-visitor panel cache — hydrated instantly on mount so the list never
// flashes empty when a guard refreshes / hard-refreshes; the server fetch then
// reconciles it. Scoped to today so stale days never linger.
const PASS_CACHE_KEY = 'slc_active_passes'
function loadCachedPasses() {
  try {
    const raw = JSON.parse(localStorage.getItem(PASS_CACHE_KEY) || 'null')
    if (raw && raw.date === new Date().toDateString() && Array.isArray(raw.passes)) return raw.passes
  } catch { /* ignore */ }
  return []
}
function saveCachedPasses(passes) {
  try {
    localStorage.setItem(PASS_CACHE_KEY, JSON.stringify({ date: new Date().toDateString(), passes }))
  } catch { /* ignore */ }
}

// Time-left / overstay info for an active visitor pass
function passTimeInfo(p) {
  if (!p.expires_at) return { label: 'No limit', overdue: false, soon: false }
  const diffMin = Math.round((new Date(p.expires_at).getTime() - Date.now()) / 60000)
  if (diffMin >= 0) return { label: `${diffMin}m left`, overdue: false, soon: diffMin <= 10 }
  return { label: `OVERSTAY +${-diffMin}m`, overdue: true, soon: false }
}

function printVisitorSlip({ plate, purpose, officeName, guardName, issuedAt, expiresAt, duration, qrPayload }) {
  const w = window.open('', '_blank', 'width=320,height=520')
  if (!w) return
  // QR scanned at the gate to record the visitor's exit (payload: SLC-VISITOR:{id})
  const qrSvg = qrPayload
    ? renderToStaticMarkup(<QRCodeSVG value={qrPayload} size={130} level="M" />)
    : ''
  // Short form — the year-and-seconds version overflows a 48mm line.
  const fmt = (d) => d ? new Date(d).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '—'
  w.document.write(`<!DOCTYPE html><html><head>
<meta charset="utf-8"/><title>Visitor Slip</title>
<style>
  /* JP-58H thermal printer: 58mm roll, 48mm (384-dot) printable width. The
     page height is set to the slip's measured length just before printing,
     so the printer feeds one slip instead of a Letter-length page. */
  @page { size: 58mm 200mm; margin: 0; }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; background: #fff; }
  /* Thermal heads cannot print grey — every tint dithers into specks — so
     the slip is pure black on white. */
  body { font-family: 'Courier New', monospace; font-size: 10px; line-height: 1.25; color: #000;
         width: 48mm; margin: 0 auto; padding: 2mm 1mm 4mm; word-wrap: break-word; overflow-wrap: anywhere; }
  h2 { text-align: center; font-size: 12px; margin: 3px 0 1px; }
  .sub { text-align: center; font-size: 8.5px; margin-bottom: 3px; }
  .sub.title { font-size: 10px; font-weight: bold; margin: 4px 0 2px; }
  hr { border: none; border-top: 1px dashed #000; margin: 4px 0; }
  .row { display: flex; justify-content: space-between; gap: 4px; margin: 2px 0; }
  .label { flex: none; }
  .row span:last-child { text-align: right; min-width: 0; }
  .plate { font-size: 18px; font-weight: bold; text-align: center; letter-spacing: 2px; margin: 5px 0; border: 2px solid #000; padding: 3px 2px; }
  .qr { text-align: center; margin: 6px 0 4px; }
  .qr svg { width: 32mm; height: 32mm; }
  .footer { text-align: center; font-size: 8px; margin-top: 6px; }
  .warn { text-align: center; font-size: 9px; font-weight: bold; margin: 4px 0; }
  /* Both seals head the slip, as they head every screen. Kept small for the
     58mm roll, where anything larger prints as a black smudge. */
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
<div class="sub title">--- VISITOR SLIP ---</div>
<div class="plate">${plate}</div>
<hr/>
<div class="row"><span class="label">Office:</span><span>${officeName || 'N/A'}</span></div>
<div class="row"><span class="label">Purpose:</span><span>${purpose || 'N/A'}</span></div>
<div class="row"><span class="label">Duration:</span><span>${duration} min</span></div>
<hr/>
<div class="row"><span class="label">Issued:</span><span>${fmt(issuedAt)}</span></div>
<div class="row"><span class="label">Expires:</span><span>${fmt(expiresAt)}</span></div>
<div class="row"><span class="label">Guard:</span><span>${guardName || 'N/A'}</span></div>
<hr/>
${qrSvg ? `<div class="qr">${qrSvg}</div>
<div class="warn">SCAN THIS QR AT THE GATE TO EXIT</div>` : ''}
<div class="warn">RETURN THIS SLIP UPON EXIT</div>
<div class="footer">Unauthorized possession is subject to penalty.</div>
</body></html>`)
  w.document.close(); w.focus()
  setTimeout(() => {
    // Size the page to the slip itself (CSS px → mm, plus a little tail for
    // the tear bar) so the roll isn't fed a blank Letter-length page.
    const heightMm = Math.ceil((w.document.body.scrollHeight * 25.4) / 96) + 6
    const pageStyle = w.document.createElement('style')
    pageStyle.textContent = `@page { size: 58mm ${heightMm}mm; margin: 0; }`
    w.document.head.appendChild(pageStyle)
    w.print(); w.close()
  }, 400)
}

// ─── VisitorPassModal ──────────────────────────────────────────────────────────
function VisitorPassModal({ plate, offices, onClose, onCreated, guardName }) {
  const [officeId, setOfficeId] = useState('')
  const [purpose, setPurpose]   = useState('')
  const [duration, setDuration] = useState('15')  // typeable string; default 15 min
  const [loading, setLoading]   = useState(false)

  const durationNum = Math.max(1, Math.min(480, parseInt(duration, 10) || 15))

  const doPrint = (pass) => {
    const officeName = offices.find(o => String(o.id) === String(officeId))?.name
    printVisitorSlip({
      plate, purpose, officeName, guardName,
      issuedAt: pass.entered_at, expiresAt: pass.expires_at, duration: durationNum,
      qrPayload: pass.qr_payload || `SLC-VISITOR:${pass.id}`,
    })
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    const problems = [...fieldProblems(e.currentTarget)]
    if (!purpose.trim()) problems.push('Enter the purpose of the visit.')
    if (await notify.validation(problems, { title: 'Pass not issued' })) return
    setLoading(true)
    try {
      const res = await createVisitorPass({
        plate_number: plate, office: officeId || null, purpose, allowed_duration: durationNum,
      })
      const pass = res.data
      doPrint(pass)
      // Auto-log the visitor's entry as soon as the slip is sent to the printer —
      // no separate manual confirmation step.
      try {
        await confirmVisitorSlipPrinted(pass.id)
        toast.success(`Visitor pass issued & entry logged for ${plate} — valid ${durationNum} min.`)
      } catch {
        toast.success(`Visitor pass issued for ${plate}.`)
      }
      onCreated()
      onClose()
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Failed to create visitor pass.')
    } finally { setLoading(false) }
  }

  return (
    <div className="em-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="em-modal">
        <div className="em-modal-head">
          <span className="em-modal-title"><UserPlus size={17} /> Create Visitor Pass</span>
          <button className="em-modal-close" onClick={onClose}><X size={15} /></button>
        </div>
        <form onSubmit={handleSubmit} noValidate>
          <div className="em-modal-body">
            <div className="em-field">
              <label className="em-label">License Plate</label>
              <input className="em-input" value={plate} readOnly />
            </div>
            <div className="em-field">
              <label className="em-label">Destination Office <span style={{ color: '#64839C', fontWeight: 400 }}>(optional)</span></label>
              <select className="em-select" value={officeId} onChange={(e) => setOfficeId(e.target.value)}>
                <option value="">No specific office</option>
                {offices.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
              </select>
            </div>
            <div className="em-field">
              <label className="em-label">Purpose of Visit</label>
              <textarea className="em-textarea" placeholder="e.g. Enrollment inquiry…" value={purpose}
                onChange={(e) => setPurpose(e.target.value)} required />
            </div>
            <div className="em-field">
              <label className="em-label">Allowed Duration (minutes)</label>
              <input className="em-input" type="text" inputMode="numeric" value={duration}
                placeholder="15"
                onChange={(e) => setDuration(e.target.value.replace(/\D/g, '').slice(0, 3))}
                onBlur={() => setDuration(String(durationNum))} />
              <span style={{ fontSize: 11, color: '#64839C', marginTop: 4, display: 'block' }}>
                Defaults to 15 minutes. Guards can extend an active pass by +30 min anytime.
              </span>
            </div>
          </div>
          <div className="em-modal-foot">
            <button type="button" className="em-btn em-btn-secondary" onClick={onClose}>Cancel</button>
            <button type="submit" className="em-btn em-btn-primary" disabled={loading}>
              {loading ? <><div className="em-spinner" /> Creating…</> : 'Create & Print Slip'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ─── OverrideModal ─────────────────────────────────────────────────────────────
function OverrideModal({ plate, onClose, onOverridden }) {
  const [reason, setReason]   = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    const problems = [...fieldProblems(e.currentTarget)]
    if (!reason.trim()) problems.push('Give a reason for the override.')
    if (await notify.validation(problems, { title: 'Override not logged' })) return
    setLoading(true)
    try {
      await overrideEntry({ plate_number: plate, reason })
      toast.success(`Entry override logged for ${plate}.`)
      onOverridden(); onClose()
    } catch (err) {
      toast.error(err?.response?.data?.error || 'Override failed.')
    } finally { setLoading(false) }
  }

  return (
    <div className="em-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="em-modal">
        <div className="em-modal-head">
          <span className="em-modal-title"><Shield size={17} /> Override Entry</span>
          <button className="em-modal-close" onClick={onClose}><X size={15} /></button>
        </div>
        <form onSubmit={handleSubmit} noValidate>
          <div className="em-modal-body">
            <div className="em-field">
              <label className="em-label">License Plate</label>
              <input className="em-input" value={plate} readOnly />
            </div>
            <div className="em-field">
              <label className="em-label">Override Reason</label>
              <textarea className="em-textarea" placeholder="e.g. Event day — general admission…"
                value={reason} onChange={(e) => setReason(e.target.value)} rows={3} required />
            </div>
            <p style={{ margin: 0, fontSize: 12, color: '#8A6B00', background: '#FEF9E4', border: '1px solid #F7E08A', borderRadius: 6, padding: '6px 10px' }}>
              This override will be logged in the audit trail.
            </p>
          </div>
          <div className="em-modal-foot">
            <button type="button" className="em-btn em-btn-secondary" onClick={onClose}>Cancel</button>
            <button type="submit" className="em-btn em-btn-primary" disabled={loading} style={{ background: '#8A6B00', borderColor: '#8A6B00' }}>
              {loading ? <><div className="em-spinner" /> Overriding…</> : 'Confirm Override'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ─── DenyEntryModal ────────────────────────────────────────────────────────────
function DenyEntryModal({ plate, onClose, onDenied }) {
  const [reason, setReason]   = useState('')
  const [loading, setLoading] = useState(false)

  const handleConfirm = async () => {
    setLoading(true)
    try {
      await denyEntry({ plate_number: plate, reason })
      toast.success(`Entry denied for ${plate}.`)
      onDenied?.(); onClose()
    } catch (err) {
      toast.error(err?.response?.data?.error || 'Failed to record the denial.')
    } finally { setLoading(false) }
  }

  return (
    <div className="em-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="em-modal">
        <div className="em-modal-head">
          <span className="em-modal-title"><Ban size={17} style={{ color: '#C62828' }} /> Deny Entry</span>
          <button className="em-modal-close" onClick={onClose}><X size={15} /></button>
        </div>
        <div className="em-modal-body">
          <p style={{ margin: '0 0 10px', fontSize: 13, color: '#0B2340' }}>
            Deny entry for <strong>{plate}</strong>? The vehicle will be turned away
            and the denial logged at this gate.
          </p>
          <div className="em-field">
            <label className="em-label">Reason (optional)</label>
            <textarea className="em-textarea" placeholder="e.g. No valid purpose of visit…"
              value={reason} onChange={(e) => setReason(e.target.value)} rows={3} />
          </div>
          <p style={{ margin: 0, fontSize: 12, color: '#8A6B00', background: '#FEF9E4', border: '1px solid #F7E08A', borderRadius: 6, padding: '6px 10px' }}>
            This denial will be logged in the audit trail. No violation is issued.
          </p>
        </div>
        <div className="em-modal-foot">
          <button type="button" className="em-btn em-btn-secondary" onClick={onClose}>Cancel</button>
          <button type="button" className="em-btn em-btn-primary" disabled={loading}
            style={{ background: '#C62828', borderColor: '#C62828' }}
            onClick={handleConfirm}>
            {loading ? <><div className="em-spinner" /> Denying…</> : 'Confirm Denial'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ─── ResultModal ───────────────────────────────────────────────────────────────
/**
 * The answer to a plate lookup — typed, scanned from a QR, or read off a camera.
 *
 * This used to be a card in the right-hand column that faded itself out after
 * the dedup window. A guard who looks down at a plate for two seconds came back
 * to an empty panel with no way to re-read who the car belonged to, which is
 * the same reason nothing else in this system is transient any more (see
 * components/Feedback/notify.js). So the lookup is a dialog now: it stays until
 * it is acknowledged, and the next one in the queue takes its place.
 */
// ─── Owner Lookup Modal ────────────────────────────────────────────────────────
// Shown when the guard searched by NAME and more than nothing came back. The
// guard picks the vehicle they are looking at; picking runs the ordinary plate
// check on it, so searching by name never skips an entry rule.
function OwnerLookupModal({ data, onPick, onClose }) {
  const results = data?.results ?? []
  return (
    <div className="em-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="em-modal">
        <div className="em-modal-head">
          <span className="em-modal-title"><Users size={17} /> Vehicles matching “{data?.query}”</span>
          <button className="em-modal-close" onClick={onClose}><X size={15} /></button>
        </div>
        <div className="em-modal-body">
          <p style={{ margin: '0 0 10px', fontSize: 12, color: '#64839C' }}>
            Pick the vehicle at the barrier. The usual entry check runs on it —
            choosing from this list does not grant entry by itself.
          </p>
          <div className="em-lookup-list">
            {results.map(m => {
              const cm = getClassMeta(m.classification)
              return (
                <button
                  type="button"
                  key={m.vehicle_id}
                  className="em-lookup-row"
                  onClick={() => onPick(m)}
                >
                  <div className="em-lookup-main">
                    <span className="em-lookup-plate">{m.identifier || 'No plate on file'}</span>
                    <span className={`em-class-tag ${cm.cls}`}>{cm.label}</span>
                    {m.is_inside && (
                      <span className="em-class-tag cls-unknown">
                        <LogOut size={9} style={{ verticalAlign: -1 }} /> Inside — next check logs the exit
                      </span>
                    )}
                  </div>
                  <div className="em-lookup-sub">
                    {m.owner_name || 'No owner on file'}
                    {(m.color || m.vehicle_type || m.model) && (
                      <> · {[m.color, m.vehicle_type, m.model].filter(Boolean).join(' ')}</>
                    )}
                  </div>
                </button>
              )
            })}
          </div>
          {data?.truncated && (
            <p style={{ margin: '10px 0 0', fontSize: 11, color: '#8A6B00' }}>
              Showing the first {results.length}. Type more of the name to narrow it down.
            </p>
          )}
        </div>
        <div className="em-modal-foot">
          <button type="button" className="em-btn em-btn-secondary" onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  )
}


// ─── Unrecognized Vehicle Modal ────────────────────────────────────────────────
// A vehicle with no plate and no conduction sticker still drives onto campus.
// Before this it left a bare "unreadable" row with nothing on it — no
// description, no driver, no way to log the exit. The guard writes down what
// they can see and the entry behaves like any other from then on.
function UnrecognizedVehicleModal({ onClose, onRecorded, gateId }) {
  const [form, setForm] = useState({
    driver_name: '', entrant_category: '', vehicle_type: '',
    vehicle_color: '', vehicle_model: '', entry_note: '',
  })
  const [loading, setLoading] = useState(false)
  const set = (k) => (e) => setForm(p => ({ ...p, [k]: e.target.value }))

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (await notify.validation(fieldProblems(e.currentTarget))) return
    setLoading(true)
    try {
      const { data } = await recordUnrecognizedEntry({ ...form, gate_id: gateId })
      await notify.success(
        `Recorded as ${data.reference} — ${form.vehicle_color} ${form.vehicle_type} driven by ${form.driver_name}. ` +
        'It is now counted as inside; log the exit from the Unrecognized Vehicles panel when it leaves.',
        { title: 'Vehicle recorded' },
      )
      onRecorded(data)
      onClose()
    } catch (err) {
      const body = err?.response?.data
      const msg = body && typeof body === 'object'
        ? Object.values(body).flat().join(' ')
        : 'Failed to record the vehicle.'
      toast.error(msg)
    } finally { setLoading(false) }
  }

  return (
    <div className="em-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="em-modal">
        <div className="em-modal-head">
          <span className="em-modal-title"><FileQuestion size={17} /> Record Unrecognized Vehicle</span>
          <button className="em-modal-close" onClick={onClose}><X size={15} /></button>
        </div>
        <form onSubmit={handleSubmit} noValidate>
          <div className="em-modal-body">
            <p className="em-modal-hint">
              For vehicles with no plate and no conduction number. Fill in what you
              can see — this description stands in for the plate on the log.
            </p>

            <div className="em-field">
              <label className="em-label">Driver's Name</label>
              <input className="em-input" value={form.driver_name} onChange={set('driver_name')}
                     placeholder="e.g. Juan Dela Cruz" required />
            </div>

            <div className="em-field">
              <label className="em-label">Who is entering?</label>
              <select className="em-select" value={form.entrant_category}
                      onChange={set('entrant_category')} required>
                <option value="">Select…</option>
                {MANUAL_CATEGORIES.map(c => (
                  <option key={c} value={c}>{CLASSIFICATION_META[c].label}</option>
                ))}
              </select>
            </div>

            <div className="em-field-row">
              <div className="em-field">
                <label className="em-label">Vehicle Type</label>
                <select className="em-select" value={form.vehicle_type}
                        onChange={set('vehicle_type')} required>
                  <option value="">Select…</option>
                  {VEHICLE_TYPES.map(t => (
                    <option key={t.value} value={t.value}>{t.label}</option>
                  ))}
                </select>
              </div>
              <div className="em-field">
                <label className="em-label">Colour</label>
                <input className="em-input" value={form.vehicle_color} onChange={set('vehicle_color')}
                       placeholder="e.g. Red" required />
              </div>
            </div>

            <div className="em-field">
              <label className="em-label">
                Make / Model <span style={{ color: '#64839C', fontWeight: 400 }}>(optional)</span>
              </label>
              <input className="em-input" value={form.vehicle_model} onChange={set('vehicle_model')}
                     placeholder="e.g. Toyota Vios" />
            </div>

            <div className="em-field">
              <label className="em-label">
                Note <span style={{ color: '#64839C', fontWeight: 400 }}>(optional)</span>
              </label>
              <textarea className="em-textarea" value={form.entry_note} onChange={set('entry_note')}
                        placeholder="e.g. Newly delivered unit, plate not yet issued" />
            </div>
          </div>
          <div className="em-modal-foot">
            <button type="button" className="em-btn em-btn-secondary" onClick={onClose}>Cancel</button>
            <button type="submit" className="em-btn em-btn-primary" disabled={loading}>
              {loading ? <><div className="em-spinner" /> Recording…</> : <>Record Entry</>}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}


function ResultModal({ result, offices, onPassCreated, onOverride, onDeny, guardName, onDismiss, queued = 0 }) {
  const [showVisitor,  setShowVisitor]  = useState(false)
  const [showOverride, setShowOverride] = useState(false)
  const [showDeny,     setShowDeny]     = useState(false)

  // Visitor pass / override / deny open on top of this dialog. While one of
  // them is up it owns the keyboard and the backdrop — dismissing the result
  // underneath would tear the form off the screen mid-entry.
  const nestedOpen = showVisitor || showOverride || showDeny

  // Capture phase with stopImmediatePropagation, like FeedbackHost: the page
  // underneath (fullscreen viewport, QR scanner) listens for Escape too, and
  // one press must close the dialog only. A notify dialog raised on top of
  // this one registers its own capture listener first and wins, so an error
  // message never dismisses the result it is reporting on.
  useEffect(() => {
    if (nestedOpen) return
    const onKey = (e) => {
      if (e.key !== 'Escape') return
      e.preventDefault()
      e.stopImmediatePropagation()
      onDismiss?.()
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [nestedOpen, onDismiss])

  const { Icon, label, cls } = getMeta(result.status)
  const owner     = result.vehicle?.user
  const vehicle   = result.vehicle
  const isVisitor = result.status === 'unknown' || result.status === 'no_pass'
  const isDeniable = ['denied', 'wrong_day', 'disabled'].includes(result.status)
  const todayName = new Date().toLocaleDateString('en-US', { weekday: 'long' })

  return (
    <>
      <div
        className="em-overlay"
        onClick={(e) => e.target === e.currentTarget && onDismiss?.()}
      >
        <div className={`em-card em-result em-result-dialog ${cls}`} role="dialog" aria-modal="true"
          aria-label={`${label} — ${result.plate_number || 'unknown plate'}`}>
          <div className={`em-result-banner ${cls}`} style={{ position: 'relative' }}>
            <button
              type="button"
              className="em-modal-close"
              style={{ position: 'absolute', top: 8, right: 8 }}
              onClick={() => onDismiss?.()}
              title="Dismiss"
              aria-label="Dismiss"
            >
              <X size={15} />
            </button>
            <div className="em-result-icon"><Icon size={20} /></div>
            <div className="em-result-text">
              <p className="em-result-status">{label}</p>
              <p className="em-result-plate">{result.plate_number || '—'}</p>
              {/* Who this is. On the banner rather than down in the rows
                  because it is what decides how the guard handles the car,
                  and the rows below are hidden entirely for a visitor. */}
              <span className={`em-class-tag ${getClassMeta(resultClassification(result)).cls}`}>
                {getClassMeta(resultClassification(result)).label}
              </span>
            </div>
          </div>
          <div className="em-result-body">
            <p className="em-result-msg">{result.message}</p>
            {result.constraint && (
              <div className="em-constraint-info">
                <AlertTriangle size={13} style={{ flexShrink: 0 }} />
                <span>Rule: <strong>{result.constraint}</strong></span>
              </div>
            )}
            {!isVisitor && owner && (
              <div className="em-result-rows">
                {owner.full_name && (
                  <div className="em-result-row">
                    <span className="em-result-row-label">Owner</span>
                    <span className="em-result-row-value">{owner.full_name}</span>
                  </div>
                )}
                {owner.owner_type && (
                  <div className="em-result-row">
                    <span className="em-result-row-label">Type</span>
                    <span className="em-result-row-value" style={{ textTransform: 'capitalize' }}>
                      {owner.owner_type.replace('_', ' ')}
                    </span>
                  </div>
                )}
                {vehicle && (vehicle.vehicle_type || vehicle.color) && (
                  <div className="em-result-row">
                    <span className="em-result-row-label">Vehicle</span>
                    <span className="em-result-row-value">
                      {[vehicle.vehicle_type, vehicle.color].filter(Boolean).map(s => s.charAt(0).toUpperCase() + s.slice(1)).join(' · ')}
                    </span>
                  </div>
                )}
                {result.has_violations && (
                  <div className="em-result-row">
                    <span className="em-result-row-label">Violations</span>
                    <span className="em-violation-pill"><AlertTriangle size={10} /> Unresolved violations</span>
                  </div>
                )}
                {result.already_inside && (
                  <div className="em-result-row">
                    <span className="em-result-row-label">Warning</span>
                    <span className="em-violation-pill" style={{ background: '#FDF0BE', border: '1px solid #F7E08A', color: '#7A5C00' }}>
                      <AlertTriangle size={10} /> Already inside — no exit logged
                    </span>
                  </div>
                )}
                {result.organizer_event && (
                  <div className="em-result-row">
                    <span className="em-result-row-label">Organizer</span>
                    <span className="em-violation-pill" style={{ background: '#EAF2F8', border: '1px solid #BDD4E5', color: '#084A85' }}>
                      <Star size={10} /> {result.organizer_event.name}
                    </span>
                  </div>
                )}
              </div>
            )}
            {isVisitor && (owner?.full_name || result.organizer_event) && (
              <div className="em-result-rows">
                {owner?.full_name && (
                  <div className="em-result-row">
                    <span className="em-result-row-label">Owner</span>
                    <span className="em-result-row-value">{owner.full_name}</span>
                  </div>
                )}
                {result.organizer_event && (
                  <div className="em-result-row">
                    <span className="em-result-row-label">Organizer</span>
                    <span className="em-violation-pill" style={{ background: '#EAF2F8', border: '1px solid #BDD4E5', color: '#084A85' }}>
                      <Star size={10} /> {result.organizer_event.name}
                    </span>
                  </div>
                )}
              </div>
            )}
            <div style={{ display: 'flex', gap: 6, marginTop: 8, flexDirection: 'column' }}>
              {isVisitor && (
                <button className="em-btn em-btn-secondary" style={{ width: '100%' }} onClick={() => setShowVisitor(true)}>
                  <UserPlus size={14} /> Create Visitor Pass
                </button>
              )}
              {isVisitor && (
                <button className="em-btn" style={{ width: '100%', background: '#C62828', color: '#fff', border: 'none', justifyContent: 'center' }}
                  onClick={() => setShowDeny(true)}>
                  <Ban size={14} /> Deny Entry
                </button>
              )}
              {isDeniable && (
                <button className="em-btn" style={{ width: '100%', background: '#8A6B00', color: '#fff', border: 'none', justifyContent: 'center' }}
                  onClick={() => setShowOverride(true)}>
                  <Shield size={14} /> Override Entry
                </button>
              )}
              {/* The dialog is the acknowledgement — nothing clears it on a timer.
                  Saying how many are behind it stops a queue of scans reading as
                  one dialog that will not close. */}
              <button
                className="em-btn em-btn-primary"
                style={{ width: '100%', justifyContent: 'center', marginTop: 2 }}
                onClick={() => onDismiss?.()}
                autoFocus
              >
                <CheckCircle size={14} />
                {queued > 0 ? `Acknowledge — ${queued} more waiting` : 'Acknowledge'}
              </button>
            </div>
          </div>
        </div>
      </div>

      {showVisitor && (
        <VisitorPassModal plate={result.plate_number} offices={offices}
          onClose={() => setShowVisitor(false)} onCreated={onPassCreated} guardName={guardName} />
      )}
      {showOverride && (
        <OverrideModal plate={result.plate_number}
          onClose={() => setShowOverride(false)}
          onOverridden={() => onOverride?.()} />
      )}
      {showDeny && (
        <DenyEntryModal plate={result.plate_number}
          onClose={() => setShowDeny(false)}
          onDenied={() => { onDeny?.(); onDismiss?.() }} />
      )}
    </>
  )
}

// ─── Main Page ─────────────────────────────────────────────────────────────────
export default function SecurityEntryManagement() {
  const { user } = useAuthStore()
  const { gateLabel: labelFor } = useGates()
  const gateLabel = labelFor(user?.gate_assignment) || 'Main Gate'

  const [plateInput, setPlateInput]   = useState('')
  const [loading, setLoading]         = useState(false)
  const [scanQueue, setScanQueue]     = useState([]) // [{id, result}] — head is on screen
  const [logs, setLogs]               = useState([])
  const [offices, setOffices]         = useState([])
  const [passes, setPasses]           = useState(loadCachedPasses) // today's ACTIVE visitor passes (hydrated from cache)
  const overstayToasted = useRef(new Set()) // pass ids already alerted for overstay
  const [dedupSeconds, setDedupSeconds] = useState(5)
  const [openCampus, setOpenCampus]     = useState(false)
  const [showExitScanner, setShowExitScanner] = useState(false) // camera exit-QR scanner
  const [exitScanBusy, setExitScanBusy]       = useState(false)
  // Results queue in arrival order and are shown one at a time — the head is
  // the dialog on screen, acknowledging it brings up the next. Oldest first,
  // because at a lane the car still at the barrier is the one scanned first.
  //
  // The cap is what stops a camera that keeps re-reading the same lorry from
  // handing the guard an unbounded stack to click through; the plate/status
  // dedup in the scan effect below keeps it from filling in the first place.
  const addToQueue = (r) => {
    const id = Date.now() + Math.random()
    setScanQueue(prev => [...prev, { id, result: r }].slice(-4))
  }

  const removeFromQueue = (id) => setScanQueue(prev => prev.filter(e => e.id !== id))

  const { cameras, results, syncCameras, registerCanvas } = useCameraContext()
  const [rtspActiveCamId, setRtspActiveCam] = useState(null)
  const [camQuery, setCamQuery] = useState('')
  const rtspCameras = cameras.filter(c => c.assignment === 'entry')
  const rtspActiveCam = rtspCameras.find(c => c.id === rtspActiveCamId) ?? rtspCameras[0] ?? null
  const fs = useFullscreen()

  // Only the camera picker is filtered. Every camera's canvas stays mounted
  // in the stage above — hidden, but registered with the stream context —
  // so narrowing this list must never remove one, or searching would tear down
  // a live feed and force a reconnect.
  //
  // The search box only exists once there are enough cameras to need it; a
  // query typed back then must not keep filtering a list with no box to clear.
  const camSearchable = rtspCameras.length > CAM_SEARCH_MIN
  const camQ = camSearchable ? camQuery.trim().toLowerCase() : ''
  const shownCams = camQ
    ? rtspCameras.filter(c => String(c.name ?? '').toLowerCase().includes(camQ))
    : rtspCameras
  const rtspResults = results.filter(r => rtspCameras.some(c => c.id === r._camId))

  useEffect(() => {
    if (!rtspActiveCamId && rtspCameras.length > 0) setRtspActiveCam(rtspCameras[0].id)
  }) // intentionally no deps — runs after every render until activeCamId is set

  // Name-search matches awaiting a pick, and the plateless vehicles recorded
  // by hand that are still inside.
  const [ownerMatches, setOwnerMatches] = useState(null)
  const [showUnrecognized, setShowUnrecognized] = useState(false)
  const [unrecognized, setUnrecognized] = useState([])

  const scanCooldown = useRef(new Map()) // plate → { status, timeoutId }
  const processedRids = useRef(new Set()) // result _rid values already handled

  // Auto-process ML scan results from camera
  useEffect(() => {
    if (!rtspResults?.length) return
    rtspResults.forEach(r => {
      if (!r.plate_number) return
      if (r.status === 'duplicate') return
      // Each delivered result is handled exactly once — results linger in context
      // state, and this effect re-runs on every render, so without this guard the
      // same scan would re-spam the queue/toasts/log every time the cooldown lapses
      if (r._rid) {
        if (processedRids.current.has(r._rid)) return
        processedRids.current.add(r._rid)
        if (processedRids.current.size > 500) processedRids.current.clear()
      }
      if (r._at && Date.now() - r._at > 30000) return // stale result from before this page mounted
      const existing = scanCooldown.current.get(r.plate_number)
      // Skip only when the same status repeats within the dedup window
      if (existing && existing.status === r.status) return
      // Different status (entry→exit or exit→entry): reset the timer and log it
      if (existing) clearTimeout(existing.timeoutId)
      const timeoutId = setTimeout(() => scanCooldown.current.delete(r.plate_number), dedupSeconds * 1000)
      scanCooldown.current.set(r.plate_number, { status: r.status, timeoutId })
      addToQueue(r)
      // Previously-scanned re-checks are informational — card only, kept out of recent scans
      if (r.status !== 'already_inside') {
        setLogs(prev => [{
          id: Date.now() + Math.random(),
          plate_number: r.plate_number,
          status: r.status,
          // So the row carries its category from the moment it appears; the
          // refresh below replaces it with the server's own a beat later.
          classification: resultClassification(r),
          scanned_at: new Date().toISOString(),
          scanned_by_name: user?.full_name,
          gate_id: user?.gate_assignment,
        }, ...prev].slice(0, 20))
      }
    })
  }, [rtspResults]) // eslint-disable-line react-hooks/exhaustive-deps

  const gateId = user?.gate_assignment
  const gateFilter = gateId ? { gate_id: gateId } : {}

  // Which way the lookup box will send what has been typed. Derived rather than
  // decided on submit so the field, its button and its hint all change as the
  // guard types — they can see it is about to search a name before pressing
  // anything, instead of finding out from the result.
  const isNameQuery = (() => {
    const typed = plateInput.trim()
    return typed !== '' && !/^SLC/i.test(typed) && !looksLikeIdentifier(typed)
  })()

  useEffect(() => {
    getAccessLogs({ limit: 20, ...gateFilter }).then(r => setLogs(r.data?.results ?? r.data ?? [])).catch(() => {})
    getOffices().then(r => setOffices(r.data?.results ?? r.data ?? [])).catch(() => {})
    getSystemSettings()
      .then(({ data }) => {
        if (data?.scan_dedup_seconds) setDedupSeconds(data.scan_dedup_seconds)
        setOpenCampus(!!data?.open_campus_mode)
      })
      .catch(() => {})
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Only drive the camera(s) for this guard's assigned gate — otherwise a Gate 4
  // guard would scan through the Gate 1 camera and every scan would be tagged
  // gate1, never showing up in the Gate 4 log.
  //
  // Re-run on every camera change rather than once on mount. The roster is not
  // fixed for the length of a shift: cameras are added, deleted and reassigned
  // in Device Management while the guard is on this screen, and a feed that has
  // been deleted must stop playing here instead of streaming on until someone
  // thinks to reload.
  const loadCameras = useCallback(() => {
    camerasApi.list({ assignment: 'entry', ...(gateId ? { gate_id: gateId } : {}) })
      .then(cams => syncCameras('entry', cams, { detect: true }))
      .catch(() => {})
  }, [gateId, syncCameras])

  useEffect(() => { loadCameras() }, [loadCameras])
  useLiveUpdates(loadCameras, 'camera')

  // Follow Open Campus Mode toggles live so the banner appears/disappears
  // without the guard having to reload the page.
  useLiveUpdates(() => {
    getSystemSettings()
      .then(({ data }) => setOpenCampus(!!data?.open_campus_mode))
      .catch(() => {})
  }, 'systemsettings')

  const refreshLogs = () =>
    getAccessLogs({ limit: 20, ...gateFilter }).then(r => setLogs(r.data?.results ?? r.data ?? [])).catch(() => {})

  // Active visitor passes — alert once per pass when it crosses into overstay
  const refreshPasses = () =>
    getVisitorPasses().then(r => {
      const list = (r.data?.results ?? r.data ?? []).filter(p => p.status === 'active')
      setPasses(list)
      saveCachedPasses(list)
      list.forEach(p => {
        if (p.expires_at && new Date(p.expires_at).getTime() < Date.now()
            && !overstayToasted.current.has(p.id)) {
          overstayToasted.current.add(p.id)
          toast.warning(`Visitor overstay: ${p.plate_number} exceeded the allowed ${p.allowed_duration} min.`, { duration: 8000 })
        }
      })
    }).catch(() => {})

  const refreshUnrecognized = () =>
    getUnrecognizedInside(gateId).then(r => setUnrecognized(r.data ?? [])).catch(() => {})

  const refreshAll = () => { refreshLogs(); refreshPasses(); refreshUnrecognized() }

  // Instant refresh on new gate scans / visitor-pass changes
  useLiveUpdates(refreshAll)

  useEffect(() => {
    refreshPasses()
    refreshUnrecognized()
    const t = setInterval(refreshPasses, 30000)
    return () => clearInterval(t)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const handleExtendPass = (p) => {
    extendVisitorPass(p.id, 30)
      .then(() => {
        toast.success(`Pass extended +30 min for ${p.plate_number}.`)
        overstayToasted.current.delete(p.id) // re-alert if it overstays again
        refreshPasses()
      })
      .catch(err => toast.error(err?.response?.data?.error || 'Failed to extend pass.'))
  }

  // Record a visitor exit from a slip QR (payload SLC-VISITOR:{id}) — shared by
  // the lookup box (USB scanner-gun / typed) and the camera scanner. Drives the
  // same on-screen "Exited" feedback as a plate-based exit (result card, green
  // banner, and a Recent Scans entry).
  const recordVisitorExit = async (qrData) => {
    try {
      const res = await visitorQrExit(qrData)
      const d = res.data
      const dur = d.duration_minutes

      // Raises the same "Exited" dialog a plate exit does
      addToQueue({
        plate_number: d.plate_number,
        status: 'exited',
        message: dur != null
          ? `Visitor exited — inside for ${dur} min${d.overstay_minutes > 0 ? `, overstayed ${d.overstay_minutes} min` : ''}.`
          : 'Visitor exited.',
      })
      setLogs(prev => [{
        id: Date.now() + Math.random(),
        plate_number: d.plate_number,
        status: 'exited',
        scanned_at: new Date().toISOString(),
        scanned_by_name: user?.full_name,
        gate_id: user?.gate_assignment,
      }, ...prev].slice(0, 20))

      refreshAll()
      return true
    } catch (err) {
      toast.error(err?.response?.data?.error || 'Failed to record visitor exit.')
      return false
    }
  }

  // Run the normal plate entry/exit check. Rules are applied server-side by
  // check_entry(): the first scan logs an entry, a re-scan while the vehicle is
  // inside logs the exit. Returns the response data so callers can react.
  const runPlateCheck = async (plate) => {
    // Conduction numbers get through too. ManualEntryView has accepted them
    // since it was written — it resolves the identifier before it validates the
    // format — but this check ran first and answered "invalid plate" without
    // ever asking the server, so a brand-new car on a conduction sticker could
    // not be looked up by hand at all.
    if (!isValidPlateNumber(plate) && !isValidConductionNumber(plate)) {
      toast.error('Enter a Philippine plate (e.g. AAA 0000) or a conduction number.',
                  { title: 'Nothing to look up' })
      return null
    }
    setLoading(true)
    try {
      const res = await manualEntry({ plate_number: plate })
      // Cooldown/window responses report their true remaining time — the card's
      // ring counts down from that instead of restarting at the full duration
      // The dialog raised here IS the acknowledgement — it carries the status,
      // the plate, the server's message and the owner, so a second bare
      // "Entry approved" alert on top of it would only be one more thing to
      // click past. Failures that never reach a dialog still alert below.
      addToQueue(res.data)
      // The check action doubles as the exit action once a vehicle is inside
      if (res.data.status === 'exited') refreshAll()
      // Previously-scanned re-checks are informational — card only, kept out of recent scans
      if (res.data.status !== 'already_inside') {
        setLogs(prev => [{
          id: Date.now(), plate_number: plate, status: res.data.status,
          classification: resultClassification(res.data),
          scanned_at: new Date().toISOString(), scanned_by_name: user?.full_name,
          gate_id: user?.gate_assignment,
        }, ...prev].slice(0, 20))
        // Reconcile Recent Scans with the server so the manual entry is backed by
        // the persisted AccessLog row, not just the optimistic placeholder.
        refreshLogs()
      }
      return res.data
    } catch (err) {
      toast.error(err?.response?.data?.error || 'Lookup failed.')
      return null
    } finally { setLoading(false) }
  }

  // Search by the owner's NAME. Returns candidates for the guard to pick from
  // rather than acting on the first hit: two people share a surname more often
  // than not, and admitting the wrong car is not a recoverable mistake.
  const runNameLookup = async (query) => {
    setLoading(true)
    try {
      const { data } = await lookupOwner(query)
      if (!data.results?.length) {
        await notify.error(
          `No vehicle is registered under a name matching “${query}”. Check the spelling, ` +
          'or use “No Plate?” if the vehicle has no plate at all.',
          { title: 'No match found' },
        )
        return
      }
      setOwnerMatches(data)
    } catch (err) {
      toast.error(err?.response?.data?.error || 'Lookup failed.')
    } finally { setLoading(false) }
  }

  // A picked match runs the ordinary plate check, so every rule still applies.
  const handlePickOwner = async (match) => {
    setOwnerMatches(null)
    if (!match.identifier) {
      await notify.error(
        'That vehicle has no plate or conduction number on file, so there is nothing ' +
        'to check it by. Record it with “No Plate?” instead.',
        { title: 'Nothing to check' },
      )
      return
    }
    setPlateInput(match.identifier)
    await runPlateCheck(match.identifier)
  }

  const handleUnrecognizedExit = async (row) => {
    try {
      const { data } = await recordUnrecognizedExit(row.id, gateId)
      await notify.success(
        `${data.reference} — ${data.driver_name} logged out after ${data.duration_minutes} min inside.`,
        { title: 'Exit recorded' },
      )
      refreshAll()
    } catch (err) {
      toast.error(err?.response?.data?.error || 'Failed to record the exit.')
    }
  }

  // Registered vehicle QR pass payload is "VEHICLE:{plate}|ID:{regId}".
  // Returns the plate, or '' if the string isn't that format.
  const plateFromVehicleQr = (raw) => {
    const s = (raw || '').trim().toUpperCase()
    if (!s.startsWith('VEHICLE:')) return ''
    return s.slice('VEHICLE:'.length).split('|')[0].trim()
  }

  // Camera scanner read a QR. Route by payload type:
  //  • SLC-VISITOR:{id}       → visitor slip exit
  //  • VEHICLE:{plate}|ID:{n} → registered vehicle entry / exit (rules applied)
  const handleQrDetected = async (data) => {
    const upper = (data || '').trim().toUpperCase()

    if (upper.startsWith('SLC-VISITOR:')) {
      setExitScanBusy(true)
      const ok = await recordVisitorExit(upper)
      setExitScanBusy(false)
      if (ok) setShowExitScanner(false)
      return
    }

    const plate = plateFromVehicleQr(upper)
    if (plate) {
      setExitScanBusy(true)
      const res = await runPlateCheck(plate)
      setExitScanBusy(false)
      if (res) setShowExitScanner(false)
      return
    }

    toast.error('Unrecognized QR. Scan a vehicle QR pass or a visitor slip QR.')
  }

  const handleCheckEntry = async (e) => {
    e?.preventDefault()
    const typed = plateInput.trim()
    const raw = typed.toUpperCase()
    if (!raw) {
      await notify.error(
        "Enter the owner's name, a plate, or a conduction number to look up.",
        { title: 'Nothing to check' },
      )
      return
    }

    // Visitor slip QR scanned into the lookup box (USB scanner or typed):
    // records the visitor's exit — visitor exits are QR-only, never by plate.
    if (raw.startsWith('SLC-VISITOR:')) {
      setLoading(true)
      await recordVisitorExit(raw)
      setPlateInput('')
      setLoading(false)
      return
    }

    // Accept a pasted vehicle QR pass string; otherwise treat the input as a plate.
    const qrPlate = plateFromVehicleQr(raw)
    if (!qrPlate && !looksLikeIdentifier(typed)) {
      // No digits anywhere — this is a person's name, not a plate.
      await runNameLookup(typed)
      return
    }

    const res = await runPlateCheck(qrPlate || raw)
    if (res?.status === 'exited') setPlateInput('')
  }

  // The picture's state, in the same words every camera screen uses.
  const activeState = feedState(rtspActiveCam)
  const liveCount   = rtspCameras.filter(c => c.streamConnected).length
  const headState   = rtspCameras.length === 0 ? 'none'
    : liveCount > 0 ? 'live'
    : activeState
  const headLabel   = headState === 'live'
    ? `${liveCount}/${rtspCameras.length} live`
    : headState === 'none' ? 'No cameras' : FEED_LABEL[headState]
  // A backend message worth passing on ("attempt 2/6", "Reconnecting in 8s…"),
  // as opposed to the bare "Connecting…" the title already says.
  const activeMsg = (rtspActiveCam?.statusMsg || '').trim()
  const activeDetail = activeMsg && !/^connecting…?$/i.test(activeMsg) ? activeMsg : ''
  const mlStage = rtspActiveCam?.mlStatus?.stage
  const mlMessage = rtspActiveCam?.mlStatus?.message
  // Every row carried "On duty: <name>", and on a guard's own terminal that
  // name is theirs — twenty copies of it pushed the owner's name out of view.
  // Only a name that is not the person reading is news.
  const me = user?.full_name || ''

  return (
    <>
      <div className="cm-page">

        <div className="cm-layout">

          {/* Left: the gate camera, then the manual way in under it. */}
          <section className="cm-card">
            <div className="cm-head">
              <span className="cm-title"><Video size={15} /> CCTV Monitor</span>
              <span className="cm-head-note">{gateLabel}</span>
              <div className="cm-head-end">
                <span className={`cm-pill ${headState}`}>
                  <span className="cm-pill-dot" /> {headLabel}
                </span>
              </div>
            </div>

            {camSearchable && (
              <div className="cm-toolbar">
                <span className="cm-toolbar-label"><Video size={12} /> {rtspCameras.length} cameras</span>
                <div className="cm-search">
                  <Search size={13} />
                  <input
                    type="search"
                    placeholder="Search cameras…"
                    value={camQuery}
                    onChange={e => setCamQuery(e.target.value)}
                    aria-label="Search cameras"
                  />
                  {camQ && (
                    <button
                      type="button"
                      className="cm-search-clear"
                      onClick={() => setCamQuery('')}
                      title="Clear search"
                      aria-label="Clear search"
                    >
                      <X size={12} />
                    </button>
                  )}
                </div>
              </div>
            )}

            {/* The standard stage — same size as the parking screen and the
                Operations Center (styles/camera-monitor.css). */}
            <div className="cm-well" ref={fs.setRef('cctv')}>
              <div className="cm-stage">
                {rtspCameras.map(cam => (
                  <canvas
                    key={cam.id}
                    className="cm-canvas"
                    hidden={rtspActiveCam?.id !== cam.id}
                    ref={el => registerCanvas(cam.id, el)}
                  />
                ))}

                {rtspCameras.length === 0 ? (
                  <div className="cm-state">
                    <Wifi size={30} />
                    <p className="cm-state-title">No entry cameras at this gate</p>
                    <p className="cm-state-sub">
                      Plates can still be checked by hand below. Cameras are added in Device Management.
                    </p>
                  </div>
                ) : activeState !== 'live' && (
                  <div className={`cm-state cm-state--over${activeState === 'offline' ? ' cm-state--offline' : ''}`}>
                    {activeState === 'offline' ? <VideoOff size={30} /> : <div className="cm-spinner" />}
                    <p className="cm-state-title">
                      {activeState === 'offline'
                        ? `${rtspActiveCam.name} is offline`
                        : `Connecting to ${rtspActiveCam.name}…`}
                    </p>
                    <p className="cm-state-sub">
                      {activeDetail || 'Type the plate below while the picture loads.'}
                    </p>
                  </div>
                )}

                {rtspActiveCam && (
                  <>
                    <div className="cm-tag">
                      <span className={`cm-dot ${FEED_DOT[activeState]}`} />
                      {rtspActiveCam.name}
                    </div>
                    <button
                      className="cm-fs"
                      onClick={async () => {
                        if (!(await fs.toggle('cctv'))) toast.error('Fullscreen was blocked by the browser.')
                      }}
                      title={fs.isFullscreen('cctv') ? 'Exit fullscreen' : 'Fullscreen'}
                      aria-label={fs.isFullscreen('cctv') ? 'Exit fullscreen' : 'Fullscreen'}
                    >
                      {fs.isFullscreen('cctv') ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
                    </button>
                  </>
                )}

                {/* Bottom-left: Open Campus beside the detector's own status. */}
                {rtspActiveCam && (openCampus || (mlStage && mlStage !== 'idle')) && (
                  <div className="cm-badges">
                    {openCampus && (
                      <span className="cm-badge info"><DoorOpen size={12} /> Open Campus</span>
                    )}
                    {mlStage === 'ready' ? (
                      <span className="cm-badge ok"><span className="cm-dot live" /> Detection Ready</span>
                    ) : mlStage && mlStage !== 'idle' && (
                      <span className="cm-badge"><span className="cm-spinner cm-spinner--sm" /> {mlMessage || 'Initializing…'}</span>
                    )}
                  </div>
                )}
              </div>
            </div>

            {/* Which camera is on the stage — cards, same as the Operations Center. */}
            {rtspCameras.length > 1 && (
              <div className="cm-picker" role="group" aria-label="Gate cameras">
                {shownCams.length === 0 && (
                  <p className="cm-picker-empty">No cameras match “{camQuery.trim()}”</p>
                )}
                {shownCams.map(cam => {
                  const st = feedState(cam)
                  const active = rtspActiveCam?.id === cam.id
                  return (
                    <button
                      key={`pick-${cam.id}`}
                      type="button"
                      className={`cm-pick${active ? ' active' : ''}`}
                      onClick={() => setRtspActiveCam(cam.id)}
                      aria-pressed={active}
                    >
                      <span className={`cm-dot ${FEED_DOT[st]}`} />
                      <span className="cm-pick-text">
                        <span className="cm-pick-name">{cam.name}</span>
                        <span className="cm-pick-sub">{FEED_LABEL[st]}</span>
                      </span>
                    </button>
                  )
                })}
              </div>
            )}

            {/* Combined lookup — the manual way in when the detector does not
                read a plate: type the plate or conduction number, a name, or
                scan the owner's QR pass. It must never be the thing that gets
                cut off, which is why the stage above is what shrinks. */}
            <div className="em-lookup">
              <label className="em-lookup-label" htmlFor="em-lookup-input">
                <Search size={14} /> Owner Name / Plate / Conduction No.
              </label>
              <form onSubmit={handleCheckEntry} noValidate className="em-lookup-form">
                <input
                  id="em-lookup-input"
                  className={`em-lookup-input${isNameQuery ? ' is-name' : ''}`}
                  value={plateInput}
                  onChange={e => {
                    const raw = e.target.value
                    // Visitor slip QRs (SLC-VISITOR:{id}) must not be plate-formatted,
                    // and neither must a name — plate formatting upper-cases and
                    // injects a space, which turned "dela cruz" into "DEL ACRUZ".
                    if (/^SLC/i.test(raw.trim())) setPlateInput(raw.toUpperCase())
                    else if (looksLikeIdentifier(raw)) setPlateInput(formatPlateNumber(raw))
                    else setPlateInput(raw)
                  }}
                  placeholder="Name, e.g. Juan Dela Cruz — or AAA 0000 / CS12345A678"
                  autoComplete="off"
                />
                <button
                  type="submit"
                  className="em-btn em-btn-primary em-lookup-go"
                  disabled={loading}
                >
                  {loading
                    ? <><div className="em-spinner" /> Checking…</>
                    : isNameQuery
                      ? <><Users size={15} /> Search by Name</>
                      : <><Search size={15} /> Check Plate — Entry / Exit</>}
                </button>
                <div className="em-lookup-alt">
                  <button
                    type="button"
                    className="em-btn em-btn-secondary"
                    onClick={() => setShowExitScanner(true)}
                    title="Scan a vehicle QR pass or a visitor slip QR — entry / exit"
                  >
                    <ScanLine size={15} /> Scan QR
                  </button>
                  {/* The way in for a vehicle the plate path cannot serve at all.
                      Beside the plate field rather than buried in a menu — the
                      guard needs it while the car is still at the barrier. */}
                  <button
                    type="button"
                    className="em-btn em-btn-secondary"
                    onClick={() => setShowUnrecognized(true)}
                    title="Record a vehicle that has no plate and no conduction number"
                  >
                    <FileQuestion size={15} /> No Plate?
                  </button>
                </div>
              </form>
              <p className="em-lookup-hint">
                {isNameQuery
                  ? 'Searching by name — pick the vehicle from the results, then the usual entry check runs on it.'
                  : 'Entry and exit are detected automatically — vehicles inside campus are logged out on re-check. Type a name instead to look an owner up.'}
              </p>
            </div>
          </section>

          {/* Right: what has happened at this gate, and who to watch for. The
              lookup result is a dialog (ResultModal, below), so this column is
              reading only. */}
          <aside className="cm-side">

            <section className="cm-panel">
              <div className="cm-panel-head">
                <span className="cm-panel-title"><ClipboardList size={14} /> Recent Scans</span>
                <div className="cm-panel-end">
                  <span className="cm-count">{logs.length}</span>
                  <button
                    type="button"
                    className="cm-icon-btn"
                    onClick={refreshLogs}
                    title="Refresh"
                    aria-label="Refresh recent scans"
                  >
                    <RefreshCw size={13} />
                  </button>
                </div>
              </div>
              {/* Who came through, at a glance. Counts only — this panel is
                  the last 20 scans, so a filter here would hide rows without
                  being able to say how many it hid. */}
              {logs.length > 0 && (
                <div className="em-class-filters">
                  {MANUAL_CATEGORIES.concat('supplier').map(key => {
                    const n = logs.filter(l => (l.classification || 'unknown') === key).length
                    if (!n) return null
                    const cm = getClassMeta(key)
                    return (
                      <span key={key} className={`em-class-tag ${cm.cls}`}>
                        {cm.label} <strong>{n}</strong>
                      </span>
                    )
                  })}
                </div>
              )}
              {logs.length === 0 ? (
                <p className="cm-empty">No entries recorded yet today.</p>
              ) : (
                <ul className="cm-log em-scan-log">
                  {logs.map((log, i) => {
                    const m = getMeta(log.status)
                    const owner = log.vehicle_owner_name
                      // A plateless vehicle has no owner account; the driver
                      // and the description are all it has.
                      || (log.driver_name
                        ? [log.driver_name, [log.vehicle_color, log.vehicle_type, log.vehicle_model].filter(Boolean).join(' ')]
                            .filter(Boolean).join(' · ')
                        : '')
                    const staff = [
                      log.on_duty_guard_name && log.on_duty_guard_name !== me
                        ? `On duty: ${log.on_duty_guard_name}` : '',
                      log.scanned_by_name && log.scanned_by_name !== log.on_duty_guard_name && log.scanned_by_name !== me
                        ? `By ${log.scanned_by_name}` : '',
                    ].filter(Boolean)
                    const who = [owner, ...staff].filter(Boolean).join(' · ')
                    return (
                      <li key={log.id ?? i} className="cm-log-row">
                        <span className={`em-audit-icon ${m.logCls}`}><m.Icon size={13} /></span>
                        <div className="cm-log-main">
                          <div className="cm-log-line">
                            <span className="cm-log-plate">
                              {log.plate_number || (log.is_unrecognized ? `NP-${log.id}` : '—')}
                            </span>
                            <span className="cm-log-time">{timeAgo(log.scanned_at)}</span>
                          </div>
                          <div className="cm-log-tags">
                            <span className={`em-log-badge ${m.logCls}`}>{m.label}</span>
                            {log.classification && (
                              <span className={`em-class-tag ${getClassMeta(log.classification).cls}`}>
                                {getClassMeta(log.classification).label}
                              </span>
                            )}
                            {/* One visit is one row: AccessLogListView folds an
                                exit into the entry it pairs with. Without this
                                the row keeps reading "Approved for Entry" hours
                                after the car left, and a guard looking down the
                                list cannot tell who is still inside. */}
                            {log.exited_at && (
                              <span className="em-log-badge exited em-audit-exit">
                                <LogOut size={9} />
                                Exited {timeAgo(log.exited_at)}
                                {log.duration_minutes != null && ` · ${log.duration_minutes} min`}
                              </span>
                            )}
                          </div>
                          {who && <div className="cm-log-who" title={who}>{who}</div>}
                        </div>
                      </li>
                    )
                  })}
                </ul>
              )}
            </section>

            {/* Plateless vehicles recorded by hand. There is no plate to
                re-check, so this panel is the only way their exit gets
                logged — without it they would sit in the inside-count for
                ever. Shown only when there are any. */}
            {unrecognized.length > 0 && (
              <section className="cm-panel">
                <div className="cm-panel-head">
                  <span className="cm-panel-title"><FileQuestion size={14} /> Unrecognized Vehicles Inside</span>
                  <div className="cm-panel-end"><span className="cm-count">{unrecognized.length}</span></div>
                </div>
                <div className="em-side-list">
                  {unrecognized.map(row => {
                    const cm = getClassMeta(row.classification)
                    return (
                      <div key={row.id} className="em-unrec-row">
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div className="em-unrec-top">
                            <span className="em-unrec-ref">{row.reference}</span>
                            <span className={`em-class-tag ${cm.cls}`}>{cm.label}</span>
                          </div>
                          <div className="em-unrec-sub">
                            {row.driver_name}
                            {' · '}
                            {[row.vehicle_color, row.vehicle_type, row.vehicle_model].filter(Boolean).join(' ')}
                          </div>
                          <div className="em-unrec-time">Entered {timeAgo(row.scanned_at)}</div>
                        </div>
                        <button
                          className="em-btn em-btn-secondary em-unrec-exit"
                          onClick={() => handleUnrecognizedExit(row)}
                          title="Record this vehicle's exit"
                        >
                          <LogOut size={13} /> Log Exit
                        </button>
                      </div>
                    )
                  })}
                </div>
              </section>
            )}

            {/* Active visitors — time remaining / overstay */}
            <section className="cm-panel">
              <div className="cm-panel-head">
                <span className="cm-panel-title"><Clock size={14} /> Active Visitors</span>
                <div className="cm-panel-end"><span className="cm-count">{passes.length}</span></div>
              </div>
              {passes.length === 0 ? (
                <p className="cm-empty">No visitors currently inside.</p>
              ) : (
                <div className="em-side-list">
                  {passes.map(p => {
                    const t = passTimeInfo(p)
                    return (
                      <div key={p.id} className={`em-visitor-row${t.overdue ? ' overdue' : ''}`}>
                        <div className="em-visitor-main">
                          <span className="em-visitor-plate">{p.plate_number}</span>
                          <span className="em-visitor-sub">
                            {p.office_name || 'No office'}{p.purpose ? ` · ${p.purpose}` : ''}
                          </span>
                        </div>
                        <span className={`em-visitor-time${t.overdue ? ' overdue' : t.soon ? ' soon' : ''}`}>
                          {t.overdue && <AlertTriangle size={11} />}
                          {t.label}
                        </span>
                        <button
                          type="button"
                          className="em-visitor-extend"
                          onClick={() => handleExtendPass(p)}
                          title="Extend by 30 minutes"
                        >
                          +30m
                        </button>
                      </div>
                    )
                  })}
                </div>
              )}
            </section>

            {/* Confiscated owners may not enter. The guard meeting the car at
                the barrier is the person who has to know, and a car turning up
                during the penalty is itself a further offence — so it sits in
                the column the guard is already reading, not under the fold. */}
            <ConfiscatedAccounts compact />
          </aside>
        </div>

        {scanQueue[0] && (
          <ResultModal
            key={scanQueue[0].id}
            result={scanQueue[0].result}
            offices={offices}
            onPassCreated={refreshAll}
            onOverride={refreshAll}
            onDeny={refreshAll}
            guardName={user?.full_name}
            onDismiss={() => removeFromQueue(scanQueue[0].id)}
            queued={scanQueue.length - 1}
          />
        )}

        {ownerMatches && (
          <OwnerLookupModal
            data={ownerMatches}
            onPick={handlePickOwner}
            onClose={() => setOwnerMatches(null)}
          />
        )}

        {showUnrecognized && (
          <UnrecognizedVehicleModal
            gateId={gateId}
            onClose={() => setShowUnrecognized(false)}
            onRecorded={() => refreshAll()}
          />
        )}

        {showExitScanner && (
          <QrScanModal
            title="Scan QR — Entry / Exit"
            hint="Point the camera at a vehicle QR pass (first scan = entry, next = exit) or a visitor slip QR."
            busy={exitScanBusy}
            onDetected={handleQrDetected}
            onClose={() => setShowExitScanner(false)}
          />
        )}
      </div>
    </>
  )
}
