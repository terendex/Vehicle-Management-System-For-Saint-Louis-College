"""Outgoing mail over HTTPS, for hosts where SMTP cannot leave the machine.

Railway blocks outbound SMTP on ports 25, 465 and 587 to deter spam, and blocks
it by dropping the packets rather than refusing them — so Gmail on port 587
works perfectly from the campus machine and hangs until EMAIL_TIMEOUT on
Railway, with nothing in the logs to tell the two apart. No port, password or
mail-provider change fixes that; the only way out of the container is HTTPS on
443, which the platform does allow.

This backend sends through Resend's HTTP API instead of speaking SMTP. It is a
drop-in for the SMTP backend: every existing `send_mail` and
`EmailMultiAlternatives` call — HTML alternatives, the approval email's PDF
attachment, the inline `cid:` evidence photo on violation emails — goes through
unchanged. Only the transport differs, so the campus half keeps using Gmail
SMTP and needs no configuration at all.

Enable it on Railway only:

    EMAIL_BACKEND=config.email_backends.ResendEmailBackend
    RESEND_API_KEY=re_xxxxxxxx
    DEFAULT_FROM_EMAIL=noreply@spvvs.slc-sflu.edu.ph

DEFAULT_FROM_EMAIL must be on a domain verified in the Resend dashboard —
unlike Gmail, the sending identity is not tied to the account's own address, and
Resend rejects any other domain outright. Until the school's DNS records exist,
`onboarding@resend.dev` works for testing and nothing else does.

Verify a deployment with `python manage.py check_email --to you@example.com`.
"""
import base64
import logging
import time

import requests
from django.core.mail.backends.base import BaseEmailBackend

log = logging.getLogger(__name__)

API_URL = 'https://api.resend.com/emails'
# Resend rate-limits to 2 requests/second by default. A CDSO approving a batch
# of registrations trips that easily, and a 429 that is not retried loses the
# owner their credentials email, so the burst is absorbed here rather than being
# reported as a send failure.
MAX_ATTEMPTS = 4
RETRY_BACKOFF = 1.0  # seconds; doubled per attempt


class ResendEmailBackend(BaseEmailBackend):
    """Django email backend that posts to the Resend API over HTTPS."""

    def __init__(self, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently, **kwargs)
        from django.conf import settings
        self.api_key = getattr(settings, 'RESEND_API_KEY', '') or ''
        # Reuse the SMTP knob rather than inventing a second one: it already
        # means "how long a stalled mail server may hold up the caller", and the
        # scan pipeline sends mail inline.
        self.timeout = getattr(settings, 'EMAIL_TIMEOUT', 10) or 10
        self.session = None

    # ── connection lifecycle ─────────────────────────────────────────────
    def open(self):
        if self.session is not None:
            return False
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        })
        return True

    def close(self):
        if self.session is not None:
            self.session.close()
            self.session = None

    def send_messages(self, email_messages):
        if not email_messages:
            return 0

        if not self.api_key:
            # Distinguished from a network failure on purpose: an unset key is a
            # deployment mistake, not an outage, and silently sending nothing is
            # how "approved but never emailed" happens.
            if not self.fail_silently:
                raise ValueError(
                    'RESEND_API_KEY is not set, so no mail can be sent. Set it on '
                    'this host, or set EMAIL_BACKEND back to the SMTP backend.'
                )
            log.error('RESEND_API_KEY is not set — dropping %d message(s).',
                      len(email_messages))
            return 0

        new_session = self.open()
        try:
            return sum(1 for m in email_messages if self._send(m))
        finally:
            if new_session:
                self.close()

    # ── one message ──────────────────────────────────────────────────────
    def _send(self, message):
        recipients = message.recipients()
        if not recipients:
            return False

        try:
            payload = self._payload(message)
        except Exception:
            if not self.fail_silently:
                raise
            log.exception('Could not build a Resend payload for %r', message.subject)
            return False

        delay = RETRY_BACKOFF
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = self.session.post(API_URL, json=payload, timeout=self.timeout)
            except requests.RequestException as exc:
                # The network itself failed. Retrying is worthwhile — but only
                # up to the attempt budget, so a hard outage cannot stall a scan.
                if attempt == MAX_ATTEMPTS:
                    if not self.fail_silently:
                        raise
                    log.exception('Resend unreachable after %d attempts', attempt)
                    return False
                log.warning('Resend request failed (%s), retrying in %.1fs', exc, delay)
            else:
                if resp.status_code < 300:
                    return True

                # 429 is a rate limit and 5xx is Resend's problem; both clear on
                # their own. Everything else (bad key, unverified domain,
                # malformed address) will fail identically forever, so it is
                # reported immediately rather than after four slow retries.
                retryable = resp.status_code == 429 or resp.status_code >= 500
                detail = self._error_detail(resp)
                if not retryable or attempt == MAX_ATTEMPTS:
                    if not self.fail_silently:
                        raise RuntimeError(
                            f'Resend rejected the message ({resp.status_code}): {detail}'
                        )
                    log.error('Resend rejected the message (%s): %s',
                              resp.status_code, detail)
                    return False

                # Honour Retry-After when Resend supplies one.
                try:
                    delay = max(delay, float(resp.headers.get('Retry-After', 0)))
                except (TypeError, ValueError):
                    pass
                log.warning('Resend returned %s (%s), retrying in %.1fs',
                            resp.status_code, detail, delay)

            time.sleep(delay)
            delay *= 2

        return False

    @staticmethod
    def _error_detail(resp):
        """Resend's JSON error message, falling back to the raw body."""
        try:
            body = resp.json()
        except ValueError:
            return (resp.text or '')[:300]
        if isinstance(body, dict):
            return body.get('message') or body.get('error') or str(body)[:300]
        return str(body)[:300]

    # ── Django EmailMessage → Resend JSON ────────────────────────────────
    def _payload(self, message):
        payload = {
            'from': message.from_email,
            'to': list(message.to),
            'subject': message.subject or '',
        }
        if message.cc:
            payload['cc'] = list(message.cc)
        if message.bcc:
            payload['bcc'] = list(message.bcc)
        if message.reply_to:
            payload['reply_to'] = list(message.reply_to)

        # `send_mail(html_message=...)` and `attach_alternative` both land in
        # `alternatives`; a message whose content_subtype is 'html' carries its
        # HTML in `body` instead. Resend needs at least one of html/text.
        body = message.body or ''
        if getattr(message, 'content_subtype', 'plain') == 'html':
            payload['html'] = body
        else:
            payload['text'] = body

        for content, mimetype in getattr(message, 'alternatives', None) or []:
            if mimetype == 'text/html':
                payload['html'] = content
            elif mimetype == 'text/plain':
                payload['text'] = content

        attachments = [a for a in (self._attachment(a) for a in message.attachments) if a]
        if attachments:
            payload['attachments'] = attachments
        return payload

    @staticmethod
    def _attachment(attachment):
        """Convert one Django attachment to Resend's `{filename, content}` form.

        Django allows two shapes here and both are used in this project: the
        approval email attaches a `(filename, bytes, mimetype)` tuple for the
        registration PDF, while violation emails attach a raw MIMEImage carrying
        a Content-ID so the evidence photo renders inline from `cid:evidence`.
        The MIMEBase branch preserves that id — dropping it would turn the inline
        photo into a bare attachment and leave a broken image in the body.
        """
        if isinstance(attachment, tuple):
            filename, content, mimetype = attachment
            content_id = None
        else:  # MIMEBase
            filename = attachment.get_filename()
            content = attachment.get_payload(decode=True)
            mimetype = attachment.get_content_type()
            content_id = (attachment.get('Content-ID') or '').strip('<>') or None

        if content is None:
            return None
        if isinstance(content, str):
            content = content.encode('utf-8')

        item = {
            'filename': filename or 'attachment',
            'content': base64.b64encode(content).decode('ascii'),
        }
        if mimetype:
            item['content_type'] = mimetype
        if content_id:
            item['content_id'] = content_id
        return item
