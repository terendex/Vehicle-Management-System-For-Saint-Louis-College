"""The school year: August 1 to July 31, and everything derived from it.

A school year (S.Y.) Y runs from August 1 of Y to July 31 of Y+1. The admin
picks a school year ("2026-2027") and nothing else: its vehicle registration
period IS that school year, August 1 to July 31, and every vehicle pass
accepted under it is valid until its last day, July 31 of Y+1. No other dates
can be set, on the form or through the API.

Every date check here asks today() for "today". It is the server's campus
date (timezone.localdate()), so changing the computer's date on localhost, or
the instructor demo's Test Clock, moves every rule at once.

Periods saved before this rule existed have dates of their own ("legacy").
They stay readable and keep their history; see is_legacy().
"""
from __future__ import annotations

import re
from datetime import date

from django.utils import timezone

FIRST_MONTH = 8          # August: the first semester starts the school year


def today() -> date:
    """The one "today" every school year and registration date check uses."""
    return timezone.localdate()


def school_year_of(day: date) -> int:
    """The school year (its first calendar year) a date falls in."""
    return day.year if day.month >= FIRST_MONTH else day.year - 1


def period_dates(year: int) -> tuple[date, date]:
    """The registration period of S.Y. ``year``: August 1 to July 31."""
    return date(year, FIRST_MONTH, 1), date(year + 1, 7, 31)


def valid_until(year: int) -> date:
    """The last day of S.Y. ``year``: every pass accepted for it ends here."""
    return date(year + 1, 7, 31)


def label(year: int) -> str:
    return f'S.Y. {year}–{year + 1}'


def as_text(year: int) -> str:
    """'2026-2027', the form the selector and the API use."""
    return f'{year}-{year + 1}'


_TEXT = re.compile(r'^\s*(\d{4})\s*[-–]\s*(\d{4})\s*$')


def parse(text) -> int:
    """'2026-2027' -> 2026. Raises ValueError unless the years are consecutive."""
    m = _TEXT.match(str(text or ''))
    if not m or int(m.group(2)) != int(m.group(1)) + 1:
        raise ValueError('Choose a school year such as 2026-2027 (two consecutive years).')
    return int(m.group(1))


def year_of_period(start: date, end: date):
    """The school year a period covers exactly, or None for a legacy period."""
    year = school_year_of(start)
    return year if (start, end) == period_dates(year) else None


def is_legacy(period) -> bool:
    """A period saved with dates of its own, before school years were fixed."""
    return year_of_period(period.start_date, period.end_date) is None


def selectable_years(on: date | None = None) -> list[int]:
    """The school years the form offers: the current one and the next two."""
    current = school_year_of(on or today())
    return [current, current + 1, current + 2]


def long_date(day: date) -> str:
    """'August 1, 2026'. Built by hand: Windows strftime has no %-d."""
    return f'{day:%B} {day.day}, {day.year}'


def pass_expiry_date(on: date | None = None) -> date:
    """The expires_at an owner account accepted ``on`` (default: today) gets.

    July 31 at the end of the active registration period's school year while
    that school year is running; otherwise the end of the school year ``on``
    falls in. Never a date already past.
    """
    from .models import RegistrationPeriod
    on = on or today()
    period = RegistrationPeriod.get_active()
    if period is not None:
        until = valid_until(school_year_of(period.start_date))
        if until >= on:
            return until
    return valid_until(school_year_of(on))
