import logging
from datetime import datetime, timedelta

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser
from rest_framework import permissions, status
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.db.models import Q
from vehicles.models import Vehicle, SupplierPlate
from violations.models import Violation, NEW_STYLE_TYPES
from accounts.models import User, AuditLog
from accounts.views import IsAdminRole
from .models import (AccessLog, VisitorPass, Office, MLTrainingSample,
                     GuardShift, open_shift_for)
from .entry_logic import (check_entry, classify_entrant, get_organizer_event, is_open_campus,
                          vehicle_identifiers)
from .ml.reader import read_plate
from .ml.collector import record_scan
from .ml.validator import is_valid_ph_plate
from vehicles.serializers import VehicleSerializer
from .serializers import VisitorPassSerializer, OfficeSerializer, AccessLogSerializer, GuardShiftSerializer, MLTrainingSampleSerializer
from time_utils import day_range, filter_local_date_range

logger = logging.getLogger(__name__)     # messages appear under "scanning.views"

# =============================================================================
# HOW TO READ THIS FILE
#
# This is the gate. Everything a vehicle does at the boundary of the campus
# passes through here: the camera's scan, the guard typing a plate by hand, a
# visitor's printed slip, the exit that closes the visit.
#
# The one idea the whole file is built on is that a plate has a STATE, and the
# same scan means different things depending on it:
#
#     outside  →  a scan is an ENTRY
#     inside   →  a scan is an EXIT
#     just scanned (within a few seconds)  →  the camera saw the same car
#                                             twice; ignore it
#     just exited (within a minute)        →  suppress, or the car would be
#                                             re-admitted as it drives away
#
# There is no "is_inside" column. The state is derived from today's AccessLog
# rows every time it is asked for: an AUTHORIZED row that no EXITED row points
# back at means the vehicle is still in. `_inside_state` is where that is
# worked out, and the three constants below are the windows it uses.
#
# The decision about whether a vehicle MAY enter is not made here — that is
# scanning/entry_logic.py. This file asks it, records the answer, and handles
# everything around it: pairing, slips, violations, overrides, audit.
# =============================================================================


# Where the request actually came from, for the audit trail.
def get_client_ip(request):
    # Behind Railway's proxy REMOTE_ADDR is the proxy, not the caller. The
    # forwarded header is a chain "client, proxy1, proxy2", so the first entry
    # is the original client.
    x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded:
        return x_forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')   # on the campus half there is no proxy, so this is the real address


# The two physical gates plus the fallback, named for the audit log. Gates are
# editable rows now, so this is only the fast path — see _gate_label.
GATE_DISPLAY = {'gate1': 'Gate 1', 'gate4': 'Gate 4', 'main': 'Main'}


def _gate_label(gate_id: str) -> str:
    """Human-readable gate name for audit-log details. Falls back to the dynamic
    Gate row's label so gates beyond gate1/gate4 also read nicely."""
    if gate_id in GATE_DISPLAY:
        return GATE_DISPLAY[gate_id]             # the common case, answered without touching the database
    try:
        from .models import Gate
        g = Gate.objects.filter(gate_id=gate_id).only('label').first()   # .only(): the label is all that is wanted
        if g:
            return g.label
    except Exception:
        # Swallowed on purpose. This is only decoration for an audit line, and
        # a database hiccup here must never fail the scan it is describing.
        pass
    return gate_id or 'Main'                     # the raw id is still readable; 'Main' covers an unattributed scan


# Records what a guard or officer did. Deliberately unable to fail.
def _audit(request, action, details=''):
    try:
        AuditLog.objects.create(
            actor=request.user,                  # who did it
            action=action,
            details=details,                     # the sentence a reviewer will read
            ip_address=get_client_ip(request),   # and from where
        )
    except Exception:
        # A gate that stops admitting vehicles because its audit table is
        # unreachable is worse than a gate with a gap in its log. The vehicle
        # movement itself is recorded on AccessLog, which is not this table.
        pass


# Auto-violations are issued at most once per type per vehicle per calendar day
# (see _auto_log_violation); the counter resets at midnight local time.
# The three windows the state machine runs on. They exist because a camera
# pointed at a gate sees the same car many times as it approaches, waits and
# drives through — and every one of those frames arrives here as a scan.
#
#   0-3s after entry    a second scan is the same car still in frame  → ignore
#   3-60s after entry   the car is in, but too soon to be leaving     → say so
#   60s+ after entry    a scan now genuinely means it is going out    → exit
#   0-60s after exit    the car is driving away past the camera       → suppress
#
# The gap between GRACE and BREATHING is the important one: without it, a car
# that paused in view for four seconds would be logged in and straight back
# out again.
GRACE_PERIOD_SECONDS = 3             # duplicate-scan dedup window after entry (must be well below camera interval)
EXIT_COOLDOWN_SECONDS = 60           # block new entry for this many seconds after an exit
ENTRY_BREATHING_SECONDS = 60         # re-check within this window after entry stays informational (no exit flip)


def _log_status(entry: dict) -> str:
    """Map a check_entry result to a valid AccessLog status. Client-facing
    statuses like 'open_entry' and 'no_pass' aren't AccessLog choices — store
    those rows as AUTHORIZED/DENIED depending on whether entry was granted."""
    if entry['status'] in AccessLog.Status.values:
        return entry['status']                   # a status that is already a column value passes straight through
    # Anything else is a screen label, not a stored state. `allowed` is the
    # only thing the row has to preserve: whether the vehicle got in.
    return AccessLog.Status.AUTHORIZED if entry['allowed'] else AccessLog.Status.DENIED


# The heart of the file: given a plate, is it out, in, or was it just seen?
# Every entry path calls this before deciding what a scan means.
def _inside_state(plate_number: str):
    """
    Returns ('outside', None), ('duplicate', entry), or ('inside', entry).
    'duplicate' — plate just authorized within GRACE_PERIOD_SECONDS; ignore the re-scan.
    'inside'    — plate is in campus but past the grace period; treat re-scan as exit.
    """
    # Scoped to TODAY, campus-local. A vehicle that never scanned out is
    # treated as outside again tomorrow rather than staying "inside" forever —
    # the ledger is a day's record, not a permanent occupancy flag.
    day_start, day_end = day_range(timezone.localdate())
    last_entry = AccessLog.objects.filter(
        plate_number=plate_number,
        status=AccessLog.Status.AUTHORIZED,      # only a granted entry puts a vehicle inside
        scanned_at__gte=day_start,
        scanned_at__lt=day_end,
        scanned_at__lte=timezone.now(),  # ignore future-dated rows from clock skew
    ).order_by('-scanned_at').first()            # the most recent entry is the only one that describes the state now

    if not last_entry:
        return ('outside', None)                 # nothing today: it has not come in

    # Explicit paired-exit check — avoids reverse-FK isnull quirks on self-referential tables
    #
    # Reads "does any row point back at this entry as its exit?". Note the
    # neighbouring _pair_entry_exit asks the same question the other way round,
    # with `exit_log__isnull=True` on the reverse relation.
    if AccessLog.objects.filter(paired_entry=last_entry).exists():
        return ('outside', None)                 # it came in and it left again

    seconds_ago = (timezone.now() - last_entry.scanned_at).total_seconds()
    if seconds_ago <= GRACE_PERIOD_SECONDS:
        # Still in frame from the entry that was just recorded. Returned as its
        # own state, not as 'inside', so the caller ignores the scan rather
        # than treating it as the car leaving three seconds after arriving.
        return ('duplicate', last_entry)

    return ('inside', last_entry)                # in, and old enough that a scan now means something


def _exit_cooldown_remaining(plate_number: str) -> int:
    """Seconds left in the post-exit cooldown, anchored to the exit row itself —
    repeated scans during the window never extend it. 0 when not in cooldown."""
    now = timezone.now()
    cutoff = now - timedelta(seconds=EXIT_COOLDOWN_SECONDS)   # only exits inside the window can still be in cooldown
    last_exit = AccessLog.objects.filter(
        plate_number=plate_number,
        status=AccessLog.Status.EXITED,
        scanned_at__gte=cutoff,
        scanned_at__lte=now,  # future-dated rows (clock skew) must not wedge the gate
    ).order_by('-scanned_at').first()
    if not last_exit:
        return 0                                 # no recent exit: not in cooldown
    # Measured from the exit row, which is what "anchored to the exit itself"
    # in the docstring means — the countdown runs down however many times the
    # camera sees the car on its way out. max(0, ...) guards the boundary case
    # where the row ages past the window between the query and this line.
    return max(0, EXIT_COOLDOWN_SECONDS - int((now - last_exit.scanned_at).total_seconds()))


# The yes/no form, for callers that do not need to say how long is left.
def _in_exit_cooldown(plate_number: str) -> bool:
    """True if this plate exited within EXIT_COOLDOWN_SECONDS — suppress a new entry scan."""
    return _exit_cooldown_remaining(plate_number) > 0


# The same question _inside_state answers, reduced to a boolean — for callers
# that only need "is it in?" and not the three-way distinction.
def _already_inside(plate_number: str) -> bool:
    """True if the plate has an authorized entry today with no paired exit yet."""
    day_start, day_end = day_range(timezone.localdate())
    last_entry = AccessLog.objects.filter(
        plate_number=plate_number,
        status=AccessLog.Status.AUTHORIZED,
        scanned_at__gte=day_start,
        scanned_at__lt=day_end,
        scanned_at__lte=timezone.now(),
    ).order_by('-scanned_at').first()
    if not last_entry:
        return False
    return not AccessLog.objects.filter(paired_entry=last_entry).exists()   # inside exactly when nothing has paired an exit to it


# The batch form: "which of these plates are in?", in one query instead of one
# per plate. Used by the screens that draw a list, never on the scan path.
def _plates_inside(plates) -> set:
    """Which of `plates` are on campus right now — ONE query for the whole set.

    `_inside_state()` answers this for a single plate in two round trips, which
    is the right shape on the scan hot path (it also has to distinguish a
    duplicate re-scan from a genuine exit). Decorating a twelve-result name
    search with it would be twenty-four round trips to draw twelve badges.

    "Inside" here means the same thing the occupancy ledger means: an
    authorized entry today that no exit row points back at.
    """
    plates = [p for p in plates if p]        # drop blanks: an unrecognized row carries no plate
    if not plates:
        return set()                         # nothing to ask about — and an empty __in would match nothing anyway

    day_start, day_end = day_range(timezone.localdate())
    # Every entry that HAS been closed today. Left as a queryset, not
    # evaluated: it is used as a subquery below, so this never round-trips.
    paired = (
        AccessLog.objects
        .filter(status=AccessLog.Status.EXITED, paired_entry__isnull=False,
                scanned_at__gte=day_start, scanned_at__lt=day_end)
        .values('paired_entry_id')           # just the ids, which is all the exclude needs
    )
    # Today's entries for these plates, minus the ones already closed. The same
    # definition _inside_state uses, expressed as set arithmetic instead of a
    # per-plate walk.
    return set(
        AccessLog.objects
        .filter(plate_number__in=plates,
                status=AccessLog.Status.AUTHORIZED,
                scanned_at__gte=day_start, scanned_at__lt=day_end,
                scanned_at__lte=timezone.now())   # the same clock-skew guard as everywhere else
        .exclude(pk__in=paired)
        .values_list('plate_number', flat=True)
    )                                        # a set, so the caller tests membership per row without another query


# Joins an exit row to the entry it closes. This link IS the occupancy ledger:
# an entry with nothing pointing at it is a vehicle still on campus.
def _pair_entry_exit(exit_log) -> None:
    """Link exit_log to the most recent unpaired entry for the same plate today."""
    day_start, day_end = day_range(timezone.localdate())
    entry = AccessLog.objects.filter(
        plate_number=exit_log.plate_number,
        status=AccessLog.Status.AUTHORIZED,
        scanned_at__gte=day_start,
        scanned_at__lt=day_end,
        exit_log__isnull=True,               # 'exit_log' is paired_entry's related_name, so this reads "nothing has closed it yet"
    ).order_by('-scanned_at').first()        # the most recent open entry, so a vehicle in and out twice pairs correctly both times
    if entry:
        # Silently does nothing when there is no open entry — an exit scanned
        # for a vehicle with no recorded entry still stands as a row, it just
        # closes nothing. That is the honest record of what happened.
        exit_log.paired_entry = entry
        exit_log.save(update_fields=['paired_entry'])   # one column; the rest of the exit row is already written


# Open Campus Mode, for a plate nothing knows about. Below is the first of two
# near-identical state machines in this file (the event one follows); both walk
# duplicate → inside → cooldown → entry in that order, and the order is what
# makes them correct.
def _open_campus_unknown_result(plate_number: str, gate_id: str, user) -> dict:
    """
    Open Campus Mode: admit an unregistered plate at the gate. Runs the same
    entry/exit state machine registered vehicles use (grace-period dedup, exit
    pairing, post-exit cooldown) so the plate can enter AND exit cleanly while
    the mode is on. Rows are stored as AUTHORIZED/EXITED (vehicle=None); the
    client-facing entry status is 'open_entry', displayed as "Open Entry".
    """
    from django.db import transaction as _tx   # aliased: `transaction` is not imported at module level here

    inside_status, last_entry = _inside_state(plate_number)   # the one question everything below branches on

    if inside_status == 'duplicate':
        # Checked first because it is the cheapest and the most common: a
        # camera pointed at a gate produces far more repeat frames than events.
        return {
            'status':         'duplicate',
            'allowed':        False,
            'message':        'Duplicate scan — already processed within grace period.',
            'vehicle':        None,
            'already_inside': True,
        }

    if inside_status == 'inside':
        seconds_inside = (timezone.now() - last_entry.scanned_at).total_seconds()
        # Inside, but not long enough for a scan to mean "leaving". Refused
        # rather than logged as an exit, which is what stops a car idling in
        # front of the camera from being recorded in and out repeatedly.
        if seconds_inside < ENTRY_BREATHING_SECONDS:
            return {
                'status':         'already_inside',
                'allowed':        False,
                'message':        'Vehicle just entered — within the 1-minute entry window.',
                'vehicle':        None,
                'already_inside': True,
            }
        # Past the window, so this scan is the exit. Locked because two
        # cameras — or a camera and a guard — can reach this line for the same
        # vehicle at once, and two exit rows against one entry would make the
        # occupancy count wrong in a way nothing later corrects.
        with _tx.atomic():
            locked_entry = AccessLog.objects.select_for_update().filter(pk=last_entry.pk).first()
            # Re-asked while holding the lock. Whoever got here first has
            # already written the exit; this one must not write a second.
            if not locked_entry or AccessLog.objects.filter(paired_entry=locked_entry).exists():
                return {
                    'status':         'duplicate',
                    'allowed':        False,
                    'message':        'Duplicate scan — already processed.',
                    'vehicle':        None,
                    'already_inside': False,
                }
            exit_log = AccessLog.objects.create(
                plate_number=plate_number, status=AccessLog.Status.EXITED,
                gate_id=gate_id, scanned_by=user, paired_entry=locked_entry,   # paired here rather than via _pair_entry_exit: the entry row is already in hand and locked
            )
        # scanned_at is auto_now_add, so the row's own timestamp is the moment
        # it was written — the duration is measured from the rows themselves
        # rather than from a clock read at either end.
        duration_minutes = int((exit_log.scanned_at - last_entry.scanned_at).total_seconds() / 60)
        return {
            'status':           'exited',
            'allowed':          False,
            'message':          f'Open Campus — exit recorded. Duration: {duration_minutes} min.',
            'vehicle':          None,
            'already_inside':   False,
            'duration_minutes': duration_minutes,
        }

    # Outside — but it may have only just left. Without this the camera would
    # catch the car again as it drives off and admit it straight back in.
    if _in_exit_cooldown(plate_number):
        return {
            'status':         'duplicate',
            'allowed':        False,
            'message':        'Exit cooldown — entry suppressed for 1 minute after exit.',
            'vehicle':        None,
            'already_inside': False,
        }

    # Genuinely outside and out of cooldown: this is an entry. Written with no
    # vehicle, because in this mode there is no record of one — the plate text
    # on the row is the whole of what is known.
    AccessLog.objects.create(
        plate_number=plate_number, status=AccessLog.Status.AUTHORIZED,
        gate_id=gate_id, scanned_by=user,
    )
    return {
        # 'open_entry' is a screen label, not an AccessLog status — the row
        # above was stored as AUTHORIZED. See _log_status for that split.
        'status':         'open_entry',
        'allowed':        True,
        'message':        'Open Campus Mode active — unregistered plate. Open entry granted.',
        'vehicle':        None,
        'has_violations': False,             # nothing to check: there is no vehicle record to carry violations
        'already_inside': False,
    }


# May a supplier come in at all right now? A question about the clock and the
# calendar only — which supplier it is does not enter into it.
def _supplier_rule_denial() -> str | None:
    """Day/time-window check for supplier entries against the supplier
    RuleConstraint. Returns a denial message, or None when entry is allowed.
    Open Campus Mode bypasses the restriction like every other rule."""
    from vehicles.models import SystemSettings
    # Borrowed from entry_logic rather than reimplemented, so "is today
    # allowed" and "are we inside the hours" mean exactly what they mean for
    # students and employees.
    from .entry_logic import _get_active_rule, _is_within_days, _is_within_window
    rule = _get_active_rule('supplier')
    # No rule configured means no restriction — an unconfigured system admits
    # suppliers rather than turning them all away. Open Campus overrides it the
    # same way it overrides every other rule.
    if not rule or SystemSettings.get().open_campus_mode:
        return None
    if not _is_within_days(rule):
        day_name = timezone.localdate().strftime('%A')   # the day named, so the guard can tell the driver something useful
        return f'Supplier access restricted. Today ({day_name}) is not allowed by rule: {rule.name}.'
    if not _is_within_window(rule):
        # The hours and the rule's name, for the same reason: a driver turned
        # away should learn when to come back and under what rule.
        return (f'Supplier access restricted. Outside allowed hours '
                f'({rule.start_time}–{rule.end_time}) per rule: {rule.name}.')
    return None                                  # None is the allow case, so callers read `if denial:`


# Is this unknown plate here for an event? Asked before the supplier roster,
# for the reason spelled out at the end of the docstring.
def _event_for_unregistered_plate(plate_number: str):
    """The event an unregistered plate is to be handled under, or None.

    An organizer listed on an event under way, first. Failing that, an event
    this plate was already admitted for today and has not left: the event may
    have ended while the organizer was still inside, and they must still be
    able to drive out on the same plate check that let them in — falling
    through to "Plate not registered" would strand the exit.

    Callers ask this before the supplier roster. A supplier the CDSO listed as
    an organizer is coming for the event: while it is on, they enter under the
    event (event slip, counted against the event's parking share, supplier
    hours do not turn them away) and are an ordinary supplier again after.
    """
    from .entry_logic import organizer_event_for
    event = organizer_event_for(plate_number)
    if event:
        return event                             # listed on an event that is under way right now
    # Nothing current. But the vehicle may be INSIDE under an event that has
    # since ended, and it still has to be able to drive out — so the event is
    # recovered from the entry row that admitted it.
    inside_status, last_entry = _inside_state(plate_number)
    # `!= 'outside'` covers both 'inside' and 'duplicate', since last_entry is
    # non-None for both and either way the row describes the same visit.
    # event_id, not event: this only asks whether one was recorded, without
    # fetching it until the return.
    if (inside_status != 'outside' and last_entry.event_id
            and last_entry.entrant_category == AccessLog.Category.EVENT):
        return last_entry.event
    return None


# The second of the two state machines. Same four steps as the open-campus one
# above — duplicate, inside, cooldown, entry — with an event's slip and
# category written onto the rows.
def _event_plate_result(plate_number: str, event, gate_id: str, user) -> dict:
    """Entry/exit for an unregistered organizer plate during its event.

    The supplier state machine, applied to an event's plate list: the first
    check logs an entry and hands back an event slip to print, a re-check past
    the entry window logs the exit, and the usual duplicate/cooldown guards
    apply. Registered owners who are organizers never come here — they keep
    their own rules and only carry the organizer label. Returns the response
    body without plate_number/gate_id, which each caller adds.
    """
    from django.db import transaction as _tx
    from .entry_logic import event_summary
    from .slips import event_slip

    # Every return below is `{**base, ...}`, so these four keys are on the
    # response whichever branch answers — the guard page can render the event
    # banner without having to know which outcome it got.
    base = {'vehicle': None, 'is_event': True, 'organizer_event': event_summary(event),
            'has_violations': False}
    who = f'Event organizer — {event.name}'      # named once; every message below starts with it
    inside_status, last_entry = _inside_state(plate_number)

    if inside_status == 'duplicate':
        return {**base, 'status': 'duplicate', 'allowed': False, 'already_inside': True,
                'message': 'Duplicate scan — already processed within grace period.'}

    if inside_status == 'inside':
        seconds_inside = (timezone.now() - last_entry.scanned_at).total_seconds()
        if seconds_inside < ENTRY_BREATHING_SECONDS:
            window_left = int(ENTRY_BREATHING_SECONDS - seconds_inside)
            # Unlike the open-campus version, this one tells the guard how long
            # to wait and why — they are standing at the gate with the driver,
            # and "try again in 40s" is actionable where a flat refusal is not.
            return {**base, 'status': 'already_inside', 'allowed': False, 'already_inside': True,
                    'retry_after_seconds': window_left,
                    'message': f'{who} just entered. Re-check in {window_left}s to record an exit.'}
        with _tx.atomic():
            locked_entry = AccessLog.objects.select_for_update().filter(pk=last_entry.pk).first()
            if not locked_entry or AccessLog.objects.filter(paired_entry=locked_entry).exists():
                return {**base, 'status': 'duplicate', 'allowed': False, 'already_inside': False,
                        'message': 'Duplicate scan — already processed.'}
            exit_log = AccessLog.objects.create(
                plate_number=plate_number, status=AccessLog.Status.EXITED,
                gate_id=gate_id, scanned_by=user, paired_entry=locked_entry,
                # The event the vehicle CAME IN under wins over the one passed
                # in, so a visit that straddles the end of an event still
                # closes against the event it began under.
                event=locked_entry.event or event, entrant_category=AccessLog.Category.EVENT,
            )
        duration = int((exit_log.scanned_at - last_entry.scanned_at).total_seconds() / 60)
        return {**base, 'status': 'exited', 'allowed': False, 'already_inside': False,
                'duration_minutes': duration,
                'message': f'{who}. Exit recorded. Duration: {duration} min.'}

    # The remaining-seconds form here, not the boolean: same reason as above,
    # the guard is told how long rather than simply refused.
    cooldown_left = _exit_cooldown_remaining(plate_number)
    if cooldown_left:
        return {**base, 'status': 'duplicate', 'allowed': False, 'already_inside': False,
                'retry_after_seconds': cooldown_left,
                'message': f'Exit cooldown — entry suppressed for {cooldown_left}s more.'}

    # The entry. `event` and the EVENT category are written onto the row, not
    # re-derived later: the event's plate list can be edited afterwards, and
    # this visit must keep saying which event admitted it.
    entry_log = AccessLog.objects.create(
        plate_number=plate_number, status=AccessLog.Status.AUTHORIZED,
        gate_id=gate_id, scanned_by=user,
        event=event, entrant_category=AccessLog.Category.EVENT,
    )
    return {**base, 'status': 'authorized', 'allowed': True, 'already_inside': False,
            'message': f'{who}. Entry permitted.',
            'event_slip': event_slip(entry_log)}   # printed by the guard page


def _is_standby_fetcher(user) -> bool:
    """Standby fetchers are allowed to park inside campus while waiting, so the
    fetcher max-stay limit does not apply to them (only to Drop & Go)."""
    # bool(user) first, so an unregistered plate (no account at all) answers
    # False without a query. Only an ACCEPTED registration counts — a pending
    # application claiming standby must not lift the limit.
    return bool(user) and user.registrations.filter(
        status='accepted', registrant_type='fetcher', fetcher_type='standby',
    ).exists()


# Called at EXIT, once the duration is known: did they stay longer than their
# rule allows, and if so, issue the violation for it.
def _check_stay_limit(plate_number: str, vehicle, constraint_type: str,
                      duration_minutes: int, gate_id: str = '', evidence_bytes=None) -> int:
    """
    Enforce the RuleConstraint max-stay limit for this constraint type at exit
    time. Returns overstay minutes (0 if none/no limit) and auto-issues a
    time-exceed violation when exceeded. Supplier plates have no Vehicle row,
    so one is adopted/created (unauthorized, unowned) to carry the violation.
    """
    from vehicles.models import RuleConstraint, Vehicle
    # Three conditions on one query: the right kind of rule, switched on, and
    # actually carrying a limit. A rule with no max_stay_minutes restricts
    # hours and days without capping how long a visit may run.
    rule = RuleConstraint.objects.filter(
        constraint_type=constraint_type, enabled=True,
        max_stay_minutes__isnull=False,
    ).first()
    if not rule or duration_minutes <= rule.max_stay_minutes:
        return 0                                 # no limit, or inside it — 0 is the "nothing to report" answer
    overstay = duration_minutes - rule.max_stay_minutes   # how far over, which is what the violation and the guard message both quote
    if vehicle is None:
        # A supplier or event plate has no Vehicle row, and a violation has to
        # hang off one. Created unowned and unauthorized, so it carries the
        # record without granting the plate anything.
        vehicle, _ = Vehicle.objects.get_or_create(
            plate_number=plate_number,
            defaults={'vehicle_type': 'car', 'is_authorized': False},   # 'car' is a placeholder: the real type is unknown at the gate
        )
    try:
        _auto_log_violation(
            vehicle,
            f'Overstay: exceeded allowed {rule.max_stay_minutes} min stay by {overstay} min '
            f'(rule: {rule.name})',
            gate_id,
            vtype=Violation.Type.TIME_EXCEED,
            evidence_bytes=evidence_bytes,       # the camera frame, when the caller had one
        )
    except Exception:
        # Swallowed so a failure to record the violation cannot block the exit
        # itself. The vehicle is leaving either way; the gate must not hold it.
        pass
    return overstay                              # returned regardless, so the guard is told about the overstay even if logging it failed


# Today's open visitor pass for a plate. A lookup only — see the docstring for
# why finding one never closes it.
def _active_visitor_pass(plate_number: str):
    """Today's ACTIVE visitor pass for this plate, or None. A plate check on one
    never logs the visitor out: it returns the slip, and the guard records the
    exit from it (SlipExitView) — whether they scanned the QR or typed it."""
    return VisitorPass.objects.filter(
        plate_number=plate_number,
        valid_date=timezone.localdate(),         # a pass is good for one day only
        status=VisitorPass.Status.ACTIVE,        # not one already exited or expired
    ).order_by('-entered_at').first()            # the newest, for a visitor who came twice in a day


# The exit half: closes the pass and reports how long they overstayed. Called
# from the guard's exit paths — see the note in ScanView about which paths do
# NOT call it.
def _close_active_pass(plate_number: str, gate_id: str = '', evidence_bytes=None) -> int:
    """
    Mark today's ACTIVE visitor pass for this plate as exited — called from every
    exit path (camera toggle, manual Record Exit, QR scan) so passes don't stay
    open after the visitor leaves. Returns overstay in minutes (0 if none).
    An overstay also auto-issues a 'time_exceed' violation (once per day).
    """
    now = timezone.now()                         # read once, so the close and the overstay maths use the same instant
    # The same query _active_visitor_pass runs, repeated rather than called —
    # note the two can drift if either is edited alone.
    pass_ = VisitorPass.objects.filter(
        plate_number=plate_number,
        valid_date=timezone.localdate(),
        status=VisitorPass.Status.ACTIVE,
    ).order_by('-entered_at').first()
    if not pass_:
        return 0                                 # no open pass: this exit is not a visitor's, and 0 means "nothing to report"
    # Closed FIRST, before any overstay work. A failure while issuing the
    # violation below must not leave the pass open — a stuck ACTIVE pass would
    # keep the visitor counted as on campus indefinitely.
    pass_.status = VisitorPass.Status.EXITED
    pass_.exited_at = now
    pass_.save(update_fields=['status', 'exited_at'])
    # expires_at is entry time plus the allowance, and is nullable — a pass
    # issued with no time limit simply cannot be overstayed.
    if pass_.expires_at and now > pass_.expires_at:
        overstay = int((now - pass_.expires_at).total_seconds() / 60)
        try:
            _auto_log_violation(
                pass_.vehicle,
                f'Visitor overstay: exceeded allowed {pass_.allowed_duration} min by {overstay} min',
                gate_id,
                vtype=Violation.Type.TIME_EXCEED,
                evidence_bytes=evidence_bytes,
            )
        except Exception:
            # Same rule as everywhere on this path: the visitor is leaving, and
            # a failure to record the overstay must not hold them at the gate.
            pass
        return overstay                          # reported even if the violation could not be written
    return 0                                     # left on time


# The camera frame that came with this request, read WITHOUT consuming it.
def _request_image_bytes(request):
    """Bytes of the frame the guard's device posted with this scan, or None.

    Read non-destructively: the same upload is also saved as the AccessLog
    snapshot, and consuming the stream without rewinding leaves whichever
    consumer runs second with an empty file.
    """
    # getattr first: a JSON request has no FILES at all, and this helper is
    # called from paths that may or may not be multipart.
    f = getattr(request, 'FILES', None) and request.FILES.get('image')
    if not f:
        return None
    try:
        pos = f.tell()                           # remember where the other reader left off
        f.seek(0)
        data = f.read()
        f.seek(pos)                              # and put it back — this is the "non-destructive" part of the docstring
        return data or None                      # an empty upload reads as no evidence rather than as b''
    except Exception:
        return None                              # evidence is a nicety; never let it break the scan


# Issues a violation from the gate, with no human deciding to. Everything here
# is about NOT issuing too many: one car in front of a camera generates scans
# continuously, and each one would otherwise be another offence.
def _auto_log_violation(vehicle, message: str, gate_id: str = '', vtype: str = '',
                        evidence_bytes=None, entry_status: str = ''):
    """
    Auto-issue a violation at the gate — at most ONE violation of each type per
    vehicle per calendar day, no matter how often it is scanned or detected that
    day. A new day allows the type to be issued again.

    The per-day cap matters more than it used to: a confiscated account being
    detected is itself an offence, so without it a car sitting in front of a
    camera would climb the whole ladder in a minute.

    Past violations stay stored, and the cumulative (non-cleared) count per
    ACCOUNT drives the penalty — 1st offence costs a week of campus access, 2nd
    two weeks, 3rd the rest of the registration period.
    """
    from .models import active_guard_for_gate

    # Turning up at a gate while confiscated is its own offence, not another
    # "unauthorized entry" — the CDSO needs to see that the penalty was ignored
    # rather than that an unregistered car showed up.
    if not vtype and entry_status == 'confiscated':
        vtype = Violation.Type.CONFISCATED_ACTIVITY
    vtype = vtype or Violation.Type.UNAUTHORIZED_ENTRY   # the catch-all when the caller named no type

    _day_start, _day_end = day_range(timezone.localdate())   # the cap below is per CALENDAR DAY, campus-local
    owner = vehicle.user                         # None for a gate-created vehicle with no account behind it

    # ── One auto-logged offence per ACCOUNT per calendar day ─────────────────
    # The cap used to be per vehicle AND per type, which was right while each
    # type had its own ladder. Now that the ladder is one per account, a
    # per-type cap lets a single incident spend the whole ladder in seconds:
    # the first denied scan confiscates the account, and the very next scan is
    # "activity while confiscated" — a different type, so the old check waved it
    # through. Two scans of the same car became two strikes.
    #
    # One strike per day per account. A second incident tomorrow still counts.
    if owner is not None:
        # Any ladder-bearing type at all, not just this one — that is the whole
        # point of the block comment above. One strike per account per day.
        if Violation.objects.filter(
            owner=owner,
            violation_type__in=NEW_STYLE_TYPES,
            issued_at__gte=_day_start,
            issued_at__lt=_day_end,
        ).exists():
            return                               # already struck today; this scan adds nothing
    else:
        # No account behind the plate (gate-issued vehicle) — fall back to the
        # per-vehicle, per-type cap, which is all that can be keyed on.
        dedup_types = [vtype]
        # Rows written before the type was renamed still count as the same
        # offence, so a plate is not struck twice for one thing across the
        # rename boundary.
        if vtype == Violation.Type.UNAUTHORIZED_ENTRY:
            dedup_types.append(Violation.Type.UNAUTHORIZED)  # legacy auto-logged rows
        if Violation.objects.filter(
            vehicle=vehicle,
            violation_type__in=dedup_types,
            issued_at__gte=_day_start,
            issued_at__lt=_day_end,
        ).exists():
            return

    offense_num  = Violation.compute_offense_number(owner)   # which strike this is for the ACCOUNT, not the vehicle
    violation = Violation.objects.create(
        vehicle              = vehicle,
        owner                = owner,
        violation_type       = vtype,
        notes                = f'Auto-logged at gate: {message}',
        offense_number       = offense_num,
        status               = Violation.Status.WARNING,   # issued, not yet acted on by the CDSO
        # Only the 3rd strike holds registration.
        registration_blocked = offense_num >= 3,
        is_released          = True,  # visible to the owner immediately
        on_duty_guard        = active_guard_for_gate(gate_id),   # who was on the gate, so the record is attributable even though no human issued it
    )
    # Last resort: no frame was handed in, so take the newest one the gate
    # camera has. A violation with no photo is one nobody can contest or
    # confirm later, which is exactly what the lift flow needs to judge.
    if not evidence_bytes:
        try:
            from .gate_frames import latest_jpeg_for_gate
            evidence_bytes = latest_jpeg_for_gate(gate_id)   # whatever that gate's camera has most recently held
        except Exception:
            evidence_bytes = None                # still better to issue the violation with no photo than not at all

    # Attach the camera frame as evidence (shown in admin table + owner email)
    if evidence_bytes:
        try:
            from django.core.files.base import ContentFile
            violation.evidence.save(
                # Plate plus a unix timestamp, so two violations for the same
                # car on the same day cannot overwrite each other's evidence.
                f"auto_{vehicle.plate_number}_{int(timezone.now().timestamp())}.jpg",
                ContentFile(evidence_bytes), save=True,
            )
        except Exception:
            pass                                 # storage (R2) being unreachable must not undo the violation itself
    # Impose the ladder, then tell the owner. Both are best-effort: the
    # violation itself is already recorded and must not be rolled back by a
    # mail server being down.
    try:
        from violations.penalty import apply_penalty, notify_owner
        penalty = apply_penalty(violation)
        notify_owner(violation, penalty)
    except Exception:
        logger.exception('Could not apply penalty for violation %s', violation.pk)


# The camera's endpoint: one frame in, a decision per plate out.
#
# The order of the questions is the whole design. A plate is looked at like
# this, and the FIRST match wins:
#
#   1. Is the text even a plate?          → unreadable, logged, next
#   2. Is there a Vehicle record?         → the registered path (bottom half)
#   3. No record. Is it on an event?      → admitted under the event
#   4. Is it on the supplier roster?      → the supplier path
#   5. Is Open Campus Mode on?            → admitted as an open entry
#   6. Otherwise                          → "Plate not registered"
#
# Events outrank suppliers deliberately (see _event_for_unregistered_plate),
# and Open Campus is asked last so that a plate with a real reason to be here
# is admitted for that reason rather than as an anonymous open entry.
#
# Each of paths 3, 4 and 5 then runs the same four-step state machine — the
# duplicate / inside / cooldown / entry sequence from the top of the file. The
# supplier one and the registered one are written out inline below; the other
# two live in the helpers above.
class ScanView(APIView):
    parser_classes     = [MultiPartParser]   # a frame is posted as a file, so this endpoint is multipart only
    permission_classes = [permissions.IsAuthenticated]   # the camera client signs in as a guard account

    def post(self, request):
        file = request.FILES.get('image')
        if not file:
            return Response({'error': 'No image provided'}, status=400)

        raw_bytes = file.read()                  # read once; everything below works from these bytes
        plates = read_plate(raw_bytes)           # detection + OCR — may return several plates from one frame
        # Reuse the detections/OCR just computed — record_scan would otherwise
        # run the entire pipeline a second time on the same bytes.
        ml_sample = record_scan(raw_bytes, results=plates)

        results = []                             # one entry per plate found, in the order they were detected

        if not plates:
            # Logged even though nothing was read. A frame the camera could not
            # make sense of is itself worth recording — a gate that suddenly
            # produces only unreadable rows is a gate with a problem.
            AccessLog.objects.create(plate_number='', status='unreadable', scanned_by=request.user)
            return Response({
                'status': 'unreadable',
                'message': 'Could not read a valid PH plate.',
                'results': [],
                'sample_id': ml_sample.get("sample_id") if ml_sample else None,
            })

        # A gate explicitly supplied by the camera/client wins; otherwise fall
        # back to the scanning guard's own gate so the scan lands in that gate's
        # log rather than the orphan 'main' bucket (visible in no gate's view).
        gate_id = (request.data.get('gate_id') or request.query_params.get('gate_id') or '').strip()
        # 'main' is treated as "not supplied", not as a real gate: it is the
        # model's default, so a client that simply never set one would
        # otherwise pin every scan to the orphan bucket.
        if not gate_id or gate_id == 'main':
            gate_id = getattr(request.user, 'gate_assignment', None) or 'main'

        # One frame can contain several vehicles, and each is decided
        # independently — one plate being unreadable does not stop the others.
        for plate_info in plates:
            plate = plate_info["plate_text"]
            bbox = plate_info["bbox"]            # where in the frame, so the guard page can draw the box

            # Step 1. OCR returns text; this asks whether the text is shaped
            # like a Philippine plate at all. Without it, a road sign or a
            # sticker read off the back of a van would be looked up as a plate.
            if not is_valid_ph_plate(plate):
                AccessLog.objects.create(plate_number=plate, status=AccessLog.Status.UNREADABLE, gate_id=gate_id, scanned_by=request.user)
                results.append({
                    'plate_number': plate,
                    'status': 'unreadable',
                    'allowed': False,
                    'message': 'Detected text does not match a valid Philippine plate format.',
                    'bbox': bbox,
                    'sample_id': ml_sample.get("sample_id") if ml_sample else None,
                })
                continue

            # Step 2. Plate first, then conduction number, both normalised.
            # Matches ANY Vehicle row — including the unowned ones the gate
            # creates for visitors and suppliers, not just registered cars.
            vehicle = Vehicle.resolve(plate)

            if not vehicle:                      # ── no record: steps 3 to 6 ──
                # An organizer list outranks the supplier roster while its
                # event is on — see _event_for_unregistered_plate.
                event = _event_for_unregistered_plate(plate)
                if event:
                    r = _event_plate_result(plate, event, gate_id, request.user)
                    results.append({
                        **r,
                        'plate_number': plate,
                        'bbox': bbox,
                        'sample_id': ml_sample.get("sample_id") if ml_sample else None,
                    })
                    continue

                # Step 4. Only an ACTIVE supplier's plates count, so
                # deactivating a company stops its vehicles at the gate without
                # anyone editing the plate list.
                supplier_plate = SupplierPlate.objects.select_related('supplier').filter(
                    plate_number=plate, supplier__is_active=True
                ).first()

                if not supplier_plate:
                    if is_open_campus():         # step 5, asked only once every specific reason has failed
                        r = _open_campus_unknown_result(plate, gate_id, request.user)
                        results.append({
                            **r,
                            'plate_number': plate,
                            'bbox': bbox,
                            'sample_id': ml_sample.get("sample_id") if ml_sample else None,
                        })
                        continue
                    AccessLog.objects.create(plate_number=plate, status='unknown', gate_id=gate_id, scanned_by=request.user)
                    results.append({
                        'plate_number': plate,
                        'status': 'unknown',
                        'message': 'Plate not registered.',
                        'bbox': bbox,
                        'sample_id': ml_sample.get("sample_id") if ml_sample else None,
                    })
                    continue

                # ── the supplier state machine, written out inline ──
                # The same four steps as _open_campus_unknown_result and
                # _event_plate_result, with the company's name in every message
                # and a rule check before the entry.
                supplier_name = supplier_plate.supplier.company_name
                inside_status, last_entry = _inside_state(plate)

                if inside_status == 'duplicate':
                    results.append({
                        'plate_number':   plate,
                        'status':         'duplicate',
                        'allowed':        False,
                        'message':        'Duplicate scan — already processed within grace period.',
                        'is_supplier':    True,
                        'supplier_name':  supplier_name,
                        'already_inside': True,
                        'bbox':           bbox,
                        'sample_id':      ml_sample.get("sample_id") if ml_sample else None,
                    })
                    continue

                # Note: no ENTRY_BREATHING_SECONDS check on this path, unlike
                # the open-campus and event helpers — a supplier scanned again
                # at any point past the 3-second grace window is treated as
                # leaving. Stated as found; nothing changed here.
                if inside_status == 'inside':
                    from django.db import transaction as _tx
                    with _tx.atomic():           # locked for the same reason as in the helpers: two scans must not write two exits
                        locked_entry = AccessLog.objects.select_for_update().filter(pk=last_entry.pk).first()
                        if not locked_entry or AccessLog.objects.filter(paired_entry=locked_entry).exists():
                            results.append({'plate_number': plate, 'status': 'duplicate', 'allowed': False,
                                            'message': 'Duplicate scan — already processed.', 'bbox': bbox})
                            continue
                        exit_log = AccessLog.objects.create(
                            plate_number=plate, status=AccessLog.Status.EXITED,
                            gate_id=gate_id, scanned_by=request.user, paired_entry=locked_entry,
                        )
                    delta = exit_log.scanned_at - last_entry.scanned_at
                    duration_minutes = int(delta.total_seconds() / 60)
                    results.append({
                        'plate_number':     plate,
                        'status':           'exited',
                        'allowed':          False,
                        'message':          f'Supplier vehicle — {supplier_name}. Exit recorded. Duration: {duration_minutes} min.',
                        'is_supplier':      True,
                        'supplier_name':    supplier_name,
                        'already_inside':   False,
                        'duration_minutes': duration_minutes,
                        'bbox':             bbox,
                        'sample_id':        ml_sample.get("sample_id") if ml_sample else None,
                    })
                    continue

                if _in_exit_cooldown(plate):
                    results.append({
                        'plate_number':   plate,
                        'status':         'duplicate',
                        'allowed':        False,
                        'message':        'Exit cooldown — entry suppressed for 1 minute after exit.',
                        'is_supplier':    True,
                        'supplier_name':  supplier_name,
                        'already_inside': False,
                        'bbox':           bbox,
                    })
                    continue

                # Asked only now, at the point of ENTRY. A supplier already
                # inside when their allowed hours end must still be able to
                # drive out, which is why the exit branch above never asks.
                deny_msg = _supplier_rule_denial()
                if deny_msg:
                    AccessLog.objects.create(
                        plate_number=plate, status=AccessLog.Status.DENIED,
                        denied_reason=deny_msg, gate_id=gate_id, scanned_by=request.user,
                    )
                    results.append({
                        'plate_number':  plate,
                        'status':        'denied',
                        'allowed':       False,
                        'message':       deny_msg,
                        'is_supplier':   True,
                        'supplier_name': supplier_name,
                        'bbox':          bbox,
                        'sample_id':     ml_sample.get("sample_id") if ml_sample else None,
                    })
                    continue

                entry_log = AccessLog.objects.create(
                    plate_number=plate, status=AccessLog.Status.AUTHORIZED,
                    gate_id=gate_id, scanned_by=request.user,
                )
                # Only changes the WORDING, not the outcome: the supplier was
                # admitted on their own roster either way. It tells the guard
                # which rule let them in, which matters when hours are off.
                open_campus = is_open_campus()
                from .slips import supplier_slip
                results.append({
                    'plate_number':  plate,
                    'status':        'open_entry' if open_campus else 'authorized',
                    'allowed':       True,
                    'message':       (f'Open Campus Mode active — Supplier vehicle {supplier_name}. Open entry granted.'
                                      if open_campus else
                                      f'Supplier vehicle — {supplier_name}. Entry permitted.'),
                    'is_supplier':   True,
                    'supplier_name': supplier_name,
                    'supplier_slip': supplier_slip(entry_log),   # printed by the guard page
                    'bbox':          bbox,
                    'sample_id':     ml_sample.get("sample_id") if ml_sample else None,
                })
                continue

            # ── the registered-vehicle path ──
            # Third and last copy of the state machine. It differs from the
            # three above in one way that matters: the entry branch at the
            # bottom asks entry_logic whether this vehicle may come in at all,
            # where the others already know the answer from the roster or the
            # mode that got them here.
            inside_status, last_entry = _inside_state(plate)

            if inside_status == 'duplicate':
                resp = {
                    'plate_number':   plate,
                    'status':         'duplicate',
                    'allowed':        False,
                    'message':        'Duplicate scan — already processed within grace period.',
                    'vehicle':        VehicleSerializer(vehicle).data,
                    'already_inside': True,
                    'bbox':           bbox,
                }
                if ml_sample:
                    resp['sample_id'] = ml_sample['sample_id']
                results.append(resp)
                continue

            if inside_status == 'inside':
                from django.db import transaction as _tx
                with _tx.atomic():
                    locked_entry = AccessLog.objects.select_for_update().filter(
                        pk=last_entry.pk
                    ).first()
                    if not locked_entry or AccessLog.objects.filter(paired_entry=locked_entry).exists():
                        results.append({'plate_number': plate, 'status': 'duplicate', 'allowed': False,
                                        'message': 'Duplicate scan — already processed.', 'bbox': bbox})
                        continue
                    exit_log = AccessLog.objects.create(
                        plate_number=plate,
                        vehicle=vehicle,
                        status=AccessLog.Status.EXITED,
                        gate_id=gate_id,
                        scanned_by=request.user,
                        paired_entry=locked_entry,
                    )

                delta = exit_log.scanned_at - last_entry.scanned_at
                duration_minutes = int(delta.total_seconds() / 60)

                # Note, factually: this exit records the row and pairs it, but
                # does not call _close_active_pass or _check_stay_limit. The
                # guard's exit paths (ExitLogView, ManualEntryView,
                # UnrecognizedExitView) call both. A visitor's plate DOES reach
                # this branch — VisitorPass.vehicle is a real Vehicle row and
                # Vehicle.resolve matches unowned rows — so a visitor whose
                # exit is caught by the camera leaves their pass ACTIVE and
                # their overstay unchecked. Recorded, not changed.
                owner_name = vehicle.user.full_name if vehicle.user else 'Unknown'   # 'Unknown' for a gate-created row with no account

                resp = {
                    'plate_number':    plate,
                    'status':          'exited',
                    'allowed':         False,
                    'message':         f'{owner_name} — Exit recorded. Duration: {duration_minutes} min.',
                    'vehicle':         VehicleSerializer(vehicle).data,
                    'already_inside':  False,
                    'organizer_event': get_organizer_event(*vehicle_identifiers(vehicle, plate)),
                    'duration_minutes': duration_minutes,
                    'bbox':            bbox,
                }
                if ml_sample:
                    resp['sample_id'] = ml_sample['sample_id']
                    resp['ml_confidence'] = ml_sample['confidence']
                results.append(resp)
                continue

            if _in_exit_cooldown(plate):
                resp = {
                    'plate_number':   plate,
                    'status':         'duplicate',
                    'allowed':        False,
                    'message':        'Exit cooldown — entry suppressed for 1 minute after exit.',
                    'vehicle':        VehicleSerializer(vehicle).data,
                    'already_inside': False,
                    'bbox':           bbox,
                }
                if ml_sample:
                    resp['sample_id'] = ml_sample['sample_id']
                results.append(resp)
                continue

            # The actual decision, and the only place in this method that
            # asks for one: day, hours, confiscation, registration status. This
            # file records what entry_logic decides; it does not decide.
            entry = check_entry(vehicle)
            has_violations = Violation.objects.filter(vehicle=vehicle, is_resolved=False).exists()   # shown as a flag to the guard; does not itself refuse entry
            already_inside = _already_inside(plate)   # re-asked as a plain boolean for the response body

            # Written whatever the decision was. A refusal is as much a part
            # of the gate's record as an admission — more so, since it is the
            # one somebody will later ask about.
            AccessLog.objects.create(
                plate_number  = plate,
                vehicle       = vehicle,
                status        = _log_status(entry),   # the screen status mapped onto a storable one
                denied_reason = '' if entry['allowed'] else entry['message'],   # the sentence the guard was shown, kept verbatim
                gate_id       = gate_id,
                scanned_by    = request.user,
                snapshot      = request.FILES.get('image'),   # the frame itself, attached to the row
            )

            # 'no_pass'/'unknown' mean a visitor awaiting a pass — not a violation
            # Refused AND at fault. The two excluded statuses are the ones
            # where refusal is simply the process working: a visitor who has
            # not been issued a pass yet has done nothing wrong.
            if not entry['allowed'] and entry['status'] not in ('no_pass', 'unknown'):
                _auto_log_violation(vehicle, entry['message'], gate_id,
                                    evidence_bytes=_request_image_bytes(request),   # read non-destructively: the snapshot above uses the same upload
                                    entry_status=entry['status'])   # lets the helper tell "confiscated" apart from ordinary refusal

            resp = {
                'plate_number':    plate,
                'status':          entry['status'],
                'allowed':         entry['allowed'],
                'message':         entry['message'],
                'constraint':      entry.get('constraint'),
                'vehicle':         VehicleSerializer(vehicle).data,
                'has_violations':  has_violations,
                'already_inside':  already_inside,
                'organizer_event': get_organizer_event(*vehicle_identifiers(vehicle, plate)),
                'bbox':            bbox,
            }
            # Attached only when the ML pipeline actually recorded a sample,
            # so the guard page can offer "was this read correctly?" against a
            # row that exists to be corrected.
            if ml_sample:
                resp['sample_id'] = ml_sample['sample_id']
                resp['ml_confidence'] = ml_sample['confidence']
            results.append(resp)

        # Always 200, always a list. A frame with one readable and one
        # unreadable plate is a partial success, not an error, and the caller
        # reads each result's own status rather than the HTTP code.
        return Response({'results': results})



class VisitorPassView(APIView):
    """Guard issues a visitor pass at the gate."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        """
        Create a visitor pass and return its data for thermal printing.
        Accepts plate_number directly; finds or creates the Vehicle record.
        """
        plate_number = (request.data.get('plate_number') or '').strip().upper().replace(' ', '')
        if not plate_number:
            return Response({'error': 'plate_number is required.'}, status=400)

        vehicle, vehicle_created = Vehicle.objects.get_or_create(
            plate_number=plate_number,
            defaults={'vehicle_type': 'car', 'is_authorized': False},
        )
        # A visitor pass for an unregistered plate auto-creates a Vehicle record —
        # surface that creation in the audit trail like any other vehicle add.
        if vehicle_created:
            _audit(
                request,
                AuditLog.Action.RECORD_CREATED,
                f"Vehicle added | {plate_number} (visitor) | By: {request.user.full_name}",
            )

        office_id = request.data.get('office')
        office = None
        if office_id:
            from .models import Office as OfficeModel
            office = OfficeModel.objects.filter(pk=office_id).first()

        try:
            # Hours and minutes on the form, sent as a total; at most a day.
            allowed_duration = min(24 * 60, max(1, int(request.data.get('allowed_duration', 60))))
        except (TypeError, ValueError):
            allowed_duration = 60

        # Upper-cased like every other name the system stores.
        visitor_name = ' '.join((request.data.get('visitor_name') or '').split()).upper()[:150]

        now = timezone.now()
        pass_ = VisitorPass.objects.create(
            vehicle=vehicle,
            plate_number=plate_number,
            visitor_name=visitor_name,
            office=office,
            purpose=request.data.get('purpose', ''),
            issued_by=request.user,
            valid_date=timezone.localdate(),
            allowed_duration=allowed_duration,
            expires_at=now + timedelta(minutes=allowed_duration),
        )

        # NOTE: the visitor's entry is NOT logged here. The AccessLog entry is
        # only created once the slip is confirmed printed (VisitorPassPrintedView)
        # — a visitor without a printed slip is not considered inside.
        gate_id = (request.data.get('gate_id')
                   or getattr(request.user, 'gate_assignment', None)
                   or 'main')

        guard_name  = request.user.full_name
        office_name = office.name if office else 'N/A'
        _audit(
            request,
            AuditLog.Action.VISITOR_ISSUED,
            f"Visitor pass issued | Plate: {plate_number} | Visitor: {visitor_name or 'N/A'} | "
            f"Purpose: {pass_.purpose or 'N/A'} | Office: {office_name} | "
            f"Duration: {allowed_duration} min | Gate: {_gate_label(gate_id)} | Guard: {guard_name}",
        )

        from .slips import visitor_slip
        data = VisitorPassSerializer(pass_).data
        data['slip'] = visitor_slip(pass_)   # printed by the guard page straight away
        return Response(data, status=201)

    def get(self, request):
        """List today's visitor passes."""
        passes = VisitorPass.objects.filter(
            valid_date=timezone.localdate()
        ).select_related('vehicle', 'office', 'issued_by')
        return Response(VisitorPassSerializer(passes, many=True).data)


def _slip_from_request(request, any_copy=False):
    """(model row, None) for the slip named by `code` in the query or body, or
    (None, error Response).

    A visitor slip is refused once the visitor is no longer inside
    (_spent_visitor_slip), and — unless `any_copy` — when its serial is not
    the newest print's: every print draws a new serial, so an older paper copy
    opens and exits nothing. Printing passes `any_copy`, since a print makes a
    new copy that retires all the others anyway."""
    from . import slips
    code = request.query_params.get('code') or request.data.get('code')
    parsed = slips.parse_code(code)
    if not parsed:
        return None, Response({'error': 'Not a slip QR. Scan a visitor, supplier, event or no-plate slip.'}, status=400)
    # `extra` is the code's third part: a visitor slip's serial, an event pass's
    # organizer plate, '' for the rest.
    kind, pk, extra = parsed
    obj = slips.find(kind, pk, extra)
    if not obj:
        return None, Response({'error': 'No slip matches that QR — it may have been deleted.'}, status=404)
    if isinstance(obj, VisitorPass):
        error = _spent_visitor_slip(obj) or (None if any_copy else _replaced_visitor_slip(obj, extra))
        if error:
            return None, error
    return obj, None


def _spent_visitor_slip(obj):
    """409 Response when `obj` is a visitor pass that is no longer active, else
    None. A visitor slip is good for one visit: once the visitor has exited
    (or the pass expired) the paper cannot be looked up, reprinted or exited
    again — a returning visitor is issued a new pass."""
    from .slips import slip_data, _when
    if not isinstance(obj, VisitorPass) or obj.status == VisitorPass.Status.ACTIVE:
        return None
    if obj.status == VisitorPass.Status.EXITED:
        error = (f'This visitor slip is no longer valid — {obj.plate_number} already exited on '
                 f'{_when(obj.exited_at)}. A returning visitor needs a new visitor pass.')
    else:
        error = (f'This visitor slip is no longer valid — the pass for {obj.plate_number} is '
                 f'{obj.get_status_display().lower()}. A returning visitor needs a new visitor pass.')
    return Response({'error': error, 'reason': 'slip_used', 'slip': slip_data(obj)}, status=409)


def _replaced_visitor_slip(pass_, serial):
    """409 Response when `serial` is not the newest printed copy of this pass.
    The slip itself is left out of the response: it would hand whoever holds
    the old paper the current code."""
    if serial == pass_.slip_token:
        return None
    return Response({
        'error': (f'This visitor slip is no longer valid — a newer copy was printed for '
                  f'{pass_.plate_number} (VP-{pass_.pk}). Only the latest printed slip can be used. '
                  f'Look the visitor up by plate or name if they no longer have it.'),
        'reason': 'slip_replaced',
    }, status=409)


class SlipView(APIView):
    """GET /scan/slip/?code=SLC-VISITOR:12-A1B2C3D4 — what a slip says and
    whether its vehicle is still inside. Looking a slip up changes nothing:
    the guard chooses to record the exit or reprint from what comes back. A
    visitor slip whose visitor has already left, or that a newer print
    replaced, is refused (see _slip_from_request)."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from .slips import slip_data
        obj, error = _slip_from_request(request)
        return error or Response(slip_data(obj))


class SlipPrintView(APIView):
    """POST /scan/slip/print/ {code, reprint, target} — print a slip straight
    to this server's thermal printer, no browser dialog. 503 means this server
    has no printer (the cloud site) and the frontend falls back to the browser
    print dialog; 502 means the printer is there but nothing came out.
    `target: 'browser'` skips the thermal printer: the guard chose the browser
    dialog, and only needs the slip to print.

    A visitor slip draws a new serial for every print, so each paper copy has
    its own QR and only the newest one works. The serial is kept once the slip
    goes out (thermal, or handed to the browser) — a failed thermal print keeps
    the previous one, so the copy the visitor holds still works.

    Printing logs nothing about the entry — a reprint is audited so torn-slip
    replacements are traceable."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        from .slip_printer import SlipPrinterError, print_slip
        from .slips import new_slip_token, slip_data

        obj, error = _slip_from_request(request, any_copy=True)
        if error:
            return error
        is_visitor = isinstance(obj, VisitorPass)
        if is_visitor:
            obj.slip_token = new_slip_token()
        slip = slip_data(obj)

        def keep_serial():
            if is_visitor:
                VisitorPass.objects.filter(pk=obj.pk).update(slip_token=obj.slip_token)

        if request.data.get('target') == 'browser':
            keep_serial()
            return Response({'printed': False, 'reason': 'browser', 'slip': slip})
        reprint = str(request.data.get('reprint', '')).lower() in ('1', 'true', 'yes')
        try:
            printer = print_slip(slip, reprint=reprint)
        except SlipPrinterError as exc:
            return Response({'printed': False, 'error': f'The slip did not print: {exc}.'}, status=502)
        keep_serial()
        if not printer:
            return Response({'printed': False, 'reason': 'no_printer', 'slip': slip}, status=503)
        if reprint:
            _audit_slip_reprint(request, slip)
        return Response({'printed': True, 'printer': printer, 'slip': slip})


class SlipReprintedView(APIView):
    """POST /scan/slip/reprinted/ {code} — the browser print dialog was used
    for a reprint (no server printer), so audit it the same way."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        from .slips import slip_data
        obj, error = _slip_from_request(request, any_copy=True)
        if error:
            return error
        _audit_slip_reprint(request, slip_data(obj))
        return Response({'ok': True})


def _audit_slip_reprint(request, slip):
    _audit(
        request,
        AuditLog.Action.RECORD_UPDATED,
        f"Slip reprinted | Ref: {slip['reference']} | "
        + (f"Slip No.: {slip['serial']} | " if slip.get('serial') else "")
        + (f"Plate: {slip['plate_number']} | " if slip['plate_number'] else "")
        + f"Name: {slip['name'] or 'N/A'} | Guard: {request.user.full_name}",
    )


class SlipExitView(APIView):
    """POST /scan/slip/exit/ {code, gate_id} — record the exit of the vehicle a
    slip belongs to. Deliberately separate from looking the slip up."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        from .slips import slip_data

        obj, error = _slip_from_request(request)
        if error:
            return error
        gate_id = (request.data.get('gate_id')
                   or getattr(request.user, 'gate_assignment', None)
                   or 'main')
        from vehicles.models import SupplierPlate
        if isinstance(obj, SupplierPlate):
            return Response({'error': 'A supplier pass is not one visit — scan its QR at the gate '
                                      'and the plate check records the exit.',
                             'slip': slip_data(obj)}, status=400)
        if isinstance(obj, VisitorPass):   # spent / replaced copies never get here
            duration, overstay = _record_visitor_exit(request, obj, gate_id)
        else:
            if AccessLog.objects.filter(paired_entry=obj).exists():
                return Response({'error': 'This vehicle has already been logged out.',
                                 'slip': slip_data(obj)}, status=409)
            from .slips import is_event_entry
            if is_event_entry(obj):
                duration, overstay = _record_event_exit(request, obj, gate_id)
            elif obj.is_unrecognized:
                duration, overstay = _record_noplate_exit(request, obj, gate_id)[0], 0
            else:
                duration, overstay = _record_supplier_exit(request, obj, gate_id)
        obj.refresh_from_db()
        return Response({'slip': slip_data(obj), 'duration_minutes': duration, 'overstay_minutes': overstay})


class VisitorPassPrintedView(APIView):
    """Confirm the visitor slip was printed. Only at this point is the visitor's
    entry recorded in the AccessLog — a visitor whose slip was never printed is
    not considered inside. Idempotent: re-confirming does nothing."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        pass_ = get_object_or_404(VisitorPass, pk=pk)
        if pass_.printed_at:
            return Response(VisitorPassSerializer(pass_).data)

        pass_.printed_at = timezone.now()
        pass_.save(update_fields=['printed_at'])

        gate_id = (request.data.get('gate_id')
                   or getattr(request.user, 'gate_assignment', None)
                   or 'main')
        AccessLog.objects.create(
            plate_number=pass_.plate_number,
            vehicle=pass_.vehicle,
            status=AccessLog.Status.AUTHORIZED,
            gate_id=gate_id,
            scanned_by=request.user,
        )
        _audit(
            request,
            AuditLog.Action.VISITOR_ISSUED,
            f"Visitor slip printed — entry logged | Plate: {pass_.plate_number} | "
            f"Gate: {_gate_label(gate_id)} | Guard: {request.user.full_name}",
        )
        return Response(VisitorPassSerializer(pass_).data)


def _record_visitor_exit(request, pass_, gate_id):
    """Shared exit logic for slip exits (QR scan or looked-up slip). Marks the pass exited, logs the
    exit AccessLog, and issues an overstay violation when applicable."""
    now = timezone.now()
    pass_.status    = VisitorPass.Status.EXITED
    pass_.exited_at = now
    pass_.save(update_fields=['status', 'exited_at'])

    AccessLog.objects.create(
        vehicle=pass_.vehicle,
        plate_number=pass_.plate_number,
        status=AccessLog.Status.EXITED,
        gate_id=gate_id,
        scanned_by=request.user,
    )

    duration_minutes = int((now - pass_.entered_at).total_seconds() / 60)
    overstay_minutes = (int((now - pass_.expires_at).total_seconds() / 60)
                        if pass_.expires_at and now > pass_.expires_at else 0)
    if overstay_minutes:
        try:
            _auto_log_violation(
                pass_.vehicle,
                f'Visitor overstay: exceeded allowed {pass_.allowed_duration} min by {overstay_minutes} min',
                gate_id,
                vtype=Violation.Type.TIME_EXCEED,
                evidence_bytes=_request_image_bytes(request),
            )
        except Exception:
            pass
    _audit(
        request,
        AuditLog.Action.VISITOR_EXITED,
        f"Visitor exited (slip) | Plate: {pass_.plate_number} | "
        f"Visitor: {pass_.visitor_name or 'N/A'} | "
        f"Duration: {duration_minutes} min | "
        + (f"OVERSTAYED by {overstay_minutes} min | " if overstay_minutes else "")
        + f"Gate: {_gate_label(gate_id)} | Guard: {request.user.full_name}",
    )
    return duration_minutes, overstay_minutes


def _record_supplier_exit(request, entry, gate_id):
    """Exit of a supplier vehicle recorded from its slip — the same exit log
    and stay-limit check the plate scan does. Returns (minutes inside,
    overstay minutes)."""
    from .slips import supplier_plate_for
    exit_log = AccessLog.objects.create(
        plate_number=entry.plate_number, status=AccessLog.Status.EXITED,
        gate_id=gate_id, scanned_by=request.user, paired_entry=entry,
    )
    duration = int((exit_log.scanned_at - entry.scanned_at).total_seconds() / 60)
    overstay = _check_stay_limit(entry.plate_number, None, 'supplier', duration, gate_id) or 0
    roster = supplier_plate_for(entry)
    _audit(
        request, AuditLog.Action.RECORD_UPDATED,
        f"Supplier vehicle exited (slip) | Ref: SP-{entry.pk} | Plate: {entry.plate_number} | "
        f"Company: {roster.supplier.company_name if roster else 'N/A'} | Duration: {duration} min | "
        + (f"OVERSTAYED by {overstay} min | " if overstay else "")
        + f"Gate: {_gate_label(gate_id)} | Guard: {request.user.full_name}",
    )
    return duration, overstay


def _record_event_exit(request, entry, gate_id):
    """Exit of an event organizer's vehicle recorded from its event slip.
    Returns (minutes inside, minutes past the event's end). Staying past the
    end is reported, not fined: there is no event stay rule to break, and the
    organizer is often the last one to leave."""
    from .slips import event_end
    exit_log = AccessLog.objects.create(
        plate_number=entry.plate_number, status=AccessLog.Status.EXITED,
        gate_id=gate_id, scanned_by=request.user, paired_entry=entry,
        event=entry.event, entrant_category=AccessLog.Category.EVENT,
    )
    duration = int((exit_log.scanned_at - entry.scanned_at).total_seconds() / 60)
    ends = event_end(entry.event)
    overstay = (int((exit_log.scanned_at - ends).total_seconds() // 60)
                if ends and exit_log.scanned_at > ends else 0)
    _audit(
        request, AuditLog.Action.RECORD_UPDATED,
        f"Event organizer exited (slip) | Ref: EV-{entry.pk} | Plate: {entry.plate_number} | "
        f"Event: {entry.event.name if entry.event else 'N/A'} | Duration: {duration} min | "
        + (f"Past event end by {overstay} min | " if overstay else "")
        + f"Gate: {_gate_label(gate_id)} | Guard: {request.user.full_name}",
    )
    return duration, overstay


def _audit_typed_visitor_exit(request, pass_, gate_id, duration_minutes, overstay_minutes):
    """Audit line for a visitor let out through Record Exit by plate rather
    than from their slip — worded apart so the trail shows which."""
    _audit(
        request,
        AuditLog.Action.VISITOR_EXITED,
        f"Visitor exited (record exit by plate) | Plate: {pass_.plate_number} | "
        f"Visitor: {pass_.visitor_name or 'N/A'} | "
        + (f"Duration: {duration_minutes} min | " if duration_minutes is not None else "")
        + (f"OVERSTAYED by {overstay_minutes} min | " if overstay_minutes else "")
        + f"Gate: {_gate_label(gate_id)} | Guard: {request.user.full_name}",
    )


class ExitScanView(APIView):
    """
    Guard scans the QR code on the returned thermal pass to record exit.
    The QR encodes the visitor pass ID.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        pass_ = get_object_or_404(VisitorPass, pk=pk)

        if pass_.status != VisitorPass.Status.ACTIVE:
            return Response(
                {'error': f'Pass is already marked as {pass_.status}.'},
                status=400,
            )

        gate_id = request.data.get('gate_id', 'main')
        _record_visitor_exit(request, pass_, gate_id)
        return Response(VisitorPassSerializer(pass_).data)


class VisitorQrExitView(APIView):
    """Record a visitor exit by scanning the QR on the printed slip.
    QR payload: SLC-VISITOR:{pass_id}. Visitor exits are recorded only through
    this scan — plate-based exit is refused for vehicles on an active pass."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        from .slips import parse_code
        qr_data = (request.data.get('qr_data') or '').strip()
        if not qr_data.upper().startswith('SLC-VISITOR:'):
            return Response({'error': 'Not a visitor slip QR.'}, status=400)
        parsed = parse_code(qr_data)
        if not parsed:
            return Response({'error': 'Malformed visitor slip QR.'}, status=400)
        _, pk, serial = parsed

        pass_ = VisitorPass.objects.filter(pk=pk).select_related('vehicle', 'office').first()
        if not pass_:
            return Response({'error': 'Visitor pass not found.'}, status=404)
        if pass_.status != VisitorPass.Status.ACTIVE:
            return Response({'error': f'Pass is already marked as {pass_.status}.'}, status=400)
        replaced = _replaced_visitor_slip(pass_, serial)
        if replaced:
            return replaced

        gate_id = (request.data.get('gate_id')
                   or getattr(request.user, 'gate_assignment', None)
                   or 'main')
        duration_minutes, overstay_minutes = _record_visitor_exit(request, pass_, gate_id)
        data = VisitorPassSerializer(pass_).data
        data['duration_minutes'] = duration_minutes
        data['overstay_minutes'] = overstay_minutes
        return Response(data)


class OfficeListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        offices = Office.objects.all()
        return Response(OfficeSerializer(offices, many=True).data)


def _gate_dict(g):
    return {'id': g.id, 'gate_id': g.gate_id, 'label': g.label, 'is_active': g.is_active}


class GateListView(APIView):
    """List gates. Public (the guard gate-login kiosk needs it pre-auth) —
    returns active gates only unless an admin/CDSO asks for ?all=1.
    POST (admin/CDSO): create a new gate for school expansion."""
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        from .models import Gate
        qs = Gate.objects.all()
        is_staff = request.user.is_authenticated and getattr(request.user, 'role', '') == 'admin'
        if not (is_staff and request.query_params.get('all')):
            qs = qs.filter(is_active=True)
        return Response([_gate_dict(g) for g in qs])

    def post(self, request):
        from .models import Gate
        import re as _re
        if not (request.user.is_authenticated and getattr(request.user, 'role', '') == 'admin'):
            return Response({'error': 'Not authorised.'}, status=status.HTTP_403_FORBIDDEN)

        gate_id = (request.data.get('gate_id') or '').strip().lower()
        label   = (request.data.get('label') or '').strip()
        if not _re.fullmatch(r'gate\d{1,3}', gate_id):
            return Response({'error': "Gate ID must look like 'gate2', 'gate5', etc."}, status=status.HTTP_400_BAD_REQUEST)
        if not label:
            return Response({'error': 'A display label is required (e.g. "Gate 2 — North Entrance").'}, status=status.HTTP_400_BAD_REQUEST)
        if Gate.objects.filter(gate_id=gate_id).exists():
            return Response({'error': f'{gate_id} already exists.'}, status=status.HTTP_400_BAD_REQUEST)

        gate = Gate.objects.create(gate_id=gate_id, label=label)
        AuditLog.objects.create(
            actor=request.user, action=AuditLog.Action.RECORD_UPDATED,
            details=f'Gate created: {gate.label} ({gate.gate_id})',
        )
        return Response(_gate_dict(gate), status=status.HTTP_201_CREATED)


class GateDetailView(APIView):
    """Admin/CDSO: rename a gate or toggle it active/inactive."""
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, pk):
        from .models import Gate
        if getattr(request.user, 'role', '') != 'admin':
            return Response({'error': 'Not authorised.'}, status=status.HTTP_403_FORBIDDEN)
        try:
            gate = Gate.objects.get(pk=pk)
        except Gate.DoesNotExist:
            return Response({'error': 'Gate not found.'}, status=status.HTTP_404_NOT_FOUND)

        label = (request.data.get('label') or '').strip()
        if label:
            gate.label = label
        if 'is_active' in request.data:
            gate.is_active = bool(request.data.get('is_active'))
        gate.save(update_fields=['label', 'is_active'])
        AuditLog.objects.create(
            actor=request.user, action=AuditLog.Action.RECORD_UPDATED,
            details=f'Gate updated: {gate.label} ({gate.gate_id}) — active={gate.is_active}',
        )
        return Response(_gate_dict(gate))


# -- Vehicle Log --------------------------------------------------------------
# The list screen (guard: one gate, one day — CDSO: all gates, a date range) and
# its two reports read through the same helpers below, so an exported report can
# never disagree with the table it was exported from.

# One UI choice covers several stored statuses, because that is how the question
# gets asked: "show me everything that was refused" is denied *and* wrong day.
ACCESS_LOG_STATUS_GROUPS = {
    'authorized': [AccessLog.Status.AUTHORIZED],
    'denied':     [AccessLog.Status.DENIED, AccessLog.Status.WRONG_DAY],
    'unknown':    [AccessLog.Status.UNKNOWN],
    'unreadable': [AccessLog.Status.UNREADABLE],
    'exited':     [AccessLog.Status.EXITED],
}


def _filter_access_logs(request):
    """Apply the filters the Vehicle Log screens use.

    Returns (ordered_queryset, filters_desc). `status` is deliberately NOT
    applied here — see _merge_access_log_visits: an exit row only folds into its
    entry while both are in the result set, so narrowing by status in SQL would
    strip the exit half of every visit. It is applied to the merged rows instead.
    """
    qs = (
        AccessLog.objects
        .select_related('scanned_by', 'on_duty_guard', 'vehicle__user')
        .order_by('-scanned_at')
    )
    filters_desc = []

    gate_id = (request.query_params.get('gate_id') or '').strip()
    if gate_id:
        qs = qs.filter(gate_id=gate_id)
        from .models import Gate
        label = Gate.objects.filter(gate_id=gate_id).values_list('label', flat=True).first()
        filters_desc.append(f'Gate: {label or gate_id}')

    date = (request.query_params.get('date') or '').strip()
    if date:
        try:
            _start, _end = day_range(datetime.strptime(date, '%Y-%m-%d').date())
            qs = qs.filter(scanned_at__gte=_start, scanned_at__lt=_end)
            filters_desc.append(f'Date: {date}')
        except Exception:
            pass  # ignore malformed dates rather than 500

    # Range form of the single-day filter above, for the CDSO's Vehicle Log.
    # Unparseable bounds are ignored, same as `date`.
    date_from = (request.query_params.get('date_from') or '').strip()
    date_to   = (request.query_params.get('date_to') or '').strip()
    qs = filter_local_date_range(qs, 'scanned_at', date_from, date_to)
    if date_from or date_to:
        filters_desc.append(f"Period: {date_from or 'start'} to {date_to or 'today'}")

    search = (request.query_params.get('search') or '').strip()
    if search:
        qs = qs.filter(
            Q(plate_number__icontains=search)
            | Q(vehicle__user__full_name__icontains=search)
            # A hand-recorded plateless vehicle has neither a plate nor an owner
            # account, so without these it could not be found by any search term
            # at all — only by scrolling to the right minute of the day.
            | Q(driver_name__icontains=search)
            | Q(vehicle_color__icontains=search)
            | Q(vehicle_model__icontains=search)
            | Q(on_duty_guard__full_name__icontains=search)
            | Q(scanned_by__full_name__icontains=search)
        )
        filters_desc.append(f"Search: '{search}'")

    # Who came through, as opposed to what was decided about them. Applied in
    # SQL rather than after the merge (as `status` has to be): a category is a
    # property of the entry row, and an exit row carries the same one, so
    # narrowing here cannot strip the exit half of a visit.
    category = (request.query_params.get('category') or '').strip()
    if category in AccessLog.Category.values:
        qs = qs.filter(entrant_category=category)
        filters_desc.append(
            'Category: ' + dict(AccessLog.Category.choices)[category])

    status_key = (request.query_params.get('status') or '').strip()
    if status_key in ACCESS_LOG_STATUS_GROUPS:
        labels = dict(AccessLog.Status.choices)
        filters_desc.append(
            'Status: ' + ', '.join(labels.get(v, v) for v in ACCESS_LOG_STATUS_GROUPS[status_key])
        )

    return qs, filters_desc


def _merge_access_log_visits(logs):
    """Fold each exit row into its paired entry row — one visit, one row.

    Returns (visible_logs, exit_by_entry_id). Pairing only happens when the
    entry is in `logs` too, so an exit whose entry fell outside the filter or
    the row cap still shows on its own rather than vanishing.
    """
    entries_by_id = {log.id: log for log in logs if log.status == AccessLog.Status.AUTHORIZED}
    exit_by_entry_id = {}
    for log in logs:
        if log.status == AccessLog.Status.EXITED and log.paired_entry_id in entries_by_id:
            exit_by_entry_id[log.paired_entry_id] = log

    merged_exit_ids = {exit_log.id for exit_log in exit_by_entry_id.values()}
    visible = [log for log in logs if log.id not in merged_exit_ids]
    return visible, exit_by_entry_id


def _visit_duration_minutes(entry_log, exit_log):
    return max(0, round((exit_log.scanned_at - entry_log.scanned_at).total_seconds() / 60))


def _apply_status_group(logs, status_key):
    """Narrow already-merged rows to one UI status group; unknown keys pass through."""
    wanted = ACCESS_LOG_STATUS_GROUPS.get(status_key)
    return [log for log in logs if log.status in wanted] if wanted else logs


class AccessLogListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qs, _ = _filter_access_logs(request)

        try:
            limit = int(request.query_params.get('limit', 200))
        except (TypeError, ValueError):
            limit = 200
        limit = max(1, min(limit, 1000))
        logs = list(qs[:limit])

        visible, exit_by_entry_id = _merge_access_log_visits(logs)
        entries_by_id = {log.id: log for log in logs}

        data = AccessLogSerializer(visible, many=True).data
        for row in data:
            exit_log = exit_by_entry_id.get(row['id'])
            if exit_log:
                row['exited_at'] = exit_log.scanned_at
                row['duration_minutes'] = _visit_duration_minutes(entries_by_id[row['id']], exit_log)
        return Response(data)


VEHICLE_LOG_REPORT_HEADERS = [
    # 'Category' is who came through, which 'Status' (what was decided about
    # them) cannot answer: a report asked for "how many students entered in
    # September" could not be produced from the old columns at all.
    '#', 'Date & Time', 'Plate', 'Owner', 'Category', 'Type', 'Gate',
    'Status', 'Guard on Duty', 'Exit Time', 'Duration', 'Remarks',
]

# Rows are capped rather than streamed, the same way the audit report is: a year
# of scans is far more than anyone reads out of a PDF, and an unbounded export
# is a memory hazard on a shared dyno.
VEHICLE_LOG_REPORT_CAP = 5000


def _vehicle_log_report_rows(logs, exit_by_entry_id):
    from django.utils import timezone as tz
    from .models import Gate

    status_labels = dict(AccessLog.Status.choices)
    category_labels = dict(AccessLog.Category.choices)
    gate_labels = dict(Gate.objects.values_list('gate_id', 'label'))

    def duration_text(minutes):
        if minutes is None:
            return ''
        if minutes < 60:
            return f'{minutes} min'
        hours, mins = divmod(minutes, 60)
        return f'{hours}h {mins}m' if mins else f'{hours}h'

    rows = []
    for i, log in enumerate(logs, start=1):
        exit_log = exit_by_entry_id.get(log.id)
        minutes = _visit_duration_minutes(log, exit_log) if exit_log else None
        # A hand-recorded plateless vehicle has no owner account — the driver's
        # name the guard wrote down is the only name it has, and printing
        # 'Unregistered' over the top of it loses the one identifier there is.
        owner = (getattr(getattr(log.vehicle, 'user', None), 'full_name', '')
                 or log.driver_name or 'Unregistered')

        remarks = []
        if log.is_unrecognized:
            described = ' '.join(
                x for x in (log.vehicle_color, log.vehicle_model) if x)
            remarks.append(f'No plate — {described}' if described else 'No plate')
            if log.entry_note:
                remarks.append(log.entry_note)
        if log.is_override:
            remarks.append(f'Override: {log.override_reason}' if log.override_reason else 'Override')
        if log.denied_reason:
            remarks.append(log.denied_reason)
        if not exit_log and log.status == AccessLog.Status.AUTHORIZED:
            remarks.append('Still inside')

        rows.append([
            i,
            tz.localtime(log.scanned_at).strftime('%b %d, %Y %I:%M:%S %p'),
            # Plateless vehicles are listed under the reference the guard was
            # given for them, which is what the log and the screens show too.
            log.plate_number or (f'NP-{log.id}' if log.is_unrecognized else ''),
            owner,
            category_labels.get(log.entrant_category, ''),
            (log.vehicle_type or '').title(),
            gate_labels.get(log.gate_id, log.gate_id or ''),
            status_labels.get(log.status, log.status),
            getattr(log.on_duty_guard, 'full_name', '') or '',
            tz.localtime(exit_log.scanned_at).strftime('%I:%M %p') if exit_log else '',
            duration_text(minutes),
            ' · '.join(remarks),
        ])
    return rows


def _vehicle_log_report_data(request):
    """(rows, filters_desc) for both formats — one filter path, one merge."""
    qs, filters_desc = _filter_access_logs(request)
    logs = list(qs[:VEHICLE_LOG_REPORT_CAP])
    visible, exit_by_entry_id = _merge_access_log_visits(logs)
    visible = _apply_status_group(visible, (request.query_params.get('status') or '').strip())
    return _vehicle_log_report_rows(visible, exit_by_entry_id), filters_desc


class VehicleLogExportView(APIView):
    """Download the (filtered) vehicle log as an Excel report — CDSO only."""
    permission_classes = [IsAdminRole]

    def get(self, request):
        from django.utils import timezone as tz
        from report_utils import branded_excel_response, report_filename
        rows, filters_desc = _vehicle_log_report_data(request)
        subtitle = (f"Generated {tz.localtime().strftime('%B %d, %Y %I:%M %p')} "
                    f"by {getattr(request.user, 'full_name', '')} · "
                    + ('; '.join(filters_desc) if filters_desc else 'All records')
                    + f" · {len(rows)} entries")
        return branded_excel_response(
            filename=report_filename('Vehicle Log Report', 'xlsx'),
            sheet_title='Vehicle Log',
            report_title='Vehicle Log Report',
            subtitle=subtitle,
            headers=VEHICLE_LOG_REPORT_HEADERS,
            rows=rows,
            col_widths=[5, 22, 14, 26, 16, 12, 22, 14, 22, 12, 10, 40],
        )


class VehicleLogPdfExportView(APIView):
    """Download the (filtered) vehicle log as a branded PDF report — CDSO only."""
    permission_classes = [IsAdminRole]

    def get(self, request):
        from report_utils import branded_pdf_response, report_filename
        rows, filters_desc = _vehicle_log_report_data(request)
        subtitle = (('; '.join(filters_desc) if filters_desc else 'All records')
                    + f" · {len(rows)} entries")
        return branded_pdf_response(
            filename=report_filename('Vehicle Log Report', 'pdf'),
            report_title='Vehicle Log Report',
            subtitle=subtitle,
            generated_by=getattr(request.user, 'full_name', ''),
            headers=VEHICLE_LOG_REPORT_HEADERS,
            rows=rows,
            # 267mm of printable width on landscape A4, and it must still total
            # 267 now that Category has been added. Date & Time gets enough to
            # stay on one line (the audit report learned that the hard way);
            # Remarks gives up most of the room, being the only free-text column.
            col_widths_mm=[8, 31, 21, 32, 18, 15, 24, 21, 28, 16, 14, 39],
        )


class MLTrainingSampleList(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        samples = MLTrainingSample.objects.all().order_by('-created_at')[:100]
        return Response(MLTrainingSampleSerializer(samples, many=True).data)


class MLTrainingSampleReview(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, pk):
        try:
            sample = MLTrainingSample.objects.get(pk=pk)
        except MLTrainingSample.DoesNotExist:
            return Response({'error': 'Sample not found'}, status=404)

        plate_number = request.data.get('plate_number')
        if plate_number is not None:
            sample.plate_number = plate_number
        action = request.data.get('action', sample.status)
        valid = dict(MLTrainingSample.STATUS_CHOICES).keys()
        if action not in valid and action not in ('approve', 'reject', 'mark_used'):
            return Response({'error': f'Invalid action. Must be one of {valid}'}, status=400)
        if action == 'approve':
            sample.status = MLTrainingSample.STATUS_CHOICES[2][0]  # 'verified'
        elif action == 'reject' or action == 'mark_used':
            sample.status = MLTrainingSample.STATUS_CHOICES[3][0]  # 'rejected'
        elif action == 'mark_used':
            sample.used_in_training = True
        else:
            sample.status = action
        sample.save()
        return Response(MLTrainingSampleSerializer(sample).data)


class TriggerRetrainView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        from scanning.tasks import ml_retrain_task
        task = ml_retrain_task.delay()
        return Response({
            'status': 'enqueued',
            'task_id': task.id,
            'message': 'Retrain task has been queued.',
        })


class MLStatsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        total      = MLTrainingSample.objects.count()
        unlabeled  = MLTrainingSample.objects.filter(status='unlabeled').count()
        auto_labeled = MLTrainingSample.objects.filter(status='auto_labeled').count()
        verified   = MLTrainingSample.objects.filter(status='verified').count()
        rejected   = MLTrainingSample.objects.filter(status='rejected').count()
        pending_train = MLTrainingSample.objects.filter(used_in_training=False).count()
        return Response({
            'total_samples': total,
            'unlabeled':     unlabeled,
            'auto_labeled':  auto_labeled,
            'verified':      verified,
            'rejected':      rejected,
            'pending_train': pending_train,
        })


class OverrideEntryView(APIView):
    """Guard overrides a denial and grants entry with a logged reason."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        plate_number = (request.data.get('plate_number') or '').strip().upper().replace(' ', '')
        reason       = (request.data.get('reason') or '').strip()

        if not plate_number:
            return Response({'error': 'plate_number is required.'}, status=400)
        if not reason:
            return Response({'error': 'reason is required.'}, status=400)

        vehicle = Vehicle.resolve(plate_number)  # plate or conduction number

        gate_id = getattr(request.user, 'gate_assignment', None) or 'main'
        AccessLog.objects.create(
            plate_number    = plate_number,
            vehicle         = vehicle,
            status          = AccessLog.Status.AUTHORIZED,
            is_override     = True,
            override_reason = reason,
            gate_id         = gate_id,
            scanned_by      = request.user,
        )

        guard_name = request.user.full_name
        owner_name = vehicle.user.full_name if vehicle and vehicle.user else 'Unregistered'
        _audit(
            request,
            AuditLog.Action.ENTRY_OVERRIDE,
            f"Entry override | Plate: {plate_number} | Owner: {owner_name} | "
            f"Reason: {reason} | Gate: {_gate_label(gate_id)} | Guard: {guard_name}",
        )

        return Response({'status': 'overridden', 'plate_number': plate_number})


class DenyEntryView(APIView):
    """Guard explicitly denies a visitor/unregistered plate at the gate.
    Logs a DENIED access row with the reason and an audit entry — no violation
    is issued (turning away a visitor is not an offense)."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        plate_number = (request.data.get('plate_number') or '').strip().upper().replace(' ', '')
        if not plate_number:
            return Response({'error': 'plate_number is required.'}, status=400)

        reason = (request.data.get('reason') or '').strip() or 'Entry denied at gate by guard.'

        vehicle = Vehicle.resolve(plate_number)  # plate or conduction number
        gate_id = getattr(request.user, 'gate_assignment', None) or 'main'
        AccessLog.objects.create(
            plate_number  = plate_number,
            vehicle       = vehicle,
            status        = AccessLog.Status.DENIED,
            denied_reason = reason,
            gate_id       = gate_id,
            scanned_by    = request.user,
        )

        return Response({
            'plate_number': plate_number,
            'status':       'denied',
            'allowed':      False,
            'message':      f'Entry denied by guard. {reason}',
            'gate_id':      gate_id,
        })


class ExitLogView(APIView):
    """Guard records a vehicle exit and auto-pairs it to the matching entry."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        plate_number = (request.data.get('plate_number') or '').strip().upper().replace(' ', '')
        if not plate_number:
            return Response({'error': 'plate_number is required.'}, status=400)

        if not is_valid_ph_plate(plate_number):
            return Response({'error': 'Invalid plate format. Enter a valid Philippine plate number.'}, status=400)

        # An explicit "record exit" — so a visitor on an active pass exits here
        # too, and _close_active_pass below closes the pass. (A plain plate
        # CHECK never does; it shows the slip instead.)
        visitor_pass = _active_visitor_pass(plate_number)

        vehicle = Vehicle.resolve(plate_number)  # plate or conduction number

        gate_id = getattr(request.user, 'gate_assignment', None) or 'main'
        exit_log = AccessLog.objects.create(
            plate_number = plate_number,
            vehicle      = vehicle,
            status       = AccessLog.Status.EXITED,
            gate_id      = gate_id,
            scanned_by   = request.user,
        )

        _pair_entry_exit(exit_log)
        # The exit row was classified from the plate alone, which reads an
        # organizer's unregistered plate as "unknown". It is the other half of
        # an event visit, so it says so.
        entry_log = exit_log.paired_entry
        event_visit = bool(entry_log and entry_log.entrant_category == AccessLog.Category.EVENT)
        if event_visit:
            exit_log.entrant_category = AccessLog.Category.EVENT
            exit_log.event_id = entry_log.event_id
            exit_log.save(update_fields=['entrant_category', 'event'])
        overstay_minutes = _close_active_pass(plate_number, gate_id)

        duration_minutes = None
        entry_scanned_at = None
        if exit_log.paired_entry:
            delta            = exit_log.scanned_at - exit_log.paired_entry.scanned_at
            duration_minutes = int(delta.total_seconds() / 60)
            entry_scanned_at = exit_log.paired_entry.scanned_at

        if visitor_pass:
            _audit_typed_visitor_exit(request, visitor_pass, gate_id, duration_minutes, overstay_minutes)

        # Stay-limit enforcement (fetcher / supplier rules)
        if duration_minutes is not None:
            if vehicle and vehicle.user and vehicle.user.owner_type == 'fetcher' and not _is_standby_fetcher(vehicle.user):
                overstay_minutes = max(overstay_minutes, _check_stay_limit(
                    plate_number, vehicle, 'fetcher', duration_minutes, gate_id))
            # A supplier who came in for an event was not on a supplier visit,
            # so the supplier stay limit does not apply to that stay.
            elif (not event_visit and SupplierPlate.objects.filter(
                    plate_number=plate_number, supplier__is_active=True).exists()):
                overstay_minutes = max(overstay_minutes, _check_stay_limit(
                    plate_number, vehicle, 'supplier', duration_minutes, gate_id))

        return Response({
            'plate_number':    plate_number,
            'status':          'exited',
            'duration_minutes': duration_minutes,
            'overstay_minutes': overstay_minutes,
            'entry_scanned_at': entry_scanned_at,
            'scanned_at':      exit_log.scanned_at,
        })


class GuardMonitorView(APIView):
    """Admin-only: per-gate activity, current shifts, and cross-gate discrepancies."""

    def get(self, request):
        if not request.user.is_authenticated or request.user.role != 'admin':
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()

        from django.db.models import Count, Q
        from accounts.models import User as UserModel

        today  = timezone.localdate()
        _today_start, _today_end = day_range(today)
        guards = UserModel.objects.filter(role='security').order_by('full_name')

        # Current active shifts keyed by gate
        active_shifts = {}
        for shift in GuardShift.objects.filter(clocked_out_at__isnull=True).select_related('guard'):
            active_shifts[shift.gate] = {
                'guard_name':    shift.guard.full_name,
                'guard_code':    shift.guard.user_code,
                'clocked_in_at': shift.clocked_in_at,
                'shift_id':      shift.id,
            }

        result = []
        for guard in guards:
            today_logs = AccessLog.objects.filter(
                scanned_by=guard,
                scanned_at__gte=_today_start, scanned_at__lt=_today_end,
            )

            stats = today_logs.aggregate(
                total      = Count('id'),
                authorized = Count('id', filter=Q(status=AccessLog.Status.AUTHORIZED)),
                denied     = Count('id', filter=Q(status__in=[
                    AccessLog.Status.DENIED, AccessLog.Status.WRONG_DAY, AccessLog.Status.UNKNOWN,
                ])),
                exited     = Count('id', filter=Q(status=AccessLog.Status.EXITED)),
            )

            visitors = VisitorPass.objects.filter(issued_by=guard, valid_date=today).count()

            recent = today_logs.select_related('vehicle__user').order_by('-scanned_at')[:10]
            last_log  = recent.first()
            last_seen = last_log.scanned_at if last_log else None

            photo_url = request.build_absolute_uri(guard.photo.url) if guard.photo else None

            # Shift history for today
            shifts_today = GuardShift.objects.filter(
                guard=guard,
                clocked_in_at__gte=_today_start, clocked_in_at__lt=_today_end,
            ).values('id', 'gate', 'clocked_in_at', 'clocked_out_at')

            result.append({
                'id':              guard.id,
                'full_name':       guard.full_name,
                'user_code':       guard.user_code,
                'photo_url':       photo_url,
                'gate_assignment': guard.gate_assignment,
                'last_seen':       last_seen,
                'is_active':       last_seen is not None,
                'stats': {
                    'total':      stats['total']      or 0,
                    'authorized': stats['authorized'] or 0,
                    'denied':     stats['denied']     or 0,
                    'exited':     stats['exited']     or 0,
                    'visitors':   visitors,
                },
                'recent_logs': AccessLogSerializer(recent, many=True).data,
                'shifts_today': list(shifts_today),
            })

        result.sort(key=lambda g: (not g['is_active'], g['full_name']))

        # Cross-gate discrepancies: vehicle entered one gate, exited a different gate today
        cross_gate = []
        exit_logs = (
            AccessLog.objects
            .filter(status=AccessLog.Status.EXITED, paired_entry__isnull=False,
                    scanned_at__gte=_today_start, scanned_at__lt=_today_end)
            .select_related('paired_entry', 'vehicle__user')
        )
        for ex_log in exit_logs:
            entry = ex_log.paired_entry
            if entry and entry.gate_id != ex_log.gate_id and entry.gate_id and ex_log.gate_id:
                cross_gate.append({
                    'plate_number':  ex_log.plate_number,
                    'owner_name':    ex_log.vehicle.user.full_name if ex_log.vehicle and ex_log.vehicle.user else '—',
                    'entry_gate':    entry.gate_id,
                    'exit_gate':     ex_log.gate_id,
                    'entered_at':    entry.scanned_at,
                    'exited_at':     ex_log.scanned_at,
                })

        return Response({
            'guards':          result,
            'active_shifts':   active_shifts,
            'cross_gate_flags': cross_gate,
        })


class ExtendVisitorPassView(APIView):
    """Guard extends the allowed time for an active visitor pass."""
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, pk):
        pass_ = get_object_or_404(VisitorPass, pk=pk)

        if pass_.status != VisitorPass.Status.ACTIVE:
            return Response({'error': f'Cannot extend — pass is already {pass_.status}.'}, status=400)

        try:
            extra_minutes = int(request.data.get('extra_minutes', 0))
            if extra_minutes <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return Response({'error': 'extra_minutes must be a positive integer.'}, status=400)

        pass_.allowed_duration += extra_minutes
        if pass_.expires_at:
            pass_.expires_at += timedelta(minutes=extra_minutes)
        pass_.save(update_fields=['allowed_duration', 'expires_at'])

        guard_name = request.user.full_name
        _audit(
            request,
            AuditLog.Action.VISITOR_ISSUED,
            f"Visitor pass extended | Plate: {pass_.plate_number} | "
            f"+{extra_minutes} min | New total: {pass_.allowed_duration} min | Guard: {guard_name}",
        )

        return Response(VisitorPassSerializer(pass_).data)


class TestRtspView(APIView):
    """Quick probe: tries to open an RTSP URL and read one frame, returns ok/message."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        import concurrent.futures
        rtsp_url = (request.data.get('rtsp_url') or '').strip()
        if not rtsp_url.lower().startswith('rtsp://'):
            return Response({'ok': False, 'message': 'URL must start with rtsp://'}, status=400)

        def _probe():
            # Both backends, exactly as the live feed opens it — otherwise this
            # test can pass on a camera the feed cannot show, or fail on one it
            # can. open_capture() only reports open once a frame has actually
            # decoded, so reaching this point is proof of video.
            from vehicles.ffmpeg_capture import open_capture

            cap = open_capture(rtsp_url)
            try:
                if not cap.isOpened():
                    return False, ('Cannot connect — verify the URL, credentials, and '
                                   'that the camera is on the same network as this '
                                   'server. If another app is watching this camera, '
                                   'close it: many units serve only one stream at a time.')
                ret, _ = cap.read()
                if ret:
                    return True, 'Camera connected and streaming successfully.'
                return False, ('Reached the camera but received no frames — check '
                               'stream path or encoding settings.')
            finally:
                cap.release()

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(_probe)
            try:
                ok, msg = future.result(timeout=45)
            except concurrent.futures.TimeoutError:
                ok, msg = False, 'Connection timed out (45 s) — camera is unreachable from this server.'
            except Exception as e:
                ok, msg = False, f'Error: {e}'

        return Response({'ok': ok, 'message': msg})


# ─── Dual-Gate System Views ───────────────────────────────────────────────────

class ManualEntryView(APIView):
    """Guard manually types a plate number — no image scan required."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        plate_number = (request.data.get('plate_number') or '').strip().upper().replace(' ', '')
        if not plate_number:
            return Response({'error': 'plate_number is required.'}, status=400)

        gate_id = getattr(request.user, 'gate_assignment', None) or 'main'
        # A guard may type a conduction number for a brand-new car, which is not a
        # valid PH plate — accept it when it resolves to a registered vehicle, but
        # still reject free-text garbage that matches nothing.
        vehicle = Vehicle.resolve(plate_number)
        # An organizer list may name a brand-new car by its conduction sticker,
        # which is no plate shape — so a listed identifier is let through the
        # format check the same way a registered one is.
        event = None if vehicle else _event_for_unregistered_plate(plate_number)
        if not vehicle and not event and not is_valid_ph_plate(plate_number):
            return Response({'error': 'Invalid plate format. Enter a valid Philippine plate or conduction number.'}, status=400)

        if not vehicle:
            # An organizer list outranks the supplier roster while its event
            # is on — see _event_for_unregistered_plate.
            if event:
                r = _event_plate_result(plate_number, event, gate_id, request.user)
                return Response({**r, 'plate_number': plate_number, 'gate_id': gate_id})

            supplier_plate = SupplierPlate.objects.select_related('supplier').filter(
                plate_number=plate_number, supplier__is_active=True
            ).first()

            if not supplier_plate:
                if is_open_campus():
                    r = _open_campus_unknown_result(plate_number, gate_id, request.user)
                    return Response({**r, 'plate_number': plate_number, 'gate_id': gate_id})
                AccessLog.objects.create(
                    plate_number=plate_number,
                    status=AccessLog.Status.UNKNOWN,
                    gate_id=gate_id,
                    scanned_by=request.user,
                )
                return Response({
                    'plate_number': plate_number,
                    'status':       'unknown',
                    'allowed':      False,
                    'message':      'Plate not found in the system.',
                    'gate_id':      gate_id,
                })

            supplier_name = supplier_plate.supplier.company_name
            inside_status, last_entry = _inside_state(plate_number)

            if inside_status == 'duplicate':
                return Response({
                    'plate_number':   plate_number,
                    'status':         'duplicate',
                    'allowed':        False,
                    'message':        'Duplicate scan — already processed within grace period.',
                    'is_supplier':    True,
                    'supplier_name':  supplier_name,
                    'already_inside': True,
                    'gate_id':        gate_id,
                })

            if inside_status == 'inside':
                # Single check action toggles state like the camera: within the
                # breathing window a re-check is informational; past it, it records the exit
                seconds_inside = (timezone.now() - last_entry.scanned_at).total_seconds()
                if seconds_inside < ENTRY_BREATHING_SECONDS:
                    window_left = int(ENTRY_BREATHING_SECONDS - seconds_inside)
                    return Response({
                        'plate_number':        plate_number,
                        'status':              'already_inside',
                        'allowed':             False,
                        'message':             f'Supplier vehicle — {supplier_name} just entered. '
                                               f'Re-check in {window_left}s to record an exit.',
                        'is_supplier':         True,
                        'supplier_name':       supplier_name,
                        'already_inside':      True,
                        'retry_after_seconds': window_left,
                        'gate_id':             gate_id,
                    })
                from django.db import transaction as _tx
                with _tx.atomic():
                    locked_entry = AccessLog.objects.select_for_update().filter(pk=last_entry.pk).first()
                    if not locked_entry or AccessLog.objects.filter(paired_entry=locked_entry).exists():
                        return Response({
                            'plate_number':   plate_number,
                            'status':         'duplicate',
                            'allowed':        False,
                            'message':        'Duplicate scan — already processed.',
                            'is_supplier':    True,
                            'supplier_name':  supplier_name,
                            'already_inside': False,
                            'gate_id':        gate_id,
                        })
                    exit_log = AccessLog.objects.create(
                        plate_number=plate_number,
                        status=AccessLog.Status.EXITED,
                        gate_id=gate_id,
                        scanned_by=request.user,
                        paired_entry=locked_entry,
                    )
                duration_minutes = int((exit_log.scanned_at - last_entry.scanned_at).total_seconds() / 60)
                overstay_minutes = _check_stay_limit(plate_number, None, 'supplier', duration_minutes, gate_id)
                overstay_note = f' Overstayed by {overstay_minutes} min — violation issued.' if overstay_minutes else ''
                return Response({
                    'plate_number':      plate_number,
                    'status':            'exited',
                    'allowed':           False,
                    'message':           f'Supplier vehicle — {supplier_name}. Exit recorded. Duration: {duration_minutes} min.{overstay_note}',
                    'is_supplier':       True,
                    'supplier_name':     supplier_name,
                    'already_inside':    False,
                    'duration_minutes':  duration_minutes,
                    'overstay_minutes':  overstay_minutes,
                    'gate_id':           gate_id,
                })

            cooldown_left = _exit_cooldown_remaining(plate_number)
            if cooldown_left:
                return Response({
                    'plate_number':        plate_number,
                    'status':              'duplicate',
                    'allowed':             False,
                    'message':             f'Exit cooldown — entry suppressed for {cooldown_left}s more.',
                    'is_supplier':         True,
                    'supplier_name':       supplier_name,
                    'already_inside':      False,
                    'retry_after_seconds': cooldown_left,
                    'gate_id':             gate_id,
                })

            deny_msg = _supplier_rule_denial()
            if deny_msg:
                AccessLog.objects.create(
                    plate_number=plate_number, status=AccessLog.Status.DENIED,
                    denied_reason=deny_msg, gate_id=gate_id, scanned_by=request.user,
                )
                return Response({
                    'plate_number':  plate_number,
                    'status':        'denied',
                    'allowed':       False,
                    'message':       deny_msg,
                    'is_supplier':   True,
                    'supplier_name': supplier_name,
                    'gate_id':       gate_id,
                })

            entry_log = AccessLog.objects.create(
                plate_number=plate_number, status=AccessLog.Status.AUTHORIZED,
                gate_id=gate_id, scanned_by=request.user,
            )
            open_campus = is_open_campus()
            from .slips import supplier_slip
            return Response({
                'plate_number':  plate_number,
                'status':        'open_entry' if open_campus else 'authorized',
                'allowed':       True,
                'message':       (f'Open Campus Mode active — Supplier vehicle {supplier_name}. Open entry granted.'
                                  if open_campus else
                                  f'Supplier vehicle — {supplier_name}. Entry permitted.'),
                'is_supplier':   True,
                'supplier_name': supplier_name,
                'supplier_slip': supplier_slip(entry_log),   # printed by the guard page
                'gate_id':       gate_id,
            })

        inside_status, last_entry = _inside_state(plate_number)

        # A visitor inside on an active pass is NOT logged out by a re-check.
        # The typed plate pulls up their slip — still inside, time left — and
        # the guard records the exit (or reprints) from there, so the slip's
        # status only ever changes on purpose. Ahead of the duplicate check:
        # looking a slip up is harmless however soon after entry it happens.
        if inside_status in ('inside', 'duplicate'):
            visitor_pass = _active_visitor_pass(plate_number)
            if visitor_pass:
                from .slips import visitor_slip
                return Response({
                    'plate_number':   plate_number,
                    'status':         'visitor_pass_required',
                    'allowed':        False,
                    'message':        'Visitor is inside on an active pass.',
                    'vehicle':        VehicleSerializer(vehicle).data,
                    'already_inside': True,
                    'gate_id':        gate_id,
                    'slip':           visitor_slip(visitor_pass),
                })

        if inside_status == 'duplicate':
            return Response({
                'plate_number':   plate_number,
                'status':         'duplicate',
                'allowed':        False,
                'message':        'Duplicate scan — already processed within grace period.',
                'vehicle':        VehicleSerializer(vehicle).data,
                'already_inside': True,
                'gate_id':        gate_id,
            })

        if inside_status == 'inside':
            # Single check action toggles state like the camera: within the
            # breathing window a re-check is informational; past it, it records the exit
            seconds_inside = (timezone.now() - last_entry.scanned_at).total_seconds()
            owner_name = vehicle.user.full_name if vehicle.user else 'Unknown'
            if seconds_inside < ENTRY_BREATHING_SECONDS:
                window_left = int(ENTRY_BREATHING_SECONDS - seconds_inside)
                return Response({
                    'plate_number':        plate_number,
                    'status':              'already_inside',
                    'allowed':             False,
                    'message':             f'{owner_name} — vehicle just entered. '
                                           f'Re-check in {window_left}s to record an exit.',
                    'vehicle':             VehicleSerializer(vehicle).data,
                    'already_inside':      True,
                    'retry_after_seconds': window_left,
                    'gate_id':             gate_id,
                })
            from django.db import transaction as _tx
            with _tx.atomic():
                locked_entry = AccessLog.objects.select_for_update().filter(
                    pk=last_entry.pk
                ).first()
                if not locked_entry or AccessLog.objects.filter(paired_entry=locked_entry).exists():
                    return Response({
                        'plate_number':   plate_number,
                        'status':         'duplicate',
                        'allowed':        False,
                        'message':        'Duplicate scan — already processed.',
                        'vehicle':        VehicleSerializer(vehicle).data,
                        'already_inside': False,
                        'gate_id':        gate_id,
                    })
                exit_log = AccessLog.objects.create(
                    plate_number=plate_number,
                    vehicle=vehicle,
                    status=AccessLog.Status.EXITED,
                    gate_id=gate_id,
                    scanned_by=request.user,
                    paired_entry=locked_entry,
                )
            duration_minutes = int((exit_log.scanned_at - last_entry.scanned_at).total_seconds() / 60)
            overstay_minutes = _close_active_pass(plate_number, gate_id)
            if vehicle.user and vehicle.user.owner_type == 'fetcher' and not _is_standby_fetcher(vehicle.user):
                overstay_minutes = max(overstay_minutes, _check_stay_limit(
                    plate_number, vehicle, 'fetcher', duration_minutes, gate_id))
            overstay_note = f' Overstayed by {overstay_minutes} min.' if overstay_minutes else ''
            return Response({
                'plate_number':    plate_number,
                'status':          'exited',
                'allowed':         False,
                'message':         f'{owner_name} — Exit recorded. Duration: {duration_minutes} min.{overstay_note}',
                'vehicle':         VehicleSerializer(vehicle).data,
                'already_inside':  False,
                'duration_minutes': duration_minutes,
                'overstay_minutes': overstay_minutes,
                'gate_id':         gate_id,
            })

        cooldown_left = _exit_cooldown_remaining(plate_number)
        if cooldown_left:
            return Response({
                'plate_number':        plate_number,
                'status':              'duplicate',
                'allowed':             False,
                'message':             f'Exit cooldown — entry suppressed for {cooldown_left}s more.',
                'vehicle':             VehicleSerializer(vehicle).data,
                'already_inside':      False,
                'retry_after_seconds': cooldown_left,
                'gate_id':             gate_id,
            })

        entry = check_entry(vehicle)
        has_violations = Violation.objects.filter(vehicle=vehicle, is_resolved=False).exists()

        # UI-only statuses (e.g. 'no_pass', 'open_entry') aren't valid AccessLog statuses
        AccessLog.objects.create(
            plate_number  = plate_number,
            vehicle       = vehicle,
            status        = _log_status(entry),
            gate_id       = gate_id,
            denied_reason = '' if entry['allowed'] else entry['message'],
            scanned_by    = request.user,
        )

        # 'no_pass'/'unknown' mean a visitor awaiting a pass — not a violation
        if not entry['allowed'] and entry['status'] not in ('no_pass', 'unknown'):
            _auto_log_violation(vehicle, entry['message'], gate_id,
                                    evidence_bytes=_request_image_bytes(request),
                                    entry_status=entry['status'])

        return Response({
            'plate_number':    plate_number,
            'status':          entry['status'],
            'allowed':         entry['allowed'],
            'message':         entry['message'],
            'constraint':      entry.get('constraint'),
            'vehicle':         VehicleSerializer(vehicle).data,
            'has_violations':  has_violations,
            'already_inside':  False,
            'organizer_event': get_organizer_event(*vehicle_identifiers(vehicle, plate_number)),
            'gate_id':         gate_id,
        })


class OwnerLookupView(APIView):
    """Find a vehicle by its owner's NAME, or by plate / conduction number.

    A guard at the barrier does not always have a readable plate to work from —
    a mud-covered plate, a driver who gives their name, a conduction sticker in
    the windscreen. One search box has to take all three, so the query is tried
    as an identifier and as a name and the union comes back.

    This only *finds* the vehicle. The entry/exit decision still goes through
    the normal plate check, so no rule can be skipped by searching by name.
    """
    permission_classes = [permissions.IsAuthenticated]

    MAX_RESULTS = 12

    def get(self, request):
        query = (request.query_params.get('q') or '').strip()
        if len(query) < 2:
            return Response(
                {'error': 'Type at least 2 characters — a name, plate, or conduction number.'},
                status=400,
            )

        identifier = query.upper().replace(' ', '')
        matches = (
            Vehicle.objects
            .select_related('user')
            .filter(
                Q(plate_number__icontains=identifier)
                | Q(conduction_number__icontains=identifier)
                | Q(user__full_name__icontains=query)
                # The registration carries the name for vehicles whose owner
                # account has since been renamed or archived; a guard searching
                # the name on the pass must still find the car.
                | Q(registrations__full_name__icontains=query)
            )
            .distinct()
            .order_by('plate_number', 'conduction_number')[:self.MAX_RESULTS + 1]
        )
        matches = list(matches)
        truncated = len(matches) > self.MAX_RESULTS
        matches = matches[:self.MAX_RESULTS]

        # Today's visitors on an active pass, by the name on their slip. A
        # visitor's car is usually an unregistered plate with no owner account,
        # so the vehicle search above cannot find them by name. Listed first:
        # at the barrier an active visitor is by far the likelier match.
        visitor_passes = list(
            VisitorPass.objects
            .select_related('vehicle')
            .filter(valid_date=timezone.localdate(), status=VisitorPass.Status.ACTIVE)
            .filter(Q(visitor_name__icontains=query) | Q(plate_number__icontains=identifier))
            .order_by('-entered_at')[:self.MAX_RESULTS]
        )
        visitor_vehicle_ids = {p.vehicle_id for p in visitor_passes}
        matches = [v for v in matches if v.pk not in visitor_vehicle_ids]

        inside = _plates_inside([v.identifier for v in matches] + [p.plate_number for p in visitor_passes])

        results = []
        for p in visitor_passes:
            v = p.vehicle
            results.append({
                'vehicle_id':        v.pk,
                'identifier':        p.plate_number,
                'plate_number':      p.plate_number,
                'conduction_number': v.conduction_number,
                'vehicle_type':      v.vehicle_type,
                'model':             v.model,
                'color':             v.color,
                'is_authorized':     v.is_authorized,
                'owner_name':        p.visitor_name or 'Visitor (no name on pass)',
                'owner_type':        'visitor',
                'classification':    'visitor',
                'is_inside':         p.plate_number in inside,
                # Picking this opens the slip (inside? record exit? reprint?)
                # rather than running a plate check.
                'slip_code':         p.qr_payload,   # the newest printed copy's code
            })

        # No-plate vehicles still inside today, by the driver's name. They
        # have no plate to search by, so the name is the only way to them.
        start, end = day_range(timezone.localdate())
        noplate = (
            AccessLog.objects
            .filter(is_unrecognized=True, status=AccessLog.Status.AUTHORIZED,
                    driver_name__icontains=query, scanned_at__gte=start, scanned_at__lt=end)
            .exclude(pk__in=AccessLog.objects.filter(
                paired_entry__isnull=False, scanned_at__gte=start, scanned_at__lt=end,
            ).values_list('paired_entry_id', flat=True))
            .order_by('-scanned_at')[:self.MAX_RESULTS]
        )
        for log in noplate:
            results.append({
                'vehicle_id':        f'np-{log.pk}',
                'identifier':        '',
                'plate_number':      '',
                'conduction_number': '',
                'vehicle_type':      log.vehicle_type,
                'model':             log.vehicle_model,
                'color':             log.vehicle_color,
                'is_authorized':     False,
                'owner_name':        f'{log.driver_name} · NP-{log.pk}',
                'owner_type':        '',
                'classification':    log.entrant_category or 'unknown',
                'is_inside':         True,
                'slip_code':         f'SLC-NOPLATE:{log.pk}',
            })
        for v in matches:
            owner = v.user
            plate = v.identifier
            results.append({
                'vehicle_id':        v.pk,
                'identifier':        plate,
                'plate_number':      v.plate_number,
                'conduction_number': v.conduction_number,
                'vehicle_type':      v.vehicle_type,
                'model':             v.model,
                'color':             v.color,
                'is_authorized':     v.is_authorized,
                'owner_name':        owner.full_name if owner else '',
                'owner_type':        owner.owner_type if owner else '',
                'classification':    classify_entrant(v, plate),
                'is_inside':         plate in inside,
            })

        truncated = truncated or len(results) > self.MAX_RESULTS
        results = results[:self.MAX_RESULTS]
        return Response({
            'query':     query,
            'count':     len(results),
            'truncated': truncated,
            'results':   results,
        })


class UnrecognizedEntryView(APIView):
    """Vehicles with no usable plate, recorded by hand.

    A car with no plate and no conduction sticker still drives onto campus.
    Before this it produced an 'unreadable' row with an empty plate and nothing
    else — no description, no driver, no way to record the exit — so those
    vehicles were effectively invisible in the log and in the inside-count.

    The guard fills in what they can see (who is driving, what the vehicle
    looks like, which kind of entrant it is) and the row behaves like any other
    entry: it counts as inside, it appears in the log, and it is closed with an
    exit. There is no plate to key on, so the exit is recorded against the row
    itself rather than by typing an identifier the vehicle does not have.
    """
    permission_classes = [permissions.IsAuthenticated]

    # What the guard can classify a walk-up as. 'supplier' is deliberately
    # absent: a supplier is identified by a plate on the supplier roster, and
    # there is no plate here to check one against.
    ALLOWED_CATEGORIES = {
        AccessLog.Category.STUDENT,
        AccessLog.Category.EMPLOYEE,
        AccessLog.Category.FETCHER,
        AccessLog.Category.VISITOR,
        AccessLog.Category.UNKNOWN,
    }

    def _serialize(self, log, exit_log=None):
        data = AccessLogSerializer(log).data
        data['reference'] = 'NP-%d' % log.pk
        if exit_log is not None:
            delta = exit_log.scanned_at - log.scanned_at
            data['exited_at']        = exit_log.scanned_at
            data['duration_minutes'] = int(delta.total_seconds() / 60)
        return data

    def get(self, request):
        """Unrecognized vehicles still inside — the panel the guard closes from."""
        gate_id = (request.query_params.get('gate_id')
                   or getattr(request.user, 'gate_assignment', None))
        start, end = day_range(timezone.localdate())
        logs = AccessLog.objects.filter(
            is_unrecognized=True,
            status=AccessLog.Status.AUTHORIZED,
            scanned_at__gte=start, scanned_at__lt=end,
        ).exclude(
            # Bounded to today like the rows it filters: unbounded, this
            # subquery scans every exit ever recorded to answer a question
            # about this shift.
            pk__in=AccessLog.objects.filter(
                paired_entry__isnull=False,
                scanned_at__gte=start, scanned_at__lt=end,
            ).values_list('paired_entry_id', flat=True)
        )
        if gate_id:
            logs = logs.filter(gate_id=gate_id)
        return Response([self._serialize(l) for l in logs.order_by('-scanned_at')])

    def post(self, request):
        driver_name = (request.data.get('driver_name') or '').strip()
        category    = (request.data.get('entrant_category') or '').strip()
        vtype       = (request.data.get('vehicle_type') or '').strip().lower()
        color       = (request.data.get('vehicle_color') or '').strip()
        model       = (request.data.get('vehicle_model') or '').strip()
        note        = (request.data.get('entry_note') or '').strip()

        problems = {}
        if not driver_name:
            problems['driver_name'] = "Enter the driver's name — it is the only identifier this vehicle has."
        if category not in self.ALLOWED_CATEGORIES:
            problems['entrant_category'] = 'Choose who is entering: student, employee, fetcher, visitor, or unregistered.'
        if vtype not in Vehicle.Type.values:
            problems['vehicle_type'] = 'Choose the vehicle type.'
        if not color:
            problems['vehicle_color'] = 'Enter the vehicle colour — without a plate it is how this vehicle is told apart.'
        if problems:
            return Response(problems, status=400)

        gate_id = (request.data.get('gate_id')
                   or getattr(request.user, 'gate_assignment', None)
                   or 'main')
        log = AccessLog.objects.create(
            plate_number     = '',
            vehicle_type     = vtype,
            status           = AccessLog.Status.AUTHORIZED,
            entrant_category = category,
            is_unrecognized  = True,
            driver_name      = driver_name,
            vehicle_color    = color,
            vehicle_model    = model,
            entry_note       = note,
            gate_id          = gate_id,
            scanned_by       = request.user,
        )
        described = ('%s %s %s' % (color, vtype, model)).strip()
        _audit(
            request, AuditLog.Action.RECORD_CREATED,
            'Unrecognized vehicle admitted | Ref: NP-%d | Driver: %s | %s | '
            'Category: %s | Gate: %s | Guard: %s' % (
                log.pk, driver_name, described,
                dict(AccessLog.Category.choices).get(category, category),
                _gate_label(gate_id), request.user.full_name,
            ),
        )
        from .slips import noplate_slip
        data = self._serialize(log)
        data['slip'] = noplate_slip(log)   # printed by the guard page straight away
        return Response(data, status=201)


def _record_noplate_exit(request, entry, gate_id):
    """Log the exit of a hand-recorded, plateless vehicle. Returns
    (minutes inside, the exit AccessLog)."""
    exit_log = AccessLog.objects.create(
        plate_number     = '',
        vehicle_type     = entry.vehicle_type,
        status           = AccessLog.Status.EXITED,
        entrant_category = entry.entrant_category,
        is_unrecognized  = True,
        driver_name      = entry.driver_name,
        vehicle_color    = entry.vehicle_color,
        vehicle_model    = entry.vehicle_model,
        gate_id          = gate_id,
        scanned_by       = request.user,
        paired_entry     = entry,
    )
    duration = int((exit_log.scanned_at - entry.scanned_at).total_seconds() / 60)
    _audit(
        request, AuditLog.Action.RECORD_UPDATED,
        'Unrecognized vehicle exited | Ref: NP-%d | Driver: %s | Duration: %d min | '
        'Gate: %s | Guard: %s' % (
            entry.pk, entry.driver_name, duration,
            _gate_label(gate_id), request.user.full_name,
        ),
    )
    return duration, exit_log


class UnrecognizedExitView(APIView):
    """Record the exit of a hand-recorded, plateless vehicle."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        entry = AccessLog.objects.filter(pk=pk, is_unrecognized=True).first()
        if not entry:
            return Response({'error': 'No unrecognized entry with that reference.'}, status=404)
        if entry.status != AccessLog.Status.AUTHORIZED:
            return Response({'error': 'That record is not an entry.'}, status=400)
        if AccessLog.objects.filter(paired_entry=entry).exists():
            return Response({'error': 'This vehicle has already been logged out.'}, status=409)

        gate_id = (request.data.get('gate_id')
                   or getattr(request.user, 'gate_assignment', None)
                   or entry.gate_id or 'main')
        duration, exit_log = _record_noplate_exit(request, entry, gate_id)
        return Response({
            'reference':        'NP-%d' % entry.pk,
            'status':           'exited',
            'driver_name':      entry.driver_name,
            'duration_minutes': duration,
            'scanned_at':       exit_log.scanned_at,
        })


class QRLoginView(APIView):
    """
    Kiosk QR scan login — validates guard's QR token, ends any active shift
    at their assigned gate, starts a new shift, and returns JWT tokens.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        from rest_framework_simplejwt.tokens import RefreshToken
        from django.utils import timezone as tz
        from .models import Gate

        qr_token = (request.data.get('qr_token') or '').strip()
        if not qr_token:
            return Response({'error': 'qr_token is required.'}, status=400)

        try:
            guard = User.objects.get(qr_token=qr_token, role='security', is_active=True)
        except User.DoesNotExist:
            return Response({'error': 'Invalid or unrecognised QR code.'}, status=403)

        # Gate is selected by the guard at the login screen — that selection IS their assignment.
        gate = (request.data.get('gate') or '').strip() or guard.gate_assignment
        if gate not in Gate.active_ids():
            return Response({'error': 'Please select a valid gate before scanning.'}, status=400)

        # Persist the gate the guard logged in at on their profile.
        if guard.gate_assignment != gate:
            User.objects.filter(pk=guard.pk).update(gate_assignment=gate)
            guard.gate_assignment = gate

        # Records the exact gate and clock-in time, and closes both the guard
        # being relieved here and this guard's own stale session at another
        # gate — see scanning.models.open_shift_for.
        _shift, displaced = open_shift_for(guard, gate)

        # Issue JWT
        refresh  = RefreshToken.for_user(guard)
        access   = str(refresh.access_token)

        # Audit
        _audit_ip = get_client_ip(request)
        gate_label = _gate_label(gate)
        try:
            AuditLog.objects.create(
                actor=guard,
                action=AuditLog.Action.GUARD_LOGIN,
                details=f"Guard shift login | {gate_label} | {guard.full_name}",
                ip_address=_audit_ip,
            )
        except Exception:
            pass

        return Response({
            'access':  access,
            'refresh': str(refresh),
            'user': {
                'id':              guard.id,
                'user_code':       guard.user_code,
                'full_name':       guard.full_name,
                'email':           guard.email,
                'role':            guard.role,
                'gate_assignment': gate,
                'must_change_password': guard.must_change_password,
            },
            # Gates this sign-in signed the same guard out of — see
            # scanning.models.open_shift_for.
            'signed_out_of': displaced,
        })


class CurrentShiftsView(APIView):
    """Return the currently active shift for each gate."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        active = (
            GuardShift.objects
            .filter(clocked_out_at__isnull=True)
            .select_related('guard')
        )
        result = {}
        for shift in active:
            result[shift.gate] = {
                # The id as well as the name: the Operations Center guard table
                # has to say which of its rows is the guard standing at this
                # gate, and matching on a display name is not an identity.
                'guard_id':      shift.guard_id,
                'guard_name':    shift.guard.full_name,
                'guard_code':    shift.guard.user_code,
                'gate':          shift.gate,
                'clocked_in_at': shift.clocked_in_at,
            }
        return Response(result)


class GuardShiftListView(APIView):
    """Admin: paginated full shift history."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if request.user.role != 'admin':
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()

        from .serializers import GuardShiftSerializer as GSSer
        gate   = request.query_params.get('gate', '').strip()
        guard  = request.query_params.get('guard', '').strip()
        date   = request.query_params.get('date', '').strip()

        qs = GuardShift.objects.select_related('guard', 'clocked_out_by').all()
        if gate:
            qs = qs.filter(gate=gate)
        if guard:
            qs = qs.filter(guard__id=guard)
        if date:
            try:
                _start, _end = day_range(datetime.strptime(date, '%Y-%m-%d').date())
                qs = qs.filter(clocked_in_at__gte=_start, clocked_in_at__lt=_end)
            except (TypeError, ValueError):
                pass  # ignore malformed dates rather than 500

        return Response(GSSer(qs[:100], many=True).data)