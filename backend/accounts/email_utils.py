"""Account-security emails: password changes, and blocked sign-in attempts.

Two different messages come out of the same event, because the first password
change means something the later ones do not. Accounts are created by the CDSO
with a temporary password and `must_change_password` set, so the first change is
the moment the account actually becomes the user's own — that one is a welcome.
Every change after it is a security notice: the one signal an owner has that
somebody else got into their account.

Neither may ever break the password change that triggered it. The user's new
password is already saved by the time these run, so a mail failure that raised
would show them an error for something that in fact succeeded — they would try
again with a password that is now the *current* one and be told it is wrong.
Both send with fail_silently and are wrapped by the caller.
"""
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

# These templates are f-strings, not Django templates, so nothing auto-escapes.
# A user's own full name reaches the HTML body verbatim otherwise.
from vehicles.email_utils import esc

log = logging.getLogger(__name__)

_BODY_STYLE = ('font-family: Arial, sans-serif; color: #1A1D2E; '
               'background-color: #F0F2F7; padding: 20px; margin: 0;')

_FOOTER = """
  <div style="background:#F8FAFC;border-top:1px solid #E2E6EE;padding:16px 32px;text-align:center;">
    <p style="font-size:12px;color:#7C80A3;margin:0;">Saint Louis College Smart Parking and Vehicle Verification System</p>
    <p style="font-size:11px;color:#B0B4C7;margin:4px 0 0;">This is an automated message. Please do not reply.</p>
  </div>
"""

# What each role can actually do, so the welcome is worth reading rather than
# generic. Keyed by User.Role values.
_ROLE_INTRO = {
    'vehicle_owner': (
        'Your vehicle pass account is now fully active. From your dashboard you can '
        'view your QR gate pass, check your entry history, and see any violations '
        'recorded against your vehicle.'),
    'security': (
        'Your security personnel account is now fully active. You can scan vehicles '
        'at the gate, record entries and exits, and issue violations.'),
    'admin': (
        'Your CDSO account is now fully active. You can review registrations, manage '
        'vehicle owners and personnel, issue violations, and generate reports.'),
}


def _login_url():
    # PUBLIC_SITE_URL, not FRONTEND_URL: the campus half's FRONTEND_URL is a LAN
    # address that a recipient reading this on mobile data cannot reach.
    return getattr(settings, 'PUBLIC_SITE_URL', '') or ''


def _button(url, label):
    """A link styled as a button — <button> does nothing in an email client."""
    if not url:
        return ''
    return (
        f'<div style="text-align:center;margin:24px 0 8px;">'
        f'<a href="{esc(url)}" style="display:inline-block;background:#2A2B61;color:#fff;'
        f'text-decoration:none;padding:12px 28px;border-radius:8px;font-weight:600;'
        f'font-size:14px;">{esc(label)}</a></div>'
    )


def send_welcome_email(user):
    """Sent the first time a user replaces the temporary password they were issued."""
    intro = _ROLE_INTRO.get(user.role, 'Your account is now fully active.')
    url = _login_url()
    name = esc(user.full_name or user.email)

    code_row = ''
    if getattr(user, 'user_code', None):
        code_row = (
            f'<tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;width:150px;">Account ID</td>'
            f'<td style="padding:8px 0;font-weight:700;font-family:monospace;color:#2A2B61;">'
            f'{esc(user.user_code)}</td></tr>'
        )

    html_message = f"""
    <html>
      <body style="{_BODY_STYLE}">
        <div style="max-width:600px;margin:0 auto;background:#FFFFFF;border-radius:12px;
                    border-top:4px solid #2A2B61;box-shadow:0 4px 20px rgba(0,0,0,0.08);overflow:hidden;">
          <div style="padding:28px 32px 8px;">
            <h2 style="color:#2A2B61;margin:0 0 6px;">Welcome to SPVVS &#10003;</h2>
            <p style="color:#5A5F72;font-size:14px;margin:0 0 20px;">
              Your password has been set and your account is ready to use.</p>
            <p style="margin:0 0 4px;">Dear <strong>{name}</strong>,</p>
            <p style="color:#5A5F72;font-size:14px;margin:0 0 20px;">{esc(intro)}</p>
            <table style="width:100%;border-collapse:collapse;">
              <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;width:150px;">Email</td>
                  <td style="padding:8px 0;font-weight:600;">{esc(user.email)}</td></tr>
              {code_row}
            </table>
            {_button(url, 'Go to my dashboard')}
            <p style="color:#7C80A3;font-size:12px;margin:16px 0 24px;text-align:center;">
              Keep your password to yourself. SLC staff will never ask you for it.</p>
          </div>
          {_FOOTER}
        </div>
      </body>
    </html>
    """

    text = (
        f"Dear {user.full_name or user.email},\n\n"
        f"Your password has been set and your SPVVS account is ready to use.\n\n"
        f"{intro}\n\n"
        f"Email: {user.email}\n"
        + (f"Account ID: {user.user_code}\n" if getattr(user, 'user_code', None) else '')
        + (f"\nSign in: {url}\n" if url else '')
        + "\nKeep your password to yourself. SLC staff will never ask you for it.\n"
    )

    send_mail(
        subject='Welcome to SLC Smart Parking and Vehicle Verification System',
        message=text,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        html_message=html_message,
        fail_silently=True,
    )


def send_password_changed_email(user):
    """Sent on every password change after the first.

    The point is the warning, not the confirmation: a user who changed their own
    password already knows. This exists so that a user who did *not* finds out.
    """
    changed_at = timezone.localtime(timezone.now()).strftime('%B %d, %Y at %I:%M %p')
    url = _login_url()
    name = esc(user.full_name or user.email)

    html_message = f"""
    <html>
      <body style="{_BODY_STYLE}">
        <div style="max-width:600px;margin:0 auto;background:#FFFFFF;border-radius:12px;
                    border-top:4px solid #2A2B61;box-shadow:0 4px 20px rgba(0,0,0,0.08);overflow:hidden;">
          <div style="padding:28px 32px 8px;">
            <h2 style="color:#2A2B61;margin:0 0 6px;">Your password was changed</h2>
            <p style="margin:0 0 16px;">Dear <strong>{name}</strong>,</p>
            <p style="color:#5A5F72;font-size:14px;margin:0 0 20px;">
              The password for your SLC Smart Parking and Vehicle Verification System account was changed.</p>
            <table style="width:100%;border-collapse:collapse;">
              <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;width:150px;">Account</td>
                  <td style="padding:8px 0;font-weight:600;">{esc(user.email)}</td></tr>
              <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;">Changed on</td>
                  <td style="padding:8px 0;font-weight:600;">{esc(changed_at)}</td></tr>
            </table>
            <div style="background:#FEF2F2;border-left:4px solid #DC2626;padding:15px;
                        margin:20px 0;border-radius:4px;">
              <p style="margin:0;color:#7F1D1D;font-size:14px;">
                <strong>If this was not you</strong>, contact the CDSO office immediately &mdash;
                someone else may have access to your account.</p>
            </div>
            {_button(url, 'Sign in')}
          </div>
          {_FOOTER}
        </div>
      </body>
    </html>
    """

    text = (
        f"Dear {user.full_name or user.email},\n\n"
        f"The password for your SLC Smart Parking and Vehicle Verification System account was changed.\n\n"
        f"Account:    {user.email}\n"
        f"Changed on: {changed_at}\n\n"
        f"If this was not you, contact the CDSO office immediately — someone else "
        f"may have access to your account.\n"
        + (f"\nSign in: {url}\n" if url else '')
    )

    send_mail(
        subject='SPVVS - your password was changed',
        message=text,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        html_message=html_message,
        fail_silently=True,
    )


def send_twofa_lockout_alert(user, ip_address=None, attempts=None):
    """Warn the owner that somebody got past their password but not the code.

    This is the most informative alert the system can send. A wrong password
    means nothing — bots try those constantly and never get this far. Reaching
    the two-factor step means whoever it was already had the right password, and
    then failed the codes. Either the account holder fumbled their own app, or
    the password is known to someone else.

    That is why the call to action is "change your password" rather than
    "ignore this if it was you". Two-factor already did its job and stopped the
    attempt; the password is the half that is now suspect, and it is the only
    half the owner can actually fix.

    Sent once per lockout window, not once per wrong code — see
    accounts.twofa_api._record_failure. Never raises: an alert that broke the
    request it describes would be worse than one that quietly failed to send.
    """
    when = timezone.localtime(timezone.now()).strftime('%B %d, %Y at %I:%M %p')
    url = _login_url()
    name = esc(user.full_name or user.email)
    where = esc(ip_address or 'an unknown location')
    tries = attempts or 'Several'

    html_message = f"""
    <html>
      <body style="{_BODY_STYLE}">
        <div style="max-width:600px;margin:0 auto;background:#FFFFFF;border-radius:12px;
                    border-top:4px solid #DC2626;box-shadow:0 4px 20px rgba(0,0,0,0.08);overflow:hidden;">
          <div style="padding:28px 32px 8px;">
            <h2 style="color:#B91C1C;margin:0 0 6px;">Someone tried to sign in to your account</h2>
            <p style="margin:0 0 16px;">Dear <strong>{name}</strong>,</p>
            <p style="color:#5A5F72;font-size:14px;margin:0 0 20px;">
              Someone signed in with <strong>your correct password</strong> but could not provide
              the code from your authenticator app. Sign-in was blocked and the account is
              locked for 15 minutes.</p>
            <table style="width:100%;border-collapse:collapse;">
              <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;width:150px;">Account</td>
                  <td style="padding:8px 0;font-weight:600;">{esc(user.email)}</td></tr>
              <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;">When</td>
                  <td style="padding:8px 0;font-weight:600;">{esc(when)}</td></tr>
              <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;">From</td>
                  <td style="padding:8px 0;font-weight:600;">{where}</td></tr>
              <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;">Failed attempts</td>
                  <td style="padding:8px 0;font-weight:600;">{esc(str(tries))}</td></tr>
            </table>
            <div style="background:#FEF2F2;border-left:4px solid #DC2626;padding:15px;
                        margin:20px 0;border-radius:4px;">
              <p style="margin:0 0 8px;color:#7F1D1D;font-size:14px;">
                <strong>If this was not you, change your password now.</strong></p>
              <p style="margin:0;color:#7F1D1D;font-size:13px;">
                Whoever tried already knows your current password &mdash; only your
                authenticator app stopped them. Changing it is the part you control.</p>
            </div>
            <div style="background:#F0F9FF;border-left:4px solid #0369A1;padding:15px;
                        margin:20px 0;border-radius:4px;">
              <p style="margin:0;color:#0C4A6E;font-size:13px;">
                <strong>If this was you</strong> &mdash; wrong code, or a phone whose clock has
                drifted &mdash; wait 15 minutes and try again. In Google Authenticator, use
                Settings &rarr; Time correction for codes. If you no longer have the app,
                sign in with your backup code and pair a new device.</p>
            </div>
            {_button(url, 'Go to sign in')}
          </div>
          {_FOOTER}
        </div>
      </body>
    </html>
    """

    text = (
        f"Dear {user.full_name or user.email},\n\n"
        f"Someone signed in with your correct password but could not provide the code "
        f"from your authenticator app. Sign-in was blocked and the account is locked "
        f"for 15 minutes.\n\n"
        f"Account:         {user.email}\n"
        f"When:            {when}\n"
        f"From:            {ip_address or 'an unknown location'}\n"
        f"Failed attempts: {tries}\n\n"
        f"IF THIS WAS NOT YOU, CHANGE YOUR PASSWORD NOW. Whoever tried already knows "
        f"your current password - only your authenticator app stopped them.\n\n"
        f"If this was you (wrong code, or a phone whose clock has drifted), wait 15 "
        f"minutes and try again.\n"
        + (f"\nSign in: {url}\n" if url else '')
    )

    send_mail(
        subject='SPVVS - someone tried to sign in to your account',
        message=text,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        html_message=html_message,
        fail_silently=True,
    )


def notify_twofa_lockout(user, ip_address=None, attempts=None):
    """Send the lockout warning, swallowing anything that goes wrong.

    Wrapped for the same reason as notify_password_set: this runs inside a
    failed login, and an exception here would turn "wrong code" into a 500.
    An account with no email address simply gets nothing.
    """
    if not getattr(user, 'email', None):
        return
    try:
        send_twofa_lockout_alert(user, ip_address=ip_address, attempts=attempts)
    except Exception:
        log.exception('Failed to send the two-factor lockout alert to user %s', user.pk)


def notify_password_set(user, was_first_change):
    """Send whichever of the two messages this password change warrants.

    Wrapped so nothing here can propagate: the password is already saved when
    this runs, and an exception would report failure for an action that
    succeeded. A user without an email address simply gets nothing.
    """
    if not getattr(user, 'email', None):
        return
    try:
        if was_first_change:
            send_welcome_email(user)
        else:
            send_password_changed_email(user)
    except Exception:
        log.exception('Could not send the password-change email to %s', user.email)
