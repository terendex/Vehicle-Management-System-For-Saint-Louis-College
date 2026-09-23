// Prose that belongs to a manual section but not to any one numbered callout:
// a rule the screen enforces, a panel that only appears when it has something
// in it, a behaviour shared by every screen.
//
// Kept here rather than in shots.mjs for two reasons. The installer and
// launcher figures are desktop captures that cannot be re-taken without
// walking the installer again, and they need notes too. And a wording fix
// should not require a capture run — index.mjs reads this file directly, so
// changing a note and re-running index.mjs is enough.
//
// Keys are figure ids: "<directory>--<file>", the same id capture.mjs writes.
// Each note is one paragraph, rendered as a blockquote under the section's
// callout list. **bold** works.

export const NOTES = {
  // ── Getting Started ─────────────────────────────────────────────────────
  '01-getting-started--05b-student-application-form': [
    'After the form is submitted, the applicant pays the Vehicle Pass fee at the Accounting Office '
    + 'and records it on the payment page: the Official Receipt number **and a photograph of the '
    + 'receipt**, which is required. The photograph backs up the number, which on its own is only '
    + 'a claim. It is never emailed, and it is never shown outside the CDSO review screen.',
  ],

  // ── CDSO ────────────────────────────────────────────────────────────────
  '02-cdso-admin--03-vehicle-registration': [
    '**How reports are exported, everywhere in the system.** A PDF is confirmed before it is made: '
    + 'the dialog restates the period, the filters in force and how many records match, and says so '
    + 'plainly when nothing matches. The report then opens in a new browser tab instead of landing '
    + 'in the downloads folder; the viewer’s own toolbar still offers Save and Print. Excel files '
    + 'are not confirmed and still download. Every exported PDF ends with a Prepared by / Approved '
    + 'by signature block — see *System Settings: report signatories*.',
  ],
  '02-cdso-admin--04b-registration-decision': [
    '**A registration whose fee is unsettled can no longer be approved.** Where no Official Receipt '
    + 'number is on file and the applicant is not marked Exempt, a notice appears in place of the OR '
    + 'box and Confirm & Accept is disabled. Enter the OR number once the fee has been paid at the '
    + 'counter — a number typed at the counter counts as settled, the same as an uploaded receipt '
    + '— or set the payment to Exempt where nothing is owed. The old justification box, which let a '
    + 'pass be issued against money that had never been collected, is gone.',
  ],
  '02-cdso-admin--08-suppliers': [
    'Each registered plate has a **Print Supplier Pass** button. It prints a standing pass for that '
    + 'plate on the thermal printer, to be kept in the vehicle: the guard scans its QR at the gate '
    + 'the way a registered vehicle’s QR is scanned, the first scan recording the entry and the '
    + 'next the exit.',
  ],
  '02-cdso-admin--12-parking-spaces': [
    '**Three steps make a zone monitored**, and until all three are done the zone reads *Not '
    + 'monitored yet* and its bay colours are not a reading. Capture a reference image with the lot '
    + 'empty, draw the parking slots on it, then press **Start Monitoring**, which stores that image '
    + 'as the zone’s empty baseline. A banner on the zone names whichever step is outstanding and '
    + 'takes you to it. Replacing the reference image later marks the baseline stale, and the banner '
    + 'asks you to update it.',
    '**On Campus is not the same count as Parked**, and the two are not meant to agree. Parked is '
    + 'what the parking cameras can see in the bays; On Campus counts vehicles of that category '
    + 'scanned in at a gate, parked or not. On a busy day On Campus runs ahead — drop-offs, '
    + 'vehicles still circling, vehicles parked where no camera watches — and it falls behind '
    + 'wherever a zone is not monitored yet and its bays are not being scored. The line beneath the '
    + 'tiles names both sources, and warns when a zone is unmonitored and the Free figure may '
    + 'therefore be too high.',
  ],
  '02-cdso-admin--13-parking-edit-slots': [
    '**Cameras with two lenses.** Where a camera sends two views stacked in one frame, the editor '
    + 'asks which view this zone covers before any bay is drawn, so that a bay cannot be placed '
    + 'across the seam between them. Live View then shows that view alone, and the feeds panel '
    + 'labels the two as Lens 1 and Lens 2.',
    'A warning appears if you are looking at one camera while the selected zone is watched by '
    + 'another, with a button to reassign it. Bays drawn against the wrong view are saved, but they '
    + 'are scored against a different scene than the one they look right on.',
  ],
  '02-cdso-admin--14-events': [
    'Each organizer plate has a **Print Event Pass** button. It prints a standing pass for that '
    + 'plate on the thermal printer, to be kept in the vehicle for the day: the guard scans its QR '
    + 'at the gate like a registered vehicle’s, the first scan recording the entry and the next '
    + 'the exit.',
  ],
  '02-cdso-admin--24-settings-data-backup': [
    '**What a restore does to the live data.** It is a merge, not a wipe: records in the backup are '
    + 'written over the matching live records, and nothing is deleted. Where a live record already '
    + 'holds a value the backup needs — the same email address, the same camera number — the '
    + 'live record is moved aside rather than removed, either by archiving it or by giving it a '
    + 'fresh value. Restoring onto a system that is already running and restoring onto a brand-new '
    + 'installation therefore both work.',
  ],
  '02-cdso-admin--24b-settings-report-signatories': [
    'Leaving the approver’s name blank is a real choice, not a missing value: the report then '
    + 'prints an empty ruled line to be signed by hand. Whatever is set here, the report footer '
    + 'still records the account that exported it.',
  ],

  // ── Security guard ──────────────────────────────────────────────────────
  '03-security-guard--01-entry-management': [
    '**Two panels appear on the right when they have something in them.** **Unrecognized Vehicles '
    + 'Inside** lists the no-plate vehicles still on campus, each with a Slip button (reprint it, or '
    + 'record the exit from it) and a Log Exit button — without them those vehicles would sit in '
    + 'the inside count for ever. **Overstaying** lists vehicles still on campus past the maximum '
    + 'stay their entry rule allows, with how far over they are; **Acknowledge** issues the Time '
    + 'Exceed violation there and then. They may still leave, but not return until the confiscation '
    + 'ends, and a vehicle already recorded today is marked as such rather than offered again.',
    '**Passes and slips print on the thermal printer.** Creating a visitor pass asks for the '
    + 'visitor’s name, the office, the purpose and the allowed duration in hours and minutes, then '
    + 'prints the slip — and the visitor’s entry is logged once the slip prints, not before. If '
    + 'the printer is offline the pass is still created and the screen says so, with a Retry; a '
    + 'no-plate vehicle’s entry is recorded either way, and its slip can be reprinted from the '
    + 'Unrecognized Vehicles panel.',
  ],
  '03-security-guard--03-no-plate': [
    'The entry slip prints on the thermal printer for the driver to keep. Scanning that slip when '
    + 'the vehicle leaves is the quickest way to close the entry; the Unrecognized Vehicles Inside '
    + 'panel does the same job when the slip has been lost.',
  ],

  // ── Vehicle owner ───────────────────────────────────────────────────────
  '04-vehicle-owner--01-portal-overview': [
    'An e-bike shows its college-issued **Control Number** (FM-001, FM-002, and so on) in place of '
    + 'both the plate and the conduction number. That number is the e-bike’s identity at the gate '
    + 'and on its QR code, and it cannot be edited.',
  ],

  // ── Campus launcher ─────────────────────────────────────────────────────
  '06-campus-launcher--01-launcher-window': [
    '**When a browser refuses the camera.** Browsers only allow a page to use a webcam on a secure '
    + 'page. The launcher’s own window is one, and so is the campus computer itself, but another '
    + 'device opening `http://<campus computer>:8000` is not — so QR scanning by webcam will not '
    + 'start there. The campus server also serves the same pages over HTTPS on port 8443, and any '
    + 'page that needs the camera offers a link: **Open the secure page to use the camera**. The '
    + 'first time a device follows it, the browser warns about the certificate, because it is one '
    + 'the college issued itself; choose Advanced, then Proceed. A USB scanner works on either page.',
  ],
}

export default NOTES
