"""How a scheduled visit meets the gate.

A ScheduledVisit tells the guard who to expect today and pre-fills the visitor
pass. It is also the CDSO saying "this vehicle comes in today": a plate booked
for today is not turned away by the day/hour entry rules (supplier delivery
window, student/employee/fetcher rules) — see visit_expected_today. It is not a
pass on its own, though: a visitor still enters on a printed visitor slip, a
supplier still enters off the supplier roster, and a confiscated or suspended
account is still refused.

A visit is marked arrived at the moment its person's entry is logged, and only
then:
  * explicitly, when the slip of a visitor pass checked in from the visit
    prints (scanning.views.VisitorPassPrintedView), and
  * by plate, when any authorized entry for a plate expected today is logged
    (the AccessLog signal in scanning.signals) — which is how a supplier on the
    roster, who never gets a visitor pass, is ticked off too.

An archived visit is out of all of it: the gate neither lists nor matches it.
"""
from django.utils import timezone

from .models import ScheduledVisit, SupplierPlate, _normalize_plate


def live_visits():
    """Every visit the gate may see — archived ones are on record only."""
    return ScheduledVisit.objects.filter(archived_at__isnull=True)


def expected_today():
    """Today's visits, not yet arrived first, soonest-added order within."""
    return (live_visits()
            .filter(expected_date=timezone.localdate())
            .select_related('supplier', 'created_by')
            .order_by('is_arrived', 'visitor_name'))


def open_visit_for_plate(plate):
    """Today's visit still waiting on this plate, or None."""
    plate = _normalize_plate(plate)
    if not plate:
        return None
    return (live_visits()
            .filter(expected_date=timezone.localdate(), is_arrived=False, plate_number=plate)
            .order_by('pk').first())


def visit_expected_today(*plates):
    """Today's visit booked on any of these identifiers, or None — the visit
    that waives the day/hour entry rules for this vehicle.

    Arrived visits count too: the booking is for the day, so a supplier who
    drives out between deliveries is still expected when they come back."""
    wanted = {_normalize_plate(p) for p in plates} - {''}
    if not wanted:
        return None
    return (live_visits()
            .filter(expected_date=timezone.localdate(), plate_number__in=wanted)
            .order_by('is_arrived', 'pk').first())


# The status an admission carries when it matches a booking, so the guard's
# result card reads "Scheduled Entry" rather than an ordinary approval. A
# screen label only: the AccessLog row is stored as AUTHORIZED like any entry.
SCHEDULED_ENTRY = 'scheduled_entry'


def visit_note(visit, waived=False):
    """The sentence added to an entry that matched a booking — and, when the
    booking is what got it past the day/hour rules, says so."""
    return (f' Expected visit SV-{visit.pk} ({visit.visitor_name})'
            + (' — schedule rule waived.' if waived else '.'))


def inside_since(visit):
    """When this visit's vehicle came in, if it is inside now; else None.

    Arrived and not yet gone: its visitor pass is still open, or its plate has
    an entry with no exit. The gate's Expected Today keeps such a visit on the
    list (as inside) and drops it once the vehicle has left. The time is this
    stay's entry, not the day's first arrival — a supplier back for a second
    drop shows when they came back."""
    if not visit.is_arrived:
        return None
    open_pass = visit.visitor_passes.filter(status='active').order_by('-pk').first()
    if open_pass:
        return open_pass.printed_at or open_pass.entered_at
    if not visit.plate_number:
        return None
    from scanning.views import _inside_state      # scanning imports this module; imported late
    state, entry = _inside_state(visit.plate_number)
    return entry.scanned_at if state != 'outside' else None


def mark_arrived(visit, when=None, plate=''):
    """Tick one visit off. Returns True if it changed — an already-arrived visit
    keeps its first arrival time.

    `plate` is what they actually came in. It is kept only on a booking made
    without one, so the record says which car it was; a booked plate is left
    as booked even if they came in another."""
    if visit is None or visit.is_arrived:
        return False
    visit.is_arrived = True
    visit.arrived_at = when or timezone.now()
    fields = ['is_arrived', 'arrived_at']
    plate = _normalize_plate(plate)
    if plate and not visit.plate_number:
        visit.plate_number = plate
        fields.append('plate_number')
    visit.save(update_fields=fields)
    return True


def mark_arrived_by_plate(plate, when=None):
    """Tick off every visit expected today on this plate. Two bookings for the
    same vehicle on one day are the same car arriving once."""
    plate = _normalize_plate(plate)
    if not plate:
        return 0
    visits = live_visits().filter(
        expected_date=timezone.localdate(), is_arrived=False, plate_number=plate)
    return sum(mark_arrived(v, when) for v in visits)


def is_supplier_plate(plate):
    """True when the plate is on an active supplier's roster — the gate admits
    it on a scan, so the guard has nothing to issue."""
    plate = _normalize_plate(plate)
    return bool(plate) and SupplierPlate.objects.filter(
        plate_number=plate, supplier__is_active=True).exists()


# Where a visit stands. The same words the CDSO table's tabs, the report and
# the Expected Visit card use, decided here once so none of them can file a
# visit differently.
VISIT_STATUS_LABELS = {
    'today':    'Expected today',
    'upcoming': 'Upcoming',
    'arrived':  'Arrived',
    'no_show':  'No-show',
    'archived': 'Archived',
}


def visit_status(visit, today=None):
    today = today or timezone.localdate()
    if visit.archived_at:
        return 'archived'
    if visit.is_arrived:
        return 'arrived'
    if visit.expected_date < today:
        return 'no_show'
    return 'today' if visit.expected_date == today else 'upcoming'
