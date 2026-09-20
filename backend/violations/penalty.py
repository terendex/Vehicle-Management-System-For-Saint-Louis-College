"""The violation penalty ladder.

Offences cost an owner their campus access, not money:

    1st offence — account confiscated for 1 week
    2nd offence — account confiscated for 2 weeks
    3rd offence — confiscated for the rest of the registration period, and the
                  person may not register again unless the CDSO allows it

The count is per account across every tracked violation type, so three
different kinds of offence still reach a third strike.

Everything that issues a violation routes through `apply_penalty` — the gate
scanner, the parking camera and the CDSO screen alike — so the penalty can
never depend on a particular caller remembering to impose it.
"""

# =============================================================================
# HOW TO READ THIS FILE
#
# Four things happen here, in the order the functions appear:
#
#   1. Work out WHEN a penalty ends            (_period_end, confiscation_end_for)
#   2. Put that into words for people          (describe)
#   3. Impose it when a violation is issued    (apply_penalty, notify_owner)
#   4. Re-derive it when a violation goes away (recompute_for_owner)
#
# The penalty itself is stored on the owner's account row (confiscation_level,
# confiscated_at, confiscated_until, confiscation_reason). Nothing here runs on
# a timer: the account comes back by itself once today passes the end date,
# because User.is_confiscated compares that date to today on every read.
# =============================================================================

import logging                                  # so what was imposed is recorded in the server log

from django.utils import timezone               # today's date and the current time, campus-local

from accounts.models import User                # the account a penalty is applied to
from .models import CONFISCATION_DAYS, NEW_STYLE_TYPES, Violation   # the ladder's own constants

log = logging.getLogger(__name__)               # messages appear under "violations.penalty"


# The last day of the registration period in force, used as the end of a
# 3rd-strike penalty.
def _period_end():
    """Last day of the active registration period, or None if there isn't one.

    None means the 3rd-offence penalty is stored with no end date, which
    `User.is_confiscated` reads as indefinite. That is the honest outcome: with
    no period to run against, "for the entire duration" has no date to compute,
    and the CDSO lifting it by hand is the only way out.
    """
    from vehicles.models import RegistrationPeriod   # imported here to avoid a circular import
    period = RegistrationPeriod.get_active()
    if period is None:
        return None                              # no period configured: indefinite
    # A period that already ended cannot be the end of a penalty starting now.
    today = timezone.localdate()
    return period.end_date if period.end_date >= today else None


# Turns a strike number into the date the penalty runs to.
def confiscation_end_for(level: int):
    """The end date a penalty at this level runs to (None = indefinite)."""
    if level in CONFISCATION_DAYS:               # levels 1 and 2 have fixed lengths (7 and 14 days)
        from datetime import timedelta
        return timezone.localdate() + timedelta(days=CONFISCATION_DAYS[level])   # counted from today
    return _period_end()                         # level 3: to the end of the registration period, or indefinite


# Writes the penalty as a sentence, so the owner, the guard and the audit trail
# all read exactly the same wording.
def describe(level: int, until) -> str:
    """One plain sentence for the owner, the guard screen and the audit trail."""
    if level == 1:
        span = '1 week'
    elif level == 2:
        span = '2 weeks'
    else:
        span = 'the rest of the registration period'
    if until is None and level >= 3:
        # No period to run against, so say plainly that only the CDSO can end it.
        return ('Account confiscated indefinitely after a 3rd offence — '
                'the CDSO must lift it.')
    if until is None:
        return f'Account confiscated for {span}.'
    return (f'Account confiscated for {span}, until '
            f'{until.strftime("%B %d, %Y")}.')    # e.g. "until September 27, 2026"


# Imposes the ladder when a violation is issued. This is the one path every
# caller goes through, which is what stops a penalty being forgotten.
def apply_penalty(violation: Violation) -> dict | None:
    """Impose the ladder for a newly issued violation.

    Returns a summary dict, or None when the violation does not count toward
    the ladder (legacy types, or one with no owner to penalise).

    Stacking rule: a new offence always REPLACES the running penalty rather
    than adding to it. A 2nd strike is "two weeks from today", not "two weeks
    once the first week finishes" — otherwise a burst of detections on one day
    would compound into months.
    """
    if violation.violation_type not in NEW_STYLE_TYPES:
        return None                              # an old-style type: no ladder, nothing to impose

    owner = violation.owner or (violation.vehicle.user if violation.vehicle_id else None)   # the account named on the row, else the vehicle's owner
    if owner is None:
        # A gate-issued vehicle with no account behind it. The violation still
        # stands as a record; there is simply no account to confiscate.
        return None

    level = min(violation.offense_number or Violation.compute_offense_number(owner), 3)   # use the stamped strike, or work it out; never above 3
    until = confiscation_end_for(level)          # when it ends (None = indefinite)
    reason = describe(level, until)              # the sentence everyone will read

    owner.confiscation_level  = level            # these four fields together ARE the penalty
    owner.confiscated_at      = timezone.now()
    owner.confiscated_until   = until
    owner.confiscation_reason = reason
    fields = ['confiscation_level', 'confiscated_at',
              'confiscated_until', 'confiscation_reason']   # write only these columns

    # The 3rd strike also closes the door on registering again. The CDSO can
    # reopen it — that decision is theirs, so this only ever sets the flag and
    # never clears one an officer has already lifted.
    if level >= 3 and not owner.registration_banned:
        owner.registration_banned = True
        fields.append('registration_banned')

    owner.save(update_fields=fields)             # one UPDATE, touching nothing else on the account

    log.info('Confiscated %s (offence %s) until %s', owner.email, level, until)   # leaves a trace in the server log

    return {'level': level, 'until': until, 'reason': reason}   # the caller puts this in its response


# Tells the owner by email. Deliberately swallows any failure.
def notify_owner(violation, penalty):
    """Email the owner what happened. Never raises — a failed send must not roll
    back the penalty that was actually imposed, and the owner can still see it
    on their portal."""
    if not penalty:
        return                                   # nothing was imposed, so there is nothing to announce
    try:
        from .email_utils import send_confiscation_email
        send_confiscation_email(violation, penalty)
    except Exception:
        log.exception('Could not email confiscation notice for violation %s', violation.pk)   # log it, including the traceback, and carry on


# Recalculates the penalty after a violation is lifted or cleared, so the
# account is never serving a sentence its record no longer supports.
def recompute_for_owner(owner) -> None:
    """Re-derive the account's penalty from the violations that remain.

    Called after a violation is lifted or cleared. Dropping to zero active
    offences lifts the confiscation; dropping from 3 to 2 pulls the penalty
    back down to the 2nd-offence term rather than leaving the account serving a
    sentence its record no longer supports.

    The registration ban is deliberately NOT cleared here. It is the CDSO's
    call to let someone register again, and a lifted violation should not make
    that decision on their behalf.
    """
    if owner is None:
        return

    Violation.resequence_offenses(owner)         # renumber what remains, oldest first
    count = Violation.active_for_owner(owner).count()   # how many still stand

    if count == 0:
        if owner.confiscation_level:
            owner.clear_confiscation()           # nothing left: lift the penalty entirely
        return

    level = min(count, 3)                        # the strike the remaining record supports
    if owner.confiscation_level == level:
        return                                   # already at that level: nothing to change

    # Re-derive the end date from the offence that now stands. The original
    # start is kept so shortening a penalty cannot extend it.
    #
    # Note, factually: confiscated_at (the start) is indeed left alone, but the
    # new end date comes from confiscation_end_for(), which counts from TODAY —
    # so a downgrade sets a fresh term running from now rather than from the
    # original start.
    until = confiscation_end_for(level)
    owner.confiscation_level  = level
    owner.confiscated_until   = until
    owner.confiscation_reason = describe(level, until)
    owner.save(update_fields=[
        'confiscation_level', 'confiscated_until', 'confiscation_reason',
    ])                                           # confiscated_at is not in this list, so the start stays as it was


# Everyone currently serving a penalty, for the screens that list them.
def confiscated_owners():
    """Every account currently serving a penalty.

    The date comparison lives here rather than in each caller so the three
    screens that show this list cannot drift apart on what "confiscated" means.
    """
    from django.db.models import Q
    today = timezone.localdate()
    return (User.objects
            .filter(role=User.Role.VEHICLE_OWNER, confiscation_level__gt=0)   # owners with a penalty on record
            .filter(Q(confiscated_until__isnull=True) | Q(confiscated_until__gte=today))   # indefinite, or not yet expired
            .order_by('-confiscated_at'))        # most recently confiscated first
