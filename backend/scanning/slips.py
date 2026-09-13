"""Printed gate slips — visitor passes and no-plate entries — as one shape.

Two kinds of vehicle leave the gate holding a paper slip: a visitor on a pass,
and a vehicle with no plate that the guard recorded by hand. Both slips carry a
QR code the guard scans (or a plate / name they type) to pull the slip back up,
see whether the vehicle is still inside, and then — as a separate, deliberate
action — record the exit or reprint a torn slip.

`slip_data()` is the single description of a slip. The thermal printer
(slip_printer.py), the browser's print fallback and the guard's status dialog
all render from it, so the three can never disagree about what a slip says.
"""
from django.utils import timezone

from .models import AccessLog, VisitorPass

VISITOR_PREFIX = 'SLC-VISITOR:'
NOPLATE_PREFIX = 'SLC-NOPLATE:'


def parse_code(code):
    """('visitor' | 'noplate', pk) for a slip QR payload, else None."""
    code = (code or '').strip().upper()
    for prefix, kind in ((VISITOR_PREFIX, 'visitor'), (NOPLATE_PREFIX, 'noplate')):
        if code.startswith(prefix):
            try:
                return kind, int(code[len(prefix):])
            except ValueError:
                return None
    return None


def find(kind, pk):
    """The model row behind a slip, or None."""
    if kind == 'visitor':
        return VisitorPass.objects.select_related('office', 'issued_by', 'vehicle').filter(pk=pk).first()
    if kind == 'noplate':
        return (AccessLog.objects.select_related('scanned_by')
                .filter(pk=pk, is_unrecognized=True, status=AccessLog.Status.AUTHORIZED).first())
    return None


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
    return {
        'kind':             'visitor',
        'id':               pass_.pk,
        'code':             f'{VISITOR_PREFIX}{pass_.pk}',
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
            [['Visitor', pass_.visitor_name or 'N/A'],
             ['Office', pass_.office.name if pass_.office else 'N/A'],
             ['Purpose', pass_.purpose or 'N/A'],
             ['Duration', f'{pass_.allowed_duration} min']],
            [['Issued', _when(pass_.entered_at)],
             ['Expires', _when(pass_.expires_at)],
             ['Guard', pass_.issued_by.full_name if pass_.issued_by else 'N/A']],
        ],
    }


def noplate_slip(entry):
    exit_log = AccessLog.objects.filter(paired_entry=entry).order_by('scanned_at').first()
    end = exit_log.scanned_at if exit_log else timezone.now()
    vehicle = ' '.join(filter(None, [entry.vehicle_color, entry.vehicle_type, entry.vehicle_model])) or 'N/A'
    category = dict(AccessLog.Category.choices).get(entry.entrant_category, entry.entrant_category) or 'N/A'
    details = [['Driver', entry.driver_name or 'N/A'], ['Vehicle', vehicle], ['Category', category]]
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
            [['Entered', _when(entry.scanned_at)],
             ['Guard', entry.scanned_by.full_name if entry.scanned_by else 'N/A']],
        ],
    }


def slip_data(obj):
    return visitor_slip(obj) if isinstance(obj, VisitorPass) else noplate_slip(obj)
