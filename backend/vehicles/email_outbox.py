"""Retry outgoing mail that failed to send, until it goes out.

Every send used to be one-shot. When Brevo (Railway) or Gmail (campus) had a
blip, the approval mail carrying an owner's login, or the acknowledgement
carrying an applicant's receipt-upload link, was lost for good. The only trace
was a bell notice telling the CDSO to pass the details on by hand, and the
applicant was stuck until someone did.

config.email_backends.OutboxEmailBackend wraps the real transport. When a send
raises, the message is stored in tbl_email_outbox and a background thread
retries it: after 1, 2, 5, 10, 15 and 30 minutes, then every 30 minutes until
MAX_AGE. Callers see a successful send, because the message will still arrive.
The CDSO hears about it only if it stays stuck for DELAY_NOTICE_AFTER, if the
outbox gives up, and again when a delayed message finally gets through.

Not retried, because a retry would fail the same way forever:
  * every recipient refused (SMTPRecipientsRefused), and
  * the provider rejecting the request itself (HTTP 400/422: a malformed
    address, and on Brevo also an unverified sender).
A bad API key or a Brevo IP allowlist (401), a rate limit or an outage IS
retried. Once someone fixes the cause, the backlog goes out by itself.

Mail with a deadline, like a password-reset link, is sent inside
`expires_in(seconds)` and is not retried past it. Delivering a dead link is
worse than delivering nothing.

campus and Railway share one database, so whichever server is up and able to
send claims a queued row. Claims are leases (`next_attempt_at` pushed LEASE
ahead under SKIP LOCKED), so two servers never send the same row at once, and a
server that dies mid-send only delays the row by one lease.
"""
from __future__ import annotations

import base64
import contextlib
import contextvars
import logging
import os
import smtplib
import sys
import threading
from datetime import timedelta
from email import encoders
from email.mime.base import MIMEBase

from django.conf import settings
from django.db import transaction
from django.utils import timezone

log = logging.getLogger(__name__)

# Seconds to wait after the Nth failed attempt; the last value repeats. The
# early steps catch a blip, and the 30-minute tail rides out an outage without
# hammering the provider (Brevo's free tier is 300 sends a day).
RETRY_DELAYS = (60, 120, 300, 600, 900, 1800)
# How long a send may hold its claim. Brevo's own in-request retries can take
# about a minute at EMAIL_TIMEOUT=10, so this is comfortably longer.
LEASE = timedelta(minutes=5)
# Raise one bell notice once a message has been stuck this long.
DELAY_NOTICE_AFTER = timedelta(minutes=30)
# Given-up rows are kept this long for `retry_emails --include-failed`, then
# deleted. They can hold a temporary password, so they do not stay forever.
FAILED_RETENTION = timedelta(days=14)
# When nothing is due, the worker still checks the shared table this often, to
# pick up rows the other server queued. Kept above Neon's 5-minute autosuspend
# so an idle outbox does not keep the database awake.
IDLE_POLL_SECONDS = 900
BATCH = 20


def max_age() -> timedelta:
    """How long to keep retrying a message that has no deadline of its own."""
    return timedelta(hours=getattr(settings, 'EMAIL_OUTBOX_MAX_AGE_HOURS', 72))


# ── per-send deadline ────────────────────────────────────────────────────────
_deadline_seconds = contextvars.ContextVar('email_outbox_deadline', default=None)


@contextlib.contextmanager
def expires_in(seconds):
    """Mail sent inside this block is not retried for longer than `seconds`.

    For messages that become useless after a while, such as a password-reset
    link that stops working after PASSWORD_RESET_TIMEOUT.
    """
    token = _deadline_seconds.set(int(seconds))
    try:
        yield
    finally:
        _deadline_seconds.reset(token)


# ── EmailMessage <-> JSON ────────────────────────────────────────────────────
def serialize(message) -> dict:
    """Everything needed to rebuild `message`, as JSON. Attachments are base64."""
    from config.email_backends import _HttpApiEmailBackend

    attachments = []
    for raw in message.attachments:
        decoded = _HttpApiEmailBackend._decode_attachment(raw)
        if not decoded:
            continue
        filename, content, mimetype, content_id = decoded
        attachments.append({
            'filename': filename,
            'content': base64.b64encode(content).decode('ascii'),
            'mimetype': mimetype or 'application/octet-stream',
            'content_id': content_id,
        })

    return {
        'subject': message.subject,
        'body': message.body,
        'from_email': message.from_email,
        'to': list(message.to),
        'cc': list(message.cc),
        'bcc': list(message.bcc),
        'reply_to': list(message.reply_to),
        'headers': dict(message.extra_headers or {}),
        'content_subtype': message.content_subtype,
        'alternatives': [[content, mimetype]
                         for content, mimetype in getattr(message, 'alternatives', None) or []],
        'attachments': attachments,
        'hard_deadline': _deadline_seconds.get() is not None,
    }


def deserialize(data: dict):
    """Rebuild the EmailMultiAlternatives that serialize() was given."""
    from django.core.mail import EmailMultiAlternatives

    msg = EmailMultiAlternatives(
        subject=data.get('subject', ''),
        body=data.get('body', ''),
        from_email=data.get('from_email'),
        to=data.get('to') or [],
        cc=data.get('cc') or [],
        bcc=data.get('bcc') or [],
        reply_to=data.get('reply_to') or [],
        headers=data.get('headers') or {},
    )
    msg.content_subtype = data.get('content_subtype') or 'plain'
    for content, mimetype in data.get('alternatives') or []:
        msg.attach_alternative(content, mimetype)
    for att in data.get('attachments') or []:
        content = base64.b64decode(att['content'])
        if att.get('content_id'):
            # An inline image: rebuilt as a MIME part so its Content-ID survives.
            maintype, _, subtype = att['mimetype'].partition('/')
            part = MIMEBase(maintype, subtype or 'octet-stream')
            part.set_payload(content)
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', 'inline', filename=att['filename'])
            part.add_header('Content-ID', f"<{att['content_id']}>")
            msg.attach(part)
        else:
            msg.attach(att['filename'], content, att['mimetype'])
    return msg


# ── failure classification ───────────────────────────────────────────────────
def is_permanent(exc) -> bool:
    """True for failures that a retry would only repeat."""
    from config.email_backends import PermanentEmailError
    return isinstance(exc, (smtplib.SMTPRecipientsRefused, PermanentEmailError))


def _describe(exc) -> str:
    return f'{type(exc).__name__}: {exc}'[:1000]


def _delay_after(attempts: int) -> timedelta:
    return timedelta(seconds=RETRY_DELAYS[min(attempts, len(RETRY_DELAYS)) - 1])


# ── queueing (called by OutboxEmailBackend) ──────────────────────────────────
def queue(message, exc) -> bool:
    """Store a message whose send just failed. Returns False if it could not be
    stored (e.g. the table is not migrated yet), so the caller can fall back to
    raising the original error."""
    from .models import EmailOutbox

    now = timezone.now()
    deadline = _deadline_seconds.get()
    expires_at = now + (timedelta(seconds=deadline) if deadline is not None else max_age())
    if expires_at <= now + _delay_after(1):
        return False   # it would expire before the first retry; report the failure now

    try:
        # A savepoint: the caller may be inside its own transaction, and a
        # failed INSERT must not poison it.
        with transaction.atomic():
            row = EmailOutbox.objects.create(
                subject=(message.subject or '')[:255],
                recipients=', '.join(message.recipients())[:500],
                message=serialize(message),
                last_error=_describe(exc),
                next_attempt_at=now + _delay_after(1),
                expires_at=expires_at,
            )
    except Exception:
        log.exception('[email-outbox] could not queue %r for retry', message.subject)
        return False

    log.warning('[email-outbox] send of %r to %s failed (%s); queued as #%s for retry',
                message.subject, row.recipients, row.last_error, row.pk)
    wake()
    return True


# ── the retry pass ───────────────────────────────────────────────────────────
def _transport():
    from django.core.mail import get_connection
    return get_connection(settings.EMAIL_TRANSPORT_BACKEND, fail_silently=False)


def _notify(event, title, message, severity):
    from accounts.notifications import notify
    notify('registration', event, title, message, severity=severity)


def _give_up(row, reason):
    from .models import EmailOutbox
    row.status = EmailOutbox.Status.FAILED
    row.save(update_fields=['status', 'attempts', 'last_error', 'next_attempt_at'])
    log.error('[email-outbox] gave up on #%s %r to %s: %s',
              row.pk, row.subject, row.recipients, reason)
    _notify('email_failed', f'Email not delivered — {row.subject}'[:200],
            f'"{row.subject}" to {row.recipients} could not be delivered after '
            f'{row.attempts} attempt(s): {reason}. Pass the information on by hand, '
            f'or run `manage.py retry_emails --include-failed` once mail works again.',
            'critical')


def _attempt(row, now):
    """Try one claimed row. Returns 'sent', 'retrying' or 'failed'."""
    if row.expires_at <= now:
        _give_up(row, f'still undeliverable when its retry window closed ({row.last_error})')
        return 'failed'

    try:
        message = deserialize(row.message)
    except Exception as exc:   # noqa: BLE001 — a corrupt row must not stall the queue
        row.last_error = _describe(exc)
        _give_up(row, f'the stored message could not be rebuilt ({row.last_error})')
        return 'failed'

    try:
        _transport().send_messages([message])
    except Exception as exc:   # noqa: BLE001
        row.attempts += 1
        row.last_error = _describe(exc)
        if is_permanent(exc):
            _give_up(row, row.last_error)
            return 'failed'
        # Never sleep past the deadline, so a give-up happens on time.
        row.next_attempt_at = min(now + _delay_after(row.attempts), row.expires_at)
        fields = ['attempts', 'last_error', 'next_attempt_at']
        if not row.delay_notified and now - row.created_at >= DELAY_NOTICE_AFTER:
            row.delay_notified = True
            fields.append('delay_notified')
            _notify('email_delayed', f'Email delayed — {row.subject}'[:200],
                    f'"{row.subject}" to {row.recipients} has not gone out yet '
                    f'({row.attempts} attempts). It is retried automatically every '
                    f'30 minutes. Last error: {row.last_error}',
                    'warning')
        row.save(update_fields=fields)
        log.warning('[email-outbox] #%s attempt %d failed (%s); next try %s',
                    row.pk, row.attempts, row.last_error, row.next_attempt_at)
        return 'retrying'

    # Delivered (or the provider had nothing to deliver it to). Either way the
    # row is finished, and it may hold a temporary password, so it goes.
    if row.delay_notified:
        _notify('email_delivered_late', f'Delayed email delivered — {row.subject}'[:200],
                f'"{row.subject}" to {row.recipients} went out on attempt '
                f'{row.attempts + 1} after an earlier failure.', 'info')
    log.info('[email-outbox] #%s %r to %s delivered on attempt %d',
             row.pk, row.subject, row.recipients, row.attempts + 1)
    row.delete()
    return 'sent'


def run_due() -> dict:
    """Retry every queued message that is due. Safe to call from anywhere, any
    number of processes at once. Returns counts plus `next_in`, the seconds
    until the next row is due (capped at IDLE_POLL_SECONDS)."""
    from .models import EmailOutbox

    now = timezone.now()
    outcome = {'sent': 0, 'retrying': 0, 'failed': 0}

    EmailOutbox.objects.filter(status=EmailOutbox.Status.FAILED,
                               created_at__lt=now - FAILED_RETENTION).delete()

    while True:
        # Claim a batch: push each row's next_attempt_at a lease ahead inside
        # the locking transaction, then send outside it, so no network call ever
        # holds a row lock and a dead server's claim simply lapses.
        with transaction.atomic():
            rows = list(EmailOutbox.objects.select_for_update(skip_locked=True)
                        .filter(status=EmailOutbox.Status.PENDING, next_attempt_at__lte=now)
                        .order_by('next_attempt_at')[:BATCH])
            ids = [r.pk for r in rows]
            EmailOutbox.objects.filter(pk__in=ids).update(next_attempt_at=now + LEASE)
        if not rows:
            break
        for row in rows:
            outcome[_attempt(row, now)] += 1
        if len(rows) < BATCH:
            break

    upcoming = (EmailOutbox.objects.filter(status=EmailOutbox.Status.PENDING)
                .order_by('next_attempt_at').values_list('next_attempt_at', flat=True).first())
    wait = IDLE_POLL_SECONDS
    if upcoming is not None:
        wait = min(wait, max(5, (upcoming - timezone.now()).total_seconds()))
    outcome['next_in'] = wait
    return outcome


# ── the background worker ────────────────────────────────────────────────────
_started = False
_lock = threading.Lock()
_wake = threading.Event()


def wake():
    """Have the worker recompute its schedule now (a row was just queued)."""
    _wake.set()


def _loop():
    from django.db import close_old_connections

    while True:
        wait = IDLE_POLL_SECONDS
        try:
            close_old_connections()
            outcome = run_due()
            wait = outcome.pop('next_in')
            if any(outcome.values()):
                log.info('[email-outbox] pass: %s', outcome)
        except Exception:   # noqa: BLE001 — includes "table not migrated yet"
            log.exception('[email-outbox] pass failed; will retry')
        finally:
            close_old_connections()
        _wake.wait(wait)
        _wake.clear()


def start():
    """Start the retry thread once per server process. Same guard as the
    daily scheduler: migrations, tests, shells and one-shot commands never send."""
    global _started

    if os.getenv('DISABLE_EMAIL_OUTBOX', '').lower() in ('1', 'true', 'yes'):
        log.info('[email-outbox] worker disabled by DISABLE_EMAIL_OUTBOX')
        return

    argv = ' '.join(sys.argv)
    is_server = (
        'daphne' in argv or 'uvicorn' in argv or 'gunicorn' in argv
        or 'runserver' in argv
    )
    if not is_server:
        return

    # runserver's autoreloader runs the app twice; only the reloaded child
    # (RUN_MAIN=true) should own the thread.
    if 'runserver' in argv and os.environ.get('RUN_MAIN') != 'true':
        return

    with _lock:
        if _started:
            return
        _started = True

    threading.Thread(target=_loop, name='email-outbox', daemon=True).start()
    log.info('[email-outbox] started — retrying failed mail via %s',
             settings.EMAIL_TRANSPORT_BACKEND)
