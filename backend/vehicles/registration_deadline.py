"""The 3-day payment deadline on online registrations.

The rule lives on the model (VehicleRegistration.payment_deadline and friends);
this module is what acts on it:

  * expire_overdue()    moves overdue applications to EXPIRED, mails each
                        applicant, and tells the CDSO. Run hourly by the
                        in-process scheduler, on every submission (so an
                        abandoned application never blocks a fresh one), and
                        lazily when somebody opens an overdue application's link.
  * deadline_payload()  the deadline as every public screen receives it.

Campus and Railway share one database, so either server may run a sweep at any
moment. Each sweep locks the rows it takes (SKIP LOCKED), so two servers never
expire, or mail, the same application twice, and a receipt upload that is
holding its row wins over a sweep that arrives at the same instant.
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

log = logging.getLogger(__name__)


def format_deadline(moment) -> str:
    """'Sunday, October 4, 2026 at 3:05 PM', in campus time.

    Formatted on the server, never by the browser: Railway's clock is UTC, an
    applicant's phone may be set to anything, and the time an applicant is told
    has to be the one the sweep enforces.
    """
    local = timezone.localtime(moment)
    hour = local.hour % 12 or 12
    # Built by hand: Windows' strftime (the campus server) has no %-d / %-I.
    return f"{local:%A, %B} {local.day}, {local.year} at {hour}:{local:%M} {local:%p}"


def deadline_payload(registration, now=None) -> dict:
    """The deadline fields every public endpoint returns, from one place.

    `payment_seconds_left` is counted on the server, so a countdown built from
    it is right even when the device's own clock is not. None throughout when
    no deadline applies (exempt, already paid, walk-in).
    """
    deadline = registration.payment_deadline()
    if deadline is None or not registration.on_payment_clock():
        return {'payment_deadline': None, 'payment_deadline_display': None,
                'payment_seconds_left': None,
                'payment_window_days': registration.PAYMENT_WINDOW.days}
    now = now or timezone.now()
    return {
        'payment_deadline': deadline.isoformat(),
        'payment_deadline_display': format_deadline(deadline),
        'payment_seconds_left': max(0, int((deadline - now).total_seconds())),
        'payment_window_days': registration.PAYMENT_WINDOW.days,
    }


def expire_overdue(now=None, pk=None) -> list:
    """Expire every application whose payment deadline (plus grace) has passed.

    `pk` limits the sweep to one row — the lazy check a payment or edit link
    makes for itself. Returns the rows expired. Idempotent: an expired row no
    longer matches, so a re-run finds nothing.

    EXPIRED is the status that releases a registration's hold on its plate,
    email, licence and schedule slot (the uniqueness constraints and the slot
    counts only see pending/accepted), so once this commits the applicant — or
    anybody else — can register that vehicle again.
    """
    from .models import VehicleRegistration

    now = now or timezone.now()
    qs = VehicleRegistration.objects.filter(VehicleRegistration.overdue_q(now))
    if pk is not None:
        qs = qs.filter(pk=pk)

    with transaction.atomic():
        rows = list(qs.select_for_update(skip_locked=True))
        if not rows:
            return []
        # One UPDATE, filtered again on the status it expects, so a row the
        # CDSO decided between the SELECT and here is left as they decided it.
        VehicleRegistration.objects.filter(
            pk__in=[r.pk for r in rows],
            status=VehicleRegistration.Status.PENDING,
            payment_status=VehicleRegistration.PaymentStatus.UNPAID,
        ).update(status=VehicleRegistration.Status.EXPIRED,
                 rejection_reason=VehicleRegistration.EXPIRED_UNPAID_REASON)

    for row in rows:
        row.status = VehicleRegistration.Status.EXPIRED
        row.rejection_reason = VehicleRegistration.EXPIRED_UNPAID_REASON

    _announce(rows)
    log.info("[registration-deadline] expired %d unpaid application(s): %s",
             len(rows), ', '.join(f'REG-{r.pk:06d}' for r in rows))
    return rows


def _announce(rows):
    """Mail each applicant, then tell the CDSO once for the whole batch.

    Nothing here may undo the expiry, which has already committed: every step
    catches its own failure. The mail goes through send_in_background and the
    outbox, so a slow or dropped connection to the mail provider delays the
    notice rather than losing it, and never holds up the request or the sweep.
    """
    from accounts.notifications import notify
    from .email_utils import send_in_background, send_registration_expired_email

    for row in rows:
        try:
            send_in_background(send_registration_expired_email, row)
        except Exception:                               # noqa: BLE001
            log.exception("[registration-deadline] could not send the expiry notice for REG-%06d", row.pk)

    names = ', '.join(f'{r.full_name} (REG-{r.pk:06d})' for r in rows[:5])
    more = f' and {len(rows) - 5} more' if len(rows) > 5 else ''
    notify(
        'registration', 'registration_expired',
        f'{len(rows)} unpaid application(s) expired',
        f'Not paid within 3 days of applying: {names}{more}. Their plates and '
        f'schedule slots are free again. Find them under the Expired filter.',
        severity='info', link='/admin/vehicles',
    )

    # .update() fires no post_save, so the live-refresh signal never sees this;
    # an open review queue would keep showing the rows as pending.
    try:
        from realtime.broadcast import broadcast_change
        broadcast_change('vehicleregistration', 'changed')
    except Exception:                                   # noqa: BLE001
        log.exception("[registration-deadline] live refresh broadcast failed")
