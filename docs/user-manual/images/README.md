# User manual images

**This file is generated.** `shots/index.mjs` writes it from the figure definitions in
`shots/shots.mjs` and the prose in `shots/notes.mjs`; the Word document is then built from
it. Edit those two files and re-run the capture, not this one — a hand edit here is lost on
the next run.

Annotated screenshots for the user manual. Each image has numbered callouts, and the same
numbers are explained in the list below it.

The web screenshots were taken against a separate demo database filled with
fictional people, plates and records. Camera pictures in them are sample
illustrations, not real camera footage. Installer screens come from the real
setup wizard, and the launcher screen from the installed launcher, with its
activity log pixelated.


## Getting Started

Signing in, password reset, applying for a vehicle pass, and guard sign-in at a gate.

### Signing in

![Signing in](01-getting-started/01-login-page.png)

Sign in here with the email address and password issued to you.

1. **Email**: Type the email address registered to your account. It is not case-sensitive.
2. **Password**: Enter your password. The eye icon shows or hides what you typed.
3. **Remember Me**: Keeps you signed in on this computer. Leave it unticked on shared computers.
4. **Forgot Password**: Sends a password-reset link to your email.
5. **Login**: Signs you in. You are then asked for a code from your authenticator app.
6. **Apply for a Vehicle Pass**: Starts the online vehicle pass application. No account is needed to apply.
7. **Privacy Policy & Terms**: Opens the data privacy notice and the vehicle pass terms.
8. **Need help signing in?**: Opens the step-by-step guide with pictures.
9. **Help**: The same guide, from the top of the page.

### Two-factor verification

![Two-factor verification](01-getting-started/02-two-factor-code.png)

After your password, confirm a 6-digit code from the authenticator app on your phone.

1. **Verification code**: Open your authenticator app (e.g. Google Authenticator) and type the 6-digit code shown for SLC Vehicle Management. It submits by itself on the sixth digit.

### Resetting a forgotten password

![Resetting a forgotten password](01-getting-started/03-forgot-password.png)

Request a reset link when you cannot sign in.

1. **Email**: Enter the email address of your account.
2. **Send Reset Link**: Emails you a link to choose a new password. The link expires, so use it soon.
3. **Back to Login**: Returns to the login page.

### Applying for a vehicle pass: privacy consent

![Applying for a vehicle pass: privacy consent](01-getting-started/04-privacy-consent.png)

Every application starts with the Data Privacy Notice (RA 10173).

1. **What is collected and why**: Read what information the college collects and how it is used before applying.
2. **Consent checkbox**: Tick to give your consent. Agree & Continue stays disabled until you do.
3. **Decline**: Cancels the application and returns to the login page.
4. **Agree & Continue**: Opens the application form.

### Applying for a vehicle pass: the form

![Applying for a vehicle pass: the form](01-getting-started/05-application-form.png)

The public application form, used by students, employees and fetchers.

1. **Registration window**: Shows whether applications are being accepted, and the dates of the current window.
2. **Registrant type**: Choose Student — Vehicle, Employee, or Fetcher / Drop & Go. The form that follows asks only for what that type needs.
3. **Back to Login**: Leave the application without submitting.

### Applying for a vehicle pass: student form

![Applying for a vehicle pass: student form](01-getting-started/05b-student-application-form.png)

The details a student applicant fills in.

1. **Form type**: The kind of application you are filling in.
2. **Campus Schedule**: Choose one schedule (Mon · Wed · Fri or Tue · Thu · Fri). Slots are first come, first served; a full schedule cannot be selected.
3. **Registrant type**: Switch here if you picked the wrong type.
4. **Vehicle identification**: Plate number, vehicle type and colour. Tick the box instead if the vehicle is brand-new and only has a conduction number. Choosing E-Bike asks for neither: the college issues the unit a control number (FM-001, FM-002, and so on) on submission, and that number is what the gate, the pass and the QR code use in place of a plate.
5. **Personal information**: Your name, then contact details further down. Scroll to the end to attach the required documents and submit.

> After the form is submitted, the applicant pays the Vehicle Pass fee at the Accounting Office
> and records it on the payment page: the Official Receipt number **and a photograph of the
> receipt**, which is required. The photograph backs up the number, which on its own is only a
> claim. It is never emailed, and it is never shown outside the CDSO review screen.

### Guard sign-in: choosing a gate

![Guard sign-in: choosing a gate](01-getting-started/06-guard-select-gate.png)

Security guards sign in at the gate terminal, not through the main login page.

1. **Gate list**: Pick the gate you are standing at. Your shift and every scan are recorded against this gate.
2. **Help**: Opens the guard sign-in guide with pictures.

### Guard sign-in: clocking in

![Guard sign-in: clocking in](01-getting-started/07-guard-credentials.png)

Sign in with your guard account or scan your QR badge to start your shift.

1. **Change gate**: Go back if you picked the wrong gate.
2. **Selected gate**: The gate you will be clocked in at.
3. **Email and password**: Your guard account credentials.
4. **Login & Clock In**: Signs you in and starts your shift at this gate. Whoever was on duty here is signed out.
5. **Forgot password?**: Sends a reset link to your email.
6. **QR badge**: Appears when your account has a badge. Scan it with a USB scanner or the camera instead of typing the password.
7. **Help**: Opens the guard sign-in guide with pictures.


## CDSO (Administrator)

Every screen available to the CDSO (administrator) account.

### CDSO dashboard and screen layout

![CDSO dashboard and screen layout](02-cdso-admin/01-dashboard-layout.png)

Every CDSO screen shares this sidebar. The dashboard summarises the whole system.

1. **Main menu**: Dashboard, then three groups: Management, Operations and System. Click a group to expand it.
2. **Notifications**: The red badge counts unread alerts about registrations and violations. Click to open the list.
3. **Refresh**: Reloads every number on the dashboard.
4. **Summary tiles**: Accounts, registered vehicles, applications to review, unresolved violations, visitor passes and today’s gate scans.
5. **Breakdown charts**: Applications, owner types, vehicle kinds, today’s gate activity and 30-day violations. Scroll down for the weekly charts.
6. **Help, Policy and Security**: Help opens the in-app user manual. Security manages your two-factor authentication.
7. **Change Password**: Set a new password for your account.
8. **Log Out**: Ends your session. Always log out on shared computers.

### Notifications

![Notifications](02-cdso-admin/02-notifications.png)

Alerts raised by new registrations, uploaded receipts and violations.

1. **Bell**: The number is how many notifications you have not read yet.
2. **Notification list**: Newest first. A blue dot marks an unread item; click one to open the screen it is about.
3. **Mark all read**: Clears the unread dots and the badge without deleting anything.
4. **Clear all**: Removes every notification from the list.

### Vehicle Registration Management

![Vehicle Registration Management](02-cdso-admin/03-vehicle-registration.png)

Management › Vehicle Registration. Review and process vehicle pass applications.

1. **Registration Form QR**: Shows a QR code applicants can scan to open the online application form.
2. **Registrations report**: Pick a date range, then export a PDF or Excel report, or a one-page summary PDF. The export carries the screen’s own filters too — registrant type, payment and status — and the bar names them beside the label, so a table narrowed to Employees produces a report of employees.
3. **Total registrations**: All applications received.
4. **Quick filters**: Click a payment state or registrant type to narrow the list below.
5. **Search**: Find an application by name, plate or type.
6. **Type, payment and status filters**: Pending Review lists the applications still waiting for your decision.
7. **Applications table**: Each row shows the applicant, plate, schedule, payment and status.
8. **View**: Opens the application to check documents and approve or reject it.

> **How reports are exported, everywhere in the system.** A PDF is confirmed before it is made:
> the dialog restates the period, the filters in force and how many records match, and says so
> plainly when nothing matches. The report then opens in a new browser tab instead of landing in
> the downloads folder; the viewer’s own toolbar still offers Save and Print. Excel files are not
> confirmed and still download. Every exported PDF ends with a Prepared by / Approved by signature
> block — see *System Settings: report signatories*.

### Reviewing an application

![Reviewing an application](02-cdso-admin/04-registration-review.png)

The application detail window, opened with the View (eye) button.

1. **Applicant details**: Name, email, registrant type, schedule, department and driver’s license.
2. **Payment**: Whether the fee is Paid, Unpaid or Exempt, with the Official Receipt number, the amount and the date it was settled.
3. **Campus days**: The days this pass will allow entry.
4. **Official Receipt photograph**: The picture of the receipt the applicant uploaded, shown with the number it backs — click it to open it full size. A row that has none says so in words, so “nothing was uploaded” is never mistaken for a picture that has not loaded. Only CDSO is shown this; it reaches neither the owner’s portal nor a guard.
5. **Vehicle information**: Plate, vehicle type, colour and conduction number.
6. **Accept Registration**: Check the number against the photograph before approving. Scroll down for the decision buttons.

### Approving or rejecting an application

![Approving or rejecting an application](02-cdso-admin/04b-registration-decision.png)

The bottom of the Registration Details window.

1. **Official Receipt (OR) Number**: Filled in from the receipt the applicant uploaded. Check it against the photograph shown with the OR number higher up this window, and correct it only if the two disagree. It is not asked for at all when the applicant is fee-exempt.
2. **Reason for Extra Days**: Appears only when the pass allows more than the standard 3 days. Required; the registration is flagged as a Special Case.
3. **Reject**: Turns the application down. You will be asked for the reason.
4. **Confirm & Accept**: Approves the application: the pass and system ID are issued and the owner’s portal account is created. It stays disabled while the fee is unsettled.

> **A registration whose fee is unsettled can no longer be approved.** Where no Official Receipt
> number is on file and the applicant is not marked Exempt, a notice appears in place of the OR
> box and Confirm & Accept is disabled. Enter the OR number once the fee has been paid at the
> counter — a number typed at the counter counts as settled, the same as an uploaded receipt — or
> set the payment to Exempt where nothing is owed. The old justification box, which let a pass be
> issued against money that had never been collected, is gone.

### User Management

![User Management](02-cdso-admin/05-user-management.png)

Management › User Management. Create, edit, disable and delete accounts.

1. **Add User**: Create a security guard or vehicle-owner account.
2. **Screen tabs**: User Accounts lists everyone. Detail Change Requests holds owners’ requested corrections; the badge counts pending ones.
3. **Account counts**: Total, active and disabled accounts.
4. **Role tabs**: Show only one kind of account.
5. **Search**: Search by name, email or user ID (e.g. SLC-OWN-000005).
6. **Status filter**: Show active, disabled or archived accounts.
7. **Accounts table**: User ID, name, email, role and status.
8. **Actions menu**: Edit, disable/enable, reset two-factor, print a guard QR badge, or delete the account.

### Approving detail change requests

![Approving detail change requests](02-cdso-admin/06-detail-change-requests.png)

Owners cannot edit an accepted registration directly. Their corrections wait here for approval.

1. **Detail Change Requests tab**: The badge counts requests waiting for your review.
2. **Status filter**: Show requests waiting for review, or ones already decided.
3. **Who is asking**: Owner name, plate, registrant type, email, and when the request was filed.
4. **Requested changes**: Each field shows the current value struck through and the new value the owner wants.
5. **Decline / Approve & apply**: Approve & apply updates the registration. Decline leaves it unchanged. The owner is emailed either way.

### Device Management (cameras)

![Device Management (cameras)](02-cdso-admin/07-device-management.png)

Management › Device Management. Register the IP cameras used at the gates and parking areas.

1. **Add Device**: Register a new camera: name, IP address, device ID, password and whether it watches a gate or a parking area.
2. **Camera counts**: How many cameras are registered, and how many cover entry gates and parking.
3. **Search**: Find a camera by name, IP address or device ID.
4. **Assignment**: The gate or parking area this camera covers.
5. **Connect / Disconnect**: Opens or closes the camera’s live picture in Live Feeds below.
6. **Test, Edit, Delete**: Check the camera answers, change its settings, or remove it.
7. **Live Feeds**: Live pictures from the cameras you connected. (Sample picture shown.)

### Supplier Management

![Supplier Management](02-cdso-admin/08-suppliers.png)

Management › Suppliers. Supplier vehicles are admitted automatically when their plate is scanned.

1. **Add Supplier**: Register a supplier company and its plates.
2. **Supplier card**: Company name and how many plates it has registered.
3. **Category**: Delivery, maintenance, vendor, contractor or other.
4. **Active**: Click to deactivate a supplier; its vehicles stop being admitted automatically.

> Each registered plate has a **Print Supplier Pass** button. It prints a standing pass for that
> plate on the thermal printer, to be kept in the vehicle: the guard scans its QR at the gate the
> way a registered vehicle’s QR is scanned, the first scan recording the entry and the next the
> exit.

### Operations Center: live monitor

![Operations Center: live monitor](02-cdso-admin/09-operations-center.png)

Operations › Operations Center. Watch all gates, guards and cameras at once.

1. **Live summary**: Guards on duty, recent entries, cross-gate flags and total guards.
2. **Screen tabs**: Live Monitor (this view), Guards (who is on shift where) and Gate Records (visitors inside, cross-gate discrepancies and confiscated accounts).
3. **Camera Monitor**: View-only camera pictures. Plate detection runs on the guard terminals. (Sample picture shown.)
4. **Gate column**: Latest scans at each gate. The green chip names the guard on duty and how long they have been on shift.
5. **Refresh**: Reloads the screen.

### Operations Center: guards

![Operations Center: guards](02-cdso-admin/10-operations-guards.png)

Guard shifts: who is on duty at which gate.

1. **Guards tab**: Guard activity and shift history.
2. **Guard activity**: Each guard’s code, gate, duty status, and today’s totals: scans, authorized, denied, visitors and exits.
3. **Duty status**: On Duty while the guard is signed in at a gate; Off Duty otherwise.
4. **Recent shift history**: When each shift started and ended. "Still active" means the guard has not signed out yet.

### Operations Center: gate records

![Operations Center: gate records](02-cdso-admin/11-operations-gate-records.png)

Visitors still on campus, cross-gate discrepancies and confiscated accounts.

1. **Active Visitors**: Visitor passes still inside, with the office visited, who issued the pass, and time left (or how long they have overstayed).
2. **Cross-Gate Discrepancies**: Vehicles that entered through one gate and left through another, so both gates’ records can be checked.
3. **Confiscated accounts**: Owners serving a violation penalty and when it ends.
4. **Lift**: Ends a confiscation early. The violations themselves stay on record.

### Parking Space Management

![Parking Space Management](02-cdso-admin/12-parking-spaces.png)

Operations › Parking Space Management. Bays, zones, live occupancy and events.

1. **Screen tabs**: Parking Spaces (this view) and Events (campus events that reserve parking).
2. **Occupancy tiles**: Free, Parked and Capacity cover every zone of the selected vehicle category and come from the parking cameras. On Campus counts vehicles scanned in at a gate, parked or not.
3. **Zone tabs**: One tab per parking zone, each naming the camera that watches it, or "No camera" where none is assigned. Click a tab to select the zone.
4. **Refresh, Cameras, New Zone**: Reload the zones, open the live parking feeds, or create a zone. The Cameras button carries an "unzoned" badge counting cameras with no zone drawn on them yet.
5. **Live View / Edit Parking Slots**: Watch the bays over the live feed, or switch to drawing them over the zone’s still reference image.
6. **Monitoring status**: Monitoring bays means the detector is running on this zone; Starting camera that it is coming up; Not monitored yet that the zone’s setup is unfinished, so the bay colours are not a live reading. There is no on/off switch — the server restarts the detector by itself.
7. **Delete Zone**: Removes the selected zone and its bays. It sits apart from the working controls because it cannot be undone.
8. **Bay map**: Drawn over the live feed. Green bays are free, red bays are taken (with the plate where one is known); a vehicle lying across two bays is flagged as double parking.

> **Three steps make a zone monitored**, and until all three are done the zone reads *Not
> monitored yet* and its bay colours are not a reading. Capture a reference image with the lot
> empty, draw the parking slots on it, then press **Start Monitoring**, which stores that image as
> the zone’s empty baseline. A banner on the zone names whichever step is outstanding and takes
> you to it. Replacing the reference image later marks the baseline stale, and the banner asks you
> to update it.

> **On Campus is not the same count as Parked**, and the two are not meant to agree. Parked is
> what the parking cameras can see in the bays; On Campus counts vehicles of that category scanned
> in at a gate, parked or not. On a busy day On Campus runs ahead — drop-offs, vehicles still
> circling, vehicles parked where no camera watches — and it falls behind wherever a zone is not
> monitored yet and its bays are not being scored. The line beneath the tiles names both sources,
> and warns when a zone is unmonitored and the Free figure may therefore be too high.

### Drawing parking bays

![Drawing parking bays](02-cdso-admin/13-parking-edit-slots.png)

Edit Parking Slots mode: draw and adjust bays over the zone’s reference image.

1. **Drawing tools**: Box draws rectangular bays; Pen places points and closes the shape on the first (yellow) point, for angled bays. The hint beside the tools counts the points placed.
2. **Upload Image / Save Layout**: Upload a reference picture of the lot, and save the bays when you are done.
3. **Drawing area**: Click and drag to draw a bay. Click a bay to open its small toolbar: rename it, duplicate it, or remove it. Enter keeps a bay, Delete removes it, and Esc cancels a half-drawn shape.
4. **Camera**: The camera whose picture the bays are drawn over. Draw against the zone’s own camera — bays drawn over another camera’s view are scored against a different scene.
5. **Set Up Bay Monitoring**: The three steps that make this zone monitored, in order, with the next one’s button highlighted: capture a reference image of the empty lot, draw the parking slots on it, then Start Monitoring. Until all three are done the bays are not being watched.

> **Cameras with two lenses.** Where a camera sends two views stacked in one frame, the editor
> asks which view this zone covers before any bay is drawn, so that a bay cannot be placed across
> the seam between them. Live View then shows that view alone, and the feeds panel labels the two
> as Lens 1 and Lens 2.

> A warning appears if you are looking at one camera while the selected zone is watched by
> another, with a button to reassign it. Bays drawn against the wrong view are saved, but they are
> scored against a different scene than the one they look right on.

### Campus events

![Campus events](02-cdso-admin/14-events.png)

Events reserve part of campus parking and note organizer plates.

1. **Event Mode overrides**: Parking Override lets guards admit vehicles when a zone is full; Entry Override lets them admit plates that would be denied. Click the switch to turn each on or off.
2. **Add Event**: Create an event: name, date, times, how much parking it takes, and organizer plates.
3. **Event card**: Date, time, organizer plates, and how much of campus parking the event holds.
4. **Event actions**: Activate the event, reschedule it, show its details, or delete it.
5. **Archived Events**: Past events, kept for reference.

> Each organizer plate has a **Print Event Pass** button. It prints a standing pass for that plate
> on the thermal printer, to be kept in the vehicle for the day: the guard scans its QR at the
> gate like a registered vehicle’s, the first scan recording the entry and the next the exit.

### Violations

![Violations](02-cdso-admin/15-violations.png)

Operations › Violations. The 3-offence penalty ladder and every recorded offence.

1. **Active warnings**: How many violations are still counting against their owners.
2. **Violations report**: Pick a date range and export a PDF or Excel report. The range narrows the table on screen as well, so what you are looking at is what the file will contain; where it is set, it overrides the period buttons below.
3. **Status filter**: All, Warnings, Confiscated (3rd offence) or Cleared / Resolved.
4. **Type and period**: Filter by violation type, and by Today, Week, Month or Year.
5. **Search**: Find by plate, conduction number, owner or notes. The arrow button resets all filters.
6. **Violations table**: Plate, owner, type with offence number (1st, 2nd, 3rd), notes, evidence photo, when it was issued and by whom.
7. **Lift**: Voids a violation as a false alarm. It stops counting, and later offences are renumbered.

### Vehicle Log

![Vehicle Log](02-cdso-admin/16-vehicle-log.png)

Operations › Vehicle Log. Every gate scan, entry and exit, across all gates.

1. **Record count and exports**: Download the filtered log as PDF or Excel.
2. **Date range**: Quick ranges, or pick exact start and end dates.
3. **Search**: Find a plate, owner or guard.
4. **Gate, status and category filters**: Narrow the log; Clear filters resets them.
5. **Log table**: Time, plate, owner, gate, decision and the guard on duty.
6. **Exit**: When the vehicle left and how long it stayed, or "Still inside".

### Rule Constraints: entry rules

![Rule Constraints: entry rules](02-cdso-admin/17-rule-constraints.png)

System › Rule Constraints. When each kind of vehicle may enter campus.

1. **Screen tabs**: Entry Rules, Registration Period (when applications are accepted) and Access Mode.
2. **Rule**: One rule per registrant type: who it applies to, allowed days and hours.
3. **Days, hours and stay limit**: Allowed campus days, the time window, and the maximum stay where one is set.
4. **Edit**: Opens the rule to change its days, hours or stay limit.

### Rule Constraints: registration period

![Rule Constraints: registration period](02-cdso-admin/18-registration-period.png)

Set when the online application form accepts submissions.

1. **New Period**: Create a registration window with a label, start date and end date.
2. **Period**: Label, start and end dates. Only one period can be Active; the public form accepts applications only while it is open.
3. **Extend Duration / Deactivate**: Open a running period to move its end date, or close the window early. The editor is headed "Extending period" and saves with Save New Dates.

### Rule Constraints: access mode

![Rule Constraints: access mode](02-cdso-admin/19-access-mode.png)

Campus-wide overrides such as open campus mode.

1. **Open Campus switch**: When ON, every vehicle may enter regardless of registration, schedule or entry rules. Use it for open events or graduation, and switch it OFF afterwards.
2. **Current effect**: What the setting means right now.

### Audit Log

![Audit Log](02-cdso-admin/20-audit-log.png)

System › Audit Log. What staff did to accounts and records.

1. **Event count and exports**: Download the filtered log as PDF or Excel.
2. **Date range**: All, Today, Week, Month, Year, or exact dates.
3. **Search**: Search by the staff member’s name or the details text.
4. **Action filter**: Show one kind of action, e.g. User Created or Entry Override.
5. **Audit table**: When, who, what kind of action, and the details.

### System Settings: accounts & fees

![System Settings: accounts & fees](02-cdso-admin/21-settings-accounts-fees.png)

System › System Settings. System-wide policies, grouped into five tabs.

1. **Settings tabs**: Accounts & Fees, Gates & Scanning, Parking, Data & Backup, and Report Signatories. A tab holding unsaved changes carries a dot, and Save stays disabled until something differs from what is stored.
2. **Account expiry period**: How long a vehicle-owner account lasts before it is archived automatically.
3. **Retention notice**: What the chosen period means for owners’ records.
4. **Vehicle pass fees**: The amounts applicants are asked to pay at the Accounting Office.

### System Settings: gates & scanning

![System Settings: gates & scanning](02-cdso-admin/22-settings-gates-scanning.png)

Gates, scan timing and event overrides.

1. **Add a gate**: Enter the gate number and a display label, then Add Gate. It appears on the guard sign-in page straight away.
2. **Gate list**: Every gate. Deactivate removes a gate from the sign-in page without deleting its history.
3. **Scan deduplication**: If the same plate is read again within this many seconds, the repeat is ignored so it is not logged twice.

### System Settings: parking

![System Settings: parking](02-cdso-admin/23-settings-parking.png)

Parking detection thresholds.

1. **Counts as parked after**: How long a vehicle must stay still before its bay counts as taken.
2. **Reports double parking after**: How long a vehicle must sit across two bays before a double-parking violation is issued.
3. **Broadcast Parking Notice**: Write a subject and message, then Broadcast to All Owners. It is emailed and shown in every owner’s portal.
4. **Active notices**: Notices owners can currently see. The × removes one.

### System Settings: data & backup

![System Settings: data & backup](02-cdso-admin/24-settings-data-backup.png)

Backups, restore and data retention.

1. **Retention period**: Access logs, violations and archived accounts older than this are deleted automatically. The audit log is kept.
2. **Download / Restore**: Download a full snapshot of the system data, or restore the system from a backup file.
3. **Automatic backups**: How often the server backs itself up, and how many automatic backups to keep.
4. **Backups on the server**: Saved backups. Save As downloads a copy, Restore brings the system back to that point, and the bin deletes it.

> **What a restore does to the live data.** It is a merge, not a wipe: records in the backup are
> written over the matching live records, and nothing is deleted. Where a live record already
> holds a value the backup needs — the same email address, the same camera number — the live
> record is moved aside rather than removed, either by archiving it or by giving it a fresh value.
> Restoring onto a system that is already running and restoring onto a brand-new installation
> therefore both work.

### System Settings: report signatories

![System Settings: report signatories](02-cdso-admin/24b-settings-report-signatories.png)

Who signs the PDF reports. Every exported PDF ends with this block.

1. **Prepared by**: Normally whoever pressed the export button — their name and role are taken from the account signed in. Leave these two boxes blank to keep it that way; fill them in only to sign every report with one name instead.
2. **Approved by**: Set here, because the head of office does not sign in to run every report and the post changes hands. Leave the name blank and the report prints an empty ruled line to be signed by hand.
3. **Captions**: The wording above each signature. An office that files these as "Submitted by" or "Noted by" can say so without a change to the system.
4. **Preview**: Shows the signature block exactly as it will be printed.

> Leaving the approver’s name blank is a real choice, not a missing value: the report then prints
> an empty ruled line to be signed by hand. Whatever is set here, the report footer still records
> the account that exported it.

### Help & User Manual

![Help & User Manual](02-cdso-admin/25-help.png)

The built-in guide, available to every role from the sidebar.

1. **Search the manual**: Type a keyword such as backup, violations or scanning.
2. **Topics**: Topics for your role, grouped by area. Click one to read it.
3. **Article**: Step-by-step instructions for the selected topic.


## Security Guard

The gate terminal used by security guards.

### Entry Management (gate terminal)

![Entry Management (gate terminal)](03-security-guard/01-entry-management.png)

The guard’s main screen at the gate. Plates are read automatically from the entry camera.

1. **Menu and gate**: Entry Management, Parking and Vehicle Log. The green tag shows the gate you are clocked in at.
2. **CCTV Monitor**: Live picture from this gate’s entry camera. Detected plates are boxed and checked automatically. (Sample picture shown.)
3. **Owner name / plate search**: Type a plate, conduction number or owner name when a plate is not read automatically.
4. **Check Plate — Entry / Exit**: Checks the typed plate. A vehicle already inside is logged out; otherwise its entry is checked against its pass and schedule.
5. **Scan QR**: Scan the QR code on the owner’s vehicle pass, or on any printed slip — a visitor slip, a supplier or event pass, or the entry slip given to a vehicle with no plate. A slip opens rather than acting: you then press Record Exit or Reprint, so looking one up cannot let a vehicle out by accident.
6. **No Plate?**: Record a vehicle with no plate or conduction sticker by describing it. Its entry slip prints on the thermal printer for the driver to keep.
7. **Recent Scans**: Latest decisions at this gate. The chips count entries by category.
8. **Active Visitors**: Visitor passes still inside, with time left. +30m extends a pass.
9. **Confiscated accounts**: Owners serving a violation penalty. They may not enter or park.
10. **Shift controls**: On-duty timer, Help, Policy, Change Shift (hand over the gate) and Log Out.

> **Two panels appear on the right when they have something in them.** **Unrecognized Vehicles
> Inside** lists the no-plate vehicles still on campus, each with a Slip button (reprint it, or
> record the exit from it) and a Log Exit button — without them those vehicles would sit in the
> inside count for ever. **Overstaying** lists vehicles still on campus past the maximum stay
> their entry rule allows, with how far over they are; **Acknowledge** issues the Time Exceed
> violation there and then. They may still leave, but not return until the confiscation ends, and
> a vehicle already recorded today is marked as such rather than offered again.

> **Passes and slips print on the thermal printer.** Creating a visitor pass asks for the
> visitor’s name, the office, the purpose and the allowed duration in hours and minutes, then
> prints the slip — and the visitor’s entry is logged once the slip prints, not before. If the
> printer is offline the pass is still created and the screen says so, with a Retry; a no-plate
> vehicle’s entry is recorded either way, and its slip can be reprinted from the Unrecognized
> Vehicles panel.

### Checking a plate

![Checking a plate](03-security-guard/02-plate-check-result.png)

The result shown after a plate is checked.

1. **Decision**: The result in large type: here WRONG SCHEDULE DAY, with the plate and registrant type. Approved entries show in green.
2. **Reason and rule**: Why the vehicle was denied and which entry rule applied.
3. **Owner and vehicle**: Check these against the vehicle in front of you.
4. **Override Entry**: Let the vehicle in anyway. You must give a reason, and the override is recorded under your name.
5. **Acknowledge**: Accept the decision and close the result.
6. **Detected plate**: The plate the camera read, boxed on the live picture. (Sample picture shown.)

### Recording a vehicle with no plate

![Recording a vehicle with no plate](03-security-guard/03-no-plate.png)

Describe the vehicle when there is no plate the system can read.

1. **Driver's Name**: The name the driver gives you.
2. **Who is entering?**: Student, employee, fetcher, visitor or supplier.
3. **Vehicle description**: Vehicle type, colour and make/model stand in for the missing plate on the log.
4. **Note**: Anything useful, e.g. "newly delivered unit, plate not yet issued".
5. **Record Entry**: Saves the entry to the Vehicle Log. Cancel closes without saving.

> The entry slip prints on the thermal printer for the driver to keep. Scanning that slip when the
> vehicle leaves is the quickest way to close the entry; the Unrecognized Vehicles Inside panel
> does the same job when the slip has been lost.

### Parking monitor

![Parking monitor](03-security-guard/04-parking.png)

Watch parking zones, see free spaces and act on parking offences.

1. **Zones**: Pick the parking zone to watch.
2. **Cameras**: Switch between the cameras watching parking.
3. **Live picture**: The camera view with bay outlines. (Sample picture shown.)
4. **Legend**: Free, Occupied and Double parking. The bays refresh every 8 seconds.
5. **Campus-wide figures**: Free, Parked and Capacity for this vehicle type across campus, from the parking cameras. On campus beside them counts gate entry and exit scans instead, parked or not, so the two are counted differently and will not agree. Held appears where an event is reserving spaces.
6. **Bays in this zone**: How many bays the camera sees taken, with the zone’s own status: Monitoring, Camera off, or Not set up — which means an admin has not finished the zone’s setup, so those bay colours may be out of date.
7. **Issue Violation / Override Parking**: Record a parking offence, or let a vehicle park when the area is full (event mode).

### Issuing a parking violation

![Issuing a parking violation](03-security-guard/05-issue-violation.png)

The violation form opened from the parking monitor.

1. **License Plate**: The plate of the offending vehicle. Required.
2. **Violation Type**: Choose the offence, e.g. No Sticker, Double Parking or Time Exceed.
3. **Notes**: Optional details that help the CDSO review it.
4. **Issue Violation**: Records the violation against the vehicle’s owner and counts toward the offence ladder. Cancel closes without saving.

### Vehicle Log (gate)

![Vehicle Log (gate)](03-security-guard/06-vehicle-log.png)

Every scan recorded at your gate.

1. **Status filter**: Show all scans or only one decision: Authorized, Denied, Wrong Day, Visitor or Exited.
2. **Date and refresh**: Pick another day, or reload the list.
3. **Scan entry**: Plate, decision, owner, guard on duty and time. Exits show when the vehicle left and how long it stayed.


## Vehicle Owner

The portal registered vehicle owners see.

### Vehicle owner portal: your pass

![Vehicle owner portal: your pass](04-vehicle-owner/01-portal-overview.png)

What a registered owner sees after signing in.

1. **Help, Policy and Log Out**: The in-app guide, the privacy policy, and signing out.
2. **Security and Change Password**: Manage two-factor authentication and set a new password.
3. **Account and registration IDs**: Your portal account ID and your vehicle pass registration ID.
4. **Personal information**: Your details and assigned campus days.
5. **Pending change request**: A correction you asked for, waiting for CDSO approval. You can withdraw it.
6. **Vehicle access QR code**: Show this at the gate. Tap to enlarge, show it fullscreen, or copy its data.
7. **Registration status**: Whether your vehicle is authorized to enter campus.
8. **Vehicle information**: Plate, type, colour and conduction number on file.

> An e-bike shows its college-issued **Control Number** (FM-001, FM-002, and so on) in place of
> both the plate and the conduction number. That number is the e-bike’s identity at the gate and
> on its QR code, and it cannot be edited.

### Vehicle owner portal: violations & parking

![Vehicle owner portal: violations & parking](04-vehicle-owner/02-portal-violations-parking.png)

Further down the portal: your violation record, announcements and live parking.

1. **My violation record**: Any violations against your account and their status.
2. **Announcements**: Parking notices from the CDSO.
3. **Available / Parked / Total**: Spaces for your vehicle type right now. A "Held for event" figure joins them while an event is reserving parking.
4. **Zone fill level**: How full each parking zone is.
5. **Bays**: Green bays are free; red bays are taken.

### Account security (two-factor)

![Account security (two-factor)](04-vehicle-owner/03-security-two-factor.png)

Manage your authenticator and backup codes.

1. **Two-factor status**: On means an authenticator code is required when you sign in on a new device, after 7 days away, or before sensitive changes.
2. **Last used**: When your authenticator last produced a valid code. If that looks wrong, pair a new phone.
3. **Backup code**: A one-time code for signing in if you lose your phone. Generate one and keep it somewhere safe.
4. **New backup code / Pair a new phone**: Create a fresh backup code, or move two-factor to a different phone.


## Installing the Campus System

Installing the campus system with SLC-Smart-Parking-Campus-Setup.exe.

### Step 1 · License Agreement

![Step 1 · License Agreement](05-installer/01-license.png)

Run SLC-Smart-Parking-Campus-Setup.exe on the campus computer and accept the license to continue.

1. **License text**: Read the terms for installing the system.
2. **Accept**: Select "I accept the agreement". Next stays disabled until you do.
3. **Next / Cancel**: Next continues. Cancel quits Setup without installing anything.

### Step 2 · Choose the install folder

![Step 2 · Choose the install folder](05-installer/02-destination.png)

Keep the default folder unless your IT office says otherwise.

1. **Install folder**: Default: C:\Smart Parking and Vehicle Verification System. Keep the path short: Setup refuses folders longer than 110 characters.
2. **Browse...**: Pick a different folder.
3. **Disk space**: About 6 GB is needed once the Python environment and application code are downloaded.
4. **Back / Next / Cancel**: Next continues, Back returns to the previous page, and Cancel quits Setup without installing anything.

### Step 3 · Select components

![Step 3 · Select components](05-installer/03-components.png)

Choose what Setup installs. A full installation is right for a new gate computer.

1. **Installation type**: Full installation selects everything. Choose Custom installation to pick items yourself.
2. **Component list**: The launcher is always installed. Untick Git, Python, Node.js or FFmpeg only if your IT office already manages them. The firewall rule lets guards’ computers reach this machine.
3. **Space required**: Disk space the current selection needs.
4. **Back / Next / Cancel**: Next continues, Back returns to the previous page, and Cancel quits Setup without installing anything.

### Step 4 · Deployment options

![Step 4 · Deployment options](05-installer/04-deployment-options.png)

Choose the network port the system is served on.

1. **Update branch**: This computer follows the branch the installer was built for and updates from it. It cannot be changed here.
2. **Port to serve on**: Guards open http://<this computer’s address>:<port>. Keep 8000 unless another program already uses it.
3. **Back / Next / Cancel**: Next continues, Back returns to the previous page, and Cancel quits Setup without installing anything.

### Step 5 · Prerequisites check

![Step 5 · Prerequisites check](05-installer/05-prerequisites.png)

Setup shows what the computer already has before anything is installed.

1. **Detected software**: [installed] means it is already present; "will install" means Setup downloads it through winget.
2. **What happens next**: On first launch the launcher downloads the application (about 300 MB) and builds a Python environment (about 5.7 GB). This takes a while; keep the computer online.
3. **Back / Next / Cancel**: Next continues, Back returns to the previous page, and Cancel quits Setup without installing anything.

### Step 6 · Start Menu folder

![Step 6 · Start Menu folder](05-installer/06-start-menu.png)

Where the launcher, repair and uninstall shortcuts are placed.

1. **Folder name**: The Start Menu folder for the shortcuts. The default is fine.
2. **No Start Menu folder**: Tick to skip creating Start Menu shortcuts.
3. **Back / Next / Cancel**: Next continues, Back returns to the previous page, and Cancel quits Setup without installing anything.

### Step 7 · Additional tasks

![Step 7 · Additional tasks](05-installer/07-additional-tasks.png)

Optional shortcuts and system changes.

1. **Desktop shortcut**: Puts a launcher shortcut on the desktop (recommended).
2. **Open at startup**: Opens the launcher automatically when the computer starts. Recommended for a dedicated gate computer.
3. **Add to PATH**: For IT staff who run the launcher from a command prompt. Usually not needed.
4. **Back / Next / Cancel**: Next continues, Back returns to the previous page, and Cancel quits Setup without installing anything.

### Step 8 · Ready to install

![Step 8 · Ready to install](05-installer/08-ready.png)

Check the summary, then click Install. The launcher opens when Setup finishes.

1. **Summary**: Install type, destination, components and tasks. Click Back to change anything.
2. **Back / Install / Cancel**: Install continues, Back returns to the previous page, and Cancel quits Setup without installing anything.


## Running the Campus System

The launcher window that runs the campus server.

### The campus launcher

![The campus launcher](06-campus-launcher/01-launcher-window.png)

Opened from the desktop or Start Menu shortcut. It runs the server that the gate terminals connect to, so leave this window open.

1. **Server status and address**: RUNNING or STOPPED, and the address guards and CDSO staff open in their browsers.
2. **Health**: Database connection, cameras online, and whether live updates are working.
3. **Start / Stop server**: Starts or stops the system. Stopping it disconnects every gate terminal and camera.
4. **Open pages**: Guard terminal opens the gate sign-in page, Admin login opens the CDSO login, and Copy URL copies the address.
5. **Updates**: Checks for a newer version every few minutes. When one is found, click "Update and restart" at a quiet moment: restarting drops the camera feeds briefly.
6. **Settings**: Port, start the server when this window opens, kiosk mode (full screen), and which page opens automatically.
7. **Save settings / Credentials**: Save your changes. Credentials holds the shared database URL and secret key; it is only needed once per computer.
8. **Activity**: Live messages from the server. Log files opens the saved logs; Clear empties this view. Useful when reporting a problem.
9. **Install location and version**: Where the application lives and which version (branch and commit) is running.

> **When a browser refuses the camera.** Browsers only allow a page to use a webcam on a secure
> page. The launcher’s own window is one, and so is the campus computer itself, but another device
> opening `http://<campus computer>:8000` is not — so QR scanning by webcam will not start there.
> The campus server also serves the same pages over HTTPS on port 8443, and any page that needs
> the camera offers a link: **Open the secure page to use the camera**. The first time a device
> follows it, the browser warns about the certificate, because it is one the college issued
> itself; choose Advanced, then Proceed. A USB scanner works on either page.

