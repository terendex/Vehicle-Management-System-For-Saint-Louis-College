"""Parking capacity per vehicle category.

Three numbers, from two different sources, deliberately:

  * **Capacity** is declared by an admin per zone (`capacity_override`), falling
    back to the number of bays drawn on that zone's map when it has not been set.
  * **Parked** (`occupied`) is the bays the cameras read as taken — vehicles
    actually standing in a space.
  * **On campus** (`on_campus`) comes from the gate ledger — see
    `scanning.occupancy`. A vehicle counts from its entry scan to its exit scan,
    whether it has parked yet or is still looking, dropping someone off, or
    parked somewhere no camera watches.

Free spaces are capacity minus parked, not capacity minus on campus. The gate
count used to drive the free number, which read a car idling at the drop-off
point as a space gone and could not say which lot still had room. The two now
answer the two questions they are each good at: how full the lots are, and how
many vehicles are inside the gates.

A zone whose bays are not monitored yet (no baseline) still contributes its
capacity, and its bays read as whatever they last were. `unmonitored` counts
those zones so a screen can say the parked figure is incomplete.

Cost
----
`category_state()` is three round trips for an entire page — one aggregate over
all zones (capacity, parked bays and unmonitored zones together), one for the
ledger count, and one for the events that might be holding bays back — no
matter how many zones, bays or vehicles exist. Callers pass the result through
serializer context so a list of N zones does not repeat any of them N times.

Flat is the property that matters here, not the constant: the per-zone
serializer this replaced ran three queries *per zone*, so five zones cost
nineteen. Anything added here must stay outside the zone loop.
"""
from __future__ import annotations

import logging

from django.db.models import Count, Q

from scanning.occupancy import (
    CATEGORIES, UNCATEGORIZED, UNCATEGORIZED_COUNTS_AS, empty_counts, inside_counts,
)

log = logging.getLogger(__name__)


def zone_totals() -> dict:
    """Capacity, parked bays and unmonitored zones per category — one query.

    Returns ``{category: {'capacity', 'parked', 'unmonitored'}}``.

    Capacity per zone is `capacity_override` when the admin set one, otherwise
    the count of bays drawn on that zone. Zone rows are a handful, so the
    per-category fold happens in Python rather than as a nested aggregate the
    ORM would have to express as a subquery per row.
    """
    from .models import ParkingZone

    totals = {cat: {'capacity': 0, 'parked': 0, 'unmonitored': 0} for cat in CATEGORIES}
    rows = (
        ParkingZone.objects
        .annotate(n_spaces=Count('spaces'),
                  n_parked=Count('spaces', filter=Q(spaces__is_occupied=True)))
        .values('vehicle_category', 'capacity_override', 'n_spaces', 'n_parked',
                'baseline_image')
    )
    for row in rows:
        entry = totals.get(row['vehicle_category'])
        if entry is None:
            continue
        declared = row['capacity_override']
        entry['capacity'] += row['n_spaces'] if declared is None else declared
        entry['parked']   += row['n_parked']
        if not row['baseline_image']:
            entry['unmonitored'] += 1
    return totals


def category_capacity() -> dict:
    """Declared capacity per category — ``{category: int}``, one query."""
    return {cat: t['capacity'] for cat, t in zone_totals().items()}


def event_reservation() -> dict | None:
    """Parking an event under way right now is expected to take up.

    Returns ``{'name', 'share', 'share_label', 'fraction', 'time_display'}`` for
    the event with the largest declared share, or None when nothing is running.

    Only events that are *under way* reserve anything. An event that is active
    today but starts at 6pm must not make the car park read as half gone at
    nine in the morning — the whole point of recording a time was so the
    reservation follows the clock rather than the calendar.
    """
    from .models import Event

    best = None
    for ev in Event.objects.filter(is_active=True, archived=False):
        if not ev.is_under_way():
            continue
        if ev.share_fraction <= 0:
            continue
        if best is None or ev.share_fraction > best.share_fraction:
            best = ev
    if best is None:
        return None
    return {
        'name':         best.name,
        'share':        best.parking_share,
        'share_label':  best.get_parking_share_display(),
        'fraction':     best.share_fraction,
        'time_display': best.time_display,
    }


def category_state(inside=None, zones=None, event=None) -> dict:
    """Capacity, parking occupancy, vehicles on campus and fullness per category.

    Returns ``{'car': {...}, 'motorcycle': {...}, 'stale_excluded': int}`` where
    each category holds ``capacity``, ``occupied`` (bays parked in),
    ``on_campus`` (gate ledger), ``unmonitored`` (zones with no baseline),
    ``reserved``, ``available``, ``is_full`` and ``fill_pct``.

    ``reserved`` is the share an event under way has declared it will fill. It
    is held back from ``available`` rather than added to ``occupied``: those
    bays are spoken for but no car has driven into them yet, and a screen that
    reported them as occupied would be claiming to have counted vehicles that
    are not there.

    Both halves are injectable so a caller that already fetched them (a list
    endpoint building serializer context) pays for them once.

    A ledger that cannot be read degrades to zero on campus rather than raising:
    a parking screen that shows an optimistic count is recoverable, a 500 in the
    middle of a guard's shift is not. The failure is logged, not swallowed
    silently.
    """
    if inside is None:
        try:
            inside = inside_counts()
        except Exception:
            log.exception("[capacity] gate ledger unreadable; reporting zero on campus")
            inside = empty_counts()
    if zones is None:
        zones = zone_totals()
    if event is None:
        try:
            event = event_reservation()
        except Exception:
            log.exception("[capacity] event reservation unreadable; reserving nothing")
            event = None
    fraction = (event or {}).get('fraction', 0.0)

    # Vehicles on campus with no registration record still take up room. They
    # are charged to one category rather than dropped — see
    # UNCATEGORIZED_COUNTS_AS for why that direction of error is the safe one.
    unknown = inside.get(UNCATEGORIZED, 0)

    state = {}
    for category in CATEGORIES:
        totals = zones.get(category) or {}
        cap = totals.get('capacity', 0)
        occupied = totals.get('parked', 0)
        on_campus = inside.get(category, 0)
        if category == UNCATEGORIZED_COUNTS_AS:
            on_campus += unknown
        # Rounded down, so a declared half of an odd capacity leaves the spare
        # bay usable rather than quietly withheld.
        reserved = min(cap, int(cap * fraction)) if cap > 0 else 0
        state[category] = {
            'capacity':    cap,
            'occupied':    occupied,
            'on_campus':   on_campus,
            'unmonitored': totals.get('unmonitored', 0),
            'reserved':    reserved,
            # Never negative: an override lowered below the live count (or an
            # uncategorised admit) must read as full, not as minus three free.
            'available': max(0, cap - occupied - reserved),
            'is_full':   cap > 0 and occupied + reserved >= cap,
            'fill_pct':  min(100, round((occupied + reserved) / cap * 100)) if cap > 0 else 0,
        }

    state['event']          = event
    state['unknown']        = inside.get(UNCATEGORIZED, 0)
    state['total_inside']   = inside.get('total', 0)
    state['stale_excluded'] = inside.get('stale_excluded', 0)
    return state
