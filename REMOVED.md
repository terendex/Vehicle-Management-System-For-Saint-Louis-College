# REMOVED.md — what was taken out, and how to put it back

This file records every file and every line removed during the September 2026
cleanup. **Nothing was deleted.** Each file was moved into the `_removed/`
folder at the top of the repository, keeping its original folder layout, so
`_removed/backend/vehicles/check_data.py` used to live at
`backend/vehicles/check_data.py`.

## Why these were removed

Nothing in the running app used any of them. Before removal, every file's name
was searched for across all code, routes, scripts, docs and config files, and
each one had no live user. They were leftovers from debugging, experiments, or
pages that had been unhooked from the app months earlier.

## How to put something back

- **One file:** move it back to where it was, then follow its "To bring it
  back" note below. Example:
  `git mv _removed/backend/scanning/ml/ocr.py backend/scanning/ml/ocr.py`
- **Anything exactly as it was before the cleanup:** a git tag named
  `pre-cleanup-2026-09-18` marks the last commit before these changes
  (`73e71908`). `git checkout pre-cleanup-2026-09-18 -- <path>` restores any
  file or folder from that point.
- **Lines removed from files that were kept** are quoted exactly in section 4,
  with their line numbers, so they can be pasted back in place.

---

## 1. Backend debug leftovers

### 1.1 Plate-reader debug pictures (10 files)

- `backend/crop_gray.jpg`
- `backend/crop_heuristic.jpg`
- `backend/crop_padded.jpg`
- `backend/crop_processed.jpg`
- `backend/crop_sharp.jpg`
- `backend/crop_test.jpg`
- `backend/crop_thresh.jpg`
- `backend/crop_upscaled.jpg`
- `backend/latest_frame.jpg`
- `backend/plate_only.jpg`

**What they were:** sample pictures saved while tuning the licence-plate reader
in early June 2026 (added 2026-06-10, commit `6e814e13`). Each `crop_*` file
shows one step of preparing a plate photo so text recognition can read it —
turned grey, sharpened, turned pure black-and-white ("thresholded"), padded,
enlarged. `latest_frame.jpg` is a whole camera frame and `plate_only.jpg` is
the plate cut out of it.

**What depended on them:** nothing. No code reads or writes these names any
more. The line `backend/*.jpg` in `.dockerignore` stays on purpose: it stops
future debug pictures from being copied into the deployed server image.

**To bring them back:** not needed by the app. If you tune the plate reader
again, save fresh debug pictures from `backend/scanning/ml/reader.py` with
`cv2.imwrite(...)` at the step you want to see, and keep them out of git.

### 1.2 `backend/backup.sql`

**What it was:** an empty file (0 bytes), added 2026-06-19 (commit
`c18f8e66`), apparently a placeholder for a database export that never
happened.

**What depended on it:** nothing. Real backups are made by the app's
Settings → Data & Backup screen (`backend/accounts/backup_utils.py`) and are
stored in `backend/backups/`, which git ignores. The `backend/backup.sql` line
in `.dockerignore` stays as a guard.

**To bring it back:** nothing to rebuild — it was empty.

### 1.3 `backend/vehicles/check_data.py`

**What it was:** a 19-line debugging script that prints every user (email,
name, role), every vehicle, and every accepted registration to the terminal.
Added 2026-06-18 (commit `2e025acf`).

**What depended on it:** nothing — and it could never actually run. It is
written as a Django "management command" (a script you run with
`python manage.py <name>`), but Django only finds those inside a
`management/commands/` folder, and this file sat directly in `vehicles/`.

**Be careful:** it prints personal data (emails, student IDs) to the screen.

**To bring it back working:** move it to
`backend/vehicles/management/commands/check_data.py`, then from `backend/` run
`python manage.py check_data`. It uses the `User`, `Vehicle` and
`VehicleRegistration` models, which still exist.

---

## 2. Unused machine-learning scripts (`backend/scanning/ml/`)

**Shared background:** these are stand-alone experiments from June–July 2026
for reading licence plates from video. The live app never used them. It uses
`detection.py`, `reader.py`, `validator.py`, `tracking.py`,
`proximity_tracker.py`, `collector.py`, `database.py` and `train.py`, which all
stay. None of the removed scripts is used by any other file, so removing them
leaves nothing broken. They need the machine-learning packages already listed
in `requirements.txt` (ultralytics, paddleocr, opencv, torch).

**To bring any of them back:** move it back into `backend/scanning/ml/`. Most
use their neighbours through relative imports (`from .detection import ...`),
so run them as a module from inside `backend/`:
`python -m scanning.ml.<name>`.

### 2.1 `backend/scanning/ml/main.py` (363 lines)

**What it did:** a stand-alone "watch a camera and read plates" program: finds
plates with the YOLO model, follows each car from frame to frame with a Kalman
filter (a standard way to smooth a moving box's position), keeps 10 plate
pictures per car, reads the text by majority vote, and shows a live window with
the frame rate. Last changed 2026-07-12 (`a01229f7`).

**Non-obvious:** its opening comment promises "PostgreSQL storage", but the
code has no database calls. Its car-following logic duplicates what
`tracking.py` and `proximity_tracker.py` do inside the app.

### 2.2 `backend/scanning/ml/video_train.py` (344 lines)

**What it did:** an offline pipeline: read a video, find and follow vehicles,
find their plates, cut the plates out, turn them black-and-white for text
recognition, and pass results on for training. Last changed 2026-07-12.

**Non-obvious:** real training now goes through `train.py` (kept), which the
app's automatic retraining task calls.

### 2.3 `backend/scanning/ml/video_pipeline.py` (312 lines)

**What it did:** nearly the same as `video_train.py` without the training step
— find vehicles, follow them, match plates to vehicles, read the plates. Uses
`detection.py`. Last changed 2026-07-12.

### 2.4 `backend/scanning/ml/lpr_pipeline.py` (303 lines)

**What it did:** an experimental plate-reading loop built for speed: a
background thread reads video and drops frames to stay live, a light
car-follower, text recognition only once per new car (then remembered), and
correction of Philippine plate formats. Uses `detection.py` and `reader.py`.
Last changed 2026-06-20.

**Non-obvious:** the live app does this job in `backend/scanning/consumers.py`
(the code that talks to the browser over a live connection), not here.

### 2.5 `backend/scanning/ml/ocr_bridge.py` (46 lines)

**What it did:** a small helper that runs text recognition in background
threads so a video loop doesn't pause while waiting. Uses `reader.py`. Last
changed 2026-06-30. Nothing ever called it.

### 2.6 `backend/scanning/ml/extract_frames.py` (132 lines)

**What it did:** prepared videos for labelling: cut them into still pictures
(2.5 per second by default), dropped blurry ones, and removed near-duplicates.
Needs `ffmpeg` installed plus the `tqdm`, `Pillow` and `imagehash` packages.
Last changed 2026-06-19.

**Run it (if restored):**
`python -m scanning.ml.extract_frames --video-dir <path> --output-dir <path> --fps 2.5`

**Non-obvious:** `video_to_labeled.py` (kept) now cuts frames itself, so the
current labelling process doesn't need this.

### 2.7 `backend/scanning/ml/ocr.py` (315 lines)

**What it did:** an older text-recognition module (PaddleOCR on several
versions of each picture, character correction, majority vote). Replaced by
`reader.py`, which the app uses. Uses `validator.py` and `reader.py`. Last
changed 2026-06-30.

**Goes with:** one line changed in `backend/scanning/ml/README.md` — see 4.2.

---

## 3. Unused frontend files (`frontend/src/`)

**Shared background:** no page that the app shows imports any of these, so
they were never part of the built website. Removing them does not change the
built website at all — the build output was compared file-by-file before and
after (section 6). The code checker (ESLint) also stops reporting the 12 errors
and 2 warnings these files contained.

### 3.1 `frontend/src/hooks/useRtspStream.js` (292 lines)

**What it did:** a React "hook" (a reusable piece of page logic) that opened a
live connection to the backend's `/ws/scan/rtsp/` address and drew the pictures
it received from one IP camera onto the page.

**What depended on it:** nothing; no page ever imported it.

**Non-obvious:** the backend address `/ws/scan/rtsp/` is still in use — the
live camera views reach it through `frontend/src/context/CameraContext.jsx`.
This hook was an older, unused way of doing the same thing.

**To bring it back:** move it back, then in a page:
`import { useRtspStream } from '../hooks/useRtspStream'` and call it with the
signed-in user's access token.

### 3.2 `frontend/src/hooks/useMultiRtspStream.js` (313 lines)

**What it did:** the same as 3.1, but one connection per camera, for a grid of
camera feeds.

**What depended on it:** only `SecurityDashboard.jsx` (also removed, 3.7).
Restore the two together.

### 3.3 `frontend/src/hooks/useScanStream.js` (302 lines)

**What it did:** sent the computer's webcam picture to the backend address
`/ws/scan/live/?gate=<gate>` for plate scanning, and drew the results.

**What depended on it:** nothing since 2026-06-29 (commit `1692fe4d`), when the
last page using it was unhooked.

**Non-obvious:** the backend side of `/ws/scan/live/` (`ScanLiveConsumer` in
`backend/scanning/consumers.py`) was left in place. With this hook gone, no
page connects to it — but that was already true before the cleanup.

### 3.4 `frontend/src/utils/imageCompress.js`

**What it did:** provided `compressImage(file)`, which shrank a phone photo of
a document before uploading it (PDF files passed through untouched).

**What depended on it:** nothing since 2026-09-12. Commit `8fed3d13` ("Bring
the DPO registration-form changes onto main") removed the driver's-licence
photo, assessment form and receipt uploads from the registration forms at the
Data Privacy Office's request. Those were its only users.

**To bring it back:** only needed if photo uploads return to the registration
form. Move it back, then in the form:
`import { compressImage } from '../../utils/imageCompress'` and
`const file = await compressImage(picked)` before adding the file to the upload.

### 3.5 `frontend/src/pages/Admin/EntryManagement.jsx` (571 lines) — **its CSS stays**

**What it did:** the old admin "Vehicle Entry Management" page (live camera,
scan results, visitor-pass form). Its address `/admin/entries` was removed on
2026-06-29 (commit `1692fe4d`). The guard page
`frontend/src/pages/Security/SecurityEntryManagement.jsx` replaced it and does
everything it did (checked: every data call, hook and setting it used is also
in the guard page).

**What depended on it:** nothing. It was still being edited alongside the
guard page (last on 2026-09-15, commit `f4db0c10`, which added an "Event"
label), which was wasted duplicate work.

**IMPORTANT — do not move `frontend/src/pages/Admin/EntryManagement.css`.** It
was deliberately kept. The live guard page loads it through
`@import '../Admin/EntryManagement.css'` in `SecurityEntryManagement.css`, and
the Operations Center relies on its `.cls-*` badge styles.

**To bring it back:** move it back, then in `frontend/src/App.jsx` add
`const EntryManagement = lazy(() => import('./pages/Admin/EntryManagement'))`
and, inside the admin routes,
`<Route path="/admin/entries" element={<EntryManagement />} />`. Expect to
bring it up to date with the guard page first.

### 3.6 `frontend/src/pages/Admin/GateActivityMonitor.jsx` (278 lines) and `GateActivityMonitor.css` (448 lines)

**What it did:** an admin "Guard Activity" page: a card per guard with daily
scan counts and warnings when a guard scanned at the wrong gate, using the
backend's `/scan/guard-monitor/` data.

**What depended on it:** nothing. It was never given an address in the app.
The CSS file was used only by this page.

**Non-obvious:** the data function `getGuardMonitor` (in
`frontend/src/api/scanning.js`) and its backend endpoint stay — the live
Operations Center uses them.

**To bring it back:** move both files back; in `App.jsx` add a lazy import and
a route such as `<Route path="/admin/guard-activity" element={<GateActivityMonitor />} />`.
It uses `hooks/useGates.js`, which still exists.

### 3.7 `frontend/src/pages/Security/SecurityDashboard.jsx` (271 lines) and `SecurityDashboard.css` (406 lines)

**What it did:** the old guard home page — summary numbers, recent scans, and
a live camera grid (through `useMultiRtspStream`, 3.2). Its address
`/security` was removed on 2026-06-28 (commit `6e21e988`).

**To bring it back:** move both files back together with
`hooks/useMultiRtspStream.js`; in `App.jsx` add a lazy import and
`<Route path="/security" element={<SecurityDashboard />} />` — first check that
no newer page uses `/security`.

### 3.8 `frontend/src/pages/Admin/GuardMonitor.jsx` (202 lines) and `GuardMonitor.css` (456 lines)

**What it did:** an earlier version of the guard-activity page (3.6). Its
address `/admin/guard-monitor` was removed on 2026-06-29 (commit `1692fe4d`).

**To bring it back:** as 3.6, with the address `/admin/guard-monitor`.

### 3.9 `frontend/src/pages/Security/SecurityAuditLog.jsx` (273 lines) and `SecurityAuditLog.css` (389 lines)

**What it did:** a guard's "My Activity Log" page. Its address
`/security/audit` was removed on purpose on 2026-06-29 (commit `c937c5c5`,
"restrict audit log access to admin only").

**Non-obvious:** do not confuse it with `SecurityAuditLogPage.jsx` /
`SecurityAuditLogPage.css`, which are live and were kept (they now serve
`/security/audit`). Restoring the old page would undo a deliberate access
restriction.

### 3.10 `frontend/src/App.css` (184 lines)

**What it was:** starter styles from the project template created on
2026-05-26 (commit `f55902f1`, "set up") — `#center`, `#docs`, `#next-steps`,
`.counter`, `.hero`, `.ticks`. No file ever imported it, so the site never
loaded it. Site-wide styles live in `frontend/src/index.css`.

### 3.11 `frontend/src/pages/Security/ScanOverlay.css` (21 lines)

**What it was:** styles for drawing over a camera video (`.em-video`,
`.em-video-container`, `.em-overlay-canvas`). No file imported it, so the site
never loaded it.

---

## 4. Lines removed or changed in files that were kept

The original lines are recorded here instead of being commented out in place.
Line numbers are as of commit `73e71908`, before the change.

### 4.1 `README.md` (project root), folder-tree section

**Why:** the tree named files that were removed. In these trees `├──` means
"more items follow" and `└──` marks the last item in a folder.

**Replaced lines 78–80.** The tree listed the three hooks that were removed and
never listed the two hooks that actually remain, so the three lines were
replaced with the two real ones.

Before:

```
78: │   │   │   ├── useScanStream.js       # Webcam → WS → 60fps canvas (live scan)
79: │   │   │   ├── useRtspStream.js       # Single RTSP camera → WS → 60fps canvas
80: │   │   │   └── useMultiRtspStream.js  # Multiple RTSP cameras, one WS per camera
```

After:

```
│   │   │   ├── useFullscreen.js       # Fullscreen one camera feed without remounting it
│   │   │   └── useGates.js            # Gate list from System Settings, cached for every screen
```

**Removed lines:**

```
90: │   │   │   │   ├── EntryManagement.jsx
96: │   │   │   │   ├── SecurityDashboard.jsx
98: │   │   │   │   └── SecurityAuditLog.jsx
```

**Changed line** (with `SecurityAuditLog.jsx` gone, `SecurityEntryManagement.jsx`
became the last item in `Security/`):

```
97 before: │   │   │   │   ├── SecurityEntryManagement.jsx
97 after:  │   │   │   │   └── SecurityEntryManagement.jsx
```

**Note:** the `Admin/` and `Security/` page lists in this tree were already
incomplete before the cleanup (for example, `SecurityParkingView.jsx` and
`SecurityAuditLogPage.jsx` are live but not listed). Only the lines above were
changed.

**To restore:** put lines 78–80 back in the `hooks/` list (keep `└──` on
whichever hook ends up last), paste lines 90, 96 and 98 back at those
positions, and change line 97 back to `├──`.

### 4.2 `backend/scanning/ml/README.md`, folder-tree section

```
23 before: ├── reader.py / ocr.py     # Plate OCR (PaddleOCR)
23 after:  ├── reader.py              # Plate OCR (PaddleOCR)
```

**To restore:** put the original line back if `ocr.py` returns (2.7).

### 4.3 `backend/vehicles/views.py` — parking-notice email failure (debug prep)

**Why:** when a parking-notice email failed, the error was printed straight to
the console with `print()` and `traceback.print_exc()`, so it bypassed the
app's logging and never reached a log file. It now goes through the module's
existing `logger` (`logger.exception(...)` records the same message plus the
full traceback).

**Replaced lines** (line numbers as of commit `97a7e99c`):

```
3603:             except Exception as e:
3604:                 import traceback
3605:                 print(f"[EMAIL ERROR] Parking notice broadcast failed: {e}")
3606:                 traceback.print_exc()
```

**With:**

```
            except Exception:
                # Report through the app logger (message + full traceback) so the
                # failure lands in the configured logs, not only on the console.
                logger.exception('Parking notice broadcast failed')
```

The next line, `email_status = 'failed'`, is unchanged, so the response still
reports the failure.

**To restore:** put the four original lines back in place of the new ones.

### 4.4 `DEPLOY.md` — which requirements file is the real one (debug prep)

**Why:** three passages said the root `requirements.txt` was only a "detection
marker" that defers to `backend/requirements.txt`. The reverse is true: the
root file holds the pins and Railpack installs it directly (`railpack.json`),
and `backend/requirements.txt` is a one-line redirect (`-r ../requirements.txt`).
Both Dockerfiles already said so.

**Original lines** (line numbers as of commit `97a7e99c`):

```
26:   before `backend/requirements.txt`. Order matters: the PyPI default would drag
```

```
32: - The root [`requirements.txt`](requirements.txt) exists purely as a detection
33:   marker (it just defers to `backend/requirements.txt`). Without a dependency
34:   file at the root, the build can fail with *"Railpack could not determine how
35:   to build the app"*.
```

```
273: `backend/requirements.txt` as before — the root `requirements.txt` is only a
274: build-detection marker.
```

**New text:** line 26 now says "before the root `requirements.txt`"; lines 32–35
became five lines describing the root file as the canonical pin list and
`backend/requirements.txt` as a redirect; lines 273–274 now say it "redirects to
the root `requirements.txt`, which holds the actual pins".

**To restore:** paste the original lines back over the new wording.

---

## 5. Lines added

`.dockerignore` — a new section of three lines plus one blank separator line,
inserted just before the `# ── Docs ──` section (at line 73):

```
# ── Archive (see REMOVED.md) ─────────────────────────────────────────────────
# Files archived in the 2026-09 cleanup; never needed inside the container.
_removed/
```

**Why:** the Railway build (Railpack) copies the whole repository into the
server image; this keeps the archive out of it. The two Dockerfiles copy only
`backend/` and `frontend/`, so they were never affected. `REMOVED.md` itself is
already kept out by the existing `*.md` rule.

**To undo:** delete those lines.

`.gitignore` — two lines added after `backend/*.log` (debug prep):

```
# Debug logs from backend/debug_settings.py (includes rotated copies like server.log.1)
backend/logs/
```

**Why:** `backend/debug_settings.py` writes log files to `backend/logs/` and
keeps rotated copies named like `server.log.1`, which the existing `*.log` rule
does not match.

**To undo:** delete those two lines.

`README.md` — a new subsection, "One-step alternative on Windows — `dev.ps1`",
inserted in "Running the Project" just before "Terminal 1 — Backend (Django +
Daphne)" (debug prep).

**Why:** it documents the new `dev.ps1` launcher and states plainly that
background jobs (the daily backup / archive / purge scheduler and parking-camera
auto-detection) are ON by default — for `dev.ps1` and for the manual `daphne`
command alike — and how to turn them off (`-NoBackgroundJobs`, or the
environment variables `DISABLE_DAILY_SCHEDULER=1` and
`DISABLE_PARKING_AUTODETECT=1`). It also says that this default runs **more**
than the Railway (cloud) server, which by default runs only the daily scheduler
(auto-detection switches itself off there — see
`backend/vehicles/detection_supervisor.py`, `_autodetect_disabled`).

**Wording corrected before commit (2026-09-19).** The first draft of this note
said background jobs run on a bare launch "exactly as in production", and the
`dev.ps1` help said a bare launch "behaves like the real server". That was
wrong for Railway: a bare launch runs both the scheduler **and** camera
auto-detection, while Railway by default runs only the scheduler. Both texts
now say so explicitly, and add that the campus server, by default, runs both.

**To undo:** delete that subsection (from its heading down to the blank line
before "### Terminal 1").

---

## 6. How the removal was checked

"Before" was recorded on 2026-09-18 at commit `73e71908`, just before the
cleanup. "Actual after" was measured on the cleaned-up tree: the frontend checks
on 2026-09-19, and the Django suite finally on 2026-09-20, once this machine had
been brought in line with the versions production installs (Django 4.2.30; it
had been running 6.0.5). Every value below comes from a real run, not a
prediction.

| Check | Before | Expected after | Actual after |
|---|---|---|---|
| Frontend build output, compared file-by-file by SHA-256 fingerprint | 196 files | byte-identical | **All 196 files byte-identical** (2026-09-19 13:14) |
| ESLint: files / errors / warnings (files with problems) | 110 / 77 / 10 (37) | 101 / 65 / 8 (30), every remaining file unchanged | **101 / 65 / 8 (30).** Only the 7 archived files left the report; no remaining file's result changed (2026-09-19 13:14) |
| Brand lockup check (`npm run check:lockups`) | 13 / 13 | 13 / 13 | **13 / 13** (2026-09-19 13:14) |
| Django tests, `manage.py test --noinput --keepdb`, real clock, started 13:00–14:00 | 1,109 / 1,113 at 19:28 (4 known time-of-day failures) | 1,113 / 1,113 | **1,109 / 1,113** (final run 2026-09-20, 13:02–13:47, on **Django 4.2.30** — the version production installs). The 4 failures are the seeded Monday–Saturday campus rule, and 2026-09-20 was a Sunday; the same 4 pass on the same Django 4.2.30 with the clock set to a Monday. Neither this cleanup nor the version is involved — see the second note below. (An earlier full run, 2026-09-19 13:13–14:05 and still on Django 6.0.5, gave **1,111 / 1,113**; those 2 failures were leftover test data — first note.) |

**About the 2 Django failures in the 2026-09-19 run.** The failing tests were
`accounts.tests.NotificationBellTests.test_list_endpoint_returns_unread_count`
(unread count 4 instead of 1) and `test_mark_all_read` (5 marked read instead of
2). The tests run against a separate test database that is kept between runs
(that is what `--keepdb` does). On 2026-09-18 a test run was started through a
helper script that hid the word `test` from Django's settings, so the app sent
emails from background threads as it would on a live server. When a send
failed, the thread saved an "Acknowledgement email failed" notification using
its own database connection — outside the test's automatic undo — so 3 of those
rows stayed in the test database (ids 14144, 14297, 14300). The admin
notification bell counts every unread notification, so these two tests saw 3
extra. The rows were identified by their timestamps, confirmed to be the only
leftovers from that run (all 52 timestamp columns in the test database were
checked), and deleted from the **test database only**. The 7
`NotificationBellTests` were then re-run and all passed. The live database was
never involved, and none of the files archived here are used by these tests.

**About the 4 Django failures in the final 2026-09-20 run.** The failing tests
were `scanning.tests.EntryLogicTests.test_authorized_employee_allowed`,
`test_authorized_student_any_schedule_allowed`,
`test_expired_confiscation_lets_the_owner_back_in`, and
`scanning.tests.ManualEntryAPITests.test_known_authorized_plate_returns_authorized`.
Each one checks that a vehicle is allowed in, and each got "not allowed".

The reason is the calendar, not the code. The entry rules that the database
starts with allow **Monday to Saturday only** — `CAMPUS_DAYS` in
`backend/vehicles/migrations/0074_seed_missing_rule_constraints.py` has no
Sunday — and `_is_within_days()` in `backend/scanning/entry_logic.py` compares
today's weekday against that list. So on a Sunday every student and employee
entry is refused, at any hour, and 2026-09-20 was a Sunday. The one test in the
same family that passed is the only one that fixes its own date, to a Monday.

This was proven rather than assumed: with the same code and the same
Django 4.2.30, and only the weekday changed, the same 4 tests fail when Django's
clock says Sunday and all 5 pass when it says Monday. So neither this cleanup
nor the version change is involved.

Practical note for the next person: several tests in this suite depend on the
clock, which is a pre-existing trait, not something this cleanup introduced. A
run where all 1,113 should pass has to start on a **weekday** between 13:00 and
19:00. The hour alone is not enough, as this Sunday run shows.
