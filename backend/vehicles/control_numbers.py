"""E-bike control numbers — FM-001, FM-002, ...

E-bikes carry no LTO plate and no conduction sticker, so the system issues one
identifier per registration instead. It is stored in `plate_number`, the column
every identity reader already uses: the gate QR (`VEHICLE:{plate}|ID:{id}`), the
guard's manual lookup, `Vehicle.resolve`, violations and the active-registration
uniqueness constraints all work on it unchanged. What makes it a control number
rather than a plate is only that the applicant never types it and can never edit
it (see `registration_edits.editable_for`).

The sequence is derived from the numbers already issued rather than kept in a
counter row. A counter can drift from the data (a restore from backup, a row
edited in the Django admin); the highest issued number cannot. Rejected and
expired registrations still count, and so do Vehicle rows, so a number is never
handed to a second person while any record of the first one remains.
"""
import re
from itertools import chain

from django.db import connection

from .models import Vehicle, VehicleRegistration

PREFIX = 'FM-'
_CONTROL_RE = re.compile(r'^FM-(\d+)$')

# Arbitrary, fixed key for pg_advisory_xact_lock. Two submissions reading the
# same "highest" number at once would otherwise both mint it; the second would
# then fail on uniq_active_registration_plate rather than get the next number.
_ADVISORY_LOCK_KEY = 0x45424B43  # "EBKC"


def is_ebike(vehicle_type) -> bool:
    """Whether a registration form's vehicle type is an e-bike."""
    return (vehicle_type or '').strip().lower() in ('e-bike', 'ebike')


def is_control_number(value) -> bool:
    return bool(_CONTROL_RE.match((value or '').strip().upper()))


def plate_label(plate_number) -> str:
    """What to call the value in `plate_number` on a document or screen."""
    return 'Control Number' if is_control_number(plate_number) else 'Plate Number'


def vehicle_identifier(registration) -> str:
    """The identifier a vehicle is actually known by at the gate.

    `plate_number` is blank on a car still carrying a conduction sticker (it is
    filled by the one-time plate swap when the real plate arrives), so reading it
    alone yields '' for exactly those owners. An e-bike's FM- control number
    already lives in `plate_number` and needs no case of its own.
    """
    return ((registration.plate_number or '').strip()
            or (registration.conduction_number or '').strip())


def gate_qr_payload(registration) -> str:
    """The gate QR payload, or '' when the record has nothing to identify it by.

    Parsed by `plateFromVehicleQr()` in SecurityEntryManagement.jsx and mirrored
    by `vehicleQrPayload()` in frontend/src/utils/plateFormat.js; the shape must
    stay `VEHICLE:{identifier}|ID:{registration id}`. A payload built with an
    empty identifier (`VEHICLE:|ID:n`) scans perfectly and then fails at the gate
    as "Unrecognized QR", so it is refused here instead.
    """
    identifier = vehicle_identifier(registration)
    return f'VEHICLE:{identifier}|ID:{registration.id}' if identifier else ''


def format_control_number(n: int) -> str:
    # Three digits minimum; FM-1000 follows FM-999 rather than wrapping.
    return f'{PREFIX}{n:03d}'


def _highest_issued() -> int:
    values = chain(
        VehicleRegistration.objects.filter(plate_number__startswith=PREFIX)
            .values_list('plate_number', flat=True),
        Vehicle.objects.filter(plate_number__startswith=PREFIX)
            .values_list('plate_number', flat=True),
    )
    numbers = (int(m.group(1)) for m in map(_CONTROL_RE.match, values) if m)
    return max(numbers, default=0)


def peek_next_control_number() -> str:
    """The number the next e-bike registration will most likely receive.

    A preview only — another applicant can submit first. The number actually
    issued comes from `allocate_control_number`.
    """
    return format_control_number(_highest_issued() + 1)


def allocate_control_number() -> str:
    """Issue the next control number.

    Must run inside `transaction.atomic()`, and the registration carrying the
    number must be saved in that same transaction: the advisory lock is held
    until commit, which is what stops a concurrent submission reading the same
    highest number before this one's row is visible.
    """
    if not connection.in_atomic_block:
        raise RuntimeError('allocate_control_number() must run inside transaction.atomic().')
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_xact_lock(%s)', [_ADVISORY_LOCK_KEY])
    return format_control_number(_highest_issued() + 1)
