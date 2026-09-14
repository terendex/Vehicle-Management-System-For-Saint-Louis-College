"""Printed gate slips — visitor passes, supplier entries and no-plate entries —
as one shape.

Three kinds of vehicle leave the gate holding a paper slip: a visitor on a
pass, a supplier vehicle let in off the supplier roster, and a vehicle with no
plate that the guard recorded by hand. Every slip carries a QR code the guard
scans (or a plate / name they type) to pull the slip back up, see whether the
vehicle is still inside, and then — as a separate, deliberate action — record
the exit or reprint a torn slip.

`slip_data()` is the single description of a slip. The thermal printer
(slip_printer.py), the browser's print fallback and the guard's status dialog
all render from it, so the three can never disagree about what a slip says.

Each section row is [label, value] or [label, value, True]; the True marks the
row the guard reads at a glance (who, and until when), printed bold and larger.
"""
import secrets

from django.utils import timezone

from .models import AccessLog, VisitorPass

VISITOR_PREFIX       = 'SLC-VISITOR:'
SUPPLIER_PREFIX      = 'SLC-SUPPLIER:'
SUPPLIER_PASS_PREFIX = 'SLC-SUPPLIER-PASS:'
NOPLATE_PREFIX       = 'SLC-NOPLATE:'

KEY = True   # marks an important row, see the module docstring

# The two lines under the QR. A slip can override them with 'footer'.
ENTRY_FOOTER = ['SCAN QR AT THE GATE TO EXIT', 'RETURN THIS SLIP UPON EXIT']


def parse_code(code):
    """('visitor' | 'supplier' | 'supplierpass' | 'noplate', pk, serial) for a
    slip code, else None. Only a visitor slip carries a serial —
    SLC-VISITOR:{id}-{serial}; it is '' everywhere else, and on a visitor slip
    printed before serials existed."""
    code = (code or '').strip().upper()
    for prefix, kind in ((VISITOR_PREFIX, 'visitor'), (SUPPLIER_PREFIX, 'supplier'),
                         (SUPPLIER_PASS_PREFIX, 'supplierpass'), (NOPLATE_PREFIX, 'noplate')):
        if code.startswith(prefix):
            body, serial = code[len(prefix):], ''
            if kind == 'visitor' and '-' in body:
                body, serial = body.split('-', 1)
            try:
                return kind, int(body), serial
            except ValueError:
                return None
    return None


def new_slip_token():
    """A fresh visitor slip serial — 8 hex digits, drawn on every print."""
    return secrets.token_hex(4).upper()


def find(kind, pk):
    """The model row behind a slip, or None."""
    if kind == 'visitor':
        return VisitorPass.objects.select_related('office', 'issued_by', 'vehicle').filter(pk=pk).first()
    if kind == 'noplate':
        return (AccessLog.objects.select_related('scanned_by')
                .filter(pk=pk, is_unrecognized=True, status=AccessLog.Status.AUTHORIZED).first())
    if kind == 'supplier':
        from vehicles.models import SupplierPlate
        entry = (AccessLog.objects.select_related('scanned_by')
                 .filter(pk=pk, is_unrecognized=False, status=AccessLog.Status.AUTHORIZED)
                 .exclude(plate_number='').first())
        if entry and SupplierPlate.objects.filter(plate_number=entry.plate_number).exists():
            return entry
    if kind == 'supplierpass':
        from vehicles.models import SupplierPlate
        return SupplierPlate.objects.select_related('supplier').filter(pk=pk).first()
    return None


def supplier_plate_for(entry):
    """The roster row behind a supplier entry, or None."""
    from vehicles.models import SupplierPlate
    return (SupplierPlate.objects.select_related('supplier')
            .filter(plate_number=entry.plate_number).first())


def _when(dt):
    if not dt:
        return '—'
    dt = timezone.localtime(dt)
    return f"{dt.strftime('%b')} {dt.day}, {dt.strftime('%I').lstrip('0')}:{dt.strftime('%M %p')}"


def _iso(dt):
    # Strings, not datetimes: a slip also rides the camera websocket, whose
    # json.dumps cannot encode a datetime.
    return dt.isoformat() if dt else None


def _minutes(a, b):
    return max(0, int((b - a).total_seconds() // 60))


def visitor_slip(pass_):
    now = timezone.now()
    end = pass_.exited_at or now
    state = {'active': 'inside', 'exited': 'exited'}.get(pass_.status, pass_.status)
    overstay = _minutes(pass_.expires_at, end) if pass_.expires_at and end > pass_.expires_at else 0
    issued = [['Issued', _when(pass_.entered_at)],
              ['Expires', _when(pass_.expires_at), KEY],
              ['Guard', pass_.issued_by.full_name if pass_.issued_by else 'N/A']]
    if pass_.slip_token:
        issued.append(['Slip No.', pass_.slip_token])   # tells two paper copies apart
    return {
        'kind':             'visitor',
        'id':               pass_.pk,
        'code':             pass_.qr_payload,   # carries the serial of the newest print
        'serial':           pass_.slip_token,
        'reference':        f'VP-{pass_.pk}',
        'title':            'VISITOR SLIP',
        'headline':         pass_.plate_number,
        'plate_number':     pass_.plate_number,
        'name':             pass_.visitor_name,
        'state':            state,
        'entered_at':       _iso(pass_.entered_at),
        'expires_at':       _iso(pass_.expires_at),
        'exited_at':        _iso(pass_.exited_at),
        'printed_at':       _iso(pass_.printed_at),
        'minutes_inside':   _minutes(pass_.entered_at, end),
        'overstay_minutes': overstay,
        'sections': [
            [['Visitor', pass_.visitor_name or 'N/A', KEY],
             ['Office', pass_.office.name if pass_.office else 'N/A', KEY],
             ['Purpose', pass_.purpose or 'N/A'],
             ['Duration', f'{pass_.allowed_duration} min']],
            issued,
        ],
    }


def supplier_slip(entry):
    exit_log = AccessLog.objects.filter(paired_entry=entry).order_by('scanned_at').first()
    end = exit_log.scanned_at if exit_log else timezone.now()
    roster = supplier_plate_for(entry)
    supplier = roster.supplier if roster else None
    return {
        'kind':             'supplier',
        'id':               entry.pk,
        'code':             f'{SUPPLIER_PREFIX}{entry.pk}',
        'reference':        f'SP-{entry.pk}',
        'title':            'SUPPLIER SLIP',
        'headline':         entry.plate_number,
        'plate_number':     entry.plate_number,
        'name':             supplier.company_name if supplier else '',
        'state':            'exited' if exit_log else 'inside',
        'entered_at':       _iso(entry.scanned_at),
        'expires_at':       None,
        'exited_at':        _iso(exit_log.scanned_at) if exit_log else None,
        'printed_at':       None,
        'minutes_inside':   _minutes(entry.scanned_at, end),
        'overstay_minutes': 0,
        'sections': [
            [['Company', supplier.company_name if supplier else 'N/A', KEY],
             ['Category', supplier.get_category_display() if supplier else 'N/A']],
            [['Entered', _when(entry.scanned_at), KEY],
             ['Guard', entry.scanned_by.full_name if entry.scanned_by else 'N/A']],
        ],
    }


def noplate_slip(entry):
    exit_log = AccessLog.objects.filter(paired_entry=entry).order_by('scanned_at').first()
    end = exit_log.scanned_at if exit_log else timezone.now()
    vehicle = ' '.join(filter(None, [entry.vehicle_color, entry.vehicle_type, entry.vehicle_model])) or 'N/A'
    category = dict(AccessLog.Category.choices).get(entry.entrant_category, entry.entrant_category) or 'N/A'
    details = [['Driver', entry.driver_name or 'N/A', KEY], ['Vehicle', vehicle, KEY], ['Category', category]]
    if entry.entry_note:
        details.append(['Note', entry.entry_note])
    return {
        'kind':             'noplate',
        'id':               entry.pk,
        'code':             f'{NOPLATE_PREFIX}{entry.pk}',
        'reference':        f'NP-{entry.pk}',
        'title':            'NO-PLATE ENTRY SLIP',
        'headline':         f'NP-{entry.pk}',
        'plate_number':     '',
        'name':             entry.driver_name,
        'state':            'exited' if exit_log else 'inside',
        'entered_at':       _iso(entry.scanned_at),
        'expires_at':       None,
        'exited_at':        _iso(exit_log.scanned_at) if exit_log else None,
        'printed_at':       None,
        'minutes_inside':   _minutes(entry.scanned_at, end),
        'overstay_minutes': 0,
        'sections': [
            details,
            [['Entered', _when(entry.scanned_at), KEY],
             ['Guard', entry.scanned_by.full_name if entry.scanned_by else 'N/A']],
        ],
    }


def supplier_pass_slip(plate):
    """A standing pass for one supplier plate, printed from Supplier
    Management and kept in the vehicle. Unlike every other slip it belongs to
    no single visit: its QR carries the plate in the same VEHICLE: format a
    registered vehicle's QR pass uses, so scanning it at the gate runs the
    ordinary plate check — the first scan logs the entry, the next the exit —
    with the supplier roster and rules applied as for a camera read."""
    supplier = plate.supplier
    return {
        'kind':             'supplierpass',
        'id':               plate.pk,
        'code':             f'{SUPPLIER_PASS_PREFIX}{plate.pk}',
        'qr':               f'VEHICLE:{plate.plate_number}|SUPPLIER:{supplier.pk}',
        'reference':        f'SPP-{plate.pk}',
        'title':            'SUPPLIER PASS',
        'headline':         plate.plate_number,
        'plate_number':     plate.plate_number,
        'name':             supplier.company_name,
        'state':            'active' if supplier.is_active else 'inactive',
        'entered_at':       None,
        'expires_at':       None,
        'exited_at':        None,
        'printed_at':       None,
        'minutes_inside':   0,
        'overstay_minutes': 0,
        'sections': [
            [['Company', supplier.company_name, KEY],
             ['Category', supplier.get_category_display()]],
            [['Registered', _when(plate.created_at)],
             ['Printed', _when(timezone.now())]],
        ],
        # Each line fits the 48mm roll without wrapping.
        'footer': ['SCAN QR AT THE GATE', 'ON ENTRY AND ON EXIT', 'KEEP THIS PASS IN VEHICLE'],
    }


def slip_data(obj):
    from vehicles.models import SupplierPlate
    if isinstance(obj, VisitorPass):
        return visitor_slip(obj)
    if isinstance(obj, SupplierPlate):
        return supplier_pass_slip(obj)
    return noplate_slip(obj) if obj.is_unrecognized else supplier_slip(obj)
