"""Live campus occupancy, derived from the gate ledger.

Capacity is a gate question, not a camera question. A vehicle takes up a slot
from the moment a guard scans it in until one scans it out, and the AccessLog
already records exactly that with `paired_entry`. Counting from there is
deterministic and auditable: it does not care about rain, darkness, a camera
that drifted off its mounting, or how a parked car happens to overlap a drawn
box.

The parking cameras keep answering the *other* question — is each car parked
properly, and is anyone straddling two bays. Bay occupancy is a map, not a
count.

Cost
----
`inside_counts()` is ONE database round trip regardless of how many vehicles are
on campus, how many gates are open, or how many zones exist: a single grouped
aggregate with one uncorrelated subquery. The live and stale tallies share that
aggregate via FILTER clauses rather than costing a query each, and nothing is
walked row by row in Python. The existing `accesslog_status_time` index
(status, -scanned_at) serves both the outer filter and the subquery as range
seeks.
"""
from __future__ import annotations

from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from time_utils import day_range

# Six vehicle types, two parking categories. Trucks, vans and buses take a car
# slot; e-bikes take a motorcycle slot. If a bus should ever cost more than one
# car slot this is where that becomes a weight instead of a label.
CATEGORY_BY_TYPE = {
    'car':        'car',
    'truck':      'car',
    'van':        'car',
    'bus':        'car',
    'motorcycle': 'motorcycle',
    'ebike':      'motorcycle',
}

CATEGORIES = ('car', 'motorcycle')

# Entries with no vehicle record — visitors, suppliers, open-campus admits.
UNCATEGORIZED = 'unknown'

# Which category an uncategorised entry consumes.
#
# Nothing in the system can classify these. The vehicle detector is single-class
# by design: it finds vehicles, it does not tell a car from a motorcycle. And an
# unregistered entry has no registration row to read a type from either. So the
# choice is not between guessing and knowing — it is between charging the slot
# to a category or dropping it from capacity entirely.
#
# Dropping it is the worse failure by a distance: twenty uncounted visitor cars
# means the lot reports twenty free slots that do not exist, and guards keep
# waving vehicles into a full campus. Charging them to cars errs the other way —
# the lot reads full slightly early, which turns one vehicle away instead of
# overfilling. Most unregistered entries (deliveries, suppliers, visitor cars)
# are car-sized anyway.
#
# The raw count stays reported separately as `unknown`, so the assumption is
# visible on screen rather than buried in an aggregate.
UNCATEGORIZED_COUNTS_AS = 'car'

# An entry older than this with no exit scan stops counting against capacity.
#
# This is the guard-discipline backstop. A missed exit scan would otherwise hold
# a slot for the rest of the day, and by mid-afternoon a busy gate would report
# a full lot that is half empty. Doing it on the read path rather than as a
# nightly cleanup job means the count self-heals immediately instead of staying
# wrong until midnight.
#
# The trade: a vehicle legitimately parked longer than this drops out of the
# count. On a campus that is rare, and the daily reset at local midnight already
# discards overnight stays, so the exposure is bounded either way.
STALE_ENTRY_HOURS = 12


def empty_counts() -> dict:
    """A zeroed count dict — the shape callers can rely on when the ledger is
    unreachable, so a DB blip degrades to "0 inside" rather than a 500."""
    counts = {cat: 0 for cat in CATEGORIES}
    counts[UNCATEGORIZED] = 0
    counts['total'] = 0
    counts['stale_excluded'] = 0
    return counts


def inside_counts(now=None) -> dict:
    """Vehicles currently on campus, grouped by parking category.

    Returns::

        {'car': int, 'motorcycle': int, 'unknown': int,
         'total': int, 'stale_excluded': int}

    An entry counts when it is today's, authorized, and no exit row points back
    at it. Plates are counted DISTINCT, so a vehicle that somehow accumulated
    two unpaired entry rows still occupies one slot rather than two.

    `stale_excluded` is the reconciliation number: entries dropped by
    STALE_ENTRY_HOURS, i.e. how many exit scans a gate missed today. It is
    reported rather than hidden — a rising number means a gate stopped scanning
    exits, and that is worth seeing on the admin screen.
    """
    from .models import AccessLog

    now = now or timezone.now()
    start, end = day_range(timezone.localdate())
    stale_cutoff = now - timedelta(hours=STALE_ENTRY_HOURS)

    # Today's exits, as the set of entry rows they closed. Values-only, so this
    # stays a subquery and never materialises in Python.
    paired = (
        AccessLog.objects
        .filter(status=AccessLog.Status.EXITED,
                scanned_at__gte=start, scanned_at__lt=end,
                paired_entry__isnull=False)
        .values('paired_entry_id')
    )

    # One grouped aggregate for both tallies.
    #
    # `vehicle__vehicle_type` is a join, not the denormalised
    # AccessLog.vehicle_type column — that column is declared but no write path
    # ever populates it, so reading it would report every vehicle as
    # uncategorised.
    rows = (
        AccessLog.objects
        .filter(status=AccessLog.Status.AUTHORIZED,
                scanned_at__gte=start, scanned_at__lt=end,
                # Future-dated rows from clock skew must not count — the same
                # guard the scan hot path applies.
                scanned_at__lte=now)
        .exclude(pk__in=paired)
        .values('vehicle__vehicle_type')
        .annotate(
            live=Count('plate_number', distinct=True,
                       filter=Q(scanned_at__gte=stale_cutoff)),
            stale=Count('plate_number', distinct=True,
                        filter=Q(scanned_at__lt=stale_cutoff)),
        )
    )

    counts = empty_counts()
    for row in rows:
        category = CATEGORY_BY_TYPE.get(row['vehicle__vehicle_type'] or '', UNCATEGORIZED)
        counts[category] += row['live']
        counts['stale_excluded'] += row['stale']

    counts['total'] = sum(counts[c] for c in (*CATEGORIES, UNCATEGORIZED))
    return counts
