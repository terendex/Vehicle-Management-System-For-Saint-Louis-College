"""Printed gate slips — visitor passes, supplier entries, event organizer
entries and no-plate entries — as one shape.

Four kinds of vehicle leave the gate holding a paper slip: a visitor on a
pass, a supplier vehicle let in off the supplier roster, an organizer's vehicle
let in off an event's plate list, and a vehicle with no plate that the guard
recorded by hand. Every slip carries a QR code the guard
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
EVENT_PREFIX         = 'SLC-EVENT:'
EVENT_PASS_PREFIX    = 'SLC-EVENT-PASS:'
SCHEDULED_PREFIX     = 'SLC-SCHEDULED:'

KEY = True   # marks an important row, see the module docstring

# The two lines under the QR. A slip can override them with 'footer'.
ENTRY_FOOTER = ['SCAN QR AT THE GATE TO EXIT', 'RETURN THIS SLIP UPON EXIT']


def parse_code(code):
    """('visitor' | 'supplier' | 'supplierpass' | 'noplate' | 'event' |
    'eventpass' | 'scheduled', pk, extra) for a slip code, else None.

    `extra` is the third part of the code, and only two kinds carry one: a
    visitor slip's serial (SLC-VISITOR:{id}-{serial}) and an event pass's
    organizer identifier (SLC-EVENT-PASS:{event id}:{identifier}). It is ''
    everywhere else, and on a visitor slip printed before serials existed.
    """
    code = (code or '').strip().upper()
    for prefix, kind in ((VISITOR_PREFIX, 'visitor'), (SUPPLIER_PREFIX, 'supplier'),
                         (SUPPLIER_PASS_PREFIX, 'supplierpass'), (NOPLATE_PREFIX, 'noplate'),
                         (EVENT_PASS_PREFIX, 'eventpass'), (EVENT_PREFIX, 'event'),
                         (SCHEDULED_PREFIX, 'scheduled')):
        if code.startswith(prefix):
            body, extra = code[len(prefix):], ''
            if kind == 'visitor' and '-' in body:
                body, extra = body.split('-', 1)
            elif kind == 'eventpass':
                # An event pass names a plate, not a row of its own — an event's
                # organizer plates are a list of strings, so the pass is the
                # event and the identifier together.
                body, _, extra = body.partition(':')
                if not extra:
                    return None
            try:
                return kind, int(body), extra
            except ValueError:
                return None
    return None


def new_slip_token():
    """A fresh visitor slip serial — 8 hex digits, drawn on every print."""
    return secrets.token_hex(4).upper()


class EventPass:
    """A standing pass for one organizer plate on one event.

    Backed by no row of its own: an event's organizer plates are a list of
    strings on the event (`Event.organizer_plates`), not records, so the pass
    is that pairing rather than something to look up. Everything else the slip
    needs comes off the event.
    """

    def __init__(self, event, plate_number):
        self.event = event
        self.plate_number = plate_number


def find(kind, pk, extra=''):
    """The model row behind a slip, or None. `extra` is parse_code's third
    part — only an event pass uses it, to name which organizer plate."""
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
    if kind == 'event':
        # By category rather than by event_id, so the slip still opens (and
        # the vehicle can still be let out) after its event was deleted.
        return (AccessLog.objects.select_related('scanned_by', 'event')
                .filter(pk=pk, status=AccessLog.Status.AUTHORIZED,
                        entrant_category=AccessLog.Category.EVENT).first())
    if kind == 'scheduled':
        from vehicles.models import ScheduledVisit
        return ScheduledVisit.objects.select_related('supplier', 'created_by').filter(pk=pk).first()
    if kind == 'eventpass':
        # The identifier is compared in the form the event stores and the gate
        # reads, so a pass printed for “ABC 1234” still opens as ABC1234.
        from vehicles.models import Event, canonical_identifier
        ident = canonical_identifier(extra)
        event = Event.objects.filter(pk=pk, archived=False).first()
        if event and ident and ident in (event.organizer_plates or []):
            return EventPass(event, ident)
        return None
    return None


def is_event_entry(entry):
    """True for an AccessLog row that admitted an organizer off an event list."""
    return (isinstance(entry, AccessLog)
            and entry.entrant_category == AccessLog.Category.EVENT
            and not entry.is_unrecognized)


def event_end(event):
    """When the event's window closes, as an aware datetime, or None when the
    event runs all day or has no end time."""
    from datetime import datetime
    if not event or not event.end_time:
        return None
    return timezone.make_aware(datetime.combine(event.date, event.end_time))


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


def _duration(minutes):
    """'45 min', '2 hr', '1 hr 30 min' — visitor passes can run for hours."""
    hours, mins = divmod(int(minutes or 0), 60)
    if not hours:
        return f'{mins} min'
    return f'{hours} hr {mins} min' if mins else f'{hours} hr'


def _scheduled_rows(visit, purpose_shown=''):
    """The block a slip gets when its holder was expected: the booking's
    reference, who arranged it, and what for, so the guard at the exit and the
    office being visited can tell a scheduled visitor from a walk-in.
    `purpose_shown` is the purpose already on the slip — the booking's is left
    out when it only repeats it."""
    rows = [['Scheduled Visit', f'SV-{visit.pk}', KEY],
            ['Arranged by', visit.created_by.full_name if visit.created_by else 'CDSO']]
    if visit.purpose and visit.purpose.strip().lower() != (purpose_shown or '').strip().lower():
        rows.append(['Booked for', visit.purpose])
    return rows


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
             *([['Conduction No.', pass_.conduction_number]] if pass_.conduction_number else []),
             ['Office', pass_.office.name if pass_.office else 'N/A', KEY],
             ['Purpose', pass_.purpose or 'N/A'],
             ['Duration', _duration(pass_.allowed_duration)]],
            *([_scheduled_rows(pass_.scheduled_visit, pass_.purpose)] if pass_.scheduled_visit else []),
            issued,
        ],
    }


def supplier_slip(entry):
    exit_log = AccessLog.objects.filter(paired_entry=entry).order_by('scanned_at').first()
    end = exit_log.scanned_at if exit_log else timezone.now()
    roster = supplier_plate_for(entry)
    supplier = roster.supplier if roster else None
    # A supplier needs no pass, but the CDSO may still have booked the trip.
    # Matched on the plate and the day it came in, not on is_arrived — the
    # entry being printed is the one that ticked it off.
    from vehicles.scheduled_visits import live_visits
    visit = (live_visits().select_related('created_by')
             .filter(plate_number=entry.plate_number,
                     expected_date=timezone.localtime(entry.scanned_at).date())
             .order_by('pk').first()) if entry.plate_number else None
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
            *([_scheduled_rows(visit)] if visit else []),
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


def event_slip(entry):
    """One organizer visit for an event. Its window's end doubles as the slip's
    expiry, so a vehicle still inside after the event is shown overstaying the
    same way a visitor past their pass is."""
    exit_log = AccessLog.objects.filter(paired_entry=entry).order_by('scanned_at').first()
    end = exit_log.scanned_at if exit_log else timezone.now()
    event = entry.event
    expires = event_end(event)
    overstay = _minutes(expires, end) if expires and end > expires else 0
    event_rows = [['Event', event.name if event else 'N/A', KEY]]
    if event:
        event_rows += [['Date', f"{event.date.strftime('%b')} {event.date.day}, {event.date.year}"],
                       ['Time', event.time_display, KEY]]
    return {
        'kind':             'event',
        'id':               entry.pk,
        'code':             f'{EVENT_PREFIX}{entry.pk}',
        'reference':        f'EV-{entry.pk}',
        'title':            'EVENT SLIP',
        'headline':         entry.plate_number,
        'plate_number':     entry.plate_number,
        'name':             event.name if event else '',
        'state':            'exited' if exit_log else 'inside',
        'entered_at':       _iso(entry.scanned_at),
        'expires_at':       _iso(expires),
        'exited_at':        _iso(exit_log.scanned_at) if exit_log else None,
        'printed_at':       None,
        'minutes_inside':   _minutes(entry.scanned_at, end),
        'overstay_minutes': overstay,
        'sections': [
            event_rows,
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


def event_pass_slip(pass_):
    """A standing pass for one organizer plate, printed from Events management
    before the event and kept in the vehicle.

    Like a supplier pass it belongs to no single visit, and its QR carries the
    plate in the same VEHICLE: form a registered vehicle's QR pass uses — so
    scanning it at the gate runs the ordinary plate check, which is already
    what admits an organizer: `organizer_event_for()` matches the plate against
    the event's list. The first scan logs the entry and the next the exit, and
    the slip for that visit is the EVENT SLIP the entry itself creates.

    The event's window end is the pass's expiry, since that is when the list
    stops admitting the vehicle.
    """
    event, plate = pass_.event, pass_.plate_number
    return {
        'kind':             'eventpass',
        'id':               event.pk,
        'code':             f'{EVENT_PASS_PREFIX}{event.pk}:{plate}',
        'qr':               f'VEHICLE:{plate}|EVENT:{event.pk}',
        'reference':        f'EVP-{event.pk}',
        'title':            'EVENT PASS',
        'headline':         plate,
        'plate_number':     plate,
        'name':             event.name,
        'state':            'active' if event.is_under_way() else 'inactive',
        'entered_at':       None,
        'expires_at':       _iso(event_end(event)),
        'exited_at':        None,
        'printed_at':       None,
        'minutes_inside':   0,
        'overstay_minutes': 0,
        'sections': [
            [['Event', event.name, KEY],
             ['Date', f"{event.date.strftime('%b')} {event.date.day}, {event.date.year}"],
             ['Time', event.time_display, KEY]],
            [['Organizer', plate, KEY],
             ['Printed', _when(timezone.now())]],
        ],
        # Each line fits the 48mm roll without wrapping.
        'footer': ['SCAN QR AT THE GATE', 'ON ENTRY AND ON EXIT', 'KEEP THIS PASS IN VEHICLE'],
    }


def expected_visit_slip(visit):
    """The Expected Visit card, printed from the CDSO's Scheduled Visits table
    — for the visitor to bring, or for the gate to keep on hand.

    It is notice, not a pass: nobody is admitted on it. Its QR names the
    booking, and scanning it at the gate opens the check-in for it — the
    visitor pass is issued, and the visit marked arrived, from there. So the
    guard reads the booking off the card instead of typing it in."""
    from vehicles.scheduled_visits import visit_status
    expected = visit.expected_date
    when = f"{expected.strftime('%b')} {expected.day}, {expected.year}"   # fits the bold row on 48mm
    who = [['Visitor', visit.visitor_name, KEY], ['Category', visit.get_category_display()]]
    if visit.supplier and visit.supplier.company_name != visit.visitor_name:
        who.append(['Company', visit.supplier.company_name])
    visit_rows = [['Expected', when, KEY], ['Day', expected.strftime('%A')]]
    if visit.purpose:
        visit_rows.append(['Purpose', visit.purpose])
    if not visit.plate_number:
        visit_rows.append(['Plate', 'Recorded at the gate'])
    return {
        'kind':             'scheduled',
        'id':               visit.pk,
        'code':             f'{SCHEDULED_PREFIX}{visit.pk}',
        'reference':        f'SV-{visit.pk}',
        'title':            'EXPECTED VISIT',
        'headline':         visit.plate_number or f'SV-{visit.pk}',
        'plate_number':     visit.plate_number,
        'name':             visit.visitor_name,
        'state':            visit_status(visit),
        'expected_date':    expected.isoformat(),
        'expected_label':   f"{expected.strftime('%A')}, {when}",
        'entered_at':       None,
        'expires_at':       None,
        'exited_at':        None,
        'printed_at':       None,
        'minutes_inside':   0,
        'overstay_minutes': 0,
        'sections': [
            who,
            visit_rows,
            # The reference is the headline already when there is no plate.
            [*([['Reference', f'SV-{visit.pk}']] if visit.plate_number else []),
             ['Arranged by', visit.created_by.full_name if visit.created_by else 'CDSO'],
             ['Printed', _when(timezone.now())]],
        ],
        # Each line fits the 48mm roll without wrapping.
        'footer': ['SHOW THIS CARD AT THE GATE', 'ON THE EXPECTED DATE', 'NOT A PASS BY ITSELF'],
    }


def slip_data(obj):
    from vehicles.models import ScheduledVisit, SupplierPlate
    if isinstance(obj, ScheduledVisit):
        return expected_visit_slip(obj)
    if isinstance(obj, VisitorPass):
        return visitor_slip(obj)
    if isinstance(obj, SupplierPlate):
        return supplier_pass_slip(obj)
    if isinstance(obj, EventPass):
        return event_pass_slip(obj)
    if is_event_entry(obj):
        return event_slip(obj)
    return noplate_slip(obj) if obj.is_unrecognized else supplier_slip(obj)
