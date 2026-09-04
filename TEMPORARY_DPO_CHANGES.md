# TEMPORARY — Data Privacy Office trial (`temporary` branch only)

This branch exists to satisfy the Data Privacy Office for a round of testing.
**Nothing here is meant to reach `main`.** Every change is additive-free: no
model fields were dropped, no migration was written, and the database schema is
byte-for-byte what `main` expects.

## What the DPO asked for

From the CDSO comment sheet:

> home address + copy of driver's license need not be collected

and, as scoped for this trial:

- no photo/file attachment anywhere, including the email flow
- no age
- no home address
- no contact number
- no ID number (the applicant's name identifies them)

## What actually changed

**The public registration form** (`frontend/src/pages/Register/RegisterPage.jsx`)
no longer asks for: home address (the whole PSGC province/city/barangay cascade
is gone), contact number, the authorized driver's contact number, age, Student
ID, Employee ID, the driver's licence photo, the assessment form, and — for a
fetcher — each fetched student's ID and assessment form. A fetcher now lists the
students they collect by **name and education level** only.

Still collected, because the DPO did not object to them and CDSO cannot review
an application without them: full name, SLC/personal email, driver's licence
**number**, plate or conduction number, vehicle type/colour/body number,
education level, program & year, department, and campus schedule.

**Uploads are closed end to end.** `POST /api/vehicles/register/documents/` is
kept as a route but answers `410 Gone` — a browser still running the previous
bundle reads a 404 as a network fault and retries, and the retry would be an
upload we must not accept. `POST /api/vehicles/register/payment/` now takes the
Official Receipt **number** alone; a file posted by a stale bundle is parsed and
discarded, never stored. CDSO checks the paper receipt at the counter.

**Nothing withheld is written, even from a stale client.**
`PublicOpenRegistrationView` strips `address`, `contact_number`, `age`,
`student_id`, `employee_id` and `driver_contact` from the payload before the
serializer sees it. The columns are still there; nothing writes them.

**Nothing withheld is served or mailed, even from an older row.** Rows filed
before the trial still hold an address and a contact number, and the emails and
PDFs are rebuilt from the row every time they are sent — so
`VehicleRegistrationSerializer.to_representation` drops the withheld fields and
reports every document slot empty, and the acceptance/pending emails and the
registration PDF no longer print those rows at all.

**Surfaces that displayed them** — the CDSO review modal, the owner portal, the
approved-account modal, and the vehicle-owner fields in User Management — had
the corresponding rows removed.

## What was deliberately left alone

- **The database.** No migration, no data deletion. `manage.py makemigrations
  --check` is clean, so the `migrate` that `backend/start.sh` runs at boot is a
  no-op against the shared Neon instance.
- **`CdsoDirectRegisterView`** (`/register/direct/`) and
  **`AdminCreateOwnerView`** (`/accounts/admin/create-owner/`). Both still accept
  the withheld fields, and both are unreachable from the UI — no frontend calls
  either. Changing dead code adds revert surface for no compliance benefit. If
  the DPO wants every write path closed, these two are the remaining ones.
- **`frontend/src/pages/Admin/EntryManagement.jsx`**, which is unrouted. It
  guards each field with `&&`, so the serializer change already blanks it.
- **`PolicyPage`'s privacy policy**, which is the College's own institutional
  document. Only its "Application Requirement" clause was corrected, because it
  told applicants to submit a photocopy the form no longer accepts.

## The installer does not follow this branch

The tracked branch is compiled into the installer, not chosen at install time —
`#define DefaultBranch "main"` in `installer/SLC-VMS-Campus.iss`. Every machine
already installed runs `git fetch origin main` and will never see this branch.
**Do not run `installer\build.ps1 -Branch temporary`**; a machine installed from
that exe would follow this branch permanently.

## Reverting

Every change carries a `TEMPORARY` marker. To find them all:

```sh
git grep -n "TEMPORARY" -- backend frontend
```

Since no migration was written and no data was deleted, reverting is a matter of
dropping this branch — `main` is untouched.
