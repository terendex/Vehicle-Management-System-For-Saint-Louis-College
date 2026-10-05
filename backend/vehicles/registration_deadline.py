"""The 3-working-day payment deadline on online registrations.

The rule lives on the model (VehicleRegistration.payment_deadline and friends);
this module is what acts on it:

  * expire_overdue()    moves overdue applications to EXPIRED (their working
                        days ran out, or the registration period they were
                        filed in closed first), mails each
                        applicant, and tells the CDSO. Run hourly by the
                        in-process scheduler, on every submission (so an
                        abandoned application never blocks a fresh one), and
                        lazily when somebody opens an overdue application's link.
  * remind_due()        mails one reminder to each applicant whose deadline is
                        under a day away. Run hourly beside expire_overdue.
  * deadline_payload()  the deadline as every public screen receives it.

Campus and Railway share one database, so either server may run a sweep at any
moment. Each sweep locks the rows it takes (SKIP LOCKED), so two servers never
expire, or mail, the same application twice, and a receipt upload that is
holding its row wins over a sweep that arrives at the same instant.
Reminders are claimed in the scheduler's job ledger instead, one row per
application, so neither server ever sends the same reminder twice.
"""
from __future__ import annotations

import logging

from django.db import IntegrityError, transaction
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
                'payment_window_days': registration.PAYMENT_WINDOW_DAYS}
    now = now or timezone.now()
    return {
        'payment_deadline': deadline.isoformat(),
        'payment_deadline_display': format_deadline(deadline),
        'payment_seconds_left': max(0, int((deadline - now).total_seconds())),
        'payment_window_days': registration.PAYMENT_WINDOW_DAYS,
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
        # Which of the two deadlines ran out decides the reason the applicant
        # is given: their working days, or the registration period closing.
        # The deadline they were told is the earlier of the two; compared
        # with each other, not with `now`, so a late sweep (both passed by
        # then) still names the one that applied, as the email and the dead
        # link do.
        periods = VehicleRegistration.registration_periods()
        for row in rows:
            row.rejection_reason = (
                VehicleRegistration.EXPIRED_CLOSED_REASON
                if row.payment_deadline(periods) < row.working_day_deadline()
                else VehicleRegistration.EXPIRED_UNPAID_REASON)
        # One UPDATE per reason, filtered again on the status it expects, so a
        # row the CDSO decided between the SELECT and here is left as they
        # decided it.
        for reason in {row.rejection_reason for row in rows}:
            VehicleRegistration.objects.filter(
                pk__in=[r.pk for r in rows if r.rejection_reason == reason],
                status=VehicleRegistration.Status.PENDING,
                payment_status=VehicleRegistration.PaymentStatus.UNPAID,
            ).update(status=VehicleRegistration.Status.EXPIRED, rejection_reason=reason)

    for row in rows:
        row.status = VehicleRegistration.Status.EXPIRED

    _announce(rows)
    log.info("[registration-deadline] expired %d unpaid application(s): %s",
             len(rows), ', '.join(f'REG-{r.pk:06d}' for r in rows))
    return rows


# Ledger key prefix for reminder claims: "payment_reminder:<pk>".
REMINDER_JOB = 'payment_reminder'


def remind_due(now=None) -> list:
    """Mail one reminder to each applicant whose deadline is under a day away.

    Returns the rows a reminder was handed off for. Idempotent: the claim is a
    DailyJobRun row keyed by the application (run_date is the deadline's date),
    so the hourly sweep, a second server, or a restart never sends it twice.
    It needs no column on the registration, so there is no migration to
    coordinate between Railway and the campus clone.

    Nothing is sent once the deadline has passed. The grace hour is never
    advertised, and a reminder that lands after the stated deadline would only
    contradict the expiry notice that follows it.
    """
    from .email_utils import send_in_background, send_payment_reminder_email
    from .models import DailyJobRun, VehicleRegistration

    R = VehicleRegistration
    now = now or timezone.now()
    lead = R.PAYMENT_REMINDER_LEAD
    # Every application still on the clock; the exact window is checked per
    # row below. No created_at cut: a registration period closing can bring a
    # deadline forward to any time after submission. Unpaid online
    # applications are only ever a handful of rows.
    candidates = R.objects.filter(
        status=R.Status.PENDING,
        payment_status=R.PaymentStatus.UNPAID,
        source=R.Source.PUBLIC,
    )

    sent = []
    periods = R.registration_periods()
    for row in candidates:
        deadline = row.payment_deadline(periods)
        if deadline is None or not (deadline - lead <= now < deadline):
            continue
        try:
            with transaction.atomic():
                claim = DailyJobRun.objects.create(
                    job=f'{REMINDER_JOB}:{row.pk}',
                    run_date=timezone.localdate(deadline),
                    finished_at=now, result='reminder sent')
        except IntegrityError:
            continue                                    # already reminded

        def release(claim_pk=claim.pk):
            # The mail could not even be built or stored. Drop the claim so the
            # next hourly pass tries again while there is still time.
            DailyJobRun.objects.filter(pk=claim_pk).delete()

        try:
            send_in_background(send_payment_reminder_email, row, on_failure=release)
        except Exception:                               # noqa: BLE001
            log.exception("[registration-deadline] could not send the reminder for REG-%06d", row.pk)
            release()
            continue
        sent.append(row)

    if sent:
        log.info("[registration-deadline] reminded %d applicant(s): %s",
                 len(sent), ', '.join(f'REG-{r.pk:06d}' for r in sent))
    return sent


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

    from .models import VehicleRegistration
    names = ', '.join(f'{r.full_name} (REG-{r.pk:06d})' for r in rows[:5])
    more = f' and {len(rows) - 5} more' if len(rows) > 5 else ''
    closed = all(r.rejection_reason == VehicleRegistration.EXPIRED_CLOSED_REASON for r in rows)
    why = ('Registration closed before they paid' if closed
           else 'Not paid within 3 working days of applying, or before registration closed')
    notify(
        'registration', 'registration_expired',
        f'{len(rows)} unpaid application(s) expired',
        f'{why}: {names}{more}. Their plates and '
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
