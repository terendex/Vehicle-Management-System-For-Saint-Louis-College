// Figure definitions for the web system. Each mark points at a real element,
// so the callout boxes track the live layout rather than guessed coordinates.
import { ACCOUNTS, PASSWORD, totp } from './lib.mjs'

// ── locator helpers ──────────────────────────────────────────────────────
const inMain = (p) => p.locator('main').first()
const text = (s, scope = 'main', exact = true) => (p) => p.locator(scope).first().getByText(s, { exact })
const btn = (name, scope = 'body') => (p) => { const s = p.locator(scope).first(); return s.getByRole('button', { name }).or(s.getByRole('tab', { name })).first() }
const clickTab = (p, name) => p.getByRole('tab', { name }).or(p.getByRole('button', { name })).first().click()
const css = (s) => (p) => p.locator(s)
const ph = (s) => (p) => p.getByPlaceholder(s)
const union = (...fns) => async (p) => (await Promise.all(fns.map((f) => f(p)))).flat()

// Walk up from an element to the nearest thing that looks like a card or chip
// (has a border, a shadow, or a filled rounded background).
const CARD_FN = (el) => {
  let e = el
  while (e && e !== document.body) {
    const cs = getComputedStyle(e)
    const border = parseFloat(cs.borderTopWidth) > 0 && cs.borderTopStyle !== 'none'
    const bg = cs.backgroundColor
    const filled = bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent' && parseFloat(cs.borderTopLeftRadius) > 2
    if (border || cs.boxShadow !== 'none' || filled) return e
    e = e.parentElement
  }
  return el
}
const card = (fn) => async (p) => (await fn(p).first().evaluateHandle(CARD_FN)).asElement()
const parent = (fn, levels = 1) => async (p) => {
  const h = await (await fn(p)).evaluateHandle((el, n) => { let e = el; for (let i = 0; i < n; i++) e = e.parentElement; return e }, levels)
  return h.asElement()
}

const dismissOk = async (p) => {
  const ok = p.getByRole('button', { name: 'OK', exact: true })
  if (await ok.isVisible().catch(() => false)) await ok.click()
}

const m = (n, label, desc, at, extra = {}) => ({ n, label, desc, at, ...extra })
const MODAL = (p) => p.getByText('Registration Details', { exact: true }).locator('xpath=ancestor::*[.//*[normalize-space(text())=\"Vehicle Information\"]][1]')

const GS = { dir: '01-getting-started', section: 'Getting Started' }
const AD = { dir: '02-cdso-admin', section: 'CDSO (Administrator)' }
const SE = { dir: '03-security-guard', section: 'Security Guard' }
const OW = { dir: '04-vehicle-owner', section: 'Vehicle Owner' }
const fig = (grp, file, rest) => ({ ...rest, section: grp.section, out: `${grp.dir}/${file}.png`, id: `${grp.dir}--${file}` })

export const SHOTS = [
  // ════════════════════════ GETTING STARTED ════════════════════════
  fig(GS, '01-login-page', {
    fresh: true, path: '/login', title: 'Signing in',
    caption: 'Sign in here with the email address and password issued to you.',
    prepare: async (p) => { await p.fill('#login-email', ACCOUNTS.owner.email) },
    marks: [
      m(1, 'Email', 'Type the email address registered to your account. It is not case-sensitive.', css('#login-email')),
      m(2, 'Password', 'Enter your password. The eye icon shows or hides what you typed.', union(css('#login-password'), css('.toggle-password'))),
      m(3, 'Remember Me', 'Keeps you signed in on this computer. Leave it unticked on shared computers.', union(css('.login-card input[type=checkbox], form input[type=checkbox]'), text('Remember Me', 'body'))),
      m(4, 'Forgot Password', 'Sends a password-reset link to your email.', css('.forgot-link')),
      m(5, 'Login', 'Signs you in. You are then asked for a code from your authenticator app.', css('#login-submit')),
      m(6, 'Apply for a Vehicle Pass', 'Starts the online vehicle pass application. No account is needed to apply.', css('.register-cta-btn')),
      m(7, 'Privacy Policy & Terms', 'Opens the data privacy notice and the vehicle pass terms.', css('.terms-link >> nth=0')),
      m(8, 'Need help signing in?', 'Opens the step-by-step guide with pictures.', css('.terms-link >> nth=1')),
      m(9, 'Help', 'The same guide, from the top of the page.', css('#login-help')),
    ],
  }),
  fig(GS, '02-two-factor-code', {
    fresh: true, path: '/login', title: 'Two-factor verification',
    caption: 'After your password, confirm a 6-digit code from the authenticator app on your phone.',
    prepare: async (p) => {
      await p.fill('#login-email', ACCOUNTS.owner.email)
      await p.fill('#login-password', PASSWORD)
      await p.click('#login-submit')
      await p.waitForSelector('.tfa-code-input')
    },
    marks: [
      m(1, 'Verification code', 'Open your authenticator app (e.g. Google Authenticator) and type the 6-digit code shown for SLC Vehicle Management. It submits by itself on the sixth digit.', css('.tfa-code-input')),
    ],
  }),
  fig(GS, '03-forgot-password', {
    fresh: true, path: '/forgot-password', title: 'Resetting a forgotten password',
    caption: 'Request a reset link when you cannot sign in.',
    marks: [
      m(1, 'Email', 'Enter the email address of your account.', css('input[type=email]')),
      m(2, 'Send Reset Link', 'Emails you a link to choose a new password. The link expires, so use it soon.', btn(/Send Reset Link/)),
      m(3, 'Back to Login', 'Returns to the login page.', btn(/Back to Login/)),
    ],
  }),
  fig(GS, '04-privacy-consent', {
    fresh: true, path: '/register', title: 'Applying for a vehicle pass: privacy consent',
    caption: 'Every application starts with the Data Privacy Notice (RA 10173).',
    marks: [
      m(1, 'What is collected and why', 'Read what information the college collects and how it is used before applying.', card(text('WHAT WE COLLECT', 'body', false))),
      m(2, 'Consent checkbox', 'Tick to give your consent. Agree & Continue stays disabled until you do.', card(text('DATA PRIVACY CONSENT', 'body', false))),
      m(3, 'Decline', 'Cancels the application and returns to the login page.', btn('Decline')),
      m(4, 'Agree & Continue', 'Opens the application form.', btn('Agree & Continue')),
    ],
  }),
  fig(GS, '05-application-form', {
    fresh: true, path: '/register', title: 'Applying for a vehicle pass: the form',
    viewport: { width: 1440, height: 1100 },
    caption: 'The public application form, used by students, employees and fetchers.',
    prepare: async (p) => {
      await p.locator('input[type=checkbox]').first().check()
      await p.getByRole('button', { name: 'Agree & Continue' }).click()
    },
    marks: [
      m(1, 'Registration window', 'Shows whether applications are being accepted, and the dates of the current window.', card(text('Registration is currently open', 'body', false))),
      m(2, 'Registrant type', 'Choose Student — Vehicle, Employee, or Fetcher / Drop & Go. The form that follows asks only for what that type needs.', union(card(text('Student — Vehicle', 'body')), card(text('Fetcher / Drop & Go', 'body')))),
      m(3, 'Back to Login', 'Leave the application without submitting.', (p) => p.getByRole('button', { name: /Back to Login/ }).last()),
    ],
  }),
  fig(GS, '05b-student-application-form', {
    fresh: true, path: '/register', title: 'Applying for a vehicle pass: student form',
    viewport: { width: 1440, height: 1500 },
    caption: 'The details a student applicant fills in.',
    prepare: async (p) => {
      await p.locator('input[type=checkbox]').first().check()
      await p.getByRole('button', { name: 'Agree & Continue' }).click()
      await p.getByText('Student — Vehicle', { exact: true }).click()
    },
    marks: [
      m(1, 'Form type', 'The kind of application you are filling in.', union(text(/application form for a vehicle pass/i, 'body', false), text(/student — vehicle registration/i, 'body', false))),
      m(2, 'Campus Schedule', 'Choose one schedule (Mon · Wed · Fri or Tue · Thu · Fri). Slots are first come, first served; a full schedule cannot be selected.', union(text(/^campus schedule/i, 'body', false), card(text(/Choose one schedule/, 'body', false)))),
      m(3, 'Registrant type', 'Switch here if you picked the wrong type.', union(card(text('Registered SLC student', 'body')), card(text('Parent or guardian', 'body')))),
      m(4, 'Vehicle identification', 'Plate number, vehicle type and colour. Tick the box instead if the vehicle is brand-new and only has a conduction number. Choosing E-Bike asks for neither: the college issues the unit a control number (FM-001, FM-002, and so on) on submission, and that number is what the gate, the pass and the QR code use in place of a plate.', union(card(text(/brand-new and does not have a plate/, 'body', false)), (p) => p.getByPlaceholder(/AAA 0000/).first(), text('Select Color', 'body'))),
      m(5, 'Personal information', 'Your name, then contact details further down. Scroll to the end to attach the required documents and submit.', union(text(/^personal information$/i, 'body', false), ph('e.g. Santos'))),
    ],
  }),
  fig(GS, '06-guard-select-gate', {
    fresh: true, path: '/security/guard-login', title: 'Guard sign-in: choosing a gate',
    caption: 'Security guards sign in at the gate terminal, not through the main login page.',
    marks: [
      m(1, 'Gate list', 'Pick the gate you are standing at. Your shift and every scan are recorded against this gate.', css('.sqr-gate-list')),
      m(2, 'Help', 'Opens the guard sign-in guide with pictures.', css('.sqr-header .header-back-btn')),
    ],
  }),
  fig(GS, '07-guard-credentials', {
    fresh: true, path: '/security/guard-login', title: 'Guard sign-in: clocking in',
    viewport: { width: 1440, height: 1000 },
    caption: 'Sign in with your guard account or scan your QR badge to start your shift.',
    prepare: async (p) => {
      await p.click('.sqr-gate-item >> nth=0')
      await p.fill('#guard-email', ACCOUNTS.guard.email)
      await p.waitForTimeout(1500)
    },
    marks: [
      m(1, 'Change gate', 'Go back if you picked the wrong gate.', css('.sqr-back')),
      m(2, 'Selected gate', 'The gate you will be clocked in at.', css('.sqr-gate-pill')),
      m(3, 'Email and password', 'Your guard account credentials.', union(css('#guard-email'), css('#guard-password'))),
      m(4, 'Login & Clock In', 'Signs you in and starts your shift at this gate. Whoever was on duty here is signed out.', css('.sqr-cred-submit')),
      m(5, 'Forgot password?', 'Sends a reset link to your email.', css('.sqr-forgot-link')),
      m(6, 'QR badge', 'Appears when your account has a badge. Scan it with a USB scanner or the camera instead of typing the password.', union(css('.sqr-divider'), css('.sqr-input-section, .sqr-camera-wrap'))),
      m(7, 'Help', 'Opens the guard sign-in guide with pictures.', css('.sqr-header .header-back-btn')),
    ],
  }),

  // ════════════════════════ CDSO / ADMIN ════════════════════════
  fig(AD, '01-dashboard-layout', {
    who: 'admin', path: '/admin', title: 'CDSO dashboard and screen layout',
    caption: 'Every CDSO screen shares this sidebar. The dashboard summarises the whole system.',
    marks: [
      m(1, 'Main menu', 'Dashboard, then three groups: Management, Operations and System. Click a group to expand it.', css('aside .sidebar-nav')),
      m(2, 'Notifications', 'The red badge counts unread alerts about registrations and violations. Click to open the list.', css('aside .sidebar-brand button:visible')),
      m(3, 'Refresh', 'Reloads every number on the dashboard.', btn('Refresh', 'main')),
      m(4, 'Summary tiles', 'Accounts, registered vehicles, applications to review, unresolved violations, visitor passes and today’s gate scans.', union(card(text('People with accounts', 'main', false)), card(text('Gate scans today', 'main', false)))),
      m(5, 'Breakdown charts', 'Applications, owner types, vehicle kinds, today’s gate activity and 30-day violations. Scroll down for the weekly charts.', union(card(text('Vehicle pass applications')), card(text('Violations in the last 30 days')))),
      m(6, 'Help, Policy and Security', 'Help opens the in-app user manual. Security manages your two-factor authentication.', union(btn('Help', 'aside .sidebar-footer'), btn('Security', 'aside .sidebar-footer'))),
      m(7, 'Change Password', 'Set a new password for your account.', btn('Change Password', 'aside .sidebar-footer')),
      m(8, 'Log Out', 'Ends your session. Always log out on shared computers.', btn('Log Out', 'aside .sidebar-footer')),
    ],
  }),
  fig(AD, '02-notifications', {
    who: 'admin', path: '/admin', title: 'Notifications',
    caption: 'Alerts raised by new registrations, uploaded receipts and violations.',
    prepare: async (p) => { await p.locator('aside .sidebar-brand button:visible').first().click() },
    marks: [
      m(1, 'Bell', 'The number is how many notifications you have not read yet.', css('aside .sidebar-brand button:visible')),
      m(2, 'Notification list', 'Newest first. A blue dot marks an unread item; click one to open the screen it is about.', card(text('Notifications', 'body'))),
      m(3, 'Mark all read', 'Clears the unread dots and the badge without deleting anything.', btn(/Mark all read/)),
      m(4, 'Clear all', 'Removes every notification from the list.', btn(/Clear all/)),
    ],
  }),
  fig(AD, '03-vehicle-registration', {
    who: 'admin', path: '/admin/vehicles', title: 'Vehicle Registration Management',
    caption: 'Management › Vehicle Registration. Review and process vehicle pass applications.',
    marks: [
      m(1, 'Registration Form QR', 'Shows a QR code applicants can scan to open the online application form.', btn(/Registration Form QR/)),
      m(2, 'Registrations report', 'Pick a date range, then export a PDF or Excel report, or a one-page summary PDF. The export carries the screen’s own filters too — registrant type, payment and status — and the bar names them beside the label, so a table narrowed to Employees produces a report of employees.', css('.report-bar')),
      m(3, 'Total registrations', 'All applications received.', card(text('Total Registrations', 'main', false))),
      // Was matched on the words "Refine within", which only appear while a
      // status tile is selected; the block reads "Refine the table" otherwise.
      m(4, 'Quick filters', 'Click a payment state or registrant type to narrow the list below.', css('.vr-stats-refine')),
      m(5, 'Search', 'Find an application by name, plate or type.', ph('Search name, plate, type')),
      m(6, 'Type, payment and status filters', 'Pending Review lists the applications still waiting for your decision.', union(css('main select >> nth=0'), css('main select >> nth=2'))),
      m(7, 'Applications table', 'Each row shows the applicant, plate, schedule, payment and status.', css('main table')),
      m(8, 'View', 'Opens the application to check documents and approve or reject it.', (p) => p.locator('main table tbody tr').first().locator('button').last()),
    ],
  }),
  fig(AD, '04-registration-review', {
    who: 'admin', path: '/admin/vehicles', title: 'Reviewing an application',
    // Taller than the other modals: the receipt thumbnail adds a block to the
    // window, and Accept Registration is a callout that has to stay in frame.
    viewport: { width: 1440, height: 1250 },
    caption: 'The application detail window, opened with the View (eye) button.',
    prepare: async (p) => { await p.locator('main table tbody tr').nth(1).locator('button').last().click() },
    marks: [
      m(1, 'Applicant details', 'Name, email, registrant type, schedule, department and driver’s license.', union((p) => MODAL(p).getByText(/^full name$/i), (p) => MODAL(p).getByText(/^A\d\d-\d\d-\d+$/))),
      m(2, 'Payment', 'Whether the fee is Paid, Unpaid or Exempt, with the Official Receipt number, the amount and the date it was settled.', union((p) => MODAL(p).getByText(/^payment$/i), (p) => MODAL(p).getByText(/OR No\./))),
      m(3, 'Campus days', 'The days this pass will allow entry.', union((p) => MODAL(p).getByText(/^campus days$/i), (p) => MODAL(p).getByText('Saturday', { exact: true }))),
      // The photograph is a block of its own under Campus Days, not part of the
      // Payment row, so it gets its own number rather than a box that misses it.
      m(4, 'Official Receipt photograph', 'The picture of the receipt the applicant uploaded, shown with the number it backs — click it to open it full size. A row that has none says so in words, so “nothing was uploaded” is never mistaken for a picture that has not loaded. Only CDSO is shown this; it reaches neither the owner’s portal nor a guard.', union((p) => MODAL(p).getByText(/Official Receipt \(OR\) No\./), (p) => MODAL(p).locator('.or-receipt-thumb, .or-receipt-link, .or-receipt-missing'))),
      m(5, 'Vehicle information', 'Plate, vehicle type, colour and conduction number.', union((p) => MODAL(p).getByText('Vehicle Information', { exact: true }), (p) => MODAL(p).getByText('N/A', { exact: true }))),
      m(6, 'Accept Registration', 'Check the number against the photograph before approving. Scroll down for the decision buttons.', union((p) => MODAL(p).getByText('Accept Registration', { exact: true }), (p) => MODAL(p).locator('input').first())),
    ],
  }),
  fig(AD, '04b-registration-decision', {
    who: 'admin', path: '/admin/vehicles', title: 'Approving or rejecting an application',
    viewport: { width: 1440, height: 1000 },
    caption: 'The bottom of the Registration Details window.',
    prepare: async (p) => {
      await p.locator('main table tbody tr').nth(1).locator('button').last().click()
      await p.waitForTimeout(800)
      await MODAL(p).evaluate((el) => { let e = el; while (e) { if (e.scrollHeight > e.clientHeight + 20) { e.scrollTop = e.scrollHeight; } e = e.parentElement } })
    },
    marks: [
      m(1, 'Official Receipt (OR) Number', 'Filled in from the receipt the applicant uploaded. Check it against the photograph shown with the OR number higher up this window, and correct it only if the two disagree. It is not asked for at all when the applicant is fee-exempt.', union((p) => MODAL(p).getByText(/Official Receipt \(OR\) Number/), (p) => MODAL(p).locator('input').first())),
      m(2, 'Reason for Extra Days', 'Appears only when the pass allows more than the standard 3 days. Required; the registration is flagged as a Special Case.', union((p) => MODAL(p).getByText(/Reason for Extra Days/), (p) => MODAL(p).locator('textarea').first())),
      m(3, 'Reject', 'Turns the application down. You will be asked for the reason.', btn(/^Reject$/)),
      m(4, 'Confirm & Accept', 'Approves the application: the pass and system ID are issued and the owner’s portal account is created. It stays disabled while the fee is unsettled.', btn(/Confirm & Accept/)),
    ],
  }),
  fig(AD, '05-user-management', {
    who: 'admin', path: '/admin/users', title: 'User Management',
    caption: 'Management › User Management. Create, edit, disable and delete accounts.',
    marks: [
      m(1, 'Add User', 'Create a security guard or vehicle-owner account.', btn(/Add User/)),
      m(2, 'Screen tabs', 'User Accounts lists everyone. Detail Change Requests holds owners’ requested corrections; the badge counts pending ones.', union(btn(/User Accounts/), btn(/Detail Change Requests/))),
      m(3, 'Account counts', 'Total, active and disabled accounts.', parent(card(text('Total Users', 'main', false)))),
      m(4, 'Role tabs', 'Show only one kind of account.', union(text('All Users'), text('Fetcher / Drop & Go'))),
      m(5, 'Search', 'Search by name, email or user ID (e.g. SLC-OWN-000005).', ph('Search by name, email, or user ID')),
      m(6, 'Status filter', 'Show active, disabled or archived accounts.', card(text('All Statuses'))),
      m(7, 'Accounts table', 'User ID, name, email, role and status.', css('main table')),
      m(8, 'Actions menu', 'Edit, disable/enable, reset two-factor, print a guard QR badge, or delete the account.', (p) => p.locator('main table tbody tr').first().locator('button').last()),
    ],
  }),
  fig(AD, '06-detail-change-requests', {
    who: 'admin', path: '/admin/users', title: 'Approving detail change requests',
    caption: 'Owners cannot edit an accepted registration directly. Their corrections wait here for approval.',
    prepare: async (p) => { await clickTab(p, /Detail Change Requests/) },
    marks: [
      m(1, 'Detail Change Requests tab', 'The badge counts requests waiting for your review.', btn(/Detail Change Requests/)),
      m(2, 'Status filter', 'Show requests waiting for review, or ones already decided.', css('main select >> nth=0')),
      m(3, 'Who is asking', 'Owner name, plate, registrant type, email, and when the request was filed.', union(text('CARLO MENDOZA'), (p) => p.locator('main').getByText(/pending cdso review/i).first(), (p) => p.locator('main').getByText(/20231001@slc-sflu\.edu\.ph/).first(), (p) => p.locator('main').getByText(/Sep \d+, 2026 ·/).first())),
      m(4, 'Requested changes', 'Each field shows the current value struck through and the new value the owner wants.', union((p) => p.locator('main').getByText(/vehicle colou?r/i).first(), (p) => p.locator('main').getByText('09171234567'))),
      m(5, 'Decline / Approve & apply', 'Approve & apply updates the registration. Decline leaves it unchanged. The owner is emailed either way.', union(btn(/Decline/, 'main'), btn(/Approve & apply/))),
    ],
  }),
  fig(AD, '07-device-management', {
    who: 'admin', path: '/admin/devices', title: 'Device Management (cameras)',
    viewport: { width: 1440, height: 1250 },
    caption: 'Management › Device Management. Register the IP cameras used at the gates and parking areas.',
    prepare: async (p) => {
      await p.locator('main table tbody tr').first().getByRole('button', { name: /Connect/ }).first().click()
      await p.waitForTimeout(2500)
    },
    marks: [
      m(1, 'Add Device', 'Register a new camera: name, IP address, device ID, password and whether it watches a gate or a parking area.', btn(/Add Device/)),
      m(2, 'Camera counts', 'How many cameras are registered, and how many cover entry gates and parking.', parent(card(text('Total Cameras', 'main', false)))),
      m(3, 'Search', 'Find a camera by name, IP address or device ID.', ph(/Search name, IP/)),
      m(4, 'Assignment', 'The gate or parking area this camera covers.', (p) => p.locator('main table tbody tr').first().locator('td').nth(1)),
      m(5, 'Connect / Disconnect', 'Opens or closes the camera’s live picture in Live Feeds below.', (p) => p.locator('main table tbody tr').first().getByRole('button', { name: /Connect|Disconnect/ })),
      m(6, 'Test, Edit, Delete', 'Check the camera answers, change its settings, or remove it.', (p) => p.locator('main table tbody tr').first().locator('td').last()),
      m(7, 'Live Feeds', 'Live pictures from the cameras you connected. (Sample picture shown.)', card(text('Live Feeds'))),
    ],
  }),
  fig(AD, '08-suppliers', {
    who: 'admin', path: '/admin/suppliers', title: 'Supplier Management',
    caption: 'Management › Suppliers. Supplier vehicles are admitted automatically when their plate is scanned.',
    prepare: async (p) => { await p.locator('main').getByText('Ilocos Fresh Produce Trading').click().catch(() => {}); },
    marks: [
      m(1, 'Add Supplier', 'Register a supplier company and its plates.', btn(/Add Supplier/)),
      m(2, 'Supplier card', 'Company name and how many plates it has registered.', card(text('Coastal Office Supplies Inc.'))),
      m(3, 'Category', 'Delivery, maintenance, vendor, contractor or other.', css('main select >> nth=0'), { badge: 'bl' }),
      m(4, 'Active', 'Click to deactivate a supplier; its vehicles stop being admitted automatically.', (p) => p.locator('main').getByRole('button', { name: /Active|Inactive/ }).first()),
    ],
  }),
  fig(AD, '09-operations-center', {
    who: 'admin', path: '/admin/entries', title: 'Operations Center: live monitor',
    caption: 'Operations › Operations Center. Watch all gates, guards and cameras at once.',
    wait: 3500,
    marks: [
      m(1, 'Live summary', 'Guards on duty, recent entries, cross-gate flags and total guards.', parent(card(text('Guards On Duty', 'main', false)))),
      m(2, 'Screen tabs', 'Live Monitor (this view), Guards (who is on shift where) and Gate Records (visitors inside, cross-gate discrepancies and confiscated accounts).', union(btn(/Live Monitor/), btn(/Gate Records/))),
      m(3, 'Camera Monitor', 'View-only camera pictures. Plate detection runs on the guard terminals. (Sample picture shown.)', card(text('Camera Monitor'))),
      m(4, 'Gate column', 'Latest scans at each gate. The green chip names the guard on duty and how long they have been on shift.', card((p) => p.locator('main').getByText('789UIO').first())),
      m(5, 'Refresh', 'Reloads the screen.', btn('Refresh', 'main')),
    ],
  }),
  fig(AD, '10-operations-guards', {
    who: 'admin', path: '/admin/entries', title: 'Operations Center: guards',
    caption: 'Guard shifts: who is on duty at which gate.',
    prepare: async (p) => { await clickTab(p, /^Guards$/) },
    marks: [
      m(1, 'Guards tab', 'Guard activity and shift history.', btn(/^Guards$/)),
      m(2, 'Guard activity', 'Each guard’s code, gate, duty status, and today’s totals: scans, authorized, denied, visitors and exits.', css('main table')),
      m(3, 'Duty status', 'On Duty while the guard is signed in at a gate; Off Duty otherwise.', (p) => p.locator('main table').getByText('On Duty').first()),
      m(4, 'Recent shift history', 'When each shift started and ended. "Still active" means the guard has not signed out yet.', union(text('Recent Shift History'), (p) => p.locator('main').getByText('Still active').first())),
    ],
  }),
  fig(AD, '11-operations-gate-records', {
    who: 'admin', path: '/admin/entries', title: 'Operations Center: gate records',
    caption: 'Visitors still on campus, cross-gate discrepancies and confiscated accounts.',
    prepare: async (p) => { await clickTab(p, /Gate Records/) },
    marks: [
      m(1, 'Active Visitors', 'Visitor passes still inside, with the office visited, who issued the pass, and time left (or how long they have overstayed).', union(text('Active Visitors'), (p) => p.locator('main').getByText('HJK7021').first())),
      m(2, 'Cross-Gate Discrepancies', 'Vehicles that entered through one gate and left through another, so both gates’ records can be checked.', union(text('Cross-Gate Discrepancies'), (p) => p.locator('main').getByText(/No discrepancies detected/).first())),
      m(3, 'Confiscated accounts', 'Owners serving a violation penalty and when it ends.', card(text('Confiscated accounts'))),
      m(4, 'Lift', 'Ends a confiscation early. The violations themselves stay on record.', (p) => p.locator('main').getByRole('button', { name: /Lift/ }).first()),
    ],
  }),
  fig(AD, '12-parking-spaces', {
    who: 'admin', path: '/admin/parking', title: 'Parking Space Management',
    viewport: { width: 1440, height: 1150 },
    caption: 'Operations › Parking Space Management. Bays, zones, live occupancy and events.',
    wait: 3000,
    // The badge asks the server whether the detector is running, and the
    // capture runs with auto-detection off so the bays cannot be rewritten
    // mid-run. Answering for a working campus install instead: see applyRoutes.
    routes: { '/parking-zones/camera-status/': { 1: { running: true, stream: 'online', offline_seconds: null }, 2: { running: true, stream: 'online', offline_seconds: null } } },
    marks: [
      m(1, 'Screen tabs', 'Parking Spaces (this view) and Events (campus events that reserve parking).', union(btn(/Parking Spaces/), btn(/^Events$/))),
      m(2, 'Occupancy tiles', 'Free, Parked and Capacity cover every zone of the selected vehicle category and come from the parking cameras. On Campus counts vehicles scanned in at a gate, parked or not.', parent(card(text('Free', 'main')))),
      m(3, 'Zone tabs', 'One tab per parking zone, each naming the camera that watches it, or "No camera" where none is assigned. Click a tab to select the zone.', card(text('Zones', 'main', false))),
      m(4, 'Refresh, Cameras, New Zone', 'Reload the zones, open the live parking feeds, or create a zone. The Cameras button carries an "unzoned" badge counting cameras with no zone drawn on them yet.', union(btn('Refresh', 'main'), btn(/New Zone/))),
      m(5, 'Live View / Edit Parking Slots', 'Watch the bays over the live feed, or switch to drawing them over the zone’s still reference image.', union(btn(/Live View/), btn(/Edit Parking Slots/))),
      m(6, 'Monitoring status', 'Monitoring bays means the detector is running on this zone; Starting camera that it is coming up; Not monitored yet that the zone’s setup is unfinished, so the bay colours are not a live reading. There is no on/off switch — the server restarts the detector by itself.', css('.pm-detect-badge')),
      m(7, 'Delete Zone', 'Removes the selected zone and its bays. It sits apart from the working controls because it cannot be undone.', btn(/Delete Zone/)),
      m(8, 'Bay map', 'Drawn over the live feed. Green bays are free, red bays are taken (with the plate where one is known); a vehicle lying across two bays is flagged as double parking.', (p) => p.locator('main canvas, main img').last()),
    ],
  }),
  fig(AD, '13-parking-edit-slots', {
    who: 'admin', path: '/admin/parking', title: 'Drawing parking bays',
    // Tall enough to hold the whole Set Up Bay Monitoring checklist, which
    // sits under the canvas and is one of the callouts.
    viewport: { width: 1440, height: 1560 },
    caption: 'Edit Parking Slots mode: draw and adjust bays over the zone’s reference image.',
    prepare: async (p) => { await clickTab(p, /Edit Parking Slots/) },
    marks: [
      m(1, 'Drawing tools', 'Box draws rectangular bays; Pen places points and closes the shape on the first (yellow) point, for angled bays. The hint beside the tools counts the points placed.', union(btn(/^Box$/), btn(/^Pen$/))),
      m(2, 'Upload Image / Save Layout', 'Upload a reference picture of the lot, and save the bays when you are done.', union(btn(/Upload Image/), btn(/Save Layout/))),
      m(3, 'Drawing area', 'Click and drag to draw a bay. Click a bay to open its small toolbar: rename it, duplicate it, or remove it. Enter keeps a bay, Delete removes it, and Esc cancels a half-drawn shape.', (p) => p.locator('main canvas, main img').last()),
      m(4, 'Camera', 'The camera whose picture the bays are drawn over. Draw against the zone’s own camera — bays drawn over another camera’s view are scored against a different scene.', css('main select >> nth=0')),
      m(5, 'Set Up Bay Monitoring', 'The three steps that make this zone monitored, in order, with the next one’s button highlighted: capture a reference image of the empty lot, draw the parking slots on it, then Start Monitoring. Until all three are done the bays are not being watched.', css('.pm-setup')),
    ],
  }),
  fig(AD, '14-events', {
    who: 'admin', path: '/admin/parking', title: 'Campus events',
    caption: 'Events reserve part of campus parking and note organizer plates.',
    prepare: async (p) => { await clickTab(p, /^Events$/) },
    marks: [
      m(1, 'Event Mode overrides', 'Parking Override lets guards admit vehicles when a zone is full; Entry Override lets them admit plates that would be denied. Click the switch to turn each on or off.', union(card(text('Parking Override')), card(text('Entry Override')))),
      m(2, 'Add Event', 'Create an event: name, date, times, how much parking it takes, and organizer plates.', btn(/Add Event/)),
      m(3, 'Event card', 'Date, time, organizer plates, and how much of campus parking the event holds.', card(text('Research Congress 2026'))),
      m(4, 'Event actions', 'Activate the event, reschedule it, show its details, or delete it.', (p) => { const b = p.locator('main').getByText('Research Congress 2026', { exact: true }).locator('xpath=ancestor::*[.//button[contains(.,"Activate")]][1]').getByRole('button'); return [b.first(), b.last()] }),
      m(5, 'Archived Events', 'Past events, kept for reference.', btn(/Archived Events/)),
    ],
  }),
  fig(AD, '15-violations', {
    who: 'admin', path: '/admin/violations', title: 'Violations',
    caption: 'Operations › Violations. The 3-offence penalty ladder and every recorded offence.',
    marks: [
      m(1, 'Active warnings', 'How many violations are still counting against their owners.', card(text('Active Warnings', 'main', false))),
      m(2, 'Violations report', 'Pick a date range and export a PDF or Excel report. The range narrows the table on screen as well, so what you are looking at is what the file will contain; where it is set, it overrides the period buttons below.', css('.report-bar')),
      m(3, 'Status filter', 'All, Warnings, Confiscated (3rd offence) or Cleared / Resolved.', union(btn(/^All$/, 'main'), btn(/Cleared \/ Resolved/))),
      m(4, 'Type and period', 'Filter by violation type, and by Today, Week, Month or Year.', union(css('main select >> nth=0'), btn('Year', 'main'))),
      m(5, 'Search', 'Find by plate, conduction number, owner or notes. The arrow button resets all filters.', union(ph(/Search plate, owner/), (p) => p.locator('main').getByPlaceholder(/Search plate, owner/).locator('xpath=following::button[1]'))),
      m(6, 'Violations table', 'Plate, owner, type with offence number (1st, 2nd, 3rd), notes, evidence photo, when it was issued and by whom.', css('main table')),
      m(7, 'Lift', 'Voids a violation as a false alarm. It stops counting, and later offences are renumbered.', (p) => p.locator('main').getByRole('button', { name: /Lift/ }).first()),
    ],
  }),
  fig(AD, '16-vehicle-log', {
    who: 'admin', path: '/admin/vehicle-log', title: 'Vehicle Log',
    caption: 'Operations › Vehicle Log. Every gate scan, entry and exit, across all gates.',
    marks: [
      m(1, 'Record count and exports', 'Download the filtered log as PDF or Excel.', union(btn(/Export PDF/), btn(/Export Excel/))),
      m(2, 'Date range', 'Quick ranges, or pick exact start and end dates.', card(text('Date range', 'main'))),
      m(3, 'Search', 'Find a plate, owner or guard.', ph(/Search plate, owner or guard/)),
      m(4, 'Gate, status and category filters', 'Narrow the log; Clear filters resets them.', union(text('All Gates'), btn(/Clear filters/))),
      m(5, 'Log table', 'Time, plate, owner, gate, decision and the guard on duty.', css('main table')),
      m(6, 'Exit', 'When the vehicle left and how long it stayed, or "Still inside".', (p) => p.locator('main table thead th').last()),
    ],
  }),
  fig(AD, '17-rule-constraints', {
    who: 'admin', path: '/admin/rules', title: 'Rule Constraints: entry rules',
    caption: 'System › Rule Constraints. When each kind of vehicle may enter campus.',
    marks: [
      m(1, 'Screen tabs', 'Entry Rules, Registration Period (when applications are accepted) and Access Mode.', union(btn(/Entry Rules/), btn(/Access Mode/))),
      m(2, 'Rule', 'One rule per registrant type: who it applies to, allowed days and hours.', (p) => p.locator('main').getByText('Student — Vehicle', { exact: true }).locator('xpath=ancestor::*[.//button][1]')),
      m(3, 'Days, hours and stay limit', 'Allowed campus days, the time window, and the maximum stay where one is set.', union(text('Supplier'), text(/Max stay/, 'main', false))),
      m(4, 'Edit', 'Opens the rule to change its days, hours or stay limit.', (p) => p.locator('main').getByText('Student — Vehicle', { exact: true }).locator('xpath=following::button[1]')),
    ],
  }),
  fig(AD, '18-registration-period', {
    who: 'admin', path: '/admin/rules', title: 'Rule Constraints: registration period',
    caption: 'Set when the online application form accepts submissions.',
    prepare: async (p) => { await clickTab(p, /Registration Period/) },
    marks: [
      m(1, 'New Period', 'Create a registration window with a label, start date and end date.', btn(/New Period/)),
      m(2, 'Period', 'Label, start and end dates. Only one period can be Active; the public form accepts applications only while it is open.', (p) => p.locator('main table tbody tr').first()),
      m(3, 'Extend Duration / Deactivate', 'Open a running period to move its end date, or close the window early. The editor is headed "Extending period" and saves with Save New Dates.', union(btn(/Extend Duration/, 'main'), btn(/Deactivate/, 'main'))),
    ],
  }),
  fig(AD, '19-access-mode', {
    who: 'admin', path: '/admin/rules', title: 'Rule Constraints: access mode',
    caption: 'Campus-wide overrides such as open campus mode.',
    prepare: async (p) => { await clickTab(p, /Access Mode/) },
    marks: [
      m(1, 'Open Campus switch', 'When ON, every vehicle may enter regardless of registration, schedule or entry rules. Use it for open events or graduation, and switch it OFF afterwards.', card(text(/Open Campus (OFF|ON)/, 'main', false))),
      m(2, 'Current effect', 'What the setting means right now.', text(/Normal entry restrictions apply/, 'main', false)),
    ],
  }),
  fig(AD, '20-audit-log', {
    who: 'admin', path: '/admin/audit', title: 'Audit Log',
    caption: 'System › Audit Log. What staff did to accounts and records.',
    marks: [
      m(1, 'Event count and exports', 'Download the filtered log as PDF or Excel.', union(btn(/Export PDF/), btn(/Export Excel/))),
      m(2, 'Date range', 'All, Today, Week, Month, Year, or exact dates.', card(text('Date range', 'main'))),
      m(3, 'Search', 'Search by the staff member’s name or the details text.', ph(/Search actor or details/)),
      m(4, 'Action filter', 'Show one kind of action, e.g. User Created or Entry Override.', (p) => p.locator('main select').last()),
      m(5, 'Audit table', 'When, who, what kind of action, and the details.', css('main table')),
    ],
  }),
  fig(AD, '21-settings-accounts-fees', {
    who: 'admin', path: '/admin/settings', title: 'System Settings: accounts & fees',
    caption: 'System › System Settings. System-wide policies, grouped into five tabs.',
    marks: [
      m(1, 'Settings tabs', 'Accounts & Fees, Gates & Scanning, Parking, Data & Backup, and Report Signatories. A tab holding unsaved changes carries a dot, and Save stays disabled until something differs from what is stored.', union(btn(/Accounts & Fees/), btn(/Report Signatories/))),
      m(2, 'Account expiry period', 'How long a vehicle-owner account lasts before it is archived automatically.', union(css('main input >> nth=0'), css('main input >> nth=1'))),
      m(3, 'Retention notice', 'What the chosen period means for owners’ records.', card(text(/Owner accounts archive/, 'main', false))),
      m(4, 'Vehicle pass fees', 'The amounts applicants are asked to pay at the Accounting Office.', union(css('main input >> nth=2'), css('main input >> nth=3'))),
    ],
  }),
  fig(AD, '22-settings-gates-scanning', {
    who: 'admin', path: '/admin/settings', title: 'System Settings: gates & scanning',
    caption: 'Gates, scan timing and event overrides.',
    prepare: async (p) => { await clickTab(p, /Gates & Scanning/) },
    marks: [
      m(1, 'Add a gate', 'Enter the gate number and a display label, then Add Gate. It appears on the guard sign-in page straight away.', union(ph(/e\.g\. 2$/), btn(/Add Gate/))),
      m(2, 'Gate list', 'Every gate. Deactivate removes a gate from the sign-in page without deleting its history.', union(text(/^Gates \(/i, 'main', false), (p) => p.locator('main').getByRole('button', { name: /Deactivate|Activate/ }).last())),
      m(3, 'Scan deduplication', 'If the same plate is read again within this many seconds, the repeat is ignored so it is not logged twice.', (p) => p.locator('main input[type=number]').last()),
    ],
  }),
  fig(AD, '23-settings-parking', {
    who: 'admin', path: '/admin/settings', title: 'System Settings: parking',
    caption: 'Parking detection thresholds.',
    prepare: async (p) => { await clickTab(p, /^Parking$/) },
    marks: [
      m(1, 'Counts as parked after', 'How long a vehicle must stay still before its bay counts as taken.', (p) => p.locator('main input[type=number]').nth(0)),
      m(2, 'Reports double parking after', 'How long a vehicle must sit across two bays before a double-parking violation is issued.', (p) => p.locator('main input[type=number]').nth(1)),
      m(3, 'Broadcast Parking Notice', 'Write a subject and message, then Broadcast to All Owners. It is emailed and shown in every owner’s portal.', union(ph(/Parking suspension/), btn(/Broadcast to All Owners/))),
      m(4, 'Active notices', 'Notices owners can currently see. The × removes one.', union(text(/Active notices/i, 'main', false), card(text('Gym parking closed on Friday')))),
    ],
  }),
  fig(AD, '24-settings-data-backup', {
    who: 'admin', path: '/admin/settings', title: 'System Settings: data & backup',
    viewport: { width: 1440, height: 1100 },
    caption: 'Backups, restore and data retention.',
    prepare: async (p) => { await clickTab(p, /Data & Backup/) },
    marks: [
      m(1, 'Retention period', 'Access logs, violations and archived accounts older than this are deleted automatically. The audit log is kept.', union((p) => p.locator('main input[type=number]').nth(0), card(text(/permanently deleted/, 'main', false)))),
      m(2, 'Download / Restore', 'Download a full snapshot of the system data, or restore the system from a backup file.', union(btn(/Download Backup/), btn(/Restore from Backup/))),
      m(3, 'Automatic backups', 'How often the server backs itself up, and how many automatic backups to keep.', union(css('main select >> nth=0'), (p) => p.locator('main input[type=number]').nth(1))),
      m(4, 'Backups on the server', 'Saved backups. Save As downloads a copy, Restore brings the system back to that point, and the bin deletes it.', (p) => p.locator('main').getByRole('button', { name: /Save As/ }).first().locator('xpath=ancestor::*[.//*[contains(text(),".json")]][1]')),
    ],
  }),
  fig(AD, '24b-settings-report-signatories', {
    who: 'admin', path: '/admin/settings', title: 'System Settings: report signatories',
    // Taller than the default: the preview of the signature block sits below
    // the fold at 900, and a figure whose point is the preview cannot cut it.
    viewport: { width: 1440, height: 1150 },
    caption: 'Who signs the PDF reports. Every exported PDF ends with this block.',
    prepare: async (p) => { await clickTab(p, /Report Signatories/) },
    marks: [
      m(1, 'Prepared by', 'Normally whoever pressed the export button — their name and role are taken from the account signed in. Leave these two boxes blank to keep it that way; fill them in only to sign every report with one name instead.', union((p) => p.locator('#report_preparer_name'), (p) => p.locator('#report_preparer_position'))),
      m(2, 'Approved by', 'Set here, because the head of office does not sign in to run every report and the post changes hands. Leave the name blank and the report prints an empty ruled line to be signed by hand.', union((p) => p.locator('#report_approver_name'), (p) => p.locator('#report_approver_position'))),
      m(3, 'Captions', 'The wording above each signature. An office that files these as "Submitted by" or "Noted by" can say so without a change to the system.', union((p) => p.locator('#report_prepared_by_label'), (p) => p.locator('#report_approved_by_label'))),
      m(4, 'Preview', 'Shows the signature block exactly as it will be printed.', card(text(/As it will appear on every PDF report/, 'main', false))),
    ],
  }),
  fig(AD, '25-help', {
    who: 'admin', path: '/help', title: 'Help & User Manual',
    caption: 'The built-in guide, available to every role from the sidebar.',
    marks: [
      m(1, 'Search the manual', 'Type a keyword such as backup, violations or scanning.', ph(/Search the manual/)),
      m(2, 'Topics', 'Topics for your role, grouped by area. Click one to read it.', card((p) => p.locator('main').getByText('GETTING STARTED', { exact: false }).first())),
      m(3, 'Article', 'Step-by-step instructions for the selected topic.', card((p) => p.locator('main h2, main h3').filter({ hasText: 'Logging In & Your Account' }))),
    ],
  }),

  // ════════════════════════ SECURITY GUARD ════════════════════════
  fig(SE, '01-entry-management', {
    who: 'guard', path: '/security/entries', title: 'Entry Management (gate terminal)',
    caption: 'The guard’s main screen at the gate. Plates are read automatically from the entry camera.',
    wait: 3500,
    marks: [
      m(1, 'Menu and gate', 'Entry Management, Parking and Vehicle Log. The green tag shows the gate you are clocked in at.', union(css('aside .sidebar-brand'), text('Vehicle Log', 'aside'))),
      m(2, 'CCTV Monitor', 'Live picture from this gate’s entry camera. Detected plates are boxed and checked automatically. (Sample picture shown.)', card(text('CCTV Monitor'))),
      m(3, 'Owner name / plate search', 'Type a plate, conduction number or owner name when a plate is not read automatically.', ph(/NAME, E\.G\./i)),
      m(4, 'Check Plate — Entry / Exit', 'Checks the typed plate. A vehicle already inside is logged out; otherwise its entry is checked against its pass and schedule.', btn(/Check Plate/)),
      m(5, 'Scan QR', 'Scan the QR code on the owner’s vehicle pass, or on any printed slip — a visitor slip, a supplier or event pass, or the entry slip given to a vehicle with no plate. A slip opens rather than acting: you then press Record Exit or Reprint, so looking one up cannot let a vehicle out by accident.', btn(/Scan QR/)),
      m(6, 'No Plate?', 'Record a vehicle with no plate or conduction sticker by describing it. Its entry slip prints on the thermal printer for the driver to keep.', btn(/No Plate/)),
      m(7, 'Recent Scans', 'Latest decisions at this gate. The chips count entries by category.', card(text('Recent Scans'))),
      m(8, 'Active Visitors', 'Visitor passes still inside, with time left. +30m extends a pass.', card(text('Active Visitors'))),
      m(9, 'Confiscated accounts', 'Owners serving a violation penalty. They may not enter or park.', card(text('Confiscated accounts'))),
      m(10, 'Shift controls', 'On-duty timer, Help, Policy, Change Shift (hand over the gate) and Log Out.', css('aside .sidebar-footer')),
    ],
  }),
  fig(SE, '02-plate-check-result', {
    who: 'guard', path: '/security/entries', title: 'Checking a plate',
    caption: 'The result shown after a plate is checked.',
    prepare: async (p) => {
      await p.getByPlaceholder(/NAME, E\.G\./i).fill('NBC1234')
      await clickTab(p, /Check Plate/)
      await p.waitForTimeout(2500)
    },
    after: async (p) => { await p.keyboard.press('Escape'); await dismissOk(p) },
    // The check really runs, and on a closed day it files a violation and
    // confiscates the demo owner. Put the demo data back afterwards.
    afterSql: `delete from tbl_violation where plate_number='NBC1234';
      update tbl_user set confiscation_level=0, confiscated_at=null, confiscated_until=null, confiscation_reason='' where email='20231001@slc-sflu.edu.ph';
      delete from tbl_access_log where plate_number='NBC1234' and scanned_at > now() - interval '3 hours' and status <> 'authorized' and status <> 'exited';
      delete from tbl_notification where created_at > now() - interval '10 minutes' and plate_number='NBC1234';`,
    marks: [
      m(1, 'Decision', 'The result in large type: here WRONG SCHEDULE DAY, with the plate and registrant type. Approved entries show in green.', union((p) => p.getByRole('button', { name: /^Acknowledge$/ }).locator('xpath=ancestor::*[.//*[text()="NBC1234"]][1]').getByText(/wrong schedule day/i), (p) => p.getByRole('button', { name: /^Acknowledge$/ }).locator('xpath=ancestor::*[.//*[text()="NBC1234"]][1]').getByText('NBC1234', { exact: true }))),
      m(2, 'Reason and rule', 'Why the vehicle was denied and which entry rule applied.', union((p) => p.getByText(/Campus closed to this entry type/).first(), card((p) => p.getByText(/^Rule:/).first()))),
      m(3, 'Owner and vehicle', 'Check these against the vehicle in front of you.', union((p) => p.getByText('Owner', { exact: true }).first(), (p) => p.getByText('Car · White').first())),
      m(4, 'Override Entry', 'Let the vehicle in anyway. You must give a reason, and the override is recorded under your name.', btn(/Override Entry/)),
      m(5, 'Acknowledge', 'Accept the decision and close the result.', btn(/^Acknowledge$/)),
      m(6, 'Detected plate', 'The plate the camera read, boxed on the live picture. (Sample picture shown.)', { x: 468, y: 249, w: 50, h: 19 }),
    ],
  }),
  fig(SE, '03-no-plate', {
    who: 'guard', path: '/security/entries', title: 'Recording a vehicle with no plate',
    viewport: { width: 1440, height: 1000 },
    caption: 'Describe the vehicle when there is no plate the system can read.',
    prepare: async (p) => { await clickTab(p, /No Plate/) },
    after: async (p) => { await p.keyboard.press('Escape') },
    marks: [
      m(1, "Driver's Name", 'The name the driver gives you.', (p) => p.getByPlaceholder('e.g. Juan Dela Cruz', { exact: true })),
      m(2, 'Who is entering?', 'Student, employee, fetcher, visitor or supplier.', (p) => p.getByText('Who is entering?').locator('xpath=following::select[1]')),
      m(3, 'Vehicle description', 'Vehicle type, colour and make/model stand in for the missing plate on the log.', union((p) => p.getByText('Vehicle Type', { exact: true }).locator('xpath=following::select[1]'), ph('e.g. Toyota Vios'))),
      m(4, 'Note', 'Anything useful, e.g. "newly delivered unit, plate not yet issued".', ph(/Newly delivered unit/)),
      m(5, 'Record Entry', 'Saves the entry to the Vehicle Log. Cancel closes without saving.', union(btn(/^Cancel$/), btn(/Record Entry/))),
    ],
  }),
  fig(SE, '04-parking', {
    who: 'guard', path: '/security/parking', title: 'Parking monitor',
    caption: 'Watch parking zones, see free spaces and act on parking offences.',
    wait: 3500,
    routes: { '/parking-zones/camera-status/': { 1: { running: true, stream: 'online', offline_seconds: null }, 2: { running: true, stream: 'online', offline_seconds: null } } },
    marks: [
      m(1, 'Zones', 'Pick the parking zone to watch.', union(text('ZONES', 'main', false), btn(/Gym Motorcycle Area/))),
      m(2, 'Cameras', 'Switch between the cameras watching parking.', union(btn(/Main Parking Cam/), btn(/Motorcycle Parking Cam/))),
      m(3, 'Live picture', 'The camera view with bay outlines. (Sample picture shown.)', (p) => p.locator('main canvas').first()),
      m(4, 'Legend', 'Free, Occupied and Double parking. The bays refresh every 8 seconds.', union(text('Free', 'main'), text('Double parking', 'main'))),
      m(5, 'Campus-wide figures', 'Free, Parked and Capacity for this vehicle type across campus, from the parking cameras. On campus beside them counts gate entry and exit scans instead, parked or not, so the two are counted differently and will not agree. Held appears where an event is reserving spaces.', card(text('Car Parking', 'main'))),
      m(6, 'Bays in this zone', 'How many bays the camera sees taken, with the zone’s own status: Monitoring, Camera off, or Not set up — which means an admin has not finished the zone’s setup, so those bay colours may be out of date.', card(text(/Bays in /, 'main', false))),
      m(7, 'Issue Violation / Override Parking', 'Record a parking offence, or let a vehicle park when the area is full (event mode).', union(btn(/Issue Violation/), btn(/Override Parking/))),
    ],
  }),
  fig(SE, '05-issue-violation', {
    who: 'guard', path: '/security/parking', title: 'Issuing a parking violation',
    viewport: { width: 1440, height: 1000 },
    caption: 'The violation form opened from the parking monitor.',
    prepare: async (p) => { await clickTab(p, /Issue Violation/) },
    after: async (p) => { await p.keyboard.press('Escape') },
    marks: [
      m(1, 'License Plate', 'The plate of the offending vehicle. Required.', ph(/ABC 123/i)),
      m(2, 'Violation Type', 'Choose the offence, e.g. No Sticker, Double Parking or Time Exceed.', (p) => p.getByText('Violation Type', { exact: true }).locator('xpath=following::select[1]')),
      m(3, 'Notes', 'Optional details that help the CDSO review it.', ph(/Optional additional details/)),
      m(4, 'Issue Violation', 'Records the violation against the vehicle’s owner and counts toward the offence ladder. Cancel closes without saving.', union(btn(/^Cancel$/), (p) => p.getByRole('button', { name: /Issue Violation/ }).last())),
    ],
  }),
  fig(SE, '06-vehicle-log', {
    who: 'guard', path: '/security/audit', title: 'Vehicle Log (gate)',
    caption: 'Every scan recorded at your gate.',
    marks: [
      m(1, 'Status filter', 'Show all scans or only one decision: Authorized, Denied, Wrong Day, Visitor or Exited.', union(btn(/^All$/, 'main'), btn(/^Exited$/, 'main'))),
      m(2, 'Date and refresh', 'Pick another day, or reload the list.', union(css('main input[type=date]'), (p) => p.locator('main input[type=date]').locator('xpath=following::button[1]'))),
      m(3, 'Scan entry', 'Plate, decision, owner, guard on duty and time. Exits show when the vehicle left and how long it stayed.', (p) => p.locator('main').getByText('ABK5521').first().locator('xpath=ancestor::*[contains(.,"Owner:")][1]')),
    ],
  }),

  // ════════════════════════ VEHICLE OWNER ════════════════════════
  fig(OW, '01-portal-overview', {
    who: 'owner', path: '/owner', title: 'Vehicle owner portal: your pass',
    viewport: { width: 1440, height: 2320 }, clip: { x: 0, y: 0, width: 1440, height: 1230 },
    caption: 'What a registered owner sees after signing in.',
    marks: [
      m(1, 'Help, Policy and Log Out', 'The in-app guide, the privacy policy, and signing out.', (p) => { const b = p.locator('header').first().getByRole('button'); return [b.first(), b.last()] }),
      m(2, 'Security and Change Password', 'Manage two-factor authentication and set a new password.', union(btn(/^Security$/, 'main'), btn(/Change Password/, 'main'))),
      m(3, 'Account and registration IDs', 'Your portal account ID and your vehicle pass registration ID.', union(card(text('Portal Account ID', 'main', false)), card(text('System Registration ID', 'main', false)))),
      m(4, 'Personal information', 'Your details and assigned campus days.', card(text('Personal Information', 'main', false))),
      m(5, 'Pending change request', 'A correction you asked for, waiting for CDSO approval. You can withdraw it.', card(text(/Waiting for CDSO approval/i, 'main', false))),
      m(6, 'Vehicle access QR code', 'Show this at the gate. Tap to enlarge, show it fullscreen, or copy its data.', card(text('Vehicle Access QR Code', 'main', false))),
      m(7, 'Registration status', 'Whether your vehicle is authorized to enter campus.', card(text('Registration Status', 'main', false))),
      m(8, 'Vehicle information', 'Plate, type, colour and conduction number on file.', card(text('Vehicle Information', 'main', false))),
    ],
  }),
  fig(OW, '02-portal-violations-parking', {
    who: 'owner', path: '/owner', title: 'Vehicle owner portal: violations & parking',
    viewport: { width: 1440, height: 2320 }, clip: { x: 0, y: 1220, width: 1440, height: 1100 },
    caption: 'Further down the portal: your violation record, announcements and live parking.',
    marks: [
      m(1, 'My violation record', 'Any violations against your account and their status.', card(text('My Violation Record', 'main', false))),
      m(2, 'Announcements', 'Parking notices from the CDSO.', card(text('Gym parking closed on Friday', 'main'))),
      m(3, 'Available / Parked / Total', 'Spaces for your vehicle type right now. A "Held for event" figure joins them while an event is reserving parking.', union(card(text(/^available$/i, 'main', false)), card(text(/^total$/i, 'main', false)))),
      m(4, 'Zone fill level', 'How full each parking zone is.', card(text('Main Building Car Park', 'main'))),
      m(5, 'Bays', 'Green bays are free; red bays are taken.', (p) => p.locator('main').getByText('C01', { exact: true }).locator('xpath=../..')),
    ],
  }),
  fig(OW, '03-security-two-factor', {
    who: 'owner', path: '/owner', title: 'Account security (two-factor)',
    caption: 'Manage your authenticator and backup codes.',
    prepare: async (p) => { await p.getByRole('button', { name: /^Security$/ }).first().click() },
    after: async (p) => { await p.keyboard.press('Escape') },
    marks: [
      m(1, 'Two-factor status', 'On means an authenticator code is required when you sign in on a new device, after 7 days away, or before sensitive changes.', (p) => p.getByText('On', { exact: true }).first().locator('xpath=ancestor::*[1]')),
      m(2, 'Last used', 'When your authenticator last produced a valid code. If that looks wrong, pair a new phone.', (p) => p.getByText(/^Last used/).first()),
      m(3, 'Backup code', 'A one-time code for signing in if you lose your phone. Generate one and keep it somewhere safe.', (p) => p.getByText(/backup code$/i).first()),
      m(4, 'New backup code / Pair a new phone', 'Create a fresh backup code, or move two-factor to a different phone.', union(btn(/New backup code/), btn(/Pair a new phone/))),
    ],
  }),
]

// ── Phone versions of the vehicle owner's pictures ──────────────────────
// Owners mostly use their phones, and on a phone the screens stack into one
// column, so a shrunk desktop picture would point at buttons that are not
// where the reader sees them. Each phone figure reuses its desktop figure's
// steps and callout wording, re-measured on the phone layout.
const PHONE = { width: 390, height: 844 }
const violationsTop = async (p) => {
  const y = await p.getByText(/^violations$/i).first().evaluate((el) => el.getBoundingClientRect().top + window.scrollY)
  return Math.round(y) - 12
}
const pageHeight = (p) => p.evaluate(() => document.documentElement.scrollHeight)

const phoneVariant = (id, extra = {}) => {
  const base = SHOTS.find((s) => s.id === id)
  if (!base) throw new Error(`no desktop figure ${id}`)
  const { clip, viewport, ...rest } = base
  return {
    ...rest,
    ...extra,
    id: `${id}-mobile`,
    out: base.out.replace(/\.png$/, '-mobile.png'),
    viewport: PHONE,
    phone: true,
    layout: 'side',
  }
}

SHOTS.push(
  phoneVariant('01-getting-started--01-login-page', {
    prepare: async (p) => { await p.fill('#login-email', ACCOUNTS.owner.email) },
  }),
  phoneVariant('01-getting-started--02-two-factor-code', {
    // An owner reads this, so show the owner's own sign-in.
    prepare: async (p) => {
      await p.fill('#login-email', ACCOUNTS.owner.email)
      await p.fill('#login-password', PASSWORD)
      await p.click('#login-submit')
      await p.waitForSelector('.tfa-code-input')
    },
  }),
  phoneVariant('01-getting-started--03-forgot-password'),
  phoneVariant('01-getting-started--04-privacy-consent'),
  phoneVariant('01-getting-started--05-application-form'),
  phoneVariant('01-getting-started--05b-student-application-form', {
    clipFn: async () => ({ x: 0, y: 0, width: PHONE.width, height: 1720 }),
  }),
  phoneVariant('04-vehicle-owner--01-portal-overview', {
    clipFn: async (p) => ({ x: 0, y: 0, width: PHONE.width, height: await violationsTop(p) }),
  }),
  phoneVariant('04-vehicle-owner--02-portal-violations-parking', {
    clipFn: async (p) => {
      const y = await violationsTop(p)
      return { x: 0, y, width: PHONE.width, height: (await pageHeight(p)) - y }
    },
  }),
  phoneVariant('04-vehicle-owner--03-security-two-factor'),
)
