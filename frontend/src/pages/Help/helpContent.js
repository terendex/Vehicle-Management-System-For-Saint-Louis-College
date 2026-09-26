// User-manual content for the Help section.
//
// Each topic is tagged with:
//   roles  — which signed-in accounts see it in /help ('admin', 'security', 'vehicle_owner')
//   guide  — which login page's public help (/guide) shows it: 'owner' for the
//            main login (students, employees, fetchers), 'guard' for the gate
//            sign-in page. Only what someone needs BEFORE they can sign in
//            belongs here. CDSO material is never tagged for the guide: how the
//            administration console works is not published on a page anyone
//            can open, so it lives only in the CDSO's own signed-in manual.
//
// Body blocks:
//   { type: 'p',      text }          → paragraph
//   { type: 'steps',  items: [...] }  → numbered steps
//   { type: 'list',   items: [...] }  → bulleted list
//   { type: 'note',   text }          → highlighted tip/warning
//   { type: 'figure', id, caption? }  → annotated screenshot from helpFigures.js

export const HELP_ROLE_LABELS = {
  admin: 'CDSO',
  security: 'Security Guard',
  vehicle_owner: 'Vehicle Owner',
}

// The public guide's two audiences. Each login page opens its own, and the
// guide offers no way across — the main login's help is for vehicle owners
// only, the gate sign-in page's help for guards only.
export const GUIDE_AUDIENCES = {
  owner: {
    title: 'Help for students, employees & fetchers',
    blurb: 'How to apply for a vehicle pass, sign in to your portal, and reset your password.',
    searchExamples: 'forgot password, vehicle pass, code',
    returnTo: '/login',
  },
  guard: {
    title: 'Help for security guards',
    blurb: 'How to sign in at your gate and start your shift.',
    searchExamples: 'gate, QR badge, forgot password',
    returnTo: '/security/guard-login',
  },
}

export const HELP_TOPICS = [
  // ── Getting Started ─────────────────────────────────────────────────────
  {
    id: 'logging-in',
    title: 'Logging In',
    category: 'Getting Started',
    roles: ['admin', 'vehicle_owner'],
    guide: ['owner'],
    body: [
      { type: 'p', text: 'Sign in on the login page with the email address and password issued to you. Emails are not case-sensitive.' },
      { type: 'steps', items: [
        'Open the login page and type your email and password.',
        'Press Login.',
        'If this is your first login with a temporary password, you will be asked to set a new one before continuing.',
      ] },
      { type: 'figure', id: 'start-login-page' },
      { type: 'note', text: 'Security guards do not use this page. They sign in at the gate terminal instead.' },
    ],
  },
  {
    id: 'two-factor',
    title: 'Two-Factor Verification',
    category: 'Getting Started',
    roles: ['admin', 'vehicle_owner'],
    guide: ['owner'],
    body: [
      { type: 'p', text: 'After your password, the system asks for a 6-digit code from an authenticator app on your phone (for example Google Authenticator). This stops anyone who learns your password from getting into your account.' },
      { type: 'steps', items: [
        'The first time you sign in, scan the QR code shown with your authenticator app and type the code it displays.',
        'After that, open the app whenever the code screen appears and type the current 6-digit code for SLC Vehicle Management.',
        'The code submits by itself when the sixth digit is typed.',
      ] },
      { type: 'figure', id: 'start-two-factor-code' },
      { type: 'note', text: 'You are asked for a code on a new device, after 7 days away, and after resetting your password. If you lose your phone, use a backup code, or visit the CDSO Office to have your two-factor setup reset.' },
    ],
  },
  {
    id: 'forgot-password',
    title: 'Forgot Password',
    category: 'Getting Started',
    roles: ['admin', 'security', 'vehicle_owner'],
    guide: ['owner', 'guard'],
    body: [
      { type: 'p', text: 'If you cannot remember your password, request a reset link by email.' },
      { type: 'steps', items: [
        'On the login page press “Forgot Password” (guards: “Forgot password?” on the gate sign-in page).',
        'Type the email address of your account and press Send Reset Link.',
        'Open the email and follow the link to choose a new password. The link expires, so use it soon.',
      ] },
      { type: 'figure', id: 'start-forgot-password' },
      { type: 'note', text: 'If your account uses two-factor verification, you are asked for a code on the next sign-in after a reset.' },
    ],
  },
  {
    id: 'status-messages',
    title: 'Understanding Messages',
    category: 'Getting Started',
    roles: ['admin', 'security', 'vehicle_owner'],
    guide: ['owner', 'guard'],
    body: [
      { type: 'p', text: 'After an action, the system shows a message box in the middle of the screen. Read it, then press its button (for example OK) to close it — it stays until you do, so a message is never missed.' },
      { type: 'list', items: [
        'Success — the action completed.',
        'Error — something went wrong; the message explains what and what to do next.',
        'Confirmation — the system is asking before it does something that cannot be undone.',
      ] },
    ],
  },

  // ── Vehicle Owner ───────────────────────────────────────────────────────
  {
    id: 'owner-register',
    title: 'Applying for a Vehicle Pass',
    category: 'Vehicle Pass',
    roles: ['vehicle_owner'],
    guide: ['owner'],
    body: [
      { type: 'p', text: 'Anyone who needs to bring a vehicle onto campus applies online — no account is needed. The CDSO reviews each application before a pass is issued.' },
      { type: 'steps', items: [
        'On the login page press “Apply for a Vehicle Pass”.',
        'Read the Data Privacy Notice, tick the consent box and press Agree & Continue.',
      ] },
      { type: 'figure', id: 'start-privacy-consent' },
      { type: 'steps', items: [
        'Choose your registrant type: Student — Vehicle, Employee, or Fetcher / Drop & Go.',
      ] },
      { type: 'figure', id: 'start-application-form' },
      { type: 'steps', items: [
        'Fill in the form: campus schedule, vehicle details and plate number, and your personal details. Names are stored in uppercase and the plate is formatted automatically.',
        'Attach the required documents and submit. Your application becomes “Pending” until the CDSO reviews it.',
        'Pay the Vehicle Pass fee at the Accounting Office, then use the link in your email to record the Official Receipt: its number and a photograph of the receipt itself. Both are required.',
        'You will be emailed when your application is accepted or rejected.',
      ] },
      { type: 'figure', id: 'start-student-application-form' },
      { type: 'note', text: 'Choosing E-Bike asks for no plate and no conduction number. The college issues the unit a control number (FM-001, FM-002, and so on) when you submit, and that number is your vehicle\'s identity at the gate and on your QR code.' },
      { type: 'note', text: 'Your application cannot be approved until the fee is settled. The photograph of the receipt is what the CDSO checks your OR number against; it is never emailed, and only the reviewer sees it.' },
      { type: 'note', text: 'Under the Data Privacy Act of 2012 (RA 10173), your information is only used for campus vehicle verification.' },
    ],
  },
  {
    id: 'owner-portal',
    title: 'Your Owner Portal',
    category: 'Vehicle Owner',
    roles: ['vehicle_owner'],
    body: [
      { type: 'p', text: 'After logging in you see your pass, your vehicle, any violations, notices from the CDSO and live parking availability.' },
      { type: 'figure', id: 'owner-portal-overview' },
      { type: 'p', text: 'Scroll down for your violation record, announcements and live parking.' },
      { type: 'figure', id: 'owner-portal-violations-parking' },
      { type: 'p', text: 'The parking figures are Available, Parked and Total for your vehicle type, plus Held for event while an event is reserving parking. Parked is what the parking cameras can see, so a zone the CDSO has not finished setting up is not counted in it.' },
      { type: 'note', text: 'Show the Vehicle Access QR code at the gate. Use the one on your dashboard — it always carries your current details.' },
      { type: 'note', text: 'An e-bike shows a Control Number (FM-001, FM-002, and so on) where other vehicles show a plate and conduction number. It is issued by the college and cannot be edited.' },
    ],
  },
  {
    id: 'owner-security',
    title: 'Account Security',
    category: 'Vehicle Owner',
    roles: ['vehicle_owner'],
    body: [
      { type: 'p', text: 'Press Security on your dashboard to manage two-factor verification: see when your authenticator was last used, create a backup code, or move two-factor to a new phone.' },
      { type: 'figure', id: 'owner-security-two-factor' },
      { type: 'note', text: 'Keep a backup code somewhere safe. Without your phone or a backup code, only the CDSO Office can reset your two-factor setup.' },
    ],
  },
  {
    id: 'owner-correct-details',
    title: 'Correcting Your Details',
    category: 'Vehicle Owner',
    roles: ['vehicle_owner'],
    body: [
      { type: 'p', text: 'How a mistake in your details is fixed depends on whether the CDSO has already approved your application.' },
      { type: 'p', text: 'While your application is still pending, you can correct it yourself. Use the “Edit My Details” button in the acknowledgement email you were sent when you applied.' },
      { type: 'steps', items: [
        'Open the acknowledgement email and press “Edit My Details”.',
        'Change what is wrong and save. The correction applies straight away.',
        'A replacement acknowledgement PDF is emailed to you — keep that one instead of the first.',
      ] },
      { type: 'p', text: 'Once your registration is approved, changes need CDSO approval first, because your pass, gate QR code and portal account were all issued from it.' },
      { type: 'steps', items: [
        'On your dashboard, under Personal Information, press “Request a detail change”.',
        'Edit what is wrong and submit it for approval. Nothing changes yet — your pass and QR code still show your current details.',
        'The request shows on your dashboard as “Waiting for CDSO approval”. The CDSO approves or declines it, and you are emailed the outcome either way. A declined request tells you the reason.',
        'You can withdraw a request you have not had a decision on, then file a corrected one.',
      ] },
      { type: 'note', text: 'Your email address, registrant type and campus schedule cannot be changed from either screen — your email is your login, and the CDSO assigns campus days. Ask the CDSO Office for those. If your plate number changes, use the QR code on your dashboard rather than the one in your approval email, because that one still shows the old plate.' },
    ],
  },

  // ── Security ────────────────────────────────────────────────────────────
  {
    id: 'security-gate-login',
    title: 'Signing In at a Gate',
    category: 'Security',
    roles: ['security'],
    guide: ['guard'],
    body: [
      { type: 'p', text: 'Guards sign in against a specific gate so that every scan is attributed to the correct entrance and the right guard.' },
      { type: 'steps', items: [
        'Open the gate sign-in page on the gate computer and pick the gate you are standing at.',
      ] },
      { type: 'figure', id: 'start-guard-select-gate' },
      { type: 'steps', items: [
        'Type your guard email and password and press Login & Clock In — or scan your QR badge.',
        'Your shift starts at that gate. Whoever was on duty there before you is signed out.',
      ] },
      { type: 'figure', id: 'start-guard-credentials' },
      { type: 'note', text: 'A guard can only be on duty at one gate. If you move gates, use Change Shift or sign in again at the new gate so entries are attributed correctly.' },
    ],
  },
  {
    id: 'security-entries',
    title: 'Entry Management (Scanning)',
    category: 'Security',
    roles: ['security'],
    body: [
      { type: 'p', text: 'The Entry Management screen shows the live entry camera and lets you check vehicles in and out.' },
      { type: 'list', items: [
        'Authorized vehicles are recognised automatically from their plate.',
        'Unregistered or denied plates are flagged so you can act on them.',
        'Visitor passes can be issued for guests, and their exit recorded.',
        'Every scan is labelled with who is entering — Student, Employee, Drop & Go / Fetcher, Supplier, Event Organizer, Visitor, or Unregistered — as a coloured tag beside the plate.',
      ] },
      { type: 'figure', id: 'guard-entry-management' },
      { type: 'p', text: 'When a plate is checked, the result appears in the middle of the screen. Compare the owner and vehicle with the car in front of you, then Acknowledge — or Override Entry with a reason if it should be let in anyway.' },
      { type: 'figure', id: 'guard-plate-check-result' },
      { type: 'p', text: 'Scan QR reads more than an owner\'s pass. A visitor slip, a supplier or event pass, and the entry slip given to a vehicle with no plate all scan here. A slip opens rather than acting: you then press Record Exit or Reprint, so looking one up can never let a vehicle out by accident.' },
      { type: 'p', text: 'Expected Today lists the visitors the CDSO scheduled for today. Check In issues the visitor pass with their name and purpose already filled in, and prints the slip; type the plate if the booking has none. When a scheduled visitor’s plate is scanned instead, the result says Expected and its button reads Check In Scheduled Visitor. Rows marked On scan are suppliers — scanning the plate is all they need. Scanning an Expected Visit card (the CDSO’s printed card with a QR) opens the same check-in; a card for another day, or for a visit already checked in or cancelled, says so instead. A scheduled visitor booked without a plate, or arriving in a different car, will not match on the scan: the result notes that visitors are still expected, and the visitor pass form has a Scheduled Visitor list to choose them from.' },
      { type: 'p', text: 'Two more panels appear on the right only when they have something in them. Unrecognized Vehicles Inside lists the no-plate vehicles still on campus, with a Slip button to reprint or exit from, and a Log Exit button. Overstaying lists vehicles past the maximum stay their entry rule allows, with how far over they are.' },
      { type: 'note', text: 'Acknowledge on an overstaying vehicle issues the Time Exceed violation there and then. They may still leave, but may not return until the confiscation ends. A vehicle already recorded today is marked as such instead.' },
      { type: 'note', text: 'If the same plate is read twice within the deduplication window (set by the CDSO), the second scan is ignored to avoid duplicate log rows.' },
    ],
  },
  {
    id: 'security-lookup-by-name',
    title: 'Looking a Vehicle Up by Name',
    category: 'Security',
    roles: ['security'],
    body: [
      { type: 'p', text: 'The lookup box on Entry Management takes three things: the owner\'s name, a plate number, or a conduction number. You do not have to switch modes — it works out which you typed.' },
      { type: 'steps', items: [
        'Type what you have. Anything with numbers in it is treated as a plate or conduction number; anything without is treated as a name.',
        'The button changes to "Search by Name" when the box holds a name, so you can see which way it is about to go before you press it.',
        'A name search lists every matching vehicle with its owner, colour and type. Pick the one at the barrier.',
        'Picking a result runs the normal entry check on that vehicle — exactly as if you had typed its plate.',
      ] },
      { type: 'note', text: 'Searching by name never grants entry on its own. The usual rules — schedule day, allowed hours, confiscation, visitor pass — are still applied to whichever vehicle you pick.' },
    ],
  },
  {
    id: 'security-unrecognized',
    title: 'Vehicles With No Plate',
    category: 'Security',
    roles: ['security'],
    body: [
      { type: 'p', text: 'Some vehicles arrive with no plate and no conduction sticker — a newly delivered unit, a damaged or missing plate. Use the "No Plate?" button beside the lookup box so the vehicle is still on the record.' },
      { type: 'steps', items: [
        'Press "No Plate?" on the Entry Management screen.',
        'Fill in what you can see: the driver\'s name, who is entering, the vehicle type and its colour. Make and model, and a note, are optional.',
        'Press Record Entry. The system gives the vehicle a reference like NP-214 that stands in for the plate in Recent Scans, the Vehicle Log and reports.',
        'Its entry slip prints on the thermal printer. Hand it to the driver — scanning that slip when they leave is the quickest way to close the entry.',
        'The vehicle now counts as inside campus, the same as any scanned entry.',
        'When it leaves, scan its slip, or find it in the "Unrecognized Vehicles Inside" panel on the right and press "Log Exit".',
      ] },
      { type: 'figure', id: 'guard-no-plate' },
      { type: 'note', text: 'The entry is recorded whether or not the slip prints. If the printer was offline, the screen says so and the slip can be reprinted from the Slip button in the Unrecognized Vehicles panel.' },
      { type: 'note', text: 'There is no plate to re-scan, so the slip and that panel are the only ways the exit gets recorded. A vehicle left unclosed stays in the inside-count all day.' },
    ],
  },
  {
    id: 'security-parking',
    title: 'Parking Monitor & Violations',
    category: 'Security',
    roles: ['security'],
    body: [
      { type: 'p', text: 'The Parking screen shows each parking zone through its camera, how many spaces are free, and the accounts that may not park. Bay colours are Free, Occupied and Double parking, and they refresh every 8 seconds.' },
      { type: 'figure', id: 'guard-parking' },
      { type: 'p', text: 'Free, Parked and Capacity come from the parking cameras across every zone of that vehicle type. On campus is a different count — gate entry and exit scans, parked or not — so it is normally the higher of the two. Held appears when an event is reserving spaces.' },
      { type: 'note', text: 'Each zone shows its own status: Monitoring, Camera off, or Not set up. “Not set up” means an admin has not finished that zone\'s setup, so its bay colours may be out of date — check before sending anyone to a bay it shows as free.' },
      { type: 'steps', items: [
        'To record a parking offence, press Issue Violation.',
        'Type the plate, choose the violation type, add any notes, and press Issue Violation.',
      ] },
      { type: 'figure', id: 'guard-issue-violation' },
      { type: 'note', text: 'Override Parking is only available while the CDSO has event mode switched on. It lets a vehicle park when its area is full.' },
    ],
  },
  {
    id: 'security-log',
    title: 'Vehicle Log',
    category: 'Security',
    roles: ['security'],
    body: [
      { type: 'p', text: 'The Vehicle Log lists all scans recorded at your gate. Filter by decision, or pick another date (you cannot pick a future date).' },
      { type: 'figure', id: 'guard-vehicle-log' },
    ],
  },

  // ── CDSO (admin) ────────────────────────────────────────────────────────
  {
    id: 'cdso-dashboard',
    title: 'Dashboard & Notifications',
    category: 'CDSO',
    roles: ['admin'],
    body: [
      { type: 'p', text: 'The dashboard summarises the system at a glance and refreshes live. The sidebar on the left is the same on every CDSO screen.' },
      { type: 'list', items: [
        'Summary tiles — accounts, registered vehicles, applications to review, unresolved violations, visitor passes, and today’s scans.',
        'Charts — applications, owner types, vehicle kinds, today’s gate activity, 30-day violations, and weekly entries and capacity.',
      ] },
      { type: 'figure', id: 'cdso-dashboard-layout' },
      { type: 'p', text: 'The bell beside SLC CDSO shows how many notifications you have not read. Click it to see new registrations, uploaded receipts and violations.' },
      { type: 'figure', id: 'cdso-notifications' },
    ],
  },
  {
    id: 'cdso-registrations',
    title: 'Reviewing Vehicle Registrations',
    category: 'CDSO',
    roles: ['admin'],
    body: [
      { type: 'p', text: 'Online registrations arrive as “Pending”. Review each one and accept or reject it; the applicant is emailed the outcome.' },
      { type: 'steps', items: [
        'Open Management › Vehicle Registration. Set the status filter to Pending Review to see what is waiting.',
        'Press the eye (View) button on an application.',
      ] },
      { type: 'figure', id: 'cdso-vehicle-registration' },
      { type: 'steps', items: [
        'Check the applicant, payment, campus days and vehicle details, and the documents they attached.',
      ] },
      { type: 'figure', id: 'cdso-registration-review' },
      { type: 'steps', items: [
        'Scroll to the bottom. Check the Official Receipt number against the photograph of the receipt, which is shown beside it under Payment.',
        'Press Confirm & Accept to issue the pass, or Reject and give the reason.',
      ] },
      { type: 'figure', id: 'cdso-registration-decision' },
      { type: 'note', text: 'A registration whose fee is unsettled cannot be approved. Where there is no OR number and the applicant is not marked Exempt, a notice replaces the OR box and Confirm & Accept stays disabled. Enter the number once the fee is paid at the counter, or set the payment to Exempt where nothing is owed.' },
      { type: 'note', text: 'A row with no receipt photograph says so plainly, so “nothing was uploaded” is not mistaken for “the picture has not loaded”. Click a photograph to enlarge it.' },
      { type: 'note', text: 'Reports exported from this screen carry the filters the table is showing — registrant type, payment and status — as well as the date range, and the bar names them beside the label.' },
      { type: 'note', text: 'For walk-in applicants you can register directly — these are accepted immediately without a pending step.' },
    ],
  },
  {
    id: 'cdso-change-requests',
    title: 'Approving Detail Changes',
    category: 'CDSO',
    roles: ['admin'],
    body: [
      { type: 'p', text: 'An approved registration already issued a vehicle pass, a gate QR code and a portal account, so an owner cannot edit it themselves. They file a request and it waits in User Management › Detail Change Requests.' },
      { type: 'steps', items: [
        'Open Management › User Management and choose the Detail Change Requests tab. The badge counts waiting requests.',
        'Each card shows who asked, and every field as “what it says now → what they want”.',
        'Approve & apply writes the change to the registration, the vehicle record and their account together, and emails them.',
        'Decline needs a reason, which is emailed to them verbatim — tell them what to do instead.',
      ] },
      { type: 'figure', id: 'cdso-detail-change-requests' },
      { type: 'note', text: 'A request can be refused on approval if the detail it asks for is no longer available — a plate someone else has registered since it was filed, for example. The message names the field. Decline it and ask the owner to file a corrected request.' },
      { type: 'p', text: 'Applications that are still pending are not in this queue. Nobody has reviewed them yet, so the applicant corrects those directly from the link in their acknowledgement email; you get a notification when they do.' },
    ],
  },
  {
    id: 'cdso-users',
    title: 'User Management',
    category: 'CDSO',
    roles: ['admin'],
    body: [
      { type: 'p', text: 'Manage security guards, vehicle owners and the CDSO account. Names are saved in uppercase and emails in lowercase automatically.' },
      { type: 'list', items: [
        'Add User — create a security guard or vehicle-owner account. A temporary password is emailed.',
        'Use the role tabs, search and status filter to find an account.',
        'The ⋮ menu on each row edits, disables or enables, resets two-factor, prints a guard QR badge, or deletes the account.',
      ] },
      { type: 'figure', id: 'cdso-user-management' },
    ],
  },
  {
    id: 'cdso-devices',
    title: 'Cameras (Device Management)',
    category: 'CDSO',
    roles: ['admin'],
    body: [
      { type: 'p', text: 'Register the IP cameras that read plates at the gates and watch the parking areas.' },
      { type: 'steps', items: [
        'Open Management › Device Management and press Add Device.',
        'Enter the camera’s name, IP address, device ID and password, and choose whether it covers an entry gate (and which) or parking.',
        'Press Test to check the camera answers, then Connect to see its live picture under Live Feeds.',
      ] },
      { type: 'figure', id: 'cdso-device-management' },
    ],
  },
  {
    id: 'cdso-suppliers',
    title: 'Visits and Suppliers',
    category: 'CDSO',
    roles: ['admin'],
    body: [
      { type: 'p', text: 'Management › Visits and Suppliers has two tabs. Suppliers holds the supplier companies and their plates, which are admitted automatically when scanned at the gate, within the supplier delivery window. Scheduled Visits books the visitors and suppliers expected on a given date: press Schedule Visit, fill in who, when and why, and add a plate if it is known.' },
      { type: 'p', text: 'The Scheduled Visits table works like User Management. The tiles count visits expected today, upcoming, arrived and no-shows. The tabs above the table narrow it by status (All, Expected Today, Upcoming, Arrived, No-show, Archived), the search box finds a visitor, plate, purpose, supplier or SV number, and the category list narrows it further. The Scheduled Visits Report bar prints exactly what the table shows — the same tab, search and category — to PDF or Excel, and its two date boxes narrow both the table and the report to a range of expected dates, future dates included.' },
      { type: 'p', text: 'Each row’s ⋮ menu holds its actions: Print Expected Visit Card, Mark Arrived (or Undo Arrived), Reschedule, and Archive — or Restore on an archived visit. The Expected Visit card prints on the thermal printer with the visitor, the date, the purpose and a QR. Give it to the visitor or keep it at the gate: on the day, the guard scans it to open the check-in. It is notice, not a pass — nobody is let in on the card alone.' },
      { type: 'p', text: 'On its date, a booking appears in the guard’s Expected Today panel. The guard checks the visitor in from there, or from the scan result when their plate is read, which fills the visitor pass from the booking and prints the slip with the booking’s reference, who arranged it, and what for. The visit is marked Arrived, with the time and pass number, once the slip prints. A supplier plate on the roster needs no pass: scanning it admits the vehicle and marks the visit arrived, and its supplier slip carries the booking too.' },
      { type: 'note', text: 'A booking is notice, not permission. Nobody gets in on it alone — the visitor still needs a pass, and every usual check, including a visitor penalty, still applies. A past visit that never arrived shows as No-show. Mark Arrived is only for correcting the list by hand.' },
      { type: 'p', text: 'Any visit that has not arrived yet, a No-show included, can be moved with Reschedule: the same booking, with the same SV number, appears in Expected Today on its new date. Visits are archived rather than deleted. Archive takes an optional reason and moves the visit to the Archived tab, where it stays on record with who archived it and when; the gate stops seeing it. Restore brings it back.' },
      { type: 'figure', id: 'cdso-suppliers' },
      { type: 'note', text: 'Print Supplier Pass on a registered plate prints a standing pass for that vehicle on the thermal printer, to be kept in the cab. The guard scans its QR like a registered vehicle\'s: the first scan records the entry, the next the exit.' },
    ],
  },
  {
    id: 'cdso-operations',
    title: 'Operations Center',
    category: 'CDSO',
    roles: ['admin'],
    body: [
      { type: 'p', text: 'Operations › Operations Center watches every gate, guard and camera at once. It has three tabs.' },
      { type: 'p', text: 'Live Monitor — the camera pictures and the latest scans at each gate, with the guard on duty.' },
      { type: 'figure', id: 'cdso-operations-center' },
      { type: 'p', text: 'Guards — each guard’s activity today and the recent shift history.' },
      { type: 'figure', id: 'cdso-operations-guards' },
      { type: 'p', text: 'Gate Records — visitors still on campus, vehicles that left through a different gate than they entered, and confiscated accounts.' },
      { type: 'figure', id: 'cdso-operations-gate-records' },
    ],
  },
  {
    id: 'cdso-parking',
    title: 'Parking Spaces',
    category: 'CDSO',
    roles: ['admin'],
    body: [
      { type: 'p', text: 'Operations › Parking Space Management shows each parking zone, its camera, and which bays are free or taken. Each zone tab names the camera that watches it.' },
      { type: 'figure', id: 'cdso-parking-spaces' },
      { type: 'p', text: 'Three steps make a zone monitored, and the banner on the zone tells you which one is outstanding:' },
      { type: 'steps', items: [
        'Capture a reference image with the lot empty — press Edit Parking Slots, then Use as Reference Image. It is what every bay is scored against, so the lot has to be clear.',
        'Draw the slots on it. Use Box for rectangular bays or Pen for angled ones, then Save Layout.',
        'Press Start Monitoring. That stores the reference image as the zone\'s empty baseline and the detector begins scoring the bays.',
      ] },
      { type: 'figure', id: 'cdso-parking-edit-slots' },
      { type: 'p', text: 'Free, Parked and Capacity cover every zone of the selected vehicle type and come from the cameras. On Campus counts gate scans instead, parked or not, so it is normally higher — drop-offs, vehicles still circling, and vehicles parked where no camera watches.' },
      { type: 'note', text: 'There is no detection on/off switch. A zone with a camera and a finished setup is always monitored, and the server restarts the detector by itself. The badge beside the mode buttons says which state it is in: Monitoring bays, Starting camera, or Not monitored yet.' },
      { type: 'note', text: 'Replacing the reference image later marks the baseline stale, and the banner asks you to update it. Until you do, the zone is scored against the older picture.' },
      { type: 'note', text: 'Where a camera sends two views stacked in one frame, the editor asks which view the zone covers before any bay is drawn, so a bay cannot straddle the seam between them.' },
    ],
  },
  {
    id: 'cdso-events',
    title: 'Events & Parking',
    category: 'CDSO',
    roles: ['admin'],
    body: [
      { type: 'p', text: 'An event is recorded with its date, the hours it runs, and how much of campus parking it is expected to fill. Events live under Parking Space Management › Events.' },
      { type: 'list', items: [
        'Time — give a start and end time, or leave both blank for an all-day event.',
        'Parking taken up — a fraction rather than a number: about 1/4, 1/3, 1/2, 2/3, 3/4, or all of parking.',
        'Organizer plates — while the event is running, an unregistered organizer plate is let in at the gate and an Event Slip prints; scan its QR to record the exit. A registered vehicle on the list keeps its usual rules and is only labelled as an organizer.',
      ] },
      { type: 'figure', id: 'cdso-events' },
      { type: 'p', text: 'The declared share is held back from the free-space count while the event is actually running, so the gate stops admitting before the bays the event needs are taken. Outside those hours nothing is held back — an evening event does not make the car park read as half gone in the morning.' },
      { type: 'note', text: 'Print Event Pass on an organizer plate prints a standing pass for the day on the thermal printer. The guard scans its QR at the gate like a registered vehicle\'s: the first scan records the entry, the next the exit.' },
      { type: 'note', text: 'Held spaces are reported separately from occupied ones. Nobody has parked in them yet, so counting them as occupied would claim vehicles that are not there. Owners see the event named on their parking view, so the smaller free count is explained rather than looking like a miscount.' },
    ],
  },
  {
    id: 'cdso-violations',
    title: 'Violations',
    category: 'CDSO',
    roles: ['admin'],
    body: [
      { type: 'p', text: 'Operations › Violations lists every recorded offence. Offences are counted per owner account, and each one withdraws campus access for longer:' },
      { type: 'list', items: [
        '1st offence — the account is confiscated for 1 week.',
        '2nd offence — confiscated for 2 weeks.',
        '3rd offence — confiscated for the rest of the registration period, and the person may not register again unless the CDSO allows it.',
      ] },
      { type: 'figure', id: 'cdso-violations' },
      { type: 'note', text: 'The report bar\'s Date From / Date To narrow the table on screen as well as the exported file, so what you are looking at is what the report will contain. Where they are set, they override the Today / Week / Month / Year buttons.' },
      { type: 'note', text: 'Lift a violation that should never have been issued (a false alarm). It stops counting, and the owner’s remaining offences are renumbered.' },
    ],
  },
  {
    id: 'cdso-vehicle-log',
    title: 'Vehicle Log',
    category: 'CDSO',
    roles: ['admin'],
    body: [
      { type: 'p', text: 'The Vehicle Log is the gate history: every scan, entry, and exit recorded by security, across all gates. Each guard sees only their own gate — you see all of them in one place.' },
      { type: 'steps', items: [
        'Pick a date range with the quick period buttons, or set the dates yourself.',
        'Narrow by gate, status or category.',
        'Search by plate number, vehicle owner, or the guard on duty.',
        'Click Export PDF for a branded SLC report, or Export Excel for a spreadsheet. Both cover every row matching your filters, not just the page on screen.',
      ] },
      { type: 'figure', id: 'cdso-vehicle-log' },
      { type: 'note', text: 'A PDF is confirmed before it is made: the dialog restates the period, the filters in force and how many rows match, and says so when nothing matches. It then opens in a new tab rather than downloading — the viewer\'s own toolbar still offers Save and Print. Excel is not confirmed and still downloads.' },
      { type: 'p', text: 'A vehicle that entered and left shows as one row with its exit time and how long it stayed; one still on campus is marked “Still inside”.' },
    ],
  },
  {
    id: 'cdso-rules',
    title: 'Entry Rules, Registration Period & Access Mode',
    category: 'CDSO',
    roles: ['admin'],
    body: [
      { type: 'p', text: 'System › Rule Constraints decides when vehicles may enter and when applications are accepted.' },
      { type: 'p', text: 'Entry Rules — one rule per registrant type, with its allowed days, hours and any maximum stay.' },
      { type: 'figure', id: 'cdso-rule-constraints' },
      { type: 'p', text: 'Registration Period — the dates the online application form is open. Only one period can be active. Extend Duration on a running period opens it to move the end date; the editor is headed “Extending period” and saves with Save New Dates. (Edit on an entry-type rule is a different thing — that one opens allowed days, hours and the maximum stay.)' },
      { type: 'figure', id: 'cdso-registration-period' },
      { type: 'p', text: 'Access Mode — Open Campus lets every vehicle in regardless of rules. Use it for open events or graduation and switch it off afterwards.' },
      { type: 'figure', id: 'cdso-access-mode' },
    ],
  },
  {
    id: 'cdso-reports',
    title: 'Reports & Audit Log',
    category: 'CDSO',
    roles: ['admin'],
    body: [
      { type: 'p', text: 'The Audit Log is the system’s report centre for management actions — accounts, records, and overrides. Gate scans and entry/exit history live on the Vehicle Log.' },
      { type: 'steps', items: [
        'Choose a date range with the quick period buttons, or pick exact dates — future dates are blocked.',
        'Filter by action type or search by actor or details.',
        'Click Export PDF for a branded SLC report, or Export Excel for a formatted spreadsheet. Both include every row matching your filters.',
      ] },
      { type: 'figure', id: 'cdso-audit-log' },
      { type: 'note', text: 'A PDF is confirmed before it is made — the dialog restates the period, the filters and how many rows match — and then opens in a new tab instead of downloading. Excel still downloads.' },
      { type: 'note', text: 'Every exported PDF ends with a Prepared by / Approved by signature block. Prepared by is whoever pressed the button; Approved by is set in System Settings › Report Signatories.' },
    ],
  },
  {
    id: 'cdso-settings',
    title: 'System Settings',
    category: 'CDSO',
    roles: ['admin'],
    body: [
      { type: 'p', text: 'System › System Settings holds the system-wide controls, grouped into five tabs. A tab holding unsaved changes carries a dot, and Save stays disabled until something actually differs from what is stored.' },
      { type: 'p', text: 'Accounts & Fees — how long owner accounts last, and the Vehicle Pass fees.' },
      { type: 'figure', id: 'cdso-settings-accounts-fees' },
      { type: 'p', text: 'Gates & Scanning — add or deactivate gates, and the scan deduplication window.' },
      { type: 'figure', id: 'cdso-settings-gates-scanning' },
      { type: 'p', text: 'Parking — how long a vehicle must stay still to count as parked or double-parked, and parking notices broadcast to every owner.' },
      { type: 'figure', id: 'cdso-settings-parking' },
      { type: 'p', text: 'Data & Backup — data retention and backups; see the next topic.' },
      { type: 'figure', id: 'cdso-settings-report-signatories' },
      { type: 'p', text: 'Report Signatories — who signs the PDF reports. Approved by is set here, because the head of office does not sign in to run every report and the post changes hands. Prepared by is normally whoever pressed the export button, taken from the account signed in; fill its two boxes in only to sign every report with one name instead. The captions above each signature can be reworded for an office that files these as “Submitted by” or “Noted by”, and a preview shows the block as it will be printed.' },
      { type: 'note', text: 'Leaving the approver\'s name blank is a real choice, not a missing value: the report then prints an empty ruled line to be signed by hand. Either way the report footer still records the account that exported it.' },
    ],
  },
  {
    id: 'cdso-backup',
    title: 'Backup & Restore',
    category: 'CDSO',
    roles: ['admin'],
    body: [
      { type: 'p', text: 'Protect your data with regular backups. Everything below lives in System Settings › Data & Backup and is available to the CDSO only.' },
      { type: 'steps', items: [
        'Download Backup — a save dialog opens first so you choose the folder and filename, then a complete JSON snapshot of all system data is written there.',
        'Automatic backups — set Off, Hourly, Daily, Weekly or Monthly and the server saves a snapshot by itself, before each day’s retention purge runs.',
        'Automatic backups to keep — how many the server holds before the oldest is deleted. Ten of them is about ten hours of history on Hourly but ten months on Monthly, so raise it if you pick a fast schedule. Pre-restore snapshots are never removed by this.',
        'Backups on the server — every saved file, with what wrote it and when. Save As… downloads one to a folder you pick, Restore loads it back, and the bin deletes it.',
        'Restore from Backup — upload a backup file from your own computer and confirm.',
      ] },
      { type: 'figure', id: 'cdso-settings-data-backup' },
      { type: 'note', text: 'A restore is a merge, not a wipe: records in the backup are written over the matching live records, and nothing is deleted. Where a live record already holds a value the backup needs — the same email address, the same camera number — the live record is moved aside rather than removed, so restoring onto a running system and onto a fresh installation both work.' },
      { type: 'note', text: 'Before any restore, the system automatically saves a safety snapshot of the current data, and the whole restore is rolled back if anything goes wrong — so a bad file never leaves the system half-updated.' },
      { type: 'note', text: 'Backups on the server live with the app. Keep your own downloaded copy of anything you would not want to lose along with it.' },
    ],
  },

  // ── Campus setup (CDSO / IT) ────────────────────────────────────────────
  {
    id: 'setup-install',
    title: 'Installing the Campus System',
    category: 'Campus Setup',
    roles: ['admin'],
    body: [
      { type: 'p', text: 'The on-campus server is installed with SLC-Smart-Parking-Campus-Setup.exe on the computer the gate terminals connect to. Run it and follow the wizard.' },
      { type: 'steps', items: ['Accept the license agreement.'] },
      { type: 'figure', id: 'installer-license' },
      { type: 'steps', items: ['Keep the default install folder unless your IT office says otherwise.'] },
      { type: 'figure', id: 'installer-destination' },
      { type: 'steps', items: ['Keep Full installation for a new gate computer.'] },
      { type: 'figure', id: 'installer-components' },
      { type: 'steps', items: ['Keep port 8000 unless another program already uses it.'] },
      { type: 'figure', id: 'installer-deployment-options' },
      { type: 'steps', items: ['Check which prerequisites are already installed.'] },
      { type: 'figure', id: 'installer-prerequisites' },
      { type: 'steps', items: ['Keep the Start Menu folder.'] },
      { type: 'figure', id: 'installer-start-menu' },
      { type: 'steps', items: ['Choose the shortcuts you want. “Open the launcher when this computer starts” is recommended for a dedicated gate computer.'] },
      { type: 'figure', id: 'installer-additional-tasks' },
      { type: 'steps', items: ['Check the summary and press Install. The launcher opens when Setup finishes.'] },
      { type: 'figure', id: 'installer-ready' },
      { type: 'note', text: 'The first launch downloads the application and builds its Python environment (about 6 GB). Keep the computer online until it finishes.' },
    ],
  },
  {
    id: 'setup-launcher',
    title: 'Running the Campus Launcher',
    category: 'Campus Setup',
    roles: ['admin'],
    body: [
      { type: 'p', text: 'The launcher window runs the server that the gate terminals connect to. Leave it open while the system is in use.' },
      { type: 'figure', id: 'launcher-launcher-window' },
      { type: 'note', text: 'When an update is found, press “Update and restart” at a quiet moment — restarting drops the camera feeds briefly.' },
    ],
  },
]
