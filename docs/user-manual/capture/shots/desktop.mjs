// Figure definitions for the Windows installer and the campus launcher.
// Rectangles come from UI Automation (installer) or were measured on the
// capture (launcher, whose WPF card borders are not automation elements).
import fs from 'node:fs'
import path from 'node:path'

const D = '../desktop'
const raw = (id) => `raw/${id}`
const els = (n) => JSON.parse(fs.readFileSync(`${D}/installer/${n}.json`, 'utf8').replace(/^\uFEFF/, ''))
const el = (n, name) => {
  const e = els(n).find((x) => (typeof name === 'string' ? x.name === name : name.test(x.name || '')) && x.w < 590)
  if (!e) throw new Error(`installer page ${n}: no element ${name}`)
  return { x: e.x, y: e.y, w: e.w, h: e.h }
}
const unionBox = (...bs) => {
  const x = Math.min(...bs.map((b) => b.x)), y = Math.min(...bs.map((b) => b.y))
  return { x, y, w: Math.max(...bs.map((b) => b.x + b.w)) - x, h: Math.max(...bs.map((b) => b.y + b.h)) - y }
}
const m = (n, label, desc, box, extra = {}) => ({ n, label, desc, box, ...extra })
const NAV = (n, nextLabel = 'Next') => [
  m(90, 'Back / ' + nextLabel + ' / Cancel', `${nextLabel} continues, Back returns to the previous page, and Cancel quits Setup without installing anything.`,
    unionBox(el(n, 'Back'), el(n, nextLabel), el(n, 'Cancel'))),
]
const renumber = (marks) => marks.map((mk, i) => ({ ...mk, n: i + 1 }))

const INST = { dir: '05-installer', section: 'Installing the Campus System' }
const DEST = 'C:\\Smart Parking and Vehicle Verification System'
const figs = []
const inst = (n, file, title, caption, marks, extra = {}) => figs.push({
  id: `${INST.dir}--${file}`, src: `${D}/installer/${n}.png`, out: `${INST.dir}/${file}.png`,
  section: INST.section, title, caption, marks: renumber(marks), display: 1.45, layout: 'side', ...extra,
})

// The first page has no Back button.
inst('01', '01-license', 'Step 1 · License Agreement',
  'Run SLC-Smart-Parking-Campus-Setup.exe on the campus computer and accept the license to continue.', [
    m(1, 'License text', 'Read the terms for installing the system.', { x: 41, y: 144, w: 516, h: 216 }),
    m(2, 'Accept', 'Select "I accept the agreement". Next stays disabled until you do.', unionBox(el('01', 'I accept the agreement'), el('01', 'I do not accept the agreement'))),
    m(3, 'Next / Cancel', 'Next continues. Cancel quits Setup without installing anything.', unionBox(el('01', 'Next'), el('01', 'Cancel'))),
  ])
inst('02', '02-destination', 'Step 2 · Choose the install folder',
  'Keep the default folder unless your IT office says otherwise.', [
    m(1, 'Install folder', `Default: ${DEST}. Keep the path short: Setup refuses folders longer than 110 characters.`, el('02', /AppData/)),
    m(2, 'Browse...', 'Pick a different folder.', el('02', 'Browse...')),
    m(3, 'Disk space', 'About 6 GB is needed once the Python environment and application code are downloaded.', el('02', /free disk space/)),
    ...NAV('02'),
  ], { patches: [{ x: 43, y: 175, w: 427, h: 19, text: DEST, bg: '#FFFFFF', color: '#FFFFFF', fontSize: 12, padLeft: 0, highlight: '#0078D4' }] })
inst('03', '03-components', 'Step 3 · Select components',
  'Choose what Setup installs. A full installation is right for a new gate computer.', [
    m(1, 'Installation type', 'Full installation selects everything. Choose Custom installation to pick items yourself.', el('03', /^Full installation/)),
    m(2, 'Component list', 'The launcher is always installed. Untick Git, Python, Node.js or FFmpeg only if your IT office already manages them. The firewall rule lets guards’ computers reach this machine.', { x: 41, y: 168, w: 516, h: 212 }),
    m(3, 'Space required', 'Disk space the current selection needs.', el('03', /Current selection requires/)),
    ...NAV('03'),
  ])
inst('04', '04-deployment-options', 'Step 4 · Deployment options',
  'Choose the network port the system is served on.', [
    m(1, 'Update branch', 'This computer follows the branch the installer was built for and updates from it. It cannot be changed here.', el('04', /^This installer tracks/)),
    m(2, 'Port to serve on', 'Guards open http://<this computer’s address>:<port>. Keep 8000 unless another program already uses it.', unionBox(el('04', 'Port to serve on:'), el('04', '8000'))),
    ...NAV('04'),
  ])
inst('05', '05-prerequisites', 'Step 5 · Prerequisites check',
  'Setup shows what the computer already has before anything is installed.', [
    m(1, 'Detected software', '[installed] means it is already present; "will install" means Setup downloads it through winget.', { x: 41, y: 144, w: 516, h: 70 }),
    m(2, 'What happens next', 'On first launch the launcher downloads the application (about 300 MB) and builds a Python environment (about 5.7 GB). This takes a while; keep the computer online.', { x: 41, y: 220, w: 516, h: 80 }),
    ...NAV('05'),
  ])
inst('06', '06-start-menu', 'Step 6 · Start Menu folder',
  'Where the launcher, repair and uninstall shortcuts are placed.', [
    m(1, 'Folder name', 'The Start Menu folder for the shortcuts. The default is fine.', unionBox(el('06', 'Smart Parking and Vehicle Verification System'), el('06', 'Browse...'))),
    m(2, 'No Start Menu folder', 'Tick to skip creating Start Menu shortcuts.', el('06', /Don't create a Start Menu folder/)),
    ...NAV('06'),
  ])
inst('07', '07-additional-tasks', 'Step 7 · Additional tasks',
  'Optional shortcuts and system changes.', [
    m(1, 'Desktop shortcut', 'Puts a launcher shortcut on the desktop (recommended).', { x: 41, y: 168, w: 260, h: 18 }),
    m(2, 'Open at startup', 'Opens the launcher automatically when the computer starts. Recommended for a dedicated gate computer.', { x: 41, y: 190, w: 280, h: 18 }),
    m(3, 'Add to PATH', 'For IT staff who run the launcher from a command prompt. Usually not needed.', { x: 41, y: 234, w: 280, h: 18 }),
    ...NAV('07'),
  ])
inst('08', '08-ready', 'Step 8 · Ready to install',
  'Check the summary, then click Install. The launcher opens when Setup finishes.', [
    m(1, 'Summary', 'Install type, destination, components and tasks. Click Back to change anything.', { x: 41, y: 144, w: 516, h: 260 }),
    ...NAV('08', 'Install'),
  ], { patches: [
    { x: 43, y: 147, w: 330, h: 15, text: 'New installation of version 1.0.0', bg: '#F0F0F0', color: '#000000', fontSize: 12, padLeft: 1 },
    { x: 60, y: 193, w: 480, h: 16, text: DEST, bg: '#F0F0F0', color: '#000000', fontSize: 12, padLeft: 2 },
  ] })

// ── launcher ──────────────────────────────────────────────────────────────
figs.push({
  id: '06-campus-launcher--01-launcher-window', src: `${D}/launcher_live.png`, out: '06-campus-launcher/01-launcher-window.png',
  section: 'Running the Campus System', title: 'The campus launcher',
  caption: 'Opened from the desktop or Start Menu shortcut. It runs the server that the gate terminals connect to, so leave this window open.',
  display: 1.0, layout: 'side',
  redact: [{ x: 436, y: 122, w: 590, h: 722, block: 10, label: 'Log details hidden in this picture' }],
  marks: renumber([
    m(1, 'Server status and address', 'RUNNING or STOPPED, and the address guards and CDSO staff open in their browsers.', { x: 30, y: 90, w: 370, h: 76 }),
    m(2, 'Health', 'Database connection, cameras online, and whether live updates are working.', { x: 34, y: 181, w: 362, h: 51 }),
    m(3, 'Start / Stop server', 'Starts or stops the system. Stopping it disconnects every gate terminal and camera.', { x: 34, y: 248, w: 362, h: 42 }),
    m(4, 'Open pages', 'Guard terminal opens the gate sign-in page, Admin login opens the CDSO login, and Copy URL copies the address.', { x: 34, y: 299, w: 362, h: 34 }),
    m(5, 'Updates', 'Checks for a newer version every few minutes. When one is found, click "Update and restart" at a quiet moment: restarting drops the camera feeds briefly.', { x: 18, y: 363, w: 394, h: 133 }),
    m(6, 'Settings', 'Port, start the server when this window opens, kiosk mode (full screen), and which page opens automatically.', { x: 30, y: 524, w: 370, h: 212 }),
    m(7, 'Save settings / Credentials', 'Save your changes. Credentials holds the shared database URL and secret key; it is only needed once per computer.', { x: 34, y: 746, w: 362, h: 34 }),
    m(8, 'Activity', 'Live messages from the server. Log files opens the saved logs; Clear empties this view. Useful when reporting a problem.', { x: 434, y: 78, w: 596, h: 770 }),
    m(9, 'Install location and version', 'Where the application lives and which version (branch and commit) is running.', { x: 4, y: 868, w: 1040, h: 24 }),
  ]),
})

fs.mkdirSync('raw', { recursive: true })
for (const f of figs) {
  const { src, ...meta } = f
  const size = pngSize(fs.readFileSync(src))
  fs.copyFileSync(src, raw(`${f.id}.png`))
  fs.writeFileSync(raw(`${f.id}.json`), JSON.stringify({
    ...meta, width: size.w, height: size.h, redact: meta.redact || [], patches: meta.patches || [],
  }, null, 2))
  console.log('desktop', f.id)
}

function pngSize(buf) { return { w: buf.readUInt32BE(16), h: buf.readUInt32BE(20) } }
