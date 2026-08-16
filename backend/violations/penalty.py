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

import logging

from django.utils import timezone

from accounts.models import User
from .models import CONFISCATION_DAYS, NEW_STYLE_TYPES, Violation

log = logging.getLogger(__name__)


def _period_end():
    """Last day of the active registration period, or None if there isn't one.

    None means the 3rd-offence penalty is stored with no end date, which
    `User.is_confiscated` reads as indefinite. That is the honest outcome: with
    no period to run against, "for the entire duration" has no date to compute,
    and the CDSO lifting it by hand is the only way out.
    """
    from vehicles.models import RegistrationPeriod
    period = RegistrationPeriod.get_active()
    if period is None:
        return None
    # A period that already ended cannot be the end of a penalty starting now.
    today = timezone.localdate()
    return period.end_date if period.end_date >= today else None


def confiscation_end_for(level: int):
    """The end date a penalty at this level runs to (None = indefinite)."""
    if level in CONFISCATION_DAYS:
        from datetime import timedelta
        return timezone.localdate() + timedelta(days=CONFISCATION_DAYS[level])
    return _period_end()


def describe(level: int, until) -> str:
    """One plain sentence for the owner, the guard screen and the audit trail."""
    if level == 1:
        span = '1 week'
    elif level == 2:
        span = '2 weeks'
    else:
        span = 'the rest of the registration period'
    if until is None and level >= 3:
        return ('Account confiscated indefinitely after a 3rd offence — '
                'the CDSO must lift it.')
    if until is None:
        return f'Account confiscated for {span}.'
    return (f'Account confiscated for {span}, until '
            f'{until.strftime("%B %d, %Y")}.')


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
        return None

    owner = violation.owner or (violation.vehicle.user if violation.vehicle_id else None)
    if owner is None:
        # A gate-issued vehicle with no account behind it. The violation still
        # stands as a record; there is simply no account to confiscate.
        return None

    level = min(violation.offense_number or Violation.compute_offense_number(owner), 3)
    until = confiscation_end_for(level)
    reason = describe(level, until)

    owner.confiscation_level  = level
    owner.confiscated_at      = timezone.now()
    owner.confiscated_until   = until
    owner.confiscation_reason = reason
    fields = ['confiscation_level', 'confiscated_at',
              'confiscated_until', 'confiscation_reason']

    # The 3rd strike also closes the door on registering again. The CDSO can
    # reopen it — that decision is theirs, so this only ever sets the flag and
    # never clears one an officer has already lifted.
    if level >= 3 and not owner.registration_banned:
        owner.registration_banned = True
        fields.append('registration_banned')

    owner.save(update_fields=fields)

    log.info('Confiscated %s (offence %s) until %s', owner.email, level, until)

    return {'level': level, 'until': until, 'reason': reason}


def notify_owner(violation, penalty):
    """Email the owner what happened. Never raises — a failed send must not roll
    back the penalty that was actually imposed, and the owner can still see it
    on their portal."""
    if not penalty:
        return
    try:
        from .email_utils import send_confiscation_email
        send_confiscation_email(violation, penalty)
    except Exception:
        log.exception('Could not email confiscation notice for violation %s', violation.pk)


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

    Violation.resequence_offenses(owner)
    count = Violation.active_for_owner(owner).count()

    if count == 0:
        if owner.confiscation_level:
            owner.clear_confiscation()
        return

    level = min(count, 3)
    if owner.confiscation_level == level:
        return

    # Re-derive the end date from the offence that now stands. The original
    # start is kept so shortening a penalty cannot extend it.
    until = confiscation_end_for(level)
    owner.confiscation_level  = level
    owner.confiscated_until   = until
    owner.confiscation_reason = describe(level, until)
    owner.save(update_fields=[
        'confiscation_level', 'confiscated_until', 'confiscation_reason',
    ])


def confiscated_owners():
    """Every account currently serving a penalty.

    The date comparison lives here rather than in each caller so the three
    screens that show this list cannot drift apart on what "confiscated" means.
    """
    from django.db.models import Q
    today = timezone.localdate()
    return (User.objects
            .filter(role=User.Role.VEHICLE_OWNER, confiscation_level__gt=0)
            .filter(Q(confiscated_until__isnull=True) | Q(confiscated_until__gte=today))
            .order_by('-confiscated_at'))
