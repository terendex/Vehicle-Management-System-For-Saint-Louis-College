"""Diagnose outgoing mail on whichever half this is running on.

Gmail works from the campus machine and fails from Railway, and the two look
identical from the application side: an approval email simply never arrives.
The causes are not identical, though, and they need different fixes:

  * credentials missing        - the variables were never set on this host
  * TCP connect times out      - the platform blocks outbound SMTP (Railway
                                 blocks ports 25/465/587), so no port or
                                 password change will ever help; mail has to
                                 leave over HTTPS instead
  * connection refused / DNS   - wrong EMAIL_HOST or no egress at all
  * SMTP 535                   - reached Gmail fine, the app password is wrong
                                 (an ordinary account password always fails)

Django reports all of these as "the send failed", and the blocked-port case is
the deceptive one: it looks like a slow mail server until you time it. This
command times the raw TCP connect separately from the SMTP conversation so the
distinction is visible.

    python manage.py check_email                  # config + can we even connect
    python manage.py check_email --to me@x.com    # ...and send a real message

On Railway, run it against the deployed environment rather than locally -
the whole question is what that host's network allows:

    railway run python backend/manage.py check_email
"""
import socket
import smtplib
import time

from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.core.management.base import BaseCommand

SMTP_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
# Ports platforms block to deter spam. Railway blocks all three, which is the
# entire reason this command exists.
BLOCKED_HINT_PORTS = (25, 465, 587)

# The HTTPS backends, keyed by EMAIL_BACKEND. `sender_rule` is what the operator
# actually has to get right, and it differs per provider: Brevo authorises an
# individual address, Resend a whole domain.
HTTPS_BACKENDS = {
    'config.email_backends.BrevoEmailBackend': {
        'name': 'Brevo',
        'host': 'api.brevo.com',
        'key_setting': 'BREVO_API_KEY',
        'key_prefix': 'xkeysib-',
        'sender_rule': 'this exact address must be a verified sender in Brevo',
    },
    'config.email_backends.ResendEmailBackend': {
        'name': 'Resend',
        'host': 'api.resend.com',
        'key_setting': 'RESEND_API_KEY',
        'key_prefix': 're_',
        'sender_rule': 'its domain must be verified in Resend',
    },
}


class Command(BaseCommand):
    help = "Report why outgoing mail does or does not work from this host."

    def add_arguments(self, parser):
        parser.add_argument('--to', help="Address to send a real test message to.")
        parser.add_argument('--timeout', type=int, default=15,
                            help="Seconds to wait for the TCP connect (default 15). "
                                 "Longer than EMAIL_TIMEOUT on purpose, so a slow "
                                 "server is not mistaken for a blocked port.")

    def handle(self, *args, **opts):
        self._report_config()

        provider = HTTPS_BACKENDS.get(settings.EMAIL_BACKEND)
        if provider:
            return self._check_https(provider, opts)

        if settings.EMAIL_BACKEND != SMTP_BACKEND:
            self.stdout.write(self.style.WARNING(
                f"\nEMAIL_BACKEND is neither SMTP nor an HTTPS provider, so this command\n"
                f"  cannot probe the transport. Mail is handled by {settings.EMAIL_BACKEND}."))
            if opts['to']:
                self._send(opts['to'])
            return

        if not settings.EMAIL_HOST_USER or not settings.EMAIL_HOST_PASSWORD:
            raise SystemExit(
                "\nNO CREDENTIALS ON THIS HOST.\n"
                "  EMAIL_HOST_USER / EMAIL_HOST_PASSWORD are unset here. If mail works\n"
                "  on the other half, its .env has them and this environment does not.\n"
                "  On Railway these are service variables, not files - setting them in\n"
                "  backend/.env locally does nothing for the deployed container."
            )

        self._probe_tcp(settings.EMAIL_HOST, settings.EMAIL_PORT, opts['timeout'])

        if opts['to']:
            self._send(opts['to'])
        else:
            self.stdout.write("\nConnect succeeded. Re-run with --to <address> to send "
                              "a real message and check the credentials too.")

    # ── what this host is configured to do ───────────────────────────────
    def _report_config(self):
        self.stdout.write(f"backend       : {settings.EMAIL_BACKEND}")

        provider = HTTPS_BACKENDS.get(settings.EMAIL_BACKEND)
        if provider:
            key = getattr(settings, provider['key_setting'], '') or ''
            prefix = provider['key_prefix']
            self.stdout.write(f"transport     : HTTPS to {provider['host']} (SMTP not used)")
            # Length and prefix only, never the key: enough to catch the two
            # common copy errors (empty, or a key from the other provider)
            # without printing a credential to a shared terminal.
            self.stdout.write("api key       : "
                              + (f"{len(key)} chars, starts {key[:len(prefix)]!r}"
                                 if key else "(unset)")
                              + ("" if not key or key.startswith(prefix) else
                                 f"  <- {provider['name']} keys start {prefix!r}; "
                                 f"this looks like another provider's"))
            self.stdout.write(f"from          : {settings.DEFAULT_FROM_EMAIL or '(unset)'}"
                              f"   ({provider['sender_rule']})")
            self.stdout.write(f"send timeout  : {settings.EMAIL_TIMEOUT}s")
            return

        pwd = settings.EMAIL_HOST_PASSWORD or ''
        mode = ('SSL' if settings.EMAIL_USE_SSL else
                'STARTTLS' if settings.EMAIL_USE_TLS else 'plaintext')
        self.stdout.write(f"host:port     : {settings.EMAIL_HOST}:{settings.EMAIL_PORT} ({mode})")
        self.stdout.write(f"user          : {settings.EMAIL_HOST_USER or '(unset)'}")
        # Length only - never the value. Gmail app passwords are exactly 16
        # characters, so the length alone catches the commonest mistake.
        self.stdout.write(f"password      : {len(pwd)} chars"
                          + ("" if not pwd else
                             "" if len(pwd) == 16 else
                             "  <- Gmail app passwords are 16; this may be the "
                             "account password, which Gmail always rejects"))
        self.stdout.write(f"from          : {settings.DEFAULT_FROM_EMAIL or '(unset)'}")
        self.stdout.write(f"send timeout  : {settings.EMAIL_TIMEOUT}s")

    # ── the HTTPS path Railway actually uses ─────────────────────────────
    def _check_https(self, provider, opts):
        """Same three questions as the SMTP path, asked of an HTTPS transport:
        are the credentials here, can this host reach the provider, and does a
        real send succeed."""
        name = provider['name']
        if not getattr(settings, provider['key_setting'], ''):
            raise SystemExit(
                f"\nNO API KEY ON THIS HOST.\n"
                f"  EMAIL_BACKEND selects {name} but {provider['key_setting']} is unset,\n"
                f"  so every send raises instead of going out. On Railway this is a\n"
                f"  service variable - editing backend/.env locally does nothing for the\n"
                f"  deployed container."
            )

        if not settings.DEFAULT_FROM_EMAIL:
            raise SystemExit(
                f"\nNO SENDER ADDRESS.\n"
                f"  DEFAULT_FROM_EMAIL is unset. {name} has no account address to fall\n"
                f"  back on the way Gmail does, so the sender must be given explicitly,\n"
                f"  and {provider['sender_rule']}."
            )

        # 443 is the point of these backends, so a timeout here means something
        # far worse than the SMTP block - the container has no egress at all.
        self._probe_tcp(provider['host'], 443, opts['timeout'])

        if opts['to']:
            self._send(opts['to'])
        else:
            self.stdout.write(
                f"\n{name} is reachable. Re-run with --to <address> to send a real "
                f"message - only that checks the key and the\nsender, which is where "
                f"this setup usually fails first. Use an address that is NOT your own "
                f"provider account:\nsending to yourself can succeed while sending to "
                f"a student still fails.")

    # ── can a TCP connection even be opened? ─────────────────────────────
    def _probe_tcp(self, host, port, timeout):
        """Open a bare socket to the mail host, timed.

        Deliberately separate from the Django send: a platform-level SMTP block
        drops the packets rather than refusing them, so the socket hangs until
        it times out. Timing that on its own is what separates "the port is
        blocked" from "the password is wrong" - through Django both surface as
        the same failed send.
        """
        self.stdout.write(f"\nconnecting to {host}:{port} (up to {timeout}s)...")
        started = time.monotonic()
        try:
            with socket.create_connection((host, port), timeout=timeout):
                pass
        except socket.timeout:
            elapsed = time.monotonic() - started
            extra = ""
            if port in BLOCKED_HINT_PORTS:
                extra = (f"\n  Port {port} is one of the ports cloud platforms block to deter\n"
                         f"  spam, and Railway blocks all of {BLOCKED_HINT_PORTS}. If this is\n"
                         f"  the Railway half, no combination of port, password or provider\n"
                         f"  SMTP settings will fix it - the mail has to leave over HTTPS\n"
                         f"  through a provider API instead.")
            raise SystemExit(
                f"\nCONNECT TIMED OUT after {elapsed:.1f}s.\n"
                f"  Nothing answered and nothing refused - the packets are being dropped,\n"
                f"  which is what an outbound SMTP block looks like.{extra}"
            )
        except socket.gaierror as exc:
            raise SystemExit(
                f"\nDNS LOOKUP FAILED for {host}: {exc}\n"
                f"  EMAIL_HOST is wrong, or this container has no outbound DNS."
            )
        except OSError as exc:
            elapsed = time.monotonic() - started
            raise SystemExit(
                f"\nCONNECT REFUSED after {elapsed:.1f}s: {exc}\n"
                f"  Something answered and said no. Usually the wrong port for the\n"
                f"  chosen mode - {host} expects 587 for STARTTLS, 465 for SSL."
            )

        reached = ("outbound SMTP is not blocked here" if port in BLOCKED_HINT_PORTS
                   else f"this host can reach {host}")
        self.stdout.write(self.style.SUCCESS(
            f"connect OK in {time.monotonic() - started:.1f}s - {reached}."))

    # ── the real thing, credentials included ─────────────────────────────
    def _send(self, to):
        self.stdout.write(f"\nsending a test message to {to}...")
        msg = EmailMessage(
            subject="SPVVS - mail transport test",
            body=("This is a test message from `manage.py check_email`.\n\n"
                  "If it arrived, outgoing mail works from the host that sent it."),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[to],
            # A fresh connection, not any pooled one, so the failure reported is
            # this attempt's.
            connection=get_connection(fail_silently=False),
        )
        try:
            msg.send(fail_silently=False)
        except smtplib.SMTPAuthenticationError as exc:
            raise SystemExit(
                f"\nAUTHENTICATION REJECTED: {exc}\n"
                f"  The network is fine - Gmail was reached and refused the login.\n"
                f"  Gmail needs an App Password (16 chars, 2-Step Verification must be\n"
                f"  on); the ordinary account password is always rejected."
            )
        except smtplib.SMTPSenderRefused as exc:
            raise SystemExit(
                f"\nSENDER REFUSED: {exc}\n"
                f"  DEFAULT_FROM_EMAIL ({settings.DEFAULT_FROM_EMAIL}) is not an address\n"
                f"  this account is allowed to send as. Gmail only allows its own address."
            )
        except Exception as exc:
            raise SystemExit(f"\nSEND FAILED: {type(exc).__name__}: {exc}")

        self.stdout.write(self.style.SUCCESS(
            f"sent - check {to}, including its spam folder."))
