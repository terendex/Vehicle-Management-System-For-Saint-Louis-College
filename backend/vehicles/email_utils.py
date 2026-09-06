import html
import logging
import threading

from django.core.mail import send_mail, EmailMultiAlternatives
from django.conf import settings
from django.db import connection
import qrcode
from io import BytesIO
import base64

log = logging.getLogger(__name__)

DASH = '—'


def send_in_background(send, *args, on_failure=None, **kwargs):
    """Deliver an email without making the caller wait for the mail server.

    Approving a registration was spending its time on three things: hashing the
    new account's password, a couple of dozen database round trips, and this —
    building a large HTML mail and handing it to Brevo over HTTPS (or to Gmail
    over SMTP on the campus half). Only the third has nothing to do with the
    answer the reviewer is waiting for: the account, the vehicle and the pass
    are already committed before this runs, and the send was best-effort even
    when it was synchronous.

    The failure path is what makes this safe to move. A dead mail server used to
    surface as `email_status: 'failed'` in the response, which the CDSO page
    turned into a "give them their credentials directly" warning. Losing that
    signal to a background thread would be a real regression, so `on_failure`
    replaces it — the callers raise an admin notification, which reaches the
    same person through the bell instead of the modal, and outlives the session.

    Returns the Thread so a test can join it. With EMAIL_SEND_ASYNC off (the
    default under test) the work runs inline and None comes back — the failure
    contract is identical either way, so only the concurrency differs.
    """
    def _run():
        try:
            send(*args, **kwargs)
        except Exception:
            log.exception("[email] %s failed; notifying instead", getattr(send, '__name__', send))
            if on_failure is not None:
                try:
                    on_failure()
                except Exception:
                    log.exception("[email] the failure notice itself failed")

    if not getattr(settings, 'EMAIL_SEND_ASYNC', True):
        _run()
        return None

    def _threaded():
        try:
            _run()
        finally:
            # A thread gets its own connection the moment it touches the ORM
            # (the templates read a department FK, and on_failure writes a
            # Notification row). Left open, each send leaks one for the life of
            # the worker until the pool refuses new ones.
            connection.close()

    thread = threading.Thread(target=_threaded, name='email-send', daemon=True)
    thread.start()
    return thread


def esc(value):
    """HTML-escape an applicant-supplied value before it is interpolated into
    one of the templates below.

    These emails are built by f-string, not by the Django template engine, so
    nothing auto-escapes: a full name of `O<b>Brien & Co` would otherwise reach
    the CDSO's inbox as live markup. Escaping is applied at the point of
    interpolation rather than on the model so the stored value stays clean.
    """
    return html.escape(str(value), quote=True) if value is not None else ''


def esc_or_dash(value):
    """esc(), but renders an em dash for blank values — the templates show a
    dash wherever an optional field was left empty."""
    if value is None or value == '':
        return DASH
    return esc(value)


def department_label(registration):
    """The applicant's department, whichever way it was recorded.

    The public form stores the choice in `department_type` and deliberately
    leaves the `department` FK null (see PublicOpenRegistrationView), while
    walk-in rows created against the reference list carry the FK. Reading only
    the FK — as this module used to — meant every online employee registration
    mailed out "Department: —". Returns '' when neither is set so the callers'
    esc_or_dash() turns it into the dash.
    """
    if registration.department:
        return registration.department.name
    if registration.department_type:
        return registration.get_department_type_display()
    return ''


def _generate_qr_png(data):
    """The QR as raw PNG bytes."""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buffer = BytesIO()
    img.save(buffer)
    return buffer.getvalue()


def _qr_public_url(registration, png):
    """Upload the QR to media storage and return its absolute URL, or None.

    The QR used to be embedded as a `data:` URI. Gmail does not render those at
    all — the approval email showed a broken-image placeholder where the owner's
    gate pass should be — and `cid:` is not an option either, since the Railway
    half sends over Brevo's API, which has no Content-ID field. A plain https URL
    is the only form that renders on both transports in every client.

    Only attempted when MEDIA_URL is absolute, i.e. USE_R2 is on, which
    production requires anyway. With local storage the URL would be a relative
    `/media/...` path that means nothing inside a mail client, so the caller
    falls back to the data URI rather than uploading a file for nothing.
    """
    if not str(getattr(settings, 'MEDIA_URL', '')).startswith(('http://', 'https://')):
        return None
    try:
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        # Stable name, replaced on re-approval, so an owner emailed twice does
        # not leave an orphaned object per approval.
        name = f'qr_codes/registration-{registration.pk}.png'
        if default_storage.exists(name):
            default_storage.delete(name)
        return default_storage.url(default_storage.save(name, ContentFile(png)))
    except Exception:
        # Storage being down must not cost the owner their credentials email;
        # the data URI and the attached copy both still carry the QR.
        log.exception('Could not upload the QR for registration %s — '
                      'falling back to an inline data URI.', registration.pk)
        return None


REGISTRANT_TYPE_LABELS = {
    'student':  'Student',
    'employee': 'Employee',
    'fetcher':  'Fetcher / Drop & Go',
}


def registrant_type_label(registration):
    """'Fetcher / Drop & Go', not the raw 'fetcher' the column stores."""
    kind = registration.registrant_type or ''
    return REGISTRANT_TYPE_LABELS.get(kind, kind.capitalize())


def _fetcher_student_lines(registration):
    """One readable line per student a fetcher collects, e.g.
    'DELA CRUZ, JUAN - Grade 7'. Empty list if there are none.

    TEMPORARY (DPO trial): the student's ID number is not collected any more and
    is left out even where an older row still carries one.
    """
    lines = []
    for i, student in enumerate(registration.fetcher_students or [], start=1):
        if not isinstance(student, dict):
            continue
        bits = [student.get('full_name') or f'Student #{i}']
        if student.get('program_year'):
            bits.append(student['program_year'])
        lines.append(' - '.join(bits))
    return lines


def _fetcher_pairs(registration):
    """(label, value) rows for a fetcher: what they are classified as, and who
    they collect. Without these the application summary a fetcher receives is
    the only one with nothing in it specific to their application.

    Pairs rather than finished <tr> markup, so the caller can hand the whole
    section to _kv() and have it stack on a phone like every other row.
    """
    fetcher_type = (registration.get_fetcher_type_display()
                    if registration.fetcher_type else '')
    pairs = [('Classification', esc_or_dash(fetcher_type))]
    lines = _fetcher_student_lines(registration)
    if lines:
        label = 'Student to Fetch' if len(lines) == 1 else 'Students to Fetch'
        pairs.append((label, '<br />'.join(esc(line) for line in lines)))
    return pairs


def _authorized_driver_pairs(registration):
    """Names the authorized adult driver — present when the registrant is a
    minor / non-driving student. Empty list otherwise."""
    if not registration.driver_name:
        return []
    rel = registration.get_driver_relationship_display() if registration.driver_relationship else ''
    val = esc(registration.driver_name)
    if rel:
        val += f' ({esc(rel)})'
    # TEMPORARY (DPO trial): the driver's contact number is not collected.
    return [('Authorized Driver', val)]


def _fee_settled(registration):
    """Whether there is a settled fee to confirm on paper.

    Exempt counts: nothing was owed, so nothing is outstanding. Only an
    application still waiting on its Official Receipt is unsettled.
    """
    from .models import VehicleRegistration
    return registration.payment_status in (
        VehicleRegistration.PaymentStatus.PAID,
        VehicleRegistration.PaymentStatus.EXEMPT,
    )


def _registration_pdf_attachment(registration, pending=False):
    """The (filename, bytes, mimetype) triple for the registration PDF.

    `pending` builds the acknowledgement copy — same record, stated as an
    application under review rather than an approved pass.

    TEMPORARY (DPO trial): no uploads are collected, so neither copy carries the
    documents section that used to print them into the approved copy.
    """
    from registration_pdf import (registration_confirmation_pdf,
                                  registration_pdf_filename)
    return (registration_pdf_filename(registration, pending=pending),
            registration_confirmation_pdf(registration, pending=pending),
            'application/pdf')


# ── Palette ──────────────────────────────────────────────────────────────────
# Taken from the app's own stylesheets rather than chosen again here. The mails
# had drifted onto a separate indigo scheme (#2A2B61 / #6366F1 / #F0F2F7) that
# appears nowhere in the product, so an approval mail and the portal it links to
# did not look like the same institution. These are the values the site
# actually uses, by frequency, in frontend/src.
BRAND        = '#03396C'
INK          = '#0B2340'
MUTED        = '#4A6B85'
FAINT        = '#6B8CA6'
PAGE_BG      = '#EEF4F9'
PANEL_BG     = '#F7FAFC'
TINT_BG      = '#EAF2F8'
BORDER       = '#D3E1EC'
BORDER_FIRM  = '#BDD4E5'
OK_INK       = '#0F7A5A'
OK_BG        = '#E7F5EF'
OK_BORDER    = '#A8DCC6'
WARN_INK     = '#7A5C00'
WARN_BG      = '#FEF9E4'
WARN_BORDER  = '#F7E08A'
BAD_INK      = '#C62828'
BAD_BG       = '#FDECEC'
BAD_BORDER   = '#F3B7B7'


# The one place any of these mails describes how it should look on a phone.
#
# Every layout below is a table with inline styles, because that is the only
# thing Outlook's Word engine renders predictably. Inline styles cannot express
# a breakpoint, though, so the mobile rules live here and are applied by class:
# Apple Mail, iOS Mail and the Gmail apps all honour a <style> block, and the
# clients that strip it fall back to the inline desktop layout, which is merely
# narrow rather than broken.
#
# What actually changes on a small screen:
#   * the 32px side gutters halve, which is ~13% of a 320px screen reclaimed;
#   * label/value rows stop being a fixed 150px column plus whatever is left —
#     at that width the value column is under 170px, so an email address or a
#     schedule wrapped onto three lines. They stack instead, label above value;
#   * the two-up summary panels stop being two 50% cells with a divider;
#   * call-to-action links go full width, so the tap target is the row.
#
# Deliberately NOT %-formatted. CSS is full of literal percent signs, and every
# one of them would have to be doubled — `width:100% !important` reads as a
# conversion flag followed by '!' and raises at import time, which is a silly
# way to take the whole app down. The one dynamic value is concatenated instead.
MOBILE_CSS = """
      body { margin:0 !important; padding:0 !important; width:100% !important; }
      table { border-collapse:collapse; }
      img { border:0; line-height:100%; outline:none; text-decoration:none; }
      a { color:""" + BRAND + """; }
      @media only screen and (max-width:600px) {
        .sh-pad   { padding-left:18px !important; padding-right:18px !important; }
        .sh-shell { width:100% !important; border-radius:0 !important; }
        .sh-h1    { font-size:20px !important; line-height:1.3 !important; }
        /* Label above value, each on its own line. */
        .sh-kv td      { display:block !important; width:100% !important; }
        .sh-kv td.sh-k { padding:10px 0 2px !important; }
        .sh-kv td.sh-v { padding:0 0 4px !important; font-size:15px !important; }
        /* Side-by-side summary cells stack, and the divider between them goes. */
        .sh-split td { display:block !important; width:100% !important;
                       border-left:0 !important; padding-left:0 !important;
                       padding-top:14px !important; }
        .sh-split td:first-child { padding-top:0 !important; }
        .sh-btn a { display:block !important; text-align:center !important; }
      }
"""


def _kv(rows, *, label_width='150px'):
    """A label/value block that stacks on a phone.

    `rows` is (label, value_html) pairs; the value is interpolated as HTML, so
    callers pass it through esc()/esc_or_dash() first — same contract the
    templates always had. A row whose value is None is dropped entirely, which
    is how a section drops a line that does not apply to this registrant rather
    than printing a dash against it.
    """
    out = []
    for label, value in rows:
        if value is None:
            continue
        out.append(
            '<tr>'
            '<td class="sh-k" style="padding:7px 12px 7px 0;color:%s;font-size:13px;'
            'line-height:1.45;width:%s;vertical-align:top;">%s</td>'
            '<td class="sh-v" style="padding:7px 0;color:%s;font-size:14px;'
            'font-weight:600;line-height:1.45;vertical-align:top;'
            'word-break:break-word;">%s</td>'
            '</tr>' % (MUTED, label_width, label, INK, value)
        )
    if not out:
        return ''
    return ('<table class="sh-kv" role="presentation" cellpadding="0" cellspacing="0" '
            'border="0" style="width:100%%;border-collapse:collapse;">%s</table>'
            % ''.join(out))


def _section(title, inner):
    """A titled block, with the rule under the heading the site uses."""
    return (
        '<tr><td class="sh-pad" style="padding:0 32px 18px;">'
        '<div style="margin:0 0 10px;padding-bottom:8px;border-bottom:1px solid %s;'
        'color:%s;font-size:12px;font-weight:700;letter-spacing:0.06em;'
        'text-transform:uppercase;">%s</div>%s</td></tr>'
        % (BORDER, BRAND, title, inner)
    )


def _panel(inner, *, bg, border, pad='16px 18px'):
    """A tinted callout, full width inside the gutters."""
    return (
        '<tr><td class="sh-pad" style="padding:0 32px 18px;">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'style="width:100%%;border-collapse:collapse;background:%s;'
        'border:1px solid %s;border-radius:10px;">'
        '<tr><td style="padding:%s;">%s</td></tr></table></td></tr>'
        % (bg, border, pad, inner)
    )


def _split(left_label, left_value, right_label, right_value):
    """Two facts side by side on a laptop, stacked on a phone."""
    cell = ('<div style="color:%s;font-size:11px;font-weight:700;'
            'letter-spacing:0.07em;text-transform:uppercase;margin-bottom:4px;">%s</div>'
            '<div style="color:%s;font-size:16px;font-weight:700;'
            'font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;'
            'word-break:break-word;">%s</div>')
    return (
        '<table class="sh-split" role="presentation" cellpadding="0" cellspacing="0" '
        'border="0" style="width:100%%;border-collapse:collapse;">'
        '<tr>'
        '<td style="width:50%%;vertical-align:top;">%s</td>'
        '<td style="width:50%%;vertical-align:top;border-left:1px solid %s;'
        'padding-left:18px;">%s</td>'
        '</tr></table>'
        % (cell % (BRAND, left_label, INK, left_value), BORDER_FIRM,
           cell % (BRAND, right_label, INK, right_value))
    )


def _button(href, label):
    """A call to action that is a full-width tap target on a phone."""
    return (
        '<table class="sh-btn" role="presentation" cellpadding="0" cellspacing="0" '
        'border="0" style="border-collapse:collapse;"><tr><td '
        'style="border-radius:9px;background:%s;">'
        '<a href="%s" style="display:inline-block;padding:14px 24px;color:#FFFFFF;'
        'font-size:15px;font-weight:700;text-decoration:none;border-radius:9px;">'
        '%s</a></td></tr></table>' % (BRAND, href, label)
    )


def _shell(*, accent, preheader, heading, intro, rows_html):
    """The wrapper every registration mail shares.

    `preheader` is the line the inbox shows next to the subject before anything
    is opened. Left to itself a client grabs the first text in the body, which
    for these mails was a greeting — every one of them previewed as "Dear
    <name>," and told the reader nothing. It is hidden in the mail itself.
    """
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta name="x-apple-disable-message-reformatting" />
<title>%(heading)s</title>
<style type="text/css">%(css)s</style>
</head>
<body style="margin:0;padding:0;background:%(page_bg)s;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">%(preheader)s</div>
<table role="presentation" cellpadding="0" cellspacing="0" border="0"
       style="width:100%%;border-collapse:collapse;background:%(page_bg)s;">
  <tr>
    <td align="center" style="padding:20px 10px;">
      <table class="sh-shell" role="presentation" cellpadding="0" cellspacing="0" border="0"
             style="width:100%%;max-width:620px;border-collapse:collapse;background:#FFFFFF;
                    border-radius:14px;border-top:4px solid %(accent)s;overflow:hidden;
                    font-family:'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
        <tr>
          <td class="sh-pad" style="padding:26px 32px 6px;">
            <h1 class="sh-h1" style="margin:0 0 8px;color:%(accent)s;font-size:22px;
                                     font-weight:700;line-height:1.3;">%(heading)s</h1>
            <div style="color:%(muted)s;font-size:14px;line-height:1.6;">%(intro)s</div>
          </td>
        </tr>
        <tr><td style="height:18px;line-height:18px;font-size:0;">&nbsp;</td></tr>
        %(rows)s
        <tr>
          <td class="sh-pad" style="padding:16px 32px 22px;background:%(panel_bg)s;
                                    border-top:1px solid %(border)s;text-align:center;">
            <div style="color:%(muted)s;font-size:12px;line-height:1.6;">
              Saint Louis College &middot; Smart Parking and Vehicle Verification System
            </div>
            <div style="color:%(faint)s;font-size:11px;margin-top:4px;">
              This is an automated message &mdash; please do not reply.
            </div>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
</body>
</html>""" % {
        'css': MOBILE_CSS, 'accent': accent, 'preheader': preheader,
        'heading': heading, 'intro': intro, 'rows': rows_html,
        'page_bg': PAGE_BG, 'panel_bg': PANEL_BG, 'border': BORDER,
        'muted': MUTED, 'faint': FAINT,
    }


def send_acceptance_email(registration, temp_password, user_code=None):
    # Generate QR code. The payload must stay exactly this shape — the guard
    # scanner parses `VEHICLE:{plate}|ID:{n}` (see SecurityEntryManagement.jsx).
    qr_data = f"VEHICLE:{registration.plate_number}|ID:{registration.id}"
    qr_png = _generate_qr_png(qr_data)
    qr_src = (_qr_public_url(registration, qr_png)
              or f"data:image/png;base64,{base64.b64encode(qr_png).decode()}")

    # Determine system-assigned registration ID
    system_id = esc_or_dash(registration.system_student_id or registration.system_employee_id)

    # Identity rows by registrant type, as (label, value) pairs for _kv().
    if registration.registrant_type == 'fetcher':
        # A fetcher has neither an employee ID nor a department, and the old
        # else-branch labelled their blank columns as both — an approval email
        # that read "Employee ID: —, Department: —" to every fetcher.
        identity_pairs = _fetcher_pairs(registration)
    # TEMPORARY (DPO trial): the student/employee ID row is gone with the field
    # — what is left is the one detail that still describes them.
    elif registration.registrant_type == 'student':
        identity_pairs = [('Program &amp; Year', esc_or_dash(registration.program_year))]
    else:
        identity_pairs = [('Department', esc_or_dash(department_label(registration)))]

    if registration.registrant_type == 'student':
        campus_days_str = esc_or_dash(', '.join(registration.campus_days)
                                      if registration.campus_days else '')
    else:
        campus_days_str = 'Any campus day (Monday to Saturday)'
    campus_pairs = [('Campus Days', campus_days_str)]

    # Portal account ID (use table layout — flex not supported in many email clients)
    portal_id_display   = esc_or_dash(user_code)
    license_val         = esc_or_dash(registration.drivers_license)
    color_val           = esc_or_dash(registration.vehicle_color)
    conduction_val      = esc_or_dash(registration.conduction_number)
    full_name_val       = esc(registration.full_name)
    email_val           = esc(registration.email)
    plate_val           = esc_or_dash(registration.plate_number)
    vehicle_type_val    = esc(registration.vehicle_type)
    registrant_type_val = esc(registrant_type_label(registration))
    temp_password_val   = esc(temp_password)

    html_message = _shell(
        accent=OK_INK,
        preheader=f'Approved \u2014 your gate QR and portal login for {plate_val}.',
        heading='Vehicle Registration Approved',
        intro=(f'Dear <strong style="color:{INK};">{full_name_val}</strong>, your registration '
               f'for plate number <strong style="color:{INK};">{plate_val}</strong> has been '
               f'approved. Your portal login and gate QR code are below.'),
        rows_html=(
            # Credentials first. It is the one thing the owner opens this mail
            # for, and it used to sit below three detail tables and the QR.
            _panel(
                f'<div style="color:{BRAND};font-size:12px;font-weight:700;'
                f'letter-spacing:0.06em;text-transform:uppercase;margin-bottom:8px;">'
                f'Portal login</div>'
                + _kv([('Email', email_val),
                       ('Password',
                        f'<span style="font-family:ui-monospace,SFMono-Regular,Menlo,'
                        f'Consolas,monospace;font-size:15px;letter-spacing:1px;">'
                        f'{temp_password_val}</span>')], label_width='96px')
                + f'<div style="margin-top:10px;color:{BAD_INK};font-size:12.5px;'
                  f'line-height:1.6;"><strong>Important:</strong> you will be asked to change '
                  f'this password the first time you sign in.</div>',
                bg=TINT_BG, border=BORDER_FIRM)
            # Then the QR — the thing actually held up at the gate.
            + f'<tr><td class="sh-pad" style="padding:0 32px 18px;">'
              f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
              f'style="width:100%;border-collapse:collapse;background:{PANEL_BG};'
              f'border:1px solid {BORDER};border-radius:10px;">'
              f'<tr><td align="center" style="padding:20px 18px;">'
              f'<div style="color:{MUTED};font-size:13px;line-height:1.6;margin-bottom:12px;">'
              f'Show this to security when you enter:</div>'
              f'<img src="{qr_src}" alt="Vehicle gate QR code" width="190" '
              f'style="width:190px;max-width:70%;height:auto;background:#FFFFFF;'
              f'border:1px solid {BORDER};border-radius:8px;padding:8px;" />'
              f'<div style="color:{FAINT};font-size:11.5px;line-height:1.6;margin-top:10px;">'
              f'Not showing? The same QR is attached to this email, and is always on your '
              f'portal dashboard.</div></td></tr></table></td></tr>'
            + _section('Your IDs', _split('Portal Account ID', portal_id_display,
                                          'System Registration ID', system_id))
            + _section('Registration', _kv(
                [('Full Name',        full_name_val),
                 ('Email',            email_val),
                 ('Type',             registrant_type_val)]
                + identity_pairs
                + [("Driver&#39;s License", license_val)]
                + _authorized_driver_pairs(registration)
                + campus_pairs))
            + _section('Vehicle', _kv([
                ('Plate Number',   plate_val),
                ('Vehicle Type',   vehicle_type_val),
                ('Color',          color_val),
                ('Conduction No.', conduction_val),
            ]))
        ),
    )

    # EmailMultiAlternatives rather than send_mail(): send_mail cannot carry
    # attachments, and the approval mail ships the owner's registration
    # confirmation as a PDF.
    msg = EmailMultiAlternatives(
        subject="SLC Vehicle Registration Approved \u2014 Your Account & Credentials",
        # Raw (unescaped) values here — esc_or_dash output is for the HTML part
        # only; a password containing '&' must not reach the plain-text body as
        # '&amp;' or the owner cannot log in by copying it.
        body=(
            f"Your vehicle registration has been approved.\n\n"
            f"Portal Account ID: {user_code or DASH}\n"
            f"System Registration ID: "
            f"{registration.system_student_id or registration.system_employee_id or DASH}\n\n"
            f"Email: {registration.email}\n"
            f"Temporary Password: {temp_password}\n\n"
            f"You will be prompted to change your password on first login.\n\n"
            + ("A PDF copy of your full registration details is attached."
               if _fee_settled(registration) else
               "Your registration form will be emailed to you as a PDF once "
               "your Official Receipt has been uploaded.")
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[registration.email],
    )
    msg.attach_alternative(html_message, "text/html")

    # The QR also rides along as a real file. Mail clients block remote images by
    # default and strip data: URIs outright, so the inline copy above cannot be
    # relied on — but an attachment always arrives, and the owner can save it to
    # their phone to show at the gate.
    msg.attach(f'qr-{registration.plate_number or registration.pk}.png',
               qr_png, 'image/png')

    # The confirmation PDF rides with the fee, not with the approval. An
    # application approved before its Official Receipt is in (see
    # unpaid_accept_reason) has no settled fee to confirm, so the PDF is sent
    # instead by send_receipt_received_email the moment the receipt lands.
    #
    # A failure building it must not cost the owner their approval email (and,
    # upstream, must not roll back the approval itself) — so send without the
    # attachment and log it rather than raising.
    if _fee_settled(registration):
        try:
            msg.attach(*_registration_pdf_attachment(registration))
        except Exception:
            log.exception(
                "Could not attach registration PDF for %s (registration %s) \u2014 "
                "sending the approval email without it.",
                registration.email, registration.pk,
            )

    msg.send(fail_silently=False)


def send_pending_email(registration):
    """Sent immediately after a public registration form is submitted (status=pending)."""
    submitted_at = registration.created_at.strftime('%B %d, %Y at %I:%M %p') if registration.created_at else '—'

    # Identity rows differ by registrant type, as (label, value) pairs for _kv().
    # TEMPORARY (DPO trial): no student/employee ID row — it is not collected.
    if registration.registrant_type == 'student':
        id_pairs = [('Program &amp; Year', esc_or_dash(registration.program_year))]
        campus_days_str = esc_or_dash(', '.join(registration.campus_days)
                                      if registration.campus_days else '')
        schedule_pairs = [
            ('Campus Days',    campus_days_str),
            ('Schedule Group', esc_or_dash(registration.schedule)),
        ]
    elif registration.registrant_type == 'employee':
        id_pairs = [('Department', esc_or_dash(department_label(registration)))]
        schedule_pairs = []
    else:  # fetcher
        # Classification and the students being collected are this application's
        # identity, the way a student ID or a department is for the other two.
        id_pairs = _fetcher_pairs(registration)
        schedule_pairs = [('Campus Days', 'Any campus day (Monday to Saturday)')]

    full_name_val    = esc(registration.full_name)
    email_val        = esc(registration.email)
    license_val      = esc_or_dash(registration.drivers_license)
    plate_val        = esc_or_dash(registration.plate_number)
    vehicle_type_val = esc(registration.vehicle_type)
    color_val        = esc_or_dash(registration.vehicle_color)
    conduction_val   = esc_or_dash(registration.conduction_number)

    type_label = registrant_type_label(registration)
    ref_number = f"REG-{str(registration.pk).zfill(6)}"

    # ── Payment call-to-action ──
    # The applicant records their own payment now: they settle the fee at the
    # Accounting Office and upload the Official Receipt through this link, so CDSO
    # verifies an image instead of re-keying a number at a counter.
    # PUBLIC_SITE_URL, not FRONTEND_URL — the campus half's FRONTEND_URL is a LAN
    # address that resolves nowhere from an applicant's phone.
    base_url = (getattr(settings, 'PUBLIC_SITE_URL', '') or '').rstrip('/')
    fee = registration.pass_fee()
    if fee == 0:
        # Fee-exempt: no Accounting stop, no receipt, and no link to send.
        payment_block = _panel(
            f'<div style="color:{OK_INK};font-size:15px;font-weight:700;margin-bottom:6px;">'
            f'No Payment Required</div>'
            f'<div style="color:{INK};font-size:14px;line-height:1.7;">'
            f'Your department is <strong>exempt</strong> from the Vehicle Pass fee, so there is '
            f'nothing to settle at the Accounting Office. Proceed to the CDSO Office once your '
            f'application is approved.</div>',
            bg=OK_BG, border=OK_BORDER)
        payment_steps = "<li>No Vehicle Pass fee is due &#8212; your department is exempt.</li>"
        payment_text  = "No Vehicle Pass fee is due - your department is exempt.\n\n"
    else:
        payment_link = f"{base_url}/registration/payment?token={registration.payment_token}"
        payment_block = _panel(
            f'<div style="color:{BRAND};font-size:15px;font-weight:700;margin-bottom:6px;">'
            f'Next step &mdash; pay, then file your receipt number</div>'
            f'<div style="color:{INK};font-size:14px;line-height:1.7;margin-bottom:14px;">'
            f'Settle the Vehicle Pass fee of <strong>&#8369;{fee:.2f}</strong> at the '
            f'<strong>Accounting Office</strong>, then file the Official Receipt number using '
            f'the button below. Keep the receipt itself &mdash; the CDSO checks the paper copy '
            f'when you collect your pass. Your application stays <strong>unpaid</strong> and is '
            f'not queued for review until the number is received.</div>'
            + _button(payment_link, 'File Official Receipt Number')
            + f'<div style="color:{FAINT};font-size:11.5px;line-height:1.6;margin-top:12px;'
              f'word-break:break-all;">Or paste this link into your browser: {payment_link}</div>',
            bg=TINT_BG, border=BORDER_FIRM)
        payment_steps = (
            f"<li>Pay the Vehicle Pass fee of <strong>&#8369;{fee:.2f}</strong> at the Accounting "
            f"Office, then <strong>file your Official Receipt number</strong> using the link above.</li>"
        )
        payment_text = (
            f"NEXT STEP - PAY AND FILE YOUR RECEIPT NUMBER\n"
            f"Pay the Vehicle Pass fee of PHP {fee:.2f} at the Accounting Office, then file\n"
            f"the Official Receipt number here:\n{payment_link}\n\n"
            f"Keep the receipt itself - the CDSO checks the paper copy at the counter.\n"
            f"Your application is not queued for review until the number is received.\n\n"
        )

    html_message = _shell(
        accent=WARN_INK,
        preheader=(f'{ref_number} received \u2014 '
                   + ('nothing to pay; watch for the outcome.' if fee == 0
                      else 'next, pay the fee and file your OR number.')),
        heading='Registration Received',
        intro=(f'Dear <strong style="color:{INK};">{full_name_val}</strong>, your vehicle '
               f'registration has been submitted and is awaiting CDSO review. The '
               f'acknowledgement PDF attached to this email is your proof that you applied '
               f'\u2014 it is <strong style="color:{INK};">not</strong> a vehicle pass and '
               f'does not grant entry.'),
        rows_html=(
            # Reference and date first: they are what the applicant is asked for
            # if they ever have to chase this up.
            f'<tr><td class="sh-pad" style="padding:0 32px 18px;">'
            f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            f'style="width:100%;border-collapse:collapse;background:{WARN_BG};'
            f'border:1px solid {WARN_BORDER};border-radius:10px;">'
            f'<tr><td style="padding:16px 18px;">'
            + _split('Reference No.', ref_number, 'Submitted', esc(submitted_at))
            + f'</td></tr></table></td></tr>'
            # Then the single next action. It used to sit below both detail
            # tables, which on a phone is several screens past the fold.
            + payment_block
            + _section('What happens next', (
                f'<ol style="margin:0;padding-left:20px;color:{MUTED};font-size:14px;'
                f'line-height:1.9;">{payment_steps}'
                f'<li>The CDSO office will review the information you submitted.</li>'
                f'<li>You will be emailed once your registration is '
                f'<strong style="color:{INK};">approved</strong> or '
                f'<strong style="color:{INK};">declined</strong>.</li>'
                f'<li>If approved, that email carries your portal login and vehicle QR code.</li>'
                f'</ol>'))
            + _section('Your details', _kv(
                [('Full Name',       full_name_val),
                 ('Email',           email_val),
                 ('Registrant Type', esc(type_label)),
                 ("Driver&#39;s License", license_val)]
                + _authorized_driver_pairs(registration)
                + id_pairs + schedule_pairs))
            + _section('Vehicle', _kv([
                ('Plate Number',   plate_val),
                ('Vehicle Type',   vehicle_type_val),
                ('Color',          color_val),
                ('Conduction No.', conduction_val),
            ]))
        ),
    )

    type_display = type_label
    student_lines = _fetcher_student_lines(registration)
    fetched_text = ''
    if student_lines:
        listed = '\n'.join(f"  - {line}" for line in student_lines)
        fetched_text = f"Students to fetch:\n{listed}\n\n"
    # EmailMultiAlternatives rather than send_mail(): send_mail cannot carry an
    # attachment, and this email now does — see below.
    msg = EmailMultiAlternatives(
        subject=f"SLC Vehicle Registration Received — {ref_number} (Pending Review)",
        body=(
            f"Dear {registration.full_name},\n\n"
            f"Your vehicle registration has been received and is pending CDSO review.\n\n"
            f"Reference No.: {ref_number}\n"
            f"Plate Number:  {registration.plate_number}\n"
            f"Type:          {type_display}\n"
            f"Submitted:     {submitted_at}\n\n"
            f"{fetched_text}"
            f"{payment_text}"
            f"Your registration acknowledgement is attached as a PDF \u2014 keep it as "
            f"proof that you applied. It is not a vehicle pass.\n\n"
            f"You will be notified by email once a decision has been made.\n\n"
            f"Saint Louis College Smart Parking and Vehicle Verification System"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[registration.email],
    )
    msg.attach_alternative(html_message, "text/html")

    # Proof of application, on paper. Until this existed, an applicant who was
    # asked whether they had registered had nothing to show but an email — and
    # the approval PDF, which is the document that answers that, does not exist
    # until CDSO has decided.
    #
    # Built as the *pending* copy: it states in its own text that it is not a
    # pass, so it cannot be waved at a gate. A failure building it must not cost
    # the applicant their acknowledgement email (and, upstream, must not roll
    # back the submission) — so send without the attachment and log it.
    try:
        msg.attach(*_registration_pdf_attachment(registration, pending=True))
    except Exception:
        log.exception(
            "Could not attach the registration acknowledgement PDF for %s "
            "(registration %s) — sending the pending email without it.",
            registration.email, registration.pk,
        )

    msg.send(fail_silently=False)


def send_receipt_received_email(registration):
    """Sent the moment the applicant uploads their Official Receipt.

    This is the email that carries the registration form. Until the receipt is
    in, there is no settled fee to confirm and nothing worth putting on a PDF
    the owner is meant to keep; once it is, the form is complete and goes out
    with the receipt and the rest of the uploads printed into it — the same
    document the CDSO files.

    The PDF only rides along once the registration is actually approved. It
    states in its own text that the pass was granted, so sending it to someone
    still under review would put a pass in their hands that nobody issued; the
    approval email carries it for them instead, now that their fee is settled.
    """
    from .models import VehicleRegistration

    approved   = registration.status == VehicleRegistration.Status.ACCEPTED
    ref_number = f'REG-{registration.id:06d}'
    full_name  = esc(registration.full_name)
    plate_val  = esc_or_dash(registration.plate_number)
    or_val     = esc_or_dash(registration.or_number)
    amount_val = (f'PHP {registration.amount_paid:,.2f}'
                  if registration.amount_paid is not None else DASH)

    next_step_html = (
        '<p>Your registration form is attached to this email as a PDF. Keep a '
        'copy — present it when requested at the campus gates.</p>'
        if approved else
        '<p>Your application is now queued for CDSO review. Once it is '
        'approved, your registration form will be emailed to you as a PDF '
        'along with your portal login and vehicle QR code.</p>'
    )

    html_message = _shell(
        accent=OK_INK,
        preheader=f'OR {or_val} recorded \u2014 your vehicle pass fee is settled.',
        heading='Official Receipt Received',
        intro=(f'Dear <strong style="color:{INK};">{full_name}</strong>, we have recorded the '
               f'Official Receipt for your vehicle pass. Your fee is now settled.'),
        rows_html=(
            _section('Payment', _kv([
                ('Reference No.', ref_number),
                ('Plate Number',  plate_val),
                ('OR Number',     or_val),
                ('Amount Paid',   amount_val),
            ]))
            + _panel(f'<div style="color:{OK_INK};font-size:14px;line-height:1.7;">'
                     f'{next_step_html}</div>', bg=OK_BG, border=OK_BORDER)
        ),
    )

    msg = EmailMultiAlternatives(
        subject=f"SLC Vehicle Pass — Official Receipt Received ({ref_number})",
        body=(
            f"Dear {registration.full_name},\n\n"
            f"We have received the Official Receipt for your vehicle pass.\n\n"
            f"Reference No.: {ref_number}\n"
            f"Plate Number:  {registration.plate_number}\n"
            f"OR Number:     {registration.or_number}\n\n"
            + ("Your registration form is attached to this email as a PDF."
               if approved else
               "Your application is now queued for CDSO review. Your "
               "registration form will be emailed to you once it is approved.")
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[registration.email],
    )
    msg.attach_alternative(html_message, "text/html")

    if approved:
        # As in the approval mail: a PDF that will not build must not cost the
        # applicant the confirmation that their payment landed.
        try:
            msg.attach(*_registration_pdf_attachment(registration))
        except Exception:
            log.exception(
                "Could not attach registration PDF for %s (registration %s) — "
                "sending the receipt confirmation without it.",
                registration.email, registration.pk,
            )

    msg.send(fail_silently=False)


def send_rejection_email(registration, reason):
    rejection_reason = reason or 'No specific reason provided.'
    reason_html    = esc(rejection_reason)
    full_name_val  = esc(registration.full_name)
    plate_val      = esc_or_dash(registration.plate_number)

    html_message = _shell(
        accent=BAD_INK,
        preheader=f'Your application for {plate_val} was not approved.',
        heading='Vehicle Registration Declined',
        intro=(f'Dear <strong style="color:{INK};">{full_name_val}</strong>, your registration '
               f'for plate number <strong style="color:{INK};">{plate_val}</strong> has been '
               f'reviewed and could not be approved.'),
        rows_html=(
            _panel(
                f'<div style="color:{BAD_INK};font-size:12px;font-weight:700;'
                f'letter-spacing:0.06em;text-transform:uppercase;margin-bottom:6px;">'
                f'Reason for rejection</div>'
                f'<div style="color:{INK};font-size:14px;line-height:1.6;">{reason_html}</div>',
                bg=BAD_BG, border=BAD_BORDER)
            + _section('What you can do', (
                f'<div style="color:{MUTED};font-size:14px;line-height:1.7;">'
                f'Correct whatever is named above and submit a new application, or bring your '
                f'documents to the <strong style="color:{INK};">CDSO Office</strong> if you '
                f'believe this decision was made in error.</div>'))
        ),
    )

    send_mail(
        subject="SLC Vehicle Registration Status Update",
        message=f"Your vehicle registration has been declined.\n\nReason: {rejection_reason}",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[registration.email],
        html_message=html_message,
        fail_silently=False,
    )


def send_account_archived_email(user, banned=False, next_window=None):
    """Notify a vehicle owner that their account expired and was archived.

    Registration reopens only during a registration period, so the copy points to
    the next registration window rather than saying "any time". If `banned` (the
    owner reached the maximum number of violations), the copy states they are not
    eligible to register again. `next_window` is an optional RegistrationPeriod.
    """
    expired_on = user.expires_at.strftime('%B %d, %Y') if user.expires_at else 'its expiration date'

    if next_window and getattr(next_window, 'start_date', None) and getattr(next_window, 'end_date', None):
        window_text = (f"the next registration period "
                       f"({next_window.start_date.strftime('%B %d, %Y')} to "
                       f"{next_window.end_date.strftime('%B %d, %Y')})")
    else:
        window_text = "the next registration period, once it opens"

    if banned:
        cta_panel = _panel(
            f'<div style="color:{BAD_INK};font-size:12px;font-weight:700;letter-spacing:0.06em;'
            f'text-transform:uppercase;margin-bottom:6px;">Not eligible to re-register</div>'
            f'<div style="color:{INK};font-size:14px;line-height:1.7;">Because your account '
            f'reached the maximum number of traffic violations, you are not eligible to register '
            f'a vehicle pass again. Please contact the administration office if you have any '
            f'questions.</div>',
            bg=BAD_BG, border=BAD_BORDER)
        cta_text = ("Because your account reached the maximum number of violations, you are not "
                    "eligible to register a vehicle pass again. Please contact the administration office.")
    else:
        cta_panel = _panel(
            f'<div style="color:{WARN_INK};font-size:12px;font-weight:700;letter-spacing:0.06em;'
            f'text-transform:uppercase;margin-bottom:6px;">Registering again</div>'
            f'<div style="color:{INK};font-size:14px;line-height:1.7;">You may register again '
            f'during {window_text}. Your previous email, ID and plate number are free to reuse '
            f'&mdash; they will not be reported as already taken.</div>',
            bg=WARN_BG, border=WARN_BORDER)
        cta_text = (f"You may register again during {window_text}. Your previous email, ID and plate "
                    f"number are free to reuse.")

    html_message = _shell(
        accent=WARN_INK,
        preheader=f'Your vehicle pass account expired on {expired_on}.',
        heading='Your Vehicle Pass Account Has Expired',
        intro=(f'Dear <strong style="color:{INK};">{esc(user.full_name)}</strong>, your Saint '
               f'Louis College vehicle owner account reached its expiration date on '
               f'<strong style="color:{INK};">{expired_on}</strong> and has been archived. '
               f'Your vehicle pass is no longer active.'),
        rows_html=(
            cta_panel
            + _section('If this looks wrong', (
                f'<div style="color:{MUTED};font-size:14px;line-height:1.7;">Contact the '
                f'administration office and they can check the record.</div>'))
        ),
    )

    send_mail(
        subject="SLC Vehicle Pass Account Expired",
        message=(f"Dear {user.full_name},\n\nYour vehicle owner account expired on {expired_on} "
                 f"and has been archived.\n\n{cta_text}"),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        html_message=html_message,
        fail_silently=True,  # a mail failure must not abort the archive batch
    )
