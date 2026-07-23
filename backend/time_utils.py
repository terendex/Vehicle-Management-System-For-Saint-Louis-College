"""Local-calendar-day helpers for index-friendly timestamp filtering.

Django's ``__date`` lookup compiles to ``(col AT TIME ZONE 'Asia/Manila')::date``.
That is a function call evaluated per row, so no plain B-tree index on the
timestamp column can serve it — ``scanned_at__date=today`` always degrades to a
sequential scan over the whole table, however many indexes exist.

Converting the same question into a half-open range (``>= start`` and
``< next_midnight``) compares the raw column, which an index answers with a
seek. The results are identical because the range is derived in the project's
local timezone, exactly like the ``__date`` lookup was.

Asia/Manila has no DST, so local midnight is unambiguous.
"""

from datetime import date, datetime, time, timedelta

from django.utils import timezone


def day_start(day):
    """First instant of local calendar day ``day``, as an aware datetime."""
    return timezone.make_aware(
        datetime.combine(day, time.min), timezone.get_current_timezone()
    )


def day_end(day):
    """First instant of the day *after* ``day`` — the exclusive upper bound."""
    return day_start(day) + timedelta(days=1)


def day_range(day):
    """``(start, end)`` half-open bounds covering local calendar day ``day``.

    Use as ``filter(ts__gte=start, ts__lt=end)`` in place of ``ts__date=day``.
    """
    start = day_start(day)
    return start, start + timedelta(days=1)


def parse_local_date(value):
    """Coerce ``value`` to a ``date``; return None if it isn't a usable date.

    Accepts a ``date``, a ``datetime``, or a ``'YYYY-MM-DD'`` string (the format
    the report/filter query params use).
    """
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value).strip(), '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def filter_local_date_range(qs, field, date_from=None, date_to=None):
    """Filter ``qs`` on timestamp ``field`` to the inclusive local-date range.

    Drop-in replacement for ``field__date__gte`` / ``field__date__lte`` that an
    index can actually serve. Both bounds are optional, and unparseable values
    are ignored rather than raising — the ``__date`` form let a bad query param
    surface as a ValidationError all the way out to a 500.
    """
    start = parse_local_date(date_from)
    if start is not None:
        qs = qs.filter(**{f'{field}__gte': day_start(start)})

    end = parse_local_date(date_to)
    if end is not None:
        # Exclusive upper bound of the next day keeps all of `date_to` included.
        qs = qs.filter(**{f'{field}__lt': day_end(end)})

    return qs
