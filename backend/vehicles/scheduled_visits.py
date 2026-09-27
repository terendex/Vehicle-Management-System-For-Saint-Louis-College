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


def waiver_note(visit):
    """The sentence added to an entry the booking let through."""
    return f' Expected visit SV-{visit.pk} ({visit.visitor_name}) — schedule rule waived.'


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
