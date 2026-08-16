import html
import logging

from django.core.mail import send_mail, EmailMultiAlternatives
from django.conf import settings
import qrcode
from io import BytesIO
import base64

log = logging.getLogger(__name__)

DASH = '—'


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


def _authorized_driver_row(registration, pad='8px'):
    """Table row naming the authorized adult driver — present when the
    registrant is a minor / non-driving student. Empty string otherwise."""
    if not registration.driver_name:
        return ''
    rel = registration.get_driver_relationship_display() if registration.driver_relationship else ''
    val = esc(registration.driver_name)
    if rel:
        val += f' ({esc(rel)})'
    if registration.driver_contact:
        val += f' &middot; {esc(registration.driver_contact)}'
    return (
        f'<tr><td style="padding:{pad} 0;color:#5A5F72;font-size:13px;">Authorized Driver</td>'
        f'<td style="padding:{pad} 0;font-weight:600;">{val}</td></tr>'
    )


def send_acceptance_email(registration, temp_password, user_code=None):
    # Generate QR code. The payload must stay exactly this shape — the guard
    # scanner parses `VEHICLE:{plate}|ID:{n}` (see SecurityEntryManagement.jsx).
    qr_data = f"VEHICLE:{registration.plate_number}|ID:{registration.id}"
    qr_png = _generate_qr_png(qr_data)
    qr_src = (_qr_public_url(registration, qr_png)
              or f"data:image/png;base64,{base64.b64encode(qr_png).decode()}")

    # Determine system-assigned registration ID
    system_id = esc_or_dash(registration.system_student_id or registration.system_employee_id)

    # Build identity rows based on registrant type (no nested f-strings)
    if registration.registrant_type == 'student':
        id_label  = 'Student ID'
        id_value  = esc_or_dash(registration.student_id)
        id2_label = 'Program &amp; Year'
        id2_value = esc_or_dash(registration.program_year)
    else:
        id_label  = 'Employee ID'
        id_value  = esc_or_dash(registration.employee_id)
        id2_label = 'Department'
        id2_value = esc_or_dash(department_label(registration))

    identity_rows = (
        f'<tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;width:140px;">{id_label}</td>'
        f'<td style="padding:8px 0;font-weight:600;">{id_value}</td></tr>'
        f'<tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;">{id2_label}</td>'
        f'<td style="padding:8px 0;font-weight:600;">{id2_value}</td></tr>'
    )

    # Campus days row (only for students) — built separately to avoid nested f-string
    if registration.registrant_type == 'student':
        campus_days_str = esc_or_dash(', '.join(registration.campus_days)
                                      if registration.campus_days else '')
        campus_days_row = (
            f'<tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;">Campus Days</td>'
            f'<td style="padding:8px 0;font-weight:600;">{campus_days_str}</td></tr>'
        )
    else:
        campus_days_row = ''

    # Portal account ID (use table layout — flex not supported in many email clients)
    portal_id_display   = esc_or_dash(user_code)
    contact_val         = esc_or_dash(registration.contact_number)
    address_val         = esc_or_dash(registration.address)
    license_val         = esc_or_dash(registration.drivers_license)
    color_val           = esc_or_dash(registration.vehicle_color)
    conduction_val      = esc_or_dash(registration.conduction_number)
    full_name_val       = esc(registration.full_name)
    email_val           = esc(registration.email)
    plate_val           = esc_or_dash(registration.plate_number)
    vehicle_type_val    = esc(registration.vehicle_type)
    registrant_type_val = esc(registration.registrant_type)
    temp_password_val   = esc(temp_password)

    html_message = f"""
    <html>
        <body style="font-family: Arial, sans-serif; color: #1A1D2E; background-color: #F0F2F7; padding: 20px; margin: 0;">
            <div style="max-width: 620px; margin: 0 auto; background: #FFFFFF; border-radius: 12px; border-top: 4px solid #2A2B61; box-shadow: 0 4px 20px rgba(0,0,0,0.08); overflow: hidden;">

                <!-- Header -->
                <div style="padding: 28px 32px 0;">
                    <h2 style="color: #2A2B61; margin: 0 0 6px;">Vehicle Registration Approved &#10003;</h2>
                    <p style="color: #5A5F72; margin: 0 0 16px; font-size: 14px;">Your registration has been reviewed and accepted by the administration.</p>
                    <p style="margin: 0 0 4px;">Dear <strong>{full_name_val}</strong>,</p>
                    <p style="color: #5A5F72; font-size: 14px; margin: 0 0 24px;">
                        Your vehicle registration for plate number <strong style="color:#2A2B61;">{plate_val}</strong> has been approved.
                        Below are your account details and vehicle access QR code.
                    </p>
                </div>

                <!-- System IDs Banner (table-based for email client compatibility) -->
                <div style="margin: 0 32px 24px; background: #EEF0FF; border-radius: 10px; padding: 16px 20px;">
                    <table style="width:100%; border-collapse:collapse;">
                        <tr>
                            <td style="padding: 0; vertical-align: top; width: 50%;">
                                <div style="font-size: 11px; color: #6366F1; text-transform: uppercase; letter-spacing: 0.08em; font-weight: 700; margin-bottom: 4px;">Portal Account ID</div>
                                <div style="font-family: monospace; font-size: 16px; font-weight: 700; color: #2A2B61; letter-spacing: 0.5px;">{portal_id_display}</div>
                            </td>
                            <td style="padding: 0; vertical-align: top; border-left: 1px solid #C7C9E8; padding-left: 20px; width: 50%;">
                                <div style="font-size: 11px; color: #6366F1; text-transform: uppercase; letter-spacing: 0.08em; font-weight: 700; margin-bottom: 4px;">System Registration ID</div>
                                <div style="font-family: monospace; font-size: 16px; font-weight: 700; color: #2A2B61; letter-spacing: 0.5px;">{system_id}</div>
                            </td>
                        </tr>
                    </table>
                </div>

                <!-- Personal Info -->
                <div style="margin: 0 32px 20px;">
                    <h4 style="color: #2A2B61; font-size: 13px; text-transform: uppercase; letter-spacing: 0.06em; margin: 0 0 12px; border-bottom: 1px solid #E2E6EE; padding-bottom: 8px;">Personal Information</h4>
                    <table style="width:100%; border-collapse:collapse;">
                        <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;width:140px;">Full Name</td><td style="padding:8px 0;font-weight:600;">{full_name_val}</td></tr>
                        <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;">Email</td><td style="padding:8px 0;font-weight:600;">{email_val}</td></tr>
                        <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;">Type</td><td style="padding:8px 0;font-weight:600;text-transform:capitalize;">{registrant_type_val}</td></tr>
                        {identity_rows}
                        <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;">Contact</td><td style="padding:8px 0;font-weight:600;">{contact_val}</td></tr>
                        <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;">Address</td><td style="padding:8px 0;font-weight:600;">{address_val}</td></tr>
                        <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;">Driver&#39;s License</td><td style="padding:8px 0;font-weight:600;">{license_val}</td></tr>
                        {_authorized_driver_row(registration)}
                        {campus_days_row}
                    </table>
                </div>

                <!-- Vehicle Info -->
                <div style="margin: 0 32px 20px;">
                    <h4 style="color: #2A2B61; font-size: 13px; text-transform: uppercase; letter-spacing: 0.06em; margin: 0 0 12px; border-bottom: 1px solid #E2E6EE; padding-bottom: 8px;">Vehicle Information</h4>
                    <table style="width:100%; border-collapse:collapse;">
                        <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;width:140px;">Plate Number</td><td style="padding:8px 0;font-weight:700;font-family:monospace;color:#2A2B61;">{plate_val}</td></tr>
                        <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;">Vehicle Type</td><td style="padding:8px 0;font-weight:600;text-transform:capitalize;">{vehicle_type_val}</td></tr>
                        <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;">Color</td><td style="padding:8px 0;font-weight:600;">{color_val}</td></tr>
                        <tr><td style="padding:8px 0;color:#5A5F72;font-size:13px;">Conduction No.</td><td style="padding:8px 0;font-weight:600;">{conduction_val}</td></tr>
                    </table>
                </div>

                <!-- QR Code -->
                <div style="text-align: center; margin: 0 32px 24px; background: #F8FAFC; border-radius: 10px; padding: 24px;">
                    <p style="margin: 0 0 12px; color: #5A5F72; font-size: 13px;">Present this QR code to security personnel upon entry:</p>
                    <img src="{qr_src}" alt="Vehicle QR Code" style="border: 2px solid #E2E6EE; border-radius: 8px; padding: 8px; background: white; max-width: 200px;" />
                    <p style="margin: 12px 0 0; color: #7C80A3; font-size: 12px;">Not showing? The same QR is attached to this email, and always available on your portal dashboard.</p>
                </div>

                <!-- Login Credentials -->
                <div style="margin: 0 32px 24px; background: #F0F2F7; border-radius: 10px; padding: 20px; border-left: 4px solid #2A2B61;">
                    <h4 style="color: #2A2B61; margin: 0 0 12px; font-size: 14px;">Portal Login Credentials</h4>
                    <table style="width:100%; border-collapse:collapse;">
                        <tr><td style="padding:6px 0;color:#5A5F72;font-size:13px;width:80px;">Email</td><td style="padding:6px 0;font-weight:700;">{email_val}</td></tr>
                        <tr><td style="padding:6px 0;color:#5A5F72;font-size:13px;">Password</td><td style="padding:6px 0;font-weight:700;font-family:monospace;font-size:15px;letter-spacing:1px;">{temp_password_val}</td></tr>
                    </table>
                    <p style="color: #DC2626; font-size: 12px; margin: 12px 0 0;"><strong>Important:</strong> You will be prompted to change this password on your first login.</p>
                </div>

                <!-- Footer -->
                <div style="background: #F8FAFC; border-top: 1px solid #E2E6EE; padding: 16px 32px; text-align: center;">
                    <p style="font-size: 12px; color: #7C80A3; margin: 0;">Saint Louis College Vehicle Management System</p>
                    <p style="font-size: 11px; color: #B0B4C7; margin: 4px 0 0;">This is an automated message. Please do not reply.</p>
                </div>

            </div>
        </body>
    </html>
    """

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
            f"A PDF copy of your full registration details is attached."
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

    # A failure building the PDF must not cost the owner their approval email
    # (and, upstream, must not roll back the approval itself) \u2014 so send without
    # the attachment and log it rather than raising.
    try:
        from registration_pdf import (registration_confirmation_pdf,
                                      registration_pdf_filename)
        msg.attach(registration_pdf_filename(registration),
                   registration_confirmation_pdf(registration),
                   'application/pdf')
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

    # Identity rows differ by registrant type
    if registration.registrant_type == 'student':
        id_rows = (
            f'<tr><td style="padding:7px 0;color:#5A5F72;font-size:13px;width:150px;">Student ID</td>'
            f'<td style="padding:7px 0;font-weight:600;">{esc_or_dash(registration.student_id)}</td></tr>'
            f'<tr><td style="padding:7px 0;color:#5A5F72;font-size:13px;">Program &amp; Year</td>'
            f'<td style="padding:7px 0;font-weight:600;">{esc_or_dash(registration.program_year)}</td></tr>'
        )
        campus_days_str = esc_or_dash(', '.join(registration.campus_days)
                                      if registration.campus_days else '')
        schedule_row = (
            f'<tr><td style="padding:7px 0;color:#5A5F72;font-size:13px;">Campus Days</td>'
            f'<td style="padding:7px 0;font-weight:600;">{campus_days_str}</td></tr>'
            f'<tr><td style="padding:7px 0;color:#5A5F72;font-size:13px;">Schedule Group</td>'
            f'<td style="padding:7px 0;font-weight:600;">{esc_or_dash(registration.schedule)}</td></tr>'
        )
    elif registration.registrant_type == 'employee':
        dept_name = esc_or_dash(department_label(registration))
        id_rows = (
            f'<tr><td style="padding:7px 0;color:#5A5F72;font-size:13px;width:150px;">Employee ID</td>'
            f'<td style="padding:7px 0;font-weight:600;">{esc_or_dash(registration.employee_id)}</td></tr>'
            f'<tr><td style="padding:7px 0;color:#5A5F72;font-size:13px;">Department</td>'
            f'<td style="padding:7px 0;font-weight:600;">{dept_name}</td></tr>'
        )
        schedule_row = ''
    else:  # fetcher
        id_rows = ''
        schedule_row = ''

    full_name_val    = esc(registration.full_name)
    email_val        = esc(registration.email)
    contact_val      = esc_or_dash(registration.contact_number)
    address_val      = esc_or_dash(registration.address)
    license_val      = esc_or_dash(registration.drivers_license)
    plate_val        = esc_or_dash(registration.plate_number)
    vehicle_type_val = esc(registration.vehicle_type)
    color_val        = esc_or_dash(registration.vehicle_color)
    conduction_val   = esc_or_dash(registration.conduction_number)

    type_label = {'student': 'Student', 'employee': 'Employee', 'fetcher': 'Fetcher / Drop & Go'}.get(
        registration.registrant_type, registration.registrant_type.capitalize()
    )
    ref_number = f"REG-{str(registration.pk).zfill(6)}"

    html_message = f"""
    <html>
        <body style="font-family: Arial, sans-serif; color: #1A1D2E; background-color: #F0F2F7; padding: 20px; margin: 0;">
            <div style="max-width: 620px; margin: 0 auto; background: #FFFFFF; border-radius: 12px; border-top: 4px solid #D97706; box-shadow: 0 4px 20px rgba(0,0,0,0.08); overflow: hidden;">

                <!-- Header -->
                <div style="padding: 28px 32px 20px;">
                    <h2 style="color: #D97706; margin: 0 0 6px;">Registration Received &#8212; Pending Review</h2>
                    <p style="color: #5A5F72; margin: 0; font-size: 14px;">
                        Your vehicle registration request has been submitted and is awaiting CDSO review.
                    </p>
                </div>

                <!-- Status Banner -->
                <div style="margin: 0 32px 24px; background: #FFFBEB; border: 1px solid #FDE68A; border-radius: 10px; padding: 16px 20px;">
                    <table style="width:100%; border-collapse:collapse;">
                        <tr>
                            <td style="vertical-align:top; width:50%;">
                                <div style="font-size:11px;color:#92400E;text-transform:uppercase;letter-spacing:0.07em;font-weight:700;margin-bottom:4px;">Reference No.</div>
                                <div style="font-family:monospace;font-size:16px;font-weight:700;color:#D97706;">{ref_number}</div>
                            </td>
                            <td style="vertical-align:top; border-left:1px solid #FDE68A; padding-left:20px; width:50%;">
                                <div style="font-size:11px;color:#92400E;text-transform:uppercase;letter-spacing:0.07em;font-weight:700;margin-bottom:4px;">Submitted On</div>
                                <div style="font-size:13px;font-weight:600;color:#1A1D2E;">{submitted_at}</div>
                            </td>
                        </tr>
                    </table>
                </div>

                <!-- Greeting -->
                <div style="padding: 0 32px 20px;">
                    <p style="margin:0 0 8px;">Dear <strong>{full_name_val}</strong>,</p>
                    <p style="color:#5A5F72;font-size:14px;margin:0;">
                        Thank you for submitting your vehicle registration. The CDSO office will review your application
                        and send you a follow-up email once a decision has been made. Please keep this email for your records.
                    </p>
                </div>

                <!-- Personal Information -->
                <div style="margin: 0 32px 20px;">
                    <h4 style="color:#2A2B61;font-size:12px;text-transform:uppercase;letter-spacing:0.06em;margin:0 0 10px;border-bottom:1px solid #E2E6EE;padding-bottom:8px;">
                        Personal Information
                    </h4>
                    <table style="width:100%;border-collapse:collapse;">
                        <tr><td style="padding:7px 0;color:#5A5F72;font-size:13px;width:150px;">Full Name</td><td style="padding:7px 0;font-weight:600;">{full_name_val}</td></tr>
                        <tr><td style="padding:7px 0;color:#5A5F72;font-size:13px;">Email</td><td style="padding:7px 0;font-weight:600;">{email_val}</td></tr>
                        <tr><td style="padding:7px 0;color:#5A5F72;font-size:13px;">Registrant Type</td><td style="padding:7px 0;font-weight:600;">{esc(type_label)}</td></tr>
                        <tr><td style="padding:7px 0;color:#5A5F72;font-size:13px;">Contact No.</td><td style="padding:7px 0;font-weight:600;">{contact_val}</td></tr>
                        <tr><td style="padding:7px 0;color:#5A5F72;font-size:13px;">Address</td><td style="padding:7px 0;font-weight:600;">{address_val}</td></tr>
                        <tr><td style="padding:7px 0;color:#5A5F72;font-size:13px;">Driver&#39;s License</td><td style="padding:7px 0;font-weight:600;">{license_val}</td></tr>
                        {_authorized_driver_row(registration, pad='7px')}
                        {id_rows}
                        {schedule_row}
                    </table>
                </div>

                <!-- Vehicle Information -->
                <div style="margin: 0 32px 20px;">
                    <h4 style="color:#2A2B61;font-size:12px;text-transform:uppercase;letter-spacing:0.06em;margin:0 0 10px;border-bottom:1px solid #E2E6EE;padding-bottom:8px;">
                        Vehicle Information
                    </h4>
                    <table style="width:100%;border-collapse:collapse;">
                        <tr><td style="padding:7px 0;color:#5A5F72;font-size:13px;width:150px;">Plate Number</td><td style="padding:7px 0;font-weight:700;font-family:monospace;font-size:15px;color:#2A2B61;">{plate_val}</td></tr>
                        <tr><td style="padding:7px 0;color:#5A5F72;font-size:13px;">Vehicle Type</td><td style="padding:7px 0;font-weight:600;text-transform:capitalize;">{vehicle_type_val}</td></tr>
                        <tr><td style="padding:7px 0;color:#5A5F72;font-size:13px;">Color</td><td style="padding:7px 0;font-weight:600;">{color_val}</td></tr>
                        <tr><td style="padding:7px 0;color:#5A5F72;font-size:13px;">Conduction No.</td><td style="padding:7px 0;font-weight:600;">{conduction_val}</td></tr>
                    </table>
                </div>

                <!-- What Happens Next -->
                <div style="margin: 0 32px 24px; background: #F0F2F7; border-radius: 10px; padding: 18px 20px;">
                    <h4 style="color:#2A2B61;margin:0 0 12px;font-size:14px;">What Happens Next?</h4>
                    <ol style="margin:0;padding-left:20px;color:#5A5F72;font-size:13px;line-height:1.9;">
                        <li>The CDSO office will review your submitted documents and information.</li>
                        <li>You will receive an email once your registration is <strong>approved</strong> or <strong>declined</strong>.</li>
                        <li>If approved, your portal login credentials and vehicle QR code will be sent to this email.</li>
                    </ol>
                </div>

                <!-- Footer -->
                <div style="background:#F8FAFC;border-top:1px solid #E2E6EE;padding:16px 32px;text-align:center;">
                    <p style="font-size:12px;color:#7C80A3;margin:0;">Saint Louis College Vehicle Management System</p>
                    <p style="font-size:11px;color:#B0B4C7;margin:4px 0 0;">This is an automated message. Please do not reply to this email.</p>
                </div>

            </div>
        </body>
    </html>
    """

    type_display = type_label
    send_mail(
        subject=f"SLC Vehicle Registration Received — {ref_number} (Pending Review)",
        message=(
            f"Dear {registration.full_name},\n\n"
            f"Your vehicle registration has been received and is pending CDSO review.\n\n"
            f"Reference No.: {ref_number}\n"
            f"Plate Number:  {registration.plate_number}\n"
            f"Type:          {type_display}\n"
            f"Submitted:     {submitted_at}\n\n"
            f"You will be notified by email once a decision has been made.\n\n"
            f"Saint Louis College Vehicle Management System"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[registration.email],
        html_message=html_message,
        fail_silently=False,
    )


def send_rejection_email(registration, reason):
    rejection_reason = reason or 'No specific reason provided.'
    reason_html    = esc(rejection_reason)
    full_name_val  = esc(registration.full_name)
    plate_val      = esc_or_dash(registration.plate_number)

    html_message = f"""
    <html>
        <body style="font-family: Arial, sans-serif; color: #1A1D2E; background-color: #F0F2F7; padding: 20px; margin: 0;">
            <div style="max-width: 600px; margin: 0 auto; background: #FFFFFF; padding: 30px; border-radius: 12px; border-top: 4px solid #DC2626; box-shadow: 0 4px 10px rgba(0,0,0,0.05);">
                <h2 style="color: #DC2626; margin-top: 0;">Vehicle Registration Declined</h2>
                <p>Dear {full_name_val},</p>
                <p>We regret to inform you that your vehicle registration for plate number <strong>{plate_val}</strong> has been declined.</p>
                <div style="background: #FEF2F2; border-left: 4px solid #DC2626; padding: 15px; margin: 20px 0; border-radius: 4px;">
                    <h4 style="margin: 0 0 10px 0; color: #991B1B;">Reason for Rejection:</h4>
                    <p style="margin: 0; color: #7F1D1D;">{reason_html}</p>
                </div>
                <p>If you have any questions or would like to submit a new application, please contact the administration office.</p>
                <hr style="border: 0; border-top: 1px solid #E2E6EE; margin: 20px 0;" />
                <p style="font-size: 12px; color: #7C80A3; text-align: center;">Saint Louis College Vehicle Management System</p>
            </div>
        </body>
    </html>
    """

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
        cta_html = (
            f'<div style="background: #FEF2F2; border-left: 4px solid #DC2626; padding: 15px; margin: 20px 0; border-radius: 4px;">'
            f'<p style="margin: 0; color: #7F1D1D;">Because your account reached the maximum number of traffic '
            f'violations, you are not eligible to register a vehicle pass again. Please contact the '
            f'administration office if you have any questions.</p></div>'
        )
        cta_text = ("Because your account reached the maximum number of violations, you are not "
                    "eligible to register a vehicle pass again. Please contact the administration office.")
    else:
        cta_html = (
            f'<div style="background: #FFF7ED; border-left: 4px solid #B4560F; padding: 15px; margin: 20px 0; border-radius: 4px;">'
            f'<p style="margin: 0; color: #7C2D12;">You may register again during {window_text}. Your previous '
            f'email, ID and plate number are free to reuse &mdash; they will not be reported as already taken.</p></div>'
        )
        cta_text = (f"You may register again during {window_text}. Your previous email, ID and plate "
                    f"number are free to reuse.")

    html_message = f"""
    <html>
        <body style="font-family: Arial, sans-serif; color: #1A1D2E; background-color: #F0F2F7; padding: 20px; margin: 0;">
            <div style="max-width: 600px; margin: 0 auto; background: #FFFFFF; padding: 30px; border-radius: 12px; border-top: 4px solid #B4560F; box-shadow: 0 4px 10px rgba(0,0,0,0.05);">
                <h2 style="color: #B4560F; margin-top: 0;">Your Vehicle Pass Account Has Expired</h2>
                <p>Dear {esc(user.full_name)},</p>
                <p>Your Saint Louis College vehicle owner account reached its expiration date on
                   <strong>{expired_on}</strong> and has been archived. Your vehicle pass is no longer active.</p>
                {cta_html}
                <p>If you believe this is a mistake, please contact the administration office.</p>
                <hr style="border: 0; border-top: 1px solid #E2E6EE; margin: 20px 0;" />
                <p style="font-size: 12px; color: #7C80A3; text-align: center;">Saint Louis College Vehicle Management System</p>
            </div>
        </body>
    </html>
    """

    send_mail(
        subject="SLC Vehicle Pass Account Expired",
        message=(f"Dear {user.full_name},\n\nYour vehicle owner account expired on {expired_on} "
                 f"and has been archived.\n\n{cta_text}"),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        html_message=html_message,
        fail_silently=True,  # a mail failure must not abort the archive batch
    )
