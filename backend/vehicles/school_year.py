"""The school year: August 1 to July 31, and what hangs off it.

A school year (S.Y.) Y runs from August 1 of Y to July 31 of Y+1, starting
with the first semester. Registration for it may open up to two months early,
so the dates a registration period may use run from June 1 of Y to July 31 of
Y+1. Every vehicle pass accepted for that school year is valid until its last
day, July 31 of Y+1.

The admin still picks a period's open and close dates; the school year itself
is never typed. It is read off the open date, and the close date must fall in
the same school year.
"""
from __future__ import annotations

from datetime import date

# Registration for S.Y. Y may open from this month of Y (two months before
# the first semester starts in August).
EARLY_OPEN_MONTH = 6


def school_year_of(day: date) -> int:
    """The school year (its starting calendar year) a date belongs to.

    June to December belong to the school year starting that year; January to
    May belong to the one that started the year before. June and July are the
    early-registration months of the coming year rather than the tail of the
    ending one, so a pass accepted in June runs to the July 31 after next.
    """
    return day.year if day.month >= EARLY_OPEN_MONTH else day.year - 1


def allowed_range(year: int) -> tuple[date, date]:
    """First and last date a registration period for S.Y. ``year`` may use."""
    return date(year, EARLY_OPEN_MONTH, 1), date(year + 1, 7, 31)


def valid_until(year: int) -> date:
    """The last day of S.Y. ``year``: every pass accepted for it ends here."""
    return date(year + 1, 7, 31)


def label(year: int) -> str:
    return f'S.Y. {year}–{year + 1}'


def pass_expiry_date(today: date) -> date:
    """The expires_at an owner account accepted ``today`` is given.

    July 31 at the end of the school year the active registration period is
    for, while that school year is still running; otherwise the end of the
    school year ``today`` falls in (see school_year_of). An account accepted
    from a closed period's backlog after its school year ended therefore still
    gets the year it is actually used in, never a date already past.
    """
    from .models import RegistrationPeriod
    period = RegistrationPeriod.get_active()
    if period is not None:
        until = valid_until(school_year_of(period.start_date))
        if until >= today:
            return until
    return valid_until(school_year_of(today))


def long_date(day: date) -> str:
    """'June 1, 2026'. Built by hand: Windows strftime has no %-d."""
    return f'{day:%B} {day.day}, {day.year}'


def validate_period(start: date, end: date, today: date) -> dict:
    """Field errors for a registration period's dates; empty when they are fine.

    The school year comes from ``start``. ``end`` must not be before ``start``
    and must stay inside that school year, and the school year must not have
    ended already: a window for a year that is over can only mislead.
    """
    year = school_year_of(start)
    first, last = allowed_range(year)
    if end < start:
        return {'end_date': 'Closes on must be on or after Opens on.'}
    if end > last:
        return {'end_date': (f'Closes on must be within {label(year)} '
                             f'({long_date(first)} to {long_date(last)}).')}
    if last < today:
        return {'start_date': f'{label(year)} has already ended. Choose dates in the current or coming school year.'}
    return {}
