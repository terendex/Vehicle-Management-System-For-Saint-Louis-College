"""Outgoing mail over HTTPS, for hosts where SMTP cannot leave the machine.

Railway blocks outbound SMTP on ports 25, 465 and 587 to deter spam, and blocks
it by dropping the packets rather than refusing them — so Gmail on port 587
works perfectly from the campus machine and hangs until EMAIL_TIMEOUT on
Railway, with nothing in the logs to tell the two apart. No port, password or
mail-provider change fixes that; the only way out of the container is HTTPS on
443, which the platform does allow.

Two providers are implemented because they fail on different things:

  BrevoEmailBackend   verifies a single *sender address*, so a plain Gmail
                      address can send to anyone with no domain involved. This
                      is what the Railway half uses — it needs nothing from the
                      school's DNS, and it keeps the sender identical to the
                      campus half's, so recipients see one consistent address.

  ResendEmailBackend  requires a verified sending *domain*. Better deliverability
                      once `spvvs.slc-sflu.edu.ph` exists, but until then it will
                      only deliver to the Resend account owner, which is useless
                      for emailing students.

Both are drop-in replacements for the SMTP backend: every existing `send_mail`
and `EmailMultiAlternatives` call goes through unchanged, so the campus half
needs no configuration at all and keeps using Gmail SMTP.

Enable one on Railway only — setting EMAIL_BACKEND is the whole switch:

    EMAIL_BACKEND=config.email_backends.BrevoEmailBackend
    BREVO_API_KEY=xkeysib-…
    DEFAULT_FROM_EMAIL=SLC CDSO <the-verified-address@gmail.com>

Verify a deployment with `python manage.py check_email --to you@example.com`.
"""
import base64
import logging
import time
from email.utils import parseaddr

import requests
from django.core.mail.backends.base import BaseEmailBackend

log = logging.getLogger(__name__)

# Providers rate-limit, and a CDSO approving a batch of registrations trips that
# easily. A 429 that is not retried loses an owner their credentials email, so
# the burst is absorbed here rather than surfacing as a send failure.
MAX_ATTEMPTS = 4
RETRY_BACKOFF = 1.0  # seconds; doubled per attempt


class _HttpApiEmailBackend(BaseEmailBackend):
    """Shared machinery for sending Django email through a provider's HTTP API.

    Subclasses supply the endpoint, the auth header and the JSON shape; the
    session handling, retry policy and Django plumbing are identical between
    them and live here.
    """

    API_URL = ''
    PROVIDER = ''
    KEY_SETTING = ''        # settings attribute holding the API key
    KEY_PREFIX = ''         # expected key prefix, for the diagnostic only

    def __init__(self, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently, **kwargs)
        from django.conf import settings
        self.api_key = getattr(settings, self.KEY_SETTING, '') or ''
        # Reuse the SMTP knob rather than inventing a second one: it already
        # means "how long a stalled mail server may hold up the caller", and the
        # scan pipeline sends mail inline.
        self.timeout = getattr(settings, 'EMAIL_TIMEOUT', 10) or 10
        self.session = None

    # ── provider hooks ───────────────────────────────────────────────────
    def _auth_headers(self):
        raise NotImplementedError

    def _payload(self, message):
        raise NotImplementedError

    # ── connection lifecycle ─────────────────────────────────────────────
    def open(self):
        if self.session is not None:
            return False
        self.session = requests.Session()
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        headers.update(self._auth_headers())
        self.session.headers.update(headers)
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
                    f'{self.KEY_SETTING} is not set, so no mail can be sent. Set it '
                    f'on this host, or set EMAIL_BACKEND back to the SMTP backend.'
                )
            log.error('%s is not set — dropping %d message(s).',
                      self.KEY_SETTING, len(email_messages))
            return 0

        new_session = self.open()
        try:
            return sum(1 for m in email_messages if self._send(m))
        finally:
            if new_session:
                self.close()

    # ── one message ──────────────────────────────────────────────────────
    def _send(self, message):
        if not message.recipients():
            return False

        try:
            payload = self._payload(message)
        except Exception:
            if not self.fail_silently:
                raise
            log.exception('Could not build a %s payload for %r',
                          self.PROVIDER, message.subject)
            return False

        delay = RETRY_BACKOFF
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = self.session.post(self.API_URL, json=payload, timeout=self.timeout)
            except requests.RequestException as exc:
                # The network itself failed. Retrying is worthwhile — but only up
                # to the attempt budget, so a hard outage cannot stall a scan.
                if attempt == MAX_ATTEMPTS:
                    if not self.fail_silently:
                        raise
                    log.exception('%s unreachable after %d attempts',
                                  self.PROVIDER, attempt)
                    return False
                log.warning('%s request failed (%s), retrying in %.1fs',
                            self.PROVIDER, exc, delay)
            else:
                if resp.status_code < 300:
                    return True

                # 429 is a rate limit and 5xx is the provider's problem; both
                # clear on their own. Everything else (bad key, unverified
                # sender, malformed address) fails identically forever, so it is
                # reported at once rather than after four slow retries.
                retryable = resp.status_code == 429 or resp.status_code >= 500
                detail = self._error_detail(resp)
                if not retryable or attempt == MAX_ATTEMPTS:
                    if not self.fail_silently:
                        raise RuntimeError(
                            f'{self.PROVIDER} rejected the message '
                            f'({resp.status_code}): {detail}'
                        )
                    log.error('%s rejected the message (%s): %s',
                              self.PROVIDER, resp.status_code, detail)
                    return False

                try:
                    delay = max(delay, float(resp.headers.get('Retry-After', 0)))
                except (TypeError, ValueError):
                    pass
                log.warning('%s returned %s (%s), retrying in %.1fs',
                            self.PROVIDER, resp.status_code, detail, delay)

            time.sleep(delay)
            delay *= 2

        return False

    @staticmethod
    def _error_detail(resp):
        """The provider's JSON error message, falling back to the raw body.
        Both providers use a top-level `message`."""
        try:
            body = resp.json()
        except ValueError:
            return (resp.text or '')[:300]
        if isinstance(body, dict):
            return body.get('message') or body.get('error') or str(body)[:300]
        return str(body)[:300]

    # ── shared Django EmailMessage decoding ──────────────────────────────
    @staticmethod
    def _bodies(message):
        """Return (text, html) for a Django message.

        `send_mail(html_message=...)` and `attach_alternative` both land in
        `alternatives`; a message whose content_subtype is 'html' carries its
        HTML in `body` instead. Every provider needs at least one of the two.
        """
        text = html = None
        body = message.body or ''
        if getattr(message, 'content_subtype', 'plain') == 'html':
            html = body
        else:
            text = body
        for content, mimetype in getattr(message, 'alternatives', None) or []:
            if mimetype == 'text/html':
                html = content
            elif mimetype == 'text/plain':
                text = content
        return text, html

    @staticmethod
    def _decode_attachment(attachment):
        """Normalise one Django attachment to (filename, bytes, mimetype, cid).

        Django allows two shapes and both are used here: the approval email
        attaches a `(filename, bytes, mimetype)` tuple for the registration PDF,
        while violation emails attach a raw MIMEImage carrying a Content-ID so
        the evidence photo renders inline from `cid:evidence`.
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
        return filename or 'attachment', content, mimetype, content_id

    @staticmethod
    def _b64(content):
        return base64.b64encode(content).decode('ascii')


class BrevoEmailBackend(_HttpApiEmailBackend):
    """Send through Brevo's transactional email API.

    Brevo verifies an individual *sender address* rather than a domain, so the
    project's existing Gmail address can be authorised in the dashboard and then
    used to email arbitrary students — no DNS records, and nothing needed from
    SLC IT. That is the only reason this is the Railway default over Resend.

    Caveat: Brevo's attachment objects carry no Content-ID field, so an inline
    `cid:` image cannot be referenced from the HTML the way SMTP allows. Such
    attachments are still delivered, but as ordinary attachments — see
    `_payload` below.
    """

    API_URL = 'https://api.brevo.com/v3/smtp/email'
    PROVIDER = 'Brevo'
    KEY_SETTING = 'BREVO_API_KEY'
    KEY_PREFIX = 'xkeysib-'

    def _auth_headers(self):
        return {'api-key': self.api_key}

    def _payload(self, message):
        name, addr = parseaddr(message.from_email or '')
        sender = {'email': addr}
        if name:
            sender['name'] = name

        payload = {
            'sender': sender,
            'to': [self._contact(a) for a in message.to],
            'subject': message.subject or '',
        }
        if message.cc:
            payload['cc'] = [self._contact(a) for a in message.cc]
        if message.bcc:
            payload['bcc'] = [self._contact(a) for a in message.bcc]
        if message.reply_to:
            # Brevo takes a single object here, not a list.
            payload['replyTo'] = self._contact(message.reply_to[0])

        text, html = self._bodies(message)
        if html:
            payload['htmlContent'] = html
        if text:
            payload['textContent'] = text

        attachments = []
        for raw in message.attachments:
            decoded = self._decode_attachment(raw)
            if not decoded:
                continue
            filename, content, _mimetype, content_id = decoded
            if content_id:
                # Delivered, but it will not render inline: the HTML's
                # `cid:` reference has nothing to bind to. Logged rather than
                # dropped so the evidence photo still reaches the recipient,
                # and so the cause is findable if someone reports a broken
                # image in a violation email.
                log.warning(
                    'Brevo cannot inline attachment %r (Content-ID %r); sending it '
                    'as a normal attachment. Reference the image by its public URL '
                    'instead of cid: to have it render in the body.',
                    filename, content_id)
            attachments.append({'name': filename, 'content': self._b64(content)})
        if attachments:
            payload['attachment'] = attachments
        return payload

    @staticmethod
    def _contact(address):
        name, addr = parseaddr(address)
        return {'email': addr, 'name': name} if name else {'email': addr}


class ResendEmailBackend(_HttpApiEmailBackend):
    """Send through Resend's HTTP API.

    Resend will only send from a domain verified in its dashboard and rejects
    anything else with a 403 — unlike Gmail, the sending identity is not tied to
    the account's own address. `onboarding@resend.dev` needs no verification but
    delivers only to the Resend account owner, so it is a transport test and not
    a production sender.
    """

    API_URL = 'https://api.resend.com/emails'
    PROVIDER = 'Resend'
    KEY_SETTING = 'RESEND_API_KEY'
    KEY_PREFIX = 're_'

    def _auth_headers(self):
        return {'Authorization': f'Bearer {self.api_key}'}

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

        text, html = self._bodies(message)
        if html:
            payload['html'] = html
        if text:
            payload['text'] = text

        attachments = []
        for raw in message.attachments:
            decoded = self._decode_attachment(raw)
            if not decoded:
                continue
            filename, content, mimetype, content_id = decoded
            item = {'filename': filename, 'content': self._b64(content)}
            if mimetype:
                item['content_type'] = mimetype
            # Resend does carry Content-ID, so `cid:` images survive here.
            if content_id:
                item['content_id'] = content_id
            attachments.append(item)
        if attachments:
            payload['attachments'] = attachments
        return payload
