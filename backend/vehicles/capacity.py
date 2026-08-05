"""Parking capacity per vehicle category.

Two halves, from two different sources, deliberately:

  * **Capacity** is declared by an admin per zone (`capacity_override`), falling
    back to the number of bays drawn on that zone's map when it has not been set.
  * **Occupancy** comes from the gate ledger — see `scanning.occupancy`.

Capacity lives at the *category* level rather than the zone level because that
is the honest granularity of the data behind it. The ledger knows thirty-one
cars are on campus; it cannot know which of two car zones each of them drove
into. Reporting a per-zone occupancy from a campus-wide count would be inventing
a number. Zones still own their bay map and their parked-properly status, which
is what the cameras actually measure.

Cost
----
`category_state()` is two round trips for an entire page — one aggregate for
declared capacity across all zones, one for the ledger count — no matter how
many zones, bays or vehicles exist. Callers pass the result through serializer
context so a list of N zones does not repeat either query N times.
"""
from __future__ import annotations

import logging

from django.db.models import Count

from scanning.occupancy import (
    CATEGORIES, UNCATEGORIZED, UNCATEGORIZED_COUNTS_AS, empty_counts, inside_counts,
)

log = logging.getLogger(__name__)


def category_capacity() -> dict:
    """Declared capacity per category — one query.

    Per zone: `capacity_override` when the admin set one, otherwise the count of
    bays drawn on that zone. Summed per category. Zone rows are a handful, so
    the per-category fold happens in Python rather than as a nested aggregate
    the ORM would have to express as a subquery per row.
    """
    from .models import ParkingZone

    totals = {cat: 0 for cat in CATEGORIES}
    rows = (
        ParkingZone.objects
        .annotate(n_spaces=Count('spaces'))
        .values('vehicle_category', 'capacity_override', 'n_spaces')
    )
    for row in rows:
        category = row['vehicle_category']
        if category not in totals:
            continue
        declared = row['capacity_override']
        totals[category] += row['n_spaces'] if declared is None else declared
    return totals


def category_state(inside=None, capacity=None) -> dict:
    """Capacity, occupancy and fullness per category.

    Returns ``{'car': {...}, 'motorcycle': {...}, 'stale_excluded': int}`` where
    each category holds ``capacity``, ``occupied``, ``available``, ``is_full``
    and ``fill_pct``.

    Both halves are injectable so a caller that already fetched them (a list
    endpoint building serializer context) pays for them once.

    A ledger that cannot be read degrades to zero occupancy rather than raising:
    a parking screen that shows an optimistic count is recoverable, a 500 in the
    middle of a guard's shift is not. The failure is logged, not swallowed
    silently.
    """
    if inside is None:
        try:
            inside = inside_counts()
        except Exception:
            log.exception("[capacity] gate ledger unreadable; reporting zero occupancy")
            inside = empty_counts()
    if capacity is None:
        capacity = category_capacity()

    # Vehicles on campus with no registration record still take up room. They
    # are charged to one category rather than dropped — see
    # UNCATEGORIZED_COUNTS_AS for why that direction of error is the safe one.
    unknown = inside.get(UNCATEGORIZED, 0)

    state = {}
    for category in CATEGORIES:
        cap = capacity.get(category, 0)
        occupied = inside.get(category, 0)
        if category == UNCATEGORIZED_COUNTS_AS:
            occupied += unknown
        state[category] = {
            'capacity':  cap,
            'occupied':  occupied,
            # Never negative: an override lowered below the live count (or an
            # uncategorised admit) must read as full, not as minus three free.
            'available': max(0, cap - occupied),
            'is_full':   cap > 0 and occupied >= cap,
            'fill_pct':  min(100, round(occupied / cap * 100)) if cap > 0 else 0,
        }

    state['unknown']        = inside.get(UNCATEGORIZED, 0)
    state['total_inside']   = inside.get('total', 0)
    state['stale_excluded'] = inside.get('stale_excluded', 0)
    return state
