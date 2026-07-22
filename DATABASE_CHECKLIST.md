# Database Checklist — Compliance Notes

Vehicle Management System for Saint Louis College · Capstone Project 2

This document maps the project's database design to the four database
requirements on Sir Jake's checklist, with the reasoning behind each decision.

---

## 1. Encrypted Passwords ✅

Passwords are **never stored in plaintext**. Authentication uses Django's default
password hashing, **PBKDF2 with SHA-256** (salted, with a high iteration count),
via `django.contrib.auth`. No custom `PASSWORD_HASHERS` override is set, so the
framework default applies.

Password *strength* is additionally enforced at registration by
`AUTH_PASSWORD_VALIDATORS` (see `backend/config/settings.py`):
minimum length, similarity-to-user checks, common-password rejection, and
numeric-only rejection.

- Stored value example: `pbkdf2_sha256$<iterations>$<salt>$<hash>`
- Hashing is one-way; the original password cannot be recovered from the database.

## 2. Naming Conventions ✅

Every table uses a **consistent `tbl_` prefix and a singular, descriptive name**,
set via `db_table` on each model's `Meta` (see `backend/*/models.py`):

| Model | Table |
|-------|-------|
| User | `tbl_user` |
| AuditLog | `tbl_audit_log` |
| Notification | `tbl_notification` |
| Violation | `tbl_violation` |
| Gate / Office / VisitorPass | `tbl_gate` · `tbl_office` · `tbl_visitor_pass` |
| AccessLog / GuardShift | `tbl_access_log` · `tbl_guard_shift` |
| MLTrainingSample / PlateRecognitionRecord | `tbl_ml_training_sample` · `tbl_plate_recognition_record` |
| Vehicle / VehicleRegistration | `tbl_vehicle` · `tbl_vehicle_registration` |
| ReferenceItem / RuleConstraint | `tbl_reference_item` · `tbl_rule_constraint` |
| ParkingZone / ParkingSpace / ParkingNotice | `tbl_parking_zone` · `tbl_parking_space` · `tbl_parking_notice` |
| RegistrationPeriod / Event | `tbl_registration_period` · `tbl_event` |
| SystemSettings / Supplier / SupplierPlate / Camera | `tbl_system_settings` · `tbl_supplier` · `tbl_supplier_plate` · `tbl_camera` |

- **Prefix:** every application table starts with `tbl_`.
- **Singular nouns:** table names use singular, descriptive nouns (`tbl_user`,
  `tbl_vehicle`, `tbl_violation`).
- Applied by the table-rename migrations (`accounts/0025…`, `scanning/0016…`,
  `vehicles/0051…`, `violations/0009…`). Renames preserve all data.

## 3. Avoid Generic Field Names ✅

Every primary-key **column** is named for its own entity (`<entity>_id`), not a
bare `id` and not a shortened/ambiguous stub. Set with `db_column` on each PK:

| Table | PK column |
|-------|-----------|
| `tbl_user` | `user_id` |
| `tbl_audit_log` | `audit_log_id` |
| `tbl_notification` | `notification_id` |
| `tbl_violation` | `violation_id` |
| `tbl_vehicle` | `vehicle_id` |
| `tbl_vehicle_registration` | `vehicle_registration_id` |
| `tbl_access_log` | `access_log_id` |
| `tbl_guard_shift` | `guard_shift_id` |
| `tbl_visitor_pass` | `visitor_pass_id` |
| `tbl_ml_training_sample` | `ml_training_sample_id` |
| `tbl_plate_recognition_record` | `plate_recognition_record_id` |
| `tbl_parking_zone` / `tbl_parking_space` / `tbl_parking_notice` | `parking_zone_id` / `parking_space_id` / `parking_notice_id` |
| `tbl_registration_period` | `registration_period_id` |
| `tbl_system_settings` | `system_settings_id` |
| `tbl_reference_item` / `tbl_rule_constraint` / `tbl_supplier` / `tbl_supplier_plate` / `tbl_camera` / `tbl_office` / `tbl_event` | `<entity>_id` |

A round of migrations (`scanning/0017`, `vehicles/0052`) renamed the earlier
shortened PK columns (`record_id`→`plate_recognition_record_id`,
`registration_id`→`vehicle_registration_id`, `zone_id`→`parking_zone_id`, etc.)
so every PK now matches its table.

> One deliberate exception: `tbl_gate`'s PK column is `gate_pk`, because the
> model already has a `gate_id` **slug** field (`'gate1'`, `'gate4'`, …) that is
> the stable identifier stored on access logs, shifts and cameras. `gate_pk`
> keeps the surrogate key distinct from that functional slug.

Foreign keys reference these descriptive columns (e.g. a scan's owner → `user_id`),
and every other column is named for what it holds (`plate_number`, `full_name`,
`scanned_at`, `fine_amount`, `clocked_in_at`, `detection_confidence`).

> **Note on the API layer:** in Python/REST code the primary key is still exposed
> as `id` (Django's default attribute name), while the *database column* is
> `<table>_id`. This is deliberate — it keeps the database self-documenting
> (requirement satisfied at the DB level) without breaking Django REST Framework's
> conventions that the rest of the codebase relies on.

## 4. Normalized Database Design ✅

The schema is normalized to at least **Third Normal Form (3NF)**:

- **No repeating groups / no multi-valued columns** — related records live in
  their own tables (users, vehicles, vehicle registrations, violations, gates,
  access logs, notifications, etc.).
- **Relationships are modelled with foreign keys**, not duplicated data — e.g. a
  violation references its vehicle and issuing officer by key rather than copying
  their details.
- **Each non-key attribute depends on its table's key**, reducing redundancy and
  keeping the data consistent (an owner's details are stored once and referenced,
  so an update in one place is reflected everywhere).

Referential integrity is enforced by the database via foreign-key constraints,
with deliberate `on_delete` behaviour per relationship (e.g. deleting a user
cascades to that user's owned vehicles/registrations while preserving audit
history via `SET NULL` on the actor reference).
