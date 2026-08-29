// User-manual content for the searchable Help section.
//
// Each topic is tagged with the roles that should see it. Body blocks support:
//   { type: 'p',     text }              → paragraph
//   { type: 'steps', items: [...] }      → numbered steps
//   { type: 'list',  items: [...] }      → bulleted list
//   { type: 'note',  text }              → highlighted tip/warning
//   { type: 'image', src, alt, caption } → screenshot (added in a later pass)
//
// Images are intentionally omitted for now — add an 'image' block to any topic
// and drop the screenshot into src/assets/help/ when ready. The renderer already
// supports them, so the manual stays "image-supported" by design.

export const HELP_ROLE_LABELS = {
  admin: 'CDSO',
  security: 'Security',
  vehicle_owner: 'Vehicle Owner',
}

export const HELP_TOPICS = [
  // ── Common ──────────────────────────────────────────────────────────────
  {
    id: 'logging-in',
    title: 'Logging In & Your Account',
    category: 'Getting Started',
    roles: ['admin', 'security', 'vehicle_owner'],
    body: [
      { type: 'p', text: 'Log in with the email address and password issued to you. Emails are not case-sensitive.' },
      { type: 'steps', items: [
        'Open the login page and enter your email and password.',
        'If this is your first login with a temporary password, you will be asked to set a new one before continuing.',
        'Use “Forgot Password” on the login page if you cannot access your account — a reset link is emailed to you.',
      ] },
      { type: 'note', text: 'Security guards can also log in at a gate using the QR badge issued after their first password change.' },
    ],
  },
  {
    id: 'status-messages',
    title: 'Understanding Status Messages',
    category: 'Getting Started',
    roles: ['admin', 'security', 'vehicle_owner'],
    body: [
      { type: 'p', text: 'After every action the system shows a short pop-up message in the corner of the screen:' },
      { type: 'list', items: [
        'Green = success — the action completed.',
        'Red = error — something went wrong; the message explains what.',
        'Amber = warning — the action partly succeeded or needs attention.',
      ] },
    ],
  },

  // ── Vehicle Owner ───────────────────────────────────────────────────────
  {
    id: 'owner-register',
    title: 'Registering Your Vehicle',
    category: 'Vehicle Owner',
    roles: ['vehicle_owner'],
    body: [
      { type: 'p', text: 'Vehicle registration is submitted through the public registration form and reviewed by the CDSO before it is approved.' },
      { type: 'steps', items: [
        'Fill in your personal details, vehicle details, and plate number. Names are stored in uppercase and the plate is formatted automatically.',
        'Read the Data Privacy Consent and tick the checkbox — you cannot submit without agreeing.',
        'Submit the form. Your application status becomes “Pending” until the CDSO reviews it.',
        'You will receive an email when your registration is accepted or denied.',
      ] },
      { type: 'note', text: 'Under the Data Privacy Act of 2012 (RA 10173), your information is only used for campus vehicle verification.' },
    ],
  },
  {
    id: 'owner-portal',
    title: 'Your Owner Portal',
    category: 'Vehicle Owner',
    roles: ['vehicle_owner'],
    body: [
      { type: 'p', text: 'After logging in you can see your registered vehicle, its status, any violations, and campus notices from the CDSO.' },
      { type: 'list', items: [
        'Registration status — pending, authorized, or denied.',
        'Violations — any recorded against your vehicle, with instructions to settle them.',
        'Notices — announcements broadcast by the CDSO.',
      ] },
    ],
  },

  // ── Security ────────────────────────────────────────────────────────────
  {
    id: 'security-gate-login',
    title: 'Signing In at a Gate',
    category: 'Security',
    roles: ['security'],
    body: [
      { type: 'p', text: 'Guards sign in against a specific gate so that every scan is attributed to the correct entrance.' },
      { type: 'steps', items: [
        'Choose your gate on the gate-login page (or scan your QR badge at that gate).',
        'Your session is tied to that gate until you log out.',
      ] },
      { type: 'note', text: 'If you switch gates, log out and sign in again at the new gate so entries are attributed correctly.' },
    ],
  },
  {
    id: 'security-entries',
    title: 'Entry Management (Scanning)',
    category: 'Security',
    roles: ['security'],
    body: [
      { type: 'p', text: 'The Entry Management screen shows live camera scans and lets you record vehicle entries and exits.' },
      { type: 'list', items: [
        'Authorized vehicles are recognised automatically from their plate.',
        'Unregistered or denied plates are flagged so you can act on them.',
        'Visitor passes can be issued for guests, and their exit recorded.',
      ] },
      { type: 'note', text: 'If the same plate is read twice within the deduplication window (set by the CDSO), the second scan is ignored to avoid duplicate log rows.' },
    ],
  },
  {
    id: 'security-log',
    title: 'Vehicle Log',
    category: 'Security',
    roles: ['security'],
    body: [
      { type: 'p', text: 'The Vehicle Log lists all scans recorded at your gate. Filter by date (you cannot pick a future date) or action type to find a specific entry.' },
    ],
  },

  // ── CDSO (admin) ────────────────────────────────────────────────────────
  {
    id: 'cdso-dashboard',
    title: 'Dashboard & Analytics',
    category: 'CDSO',
    roles: ['admin'],
    body: [
      { type: 'p', text: 'The dashboard summarises the system at a glance and refreshes live.' },
      { type: 'list', items: [
        'KPI strip — total users, registered vehicles, pending reviews, open violations, visitor passes, and today’s scans.',
        'Charts — registration status, registered categories, today’s entry outcomes, entries by day, and registrations vs. daily capacity.',
        'Recent activity — the latest CDSO and Security actions.',
      ] },
    ],
  },
  {
    id: 'cdso-registrations',
    title: 'Reviewing Vehicle Registrations',
    category: 'CDSO',
    roles: ['admin'],
    body: [
      { type: 'p', text: 'Online registrations arrive as “Pending”. Review each one and accept or deny it; the owner is emailed the outcome.' },
      { type: 'steps', items: [
        'Open Vehicle Registration and select a pending application.',
        'Check the applicant and vehicle details and any attached documents.',
        'Accept to authorize campus entry, or deny with a reason.',
      ] },
      { type: 'note', text: 'For walk-in applicants you can register directly — these are accepted immediately without a pending step.' },
    ],
  },
  {
    id: 'cdso-users',
    title: 'User Management',
    category: 'CDSO',
    roles: ['admin'],
    body: [
      { type: 'p', text: 'Manage security guards and the CDSO account. Names are saved in uppercase and emails in lowercase automatically.' },
      { type: 'list', items: [
        'Add a Security Guard — a temporary password is emailed and a QR badge is generated after their first login.',
        'Replace CDSO — creates a new CDSO account and removes the current one; you are logged out immediately.',
        'Enable, disable, or delete accounts, and view each user’s ID/badge QR.',
      ] },
    ],
  },
  {
    id: 'cdso-violations',
    title: 'Violations',
    category: 'CDSO',
    roles: ['admin'],
    body: [
      { type: 'p', text: 'Record and manage vehicle violations. Owners are notified and can see the violation in their portal.' },
      { type: 'steps', items: [
        'Issue the official violation report to the owner.',
        'If a fee applies, the owner settles it and presents the Official Receipt (OR).',
        'Clear the violation once resolved — the owner’s entry access is restored.',
      ] },
    ],
  },
  {
    id: 'cdso-reports',
    title: 'Reports & Audit Log',
    category: 'CDSO',
    roles: ['admin'],
    body: [
      { type: 'p', text: 'The Audit Log is the system’s report centre for management actions — accounts, records, and overrides. Gate scans and entry/exit history live on the Vehicle Log (next topic).' },
      { type: 'steps', items: [
        'Use the Date From / Date To fields (or the quick period buttons) to choose a range — future dates are blocked.',
        'Filter by action type or search by actor, plate, or details.',
        'Click Export PDF for a branded SLC report, or Export Excel for a formatted spreadsheet. Both include every row matching your filters.',
      ] },
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
        'Pick a date range with the quick period buttons, or set Date From / Date To yourself.',
        'Narrow by gate or by status (Authorized, Denied, Visitor, Unreadable, Exited).',
        'Search by plate number, vehicle owner, or the guard on duty.',
        'Click Export PDF for a branded SLC report, or Export Excel for a spreadsheet. Both cover every row matching your filters, not just the page on screen.',
      ] },
      { type: 'p', text: 'A vehicle that entered and left shows as one row with its exit time and how long it stayed; one still on campus is marked “Still inside”.' },
    ],
  },
  {
    id: 'cdso-settings',
    title: 'System Settings',
    category: 'CDSO',
    roles: ['admin'],
    body: [
      { type: 'p', text: 'System Settings holds the system-wide controls:' },
      { type: 'list', items: [
        'Data Retention — how long logs and violations are kept before automatic deletion.',
        'Scan Deduplication Window — how long duplicate reads of the same plate are suppressed.',
        'Campus Gates — add or deactivate gates as the campus expands.',
        'Parking Notices — broadcast an announcement to all owners by email and portal.',
        'Backup & Restore — see the next topic.',
      ] },
    ],
  },
  {
    id: 'cdso-backup',
    title: 'Backup & Restore',
    category: 'CDSO',
    roles: ['admin'],
    body: [
      { type: 'p', text: 'Protect your data with regular backups. Everything below lives in System Settings and is available to the CDSO only.' },
      { type: 'steps', items: [
        'Download Backup — a save dialog opens first so you choose the folder and filename, then a complete JSON snapshot of all system data is written there.',
        'Automatic backups — set Off, Hourly, Daily, Weekly or Monthly and the server saves a snapshot by itself, before each day’s retention purge runs.',
        'Automatic backups to keep — how many the server holds before the oldest is deleted. Ten of them is about ten hours of history on Hourly but ten months on Monthly, so raise it if you pick a fast schedule. Pre-restore snapshots are never removed by this.',
        'Backups on the server — every saved file, with what wrote it and when. Save As… downloads one to a folder you pick, Restore loads it back, and the bin deletes it.',
        'Restore from Backup — upload a backup file from your own computer and confirm.',
      ] },
      { type: 'note', text: 'Before any restore, the system automatically saves a safety snapshot of the current data, and the whole restore is rolled back if anything goes wrong — so a bad file never leaves the system half-updated.' },
      { type: 'note', text: 'Backups on the server live with the app. Keep your own downloaded copy of anything you would not want to lose along with it.' },
    ],
  },
]
